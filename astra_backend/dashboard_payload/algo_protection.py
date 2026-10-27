"""OKX 算法单（云端 TP/SL）保护度核验（结构优化阶段 2·B2 第九刀）。

原样搬自 update_cache_cycle 的「Parallel Phase 2」段（51 行）：
- 就地修改传入的 positions（写 exchangeSl/exchangeTp/protectionStatus 等），
  并把失败原因 append 进 source_errors —— 两者都是**入参原地改**，故无需回传；
- fetch_json 与 enrich_risk_fields 由门面注入（分别是 astra_backend/dashboard_cache.py 的
  `_fetch_json` 与 `enrich_position_risk_fields` —— 后者本身是薄壳，
  注入它才能让 POSITION_TRACKER_FILE 的 patch 继续生效）；trackers 是调用方局部量；
- 段内 `else: algo_results = {}` 这支在原文里就是**死赋值**（其后再无使用），
  照抄保留，不在本刀顺手清理。

## 第一百二十二刀：与生产者**统一契约**（`cloud_oco_verified`）

`cloud_oco_verified` 此前**只有外所聚合路径**会写，OKX 路径从来不写；
而前端判据是 `p.cloud_oco_verified !== false && p.protectionStatus !== 'unprotected'`
⇒ **同一个 `partially_protected` 状态在不同来源的行上判罚不一致**
（缺字段被当成 not-false）。更糟的是 `unknown`（不可判定）在 OKX 行也会被算作已保护。

现统一口径（外所生产者已随外所下架移除，本函数即唯一生产者）：
`cloud_oco_verified = (protectionStatus == "fully_protected")`，并统一补 `protectionLegs`。
⇒ 前端**无需改动**即得到一致且更严的判据
（`partially_protected` / `unknown` 一律不再算"已保护"）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from scripts import okx_rest

__all__ = ["collect_algo_protection"]


def _trigger_type_legs(algo):
    """把一条 OKX 算法腿按**它确实带了的触发价**标注 `kind`（供类型字段三态判定）。

    只有带 `slTriggerPx` 才算"有止损腿"，只有带 `tpTriggerPx` 才算"有止盈腿" ——
    否则"没有该类腿"会被误报成"未上报类型"。
    """
    legs = []
    if algo.get("slTriggerPx"):
        legs.append({"kind": "sl", "trigger_px_type": algo.get("slTriggerPxType")})
    if algo.get("tpTriggerPx"):
        legs.append({"kind": "tp", "trigger_px_type": algo.get("tpTriggerPxType")})
    return legs


_BINANCE_LIVE_STATUS = {"NEW", "RUNNING", "TRIGGERING"}


def _binance_leg(row):
    """币安算法腿（STOP_MARKET / TAKE_PROFIT_MARKET）→ 半 OKX 形状。

    只负责把单腿映射成 `{slTriggerPx|tpTriggerPx, sz, reduceOnly, posSide, state, algoId}`；
    非活跃状态（已触发/已撤）返回 None（与 OKX 侧 `state in {live, effective}` 同口径）。
    """
    status = str(row.get("algoStatus") or row.get("status") or "").strip().upper()
    if status and status not in _BINANCE_LIVE_STATUS:
        return None
    otype = str(row.get("orderType") or row.get("type") or "").strip().upper()
    trigger = row.get("triggerPrice") or row.get("stopPrice") or row.get("price")
    native = str(row.get("positionSide") or "BOTH").strip().upper()
    pos_side = {"LONG": "long", "SHORT": "short"}.get(native, "net")
    close_pos = str(row.get("closePosition") or "").strip().lower() in {"true", "1", "yes"}
    return {
        "algoId": str(row.get("algoId") or ""),
        "posSide": pos_side,
        "state": "live",
        "reduceOnly": "true",
        "sz": "" if close_pos else str(row.get("quantity") or row.get("origQty") or ""),
        "closePosition": close_pos,
        "tpTriggerPx" if "TAKE_PROFIT" in otype else "slTriggerPx": trigger,
    }


def _merge_binance_legs(rows):
    """把币安**分离**的 SL/TP 腿合成一条 OKX 同形状记录（SL 为主，补齐 TP）。

    币安每个保护腿是独立算法单（`STOP_MARKET` / `TAKE_PROFIT_MARKET`），而下面的判定
    逻辑假设「一条记录同时带 slTriggerPx 与 tpTriggerPx 才算 fully_protected」。
    不合并就会把已双保护的仓位误判成 `partially_protected`。
    """
    legs = [x for x in (_binance_leg(r) for r in (rows or []) if isinstance(r, dict)) if x]
    if not legs:
        return []
    sl = next((leg for leg in legs if leg.get("slTriggerPx")), None)
    tp = next((leg for leg in legs if leg.get("tpTriggerPx")), None)
    base = dict(sl or tp)
    if tp:
        base["tpTriggerPx"] = tp.get("tpTriggerPx")
    if sl:
        base["slTriggerPx"] = sl.get("slTriggerPx")
    _ids = [leg.get("algoId") for leg in (sl, tp) if leg and leg.get("algoId")]
    if _ids:
        base["algoId"] = ",".join(_ids)
    return [base]


def collect_algo_protection(positions, source_errors, fetch_json,
                           enrich_risk_fields, trackers, *, venue="okx",
                           binance_algo_fetch=None):
    # 惰性 import（不在 import 期拉 scripts 侧模块）；提到函数首行是因为下面的三态
    # 类型字段在循环里**多处**使用，曾因 import 写在用点之后触发 UnboundLocalError。
    from scripts.trader.venue_protection import (
        _is_full_close, protection_trigger_type_fields,
    )

    # Parallel Phase 2: Exchange algo orders for live TP/SL protection
    if positions:
        with ThreadPoolExecutor(max_workers=min(len(positions), 6)) as pool:
            if binance_algo_fetch is not None:
                futures = {
                    pos["instId"]: pool.submit(binance_algo_fetch, pos["instId"])
                    for pos in positions
                }
            else:
                futures = {
                    pos["instId"]: pool.submit(
                        fetch_json,
                        okx_rest.pending_algo_orders,
                        pos["instId"],
                    )
                    for pos in positions
                }
            algo_results = {inst_id: f.result() for inst_id, f in futures.items()}

        for position in positions:
            algo_ok, algo_orders, algo_error = algo_results.get(position["instId"], (False, [], "timeout"))
            if not algo_ok:
                source_errors.append(f"algo {position['instId']}: {algo_error}")
                algo_orders = []
            elif venue == "binance":
                # 币安分离腿 → 合并成 OKX 同形状记录（见 _merge_binance_legs）
                algo_orders = _merge_binance_legs(algo_orders)
            # 第一百六十七刀：触发价类型（读失败/无腿/未上报 三态，**不**用默认值顶替）。
            # 取数失败时先按"unknown"写，下面有腿时会按选中的腿覆盖。
            position.update(protection_trigger_type_fields([], readable=bool(algo_ok)))
            matching_algos = [
                o for o in (algo_orders or [])
                if str(o.get("state", "live")).lower() in {"live", "effective"}
                and str(o.get("posSide", "net")).lower() in {position["posSide"], "net"}
                and str(o.get("reduceOnly", "true")).lower() in {"true", "1", "yes"}
            ]
            # ⚠️ 第一百二十刀修正（真机可达的**假阴性**）：`protected_size` 只累加 `sz`，
            # 于是「整仓平」语义的腿（OKX `closePosition=true`）因为**给不出张数**
            # 而被算成 0 覆盖 ⇒ 面板/提示词把这笔报成
            # `partially_protected / 0%`，而交易所那条腿其实**平掉整个仓位**。
            # 判定与已专测的共享谓词一致（`venue_protection._is_full_close`
            # 覆盖各所的整仓平语义），不另写一套。
            _pos_sz = float(position.get("pos_sz") or position.get("pos") or 0)
            _sl_legs = [o for o in matching_algos if o.get("slTriggerPx")]
            full_close = any(_is_full_close(o) for o in _sl_legs)
            protected_size = sum(float(o.get("sz", 0) or 0) for o in _sl_legs)
            if full_close:
                protected_size = max(protected_size, _pos_sz)
            # 不可判定（**不得给出没有证据的确定结论**，与 `scan_protective_orders`
            # 的 `coverage_ok=None` 同一口径）的两种情形：
            #   a) 持仓规模读不出来（`pos_sz`/`pos` 都缺）⇒ 无从比较；
            #   b) 有止损腿但量读不出来、且不是整仓平语义。
            coverage_unknown = _pos_sz <= 0 or (bool(_sl_legs) and protected_size <= 0
                                                and not full_close)
            full_coverage = (not coverage_unknown) and protected_size >= _pos_sz * 0.999
            # 生产侧统一发这个字段 ⇒ 字段齐整（口径见 tests/ui/test_protection_contract.py）
            position["protectionLegs"] = len(matching_algos)
            live_algo = next((o for o in matching_algos if o.get("slTriggerPx") and o.get("tpTriggerPx")), None)
            if live_algo and full_coverage:
                position["exchangeSl"] = float(live_algo.get("slTriggerPx", 0) or 0)
                position["exchangeTp"] = float(live_algo.get("tpTriggerPx", 0) or 0)
                position["protectionStatus"] = "fully_protected"
                position["protectionCoveragePct"] = 100.0
                position["protectionAlgoId"] = live_algo.get("algoId", "")
                position["cloud_oco_verified"] = True
                position.update(protection_trigger_type_fields(
                    _trigger_type_legs(live_algo), readable=True))
            elif coverage_unknown:
                sl_algo = _sl_legs[0]
                position["exchangeSl"] = float(sl_algo.get("slTriggerPx", 0) or 0) or None
                position["exchangeTp"] = float(sl_algo.get("tpTriggerPx", 0) or 0) or None
                position["protectionStatus"] = "unknown"
                position["protectionCoveragePct"] = None
                position["protectionAlgoId"] = sl_algo.get("algoId", "")
                position["cloud_oco_verified"] = False
                position.update(protection_trigger_type_fields(
                    _trigger_type_legs(sl_algo), readable=True))
            elif matching_algos:
                sl_algo = next((o for o in matching_algos if o.get("slTriggerPx")), {})
                position["exchangeSl"] = float(sl_algo.get("slTriggerPx", 0) or 0) or None
                position["exchangeTp"] = float(sl_algo.get("tpTriggerPx", 0) or 0) or None
                position["protectionStatus"] = "partially_protected"
                position["protectionCoveragePct"] = round(min(100.0, protected_size / max(_pos_sz, 1e-12) * 100), 1)
                position["protectionAlgoId"] = sl_algo.get("algoId", "")
                position["cloud_oco_verified"] = False
                position.update(protection_trigger_type_fields(
                    _trigger_type_legs(sl_algo), readable=True))
            else:
                position["exchangeSl"] = None
                position["exchangeTp"] = None
                position["protectionStatus"] = "unprotected"
                position["protectionCoveragePct"] = 0.0
                position["protectionAlgoId"] = ""
                position["cloud_oco_verified"] = False
    else:
        algo_results = {}

    enrich_risk_fields(positions, trackers)
