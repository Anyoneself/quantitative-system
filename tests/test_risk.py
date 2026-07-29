from __future__ import annotations

import unittest

from portfolio.risk import RiskThresholds, calculate_risk


class RiskTest(unittest.TestCase):
    def test_empty_samples_return_none_and_reasons(self) -> None:
        result = calculate_risk([])

        self.assertIsNone(result.annualized_volatility)
        self.assertIsNone(result.sharpe_ratio)
        self.assertIsNone(result.beta)
        self.assertIsNone(result.var_95)
        self.assertIsNone(result.win_rate)
        self.assertIsNone(result.profit_loss_ratio)
        self.assertEqual(result.risk_level, "NO_DATA")
        self.assertTrue(result.reasons)

    def test_metrics_are_calculated_for_valid_samples(self) -> None:
        benchmark = [value / 2 for value in _returns()]
        result = calculate_risk(
            _returns(),
            benchmark_returns=benchmark,
            realized_pnls=[100, -50, 200, -100],
            max_drawdown=-0.08,
        )

        self.assertIsNotNone(result.annualized_volatility)
        self.assertIsNotNone(result.sharpe_ratio)
        self.assertAlmostEqual(result.beta, 2.0)
        self.assertGreaterEqual(result.var_95, 0)
        self.assertEqual(result.win_rate, 0.5)
        self.assertAlmostEqual(result.profit_loss_ratio, 2.0)
        self.assertEqual(result.risk_level, "WARNING")

    def test_zero_benchmark_variance_returns_no_beta(self) -> None:
        result = calculate_risk(
            _returns(),
            benchmark_returns=[0.0] * 20,
        )

        self.assertIsNone(result.beta)
        self.assertTrue(any("Beta" in reason for reason in result.reasons))

    def test_thresholds_produce_high_risk(self) -> None:
        result = calculate_risk(
            _returns(),
            max_drawdown=-0.26,
            thresholds=RiskThresholds(),
        )

        self.assertEqual(result.risk_level, "HIGH")


def _returns() -> list[float]:
    return [
        -0.04,
        0.01,
        0.015,
        -0.005,
        0.012,
        -0.008,
        0.009,
        -0.006,
        0.011,
        -0.004,
        0.008,
        -0.007,
        0.010,
        -0.003,
        0.006,
        -0.002,
        0.005,
        -0.001,
        0.004,
        0.003,
    ]


if __name__ == "__main__":
    unittest.main()
