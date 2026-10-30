"""公共行情 WebSocket 流（**只读**；不参与任何交易决策/下单路径）。

## 定位（先读这段，避免误用）

本模块是 roadmap「从轮询迈向流式」的**基础层**，本刀只交付：

1. **帧解析**：把 OKX 公共行情的原始帧归一成 tick；
2. **有界缓冲**：每个标的只留最近 N 条 tick（内存有界，绝不攒无界表）；
3. **健康账本**：帧数/tick 数/解析失败/连接错误/**陈旧度**；
4. **探测 CLI**：`python -m scripts.market_stream --probe`（只读、按需跑、不常驻）。

**它不**：不常驻、不接决策路径、不改任何下单行为。现有 REST 取数（
`market_data_service`）一字未动 —— 流式是**并存**的观测与未来取数面，不是替换。

## 被真实端点教出来的规则（本机实跑核对，2026-09-20）

1. **OKX**：`wss://ws.okx.com:8443/ws/v5/public`，`{"op":"subscribe",...}` 正常，
   帧里 `data[].ts` 是**交易所毫秒时间戳**，与本地接收时间分开记
   （两者之差才是链路延迟，混在一起就永远看不出延迟）。
2. **"已订阅但长时间无数据"必须当成故障**：这正是陈旧度账本存在的理由。

## 与既有可观测性的关系

健康账本按既有跨进程手法落盘（worker/探测进程写、后端 `/metrics` 读）：
`data/market_stream_health.json`（带 `schema_version`，版本不认识一律当"没有数据"）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

#: 快照格式版本（跨进程契约；加字段就升版本）
SCHEMA_VERSION = 1
#: 每标的保留的最近 tick 数（有界内存；探测进程短命，但常驻模式也一样有界）
_BUFFER_MAXLEN = 256

VENUE_ENDPOINTS: Dict[str, str] = {
    "okx": "wss://ws.okx.com:8443/ws/v5/public",
    # 币安 USDⓈ-M：裸 socket + SUBSCRIBE 帧（单连接最多 1024 个 stream）。
    # WS **不消耗 REST 权重**，只受连接/消息频率限制（300 连接/5min、10 msg/s）——
    # 这正是币安官方对 -1003 的建议："Please use the websocket ... avoid polling"。
    "binance": "wss://fstream.binance.com/ws",
}

#: 币安 WS 订阅的 K 线周期（内部写法 → 币安流后缀）。与决策链实际消费的三档对齐。
BINANCE_WS_INTERVALS: Dict[str, str] = {"15m": "15m", "1H": "1h", "4H": "4h"}

#: 行情快照（tickers + klines）的独立版本号——与既有“健康账本”v1 契约解耦，
#: 加字段只升这个，不会让老的 `load_snapshot()`（健康）失效。
MARKET_SCHEMA_VERSION = 1

__all__ = [
    "SCHEMA_VERSION",
    "MARKET_SCHEMA_VERSION",
    "VENUE_ENDPOINTS",
    "BINANCE_WS_INTERVALS",
    "binance_stream_params",
    "TickBuffer",
    "StreamHealth",
    "MarketState",
    "parse_frame",
    "stream_url",
    "venue_symbol",
    "subscribe_payload",
    "seed_history",
    "write_snapshot",
    "load_snapshot",
    "load_market_snapshot",
    "probe",
    "run_stream",
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def venue_symbol(venue: str, symbol: str) -> str:
    """把**规范写法**（OKX 形态，如 `BTC-USDT-SWAP`）翻成该所原生合约名。

    合约名必须按所生成：OKX 收到 `BTC_USDT_SWAP` 这类写法会订阅被拒，
    故先把分隔符归一，再拼回 OKX 形态。
    """
    core = str(symbol or "").upper().replace("/", "-").replace("_", "-")
    core = core.replace("-SWAP", "").replace("-PERP", "")
    parts = [p for p in core.split("-") if p]
    base = parts[0] if parts else "BTC"
    quote = parts[1] if len(parts) > 1 else "USDT"
    if venue == "okx":
        return f"{base}-{quote}-SWAP"
    if venue == "binance":
        # 币安流名一律小写无分隔：BTCUSDT
        return f"{base}{quote}".lower()
    raise KeyError(f"未知场所：{venue}")


_BINANCE_BAR_TO_INTERNAL = {v: k for k, v in BINANCE_WS_INTERVALS.items()}


def _binance_inst(native: Any) -> str:
    """`BTCUSDT` → `BTC-USDT-SWAP`（与适配器 `_from_symbol` 同口径）。"""
    raw = str(native or "").strip().upper()
    if raw.endswith("USDT") and len(raw) > 4:
        return f"{raw[:-4]}-USDT-SWAP"
    return raw


def _binance_bar(interval: Any) -> str:
    """币安流后缀 → 内部周期写法（`1h` → `1H`；未知原样返回）。"""
    key = str(interval or "").strip().lower()
    return _BINANCE_BAR_TO_INTERNAL.get(key, str(interval or ""))


def stream_url(venue: str, symbol: str) -> str:
    """该所的公共行情连接地址。"""
    return VENUE_ENDPOINTS[venue]


def subscribe_payload(venue: str, symbol: str) -> Optional[Dict[str, Any]]:
    """该所的订阅帧。"""
    if venue == "okx":
        return {"op": "subscribe", "args": [{"channel": "tickers", "instId": symbol}]}
    if venue == "binance":
        # 一条订阅帧同时拿 ticker + 三档K线 + 20档深度 + 标记价/资金费率（见
        # `binance_stream_params`）；后续所有周期都不再打 REST。
        return {"method": "SUBSCRIBE", "params": binance_stream_params([symbol]), "id": 1}
    raise KeyError(f"未知场所：{venue}")


def binance_stream_params(symbols: Sequence[str]) -> List[str]:
    """币安订阅流清单（**单一事实源**，`subscribe_payload` 与 `run_stream` 共用）。

    每标的：`@ticker`（价/买卖/24h）、三档 `@kline_*`、`@depth20@100ms`（盘口，
    权重 5~10/次的 REST 端点在这里变成 0）、`@markPrice@1s`（标记价 + 当期资金费率）。
    另加一条全局 `!forceOrder@arr`（清算流，**REST 完全没有**）。
    """
    params: List[str] = []
    for sym in symbols:
        params.append(f"{sym}@ticker")
        params.extend(f"{sym}@kline_{iv}" for iv in BINANCE_WS_INTERVALS.values())
        params.append(f"{sym}@depth20@100ms")
        params.append(f"{sym}@markPrice@1s")
    params.append("!forceOrder@arr")
    return params


def _pairs(rows: Any) -> List[Tuple[Any, Any]]:
    """`[["px","qty"], ...]` → `[(px, qty), ...]`（形状不对的整行丢弃）。"""
    out: List[Tuple[Any, Any]] = []
    for row in rows or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            out.append((row[0], row[1]))
    return out


def _tick(venue: str, symbol: str, price: Any, *, kind: str,
          exchange_ms: Any = None, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    value = _num(price)
    if value is None or not symbol:
        return None
    local = int(now_ms if now_ms is not None else _now_ms())
    ts = _num(exchange_ms)
    return {
        "venue": venue,
        "symbol": str(symbol),
        "price": value,
        "kind": kind,
        "exchange_ms": None if ts is None else int(ts),
        "local_ms": local,
        # 链路延迟：**只在交易所给了时间戳时**才算，不给就是 None（不臆造 0）
        "latency_ms": None if ts is None else max(0, local - int(ts)),
    }


def parse_frame(venue: str, raw: Any, *, now_ms: Optional[int] = None) -> Dict[str, Any]:
    """把一帧原始数据归一成 `{"ticks": [...], "control": str|None, "error": str|None}`。

    **永不抛异常**：解析失败一律记成 `error`（第 137 刀的纪律：静默吞掉不可接受的
    是"没人知道"，而不是"函数不抛"）。
    """
    out: Dict[str, Any] = {"ticks": [], "control": None, "error": None}
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(payload, list):
            # 币安 `!forceOrder@arr` 是**数组帧**：逐元素复用同一套解析后合并。
            if venue == "binance":
                merged: Dict[str, Any] = {"ticks": [], "control": None, "error": None}
                for item in payload:
                    one = parse_frame(venue, item, now_ms=now_ms)
                    merged["ticks"].extend(one["ticks"])
                    merged["control"] = merged["control"] or one["control"]
                    merged["error"] = merged["error"] or one["error"]
                return merged
            return {"ticks": [], "control": None, "error": "非对象帧：list"}
        if not isinstance(payload, dict):
            return {"ticks": [], "control": None, "error": f"非对象帧：{type(payload).__name__}"}
    except Exception as exc:
        return {"ticks": [], "control": None, "error": f"{type(exc).__name__}: {exc}"}

    try:
        if venue == "okx":
            event = payload.get("event")
            if event == "error":
                return {"ticks": [], "control": None,
                        "error": f"okx error code={payload.get('code')} msg={payload.get('msg')}"}
            if event:
                out["control"] = f"okx {event}"
            arg = payload.get("arg") if isinstance(payload.get("arg"), dict) else {}
            for row in payload.get("data") or []:
                if not isinstance(row, dict):
                    continue
                tick = _tick("okx", row.get("instId") or arg.get("instId"), row.get("last"),
                             kind="ticker", exchange_ms=row.get("ts"), now_ms=now_ms)
                if tick is None:
                    out["error"] = "okx tickers 帧里没有可解析的 last"
                else:
                    tick["bid"] = _num(row.get("bidPx"))
                    tick["ask"] = _num(row.get("askPx"))
                    out["ticks"].append(tick)

        elif venue == "binance":
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            # 订阅应答：{"result": null, "id": 1}
            if "id" in payload and "e" not in payload and "e" not in data:
                out["control"] = "binance subscribed"
                return out
            if payload.get("code") not in (None, 0) or (payload.get("msg") and "e" not in data):
                return {"ticks": [], "control": None,
                        "error": f"binance code={payload.get('code')} msg={payload.get('msg')}"}
            event = str(data.get("e") or "")
            if event == "24hrTicker":
                tick = _tick("binance", _binance_inst(data.get("s")), data.get("c"),
                             kind="ticker", exchange_ms=data.get("E"), now_ms=now_ms)
                if tick is None:
                    out["error"] = "binance 24hrTicker 帧里没有可解析的 last"
                else:
                    tick["bid"] = _num(data.get("b"))
                    tick["ask"] = _num(data.get("a"))
                    tick["open24h"] = _num(data.get("o"))
                    tick["vol24h"] = _num(data.get("v"))
                    out["ticks"].append(tick)
            elif event == "kline":
                k = data.get("k") if isinstance(data.get("k"), dict) else {}
                close_px = _num(k.get("c"))
                if close_px is None:
                    out["error"] = "binance kline 帧里没有可解析的 close"
                else:
                    out["ticks"].append({
                        "venue": "binance",
                        "symbol": _binance_inst(k.get("s")),
                        "kind": "kline",
                        "bar": _binance_bar(k.get("i")),
                        "open_ms": int(k.get("t") or 0),
                        "open": _num(k.get("o")),
                        "high": _num(k.get("h")),
                        "low": _num(k.get("l")),
                        "close": close_px,
                        "vol": _num(k.get("v")),
                        "quote_vol": _num(k.get("q")),
                        "closed": bool(k.get("x")),
                        "exchange_ms": data.get("E"),
                        "local_ms": int(now_ms if now_ms is not None else _now_ms()),
                    })
            elif event == "depthUpdate" or ("s" in data and ("b" in data or "bids" in data)):
                # 20 档部分深度（`@depth20@100ms`）每帧即**全量快照**，直接覆盖。
                out["ticks"].append({
                    "venue": "binance",
                    "symbol": _binance_inst(data.get("s")),
                    "kind": "depth",
                    "bids": [[_num(p), _num(q)] for p, q in _pairs(data.get("b") or data.get("bids"))],
                    "asks": [[_num(p), _num(q)] for p, q in _pairs(data.get("a") or data.get("asks"))],
                    "exchange_ms": data.get("E"),
                    "local_ms": int(now_ms if now_ms is not None else _now_ms()),
                })
            elif event == "markPriceUpdate":
                out["ticks"].append({
                    "venue": "binance",
                    "symbol": _binance_inst(data.get("s")),
                    "kind": "mark",
                    "mark_px": _num(data.get("p")),
                    # 币安给的是**小数**（0.0001）；下游 `fetch_funding_rate` 用百分比，
                    # 故这里保持原值，由读取侧统一 ×100（与适配器 REST 分支同口径）。
                    "funding_rate": _num(data.get("r")),
                    "next_funding_ms": data.get("T"),
                    "exchange_ms": data.get("E"),
                    "local_ms": int(now_ms if now_ms is not None else _now_ms()),
                })
            elif event == "forceOrder":
                order = data.get("o") if isinstance(data.get("o"), dict) else {}
                out["ticks"].append({
                    "venue": "binance",
                    "symbol": _binance_inst(order.get("s")),
                    "kind": "liquidation",
                    "side": str(order.get("S") or "").strip().lower(),
                    "price": _num(order.get("p")),
                    "qty": _num(order.get("q")),
                    "exchange_ms": data.get("E") or order.get("T"),
                    "local_ms": int(now_ms if now_ms is not None else _now_ms()),
                })
            else:
                out["control"] = f"binance {event or 'unknown'}"
        else:
            return {"ticks": [], "control": None, "error": f"未知场所：{venue}"}
    except Exception as exc:          # 解析器自身异常也必须留痕，绝不上抛
        return {"ticks": [], "control": out.get("control"),
                "error": f"parse {type(exc).__name__}: {exc}"}
    return out


class TickBuffer:
    """每标的最近 N 条 tick 的有界环形缓冲（线程安全）。"""

    def __init__(self, maxlen: int = _BUFFER_MAXLEN) -> None:
        self._lock = threading.Lock()
        self._maxlen = int(maxlen)
        self._ticks: Dict[str, Deque[Dict[str, Any]]] = {}

    @staticmethod
    def key(venue: str, symbol: str) -> str:
        return f"{str(venue).lower()}:{symbol}"

    def put(self, tick: Dict[str, Any]) -> None:
        key = self.key(tick.get("venue", ""), tick.get("symbol", ""))
        with self._lock:
            buf = self._ticks.get(key)
            if buf is None:
                buf = self._ticks[key] = deque(maxlen=self._maxlen)
            buf.append(tick)

    def latest(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            buf = self._ticks.get(key)
            return dict(buf[-1]) if buf else None

    def age_s(self, key: str, *, now_ms: Optional[int] = None) -> Optional[float]:
        latest = self.latest(key)
        if latest is None:
            return None
        return max(0.0, (int(now_ms if now_ms is not None else _now_ms())
                         - int(latest["local_ms"])) / 1000.0)

    def keys(self) -> List[str]:
        with self._lock:
            return sorted(self._ticks)

    def sizes(self) -> Dict[str, int]:
        with self._lock:
            return {k: len(v) for k, v in self._ticks.items()}


class StreamHealth:
    """流健康账本：帧/tick/解析失败/连接错误/最近消息与最近 tick 时刻（按场所）。

    "已连接但没数据"是本模块最要防的形态，
    故 `last_tick_ms` 与 `last_msg_ms` **分开**记：只连上、只收到订阅应答，
    在账本里必须表现为 tick 陈旧，而不是"健康"。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: Dict[str, int] = {}
        self._ticks: Dict[str, int] = {}
        self._parse_errors: Dict[str, int] = {}
        self._errors: Dict[str, int] = {}
        self._reconnects: Dict[str, int] = {}
        self._last_msg_ms: Dict[str, int] = {}
        self._last_tick_ms: Dict[str, int] = {}
        self._last_error: Dict[str, str] = {}

    def note_frame(self, venue: str, *, ticks: int = 0, error: Optional[str] = None,
                   now_ms: Optional[int] = None) -> None:
        stamp = int(now_ms if now_ms is not None else _now_ms())
        with self._lock:
            self._frames[venue] = self._frames.get(venue, 0) + 1
            self._last_msg_ms[venue] = stamp
            if ticks:
                self._ticks[venue] = self._ticks.get(venue, 0) + ticks
                self._last_tick_ms[venue] = stamp
            if error:
                self._parse_errors[venue] = self._parse_errors.get(venue, 0) + 1
                self._last_error[venue] = str(error)[:200]

    def note_error(self, venue: str, detail: str, *, now_ms: Optional[int] = None) -> None:
        with self._lock:
            self._errors[venue] = self._errors.get(venue, 0) + 1
            self._last_error[venue] = str(detail)[:200]

    def note_reconnect(self, venue: str) -> None:
        with self._lock:
            self._reconnects[venue] = self._reconnects.get(venue, 0) + 1

    def staleness_s(self, venue: str, *, now_ms: Optional[int] = None) -> Optional[float]:
        """距上次**收到 tick** 的秒数；从未收到过返回 None（不是 0 —— 不可判定≠安全）。"""
        with self._lock:
            last = self._last_tick_ms.get(venue)
        if last is None:
            return None
        return max(0.0, (int(now_ms if now_ms is not None else _now_ms()) - last) / 1000.0)

    def snapshot(self, *, now_ms: Optional[int] = None) -> Dict[str, Any]:
        stamp = int(now_ms if now_ms is not None else _now_ms())
        with self._lock:
            venues = sorted(set(self._frames) | set(self._ticks) | set(self._errors))
            return {
                "schema_version": SCHEMA_VERSION,
                "written_at_ms": stamp,
                "venues": {
                    v: {
                        "frames": self._frames.get(v, 0),
                        "ticks": self._ticks.get(v, 0),
                        "parse_errors": self._parse_errors.get(v, 0),
                        "errors": self._errors.get(v, 0),
                        "reconnects": self._reconnects.get(v, 0),
                        "last_msg_ms": self._last_msg_ms.get(v),
                        "last_tick_ms": self._last_tick_ms.get(v),
                        "tick_age_s": (None if self._last_tick_ms.get(v) is None
                                       else round((stamp - self._last_tick_ms[v]) / 1000.0, 3)),
                        "last_error": self._last_error.get(v),
                    }
                    for v in venues
                },
            }


def write_snapshot(path: str, payload: Dict[str, Any], *, compact: bool = False) -> bool:
    """原子写快照（tmp + `os.replace`）；**绝不抛异常**，失败返回 False。

    `compact=True` 用无缩进 JSON —— 行情快照（含 240 根 K 线 × 多标的）用缩进会膨胀
    2~3 倍，而它**每 2 秒被多个进程解析**，体积直接影响读侧延迟。
    """
    try:
        target = os.fspath(path)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent or ".", prefix=".ms-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                if compact:
                    json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
                else:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        return True
    except Exception:
        return False


def load_snapshot(path: str) -> Dict[str, Any]:
    """读快照；缺失/损坏/**版本不认识**一律返回 `{}`（调用方据此标 source_ok=0）。"""
    try:
        with open(os.fspath(path), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return {}
    return payload


def probe(*, venues: Sequence[str] = ("okx",),
          symbol: str = "BTC-USDT-SWAP", symbol_by_venue: Optional[Dict[str, str]] = None,
          seconds: float = 10.0, snapshot_path: Optional[str] = None,
          connect_factory: Any = None, now: Any = _now_ms) -> Dict[str, Any]:
    """只读探测：连上 OKX 公共行情流收若干秒，返回健康快照（可选落盘）。

    `seconds` 是**每所**的窗口（总耗时 ≈ seconds × 场所数）。

    `connect_factory(url, **kw)` 可注入（测试用假传输，绝不出网）。
    每一所独立 try：**一个所挂了不许影响另一个所**；
    "连上了但一条数据都没来"也要**留痕**——
    静默的失败正是第 137 刀事故里最贵的那种。
    """
    if connect_factory is None:                     # 延迟导入：本模块被 import 时不拉网络栈
        from websockets.sync.client import connect as connect_factory   # type: ignore[no-redef]

    health = StreamHealth()
    buffer = TickBuffer()
    for venue in venues:
        sym = (symbol_by_venue or {}).get(venue) or venue_symbol(venue, symbol)
        url = stream_url(venue, sym)
        frames_before = 0
        deadline = time.time() + max(0.5, float(seconds))
        try:
            with connect_factory(url, open_timeout=8, close_timeout=3) as ws:
                payload = subscribe_payload(venue, sym)
                if payload is not None:
                    ws.send(json.dumps(payload))
                while time.time() < deadline:
                    try:
                        raw = ws.recv(timeout=max(0.2, min(2.0, deadline - time.time())))
                    except Exception:
                        break                             # 本轮该所安静：下面统一留痕
                    parsed = parse_frame(venue, raw, now_ms=now())
                    for tick in parsed["ticks"]:
                        buffer.put(tick)
                    frames_before += 1
                    health.note_frame(venue, ticks=len(parsed["ticks"]),
                                      error=parsed["error"], now_ms=now())
                if frames_before == 0:
                    health.note_error(venue, "已连接但窗口内零数据帧（订阅被静默/流被限流）",
                                      now_ms=now())
        except Exception as exc:
            health.note_error(venue, f"{type(exc).__name__}: {exc}", now_ms=now())
    snap = health.snapshot(now_ms=now())
    snap["buffers"] = buffer.sizes()
    if snapshot_path:
        snap["written"] = write_snapshot(snapshot_path, snap)
    return snap


def _text(value: Any) -> str:
    return "" if value is None else str(value)


class MarketState:
    """常驻采集进程的行情状态：tickers + 三档 K 线（都按标的有界）。

    - ticker：每次覆盖（只需最新一条）；
    - kline：按 **bar 起点** upsert；每个 (标的, 周期) 只留最近 `max_bars` 根，
      输出 **newest-first 的 9 列**（与 OKX/适配器同形状，确认列在 index 8），
      让 `market_data_service.fetch_candles()` 可以零改动地直接消费。
    """

    def __init__(self, max_bars: int = 240, max_liquidations: int = 200) -> None:
        self._lock = threading.Lock()
        self._max_bars = max(1, int(max_bars))
        self._max_liq = max(1, int(max_liquidations))
        self._tickers: Dict[str, Dict[str, Any]] = {}
        self._klines: Dict[str, Dict[str, Dict[int, List[str]]]] = {}
        self._depth: Dict[str, Dict[str, Any]] = {}
        self._marks: Dict[str, Dict[str, Any]] = {}
        self._liquidations: Deque[Dict[str, Any]] = deque(maxlen=self._max_liq)

    def update(self, tick: Dict[str, Any]) -> None:
        kind = str(tick.get("kind") or "")
        inst = str(tick.get("symbol") or "")
        if not inst:
            return
        with self._lock:
            if kind == "ticker":
                self._tickers[inst] = {
                    "last": tick.get("price"),
                    "bid": tick.get("bid"),
                    "ask": tick.get("ask"),
                    "open24h": tick.get("open24h"),
                    "vol24h": tick.get("vol24h"),
                    "exchange_ms": tick.get("exchange_ms"),
                    "local_ms": tick.get("local_ms"),
                }
                return
            if kind == "depth":
                bids = tick.get("bids") or []
                asks = tick.get("asks") or []
                if bids or asks:
                    self._depth[inst] = {
                        "bids": [list(row) for row in bids],
                        "asks": [list(row) for row in asks],
                        "exchange_ms": tick.get("exchange_ms"),
                        "local_ms": tick.get("local_ms"),
                    }
                return
            if kind == "mark":
                self._marks[inst] = {
                    "mark_px": tick.get("mark_px"),
                    "funding_rate": tick.get("funding_rate"),
                    "next_funding_ms": tick.get("next_funding_ms"),
                    "exchange_ms": tick.get("exchange_ms"),
                    "local_ms": tick.get("local_ms"),
                }
                return
            if kind == "liquidation":
                price = tick.get("price")
                if price is not None:
                    self._liquidations.appendleft({
                        "symbol": inst,
                        "side": tick.get("side") or "",
                        "price": price,
                        "qty": tick.get("qty"),
                        "exchange_ms": tick.get("exchange_ms"),
                        "local_ms": tick.get("local_ms"),
                    })
                return
            if kind != "kline":
                return
            bar = str(tick.get("bar") or "")
            open_ms = int(tick.get("open_ms") or 0)
            close_px = tick.get("close")
            if not bar or open_ms <= 0 or close_px is None:
                return
            quote = tick.get("quote_vol")
            series = self._klines.setdefault(inst, {}).setdefault(bar, {})
            series[open_ms] = [
                str(open_ms), _text(tick.get("open")), _text(tick.get("high")),
                _text(tick.get("low")), _text(close_px), _text(tick.get("vol")),
                _text(quote), _text(quote), "1" if tick.get("closed") else "0",
            ]
            if len(series) > self._max_bars:
                for old in sorted(series)[: len(series) - self._max_bars]:
                    series.pop(old, None)

    def seed_candles(self, inst_id: str, bar: str, rows: Sequence[Sequence[Any]]) -> int:
        """用 REST 历史 K 线给某 (标的, 周期) **播种**；返回写入根数（按 open_ms upsert）。

        为什么需要（2026-10-30）：WS 只从**连上那一刻**开始攒 K 线 —— 决策链要求
        1H≥8 根、4H≥6 根（`fetch_candles` 的严格档还要 24/16 根），4H 要等 64 小时
        才够，这段"热身期"取数侧只能回退 REST；币安 REST 一旦被封/限流，整包就成
        「数据质量 invalid」。开流前播种一次，15m/1H/4H 立刻可用，之后靠 WS 增量维护。

        实时帧仍会覆盖同一 open_ms 的行（`update` 用同一个 key），故播种不会把
        "正在形成的最后一根"固化住。
        """
        if not inst_id or not bar:
            return 0
        added = 0
        with self._lock:
            series = self._klines.setdefault(inst_id, {}).setdefault(bar, {})
            for row in rows or []:
                if not isinstance(row, (list, tuple)) or len(row) < 9:
                    continue
                try:
                    open_ms = int(row[0])
                except (TypeError, ValueError):
                    continue
                if open_ms <= 0:
                    continue
                quote = _text(row[7])
                series[open_ms] = [
                    str(open_ms), _text(row[1]), _text(row[2]), _text(row[3]),
                    _text(row[4]), _text(row[5]), quote, quote, _text(row[8]),
                ]
                added += 1
            if len(series) > self._max_bars:
                for old in sorted(series)[: len(series) - self._max_bars]:
                    series.pop(old, None)
        return added

    def ticker(self, inst_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._tickers.get(inst_id)
            return dict(row) if row else None

    def candles(self, inst_id: str, bar: str, limit: int = 45) -> List[List[str]]:
        with self._lock:
            series = ((self._klines.get(inst_id) or {}).get(bar)) or {}
            keys = sorted(series, reverse=True)[: max(1, int(limit))]
            return [list(series[k]) for k in keys]

    def depth(self, inst_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._depth.get(inst_id)
            return {"bids": [list(r) for r in row["bids"]],
                    "asks": [list(r) for r in row["asks"]],
                    "local_ms": row.get("local_ms")} if row else None

    def mark(self, inst_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._marks.get(inst_id)
            return dict(row) if row else None

    def liquidations(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in list(self._liquidations)[: max(1, int(limit))]]

    def snapshot(self, *, now_ms: Optional[int] = None,
                 health: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        stamp = int(now_ms if now_ms is not None else _now_ms())
        with self._lock:
            tickers = {k: dict(v) for k, v in self._tickers.items()}
            klines = {
                inst: {bar: [list(series[k]) for k in sorted(series, reverse=True)]
                       for bar, series in bars.items()}
                for inst, bars in self._klines.items()
            }
            depth = {k: {"bids": [list(r) for r in v["bids"]],
                         "asks": [list(r) for r in v["asks"]],
                         "local_ms": v.get("local_ms")}
                     for k, v in self._depth.items()}
            marks = {k: dict(v) for k, v in self._marks.items()}
            liquidations = [dict(r) for r in list(self._liquidations)]
        return {
            "schema_version": MARKET_SCHEMA_VERSION,
            "written_at_ms": stamp,
            "venue": "binance",
            "tickers": tickers,
            "klines": klines,
            "depth": depth,
            "marks": marks,
            "liquidations": liquidations,
            "health": health or {},
        }


def load_market_snapshot(path: str) -> Dict[str, Any]:
    """读行情快照；缺失/损坏/**版本不认识**一律 `{}`（调用方据此回退 REST）。"""
    try:
        with open(os.fspath(path), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != MARKET_SCHEMA_VERSION:
        return {}
    return payload


def _seed_fetch(venue: str):
    """默认播种取数：币安**公开 REST** 的 K 线（不递归走 WS 快照，避免自我依赖）。"""
    def _fetch(inst_id: str, bar: str, limit: int):
        if str(venue or "").strip().lower() != "binance":
            return []
        # ⚠️ 双拼写铁律：本模块可能以 `scripts.market_stream` 或顶层 `market_stream` 身份
        # 被导入，而 `market_data_service` 也有两种拼写 —— 必须**复用进程里已加载的那个
        # 实例**，否则会凭空造出第二份 `_BINANCE_EXCHANGE` / 快照解析缓存。
        mds = sys.modules.get("market_data_service") or sys.modules.get("scripts.market_data_service")
        if mds is None:                           # 还没人导入过：按本模块的导入身份取同族
            try:
                from . import market_data_service as mds      # type: ignore[no-redef]
            except ImportError:                   # 从 scripts 目录直接执行时
                import market_data_service as mds            # type: ignore[no-redef]
        return mds._binance_exchange().candles(inst_id, bar=bar, limit=limit) or []
    return _fetch


def seed_history(state: MarketState, *, symbols: Sequence[str], venue: str = "binance",
                 limit: int = 240, fetch: Any = None,
                 intervals: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """用 REST 历史 K 线给 `state` 播种（`run_stream(seed=True)` 在连 WS 前调用）。

    返回 `{"rows": 写入根数, "symbols": 成功标的数, "errors": [说明...]}`。
    **任何失败都只记账**（进 `errors`），绝不让播种失败拖垮采集器 —— 缺的部分
    仍由"WS 增量 + 取数侧 REST 回退"兜着，行为不比播种前差。
    """
    bars = list(intervals or BINANCE_WS_INTERVALS.keys())
    fetcher = fetch or _seed_fetch(venue)
    rows_total = 0
    ok_symbols = 0
    errors: List[str] = []
    for raw_symbol in symbols or []:
        inst = str(raw_symbol or "").strip().upper()
        if not inst:
            continue
        got_any = False
        for bar in bars:
            try:
                rows = fetcher(inst, bar, int(limit)) or []
            except Exception as exc:
                errors.append(f"{inst} {bar}: {type(exc).__name__}: {exc}")
                continue
            written = state.seed_candles(inst, bar, rows)
            if written:
                rows_total += written
                got_any = True
        if got_any:
            ok_symbols += 1
    return {"rows": rows_total, "symbols": ok_symbols, "errors": errors}


def run_stream(*, venue: str = "binance", symbols: Sequence[str],
               snapshot_path: Optional[str] = None,
               flush_seconds: float = 2.0, max_bars: int = 240,
               reconnect_backoff: float = 3.0,
               connect_factory: Any = None, stop: Any = None,
               now: Any = _now_ms,
               max_rounds: Optional[int] = None,
               seed: bool = False, seed_limit: Optional[int] = None,
               seed_fetch: Any = None) -> Dict[str, Any]:
    """**常驻**采集：连上 → 一次性订阅全部标的(ticker + 三档 K 线) → 收帧 → 定期落快照。

    - 断线自动重连（有界退避）；`stop` 可以是 `threading.Event` 或零参可调用；
    - 空闲超时（`recv` 的 `TimeoutError`）**不算错误**，只继续等；
    - 其它异常一律记健康账本 + `note_reconnect`，绝不静默退出；
    - `seed=True`：连 WS **之前**先用 REST 历史 K 线播种（否则 1H/4H 要等
      24h/64h 才够决策链根数，热身期只能靠 REST 回退；2026-10-30）；
    - `connect_factory` / `max_rounds` / `seed_fetch` 供测试注入，绝不真出网。
    """
    if connect_factory is None:                      # 延迟导入：import 本模块不拉网络栈
        from websockets.sync.client import connect as connect_factory  # type: ignore[no-redef]

    health = StreamHealth()
    state = MarketState(max_bars=max_bars)
    canonical = [str(s or "").strip() for s in (symbols or []) if str(s or "").strip()]
    native = [venue_symbol(venue, s) for s in canonical]
    if seed and canonical:
        _seeded = seed_history(state, symbols=canonical, venue=venue,
                               limit=int(seed_limit or max_bars), fetch=seed_fetch)
        for _err in _seeded["errors"][:5]:
            health.note_error(venue, f"seed {_err}", now_ms=now())
        print(f"[market_stream] seed {venue}: {_seeded['symbols']} 标的 / "
              f"{_seeded['rows']} 根"
              + (f"，{len(_seeded['errors'])} 档失败" if _seeded["errors"] else ""))
        if snapshot_path:                     # 播种结果立刻可见（不等首次 flush）
            write_snapshot(snapshot_path,
                           state.snapshot(now_ms=now(), health=health.snapshot(now_ms=now())),
                           compact=True)
    if not native:
        return state.snapshot(now_ms=now(), health=health.snapshot(now_ms=now()))
    url = stream_url(venue, native[0])
    payload: Optional[Dict[str, Any]] = None
    if venue == "binance":
        payload = {"method": "SUBSCRIBE", "params": binance_stream_params(native), "id": 1}

    def _stopped() -> bool:
        if stop is None:
            return False
        try:
            return bool(stop.is_set()) if hasattr(stop, "is_set") else bool(stop())
        except Exception:
            return False

    rounds = 0
    while not _stopped():
        if max_rounds is not None and rounds >= max_rounds:
            break
        rounds += 1
        last_flush = time.monotonic()
        try:
            with connect_factory(url, open_timeout=10, close_timeout=3) as ws:
                if payload is not None:
                    ws.send(json.dumps(payload))
                while not _stopped():
                    try:
                        raw = ws.recv(timeout=1.0)
                    except TimeoutError:
                        raw = None                     # 空闲 = 正常，继续等
                    if raw is not None:
                        parsed = parse_frame(venue, raw, now_ms=now())
                        for tick in parsed["ticks"]:
                            state.update(tick)
                        health.note_frame(venue, ticks=len(parsed["ticks"]),
                                          error=parsed["error"], now_ms=now())
                    if snapshot_path and (time.monotonic() - last_flush) >= flush_seconds:
                        write_snapshot(snapshot_path,
                                       state.snapshot(now_ms=now(),
                                                      health=health.snapshot(now_ms=now())),
                                       compact=True)
                        last_flush = time.monotonic()
        except Exception as exc:
            health.note_error(venue, f"{type(exc).__name__}: {exc}", now_ms=now())
            health.note_reconnect(venue)
            if _stopped():
                break
            time.sleep(min(30.0, max(0.5, float(reconnect_backoff))))
    snap = state.snapshot(now_ms=now(), health=health.snapshot(now_ms=now()))
    if snapshot_path:
        write_snapshot(snapshot_path, snap, compact=True)
    return snap


def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ASTRA 公共行情流只读探测（不常驻、不下单）")
    parser.add_argument("--probe", action="store_true", help="连接公共流收若干秒（只读探测）")
    parser.add_argument("--run", action="store_true",
                        help="常驻采集：订阅 --symbols 的 ticker + 15m/1H/4H K 线，落 --snapshot")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--symbol", default="BTC-USDT-SWAP")
    parser.add_argument("--symbols", default="", help="逗号分隔的规范标的（OKX 形态）")
    parser.add_argument("--venues", default="okx")
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--flush", type=float, default=2.0)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.run:
        syms = [s.strip() for s in (args.symbols or args.symbol).split(",") if s.strip()]
        print(json.dumps(
            run_stream(venue=args.venues.strip() or "binance", symbols=syms,
                       snapshot_path=args.snapshot or None, flush_seconds=args.flush),
            ensure_ascii=False, indent=2))
        return 0
    if not args.probe:
        parser.print_help()
        return 0
    snap = probe(venues=[v.strip() for v in args.venues.split(",") if v.strip()],
                 symbol=args.symbol, seconds=args.seconds,
                 snapshot_path=args.snapshot or None)
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":       # pragma: no cover - 手动探测入口
    sys.exit(_main())
