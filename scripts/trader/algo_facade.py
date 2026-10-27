"""云端算法单统一门面（双所）：把 OKX `okx_rest` 的三个方法映射到币安 algoOrder 系列。

背景（2026-10 双所接入）：
`scripts/trader/cloud_protection.py` 的 `ensure_cloud_position_protection` /
`sync_cloud_algo_stop` 通过**注入**的 `okx_rest` 调三个方法：

    pending_algo_orders(inst_id)   -> List[row]
    place_algo_oco(...)            -> 下 OCO（TP+SL 同一单）
    amend_algo_sl(algo_id, px, ...)-> 改止损

币安是**分离腿**模型（SL / TP 各一张 CONDITIONAL 算法单），语义不同。但
`astra_backend.exchanges.binance.BinanceExchange` 已提供等价的高层能力，且返回
结构与 OKX 同形：

    protection_orders(inst_id)                     -> [{algoId, instId, tpTriggerPx, slTriggerPx, posSide, sz, state, ...}]
    place_protection(inst_id, pos_side, sz, tp, sl) -> {algoId: ...}
    amend_stop(inst_id, algo_id, new_sl)           -> {algoId: ...}
    cancel_protection(inst_id, algo_id)

本模块只做**名字映射**，不复制任何业务逻辑，也不改变 cloud_protection 的判定。
端点依据官方文档（USDⓈ-M Futures · Algo Order）：
    POST   /fapi/v1/algoOrder
    GET    /fapi/v1/openAlgoOrders
    DELETE /fapi/v1/algoOrder
"""
from __future__ import annotations

from typing import Any


class VenueAlgoClient:
    """与 `okx_rest` 三个方法同名的最小门面，可直接注入 `cloud_protection`。"""

    def __init__(self, adapter: Any) -> None:
        self._ex = getattr(adapter, "exchange", adapter)

    # ── 读 ────────────────────────────────────────────────────────────
    def pending_algo_orders(self, inst_id: str) -> list[dict[str, Any]]:
        rows = self._ex.protection_orders(inst_id) or []
        return [r for r in rows if isinstance(r, dict)]

    # ── 写 ────────────────────────────────────────────────────────────
    def place_algo_oco(
        self,
        inst_id: str,
        side: str,
        sz: Any,
        *,
        pos_side: str,
        td_mode: str = "cross",
        tp_trigger_px: Any,
        tp_ord_px: Any = "-1",
        sl_trigger_px: Any,
        sl_ord_px: Any = "-1",
        reduce_only: bool = True,
        cxl_on_close_pos: bool = True,
    ) -> dict[str, Any]:
        """下一对 TP/SL 保护腿（币安内部按 STOP_MARKET + TAKE_PROFIT_MARKET 两条）。

        `side`/`td_mode`/`*_ord_px`/`reduce_only`/`cxl_on_close_pos` 是 OKX 侧概念，
        币安实现以 `closePosition=true` 表达「只减仓、平仓即撤」，故此处不转发。
        """
        return self._ex.place_protection(inst_id, pos_side, sz, tp_trigger_px, sl_trigger_px)

    def amend_algo_sl(
        self,
        algo_id: str,
        new_sl: Any,
        *,
        inst_id: str,
        new_sl_ord_px: Any = "-1",
    ) -> dict[str, Any]:
        """币安无原地改单接口 → 适配器内部走「撤旧 SL + 补新 SL」，对外语义一致。"""
        return self._ex.amend_stop(inst_id, algo_id, new_sl)

    def cancel_algo(self, inst_id: str, algo_id: str) -> dict[str, Any]:
        return self._ex.cancel_protection(inst_id, algo_id)


def algo_client_for(venue: str, adapter: Any = None) -> Any:
    """按生效所返回算法单客户端：binance → `VenueAlgoClient`；其余 → None（调用方回退 okx_rest）。"""
    if str(venue or "").strip().lower() != "binance" or adapter is None:
        return None
    return VenueAlgoClient(adapter)
