"""看板因子视图装配（`astra_backend/dashboard_payload/factors_view.py`）残余分支收口测试 —— 第 362 刀。

本模块 141 行，负责操盘看板标的因子列表、AI 决策研判与多维度信号提取：
- 状态与因子库文件非迭代格式容错（`state_data` / `lib_data` 的 `instruments` 键为非列表脏数据时安全捕获 pass，不影响其他标的装配）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from astra_backend.dashboard_payload.factors_view import build_factors_list


class DashboardFactorsViewTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="astra_dashboard_factors_view_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.dec_file = self.tmp_path / "decisions.json"
        self.dec_file.write_text("{}", encoding="utf-8")

    def test_build_factors_list_non_iterable_instruments_keys_handled(self):
        # 当 state_file 或 factor_file 中的 instruments 字段为非列表（如整数/脏类型）时，
        # 捕获 TypeError/Exception 并静默忽略 (lines 35-36, 44-45)
        state_file = self.tmp_path / "trading_state.json"
        state_file.write_text(json.dumps({"instruments": 123}), encoding="utf-8")

        factor_file = self.tmp_path / "factor_lib.json"
        factor_file.write_text(json.dumps({"instruments": 456}), encoding="utf-8")

        factors_list, state_data = build_factors_list(
            decisions_file=self.dec_file,
            state_file=state_file,
            factor_file=factor_file,
            positions=[],
            timestamp_full="2026-09-22 12:00:00",
        )

        self.assertIsInstance(factors_list, list)
        self.assertGreater(len(factors_list), 0)
        # 验证默认标的均已生成且包含完整字段
        self.assertEqual(factors_list[0]["instId"], "BTC-USDT-SWAP")
        self.assertIn("strategy_tag", factors_list[0])

    def test_build_factors_list_valid_instruments_populated(self):
        # 正常字典列表形式装配 inst_state_map 与 factor_lib_map (lines 33-34, 42-43)
        state_file = self.tmp_path / "trading_state_valid.json"
        state_file.write_text(json.dumps({"instruments": [{"instId": "BTC-USDT-SWAP", "action": "BUY_LONG", "vwap_bias": 1.25}]}), encoding="utf-8")

        factor_file = self.tmp_path / "factor_lib_valid.json"
        factor_file.write_text(json.dumps({"instruments": [{"instId": "BTC-USDT-SWAP", "price": 60000.0}]}), encoding="utf-8")

        factors_list, state_data = build_factors_list(
            decisions_file=self.dec_file,
            state_file=state_file,
            factor_file=factor_file,
            positions=[],
            timestamp_full="2026-09-22 12:00:00",
        )
        self.assertEqual(factors_list[0]["instId"], "BTC-USDT-SWAP")
        self.assertEqual(factors_list[0]["vwap_bias"], 1.25)
        self.assertEqual(factors_list[0]["price"], 60000.0)

    def test_build_factors_list_merges_binance_snapshot_with_okx_priority(self):
        """分所快照合并：OKX 与币安都有同一 instId 时，OKX 因子优先；币安独有币种照常进入。"""
        from unittest.mock import patch
        from astra_backend.dashboard_payload import factors_view as FV

        # 合并池：OKX 的 BTC + 币安独有 SOL
        merged_pool = [
            {"instId": "BTC-USDT-SWAP", "name": "BTC", "ccy": "BTC", "venue": "okx"},
            {"instId": "SOL-USDT-SWAP", "name": "SOL", "ccy": "SOL", "venue": "binance"},
        ]
        # OKX 快照：BTC 的 price=60000
        okx_file = self.tmp_path / "factor_library_snapshot.json"
        okx_file.write_text(json.dumps({"instruments": [
            {"instId": "BTC-USDT-SWAP", "price": 60000.0, "chg24h": 1.0}
        ]}), encoding="utf-8")
        # 币安快照：BTC 的 price=99999（应被 OKX 覆盖）+ 币安独有 SOL
        binance_file = self.tmp_path / "factor_library_snapshot_binance.json"
        binance_file.write_text(json.dumps({"instruments": [
            {"instId": "BTC-USDT-SWAP", "price": 99999.0, "chg24h": 9.0},
            {"instId": "SOL-USDT-SWAP", "price": 150.0, "chg24h": 2.0},
        ]}), encoding="utf-8")

        state_file = self.tmp_path / "trading_state.json"
        state_file.write_text(json.dumps({"instruments": []}), encoding="utf-8")

        with patch.object(FV, "load_merged_instruments", return_value=merged_pool):
            factors_list, _ = build_factors_list(
                decisions_file=self.dec_file,
                state_file=state_file,
                factor_file=okx_file,
                positions=[],
                timestamp_full="2026-09-22 12:00:00",
            )

        by_id = {row["instId"]: row for row in factors_list}
        self.assertEqual(by_id["BTC-USDT-SWAP"]["price"], 60000.0,
                         "重复 instId 因子取 OKX 优先")
        self.assertEqual(by_id["BTC-USDT-SWAP"]["venue"], "okx")
        self.assertEqual(by_id["SOL-USDT-SWAP"]["price"], 150.0,
                         "币安独有币种读币安快照")
        self.assertEqual(by_id["SOL-USDT-SWAP"]["venue"], "binance")


if __name__ == "__main__":
    unittest.main()
