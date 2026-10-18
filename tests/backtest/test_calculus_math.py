"""因果演算引擎数学正确性测试（`scripts/calculus_engine.py`）。

从 cnlimiter/r20-quantum-trader 的 `test_quant_system_calculus.py` 移植，
仅保留纯演算相关用例（演算数学 / 定积分 / 概率论 / 多周期聚合），
**剔除** FactorLibraryIntegrationTest / AiFactorTraderMathProbTest /
AiFactorTraderPositionProtectionTest —— 那三类依赖本项目已重构的
`factor_library` / `ai_factor_trader` 字段契约，不在「独立演算模块」边界内。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.calculus_engine import (  # noqa: E402
    calculate_calculus,
    calculate_multi_timeframe,
    calculate_definite_integrals,
    calculate_probability_theory,
    classify_regime,
    classify_integral_regime,
    classify_probability_regime,
    _normal_cdf,
    _ema,
    _diff,
    _normalise,
)


class CalculusEngineMathTest(unittest.TestCase):
    """Test mathematical accuracy and causality of calculus computations."""

    def test_monotonic_bullish_acceleration(self):
        prices = [100.0, 101.0, 103.0, 106.0, 110.0, 115.0, 122.0, 131.0, 142.0, 155.0]
        res = calculate_calculus(prices)
        self.assertTrue(res["valid"])
        self.assertGreater(res["velocity"], 0.0)
        self.assertGreater(res["impulse"], 0.0)
        self.assertEqual(res["direction"], 1)

    def test_monotonic_bearish_acceleration(self):
        prices = [155.0, 142.0, 131.0, 122.0, 115.0, 110.0, 106.0, 103.0, 101.0, 98.0]
        res = calculate_calculus(prices)
        self.assertTrue(res["valid"])
        self.assertLess(res["velocity"], 0.0)
        self.assertLess(res["impulse"], 0.0)
        self.assertEqual(res["direction"], -1)

    def test_decelerating_top_fomo_detection(self):
        prices = [100.0, 110.0, 118.0, 123.0, 125.0, 125.5, 125.6, 125.65]
        res = calculate_calculus(prices)
        self.assertTrue(res["valid"])
        self.assertLess(res["acceleration"], 0.0, "Decelerating rally must yield negative acceleration")

    def test_decelerating_bottom_panics_detection(self):
        prices = [200.0, 190.0, 182.0, 178.0, 177.0, 176.8, 176.7]
        res = calculate_calculus(prices)
        self.assertTrue(res["valid"])
        self.assertGreater(res["acceleration"], 0.0, "Decelerating plunge must yield positive acceleration")

    def test_strict_causality(self):
        history = [100.0, 100.2, 100.5, 100.9, 101.4, 102.0, 102.7]
        res_t1 = calculate_calculus(history)

        future_candle = [101.5]
        res_t2 = calculate_calculus(history + future_candle)

        self.assertTrue(res_t1["valid"])
        self.assertTrue(res_t2["valid"])
        self.assertNotEqual(res_t1["velocity"], res_t2["velocity"])

    def test_calculus_curvature_and_power_dynamics(self):
        # Monotonic accelerating prices: velocity > 0, acceleration > 0 => power > 0
        accel_prices = [100.0, 101.0, 103.0, 106.0, 110.0, 115.0, 122.0, 131.0, 142.0]
        res_acc = calculate_calculus(accel_prices)
        self.assertTrue(res_acc["valid"])
        self.assertGreater(res_acc["power"], 0.0, "Accelerating uptrend must have positive kinetic power flux")
        self.assertGreaterEqual(res_acc["curvature"], 0.0)

        # Decelerating top: velocity > 0, acceleration < 0 => power < 0 (kinetic exhaustion)
        decel_prices = [100.0, 110.0, 118.0, 123.0, 125.0, 125.5, 125.6, 125.65]
        res_dec = calculate_calculus(decel_prices)
        self.assertTrue(res_dec["valid"])
        self.assertLess(res_dec["power"], 0.0, "Decelerating rally must yield negative kinetic power (exhaustion)")
        self.assertIn(res_dec["power_regime"], ["KINETIC_EXHAUSTION", "HIGH_CURVATURE_INFLECTION", "STEADY_FLUX"])


class DefiniteIntegralsTest(unittest.TestCase):
    """Test trapezoidal definite integration of displacement energy and deviation area."""

    def test_positive_displacement_energy_integral(self):
        prices = [100.0, 102.0, 105.0, 109.0, 114.0, 120.0, 127.0, 135.0]
        res = calculate_definite_integrals(prices, window=8)
        self.assertTrue(res["valid"])
        self.assertGreater(res["energy_integral"], 0.0)
        self.assertGreater(res["deviation_area_integral"], 0.0)

    def test_negative_displacement_energy_integral(self):
        prices = [135.0, 127.0, 120.0, 114.0, 109.0, 105.0, 102.0, 100.0]
        res = calculate_definite_integrals(prices, window=8)
        self.assertTrue(res["valid"])
        self.assertLess(res["energy_integral"], 0.0)
        self.assertLess(res["deviation_area_integral"], 0.0)

    def test_volume_action_integral(self):
        prices = [100.0, 105.0, 110.0, 115.0]
        vols = [1000.0, 2000.0, 3000.0, 4000.0]
        res = calculate_definite_integrals(prices, vols=vols, window=4)
        self.assertTrue(res["valid"])
        self.assertGreater(res["volume_action_integral"], 0.0)


class ProbabilityTheoryTest(unittest.TestCase):
    """Test stochastic moments, fat tails and conditional continuation probability."""

    def test_normal_cdf_function(self):
        self.assertAlmostEqual(_normal_cdf(0.0), 0.5, places=4)
        self.assertGreater(_normal_cdf(1.96), 0.97)
        self.assertLess(_normal_cdf(-1.96), 0.03)

    def test_skewness_and_kurtosis_calculation(self):
        # Right-skewed returns with positive outlier
        returns = [0.01, 0.02, -0.01, 0.005, 0.012, -0.008, 0.08]  # 0.08 is fat right tail
        res = calculate_probability_theory(returns, velocity=0.5, acceleration=0.2)
        self.assertTrue(res["valid"])
        self.assertGreater(res["skewness"], 0.0, "Positive outlier must induce positive skewness")
        self.assertGreater(res["kurtosis"], 0.0, "Outlier must induce positive excess kurtosis")
        self.assertGreater(res["continuation_prob_pct"], 50.0)
        self.assertGreater(res["var_95_pct"], 0.0)

    def test_fat_tail_detection(self):
        # Extreme fat tail shock: small variance background with large shock outlier
        shock_returns = [0.001, -0.001, 0.002, -0.002, 0.001, 0.002, -0.001, 0.15]
        res = calculate_probability_theory(shock_returns)
        self.assertTrue(res["valid"])
        self.assertTrue(res["is_fat_tail"], f"Kurtosis {res.get('kurtosis')} should trigger fat tail")


class MultiTimeframeIntegrationTest(unittest.TestCase):
    """Test 15M, 1H, 4H confluence and OKX reverse candle order handling."""

    def test_okx_order_inversion(self):
        chronological = [[str(i), "101", "99", str(100.0 + i), "10"] for i in range(10)]
        okx_payload = list(reversed(chronological))

        res = calculate_multi_timeframe({
            "15M": okx_payload,
            "1H": okx_payload,
            "4H": okx_payload,
        })
        self.assertTrue(res["valid"])
        self.assertGreater(res["velocity"], 0.0)
        self.assertIn("15M", res["timeframes"])
        self.assertTrue(res["timeframes"]["15M"]["valid"])
        self.assertIn("definite_integrals", res)
        self.assertIn("probability_theory", res)
        self.assertEqual(res["definite_integrals"]["regime"], "POSITIVE_ENERGY_EXPANSION")
        self.assertIn(res["probability_theory"]["regime"], {
            "HIGH_PROB_BULL_CONTINUATION", "POSITIVE_SKEW_UPSIDE", "NEGATIVE_SKEW_DOWNSIDE", "EXTREME_FAT_TAIL_RISK",
        })

    def test_aggregate_regime_classifiers_prioritise_risk(self):
        self.assertEqual(classify_integral_regime(1.2, 0.8), "POSITIVE_ENERGY_EXPANSION")
        self.assertEqual(classify_integral_regime(0.2, 3.0), "OVERSTRETCHED_MEAN_REVERSION")
        self.assertEqual(
            classify_probability_regime(-0.8, 1.0, 78.0, 22.0, False),
            "NEGATIVE_SKEW_DOWNSIDE",
        )
        self.assertEqual(
            classify_probability_regime(0.1, 0.2, 78.0, 22.0, False),
            "HIGH_PROB_BULL_CONTINUATION",
        )


if __name__ == "__main__":
    unittest.main()
