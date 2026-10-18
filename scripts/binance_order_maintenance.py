#!/usr/bin/env python3
"""Gateway 拥有的币安 pending-entry 维护入口。

移植自 cnlimiter/r20-quantum-trader 的 ``scripts/binance_order_maintenance.py``，
适配本仓：锁复用 ``astra_backend/exchanges/binance.py::make_binance_env`` +
``BinanceExchange``，交易周期锁复用 ``ai_factor_trader`` 的
``DATA_DIR/.ai_factor_trader.lock``（fcntl 非阻塞，POSIX）。

只做**对账**（reconcile），绝不新开仓、绝不重发未知订单；无凭证或无本地
pending 记录时零网络。
"""
from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = os.environ.get("ASTRA_DATA_DIR") or str(_PROJECT_ROOT / "data")
LOCK_NAME = ".ai_factor_trader.lock"


def trader_cycle_lock_path() -> Path:
    return Path(DATA_DIR) / LOCK_NAME


def run_pending_entry_maintenance() -> dict[str, Any]:
    """账户级对账。无凭证或无本地 pending 记录时不联网。"""
    result: dict[str, Any] = {
        "status": "skipped",
        "reason": "",
        "pending": 0,
        "blocked": False,
        "errors": [],
        "networked": False,
    }
    lock_handle = None
    try:
        lock_path = trader_cycle_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            lock_handle = None
            result["status"] = "busy"
            result["reason"] = "trader cycle lock held"
            return result

        from astra_backend.exchanges.binance import BinanceExchange, make_binance_env

        env = make_binance_env()
        if not env.configured:
            result["reason"] = "unconfigured"
            return result

        exchange = BinanceExchange(env)
        if not bool(exchange.has_pending_entries()):
            result["reason"] = "no_pending"
            return result

        result["networked"] = True
        reconciled = exchange.reconcile_pending_entries()
        if not isinstance(reconciled, dict):
            result["status"] = "error"
            result["blocked"] = True
            result["errors"] = ["reconcile_pending_entries returned a non-dict result"]
            result["reason"] = result["errors"][0]
            return result

        errors = [str(item).strip() for item in (reconciled.get("errors") or []) if str(item).strip()]
        try:
            pending = int(reconciled.get("pending"))
        except (TypeError, ValueError):
            pending = -1
            errors.append("pending count is not an integer")
        blocked = bool(reconciled.get("blocked"))
        if pending != 0:
            blocked = True
        result.update({
            "status": "error" if errors else "reconciled",
            "pending": pending,
            "blocked": blocked,
            "errors": errors,
            "terminal_events": reconciled.get("terminal_events") or [],
            "reason": "; ".join(errors) if errors else ("blocked" if blocked else "ok"),
        })
        return result
    except Exception as exc:
        result["status"] = "error"
        result["blocked"] = True
        message = f"{type(exc).__name__}: {exc}"
        result["errors"] = [message]
        result["reason"] = message
        return result
    finally:
        if lock_handle is not None:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                lock_handle.close()
            except OSError:
                pass


if __name__ == "__main__":
    payload = run_pending_entry_maintenance()
    errors = payload.get("errors") or []
    if payload.get("status") == "error" or errors:
        print(f"[OrderMaintenance] Error: {payload.get('reason') or '; '.join(errors)}")
        raise SystemExit(1)
    if payload.get("status") == "busy":
        print(f"[OrderMaintenance] Skip: {payload.get('reason')}")
        raise SystemExit(0)
    print(f"[OrderMaintenance] {payload.get('status')}: {payload.get('reason')}")
    raise SystemExit(0)
