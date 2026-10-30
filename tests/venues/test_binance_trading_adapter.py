"""BinanceTradingAdapter 桥接层离线测试（无网络、无凭证）。

覆盖：能力表声明（supports_orders / adapter_execution_flag / position_modes /
entry_ready_position_modes）、positions() 归一化（size_signed/side/entry_price/
mark_price，含 net 与 long_short 两态）、detect_position_mode() 分派、
place_protected_limit_order / place_protected_market_order 透传。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from astra_backend.exchanges.binance import BinanceTradingAdapter, BinanceExchange


class BinanceTradingAdapterTest(unittest.TestCase):
    def setUp(self):
        # 用 MagicMock 替换 BinanceExchange，避免真实网络/凭证。
        self._ex = MagicMock(spec=BinanceExchange)
        self._ex._hedge_mode.return_value = True  # 默认双向持仓
        self._patch_make = patch(
            "astra_backend.exchanges.binance.make_binance_env",
            return_value=MagicMock(mode="demo", api_key="k", secret_key="s"))
        self._patch_make.start()
        self.addCleanup(self._patch_make.stop)
        self._patch_ctor = patch(
            "astra_backend.exchanges.binance.BinanceExchange",
            return_value=self._ex)
        self._patch_ctor.start()
        self.addCleanup(self._patch_ctor.stop)

    def _adapter(self):
        return BinanceTradingAdapter(session=MagicMock(), environment="demo")

    # ------------------------------------------------------------------
    # 能力表
    # ------------------------------------------------------------------
    def test_capabilities_declare_trading_face(self):
        cap = BinanceTradingAdapter.capabilities
        self.assertEqual(cap.venue, "binance")
        self.assertTrue(cap.supports_orders)
        self.assertTrue(cap.supports_account)
        self.assertEqual(cap.adapter_execution_flag, "ASTRA_BINANCE_EXECUTION")
        self.assertEqual(cap.position_modes, ("net", "long_short"))
        self.assertEqual(cap.entry_ready_position_modes, ("long_short",))
        self.assertEqual(cap.quantity_unit, "base_asset")

    def test_detect_position_mode_hedge(self):
        self._ex._hedge_mode.return_value = True
        self.assertEqual(self._adapter().detect_position_mode(), "long_short")

    def test_detect_position_mode_net(self):
        self._ex._hedge_mode.return_value = False
        self.assertEqual(self._adapter().detect_position_mode(), "net")

    def test_detect_position_mode_unknown_on_error(self):
        self._ex._hedge_mode.side_effect = RuntimeError("network")
        self.assertEqual(self._adapter().detect_position_mode(), "unknown")

    # ------------------------------------------------------------------
    # positions() 归一化
    # ------------------------------------------------------------------
    def test_positions_normalize_long_short(self):
        self._ex.positions.return_value = [{
            "instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "0.5",
            "avgPx": "60000", "markPx": "60100", "upl": "50",
            "lever": "10", "mgnMode": "cross",
        }]
        out = self._adapter().positions()
        self.assertEqual(len(out), 1)
        p = out[0]
        self.assertEqual(p["venue"], "binance")
        self.assertEqual(p["side"], "long")
        self.assertEqual(p["posSide"], "long")
        self.assertEqual(p["amount"], 0.5)
        self.assertEqual(p["size_signed"], 0.5)
        self.assertEqual(p["entry_price"], 60000.0)
        self.assertEqual(p["mark_price"], 60100.0)
        self.assertEqual(p["unrealized_pnl"], 50.0)
        self.assertEqual(p["base"], "BTC")

    def test_positions_normalize_short_signed_negative(self):
        self._ex.positions.return_value = [{
            "instId": "BTC-USDT-SWAP", "posSide": "short", "pos": "0.5",
            "avgPx": "60000", "markPx": "59900", "upl": "50",
            "lever": "10", "mgnMode": "cross",
        }]
        p = self._adapter().positions()[0]
        self.assertEqual(p["side"], "short")
        self.assertEqual(p["size_signed"], -0.5)

    def test_positions_normalize_net_derives_side_from_sign(self):
        # one-way(net) 持仓：pos 是有符号数，负值 ⇒ short
        self._ex.positions.return_value = [{
            "instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "-0.5",
            "avgPx": "60000", "markPx": "59900", "upl": "50",
            "lever": "10", "mgnMode": "cross",
        }]
        p = self._adapter().positions()[0]
        self.assertEqual(p["side"], "short")
        self.assertEqual(p["size_signed"], -0.5)

    def test_positions_net_positive_is_long(self):
        self._ex.positions.return_value = [{
            "instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "0.5",
            "avgPx": "60000", "markPx": "60100", "upl": "50",
            "lever": "10", "mgnMode": "cross",
        }]
        p = self._adapter().positions()[0]
        self.assertEqual(p["side"], "long")
        self.assertEqual(p["size_signed"], 0.5)

    # ------------------------------------------------------------------
    # 下单 / 平仓 / 对账透传
    # ------------------------------------------------------------------
    def test_place_protected_limit_order_delegates(self):
        self._ex.place_protected_limit_order.return_value = {"orderId": "1"}
        ad = self._adapter()
        r = ad.place_protected_limit_order(
            "BTC-USDT-SWAP", "buy", "long", "0.1", "60000", "61000", "59000")
        self._ex.place_protected_limit_order.assert_called_once_with(
            "BTC-USDT-SWAP", "buy", "long", "0.1", "60000", "61000", "59000")
        self.assertEqual(r, {"orderId": "1"})

    def test_place_protected_market_order_delegates(self):
        self._ex.place_protected_market_order.return_value = {"orderId": "2"}
        ad = self._adapter()
        # `px_ref` 必须透传：币安市价单的 minNotional 校验要用**同一个**锚价
        # （2026-10-30：市价档此前跳过名义额校验，被交易所 -4164 拒）。
        r = ad.place_protected_market_order(
            "BTC-USDT-SWAP", "buy", "long", "0.1", "61000", "59000", px_ref="60000")
        self._ex.place_protected_market_order.assert_called_once_with(
            "BTC-USDT-SWAP", "buy", "long", "0.1", "61000", "59000", px_ref="60000")
        self.assertEqual(r, {"orderId": "2"})

    def test_close_position_delegates(self):
        self._ex.close_position.return_value = {"ok": True}
        r = self._adapter().close_position("BTC-USDT-SWAP", "long")
        self._ex.close_position.assert_called_once_with("BTC-USDT-SWAP", "long")
        self.assertEqual(r, {"ok": True})

    def test_account_snapshot_delegates_to_balance(self):
        self._ex.balance.return_value = [{"totalEq": "1000", "details": []}]
        r = self._adapter().account_snapshot()
        self.assertEqual(r, {"totalEq": "1000", "details": []})


if __name__ == "__main__":
    unittest.main()
