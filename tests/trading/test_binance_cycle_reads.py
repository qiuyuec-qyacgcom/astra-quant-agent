"""币安读路径测试：`fetch_positions_and_reconcile` 在 active_venue="binance" 时走币安。

覆盖双所化改造阶段 4 的读路径核心不变量：
1. active_venue="binance" 时挂单/余额走币安 adapter（open_orders / account_snapshot）；
2. account_snapshot 的 dict 经 venue_runtime 归一为 list，usdt_available 正确提取；
3. active_venue 默认 "okx"（向后兼容）仍走 okx_rest，行为不变。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.trader.cycle_stages import fetch_positions_and_reconcile  # noqa: E402


class _Env:
    def __init__(self, mode="demo"):
        self.mode = mode


class _OkxRest:
    def pending_orders(self):
        return []

    def balances(self):
        return [{"details": [{"ccy": "USDT", "availBal": "9999", "eq": "9999"}]}]


class _BinanceRest:
    """币安 adapter 的只读面 fake（open_orders / account_snapshot / positions）。"""

    def __init__(self, orders=(), snapshot=None):
        self._orders = list(orders)
        self._snapshot = snapshot if snapshot is not None else {
            "totalEq": "5000",
            "details": [{"ccy": "USDT", "eq": "5000", "availBal": "3000", "cashBal": "2500", "upl": "100"}],
        }

    def open_orders(self):
        return list(self._orders)

    def account_snapshot(self):
        return self._snapshot


def _run(active_venue="okx", binance_rest=None, okx_rest=None, positions=()):
    okx_rest = okx_rest or _OkxRest()
    out = fetch_positions_and_reconcile(
        entries_blocked=False,
        current_environment=lambda: _Env("demo"),
        okx_rest=okx_rest,
        query_positions=lambda: (True, list(positions), ""),
        reconcile_reservation_ledger=lambda *a, **k: None,
        binance_rest=binance_rest,
        active_venue=active_venue)
    return out


class BinanceReadPathTest(unittest.TestCase):
    def test_binance_reads_use_binance_snapshot(self):
        bn = _BinanceRest()
        out = _run(active_venue="binance", binance_rest=bn,
                   positions=[{"instId": "BTC-USDT-SWAP", "pos": "0.01", "posSide": "long"}])
        # 返回值第 10 项是 usdt_available（签名：... short_count, usdt_available）
        usdt_available = out[10]
        self.assertEqual(usdt_available, 3000.0)

    def test_binance_pending_orders_are_counted(self):
        bn = _BinanceRest(orders=[{"instId": "ETH-USDT-SWAP", "posSide": "long", "state": "live"}])
        out = _run(active_venue="binance", binance_rest=bn, positions=())
        # pending_inst_ids 在第 4 项（签名：... long_count, pending_inst_ids, ...）
        pending_inst_ids = out[4]
        self.assertIn("ETH-USDT-SWAP", pending_inst_ids)

    def test_default_okx_still_uses_okx(self):
        okx = _OkxRest()
        out = _run(active_venue="okx", okx_rest=okx, positions=())
        usdt_available = out[10]
        self.assertEqual(usdt_available, 9999.0)

    def test_binance_account_snapshot_dict_normalized(self):
        # account_snapshot 返回 dict，venue_runtime 应包成 list 供 bal_res[0]["details"] 用。
        bn = _BinanceRest(snapshot={"totalEq": "42", "details": [{"ccy": "USDT", "eq": "42", "availBal": "10"}]})
        out = _run(active_venue="binance", binance_rest=bn, positions=())
        self.assertEqual(out[10], 10.0)


if __name__ == "__main__":
    unittest.main()
