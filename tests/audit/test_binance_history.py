"""`build_binance_trade` 纯转换测试（币安平仓周期行 → 台账 closed 行）。

覆盖双所化不变量：
1. `venue="binance"`（自进化引擎按所过滤的前提）；
2. 盈亏口径 `net_pnl = gross_pnl + fee`（币安 pnl 已含费、fee 为负，故 gross = net - fee）；
3. 出场原因 `exit_reason_source="inferred"`（币安无平仓单匹配，不伪装机制确认）。
"""
from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.ledger.binance_history import build_binance_trade  # noqa: E402


class _Env:
    mode = "demo"


def _cycle(**overrides):
    h = {
        "instId": "BTC-USDT-SWAP", "direction": "long",
        "openAvgPx": "60000", "closeAvgPx": "61000",
        "pnl": "90.0", "fee": "-10.0", "openFee": "-5.0", "closeFee": "-5.0",
        "closeTotalPos": "0.01", "openMaxPos": "0.01", "lever": "5",
        "cTime": "1789000000000", "uTime": "1789003600000", "posId": "pos1",
        "type": "2", "pnlRatio": "0.1",
    }
    h.update(overrides)
    return h


def _build(h, *, allowed=None, reset="2026-01-01 00:00:00"):
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    return build_binance_trade(
        h=h, reset_time=reset, allowed=allowed or {"BTC-USDT-SWAP"},
        env=_Env(), tz_bj=tz_bj, id_suffix="_1", datetime=datetime,
        get_ct_val=lambda inst: 1.0)


class BinanceTradeTest(unittest.TestCase):
    def test_venue_is_binance(self):
        row = _build(_cycle())
        self.assertEqual(row["venue"], "binance")
        self.assertEqual(row["status"], "closed")

    def test_net_pnl_equals_gross_plus_fee(self):
        # 币安 pnl 已含费（净），fee 为负；gross = net - fee，保证 net = gross + fee
        row = _build(_cycle(pnl="90.0", fee="-10.0"))
        self.assertEqual(row["net_pnl"], 90.0)
        self.assertEqual(row["gross_pnl"], 100.0)  # 90 - (-10) = 100
        self.assertAlmostEqual(row["net_pnl"], row["gross_pnl"] + row["fee"], places=2)

    def test_exit_reason_source_is_inferred(self):
        row = _build(_cycle(pnl="90.0"))
        self.assertEqual(row["exit_reason_source"], "inferred")

    def test_side_from_direction(self):
        self.assertEqual(_build(_cycle(direction="long"))["side"], "多")
        self.assertEqual(_build(_cycle(direction="short"))["side"], "空")

    def test_not_allowed_returns_none(self):
        self.assertIsNone(_build(_cycle(), allowed={"ETH-USDT-SWAP"}))

    def test_lever_missing_is_dash(self):
        row = _build(_cycle(lever=None))
        self.assertEqual(row["lever"], "--")


if __name__ == "__main__":
    unittest.main()
