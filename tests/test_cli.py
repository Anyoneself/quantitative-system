from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import cli
from analysis.evaluation import EvaluationMetrics, WalkForwardEvaluation
from data.errors import DataRefreshError


class CliTest(unittest.TestCase):
    def test_backtest_parser_uses_current_stamp_duty_and_default_liquidation(self) -> None:
        args = cli._build_parser().parse_args(["backtest", "600519"])

        self.assertEqual(args.stamp_duty_rate, 0.0005)
        self.assertFalse(args.no_liquidate_at_end)

    def test_backtest_no_liquidation_flag_reaches_config(self) -> None:
        captured = {}

        def fake_run_backtest(symbol, bars, algorithm, config):
            captured["symbol"] = symbol
            captured["config"] = config
            return object()

        output = io.StringIO()
        with patch("cli.fetch_bars", return_value=[]):
            with patch("cli.run_backtest", side_effect=fake_run_backtest):
                with patch("cli.format_backtest_result", return_value="完成"):
                    with redirect_stdout(output):
                        exit_code = cli.main(["backtest", "600519", "--no-liquidate-at-end"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue().strip(), "完成")
        self.assertEqual(captured["symbol"], "600519")
        self.assertFalse(captured["config"].liquidate_at_end)
        self.assertEqual(captured["config"].stamp_duty_rate, 0.0005)

    def test_evaluate_text_outputs_frozen_threshold_and_out_of_sample_metrics(self) -> None:
        result = _evaluation_result()
        output = io.StringIO()

        with patch("cli.fetch_bars", return_value=[]):
            with patch("cli.evaluate_walk_forward", return_value=result) as evaluate:
                with redirect_stdout(output):
                    exit_code = cli.main(["evaluate", "600519", "--algorithm", "knn"])

        text = output.getvalue()
        self.assertEqual(exit_code, 0)
        evaluate.assert_called_once_with([], "knn")
        self.assertIn("验证集冻结阈值：60.00%", text)
        self.assertIn("样本外 Precision：66.67%", text)
        self.assertIn("样本外 Coverage：30.00%", text)
        self.assertIn("样本外平均净收益：1.50%", text)
        self.assertIn("样本外 Brier 分数：0.180000", text)
        self.assertIn("样本外样本数：20", text)
        self.assertIn("样本外达标率 Lift：", text)
        self.assertNotIn("胜率提升倍数", text)
        self.assertIn("发布状态：RESEARCH_ONLY", text)

    def test_evaluate_json_is_structured_and_contains_symbol(self) -> None:
        output = io.StringIO()

        with patch("cli.fetch_bars", return_value=[]):
            with patch("cli.evaluate_walk_forward", return_value=_evaluation_result()):
                with redirect_stdout(output):
                    exit_code = cli.main(["evaluate", "600519", "--format", "json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["symbol"], "600519")
        self.assertEqual(payload["threshold"], 0.60)
        self.assertEqual(payload["test"]["sample_count"], 20)
        self.assertEqual(payload["test"]["precision"], 2 / 3)

    def test_evaluate_rejects_invalid_symbol_without_fetching(self) -> None:
        error = io.StringIO()

        with patch("cli.fetch_bars") as fetch:
            with redirect_stderr(error):
                exit_code = cli.main(["evaluate", "abc"])

        self.assertEqual(exit_code, 2)
        self.assertIn("股票代码格式不正确", error.getvalue())
        fetch.assert_not_called()

    def test_evaluate_reports_data_or_validation_failure(self) -> None:
        error = io.StringIO()

        with patch("cli.fetch_bars", side_effect=DataRefreshError("网络不可用")):
            with redirect_stderr(error):
                exit_code = cli.main(["evaluate", "600519"])

        self.assertEqual(exit_code, 1)
        self.assertIn("评估失败：网络不可用", error.getvalue())


def _evaluation_result() -> WalkForwardEvaluation:
    validation = EvaluationMetrics(
        precision=0.75,
        coverage=0.40,
        average_net_return=0.02,
        brier_score=0.16,
        sample_count=20,
        selected_count=8,
        positive_count=6,
    )
    test = EvaluationMetrics(
        precision=2 / 3,
        coverage=0.30,
        average_net_return=0.015,
        brier_score=0.18,
        sample_count=20,
        selected_count=6,
        positive_count=4,
    )
    return WalkForwardEvaluation(
        algorithm="knn",
        horizon_days=1,
        threshold=0.60,
        validation_start_date="2025-01-01",
        validation_end_date="2025-03-31",
        test_start_date="2025-04-01",
        test_end_date="2025-06-30",
        validation=validation,
        test=test,
    )


if __name__ == "__main__":
    unittest.main()
