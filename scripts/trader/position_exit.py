"""持仓退出管理：止盈 / 追踪止损棘轮 / 时间止损（B3 抽取·trader 瘦身第十刀，第八十九刀）。

从 `scripts/ai_factor_trader.py` **纯搬家** `manage_position_tp_and_trailing`（273 行）。

## 职责

持仓的生命周期退出判定（每个交易周期对每笔持仓调用一次）：
硬止盈/止损、三档追踪止损棘轮（保本/锁盈/加速）、时间止损（ATR 带 + 小时数）、
云端保护单同步（`ensure_cloud_position_protection` / `sync_cloud_algo_stop`）、
平仓确认（`close_position_confirmed`）与台账记录（`record_trade`）。

⚠️ 与 `scripts/trader/position_mgmt.py`（**主脑持仓指令执行器**，
`execute_ai_position_management`）是**两个不同关注点**：
- `position_mgmt.py`：消费 LLM 下发的持仓指令（收紧止损/主动平仓）；
- 本模块：**无 LLM 参与**的机械退出规则（TP/棘轮/时间止损）。
命名刻意避开 `position_*` 混淆：本模块叫 `position_exit`。

## 同名注入（本仓最宽之一）

⇒ 函数体 AST **零例外全等**。`time` 由本模块自 import。
`_close_fee` / `_close_trade_payload` / `notify_trade_close` /
`protection_signals` / `ratcheted_trailing_stop` 是门面从其它子包 import 进来的
名字，同样按门面全局**同名注入**（保持 patch 面与调用期解析语义不变）。
多所路径的原生云端棘轮注入已随场所下线移除。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple


try:                                    # 与 scale_out.py 同源；独立运行时兜底
    from scripts.risk_constants import SCALE_OUT_RATIO
except Exception:                       # pragma: no cover
    SCALE_OUT_RATIO = 0.50


def _freeze_tp1_from_entry(entry_px: float, atr: float, is_long: bool, prec: int) -> Optional[float]:
    """建仓瞬间冻结首批止盈价（双腿挂单与软件判定共用同一价格）。"""
    try:
        from scripts.trader.tp1 import compute_tp1
        return compute_tp1(entry_px, atr, is_long, prec)
    except Exception:
        return None


def _is_simulated() -> bool:
    """OKX 是否模拟盘 —— 腿模式 `live_only` 需要它。

    fail-safe 方向：判不出来就当**模拟盘**（于是 live_only 下不挂腿）。
    宁可"本轮不新增挂腿"，也不在没确认环境时往实盘发新形态的委托。
    """
    try:
        import scripts.okx_rest as _okx_rest
        return bool(getattr(_okx_rest.current_environment(), "simulated", False))
    except Exception:
        return True


def _binance_exchange(binance_rest):
    """取币安适配器背后能读写保护单的 `BinanceExchange`（桥接类把它挂在 `.exchange`）。"""
    return getattr(binance_rest, "exchange", binance_rest)


def _binance_protection_state(binance_rest, inst_id, pos_side, *,
                              tp_px, sl_px, pos_sz):
    """币安侧保护单体检 + 缺失时补挂。返回 `(ok, 说明)`；**任何失败都不平仓**。

    与 OKX 分支的语义差异（刻意）：OKX 的"云端 OCO 覆盖不足"会直接安全平仓，
    因为它能**确定**自己掌握全量保护；币安侧这里只读条件单列表，读不到/挂不上
    的可能原因太多（无凭证、限流、挂单在途），把"读不到保护"当成"该平仓"会
    误平**手动开的仓**（2026-09-28 同类事故的教训）。故失败一律只告警，
    由本地机械止损（硬止损/棘轮/时间止损）继续接管。
    """
    if binance_rest is None:
        return False, "币安适配器不可用，无法核验交易所侧保护（本地机械止损接管）"
    ex = _binance_exchange(binance_rest)
    reader = getattr(ex, "protection_orders", None)
    rows = []
    if callable(reader):
        try:
            rows = reader(inst_id) or []
        except Exception as exc:
            return False, f"币安保护单读取失败（本地机械止损接管）: {exc}"
    live = [r for r in rows
            if str(r.get("posSide", "net")).lower() in {pos_side, "net"}
            and (r.get("slTriggerPx") or "")]
    if live:
        return True, f"币安条件保护 {len(live)} 条（SL={live[0].get('slTriggerPx')}）"
    placer = getattr(ex, "place_protection", None)
    if not callable(placer):
        return False, "币安适配器无保护单接口（本地机械止损接管）"
    try:
        placer(inst_id, pos_side, pos_sz, tp_px, sl_px)
    except Exception as exc:
        return False, f"币安保护单缺失且补挂失败（本地机械止损接管，下周期重试）: {exc}"
    return True, f"币安保护单缺失，已补挂 TP={tp_px:g}/SL={sl_px:g}"


def _sync_exchange_stop(*, pos_venue, binance_rest, sync_cloud_algo_stop,
                        inst_id, pos_side, sl_px, reason):
    """把收紧后的止损同步到**交易所侧**条件单（OKX 走云端 OCO，币安走 amend_stop）。"""
    if pos_venue == "okx":
        return sync_cloud_algo_stop(inst_id, pos_side, sl_px, reason=reason)
    if binance_rest is None:
        return False
    ex = _binance_exchange(binance_rest)
    amend = getattr(ex, "amend_stop", None)
    reader = getattr(ex, "protection_orders", None)
    if not callable(amend) or not callable(reader):
        return False
    try:
        live = [r for r in (reader(inst_id) or [])
                if str(r.get("posSide", "net")).lower() in {pos_side, "net"}
                and (r.get("algoId") or "")]
    except Exception:
        return False
    if not live:
        return False
    ok = True
    for row in live:
        try:
            amend(inst_id, str(row.get("algoId")), sl_px)
        except Exception:
            ok = False
    return ok


def _purge_after_close(inst_id, purge):
    """机械平仓成功后，剔除主脑指令文件里针对该标的的陈旧指令。

    所有机械平仓分支（硬止损/保护失效/时间止损/阶梯锁利/移动止盈）都会让持仓从
    OKX 真实持仓里消失。若主脑指令文件（`ai_position_management.json`）里还残留着
    针对该标的的 UPDATE_SL/CLOSE_MARKET 指令，下一轮 `execute_ai_position_management`
    会再消费一条**注定无法执行**的指令，打出"不在本路径持仓字典"的误报。

    本函数把清理动作收敛到一处，**fail-open**：`purge` 为 None（测试或未接线）或
    清理抛异常时一律静默跳过，绝不影响平仓主流程。
    """
    if purge is None:
        return
    try:
        purge(inst_id)
    except Exception:
        pass


def manage_position_tp_and_trailing(f, curr_pos, trackers, timestamp_full, executed_actions,
    *,
    _float_or_zero,
    add_stop_cooldown,
    build_signal_snapshot,
    close_position_confirmed,
    ensure_cloud_position_protection,
    evaluate_asset_signal,
    record_signal_snapshot,
    record_trade,
    sync_cloud_algo_stop,
    venue_registry,
    binance_rest=None,
    ASSET_CLASS_PROFILES,
    TAKER_FEE_RATE,
    TIME_STOP_ATR_BAND,
    TIME_STOP_HOURS,
    _close_fee,
    _close_trade_payload,
    notify_trade_close,
    protection_signals,
    ratcheted_trailing_stop,
    resolve_decision_attribution,
    purge_ai_position_instructions=None):
    # ⚠️ 2026-09-30（通知单一事实源）：本模块**不再发布**任何 `trade.closed`。
    #     此前硬止损/保护失效/时间止损/阶梯锁利四处各发一张卡片，而台账同步路径
    #     （`scripts/sync_full_ledger.py` → `scripts/ledger/notify.py`）会对同一笔再发
    #     一张 —— 实测同一天同一笔出现两条互相矛盾的金额（XRP −30.73 对 −32.63、
    #     DOGE −35.73 对 −37.71、SUI +28.32 对 +18.11），用户据此认定"数据不对"。
    #     金额类通知一律由台账路径发布（唯一握有交易所真实成交价/手续费/ROI/时长）；
    #     本路径的即时信息由 `executed_actions` 动作行与追踪器承载。
    #     参数保留以维持注入面（门面同名注入 + 既有 patch 面），故显式消费一次。
    _ = notify_trade_close
    if not f.get("market_data_valid"):
        executed_actions.append(f"[{f['name']}] 行情数据不完整，保留云端保护并跳过本地移动止盈")
        return False, "行情无效"
    inst_id = f["instId"]
    name = f["name"]
    cur_px = f["price"]
    asset_type = f.get("type", "crypto")
    profile = ASSET_CLASS_PROFILES.get(asset_type, ASSET_CLASS_PROFILES["crypto"])
    atr = max(f["atr"], cur_px * 0.005)
    prec = f["precision"]
    ct_val = float(curr_pos.get("ctVal") or f["ctVal"])
    
    pos_sz = float(curr_pos["pos"])
    is_long = "long" in curr_pos["side"].lower()
    entry_px = float(curr_pos["avgPx"])
    pos_key = f"{inst_id}_{curr_pos['side']}"
    pos_venue = str(curr_pos.get("venue") or curr_pos.get("exchange") or "okx").lower()
    # ⚠️ 场所闸（2026-10-30 修订）：OKX 与**币安**都已接入平仓确认
    # （`close_position_confirmed` 的 binance 分支先撤保护单再 reduce-only 平仓），
    # 故两所都跑机械退出 —— 币安档下不再"只读跳过"（那会让持仓失去止损保护）。
    # 未登记场所仍只读跳过：它的 instId 与 OKX 同名，**绝不能**交给 OKX 直签接口
    # （`close_position_confirmed` 不传 venue 就默认 okx，会把同名标的在 OKX 的仓
    # 平掉，2026-09-28 实盘事故）。跳过时仍留痕、不下发任何交易所指令、不抛异常。
    if pos_venue not in ("okx", "binance"):
        executed_actions.append(
            f"[{f['name']}] 场所未接入执行面({pos_venue})，只读跳过机械退出（不下发任何交易所指令）")
        return False, f"场所未接入执行面({pos_venue})，只读跳过"

    now_ts = int(time.time())
    if pos_key not in trackers:
        score, action, reasons, strat_tag, strat_desc = evaluate_asset_signal(f)
        # 决策来源在建档时刻钉死（2026-10）：本块是**建仓后首次巡检**，per-symbol
        # 决策缓存里仍留着建仓周期的"单模型 / 投委会 + 采纳席位"。到平仓时缓存已被
        # 后续周期覆盖，那时再读就是错的数据 —— 故此处一次性写进追踪器，供
        # `close_evidence` 在平仓归档时取用（单模型时 adopted_role 为 None）。
        _decision_source, _adopted_role = resolve_decision_attribution(inst_id)
        trackers[pos_key] = {
            "instId": inst_id,
            "name": name,
            "side": curr_pos["side"],
            "policy_version": f.get("policy_version", ""),
            "policy_hash": f.get("policy_hash", ""),
            "decision_source": _decision_source,
            "adopted_role": _adopted_role,
            "strategy_tag": strat_tag if strat_tag != "⚪ 观望" else ("🌊 顺势回踩" if is_long else "⚡ 阻力抛压"),
            "market_regime": f.get("market_regime") or "UNKNOWN",
            "entryPx": entry_px,
            "entryTs": now_ts,
            "entryTime": timestamp_full,
            "initialSz": pos_sz,
            "currentSz": pos_sz,
            # 分批止盈的双基准（2026-09-29）：
            #   entry_sz  —— 建仓张数**冻结**，用于识别"交易所侧 TP1 腿已成交"
            #                （旧实现只有会被每轮刷新的 currentSz，无法分辨腿成交）；
            #   scale_out_tp —— 首批止盈价**冻结**，挂腿与软件判定共用同一价格，
            #                不再每轮按当轮 ATR 重算（详见 scripts/trader/tp1.py）。
            "entry_sz": pos_sz,
            "scale_out_tp": _freeze_tp1_from_entry(entry_px, atr, is_long, prec),
            "highWaterMark": cur_px,
            "lowWaterMark": cur_px,
            "trailingStopPx": round((entry_px - atr * profile["sl_atr_mult"]) if is_long else (entry_px + atr * profile["sl_atr_mult"]), prec),
            # 初始止损距离**冻结**（2026-10）：`trailingStopPx` 会被三档棘轮逐级上移，
            # 平仓时已经不是"1R"的基准了。自进化的 MFE/MAE 回吐分析需要**建仓那一刻**
            # 的止损距离当 1R 分母，故在此独立冻结一份 —— 与 `scale_out_tp` 同一手法。
            "initialStopPx": round((entry_px - atr * profile["sl_atr_mult"]) if is_long else (entry_px + atr * profile["sl_atr_mult"]), prec),
            "takeProfitPx": round((entry_px + max(atr * profile["tp_atr_mult"], entry_px * profile["min_profit_ratio"])) if is_long else (entry_px - max(atr * profile["tp_atr_mult"], entry_px * profile["min_profit_ratio"])), prec),
            "signal_snapshot": build_signal_snapshot(f),
            "stage_desc": "持有监控中"
        }
        record_signal_snapshot({
            "instId": inst_id,
            "name": name,
            "side": curr_pos["side"],
            "entryTs": now_ts,
            "entryTime": timestamp_full,
            "entryPx": entry_px,
            "sz": pos_sz,
            "policy_version": f.get("policy_version", ""),
            "snapshot": trackers[pos_key]["signal_snapshot"],
        })

    t = trackers[pos_key]
    # 价格精度**每轮刷新**（来自合约规格，不是冻结决策）：持仓中调止盈要按它取整，
    # 2026-09-29 真机实测混用张数精度把 0.2236 下发成 0.22（差 1.6%）。
    t["px_prec"] = int(f.get("precision", 2) or 2)
    if not t.get("entry_sz"):
        # 旧 tracker 没有建仓张数：本轮只补记（不触发"腿已成交"判定）。
        t["entry_sz"] = pos_sz
    if not t.get("policy_version") and f.get("policy_version"):
        t["policy_version"] = f.get("policy_version")
        t["policy_hash"] = f.get("policy_hash", "")
    t["currentSz"] = pos_sz
    if "entryTs" not in t:
        t["entryTs"] = now_ts

    t["highWaterMark"] = max(t.get("highWaterMark", cur_px), cur_px)
    t["lowWaterMark"] = min(t.get("lowWaterMark", cur_px), cur_px)
    # Peak Profit Tracking
    if is_long:
        t["highWaterMark"] = max(t.get("highWaterMark", cur_px), cur_px)
        cur_profit_px = cur_px - entry_px
        peak_profit_px = t["highWaterMark"] - entry_px
    else:
        t["lowWaterMark"] = min(t.get("lowWaterMark", cur_px), cur_px)
        cur_profit_px = entry_px - cur_px
        peak_profit_px = entry_px - t["lowWaterMark"]

    # 1. Hard Stop Loss (loss protection is independent of profit-lock activation).
    # The tracker stop is the exchange-protection source of truth; if a legacy or
    # partially migrated position has no live cloud OCO, the local 15-minute
    # fail-safe still closes it once the stop is breached.
    # 判定见 scripts/trader/protection.py（长空方向合一）。
    hard_stop_px = float(t.get("trailingStopPx", 0.0) or 0.0)
    hard_stop_hit = protection_signals(is_long=is_long, cur_px=cur_px, hard_stop_px=hard_stop_px)
    if hard_stop_hit:
        closed, close_detail = close_position_confirmed(inst_id, "long" if is_long else "short", pos_sz, venue=pos_venue)
        if not closed:
            executed_actions.append(f"[{name}] 硬止损平仓失败，仓位仍保留: {close_detail}")
            return False, "硬止损平仓失败"
        close_fee = _close_fee(pos_sz, ct_val, cur_px, TAKER_FEE_RATE)
        pnl_val = curr_pos["upl"]
        executed_actions.append(f"[{name}] 🛑 触发硬止损 {hard_stop_px} 并确认平仓 (净盈亏: {pnl_val:+.2f}U)")
        record_trade(_close_trade_payload(
            is_long=is_long, timestamp_full=timestamp_full, name=name,
            action_type="硬止损", side_suffix="硬止损",
            pos_sz=pos_sz, cur_px=cur_px, fee=close_fee, pnl=pnl_val,
            remark=f"价格 {cur_px} 触及保护止损 {hard_stop_px}，交易所确认平仓",
        ), position=curr_pos)
        add_stop_cooldown(inst_id, "long" if is_long else "short", "硬止损")
        trackers.pop(pos_key, None)
        _purge_after_close(inst_id, purge_ai_position_instructions)
        return True, "已硬止损"

    default_tp_dist = max(atr * profile["tp_atr_mult"], entry_px * profile["min_profit_ratio"])
    if not _float_or_zero(t.get("takeProfitPx")):
        t["takeProfitPx"] = round(entry_px + default_tp_dist if is_long else entry_px - default_tp_dist, prec)
    _pos_side = "long" if is_long else "short"
    # ⚠️ 云 OCO 核验（`okx_rest.pending_algo_orders` / `place_algo_oco`）是
    # **OKX 直签链专属**；币安侧是另一套（`protection_orders` / `place_protection`
    # / `amend_stop`），由下方分支按场所分派 —— 绝不把币安 instId 交给 OKX 接口。
    # 危险点：`close_position_confirmed` **不传 venue 就默认 okx**，
    # 若把外所持仓的 instId 交给它，"保护失效退出"会把同名标的在 OKX 的仓平掉
    # —— 平的是别人的仓（2026-09-28 实盘事故）。本函数所有平仓调用都带 venue。
    # 分批参数：只有**尚未分批**且 TP1 与 TP2 几何合法时才要求拆双腿
    # （腿挂在错误一侧会被 OKX 拒或立刻触发，见 scripts/trader/tp1.py 的守卫）。
    _tp2_px = float(t.get("takeProfitPx") or 0.0)
    _tp1_px = float(t.get("scale_out_tp") or 0.0)
    _split_enabled = False
    if _tp1_px > 0 and int(t.get("scale_out_phase", 0) or 0) < 1:
        try:
            from scripts.trader.tp1 import tp1_geometry_ok
            _split_enabled = tp1_geometry_ok(_tp1_px, _tp2_px, cur_px, is_long)
        except Exception:
            _split_enabled = False
    if pos_venue == "okx":
        _pos_min = curr_pos.get("minSz")
        _leg_min_sz = float(_pos_min if _pos_min else (f.get("minSz", 0.01) or 0.01))
        protected, protection_detail = ensure_cloud_position_protection(
            inst_id, _pos_side, pos_sz, _tp2_px or float(t["takeProfitPx"]), hard_stop_px,
            tp1_px=_tp1_px if _split_enabled else None,
            scale_out_ratio=SCALE_OUT_RATIO,
            leg_min_sz=_leg_min_sz,
            prec=2,
            px_prec=int(f.get("precision", 2) or 2),
            legs_state=t,
            simulated=_is_simulated(),
            split_enabled=_split_enabled,
        )
        if not protected:
            closed, close_detail = close_position_confirmed(
                inst_id, _pos_side, pos_sz, venue=pos_venue)
            if not closed:
                executed_actions.append(f"[{name}] 🚨 云端 OCO 缺失且安全退出失败: {protection_detail}; {close_detail}")
                return False, "保护与退出均失败"
            pnl_val = curr_pos["upl"]
            executed_actions.append(f"[{name}] 🧯 云端 OCO 无法确认，已安全平仓: {protection_detail}")
            record_trade(_close_trade_payload(
                is_long=is_long, timestamp_full=timestamp_full, name=name,
                action_type="保护失效退出", side_suffix="保护失效退出",
                pos_sz=pos_sz, cur_px=cur_px,
                fee=_close_fee(pos_sz, ct_val, cur_px, TAKER_FEE_RATE), pnl=pnl_val,
                remark=f"云端 OCO 无法达到全仓覆盖，交易所确认安全平仓：{protection_detail}",
            ), position=curr_pos)
            add_stop_cooldown(inst_id, _pos_side, "云端保护失效")
            trackers.pop(pos_key, None)
            _purge_after_close(inst_id, purge_ai_position_instructions)
            return True, "保护失效安全退出"
        t["cloudProtection"] = {"verifiedAt": timestamp_full, "detail": protection_detail}
    else:
        # 币安：保护腿在下单时由 `place_protected_*` 原子挂好。这里**只体检 + 补挂**，
        # 失败不平仓（可能是用户手动开的仓；本地机械止损会继续接管并按需平仓）。
        _bn_ok, _bn_detail = _binance_protection_state(
            binance_rest, inst_id, _pos_side,
            tp_px=_tp2_px or float(t["takeProfitPx"]), sl_px=hard_stop_px, pos_sz=pos_sz)
        # 同一异常**只在首次/换样时吼一声**：不可修复的保护缺失（例如合约在
        # exchangeInfo 里字段不全）会每 15 分钟原样复现，逐轮刷同一条动作行
        # 只会淹没真正的动作。状态一直留在 `cloudProtection` 里可查。
        _prev_prot = t.get("cloudProtection") if isinstance(t.get("cloudProtection"), dict) else {}
        _prev_detail = str(_prev_prot.get("detail") or "")
        _prev_ok = bool(_prev_prot.get("ok", True))
        t["cloudProtection"] = {"verifiedAt": timestamp_full, "detail": _bn_detail,
                               "ok": bool(_bn_ok), "venue": "binance"}
        if not _bn_ok and (_bn_detail != _prev_detail or _prev_ok):
            executed_actions.append(
                f"[{name}] ⚠️ 币安保护单体检异常：{_bn_detail}")

    pos_regime = str(t.get("market_regime") or f.get("market_regime") or "UNKNOWN")
    is_chop_pos = (pos_regime in ("CHOP", "CHOP_RANGE"))

    # 2. Volatility Time-Stop Exit (持仓超最长持仓时间且缩量横盘 → 时间止损，参数见后台风控管理页)
    hold_duration_sec = now_ts - t["entryTs"]
    # 震荡市紧凑时间止损：若在震荡市入场，2.5 小时无突破即主动释放资金，不空耗 4 小时
    max_hold_hours = (TIME_STOP_HOURS * 0.625) if is_chop_pos else TIME_STOP_HOURS
    if hold_duration_sec > max_hold_hours * 3600 and abs(cur_profit_px) < TIME_STOP_ATR_BAND * atr:
        closed, close_detail = close_position_confirmed(inst_id, "long" if is_long else "short", pos_sz, venue=pos_venue)
        if not closed:
            executed_actions.append(f"[{name}] 时间止损平仓失败，仓位仍保留: {close_detail}")
            return False, "平仓失败"
        close_fee = _close_fee(pos_sz, ct_val, cur_px, TAKER_FEE_RATE)
        executed_actions.append(f"[{name}] ⌛ 超过 {max_hold_hours:g} 小时无波动横盘，时间止损平仓释放保证金")
        record_trade(_close_trade_payload(
            is_long=is_long, timestamp_full=timestamp_full, name=name,
            action_type="时间止损", side_suffix="无波动出场",
            pos_sz=pos_sz, cur_px=cur_px, fee=close_fee, pnl=curr_pos["upl"],
            remark=f"持仓超 {max_hold_hours:g} 小时无突破，主动平仓释放配比",
        ), position=curr_pos)
        # 时间止损平仓后，剔除主脑指令文件里针对该标的的陈旧指令，避免下一轮
        # 消费一条注定无法执行（持仓已消失）的 UPDATE_SL/CLOSE_MARKET 告警。
        if pos_key in trackers: del trackers[pos_key]
        _purge_after_close(inst_id, purge_ai_position_instructions)
        return True, "时间止损"

    # 3. Three-Tier Ratchet Profit-Locking & Momentum Take-Profit Engine
    # 震荡市敏捷保本：震荡市振幅窄，若达到 0.9x ATR 浮盈（约 0.6R~0.7R），即提前启动保本提损，杜绝浮盈变巨亏
    # Tier 1: Breakeven Lock at +1.5x ATR (常规) / +0.9x ATR (震荡市敏捷保本)
    # Tier 2: Solid Wave Profit Lock at +2.2x ATR (~1.6R profit, lock in at least +1.0x ATR profit)
    # Tier 3: Kinetic Momentum Pullback Exit (Symmetric >= 2.0x ATR peak profit with 0.75x ATR pullback)
    
    tier1_breakeven_trigger = (0.9 * atr) if is_chop_pos else (1.5 * atr)
    tier2_lock_trigger = 2.2 * atr
    momentum_tp_trigger = 2.0 * atr
    momentum_pullback_buffer = 0.75 * atr
    
    if is_long:
        # Dynamic Ratchet Stop Calculation for Long（数学见 scripts/trader/protection.py）
        old_sl = float(t.get("trailingStopPx", 0.0) or 0.0)
        dynamic_floor_sl, stage_desc = ratcheted_trailing_stop(
            is_long=True, entry_px=entry_px, atr=atr, prec=prec,
            peak_profit_px=peak_profit_px, old_sl=old_sl,
            tier1_breakeven_trigger=tier1_breakeven_trigger,
            tier2_lock_trigger=tier2_lock_trigger,
        )
        if stage_desc:
            t["stage_desc"] = stage_desc
        
        # If dynamic floor stop ratcheted up, commit and sync to cloud OCO
        if dynamic_floor_sl > old_sl and old_sl > 0:
            t["trailingStopPx"] = dynamic_floor_sl
            cloud_ok = _sync_exchange_stop(
                pos_venue=pos_venue, binance_rest=binance_rest,
                sync_cloud_algo_stop=sync_cloud_algo_stop,
                inst_id=inst_id, pos_side="long",
                sl_px=dynamic_floor_sl, reason=t["stage_desc"])
            if not cloud_ok:
                # 交易所侧修正失败：本地棘轮已推进但交易所止损仍停在旧价，
                # 必须显式告警（而非静默忽略），否则形成"本地乐观、交易所滞后"的状态分叉，
                # 行情反向时按旧止损平仓、损失大于本地预期。
                executed_actions.append(
                    f"[{name}] ⚠️ {pos_venue.upper()} 止损修正失败（本地已推进至 {dynamic_floor_sl}，交易所仍为 {old_sl}），下周期将重试")
                print(f"[Stop Sync Failed] {inst_id} long@{pos_venue}: 本地 {dynamic_floor_sl} / 交易所未确认，下周期重试")
        else:
            t["trailingStopPx"] = dynamic_floor_sl

        # A. Hit Ratchet Floor Stop (Locked Profit Trigger)
        if cur_px <= dynamic_floor_sl and peak_profit_px >= tier1_breakeven_trigger:
            closed, close_detail = close_position_confirmed(inst_id, "long", pos_sz, venue=pos_venue)
            if not closed:
                executed_actions.append(f"[{name}] 锁利平多失败，仓位仍保留: {close_detail}")
                return False, "平仓失败"
            close_fee = _close_fee(pos_sz, ct_val, cur_px, TAKER_FEE_RATE)
            pnl_val = curr_pos["upl"]
            executed_actions.append(f"[{name}] 🛡️ 触发阶梯动态锁利平仓 (净盈亏: {pnl_val:+.2f}U)")
            record_trade(_close_trade_payload(
                is_long=is_long, timestamp_full=timestamp_full, name=name,
                action_type="阶梯锁利", side_suffix="阶梯锁利平仓",
                pos_sz=pos_sz, cur_px=cur_px, fee=close_fee, pnl=pnl_val,
                remark=f"最高 {t['highWaterMark']} 触发阶梯利润锁定线 {dynamic_floor_sl}",
            ), position=curr_pos)
            if pos_key in trackers: del trackers[pos_key]
            _purge_after_close(inst_id, purge_ai_position_instructions)
            return True, "已阶梯锁利"

        # B. Kinetic Momentum Pullback Exit from Peak (Symmetric 2.0x ATR profit with 0.75x ATR pullback)
        if peak_profit_px >= momentum_tp_trigger and cur_px <= (t["highWaterMark"] - momentum_pullback_buffer):
            closed, close_detail = close_position_confirmed(inst_id, "long", pos_sz, venue=pos_venue)
            if not closed:
                executed_actions.append(f"[{name}] 动能见顶移动止盈失败，仓位仍保留: {close_detail}")
                return False, "平仓失败"
            close_fee = _close_fee(pos_sz, ct_val, cur_px, TAKER_FEE_RATE)
            pnl_val = curr_pos["upl"]
            executed_actions.append(f"[{name}] 🎯 触发高点回撤动能止盈 (净盈亏: {pnl_val:+.2f}U)")
            record_trade(_close_trade_payload(
                is_long=is_long, timestamp_full=timestamp_full, name=name,
                action_type="移动止盈", side_suffix="高点回撤止盈",
                pos_sz=pos_sz, cur_px=cur_px, fee=close_fee, pnl=pnl_val,
                remark=f"最高 {t['highWaterMark']} 动能回撤触及移动止盈线",
            ), position=curr_pos)
            if pos_key in trackers: del trackers[pos_key]
            _purge_after_close(inst_id, purge_ai_position_instructions)
            return True, "已移动止盈"

    else:
        # Dynamic Ratchet Stop Calculation for Short（数学见 scripts/trader/protection.py）
        old_sl = float(t.get("trailingStopPx", 0.0) or 0.0)
        dynamic_floor_sl, stage_desc = ratcheted_trailing_stop(
            is_long=False, entry_px=entry_px, atr=atr, prec=prec,
            peak_profit_px=peak_profit_px, old_sl=old_sl,
            tier1_breakeven_trigger=tier1_breakeven_trigger,
            tier2_lock_trigger=tier2_lock_trigger,
        )
        if stage_desc:
            t["stage_desc"] = stage_desc
        
        # If dynamic floor stop ratcheted down (tightened for short), commit and sync to cloud OCO
        if dynamic_floor_sl < old_sl and old_sl > 0:
            t["trailingStopPx"] = dynamic_floor_sl
            cloud_ok = _sync_exchange_stop(
                pos_venue=pos_venue, binance_rest=binance_rest,
                sync_cloud_algo_stop=sync_cloud_algo_stop,
                inst_id=inst_id, pos_side="short",
                sl_px=dynamic_floor_sl, reason=t["stage_desc"])
            if not cloud_ok:
                # 交易所侧修正失败：本地棘轮已推进但交易所止损仍停在旧价，
                # 必须显式告警（而非静默忽略），否则形成"本地乐观、交易所滞后"的状态分叉。
                executed_actions.append(
                    f"[{name}] ⚠️ {pos_venue.upper()} 止损修正失败（本地已推进至 {dynamic_floor_sl}，交易所仍为 {old_sl}），下周期将重试")
                print(f"[Stop Sync Failed] {inst_id} short@{pos_venue}: 本地 {dynamic_floor_sl} / 交易所未确认，下周期重试")
        else:
            t["trailingStopPx"] = dynamic_floor_sl

        # A. Hit Ratchet Floor Stop (Locked Profit Trigger)
        if cur_px >= dynamic_floor_sl and peak_profit_px >= tier1_breakeven_trigger:
            closed, close_detail = close_position_confirmed(inst_id, "short", pos_sz, venue=pos_venue)
            if not closed:
                executed_actions.append(f"[{name}] 锁利平空失败，仓位仍保留: {close_detail}")
                return False, "平仓失败"
            close_fee = _close_fee(pos_sz, ct_val, cur_px, TAKER_FEE_RATE)
            pnl_val = curr_pos["upl"]
            executed_actions.append(f"[{name}] 🛡️ 触发阶梯动态锁利平仓 (净盈亏: {pnl_val:+.2f}U)")
            record_trade(_close_trade_payload(
                is_long=is_long, timestamp_full=timestamp_full, name=name,
                action_type="阶梯锁利", side_suffix="阶梯锁利平仓",
                pos_sz=pos_sz, cur_px=cur_px, fee=close_fee, pnl=pnl_val,
                remark=f"最低 {t['lowWaterMark']} 触发阶梯利润锁定线 {dynamic_floor_sl}",
            ), position=curr_pos)
            if pos_key in trackers: del trackers[pos_key]
            _purge_after_close(inst_id, purge_ai_position_instructions)
            return True, "已阶梯锁利"

        # B. Kinetic Momentum Pullback Exit from Peak (Symmetric 2.0x ATR profit with 0.75x ATR pullback)
        if peak_profit_px >= momentum_tp_trigger and cur_px >= (t["lowWaterMark"] + momentum_pullback_buffer):
            closed, close_detail = close_position_confirmed(inst_id, "short", pos_sz, venue=pos_venue)
            if not closed:
                executed_actions.append(f"[{name}] 动能见底移动止盈失败，仓位仍保留: {close_detail}")
                return False, "平仓失败"
            close_fee = _close_fee(pos_sz, ct_val, cur_px, TAKER_FEE_RATE)
            pnl_val = curr_pos["upl"]
            executed_actions.append(f"[{name}] 🎯 触发低点反弹动能止盈 (净盈亏: {pnl_val:+.2f}U)")
            record_trade(_close_trade_payload(
                is_long=is_long, timestamp_full=timestamp_full, name=name,
                action_type="移动止盈", side_suffix="低点反弹止盈",
                pos_sz=pos_sz, cur_px=cur_px, fee=close_fee, pnl=pnl_val,
                remark=f"最低 {t['lowWaterMark']} 动能反弹触及移动止盈线",
            ), position=curr_pos)
            if pos_key in trackers: del trackers[pos_key]
            _purge_after_close(inst_id, purge_ai_position_instructions)
            return True, "已移动止盈"

    return False, "持仓监控中"

