"""币安执行面接入的离线测试（无网络、无凭证）。

覆盖双所化改造新增的四个只读面：
1. `BinanceTradingAdapter.open_orders()` 桥接（阶段 1）；
2. `BinanceTradingAdapter.positions()` 补 `pos` 字段（阶段 3，否则主脑静默忽略币安仓）；
3. `venue_runtime.get_venue_runtime()` 分派与 balances dict→list 归一（阶段 1）；
4. 门面 `_active_venue()` 三态（阶段 5）。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from astra_backend.exchanges.binance import BinanceTradingAdapter, BinanceExchange
from scripts.trader.venue_runtime import get_venue_runtime


class BinanceOpenOrdersBridgeTest(unittest.TestCase):
    """adapter.open_orders() 桥接内部 BinanceExchange.open_orders()。"""

    def setUp(self):
        self._ex = MagicMock(spec=BinanceExchange)
        self._ex._hedge_mode.return_value = True
        patch("astra_backend.exchanges.binance.make_binance_env",
              return_value=MagicMock(mode="demo", api_key="k", secret_key="s")).start()
        self.addCleanup(patch.stopall)
        patch("astra_backend.exchanges.binance.BinanceExchange",
              return_value=self._ex).start()

    def test_open_orders_bridges_internal(self):
        self._ex.open_orders.return_value = [
            {"instId": "BTC-USDT-SWAP", "ordId": "1", "state": "live", "posSide": "long"},
        ]
        adapter = BinanceTradingAdapter(session=MagicMock(), environment="demo")
        self.assertEqual(adapter.open_orders(), self._ex.open_orders.return_value)
        # 无参数时也应透传
        adapter.open_orders("BTC-USDT-SWAP")
        self._ex.open_orders.assert_called_with("BTC-USDT-SWAP")


class BinancePositionsPosFieldTest(unittest.TestCase):
    """adapter.positions() 补 pos 字段（币数绝对值），否则主脑 `pos>0` 判仓会静默忽略。"""

    def setUp(self):
        self._ex = MagicMock(spec=BinanceExchange)
        self._ex._hedge_mode.return_value = True
        patch("astra_backend.exchanges.binance.make_binance_env",
              return_value=MagicMock(mode="demo", api_key="k", secret_key="s")).start()
        self.addCleanup(patch.stopall)
        patch("astra_backend.exchanges.binance.BinanceExchange",
              return_value=self._ex).start()

    def test_positions_include_pos_field(self):
        self._ex.positions.return_value = [{
            "instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "0.5",
            "avgPx": "60000", "markPx": "61000", "upl": "500", "lever": "5",
            "mgnMode": "cross", "notionalUsd": "30500",
        }]
        adapter = BinanceTradingAdapter(session=MagicMock(), environment="demo")
        rows = adapter.positions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pos"], 0.5)
        self.assertEqual(rows[0]["amount"], 0.5)
        # 主脑判仓用的字段必须 > 0
        self.assertGreater(float(rows[0]["pos"]), 0.0)


class VenueRuntimeTest(unittest.TestCase):
    """get_venue_runtime 分派：OKX 返回模块本身，币安返回薄对象统一三读形状。"""

    def test_okx_returns_module_itself(self):
        okx_rest = MagicMock()
        rt = get_venue_runtime("okx", okx_rest=okx_rest, binance_rest=MagicMock())
        self.assertIs(rt, okx_rest)

    def test_auto_and_invalid_fall_back_to_okx(self):
        okx_rest = MagicMock()
        for v in ("auto", "okx", "garbage", ""):
            self.assertIs(get_venue_runtime(v, okx_rest=okx_rest, binance_rest=MagicMock()),
                          okx_rest)

    def test_binance_balances_dict_wrapped_in_list(self):
        binance_rest = MagicMock()
        binance_rest.account_snapshot.return_value = {
            "totalEq": "1000",
            "details": [{"ccy": "USDT", "eq": "1000", "availBal": "800", "cashBal": "700", "upl": "10"}],
        }
        rt = get_venue_runtime("binance", okx_rest=MagicMock(), binance_rest=binance_rest)
        bal = rt.balances()
        # 关键：dict → list 归一，下游 bal_res[0]["details"] 才能正常工作
        self.assertIsInstance(bal, list)
        self.assertEqual(bal[0]["details"][0]["ccy"], "USDT")
        self.assertEqual(bal[0]["details"][0]["availBal"], "800")

    def test_binance_runtime_returns_empty_when_adapter_none(self):
        rt = get_venue_runtime("binance", okx_rest=MagicMock(), binance_rest=None)
        self.assertEqual(rt.positions(), [])
        self.assertEqual(rt.pending_orders(), [])
        self.assertEqual(rt.balances(), [])

    def test_binance_runtime_uses_open_orders_for_pending(self):
        binance_rest = MagicMock()
        binance_rest.open_orders.return_value = [{"instId": "X", "state": "live"}]
        rt = get_venue_runtime("binance", okx_rest=MagicMock(), binance_rest=binance_rest)
        self.assertEqual(rt.pending_orders(), binance_rest.open_orders.return_value)


class ActiveVenueTest(unittest.TestCase):
    """门面 `_active_venue()`：只认 preferred_venue == "binance"，其余恒 OKX。"""

    def _call(self, preferred):
        import scripts.ai_factor_trader as aft
        with patch.object(aft, "load_preferred_venue", return_value=preferred):
            return aft._active_venue()

    def test_binance(self):
        self.assertEqual(self._call("binance"), "binance")

    def test_auto_okx_and_invalid(self):
        for v in ("auto", "okx", "garbage", ""):
            self.assertEqual(self._call(v), "okx")


if __name__ == "__main__":
    unittest.main()
