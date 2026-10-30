"""High-Performance Zero-Process Direct Public Market Data Service (market_data_service.py).

Public market data harvesting (tickers, orderbooks, indicators, candles) runs on
persistent connection-pooled HTTP Keep-Alive sessions with pure-Python fallbacks.
Failover chain: www.okx.com -> aws.okx.com -> local math.
Zero process-spawning layers; public endpoints need no credentials.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from .candle_data import closed_okx_candles
except ImportError:  # 从 scripts 目录直接执行时
    from candle_data import closed_okx_candles

logger = logging.getLogger("market_data_service")

OKX_PUBLIC_HOSTS = [
    "https://www.okx.com",
    "https://aws.okx.com",
]

# 币安行情接入（因子引擎按 venue 分流）。BinanceExchange 的公开方法无需凭证，
# 且返回结构已与 OKX 口径对齐（K线 9 列 newest-first、ticker OKX 风格字段、funding ×100）。
# 惰性实例化：只在真正走币安时才 import/构造，避免 OKX 路径引入币安依赖开销。
_BINANCE_EXCHANGE = None
_BINANCE_EXCHANGE_LOCK = threading.Lock()

# ── 币安 WebSocket 行情快照（常驻采集进程写、本模块读）──────────────────────
# 目的：把 ticker / K 线从 REST 轮询搬到 WS —— **WS 不消耗 2400 权重/分钟的额度**
# （只受限连接数与消息频率），这是币安官方对 -1003 的建议。
# 快照缺失/过期/版本不认 ⇒ 自动回退 REST（fail-open：流断了也不至于没数据）。
_WS_SNAPSHOT_FILE = str(Path(__file__).resolve().parents[1] / "data" / "binance_ws_stream.json")
_WS_TICKER_TTL_S = float(os.getenv("ASTRA_BINANCE_WS_TICKER_TTL", "30") or 30.0)
_WS_CANDLE_TTL_S = float(os.getenv("ASTRA_BINANCE_WS_CANDLE_TTL", "90") or 90.0)


def _fmt_ws(value: Any) -> str:
    return "" if value is None else str(value)


_WS_SNAPSHOT_CACHE: Dict[str, Any] = {"key": None, "snap": {}}


def _ws_snapshot(max_age_s: float) -> Dict[str, Any]:
    """读 WS 行情快照；不存在/损坏/过期/版本不认 → `{}`（调用方回退 REST）。

    ★ 按 `(mtime_ns, size)` **缓存解析结果**：快照含 240 根 K 线 × 多标的 + 20 档深度，
    每 2 秒刷新一次；若每次取数都 `json.load`，单次就是几 MB 的反序列化，
    下单前锚价、因子引擎逐币取数都会被它拖慢。这里只在**文件真变了**时解析一次。
    """
    try:
        stat = os.stat(_WS_SNAPSHOT_FILE)
    except OSError:
        return {}
    stat_key = (stat.st_mtime_ns, stat.st_size)
    if _WS_SNAPSHOT_CACHE.get("key") != stat_key:
        try:
            from .market_stream import load_market_snapshot
        except ImportError:                   # 从 scripts 目录直接执行时
            try:
                from market_stream import load_market_snapshot
            except ImportError:
                return {}
        _WS_SNAPSHOT_CACHE["key"] = stat_key
        _WS_SNAPSHOT_CACHE["snap"] = load_market_snapshot(_WS_SNAPSHOT_FILE) or {}
    snap = _WS_SNAPSHOT_CACHE.get("snap") or {}
    if not snap:
        return {}
    try:
        written = int(snap.get("written_at_ms") or 0)
    except (TypeError, ValueError):
        return {}
    if written <= 0 or (time.time() * 1000 - written) > max_age_s * 1000:
        return {}
    return snap


def _ws_ticker(inst_id: str) -> Optional[Dict[str, Any]]:
    """WS 快照里的 ticker → OKX 同形状（无则 None ⇒ 回退 REST）。"""
    snap = _ws_snapshot(_WS_TICKER_TTL_S)
    row = ((snap.get("tickers") or {}).get(inst_id)) if snap else None
    if not isinstance(row, dict) or row.get("last") in (None, ""):
        return None
    return {
        "instId": inst_id,
        "last": _fmt_ws(row.get("last")),
        "bidPx": _fmt_ws(row.get("bid")),
        "askPx": _fmt_ws(row.get("ask")),
        "open24h": _fmt_ws(row.get("open24h")),
        "vol24h": _fmt_ws(row.get("vol24h")),
        "ts": _fmt_ws(row.get("exchange_ms")),
    }


_WS_DEPTH_TTL_S = float(os.getenv("ASTRA_BINANCE_WS_DEPTH_TTL", "5") or 5.0)
_WS_FUNDING_TTL_S = float(os.getenv("ASTRA_BINANCE_WS_FUNDING_TTL", "30") or 30.0)


def _ws_funding_pct(inst_id: str) -> Optional[float]:
    """WS 标记价流里的当期资金费率（**百分比**，与适配器 REST 分支同口径）。"""
    snap = _ws_snapshot(_WS_FUNDING_TTL_S)
    row = ((snap.get("marks") or {}).get(inst_id)) if snap else None
    if not isinstance(row, dict):
        return None
    try:
        return round(float(row.get("funding_rate")) * 100, 4)
    except (TypeError, ValueError):
        return None


def _ws_depth(inst_id: str) -> Optional[Dict[str, Any]]:
    """WS 20 档盘口（`{"bids": [[px, qty]], "asks": [...]}`）；空则在调用方回退 REST。"""
    snap = _ws_snapshot(_WS_DEPTH_TTL_S)
    row = ((snap.get("depth") or {}).get(inst_id)) if snap else None
    if not isinstance(row, dict):
        return None
    bids = row.get("bids") or []
    asks = row.get("asks") or []
    if not bids and not asks:
        return None
    return {"bids": bids, "asks": asks}


#: 决策链「数据质量」六条判据的最少 K 线根数（与 `scripts/brain/packages.py` 的
#: `required_market_data` 逐条对齐）。WS 快照攒够这个数就有继续决策的价值，
#: 不必等满整段 limit 根 —— 币安 1H/4H 要等 24h/64h 才够 limit，那时 REST 一旦
#: 不可用（-1003 封禁/限流）整包就成了"数据质量 invalid"。
_MIN_DECISION_BARS = {"15m": 12, "1H": 8, "4H": 6, "1h": 8, "4h": 6}


def _ws_candles(inst_id: str, bar: str, limit: int, *,
                min_rows: Optional[int] = None) -> List[List[str]]:
    """WS 快照里的 K 线（newest-first 9 列）。

    默认**不足 limit 根就不给**（让调用方回退 REST，保证指标用的是完整历史）；
    `min_rows` 显式放宽时，只要够这个下限就返回（REST 不可用时的兜底档）。
    """
    need = max(1, int(limit))
    floor = need if min_rows is None else max(1, int(min_rows))
    snap = _ws_snapshot(_WS_CANDLE_TTL_S)
    series = (((snap.get("klines") or {}).get(inst_id) or {}).get(bar)) if snap else None
    if not isinstance(series, list):
        return []
    rows = [row for row in series if isinstance(row, list) and len(row) >= 9]
    if len(rows) < floor:
        return []
    return rows[:need]


def _normalize_venue(venue: Optional[str]) -> Optional[str]:
    """把 venue 归一成小写；None/auto/okx → None（默认 OKX 路径，行为不变）。"""
    if venue is None:
        return None
    v = str(venue).strip().lower()
    if v in ("", "auto", "okx"):
        return None
    return v


def _binance_exchange():
    """返回惰性单例 BinanceExchange（公开行情面，无需私有凭证）。"""
    global _BINANCE_EXCHANGE
    if _BINANCE_EXCHANGE is None:
        with _BINANCE_EXCHANGE_LOCK:
            if _BINANCE_EXCHANGE is None:
                from astra_backend.exchanges.binance import BinanceExchange, make_binance_env

                _BINANCE_EXCHANGE = BinanceExchange(make_binance_env())
    return _BINANCE_EXCHANGE

# 行情取数可观测性（第 137 刀）：失败计数 + 耗时/成功率。
# ⚠️ 双拼写铁律：本模块既以顶层名 `market_data_service`（SCRIPTS_DIR 在 sys.path 上，
# 生产/门面即如此）被导入，也以 `scripts.market_data_service`（仓库根在 sys.path 上，
# 部分测试即如此）被导入 —— 两种拼写是**不同的模块实例**，故必须写成 try/except 兼容
# 两种路径（scripts/README.md §双拼写）。
try:
    from market_data_health import note_call, note_failure
except ImportError:                                    # pragma: no cover - 包导入路径
    from scripts.market_data_health import note_call, note_failure


def _call_kind(method: str, path: str) -> str:
    """把请求路径归一成**有界**的指标 kind（`okx_public_get_ticker` 之类）。

    绝不把整条 URL/带参数的路径当标签：那会让基数随调用爆炸（Prometheus 反模式），
    也会把 query 里的内容（instId/limit…）带进监控面。OKX 公共路径是固定小集合
    （ticker/candles/…），取最后一段即可 —— 故**先剥掉 query/fragment 再取尾段**。
    """
    clean = str(path or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")
    tail = clean.rsplit("/", 1)[-1] or "unknown"
    safe = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in tail)
    return f"okx_public_{method}_{safe}"


class MarketDataResponseError(RuntimeError):
    """交易所**回了话但没给数据**（HTTP≠200 或 code≠0）。

    它不是 Python 异常，但语义上同样是"这次取数失败"：调用方拿不到数据。
    第 137 刀的事故里正是这种"没有异常、只是没数据"的情形最难察觉，
    所以它必须与真异常**进同一本失败账**（否则指标里 failed_calls 与 failures
    两个口径会对不上，运维一眼看到两个不同的数就会失去信任）。
    """

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

_SESSION: Optional[requests.Session] = None
_SESSION_LOCK = threading.Lock()

# K线请求槽位：发请求前先留出最小间隔，避免并发调用者把 OKX 公共 IP 限流打爆。
# 独立运行的因子/仪表盘进程共享同一出口 IP，所以这里必须**进程内**限速。
_CANDLE_LOCK = threading.Lock()
_CANDLE_LAST_STARTED = 0.0
_CANDLE_MIN_INTERVAL = 0.12


def _throttle_candles() -> None:
    """K线请求前的最小间隔闸（进程内）。"""
    global _CANDLE_LAST_STARTED
    with _CANDLE_LOCK:
        wait = _CANDLE_MIN_INTERVAL - (time.monotonic() - _CANDLE_LAST_STARTED)
        if wait > 0:
            time.sleep(wait)
        _CANDLE_LAST_STARTED = time.monotonic()

# OKX bar 合法字面量（大小写敏感：分钟小写 m，小时/天/周/月大写）
_OKX_VALID_BARS = {
    "1m", "3m", "5m", "15m", "30m",
    "1H", "2H", "4H", "6H", "12H",
    "1D", "3D", "1W", "1M", "3M",
    "1Hutc", "4Hutc", "1Dutc", "1Wutc", "1Mutc", "1Dutc8", "1Wutc8", "1Mutc8",
}


def normalize_bar(bar: str) -> str:
    """Normalize K线周期到 OKX 合法字面量（大小写容错：1h→1H、4h→4H、15M→15m）。

    OKX /market/candles 与 indicators 接口对 bar/timeframe 参数严格区分大小写：
    小写 1h/4h 一律报 Parameter bar error，导致「1H/4H 数据拿不到」——
    所有入口统一先过这里。已是合法值（含 1M 月份大写特例）原样返回。
    """
    raw = str(bar or "").strip()
    if not raw:
        return "15m"
    if raw in _OKX_VALID_BARS:
        return raw
    low = raw.lower()
    for unit, canon in (("h", "H"), ("d", "D"), ("w", "W")):
        if low.endswith(unit) and low[: -1].isdigit():
            return low[: -1] + canon
    if low.endswith("m") and low[: -1].isdigit():
        n = int(low[: -1])
        if n in (1, 3, 5, 15, 30):
            return f"{n}m"
    return raw  # 未知/非法字面量交给 OKX 报错，不在本地臆造


def get_market_session() -> requests.Session:
    """Thread-safe persistent connection-pooled requests session."""
    global _SESSION
    if _SESSION is None:
        with _SESSION_LOCK:
            if _SESSION is None:
                s = requests.Session()
                s.headers.update(DEFAULT_HEADERS)
                retries = Retry(
                    total=2,
                    backoff_factor=0.35,
                    # 429：OKX 公共行情按 IP 限频 40req/2s，引擎+面板共出口 IP 的部署
                    # 易触发——重试（尊重 Retry-After）吸收突发，避免 1H/4H K线偶发拿空
                    status_forcelist=[429, 500, 502, 503, 504],
                    raise_on_status=False,
                )
                adapter = HTTPAdapter(
                    pool_connections=12,
                    pool_maxsize=24,
                    max_retries=retries,
                    pool_block=False,
                )
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                _SESSION = s
    return _SESSION


def _public_get(path: str, params: Optional[Dict[str, Any]] = None, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """Try primary then fallback OKX public endpoints.

    K线路径（/market/candles）额外启用**重试 + 限流**：行情K线是交易/回测的
    事实源，偶发限流（50011）或空包（code=0 但 data 为空）不应直接当成"没数据"
    而应重试；同时进程内限速避免并发把 OKX 公共 IP 打爆。
    """
    session = get_market_session()
    kind = _call_kind("get", path)
    is_candles = path == "/api/v5/market/candles"
    for attempt in range(3 if is_candles else 1):
        if attempt:
            time.sleep(0.5 * attempt)  # 指数退避：0.5s / 1.0s
        if is_candles:
            _throttle_candles()
        retryable = False
        for base in OKX_PUBLIC_HOSTS:
            url = f"{base}{path}"
            started = time.time()
            try:
                resp = session.get(url, params=params, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    code = str(data.get("code", "0"))
                    if code == "0" and (not is_candles or data.get("data")):
                        note_call(kind, time.time() - started, ok=True)
                        return data
                    reason = f"code={code} msg={str(data.get('msg'))[:80]}"
                    if code == "0" and is_candles:
                        reason += " empty candle data"
                    # K线可重试的瞬时错误：空包 / 限流 50011 / 服务繁忙 50004 / 超频 50013
                    retryable = code in {"0", "50011", "50004", "50013"}
                else:
                    reason = f"http={resp.status_code}"
                note_call(kind, time.time() - started, ok=False)
                note_failure(kind, MarketDataResponseError(reason))
            except Exception as exc:
                # 第 137 刀的教训：静默 `except` 会让"现价恒 0 / 30 小时无信号"。
                # 取值行为一字不变（仍旧吞掉、仍旧换下一个 host），但**必须留痕**。
                note_call(kind, time.time() - started, ok=False)
                note_failure(kind, exc)
                if is_candles:
                    print(f"[行情K线] host={base} instId={params.get('instId') if params else ''} "
                          f"bar={params.get('bar') if params else ''} attempt={attempt + 1} error={exc}",
                          flush=True)
                logger.debug("Public GET %s failed on %s: %s", path, base, exc)
        if not retryable:
            break
    return None


def _public_post(path: str, payload: Dict[str, Any], timeout: float = 4.0) -> Optional[Dict[str, Any]]:
    """Try primary then fallback OKX public POST endpoints."""
    session = get_market_session()
    headers = {"Content-Type": "application/json"}
    kind = _call_kind("post", path)
    for base in OKX_PUBLIC_HOSTS:
        url = f"{base}{path}"
        started = time.time()
        try:
            resp = session.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if str(data.get("code", "0")) == "0":
                    note_call(kind, time.time() - started, ok=True)
                    return data
                reason = f"code={data.get('code')} msg={str(data.get('msg'))[:80]}"
            else:
                reason = f"http={resp.status_code}"
            note_call(kind, time.time() - started, ok=False)
            note_failure(kind, MarketDataResponseError(reason))
        except Exception as exc:
            note_call(kind, time.time() - started, ok=False)
            note_failure(kind, exc)
            logger.debug("Public POST %s failed on %s: %s", path, base, exc)
    return None


# ---------------------------------------------------------------------------
# 1. Ticker & Bulk Tickers
# ---------------------------------------------------------------------------

def fetch_ticker(inst_id: str, timeout: float = 3.5, venue: Optional[str] = None,
                 live: bool = False) -> Optional[Dict[str, Any]]:
    """Fetch one instrument ticker: www→aws 双域 REST 直连，失败返回 None。

    venue="binance" 时走币安公开行情（返回结构已与 OKX 对齐），其余走 OKX。

    `live=True`：**强制实时 REST**，不读 WS 快照。下单锚价必须用它——
    WS 快照最多可能旧 1 秒，而穿价闸（默认 0.5%）是拿锚价做比较的，
    剧烈波动时旧锚价会把合法的「回踩挂单」误判成穿价幻觉而拒单。
    一次调用权重 2，下单是 15 分钟级事件，代价可忽略。
    """
    v = _normalize_venue(venue)
    if v == "binance":
        # WS 优先（零权重）；`live=True` 或快照缺失/过期才回退 REST。
        if not live:
            ws_row = _ws_ticker(inst_id)
            if ws_row is not None:
                return ws_row
        try:
            row = _binance_exchange().ticker(inst_id)
            return row if isinstance(row, dict) else None
        except Exception as exc:
            logger.debug("Binance ticker failed for %s: %s", inst_id, exc)
            return None
    data = _public_get("/api/v5/market/ticker", params={"instId": inst_id}, timeout=timeout)
    if data and data.get("data"):
        return data["data"][0]
    return None


def fetch_tickers_bulk(inst_type: str = "SWAP", timeout: float = 4.0, venue: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Fetch all instrument tickers in ONE single network request."""
    v = _normalize_venue(venue)
    if v == "binance":
        try:
            return _binance_exchange().tickers()
        except Exception as exc:
            logger.debug("Binance tickers failed: %s", exc)
            return {}
    data = _public_get("/api/v5/market/tickers", params={"instType": inst_type}, timeout=timeout)
    if data and data.get("data"):
        return {item["instId"]: item for item in data["data"] if "instId" in item}
    return {}


# ---------------------------------------------------------------------------
# 2. Orderbook Depth
# ---------------------------------------------------------------------------

def fetch_orderbook_depth(inst_id: str, sz: int = 5, timeout: float = 3.5, venue: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch orderbook depth directly via REST. Returns {'bids': [...], 'asks': [...]} —
    双域直连零进程，无备源（深度语义场所间不可比）。

    venue="binance" 时走币安盘口，并经 `market_normalizer.normalize_depth` 补 numOrders，
    使下游 `compute_depth_factors` 的稳健口径仍可工作。
    """
    v = _normalize_venue(venue)
    if v == "binance":
        try:
            from market_normalizer import normalize_depth
        except ImportError:  # pragma: no cover - 包导入路径
            from scripts.market_normalizer import normalize_depth
        # WS 20 档盘口（`@depth20@100ms`）零权重；缺失/过期才回退 REST。
        ws_book = _ws_depth(inst_id)
        if ws_book is not None:
            return normalize_depth(ws_book, venue=v)
        try:
            raw = _binance_exchange().orderbook(inst_id, sz=sz)
        except Exception as exc:
            logger.debug("Binance orderbook failed for %s: %s", inst_id, exc)
            return None
        return normalize_depth(raw, venue=v)
    data = _public_get("/api/v5/market/books", params={"instId": inst_id, "sz": sz}, timeout=timeout)
    if data and data.get("data"):
        return data["data"][0]
    return None


# ---------------------------------------------------------------------------
# 3. Technical Indicators (ADX, KDJ, BBWIDTH, CMF, RSI, etc.)
# ---------------------------------------------------------------------------

def _indicator_key(name: Any) -> str:
    """指标名的**唯一**规范化形态（大写、去掉 `-` 与 `_`）。

    为什么必须只有一处（第一百八十四刀）：本模块原来有**三种**写法 ——
    MCP 分支 `ind.upper().replace("-","")`、REST 兜底分支 `ind.upper()`（**保留横杠**）、
    本地计算/单指标 `ind.upper().replace("-","").replace("_","")`。
    ⇒ 同一个指标可能同时存在两种键（`"EMA20"` 与 `"EMA-20"`），后果有两个方向：
      - **读者取不到**：消费方按一种拼法取值、生产者按另一种存 ⇒ 静默"没有"（读不到≠没有）；
      - **`missing` 判定失明**：它按去横杠比较，于是 REST 已存 `"EMA-20"` 时仍判为缺失 ⇒
        每轮都白算一遍本地指标（浪费算力，且可能把同一指标写成两个键）。
    今天线上消费方用的名字（`adx`/`kdj`/`bbwidth`/`cmf`）都不带分隔符，所以没炸 —— 属**潜在**缺陷。
    """
    return str(name or "").upper().replace("-", "").replace("_", "")


def _local_math_indicators(
    inst_id: str,
    indicators: List[str],
    bar: str = "1H",
    venue: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """末级兜底：当 OKX MCP 指标接口与 REST 均不可用时（部署环境常见），
    用本地蜡烛（自带 www→aws 双域容灾）纯 Python 计算 ADX/KDJ/BBWIDTH/CMF。
    输出与 OKX 官方口径对齐的字符串数值；样本不足时返回空 dict 让上层维持缺省。

    币安无官方指标接口，直接走本兜底；`venue` 透传给 `fetch_candles` 保证
    币安模式用币安蜡烛算 ADX（严禁跨所用 OKX 蜡烛冒充）。
    """
    rows = fetch_candles(inst_id, bar=bar, limit=120, venue=venue)
    if not rows:
        return {}
    try:
        chron = list(reversed(rows))
        highs = [float(r[2]) for r in chron]
        lows = [float(r[3]) for r in chron]
        closes = [float(r[4]) for r in chron]
        vols = [float(r[5]) for r in chron]
    except (ValueError, IndexError):
        return {}

    result: Dict[str, Dict[str, str]] = {}
    for ind in indicators:
        key = _indicator_key(ind)
        try:
            if key == "ADX" and len(closes) >= 30:
                trs, pdms, ndms = [], [], []
                for i in range(1, len(closes)):
                    tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
                    up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
                    trs.append(tr)
                    pdms.append(up if (up > dn and up > 0) else 0.0)
                    ndms.append(dn if (dn > up and dn > 0) else 0.0)
                p = 14
                atr = sum(trs[:p])
                pdm = sum(pdms[:p])
                ndm = sum(ndms[:p])
                dxs = []
                for i in range(p, len(trs)):
                    atr = atr - atr / p + trs[i]
                    pdm = pdm - pdm / p + pdms[i]
                    ndm = ndm - ndm / p + ndms[i]
                    pdi = 100.0 * pdm / atr if atr > 0 else 0.0
                    ndi = 100.0 * ndm / atr if atr > 0 else 0.0
                    denom = pdi + ndi
                    dxs.append(100.0 * abs(pdi - ndi) / denom if denom > 0 else 0.0)
                if len(dxs) >= p:
                    adx = sum(dxs[:p]) / p
                    for dx in dxs[p:]:
                        adx = (adx * (p - 1) + dx) / p
                    result["ADX"] = {"adx": f"{adx:.2f}"}
            elif key == "KDJ" and len(closes) >= 9:
                k = d = 50.0
                for i in range(8, len(closes)):
                    hh = max(highs[i - 8: i + 1])
                    ll = min(lows[i - 8: i + 1])
                    rsv = (closes[i] - ll) / (hh - ll) * 100.0 if hh > ll else 50.0
                    k = (2.0 * k + rsv) / 3.0
                    d = (2.0 * d + k) / 3.0
                j = 3.0 * k - 2.0 * d
                result["KDJ"] = {"k": f"{k:.2f}", "d": f"{d:.2f}", "j": f"{j:.2f}"}
            elif key in ("BBWIDTH", "BBANDWIDTH") and len(closes) >= 20:
                window = closes[-20:]
                mid = sum(window) / 20.0
                sd = (sum((x - mid) ** 2 for x in window) / 20.0) ** 0.5
                if mid > 0:
                    result["BBWIDTH"] = {"bbWidth": f"{(4.0 * sd / mid * 100.0):.2f}"}
            elif key == "CMF" and len(closes) >= 21:
                num = den = 0.0
                for i in range(-20, 0):
                    rng = highs[i] - lows[i]
                    mf = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / rng if rng > 0 else 0.0
                    num += mf * vols[i]
                    den += vols[i]
                result["CMF"] = {"cmf": f"{(num / den):.4f}" if den > 0 else "0.0000"}
        except Exception as exc:
            logger.debug("Local indicator %s failed for %s: %s", key, inst_id, exc)
    return result


def fetch_indicators_batch(
    inst_id: str,
    indicators: List[str],
    bar: str = "1H",
    timeout: float = 4.0,
    venue: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch multiple technical indicators in ONE SINGLE HTTP POST request.
    
    Batches 4-6 per-instrument indicator queries into 1 fast call.
    Returns: {"ADX": {"adx": "20.1", ...}, "KDJ": {"k": "...", "d": "...", "j": "..."}, ...}

    币安无官方指标接口 → 直接走本地蜡烛兜底（venue 透传，保证用币安蜡烛）。
    """
    v = _normalize_venue(venue)
    if v == "binance":
        return _local_math_indicators(inst_id, indicators, bar, venue=v)
    bar = normalize_bar(bar)
    ind_configs = {ind.upper(): {} for ind in indicators}
    payload = {
        "instId": inst_id,
        "timeframes": [bar],
        "indicators": ind_configs,
    }
    
    data = _public_post("/api/v5/aigc/mcp/indicators", payload, timeout=timeout)
    result: Dict[str, Dict[str, Any]] = {}
    
    if data and data.get("data"):
        try:
            tfs = data["data"][0].get("data", [{}])[0].get("timeframes", {}).get(bar, {}).get("indicators", {})
            for ind in indicators:
                key = _indicator_key(ind)
                items = tfs.get(key, [])
                if items and isinstance(items[0], dict):
                    result[key] = items[0].get("values", {})
            if result:
                return result
        except Exception:
            pass
    
    # If MCP endpoint failed, fallback to querying individual indicator via REST or local math
    for ind in indicators:
        key = _indicator_key(ind)
        if key not in result:
            val = fetch_single_indicator(inst_id, ind, bar=bar, timeout=timeout, venue=v)
            if val:
                result[key] = val
    
    # 末级兜底：MCP 批量接口与逐指标 REST 全灭 → 本地蜡烛纯 Python 计算
    missing = [ind for ind in indicators if _indicator_key(ind) not in result]
    if missing:
        for k, v in _local_math_indicators(inst_id, missing, bar, venue=v).items():
            result.setdefault(k, v)
    
    return result


def fetch_single_indicator(
    inst_id: str,
    indicator: str,
    bar: str = "1H",
    timeout: float = 3.5,
    venue: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch or compute a single indicator: MCP REST，失败落纯 Python 本地数学。"""
    v = _normalize_venue(venue)
    key = _indicator_key(indicator)
    if v == "binance":
        return _local_math_indicators(inst_id, [key], bar, venue=v).get(key, {})
    bar = normalize_bar(bar)
    payload = {
        "instId": inst_id,
        "timeframes": [bar],
        "indicators": {key: {}},
    }
    data = _public_post("/api/v5/aigc/mcp/indicators", payload, timeout=timeout)
    if data and data.get("data"):
        try:
            tfs = data["data"][0].get("data", [{}])[0].get("timeframes", {}).get(bar, {}).get("indicators", {})
            items = tfs.get(key, [])
            if items and isinstance(items[0], dict):
                return items[0].get("values", {})
        except Exception:
            pass
    # 末级兜底：本地蜡烛 + 纯 Python 数学（MCP 端点不可达时的最后防线）
    return _local_math_indicators(inst_id, [key], bar).get(key, {})


# ---------------------------------------------------------------------------
# 4. Candles
# ---------------------------------------------------------------------------

def fetch_candles(
    inst_id: str,
    bar: str = "15m",
    limit: int = 45,
    timeout: float = 4.0,
    *,
    closed_only: bool = True,
    venue: Optional[str] = None,
) -> List[List[str]]:
    """Fetch candles directly from OKX Official Market REST API with Keep-Alive.

    默认只返回**已收盘**（confirm=1）的K线，避免把正在形成中的 bar 当确认数据
    用于交易分析；图表可通过 `closed_only=False` 主动取回形成中的最新 bar。

    venue="binance" 时走币安 `candles()`（已返回 9 列 newest-first，confirm 列已按
    收盘时间算好）。币安不适用 OKX 的 closed_only 二次过滤，直接透传 limit。
    """
    v = _normalize_venue(venue)
    if v == "binance":
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 45
        limit = max(1, min(limit, 1500))
        # WS 优先（零权重）；不够 limit 根/快照过期 ⇒ 回退 REST。
        ws_rows = _ws_candles(inst_id, bar, limit)
        if ws_rows:
            return closed_okx_candles(ws_rows)[:limit] if closed_only else ws_rows
        rows: List[List[str]] = []
        try:
            rows = _binance_exchange().candles(inst_id, bar=bar, limit=limit)
        except Exception as exc:
            logger.debug("Binance candles failed for %s: %s", inst_id, exc)
            # 静默 `return []` 会让"币安 K 线取不到"伪装成"数据质量 invalid" ——
            # 必须进失败账（/metrics 可见），否则排障只剩猜测。
            note_failure("binance_candles", exc)
            rows = []
        if not isinstance(rows, list):
            rows = []
        if not rows:
            # REST 不可用（-1003 封禁/限流/网络）时的**最后一档**：WS 快照里已有
            # 够判据用的根数就先用它，别把"币安 K 线空"直接变成整包 invalid。
            _floor = _MIN_DECISION_BARS.get(str(bar), 1)
            rows = _ws_candles(inst_id, bar, limit, min_rows=_floor)
            if rows:
                print(f"[binance candles] warn {inst_id} {bar} REST 不可用，"
                      f"改用 WS 快照 {len(rows)} 根（判据下限 {_floor}）")
        # 币安 candles() 已 newest-first 且 confirm 列已算好；closed_only 时过滤未收盘
        if closed_only:
            return closed_okx_candles(rows)[:limit]
        return rows
    bar = normalize_bar(bar)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 45
    limit = max(1, min(limit, 300))  # OKX 单次上限 300，超限直接报错返回空
    # closed_only 时多要一根，过滤掉未收盘 bar 后仍能凑足 limit 根
    request_limit = min(limit + 1, 300) if closed_only else limit
    data = _public_get(
        "/api/v5/market/candles",
        params={"instId": inst_id, "bar": bar, "limit": request_limit},
        timeout=timeout,
    )
    if data and data.get("data"):
        if closed_only:
            return closed_okx_candles(data["data"])[:limit]
        return data["data"]
    return []


# ---------------------------------------------------------------------------
# 5. Funding Rate & Open Interest
# ---------------------------------------------------------------------------

def fetch_funding_rate(inst_id: str, timeout: float = 3.5, venue: Optional[str] = None) -> Optional[float]:
    """Fetch current perpetual funding rate as percentage."""
    v = _normalize_venue(venue)
    if v == "binance":
        # WS 标记价流（`@markPrice@1s`）零权重；缺失/过期才回退 REST。
        ws_rate = _ws_funding_pct(inst_id)
        if ws_rate is not None:
            return ws_rate
        try:
            return _binance_exchange().funding_rate(inst_id)
        except Exception as exc:
            logger.debug("Binance funding rate failed for %s: %s", inst_id, exc)
            return None
    data = _public_get("/api/v5/public/funding-rate", params={"instId": inst_id}, timeout=timeout)
    if data and data.get("data"):
        try:
            return round(float(data["data"][0].get("fundingRate", 0.0)) * 100, 4)
        except (ValueError, TypeError):
            pass
    return None


def fetch_open_interest(inst_id: str, timeout: float = 3.5, venue: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch open interest data."""
    v = _normalize_venue(venue)
    if v == "binance":
        try:
            raw = _binance_exchange().open_interest(inst_id)
        except Exception as exc:
            logger.debug("Binance open interest failed for %s: %s", inst_id, exc)
            return None
        try:
            from market_normalizer import normalize_oi
        except ImportError:  # pragma: no cover - 包导入路径
            from scripts.market_normalizer import normalize_oi
        return normalize_oi(raw, venue=v)
    data = _public_get("/api/v5/public/open-interest", params={"instId": inst_id}, timeout=timeout)
    if data and data.get("data"):
        return data["data"][0]
    return None


def fetch_long_short_ratio(inst_id: str, timeout: float = 3.5, venue: Optional[str] = None) -> Optional[float]:
    """当前多空比（全局账户多空比，5m 粒度）。

    venue="binance" 走币安公开数据门户 `/futures/data/globalLongShortAccountRatio`
    （返回 `longShortRatio` 数值）；其余走 OKX Rubik `long-short-account-ratio`。
    两侧语义一致（全局账户多空比），直接返回 float；取不到返回 None（fail-soft）。
    """
    v = _normalize_venue(venue)
    if v == "binance":
        try:
            return _binance_exchange().long_short_ratio(inst_id)
        except Exception as exc:
            logger.debug("Binance long/short ratio failed for %s: %s", inst_id, exc)
            return None
    # OKX Rubik：返回 [[ts, longShortRatio], ...]，取最新一条的数值
    base = inst_id.split("-")[0] if "-" in inst_id else inst_id
    data = _public_get(
        "/api/v5/rubik/stat/contracts/long-short-account-ratio",
        params={"ccy": base, "period": "5m"}, timeout=timeout)
    rows = data.get("data") if data else None
    if rows and isinstance(rows[0], (list, tuple)) and len(rows[0]) > 1:
        try:
            return float(rows[0][1])
        except (TypeError, ValueError):
            return None
    return None


def fetch_taker_volume(inst_id: str, timeout: float = 3.5, venue: Optional[str] = None) -> Optional[Any]:
    """主动买卖量（taker buy/sell）。

    venue="binance" 走币安 `/futures/data/takerlongshortRatio`，返回单点
    ``{"buyVol": str, "sellVol": str}``（已按标记价换算 USDT notional）；
    其余走 OKX Rubik `taker-volume`，返回 ``[[ts, sellVol, buyVol], ...]`` 序列
    （newest-first，索引 1=卖、索引 2=买）。

    ⚠️ 两侧返回**形状不同**：币安是单点 dict、OKX 是序列。调用方需按 venue 区分，
    不得假设统一形状。取不到返回 None（fail-soft）。
    """
    v = _normalize_venue(venue)
    if v == "binance":
        try:
            return _binance_exchange().taker_volume(inst_id)
        except Exception as exc:
            logger.debug("Binance taker volume failed for %s: %s", inst_id, exc)
            return None
    base = inst_id.split("-")[0] if "-" in inst_id else inst_id
    data = _public_get(
        "/api/v5/rubik/stat/taker-volume",
        params={"ccy": base, "instType": "CONTRACTS", "period": "5m"}, timeout=timeout)
    rows = data.get("data") if data else None
    if rows and isinstance(rows, list) and rows:
        return rows
    return None
