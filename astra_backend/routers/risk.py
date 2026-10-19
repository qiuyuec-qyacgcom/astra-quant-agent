"""Risk constants, instrument pool, baseline capital, and position close routes."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Body, Header, HTTPException, Query

from astra_backend.config import settings, refresh_settings
from astra_backend.settings_store import update_env, remove_env
from astra_backend.account_baseline import update_initial_capital, update_reset_time
from astra_backend.audit import record as audit_record
from astra_backend import risk_config
from astra_backend.dependencies import (
    DATA_DIR, MAX_POOL_SIZE, MIN_POOL_SIZE, REQUEST_SESSION,
    app_attr, admin_auth, okx, read_json, require_admin_header, require_superadmin,
)
from astra_backend.schemas import (
    RiskConfigUpdate,
    RiskResetRequest,
    InitialCapitalUpdate,
    ResetTimeUpdate,
    InstrumentAddRequest,
    InstrumentDeleteRequest,
    ManualCloseRequest,
    VenueSelectionUpdate,
)
from astra_backend.okx_trade_service import fast_close_confirmed
from scripts.okx_rest import OKXNotConfigured
from scripts.instrument_pool import (
    from_instrument,
    from_okx_instrument,
    load_instruments,
    mutate_instruments,
    normalize_venue,
    POOL_VENUES,
    pool_state,
    save_instruments,
    sync_instruments_state,
)
from astra_backend.account_baseline import load_account_baseline
from astra_backend.exchanges import routing_policy

router = APIRouter(tags=["risk"])


def _active_pool_venue() -> str:
    """当前**生效**的标的池所属交易所 = `venue_routing.json` 的 preferred_venue。

    这是"切换后自动使用切换后的标的池"的唯一判定源：账户、池、持仓全看这一个值，
    前端页头切换器写入它，所有读取方据此取池，不存在"切换了但仍走旧池"的缝隙。
    """
    try:
        from astra_backend.exchanges.routing_policy import load_preferred_venue
        return normalize_venue(load_preferred_venue())
    except Exception:
        return normalize_venue(None)


def _signature_of(obj) -> object:
    """取签名；取不到返回 None（视为"通配接受"）。

    - 不做缓存：`patch.object` 会替换模块属性，缓存只会读到上一个替身的签名。
    - 穿透 mock：MagicMock 自身的签名恒为 `(*args, **kwargs)`，真正的签名在
      `side_effect` 上（测试常用 `side_effect=_report`），必须递归取，否则
      会把"只收两个参数"的替身误判为"支持 venue"。
    """
    from unittest.mock import Mock as _Mock
    if isinstance(obj, _Mock):
        effect = getattr(obj, "side_effect", None)
        if effect is not None and not isinstance(effect, (Exception, BaseException)):
            return _signature_of(effect)
        return None
    try:
        import inspect
        return inspect.signature(obj)
    except (TypeError, ValueError):
        return None


def _accepts(func, param: str) -> bool:
    """函数是否可接收某个关键字参数。

    ⚠️ 不要用 `try: func(x, venue=v) except TypeError:` 判断签名——真实函数体内
    抛出的 TypeError 会被误当成"签名不匹配"而重试一次，导致替身被调用两次、
    断言收不到预期调用。这里只看签名（mock 通配为支持）。
    """
    sig = _signature_of(func)
    if sig is None:
        return True
    for p in sig.parameters.values():
        if p.kind is p.VAR_KEYWORD:
            return True
        if p.name == param and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY):
            return True
    return False


def _pool_read(venue: str) -> list[dict[str, Any]]:
    """读标的池（按签名决定是否传 venue，兼容旧替身/老调用方）。"""
    return load_instruments(venue) if _accepts(load_instruments, "venue") else load_instruments()


def _pool_mutate(mutator, venue: str) -> list[dict[str, Any]]:
    """RMW 写标的池（同上）。"""
    if _accepts(mutate_instruments, "venue"):
        return mutate_instruments(mutator, venue=venue)
    return mutate_instruments(mutator)


def _holdings(inst_id: str, trackers: dict[str, Any], venue: str) -> dict[str, Any]:
    """持仓判定（同上，兼容只接受 (inst_id, trackers) 的旧实现）。"""
    if _accepts(_holdings_report, "venue"):
        return _holdings_report(inst_id, trackers, venue)
    return _holdings_report(inst_id, trackers)


def _binance_public_instruments(inst_id: str) -> list[dict[str, Any]]:
    """币安在线 USDT 永续校验（公开接口，不需要凭证）。

    与 OKX 分支同语义：失败抛 502，查无返回空列表由调用方回 400。
    """
    try:
        from astra_backend.exchanges.binance import BinancePublicAdapter
        adapter = BinancePublicAdapter()
        spec = adapter.fetch_instrument_spec(inst_id)
        if spec is None:
            return []
        return [{
            "instId": inst_id,
            "baseCcy": getattr(spec, "base_ccy", None) or inst_id.split("-", 1)[0],
            "settleCcy": "USDT",
            "state": "live",
            "ctVal": "1",
            "lotSz": str(getattr(spec, "step_size", "") or "1"),
            "minSz": str(getattr(spec, "min_size", "") or "1"),
            "tickSz": str(getattr(spec, "tick_size", "") or "0.0001"),
        }]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"币安合约校验失败：{exc}") from exc

# 审计 P1-5(2026-09-13)：持仓快照由 dashboard 后台线程每 2s 刷新；超过此年龄视为"未知"，
# 未知 ≠ 无持仓（缺失≠0 红线）→ 删除标的这种破坏性操作在未知态必须 fail-closed。
HELD_SNAPSHOT_MAX_AGE_S = 90.0


def _tracker_keys_for(inst_id: str, trackers: dict[str, Any]) -> list[str]:
    """真实 tracker key 形如 `BTC-USDT-SWAP_long`（ai_factor_trader 写入）——精确等值永远命中不了。"""
    coin = inst_id.split("-", 1)[0].upper()
    hits = []
    for key in trackers or {}:
        k = str(key).strip().upper()
        if k == inst_id or k.startswith(f"{inst_id}_") or k == coin or k.startswith(f"{coin}_"):
            hits.append(str(key))
    return sorted(hits)


def _live_holdings(inst_id: str) -> tuple[bool, list[str], str]:
    """多所合一实时持仓判定（只读 dashboard 缓存，零网络）。

    返回 (是否持仓, 持仓场所列表, 未知原因)。未知原因非空 = 无法确认持仓，调用方须 fail-closed。
    """
    coin = inst_id.split("-", 1)[0].upper()
    try:
        import astra_backend.dashboard_cache as dashboard_app
        cache = getattr(dashboard_app, "CACHE_DATA", None)
    except Exception as exc:  # 模块不可用/导入失败
        return False, [], f"持仓快照模块不可用：{exc}"
    if not isinstance(cache, dict):
        return False, [], "持仓快照不可用"
    positions = cache.get("positions")
    if not isinstance(positions, list):
        return False, [], "持仓快照缺失"
    health = cache.get("data_health") if isinstance(cache.get("data_health"), dict) else {}
    age = health.get("cache_age_seconds")
    if isinstance(age, (int, float)) and float(age) > HELD_SNAPSHOT_MAX_AGE_S:
        return False, [], f"持仓快照已过期 {float(age):.0f}s"
    venues = set()
    for item in positions:
        if not isinstance(item, dict):
            continue
        held = str(item.get("instId") or "").strip().upper()
        if not held:
            continue
        if held == inst_id or held.startswith(f"{coin}-") or held == coin:
            venues.add(str(item.get("venue") or "okx").strip().lower())
    return bool(venues), sorted(venues), ""


def _holdings_report(inst_id: str, trackers: dict[str, Any], venue: str | None = None) -> dict[str, Any]:
    """`venue` 非空时只统计该场所的实时持仓——双所标的池互相独立，
    币安池删币时不该被 OKX 的持仓（或反之）误挡。缺省 None 保持原有跨所合并语义。"""
    keys = _tracker_keys_for(inst_id, trackers)
    held_live, venues, unknown = _live_holdings(inst_id)
    if venue:
        wanted = str(venue).strip().lower()
        venues = [v for v in venues if v == wanted]
        held_live = bool(venues)
    return {
        "tracker_keys": keys,
        "has_tracker": bool(keys),
        "held_live": held_live,
        "held_venues": venues,
        "holdings_unknown": unknown or None,
        "held": held_live or bool(keys),
    }


@router.get("/api/v1/admin/risk")
def admin_risk_get(x_astra_session: str | None = Header(default=None, alias="X-Astra-Session")) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_astra_session=x_astra_session)
    return {
        "schema": risk_config.schema(),
        "suites": risk_config.SUITES,
        "values": risk_config.current_values(),
        # 审计未完成清单#3：把"引擎此刻真正在用什么"和"下一周期会用什么"并排给出——
        # P0-2/P1-1 能长期隐身，正是因为页面上只有前者（文件值）没有后者（进程快照）。
        "process_values": risk_config.process_values(),
        "process_freshness": risk_config.process_freshness(),
        "file_vs_process": risk_config.file_vs_process_diff(),
        "effect": "交易引擎每 15 分钟一个巡检周期；子进程启动时重新读取 .env，保存后下一周期自动生效，无需重启后台。",
    }


@router.post("/api/v1/admin/risk")
def admin_risk_update(payload: RiskConfigUpdate, x_astra_session: str | None = Header(default=None, alias="X-Astra-Session")) -> dict[str, Any]:
    refresh_settings()
    actor = require_superadmin(x_astra_session)
    merged: dict[str, Any] = {}
    if payload.suite_id:
        try:
            merged.update(risk_config.suite_values(payload.suite_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    merged.update(payload.values)
    if not merged:
        raise HTTPException(status_code=400, detail="没有需要保存的修改")
    # 审计 P2-9：极端值（单标的占比≥50% / 日亏≥25% 权益 / 杠杆≥10x 等）必须逐字确认，
    # 否则一次误触即把硬风控放松到接近失效。阈值不是硬上限——显式确认后仍可越过。
    high_risk = risk_config.high_risk_changes(merged)
    if high_risk:
        if str(payload.confirmation or "").strip().upper() != risk_config.HIGH_RISK_PHRASE:
            audit_record("risk.config.update", "rejected_high_risk", {
                "actor": actor["username"], "items": high_risk,
            })
            detail = "；".join(f"{i['label']} = {i['value'] * 100 if 'RATIO' in i['key'] else i['value']:g}"
                              f"（阈值 {i['threshold'] * 100 if 'RATIO' in i['key'] else i['threshold']:g}）" for i in high_risk)
            raise HTTPException(
                status_code=400,
                detail=f"以下参数已进入极端区间，必须逐字确认 {risk_config.HIGH_RISK_PHRASE}：{detail}")
    before = risk_config.current_values()
    try:
        env_updates = risk_config.normalize(merged)
    except ValueError as exc:
        audit_record("risk.config.update", "failed", {"actor": actor["username"], "suite": payload.suite_id, "reason": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    update_env(env_updates)
    refresh_settings()
    risk_config.reload_risk_constants()
    try:
        from scripts.instrument_pool import sync_pool_leverage_caps
        cur_v = risk_config.current_values()
        _sync_min = float(env_updates.get("ASTRA_MIN_LEVERAGE", cur_v.get("ASTRA_MIN_LEVERAGE", 2.0)))
        _sync_max = float(env_updates.get("ASTRA_MAX_LEVERAGE", cur_v.get("ASTRA_MAX_LEVERAGE", 5.0)))
        sync_pool_leverage_caps(min_leverage=_sync_min, max_leverage=_sync_max)
    except Exception:
        pass
    audit_record("risk.config.update", "success", {
        "actor": actor["username"],
        "suite": payload.suite_id or None,
        "changed": {k: {"before": before.get(k), "after": float(v)} for k, v in env_updates.items()},
        "high_risk_confirmed": [i["key"] for i in high_risk],
    })
    return {
        "updated": sorted(env_updates.keys()),
        "applied_suite": payload.suite_id or None,
        "values": risk_config.current_values(),
        "effect": "已写入 .env；下一交易巡检周期（≤15 分钟）起对新开仓/加仓/时间止损全面生效，AI 主脑提示词中的风控口径同步对齐。",
    }


@router.post("/api/v1/admin/risk/reset")
def admin_risk_reset(payload: RiskResetRequest, x_astra_session: str | None = Header(default=None, alias="X-Astra-Session")) -> dict[str, Any]:
    refresh_settings()
    actor = require_superadmin(x_astra_session)
    if payload.confirmation.strip().upper() != "RESET RISK":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：RESET RISK")
    remove_env(set(risk_config.reset_keys()))
    refresh_settings()
    risk_config.reload_risk_constants()
    try:
        from scripts.instrument_pool import sync_pool_leverage_caps
        cur_v = risk_config.current_values()
        sync_pool_leverage_caps(min_leverage=float(cur_v.get("ASTRA_MIN_LEVERAGE", 2.0)),
                                max_leverage=float(cur_v.get("ASTRA_MAX_LEVERAGE", 5.0)))
    except Exception:
        pass
    audit_record("risk.config.reset", "success", {"actor": actor["username"]})
    return {
        "reset": True,
        "values": risk_config.current_values(),
        "effect": "全部自定义风控覆盖值已清除，执行层回退到代码默认基线。",
    }


@router.put("/api/v1/admin/account-baseline")
def admin_update_account_baseline(
    payload: InitialCapitalUpdate,
    venue: str = Query(default="", description="本金所属交易所：okx | binance；留空=当前生效所"),
    x_astra_session: str | None = Header(default=None, alias="X-Astra-Session"),
) -> dict[str, Any]:
    actor = require_superadmin(x_astra_session)
    if payload.confirmation.strip().upper() != "UPDATE CAPITAL":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：UPDATE CAPITAL")
    target_venue = normalize_venue(venue or _active_pool_venue())
    try:
        fn_update = app_attr("update_initial_capital", update_initial_capital)
        # 兼容双签名：基线模块按所写；测试/老替身只接受一个参数时退回单参
        try:
            result = fn_update(payload.initial_capital, venue=target_venue)
        except TypeError:
            result = fn_update(payload.initial_capital)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rec_audit = app_attr("audit_record", audit_record)
    rec_audit("account.baseline.update", "success", {
        "actor": actor["username"],
        "venue": target_venue,
        "previous_initial_capital": result["previous_initial_capital"],
        "initial_capital": result["initial_capital"],
        "reset_time_preserved": result["reset_time"],
    })
    return {
        "updated": True,
        "venue": target_venue,
        **result,
        "effect": "主页累计盈亏、累计 ROI 与权益基准线将按新本金重算；历史起算时间保持不变。",
    }


@router.put("/api/v1/admin/account-baseline/reset-time")
def admin_update_reset_time(payload: ResetTimeUpdate, x_astra_session: str | None = Header(default=None, alias="X-Astra-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_astra_session)
    if payload.confirmation.strip().upper() != "UPDATE RESET TIME":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：UPDATE RESET TIME")
    try:
        fn_update = app_attr("update_reset_time", update_reset_time)
        result = fn_update(payload.reset_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rec_audit = app_attr("audit_record", audit_record)
    rec_audit("account.reset_time.update", "success", {
        "actor": actor["username"],
        "previous_reset_time": result["previous_reset_time"],
        "reset_time": result["reset_time"],
    })
    return {
        "updated": True,
        **result,
        "effect": "台账起算基线已更新，下次同步将自动清理更早的历史成交。",
    }


@router.get("/api/v1/admin/pool-venues")
def admin_pool_venues(x_astra_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    """页头切换器的数据源：两所池的规模与生效状态一览。

    只读、零网络：不触达交易所，切换器打开即可用。
    """
    refresh_settings()
    require_admin_header(x_astra_admin_token)
    active = _active_pool_venue()
    venues = []
    for name in POOL_VENUES:
        try:
            pool = load_instruments(name)
            count = len(pool)
        except Exception:
            count = 0
        venues.append({
            "venue": name,
            "label": "币安 USDⓈ-M" if name == "binance" else "OKX V5",
            "pool_count": count,
            "is_active": name == active,
        })
    return {"active_venue": active, "venues": venues}


@router.put("/api/v1/admin/venue-selection")
def admin_set_venue_selection(
    payload: VenueSelectionUpdate,
    x_astra_session: str | None = Header(default=None, alias="X-Astra-Session"),
) -> dict[str, Any]:
    """一键切换执行交易所（页头切换器）。

    切换是一次**原子语义变更**，故在落盘前做三道前置校验，任一项不满足即整体拒绝
    （不做"切了一半"的中间态）：

    1. 目标所必须已登记下单实现（`VENUE_SUBMITTERS`），否则切过去也下不了单；
    2. 目标所标的池必须可信（`pool_state() == ok`）——池文件缺失/损坏时交易侧本来
       就 fail-closed 不开新仓，此时切换只会让面板显示一套根本不用于交易的标的；
    3. 目标所本金基线必须已设置（> 0），因为本金按所独立，未设本金的风控预算无意义。

    三道都不涉及网络，故切换是本地即时生效的。
    """
    actor = require_superadmin(x_astra_session)
    target = str(payload.venue or "").strip().lower()
    if target not in POOL_VENUES:
        raise HTTPException(status_code=400, detail=f"不支持的交易所：{payload.venue!r}（允许 {list(POOL_VENUES)}）")
    try:
        from scripts.ai_factor_trader import VENUE_SUBMITTERS as _submitters
    except Exception:
        _submitters = {}
    if _submitters and target not in _submitters:
        raise HTTPException(status_code=409, detail=f"{target} 尚未登记下单实现，不能切换为生效交易所")
    previous = _active_pool_venue()

    # 校验 2：目标池可信
    load_instruments(target)          # 触发一次读取，刷新 pool_state()
    state = pool_state()
    if state.get("status") != "ok":
        raise HTTPException(
            status_code=409,
            detail=f"{target} 标的池不可用（{state.get('status')}：{state.get('detail') or '未初始化'}），"
                   f"请先在「标的池」页签保存一次该所的标的池再切换",
        )

    # 校验 3：目标所本金基线
    baseline = load_account_baseline(venue=target)
    capital = float(baseline.get("initial_capital") or 0)
    if capital <= 0:
        raise HTTPException(status_code=409, detail=f"{target} 尚未设置初始本金基线，请先在「标的池」页签填写并保存")

    if not routing_policy.save_preferred_venue(target):
        raise HTTPException(status_code=500, detail=f"写入首选交易所失败（{target}）")

    # 切换后立刻把生效池扇出到下游（trading_state / 因子库 / 舆情 / 面板缓存），
    # 这一步才是"切换后自动使用切换后的标的池"的落地动作。
    sync_instruments_state(target)
    try:
        from astra_backend.exchanges import clear_instances
        clear_instances()
    except Exception:
        pass

    audit_record("venue.selection.switch", "success", {
        "actor": actor["username"],
        "previous_venue": previous,
        "venue": target,
        "pool_count": len(load_instruments(target)),
        "initial_capital": capital,
    })
    return {
        "ok": True,
        "venue": target,
        "active_venue": target,
        "pool_count": len(load_instruments(target)),
        "initial_capital": capital,
        "message": f"已切换为 {'币安 USDⓈ-M' if target == 'binance' else 'OKX V5'}：账户、标的池与持仓将全部使用该所数据",
    }


@router.get("/api/v1/admin/instruments")
def admin_instruments(
    x_astra_admin_token: str | None = Header(default=None),
    *,
    venue: str = Query(default="", description="标的池所属交易所：okx | binance；留空=当前生效所"),
) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_astra_admin_token)
    active_venue = _active_pool_venue()
    target_venue = normalize_venue(venue or active_venue)
    trackers = read_json("position_trackers.json", {})
    if not isinstance(trackers, dict):
        trackers = {}
    rows = []
    for item in _pool_read(target_venue):
        report = _holdings(item["instId"], trackers, target_venue)
        rows.append({
            **item,
            "protected": item["instId"] == "BTC-USDT-SWAP",
            # 审计 P1-5：旧实现在这里拿 inst_id 直接 in trackers（真实键带 _long/_short 后缀）
            # → 恒 False，UI「存在持仓追踪记录，禁止删除」徽标与禁用态永不出现。
            "has_tracker": report["has_tracker"],
            "tracker_keys": report["tracker_keys"],
            "held_live": report["held_live"],
            "held_venues": report["held_venues"],
            "holdings_unknown": report["holdings_unknown"],
            "removable": item["instId"] != "BTC-USDT-SWAP" and not report["held"] and not report["holdings_unknown"],
        })
    stale = [row for row in rows if row["holdings_unknown"] and not row["protected"]]
    return {
        "venue": target_venue,
        "active_venue": active_venue,
        "is_active": target_venue == active_venue,
        "pool_status": pool_state(),
        "instruments": rows,
        "limits": {"minimum": MIN_POOL_SIZE, "maximum": MAX_POOL_SIZE, "btc_required": True},
        "holdings_snapshot": {
            "source": "dashboard 多所合一持仓快照（2s 刷新）",
            "max_age_seconds": HELD_SNAPSHOT_MAX_AGE_S,
            # 只统计"本可删除但因持仓未知而暂停"的标的（BTC 属保底标的，恒不可删，不计入）
            "unknown_count": len(stale),
        },
    }


@router.post("/api/v1/admin/instruments")
def add_admin_instrument(
    payload: InstrumentAddRequest,
    venue: str = Body(default="", description="目标标的池所属交易所：okx | binance；留空=当前生效所"),
    x_astra_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_astra_admin_token)
    target_venue = normalize_venue(venue or _active_pool_venue())
    inst_id = payload.inst_id.upper()
    if target_venue == "binance":
        matches = _binance_public_instruments(inst_id)
        venue_label = "币安"
    else:
        try:
            matches = okx.instruments("SWAP", inst_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"OKX 合约校验失败：{exc}") from exc
        venue_label = "OKX"
    raw = matches[0] if matches else {}
    if raw.get("instId") != inst_id or raw.get("settleCcy") != "USDT" or raw.get("state") != "live":
        raise HTTPException(status_code=400, detail=f"仅允许添加{venue_label}在线可交易的 USDT 永续合约")
    item = from_instrument(target_venue, raw)

    # 审计 P2-6：load→校验→改→save 整段持锁（旧实现无锁 → 两个并发保存丢标的）；
    # 去重与容量不变量放进锁内，避免"检查通过后另一个请求插进来"。
    def _append(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if any(row["instId"] == inst_id for row in pool):
            raise HTTPException(status_code=409, detail="该币种已在交易池中")
        if len(pool) >= MAX_POOL_SIZE:
            raise HTTPException(status_code=409, detail=f"交易池最多允许 {MAX_POOL_SIZE} 个币种；请先删除一个无持仓币种，或调整环境变量 ASTRA_MAX_POOL_SIZE")
        return [*pool, item]

    updated = _pool_mutate(_append, target_venue)
    audit_record("instrument.add", "success", {"instId": inst_id, "venue": target_venue})
    return {"added": item, "venue": target_venue, "count": len(updated), "effective": "immediate",
            "message": f"{item['name']} 已成功加入{venue_label}交易池并实时同步全网大屏与因果雷达"}


@router.delete("/api/v1/admin/instruments/{inst_id}")
def delete_admin_instrument(
    inst_id: str,
    payload: InstrumentDeleteRequest | None = Body(default=None),
    confirmation: str | None = None,
    x_astra_admin_token: str | None = Header(default=None),
    x_astra_session: str | None = Header(default=None, alias="X-Astra-Session"),
    *,
    venue: str = Query(default="", description="标的池所属交易所：okx | binance；留空=当前生效所"),
) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_astra_admin_token, x_astra_session)
    target_venue = normalize_venue(venue or _active_pool_venue())
    inst_id = inst_id.upper()
    conf = ((payload.confirmation if payload else None) or confirmation or "").strip().upper()
    # 审计 P1-5：旧实现 `if conf and conf != ...` → 不传短语就免检，确认形同虚设。
    if conf != f"REMOVE {inst_id}":
        audit_record("instrument.remove", "rejected_phrase", {"instId": inst_id, "venue": target_venue, "provided": bool(conf)})
        raise HTTPException(status_code=400, detail=f"必须提供确认短语（精确为：REMOVE {inst_id}）")
    if inst_id == "BTC-USDT-SWAP":
        raise HTTPException(status_code=403, detail="BTC 是全局黑天鹅哨兵基准，不允许从交易池删除")
    current = _pool_read(target_venue)
    if len(current) <= MIN_POOL_SIZE:
        raise HTTPException(status_code=409, detail=f"交易池至少保留 {MIN_POOL_SIZE} 个币种")
    if not any(item["instId"] == inst_id for item in current):
        raise HTTPException(status_code=404, detail="该币种不在交易池中")
    trackers = read_json("position_trackers.json", {})
    if not isinstance(trackers, dict):
        trackers = {}
    # 铁律：该标的在**本所**仍被风控接管（有追踪记录 / 实时持仓 / 持仓未知）→ 一律禁删。
    report = _holdings(inst_id, trackers, target_venue)
    if report["tracker_keys"]:
        audit_record("instrument.remove", "rejected_tracker", {"instId": inst_id, "venue": target_venue, "tracker_keys": report["tracker_keys"]})
        raise HTTPException(status_code=409, detail=f"该币种存在持仓追踪记录（{', '.join(report['tracker_keys'])}），为防止失去风控接管，禁止删除")
    if report["held_live"]:
        audit_record("instrument.remove", "rejected_live_holdings", {"instId": inst_id, "venue": target_venue, "venues": report["held_venues"]})
        raise HTTPException(status_code=409, detail=f"该币种在 {', '.join(report['held_venues'])} 仍有实时持仓，删除后将失去移动止损/时间止损/AI 平仓接管，禁止删除")
    if report["holdings_unknown"]:
        # 未知 ≠ 无持仓：拒绝而不是放行（破坏性操作 fail-closed）
        audit_record("instrument.remove", "rejected_unknown_holdings", {"instId": inst_id, "venue": target_venue, "reason": report["holdings_unknown"]})
        raise HTTPException(status_code=503, detail=f"无法确认实时持仓（{report['holdings_unknown']}），为防止删掉有持仓的标的而失去风控接管，已拒绝删除；请稍后重试")
    # 审计 P2-6：删除也在锁内重读整池后再落盘——否则并发保存（例如另一页在改
    # 另一标的的参数）会被这份旧快照整份覆盖。
    updated = _pool_mutate(lambda pool: [item for item in pool if item["instId"] != inst_id], target_venue)
    audit_record("instrument.remove", "success", {"instId": inst_id, "venue": target_venue, "holdings_cleared": {"tracker_keys": [], "held_live": False}})
    return {"removed": inst_id, "venue": target_venue, "count": len(updated), "effective": "immediate",
            "message": f"{inst_id} 已从交易池移除并实时同步全网大屏与因果雷达"}


@router.post("/api/v1/admin/positions/close")
def manual_close_position(payload: ManualCloseRequest) -> dict[str, Any]:
    actor = require_superadmin(REQUEST_SESSION.get())
    refresh_settings()
    from scripts.okx_runtime import current_environment
    _close_env = current_environment()
    _close_venue = str(getattr(payload, "venue", "") or "okx").strip().lower()
    # 本仓已收口为 OKX 专用：外所平仓分支（`close_intent.venue_fast_close`）
    # 随外所下架一并移除，非 OKX 场所一律 fail-closed 拒绝。
    if _close_venue != "okx":
        raise HTTPException(status_code=400, detail=f"不支持的平仓场所：{_close_venue}")
    if not _close_env.configured:
        raise HTTPException(status_code=503, detail=f"OKX {_close_env.mode.upper()} 静态 API Key 未配置（系统 NOT READY）：V5 直签是唯一私有通道，禁止后台手动平仓；请先在「账户接入」补齐完整三件套")
    if not settings.manual_close_enabled:
        raise HTTPException(status_code=403, detail="后台手动平仓功能未启用")
    import fcntl
    lock_path = DATA_DIR / ".ai_factor_trader.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    from astra_backend.dependencies import get_auth_store
    auth_store = get_auth_store()
    if actor.get("role") == "legacy" or not auth_store.verify_password(int(actor["id"]), payload.admin_password):
        raise HTTPException(status_code=403, detail="管理员密码验证失败")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(status_code=409, detail="交易主循环正在执行，暂不允许后台快速平仓；请等待本周期结束")
        try:
            result = fast_close_confirmed(payload.close_token, payload.confirmation)
            audit_record("position.close", "confirmed_closed", {"instId": result.get("instId"), "side": result.get("posSide"), "venue": _close_venue, "environment": result.get("environment"), "size": result.get("closed_size"), "actor": actor.get("username", "admin")})
            return result
        except OKXNotConfigured as exc:
            audit_record("position.close", "rejected_not_ready", {"venue": _close_venue, "error": str(exc)[:300], "actor": actor.get("username", "admin")})
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            audit_record("position.close", "rejected", {"venue": _close_venue, "error": str(exc)[:300], "actor": actor.get("username", "admin")})
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            audit_record("position.close", "verification_failed", {"venue": _close_venue, "error": str(exc)[:300], "actor": actor.get("username", "admin")})
            raise HTTPException(status_code=502, detail=f"{_close_venue.upper()} 快速平仓未完成确认：{exc}") from exc
        finally:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
