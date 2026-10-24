"""Shared deterministic safety checks for trade quotes and risk gates.
No state, no price repair. The R:R floor comes from the single source of truth
in scripts/risk_constants.py (configurable via the admin risk-control page / .env)
and is re-read at call time, so a backend hot-reload propagates without restart.
"""
from __future__ import annotations

import math
from typing import Any, Tuple

try:
    from scripts.risk_constants import MIN_RISK_REWARD_RATIO, MAX_RISK_REWARD_RATIO
except ImportError:  # flat import when scripts/ itself is on sys.path
    from risk_constants import MIN_RISK_REWARD_RATIO, MAX_RISK_REWARD_RATIO

# ── R:R 上下限 · 调用期同步（2026-10 审计）────────────────────────────────
# 上面的模块级绑定是 import 期快照：后台「风控管理页」保存后只
# `importlib.reload(risk_constants)`，换不掉本模块这两个名字。交易子进程每个
# 巡检周期重新 import ⇒ 天然刷新；但 backend 是长驻进程，手动下单/平仓意图
# 经 binance 适配器走到这里时会继续用陈旧底线（改了页不生效，直到重启）。
# 因而每次校验前把单一事实源的最新值同步进来 —— 与 ai_brain_trader 门面同一手法。
#
# 测试补丁缝优先：名字被显式改过（`patch.object(order_risk, "MIN_RISK_REWARD_RATIO", …)`）
# 且不是本函数自己同步写进去的值时，同步跳过它 —— 且**不把补丁值记进
# LAST_SYNC**，否则补丁内的第二次调用会被误判成"自己同步的值"而遭覆盖。
_RR_BOUNDS_IMPORT_SNAPSHOT = {"min": MIN_RISK_REWARD_RATIO, "max": MAX_RISK_REWARD_RATIO}
_RR_BOUNDS_LAST_SYNC = {}


def _sync_rr_bounds() -> None:
    """把后台风控管理页的最新 R:R 上下限同步进模块全局（显式补丁改过的项跳过）。"""
    try:
        from scripts.risk_constants import (
            MAX_RISK_REWARD_RATIO as _live_max,
            MIN_RISK_REWARD_RATIO as _live_min,
        )
    except ImportError:  # flat import when scripts/ itself is on sys.path
        from risk_constants import (
            MAX_RISK_REWARD_RATIO as _live_max,
            MIN_RISK_REWARD_RATIO as _live_min,
        )
    _g = globals()
    for _key, _name, _live in (("min", "MIN_RISK_REWARD_RATIO", _live_min),
                               ("max", "MAX_RISK_REWARD_RATIO", _live_max)):
        _snapshot = _RR_BOUNDS_IMPORT_SNAPSHOT[_key]
        _current = _g.get(_name)
        if _current != _snapshot and _current != _RR_BOUNDS_LAST_SYNC.get(_key, _snapshot):
            continue  # 调用方/测试显式改过 ⇒ 尊重补丁，不覆盖也不记录
        _g[_name] = _live
        _RR_BOUNDS_LAST_SYNC[_key] = _live


def validate_quote_geometry_and_rr(
    action: str,
    entry: Any,
    tp: Any,
    sl: Any,
    enforce_max_rr: bool = False,
    confidence: float = 0.0,
    min_rr_floor: float = 0.0,
) -> Tuple[bool, str, float]:
    """Validates that opening quote prices are positive, finite numbers satisfying
    action-specific geometry, and that the calculated risk-reward ratio meets or exceeds
    the configurable floor (ASTRA_MIN_RISK_REWARD, default 2.0).
    Also supports dynamic expected value verification when confidence >= 80% and rr >= 1.2:
    E = P*RR - (1-P)*1.0 >= +0.30R.
    Returns (is_valid, failure_reason, rr_ratio).
    """
    # 后台风控管理页改值后，import 期绑定的上下限副本不会自己刷新 ⇒ 校验前同步
    _sync_rr_bounds()
    raw_act = str(action or "").upper()
    if raw_act not in {"BUY_LONG", "SELL_SHORT"}:
        return False, f"不支持的开仓方向: {action}", 0.0

    try:
        e = float(entry)
        t = float(tp)
        s = float(sl)
    except (TypeError, ValueError, OverflowError):
        return False, "核心风控拦截：入场价、止盈价、止损价必须是有效数字", 0.0

    if not (math.isfinite(e) and math.isfinite(t) and math.isfinite(s)):
        return False, "核心风控拦截：入场价、止盈价、止损价必须是有限数值 (NaN/Inf 拒绝)", 0.0

    if e <= 0 or t <= 0 or s <= 0:
        return False, "核心风控拦截：入场价、止盈价、止损价必须大于 0", 0.0

    if raw_act == "BUY_LONG":
        if not (s < e < t):
            return False, f"核心风控拦截：买多几何不合法 (须 止损 {s} < 限价 {e} < 止盈 {t})", 0.0
        risk = e - s
        reward = t - e
    else:  # SELL_SHORT
        if not (t < e < s):
            return False, f"核心风控拦截：卖空几何不合法 (须 止盈 {t} < 限价 {e} < 止损 {s})", 0.0
        risk = s - e
        reward = e - t

    if risk <= 0:
        return False, "核心风控拦截：单笔承担风险必须大于 0", 0.0

    rr = reward / risk
    if not math.isfinite(rr):
        return False, "核心风控拦截：盈亏比计算异常", 0.0

    # 判定值必须与展示值一致：二进制浮点会把算术上正好 2.00 的报价算成
    # 1.9999999999999998，直接用原始 float 比较就会出现
    # 「当前 R:R = 2.00:1，底线 2.0:1，但盈亏比不足」的自相矛盾拒单。
    # 两侧统一按展示精度（2 位小数）归一后再比较，让门禁与提示文案不可能打架。
    rr_gate = round(rr, 2)
    floor_gate = round(float(MIN_RISK_REWARD_RATIO or 0.0), 2)

    # 绝对系统安全底线：任何情况下盈亏比不得低于 1.2:1（防手续费与滑点倒挂）
    ABS_MIN_RR = 1.2
    effective_floor = max(ABS_MIN_RR, round(float(min_rr_floor or 0.0), 2))

    if rr_gate < floor_gate:
        conf_val = float(confidence or 0.0)
        # 高置信度动态正期望放行：置信度 >= 80% 且期望收益率 E = P*RR - (1-P)*1.0 >= +0.30R，且必须满足绝对安全底线 (>= 1.2R)
        if conf_val >= 80.0 and rr_gate >= effective_floor:
            p_win = conf_val / 100.0
            expected_val = p_win * rr - (1.0 - p_win) * 1.0
            if expected_val >= 0.30:
                pass  # 动态正期望放行
            else:
                return False, f"核心风控拦截：盈亏比不足 {floor_gate:.2f} (当前 R:R = {rr_gate:.2f}:1，底线 {floor_gate:.2f}:1)", rr
        else:
            return False, f"核心风控拦截：盈亏比不足 {floor_gate:.2f} (当前 R:R = {rr_gate:.2f}:1，底线 {floor_gate:.2f}:1)", rr

    if enforce_max_rr:
        _cur_max_rr = float(MAX_RISK_REWARD_RATIO or 0.0)
        if _cur_max_rr > 0 and rr > _cur_max_rr + 1e-4:
            return False, f"核心风控拦截：盈亏比超出上限 {_cur_max_rr:.1f}:1 (当前 R:R = {rr:.2f}:1，止盈过远拒单)", rr

    return True, "", rr
