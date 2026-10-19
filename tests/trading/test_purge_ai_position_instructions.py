"""`scripts/trader/position_mgmt.py::purge_ai_position_instructions` 的单元测试。

## 这个测试在守什么

时间止损（或其它机械退出）把一笔持仓平掉后，主脑指令文件
（`data/ai_position_management.json`）里针对该标的的陈旧指令若不清理，下一轮
`execute_ai_position_management` 会再消费一条**注定无法执行**的指令，打出
"不在本路径持仓字典"的告警（见 `position_mgmt.py` 第一百一十七刀留痕）。

`purge_ai_position_instructions` 负责在机械平仓成功后，把该标的的指令从文件里
剔除。它必须满足以下**安全语义**（fail-open，绝不让清理动作升格为交易故障）：

1. **文件不存在** → 原样不动，返回 `False`（绝不凭空新建文件）。
2. **文件解析失败 / 结构异常** → 吞掉异常，返回 `False`（绝不改写半坏文件）。
3. **不含目标 `instId`** → 不动文件，返回 `False`（无谓写）。
4. **含目标 `instId`** → 只剔除匹配项，**保留其余字段与其它标的指令**，返回 `True`。
5. **原子写回**：写过程用 `tempfile + os.replace`，读者永不观察到半截文件。

本文件不触网、不触交易所，全部用临时文件对拍。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.trader.position_mgmt import purge_ai_position_instructions


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class PurgeAiPositionInstructionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "ai_position_management.json"

    def test_missing_file_returns_false_and_does_not_create(self):
        self.assertFalse(purge_ai_position_instructions(
            "BTC-USDT-SWAP", ai_position_management_file=str(self.path)))
        self.assertFalse(self.path.exists(), "文件不存在时不得新建")

    def test_matching_instruction_is_removed_and_others_preserved(self):
        _write(self.path, {
            "timestamp": 123456,
            "time_str": "T",
            "policy_version": "v1",
            "instructions": [
                {"instId": "AAPL-USDT-SWAP", "action": "UPDATE_SL", "confidence": 90},
                {"instId": "BTC-USDT-SWAP", "action": "HOLD", "confidence": 80},
                {"instId": "AAPL-USDT-SWAP", "action": "CLOSE_MARKET", "confidence": 99},
            ],
        })
        self.assertTrue(purge_ai_position_instructions(
            "AAPL-USDT-SWAP", ai_position_management_file=str(self.path)))
        payload = _read(self.path)
        self.assertEqual(payload["timestamp"], 123456, "其余字段必须保留")
        self.assertEqual(payload["time_str"], "T")
        self.assertEqual(payload["policy_version"], "v1")
        self.assertEqual([i["instId"] for i in payload["instructions"]],
                         ["BTC-USDT-SWAP"], "只剔除匹配项，保留其它标的")

    def test_no_match_returns_false_and_leaves_file_untouched(self):
        _write(self.path, {
            "instructions": [{"instId": "BTC-USDT-SWAP", "action": "HOLD"}],
        })
        before = self.path.read_text(encoding="utf-8")
        self.assertFalse(purge_ai_position_instructions(
            "AAPL-USDT-SWAP", ai_position_management_file=str(self.path)))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before,
                         "无匹配项时文件字节级不变")

    def test_corrupt_json_returns_false(self):
        self.path.write_text("{ not valid json", encoding="utf-8")
        self.assertFalse(purge_ai_position_instructions(
            "AAPL-USDT-SWAP", ai_position_management_file=str(self.path)))

    def test_non_dict_or_non_list_instructions_returns_false(self):
        _write(self.path, ["not", "a", "dict"])
        self.assertFalse(purge_ai_position_instructions(
            "AAPL-USDT-SWAP", ai_position_management_file=str(self.path)))
        _write(self.path, {"instructions": "not-a-list"})
        self.assertFalse(purge_ai_position_instructions(
            "AAPL-USDT-SWAP", ai_position_management_file=str(self.path)))

    def test_clearing_all_instructions_keeps_file_with_empty_list(self):
        _write(self.path, {
            "timestamp": 1,
            "instructions": [{"instId": "AAPL-USDT-SWAP", "action": "UPDATE_SL"}],
        })
        self.assertTrue(purge_ai_position_instructions(
            "AAPL-USDT-SWAP", ai_position_management_file=str(self.path)))
        payload = _read(self.path)
        self.assertEqual(payload["instructions"], [], "清空后保留空列表结构")
        self.assertEqual(payload["timestamp"], 1)

    def test_inst_id_comparison_is_string_coerced(self):
        # instId 可能以非字符串写入（历史数据）；清理必须按字符串语义匹配。
        _write(self.path, {
            "instructions": [{"instId": 12345, "action": "HOLD"}],
        })
        self.assertTrue(purge_ai_position_instructions(
            "12345", ai_position_management_file=str(self.path)))
        self.assertEqual(_read(self.path)["instructions"], [])


if __name__ == "__main__":
    unittest.main()
