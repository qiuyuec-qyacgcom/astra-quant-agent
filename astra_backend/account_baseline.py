"""Atomic account performance baseline storage shared by admin and dashboard."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ROOT

BASELINE_FILE = ROOT / "data" / "account_initial_state.json"
BJ_TZ = timezone(timedelta(hours=8))
DEFAULT_CAPITAL = 10_000.0
MIN_CAPITAL = 1.0
MAX_CAPITAL = 1_000_000_000.0


def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def load_account_baseline(venue: Any = None) -> dict[str, Any]:
    """读取本金/台账基线。

    存储结构（双所改造，**向后兼容**）：
      {
        "initial_capital": 10000.0,          # ← OKX 的兼容镜像，老读者继续读这里
        "capital_by_venue": {"okx": 10000.0, "binance": 5000.0},
        "reset_time": "...", ...
      }

    老文件没有 `capital_by_venue` 时，OKX 取顶层 `initial_capital`（与改造前逐位一致），
    币安回落到 `INITIAL_CAPITAL` 环境变量/默认值——绝不把 OKX 的资金误当币安本金。
    `venue=None` 表示"当前生效交易所"，由 routing_policy 决定。
    """
    data: dict[str, Any] = {}
    if BASELINE_FILE.exists():
        try:
            loaded = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    env_default = _number(os.getenv("INITIAL_CAPITAL"), DEFAULT_CAPITAL)
    capital = _number(data.get("initial_capital"), env_default)

    by_venue = data.get("capital_by_venue")
    by_venue = dict(by_venue) if isinstance(by_venue, dict) else {}
    # OKX 沿用顶层值（兼容老文件），币安只认自己的分键
    okx_capital = _number(by_venue.get("okx", capital), capital)
    by_venue["okx"] = okx_capital
    if "binance" in by_venue:
        by_venue["binance"] = _number(by_venue.get("binance"), env_default)
    else:
        by_venue["binance"] = env_default

    target_venue = _resolve_venue(venue)
    venue_capital = _number(by_venue.get(target_venue), capital)
    env_evo_start = str(os.getenv("ASTRA_EVOLUTION_START_TIME", "")).strip()
    evo_start = env_evo_start or str(data.get("evolution_start_time") or data.get("reset_time") or "2026-09-01 00:00:00")
    out = {
        **data,
        # 顶层 initial_capital = **当前生效所**的本金：老读者（面板 ROI/盈亏）
        # 无需改造就跟着切换走，这正是"切换后自动使用切换后数据"的要求。
        "initial_capital": round(venue_capital, 2),
        # 双所分键：调用方需要指定所时读这里，不做二次推断
        "capital_by_venue": {k: round(float(v), 2) for k, v in by_venue.items()},
        "reset_time": str(data.get("reset_time") or "1970-01-01 00:00:00"),
        "evolution_start_time": evo_start,
    }
    # 仅在显式问某个所时回显 venue（缺省调用保持与改造前逐键一致的返回结构）
    if venue:
        out["venue"] = target_venue
    return out


def _resolve_venue(venue: Any = None) -> str:
    """None → 当前生效交易所；显式传入则原样归一。

    `routing_policy` 的缺省值是字符串 `"auto"`（评分路由，不是交易所名），
    这里必须把它归一成 OKX——否则会把 "auto" 当成一个"交易所"写进
    `capital_by_venue`，产生一个谁也不会读的分键。
    """
    key = str(venue or "").strip().lower()
    if key in ("", "auto"):
        try:
            from .exchanges.routing_policy import load_preferred_venue
            key = str(load_preferred_venue() or "").strip().lower()
        except Exception:
            key = ""
    return key if key in ("okx", "binance") else "okx"


def update_initial_capital(initial_capital: float, venue: Any = None) -> dict[str, Any]:
    capital = round(float(initial_capital), 2)
    if not MIN_CAPITAL <= capital <= MAX_CAPITAL:
        raise ValueError(f"初始本金必须在 {MIN_CAPITAL:.2f} 到 {MAX_CAPITAL:.2f} USDT 之间")
    from astra_backend.file_locks import file_lock

    target_venue = _resolve_venue(venue)
    with file_lock(BASELINE_FILE):
        return _write_baseline(capital, target_venue)


def _write_baseline(capital: float, venue: str = "okx") -> dict[str, Any]:
    """锁内完成 load → merge → 原子替换（审计 P3-6 家族收口）。

    顶层 `initial_capital` 的取值规则（老读者的兼容镜像，必须与"读者眼里的
    当前所"一致，否则面板会显示另一个所的资金）：
      · 写入的正是**当前生效所** ⇒ 顶层 = 新值（读者读到的就是刚存的数）；
      · 写入的是**非生效所**       ⇒ 顶层不动（保持当前所的值）。
    后一种情况下读者看到的仍是当前所本金，符合"切换后才生效"的预期。
    """
    previous = load_account_baseline()
    by_venue = dict(previous.get("capital_by_venue") or {})
    by_venue[venue] = capital
    active_venue = _resolve_venue(None)
    top_level = capital if venue == active_venue else float(by_venue.get(active_venue, capital))
    # 剔除读取期派生的只读字段（venue / okx_initial_capital 等），避免它们被回写进文件
    # 变成"永久的脏键"——尤其 venue="auto" 会被下一次读取当成真实键。
    _derived = {"venue", "okx_initial_capital", "capital_by_venue"}
    updated = {
        **{k: v for k, v in previous.items() if k not in _derived},
        "initial_capital": round(float(top_level), 2),
        "capital_by_venue": by_venue,
        f"{venue}_capital_updated_at": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "capital_updated_at": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".account-baseline-", suffix=".json", dir=BASELINE_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, BASELINE_FILE)
        os.chmod(BASELINE_FILE, 0o600)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return {
        "previous_initial_capital": previous["initial_capital"],
        **updated,
    }


def update_evolution_start_time(start_time: str) -> dict[str, Any]:
    """更新自进化复盘起始时间（过滤更早的人工历史交易）。"""
    st = str(start_time or "").strip()
    if not st:
        st = "2026-09-01 00:00:00"
    if len(st) == 10:
        st = f"{st} 00:00:00"
    from astra_backend.file_locks import file_lock

    with file_lock(BASELINE_FILE):
        previous = load_account_baseline()
        updated = {
            **previous,
            "evolution_start_time": st,
            "evolution_start_updated_at": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }
        BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".account-baseline-", suffix=".json", dir=BASELINE_FILE.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(updated, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, BASELINE_FILE)
            os.chmod(BASELINE_FILE, 0o600)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        return updated


def update_reset_time(reset_time: str) -> dict[str, Any]:
    """更新台账起算基线时刻（过滤更早的人工历史交易与前朝旧单）。"""
    st = str(reset_time or "").strip()
    if not st:
        raise ValueError("台账起算时间不能为空")
    if len(st) == 10:
        st = f"{st} 00:00:00"
    parsed_dt = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed_dt = datetime.strptime(st, fmt)
            st = parsed_dt.strftime("%Y-%m-%d %H:%M:%S")
            break
        except ValueError:
            continue
    if parsed_dt is None:
        raise ValueError(f"时间格式不合法：{reset_time}，请使用 YYYY-MM-DD HH:MM:SS")

    if st < "2026-01-01 00:00:00":
        raise ValueError(f"台账起算时间不得早于 2026-01-01 00:00:00：{st}")
    now_bj = datetime.now(BJ_TZ).replace(tzinfo=None)
    if parsed_dt > now_bj + timedelta(days=1):
        raise ValueError(f"台账起算时间不得晚于未来时间：{st}")

    from astra_backend.file_locks import file_lock

    with file_lock(BASELINE_FILE):
        previous = load_account_baseline()
        updated = {
            **previous,
            "reset_time": st,
            "reset_time_updated_at": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }
        BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".account-baseline-", suffix=".json", dir=BASELINE_FILE.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(updated, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, BASELINE_FILE)
            os.chmod(BASELINE_FILE, 0o600)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        return {
            "previous_reset_time": previous.get("reset_time"),
            **updated,
        }
