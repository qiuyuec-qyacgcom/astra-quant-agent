"""币安活动持仓 → 台账 holding 行的字段映射与 ct_val 修正测试。

双所化改造阶段 5：币安在持仓位要进风险界面 holding 行。关键不变量：
1. `_binance_to_holding_shape` 把币安 adapter 归一化行映射到 `_holding_row` 期望的
   OKX 原始形状（pos/avgPx/markPx/upl/lever/notional/margin/cTime）；
2. `_holding_row` 对 `venue="binance"` 强制 `ct_val=1.0`（币本位），否则 get_ct_val
   会误查 OKX 的 ctVal（如 BTC=0.01）导致 notional 少 100 倍。
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sync_full_ledger as sfl  # noqa: E402


def _binance_pos(inst, side, sz, px, upl=0.0):
    """币安 adapter positions() 的归一化行（含 raw 原始行）。"""
    raw = {
        "instId": f"{inst}-USDT-SWAP", "posSide": side, "pos": str(sz),
        "avgPx": str(int(px)) if float(px).is_integer() else str(px),
        "markPx": str(int(px)) if float(px).is_integer() else str(px),
        "upl": str(upl), "lever": "5",
        "mgnMode": "cross", "margin": str(abs(sz) * px / 5), "notionalUsd": str(abs(sz) * px),
        "cTime": "1789000000000",
    }
    return {
        "venue": "binance", "exchange": "binance", "symbol": f"{inst}-USDT-SWAP",
        "instId": f"{inst}-USDT-SWAP", "base": inst, "side": side, "posSide": side,
        "amount": abs(sz), "pos": abs(sz), "size_signed": sz if side == "long" else -abs(sz),
        "entry_price": float(px), "mark_price": float(px), "unrealized_pnl": upl,
        "upl": upl, "leverage": 5, "margin_mode": "cross", "raw": raw,
    }


class BinanceHoldingShapeTest(unittest.TestCase):
    def test_maps_normalized_to_holding_shape(self):
        p = _binance_pos("BTC", "long", 0.01, 60000.0, 100.0)
        shaped = sfl._binance_to_holding_shape(p)
        self.assertEqual(shaped["instId"], "BTC-USDT-SWAP")
        self.assertEqual(shaped["posSide"], "long")
        self.assertEqual(shaped["pos"], "0.01")
        self.assertEqual(shaped["avgPx"], "60000")
        self.assertEqual(shaped["markPx"], "60000")
        self.assertEqual(float(shaped["upl"]), 100.0)
        self.assertEqual(shaped["lever"], "5")
        self.assertEqual(shaped["notional"], "600.0")
        self.assertEqual(shaped["cTime"], "1789000000000")

    def test_maps_fallback_when_raw_missing(self):
        p = _binance_pos("BTC", "long", 0.01, 60000.0, 100.0)
        p["raw"] = {}  # 原始行缺失，回退到归一化字段
        shaped = sfl._binance_to_holding_shape(p)
        self.assertEqual(shaped["pos"], 0.01)       # 回退 amount
        self.assertEqual(shaped["avgPx"], 60000.0)  # 回退 entry_price
        self.assertEqual(shaped["markPx"], 60000.0)  # 回退 mark_price


class BinanceHoldingRowCtValTest(unittest.TestCase):
    """币安 holding 行必须用 ct_val=1（币本位），不得误用 OKX ctVal。"""

    def setUp(self):
        self.env = MagicMock()
        self.env.mode = "demo"
        self.tz_bj = datetime.timezone(datetime.timedelta(hours=8))

    def _build_row(self, p, ct_val_for_okx=0.01):
        # get_ct_val 返回 OKX 的面值（如 BTC=0.01）；币安分支应忽略它，强制 ct_val=1。
        with patch.object(sfl, "get_ct_val", return_value=ct_val_for_okx):
            return sfl._holding_row(
                p, "binance", env=self.env, trackers={}, tz_bj=self.tz_bj,
                allowed={"BTC-USDT-SWAP"}, council_by_inst={}, unmanaged=None)

    def test_notional_uses_ct_val_one_for_binance(self):
        p = sfl._binance_to_holding_shape(_binance_pos("BTC", "long", 0.01, 60000.0, 100.0))
        row = self._build_row(p, ct_val_for_okx=0.01)
        # 币本位：0.01 BTC × 1 × 60000 = 600 USDT（而非 ×0.01 = 6 USDT）
        self.assertAlmostEqual(row["sz"], 0.01)
        # margin = notional / lever = 600 / 5 = 120
        self.assertAlmostEqual(row["margin"], 120.0, places=1)
        # raw notional 有值(600.0)，直接采用
        self.assertAlmostEqual(row["sz"] * 1.0 * 60000.0, 600.0)

    def test_binance_holding_row_venue(self):
        p = sfl._binance_to_holding_shape(_binance_pos("BTC", "long", 0.01, 60000.0, 100.0))
        with patch.object(sfl, "get_ct_val", return_value=0.01):
            row = sfl._holding_row(
                p, "binance", env=self.env, trackers={}, tz_bj=self.tz_bj,
                allowed={"BTC-USDT-SWAP"}, council_by_inst={}, unmanaged=None)
        self.assertEqual(row["venue"], "binance")
        self.assertEqual(row["id"], "holding_binance_BTC_多")
        self.assertEqual(row["status"], "holding")


if __name__ == "__main__":
    unittest.main()
