"""`load_merged_instruments()`（两所标的池合并去重）的单元测试 —— 2026-10 双所矩阵展示。

覆盖三条核心语义：
1. 两所池合并（OKX 在前、币安在后，顺序即前端展示顺序）。
2. 按 instId 去重，OKX 优先（币安重复 instId 丢弃）。
3. 每条目带 `venue` 标记（小写 `"okx"`/`"binance"`）。

⚠️ 不碰真实 `data/`：`load_instruments` 直接打桩，避免读生产池文件。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import scripts.instrument_pool as ip


OKX_POOL = [
    {"instId": "BTC-USDT-SWAP", "name": "BTC", "ccy": "BTC", "ctVal": 0.01},
    {"instId": "ETH-USDT-SWAP", "name": "ETH", "ccy": "ETH", "ctVal": 0.1},
]
BINANCE_POOL = [
    {"instId": "BTC-USDT-SWAP", "name": "BTC", "ccy": "BTC", "ctVal": 1.0},  # 重复 → 丢弃
    {"instId": "SOL-USDT-SWAP", "name": "SOL", "ccy": "SOL", "ctVal": 1.0},  # 币安独有
]


class LoadMergedInstrumentsTests(unittest.TestCase):
    def _merged(self):
        # 按 venue 分派返回两所池
        def _load(venue=None):
            v = ip.normalize_venue(venue)
            return OKX_POOL if v == "okx" else BINANCE_POOL
        with patch.object(ip, "load_instruments", side_effect=_load):
            return ip.load_merged_instruments()

    def test_merges_both_venues_and_dedups_okx_first(self):
        merged = self._merged()
        inst_ids = [item["instId"] for item in merged]
        # OKX 的 BTC/ETH 在前，币安独有 SOL 在后；币安重复 BTC 被丢弃
        self.assertEqual(inst_ids, ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"])

    def test_each_entry_carries_venue_marker(self):
        merged = self._merged()
        by_id = {item["instId"]: item for item in merged}
        self.assertEqual(by_id["BTC-USDT-SWAP"]["venue"], "okx", "重复币种归属 OKX")
        self.assertEqual(by_id["ETH-USDT-SWAP"]["venue"], "okx")
        self.assertEqual(by_id["SOL-USDT-SWAP"]["venue"], "binance", "币安独有币种归属币安")

    def test_okx_ctval_wins_for_duplicate(self):
        """重复 instId 时保留 OKX 版本（含其 ctVal 语义），币安版本被丢弃。"""
        merged = self._merged()
        btc = next(item for item in merged if item["instId"] == "BTC-USDT-SWAP")
        self.assertEqual(btc["ctVal"], 0.01, "去重必须保留 OKX 条目（含 ctVal）")

    def test_single_venue_failure_does_not_break_the_other(self):
        """单所加载抛异常时，另一所照常返回（fail-closed 但不拖垮整个矩阵）。"""
        def _load(venue=None):
            v = ip.normalize_venue(venue)
            if v == "binance":
                raise RuntimeError("币安池读取失败")
            return OKX_POOL
        with patch.object(ip, "load_instruments", side_effect=_load):
            merged = ip.load_merged_instruments()
        self.assertEqual([item["instId"] for item in merged],
                         ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])

    def test_inst_id_is_normalized_for_dedup(self):
        """instId 大小写/空白差异不影响去重（防御脏数据）。"""
        dirty_binance = [
            {"instId": "  btc-usdt-swap ", "name": "BTC", "ccy": "BTC", "ctVal": 1.0},
        ]
        def _load(venue=None):
            v = ip.normalize_venue(venue)
            return OKX_POOL if v == "okx" else dirty_binance
        with patch.object(ip, "load_instruments", side_effect=_load):
            merged = ip.load_merged_instruments()
        self.assertEqual(len(merged), 2, "脏数据（小写+空白）的重复 BTC 仍应被去重")


if __name__ == "__main__":
    unittest.main()
