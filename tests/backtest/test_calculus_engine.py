"""因果演算引擎基础测试（`scripts/calculus_engine.py`）。

从 cnlimiter/r20-quantum-trader 移植的原始 pytest 用例改写为本项目 unittest 风格。
只测演算引擎本身的因果性与有界性，不涉及 factor_library / ai_factor_trader 集成。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.calculus_engine import calculate_calculus, calculate_multi_timeframe  # noqa: E402


class CalculusCausalityTest(unittest.TestCase):
    def test_calculus_is_causal_and_bounded(self):
        base = [100, 101, 102, 104, 107, 111, 116, 122, 129, 137]
        a = calculate_calculus(base)
        b = calculate_calculus(base + [145])
        self.assertTrue(a["valid"])
        self.assertTrue(b["valid"])
        self.assertTrue(-3 <= a["velocity"] <= 3)
        self.assertTrue(-3 <= a["acceleration"] <= 3)
        self.assertTrue(-3 <= a["impulse"] <= 3)
        self.assertNotEqual(a["velocity"], b["velocity"])
        self.assertIn("curvature", a)
        self.assertGreaterEqual(a["curvature"], 0)
        self.assertIn("power", a)
        self.assertIn("power_regime", a)

    def test_multi_timeframe_reverses_okx_newest_first(self):
        rows = [[str(i), "101", "99", str(100 + i), "1"] for i in range(10)]
        result = calculate_multi_timeframe({"15M": list(reversed(rows))})
        self.assertTrue(result["valid"])
        self.assertGreater(result["timeframes"]["15M"]["velocity"], 0)
        self.assertIn("power", result)
        self.assertIn("curvature", result)


if __name__ == "__main__":
    unittest.main()
