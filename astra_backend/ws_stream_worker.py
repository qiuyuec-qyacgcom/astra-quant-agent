"""币安 WebSocket 行情常驻采集的**进程内监督器**（默认关闭，显式开启才跑）。

## 为什么需要它

`market_data_service` 已改成「WS 快照优先、过期回退 REST」，但**总得有人去连 WS**。
本模块就是这个"人"：在后端进程里起一个守护线程跑 `scripts.market_stream.run_stream`，
把 ticker 与 15m/1H/4H K 线写到 `data/binance_ws_stream.json`。

收益：WS **不消耗**币安 2400 权重/分钟的 REST 额度（只受限连接数/消息频率），
这正是币安对 -1003 封 IP 的官方建议。

## 开关与边界

- `ASTRA_BINANCE_WS=1` 才启动；**默认 0（关闭）**，不影响任何既有部署；
- 仅当**生效所=币安**且标的池非空时运行；切回 OKX 会自动停掉；
- 标的池变化（增删币）由监督循环每 30s 检测一次，变了就重启采集；
- 快照文件缺失/过期 → 取数侧自动回退 REST，**流挂了也不会没数据**。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional, Tuple

_SNAPSHOT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "binance_ws_stream.json")

_STARTED = False
_START_LOCK = threading.Lock()
#: 切所信号：切到/切离币安时 `set()`，让监督器**立刻**重启或停掉采集（不用等满 30s）
_WAKE = threading.Event()


def _log(message: str) -> None:
    print(f"[binance_ws] {message}")


def notify_venue_changed() -> None:
    """切所后调用：唤醒监督器立刻重新判定（幂等，未启动时无副作用）。"""
    _WAKE.set()


def _enabled() -> bool:
    """默认**开启**；显式写 `ASTRA_BINANCE_WS=0/false/no/off` 才关闭。"""
    raw = str(os.getenv("ASTRA_BINANCE_WS", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _seed_enabled() -> bool:
    """开流前是否用 REST 历史 K 线播种（默认**开启**，`ASTRA_BINANCE_WS_SEED=0` 关闭）。

    为什么要播种：WS 只从连上那刻开始攒 K 线，而决策链要求 15m≥12 / 1H≥8 / 4H≥6 根
    （4H 得等 64 小时）—— 热身期只能回退 REST，币安 REST 一封就成了「数据质量 invalid」。
    """
    raw = str(os.getenv("ASTRA_BINANCE_WS_SEED", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _seed_limit() -> int:
    """播种根数（默认 240，与 `MarketState.max_bars` 对齐；下限 30 防呆）。"""
    try:
        value = int(os.getenv("ASTRA_BINANCE_WS_SEED_LIMIT", "240") or 240)
    except (TypeError, ValueError):
        value = 240
    return max(30, min(value, 1000))


def _stream_key() -> Optional[Tuple[str, Tuple[str, ...]]]:
    """当前应订阅的 (venue, symbols)。非币安/空池 → None（不采集）。"""
    try:
        from scripts.instrument_pool import active_pool_venue, load_instruments
        if active_pool_venue() != "binance":
            return None
        items = load_instruments(venue="binance") or []
        symbols = tuple(
            str(item.get("instId") or "").strip().upper()
            for item in items
            if isinstance(item, dict) and str(item.get("instId") or "").strip())
        return ("binance", tuple(sorted(set(symbols)))) if symbols else None
    except Exception as exc:
        _log(f"warn 读取标的池失败: {exc}")
        return None


def _spawn(key: Tuple[str, Tuple[str, ...]]) -> Tuple[Any, threading.Thread]:
    try:
        from scripts.market_stream import run_stream
    except Exception as exc:            # 依赖缺失（websockets）→ 明确告警，不静默
        _log(f"warn 无法加载采集器（已回退 REST）: {type(exc).__name__}: {exc}")
        raise

    venue, symbols = key
    stop = threading.Event()

    def _run() -> None:
        try:
            run_stream(venue=venue, symbols=list(symbols),
                       snapshot_path=_SNAPSHOT_FILE, stop=stop,
                       seed=_seed_enabled(), seed_limit=_seed_limit())
        except Exception as exc:            # 常驻线程绝不因为一次异常退出
            _log(f"warn 采集线程退出: {type(exc).__name__}: {exc}")

    thread = threading.Thread(target=_run, daemon=True, name="binance_ws_stream")
    thread.start()
    _log(f"已启动：{len(symbols)} 个标的 → {_SNAPSHOT_FILE}")
    return stop, thread


def _supervise() -> None:
    current_key: Optional[Tuple[str, Tuple[str, ...]]] = None
    stop: Any = None
    worker: Optional[threading.Thread] = None
    while True:
        try:
            key = _stream_key()
            if key != current_key:
                if stop is not None:
                    stop.set()
                if worker is not None:
                    worker.join(timeout=5.0)
                stop, worker = (None, None)
                current_key = key
                if key is not None:
                    stop, worker = _spawn(key)
                else:
                    _log("当前非币安或池为空 —— 采集已停止")
        except Exception as exc:
            _log(f"warn 监督循环异常: {type(exc).__name__}: {exc}")
        # 可被切所信号立刻唤醒；超时兜底每 30s 复查一次标的池
        _WAKE.wait(30.0)
        _WAKE.clear()


def start_binance_stream_worker() -> bool:
    """启动监督线程（**默认启动**；`ASTRA_BINANCE_WS=0` 显式关闭）。

    监督器自己按生效所判定：币安 → 采集；OKX/空池 → 不采集。切所通过
    `notify_venue_changed()` 立刻生效，无需重启后端。
    """
    global _STARTED
    if not _enabled():
        return False
    with _START_LOCK:
        if _STARTED:
            return False
        _STARTED = True
    threading.Thread(target=_supervise, daemon=True, name="binance_ws_supervisor").start()
    return True
