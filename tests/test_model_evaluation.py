from __future__ import annotations

import unittest
from math import isfinite, sin
from datetime import date, timedelta
from unittest.mock import patch

from analysis import evaluation
from analysis.evaluation import evaluate_walk_forward
from analysis.ml_model import (
    ALGORITHMS,
    DEFAULT_COMMISSION_RATE,
    DEFAULT_SLIPPAGE_RATE,
    DEFAULT_STAMP_DUTY_RATE,
    MlPrediction,
    _build_features,
    _build_samples,
    _choose_neighbor_count,
    _distance,
    _standardize,
    calculate_forward_outcome,
    predict_next_day_buy_probability,
    prediction_horizon_days,
    prediction_target_return,
)
from data.models import PriceVolumeBar


class ModelOutcomeTest(unittest.TestCase):
    def test_forward_outcome_uses_next_open_and_all_trading_costs(self) -> None:
        bars = [
            _bar(0, open_price=99.0, high=101.0, low=98.0, close=100.0),
            _bar(1, open_price=110.0, high=111.0, low=104.0, close=105.0),
            _bar(2, open_price=105.0, high=106.0, low=104.0, close=105.0),
        ]

        outcome = calculate_forward_outcome(bars, 0, 2, min_return=0.0)

        expected_entry = 110.0 * (1 + DEFAULT_SLIPPAGE_RATE)
        expected_exit = 105.0 * (1 - DEFAULT_SLIPPAGE_RATE)
        expected_return = (
            expected_exit * (1 - DEFAULT_COMMISSION_RATE - DEFAULT_STAMP_DUTY_RATE)
            / (expected_entry * (1 + DEFAULT_COMMISSION_RATE))
            - 1
        )
        self.assertAlmostEqual(outcome.entry_price, expected_entry)
        self.assertAlmostEqual(outcome.exit_price, expected_exit)
        self.assertAlmostEqual(outcome.net_return, expected_return)
        self.assertAlmostEqual(outcome.max_drawdown, 104.0 / expected_entry - 1)
        self.assertEqual(outcome.label, 0)

    def test_forward_label_does_not_count_untradeable_overnight_gain(self) -> None:
        bars = [
            _bar(0, open_price=99.0, high=101.0, low=98.0, close=100.0),
            _bar(1, open_price=110.0, high=111.0, low=104.0, close=105.0),
            _bar(2, open_price=105.0, high=106.0, low=104.0, close=105.0),
        ]

        outcome = calculate_forward_outcome(bars, 0, 2, min_return=0.0)

        self.assertGreater(bars[1].close / bars[0].close - 1, 0)
        self.assertLess(outcome.net_return, 0)
        self.assertEqual(outcome.label, 0)

    def test_forward_outcome_rejects_zero_volume_execution_day(self) -> None:
        bars = [
            _bar(0, open_price=10.0, high=10.2, low=9.8, close=10.0),
            _bar(1, open_price=10.0, high=10.2, low=9.8, close=10.1, volume=0),
            _bar(2, open_price=10.0, high=10.2, low=9.8, close=10.1),
        ]

        outcome = calculate_forward_outcome(bars, 0, 2, min_return=0.0)

        self.assertIsNone(outcome)

    def test_forward_outcome_rejects_locked_limit_up_entry(self) -> None:
        bars = [
            _bar(0, open_price=10.0, high=10.1, low=9.9, close=10.0),
            _bar(1, open_price=11.0, high=11.0, low=11.0, close=11.0),
            _bar(2, open_price=11.0, high=11.2, low=10.8, close=11.0),
        ]

        outcome = calculate_forward_outcome(bars, 0, 2, min_return=0.0)

        self.assertIsNone(outcome)

    def test_forward_outcome_rejects_locked_limit_down_exit(self) -> None:
        bars = [
            _bar(0, open_price=10.0, high=10.1, low=9.9, close=10.0),
            _bar(1, open_price=10.0, high=10.2, low=9.8, close=10.0),
            _bar(2, open_price=9.0, high=9.0, low=9.0, close=9.0),
        ]

        outcome = calculate_forward_outcome(bars, 0, 2, min_return=0.0)

        self.assertIsNone(outcome)

    def test_forward_outcome_bounds_slippage_to_daily_range(self) -> None:
        bars = [
            _bar(0, open_price=10.0, high=10.2, low=9.8, close=10.0),
            _bar(1, open_price=10.0, high=10.01, low=9.9, close=10.0),
            _bar(2, open_price=10.0, high=10.01, low=9.9, close=10.0),
        ]

        outcome = calculate_forward_outcome(
            bars,
            0,
            2,
            min_return=0.0,
            slippage_rate=0.10,
        )

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.entry_price, 10.01)
        self.assertEqual(outcome.exit_price, 9.9)

    def test_weighted_knn_positive_count_is_actual_neighbor_count(self) -> None:
        bars = _build_bars(100)
        samples = _build_samples(bars, 2, 0.0)
        predict_features = _build_features(bars, len(bars) - 1)
        normalized_samples, normalized_predict = _standardize(samples, predict_features)
        neighbor_count = _choose_neighbor_count(len(samples))
        neighbors = sorted(
            normalized_samples,
            key=lambda sample: _distance(sample[0], normalized_predict),
        )[:neighbor_count]

        prediction = predict_next_day_buy_probability(bars, "weighted_knn")

        self.assertIsNotNone(prediction)
        self.assertEqual(prediction.positive_count, sum(label for _, label, _ in neighbors))
        self.assertLessEqual(prediction.positive_count, prediction.neighbor_count)

    def test_logistic_regression_has_no_neighbor_positive_count(self) -> None:
        prediction = predict_next_day_buy_probability(_build_bars(100), "logistic_regression")

        self.assertIsNotNone(prediction)
        self.assertEqual(prediction.neighbor_count, 0)
        self.assertEqual(prediction.positive_count, 0)

    def test_a_share_labels_cannot_sell_on_buy_day(self) -> None:
        self.assertEqual(prediction_horizon_days("knn"), 2)
        with self.assertRaisesRegex(ValueError, "T\\+1"):
            calculate_forward_outcome(_build_bars(5), 0, 1, 0)


class ModelChoiceScenarios(unittest.TestCase):
    algorithms = ("random_forest", "extra_trees", "gradient_boosting", "gaussian_nb")

    def test_new_algorithms_are_real_deterministic_classifiers_with_shared_labels(self):
        """Given 含涨跌两类历史样本 When 分别选择新增算法 Then 真正执行所选模型且结果可复现、标签口径相同"""
        bars = [_bar(i, price * .998, price * 1.01, price * .99, price, 1000 + i % 7 * 40)
                for i in range(180) for price in [10 + sin(i / 5) * 1.2 + i * .005]]
        for algorithm in self.algorithms:
            with self.subTest(algorithm=algorithm):
                self.assertIn(algorithm, ALGORITHMS)
                self.assertEqual(prediction_horizon_days(algorithm), 5)
                self.assertEqual(prediction_target_return(algorithm), .012)
                prediction = predict_next_day_buy_probability(bars, algorithm)
                self.assertIsNotNone(prediction)
                self.assertEqual(prediction.algorithm, algorithm)
                self.assertEqual(prediction.algorithm_name, ALGORITHMS[algorithm])
                self.assertEqual(prediction.horizon_days, 5)
                self.assertTrue(isfinite(prediction.buy_probability))
                self.assertTrue(0 <= prediction.buy_probability <= 1)
                self.assertEqual(prediction.neighbor_count, 0)
                self.assertEqual(prediction.positive_count, 0)
                self.assertEqual(prediction, predict_next_day_buy_probability(bars, algorithm))

    def test_new_models_do_not_invent_probabilities_for_single_or_rare_classes(self):
        """Given 标签只有一类或少数类不足五例 When 训练新模型 Then 返回样本不足而非虚构高胜率或替换算法"""
        for algorithm in self.algorithms:
            for labels in ([0] * 60, [1] * 60, [0] * 56 + [1] * 4):
                with self.subTest(algorithm=algorithm, positives=sum(labels)), \
                     patch("analysis.ml_model._build_samples", return_value=[
                         ([float(i), 1.0], label, i + 20) for i, label in enumerate(labels)
                     ]):
                    self.assertIsNone(predict_next_day_buy_probability(_build_bars(100), algorithm))

    def test_unknown_algorithm_is_rejected_instead_of_silently_using_knn(self):
        """Given 未知算法代码 When 请求预测 Then 明确拒绝而不静默执行KNN"""
        with self.assertRaisesRegex(ValueError, "算法"):
            predict_next_day_buy_probability(_build_bars(100), "unknown_model")

    def test_preprocessing_fits_only_completed_training_samples(self):
        """Given 当前预测特征极端且未出现在训练集 When 朴素贝叶斯标准化 Then 只用已完成标签的训练样本拟合"""
        from analysis.ml_model import _predict_with_sklearn
        from sklearn.preprocessing import StandardScaler
        samples = [([float(i), 1.0], i % 2, i + 20) for i in range(40)]
        observed = []
        original = StandardScaler.fit

        def record_fit(scaler, values, *args, **kwargs):
            observed.append([list(row) for row in values])
            return original(scaler, values, *args, **kwargs)

        with patch.object(StandardScaler, "fit", record_fit):
            _predict_with_sklearn(samples, [9999.0, 1.0], "gaussian_nb", 5)
        self.assertEqual(observed, [[features for features, _, _ in samples]])

    def test_latest_training_label_ends_before_or_at_analysis_time(self):
        """Given 截至某日的行情 When 新模型构造训练集 Then 所有标签已在该时点完成，不使用未来行情"""
        bars = _build_bars(100)
        for algorithm in self.algorithms:
            with self.subTest(algorithm=algorithm), \
                 patch("analysis.ml_model._predict_with_sklearn", return_value=None) as predict:
                predict_next_day_buy_probability(bars, algorithm)
                samples, _, selected, horizon = predict.call_args.args
                self.assertEqual(selected, algorithm)
                self.assertTrue(all(index + horizon < len(bars) for _, _, index in samples))
                self.assertEqual(samples, _build_samples(bars, 5, .012))

    def test_new_models_work_with_existing_walk_forward_research_gate(self):
        """Given 新模型的时间顺序回放 When 用既有样本外引擎评估 Then 可产出指标但仍仅限研究"""
        bars = [_bar(i, price * .998, price * 1.01, price * .99, price, 1000 + i % 7 * 40)
                for i in range(120) for price in [10 + sin(i / 5) * 1.2 + i * .005]]
        for algorithm in self.algorithms:
            with self.subTest(algorithm=algorithm):
                result = evaluate_walk_forward(bars, algorithm, thresholds=(.2,),
                    min_validation_samples=2, min_test_samples=2, min_validation_trades=1)
                self.assertEqual(result.algorithm, algorithm)
                self.assertEqual(result.horizon_days, 5)
                self.assertLess(result.validation_end_date, result.test_start_date)
                self.assertTrue(isfinite(result.test.brier_score))
                self.assertIn(result.release_status, {"RESEARCH_ONLY", "NOT_READY", "FAILED_OOS"})


class WalkForwardEvaluationTest(unittest.TestCase):
    def test_five_day_evaluation_uses_non_overlapping_signal_step(self) -> None:
        bars = _build_bars(100)
        history_lengths = []

        def fake_predict(history, algorithm):
            history_lengths.append(len(history))
            return MlPrediction(
                algorithm=algorithm,
                algorithm_name="测试模型",
                buy_probability=0.6,
                sample_count=20,
                positive_count=3,
                neighbor_count=7,
                horizon_days=5,
            )

        with patch("analysis.evaluation.predict_next_day_buy_probability", side_effect=fake_predict):
            result = evaluate_walk_forward(
                bars,
                "robust_ensemble",
                thresholds=(0.5,),
                min_validation_samples=5,
                min_test_samples=5,
                min_validation_trades=1,
            )

        self.assertTrue(history_lengths)
        self.assertEqual(history_lengths[0], 45)
        self.assertTrue(
            all(right - left == 5 for left, right in zip(history_lengths, history_lengths[1:]))
        )
        self.assertEqual(
            result.validation.sample_count + result.test.sample_count,
            len(history_lengths),
        )

    def test_validation_selects_threshold_and_test_uses_frozen_value(self) -> None:
        points = [
            evaluation._WalkForwardPoint("2026-01-01", 0.80, 1, 0.10),
            evaluation._WalkForwardPoint("2026-01-02", 0.75, 0, -0.02),
            evaluation._WalkForwardPoint("2026-01-03", 0.60, 0, -0.05),
            evaluation._WalkForwardPoint("2026-01-04", 0.40, 1, 0.03),
            evaluation._WalkForwardPoint("2026-01-05", 0.80, 1, 0.02),
            evaluation._WalkForwardPoint("2026-01-06", 0.65, 1, 0.05),
            evaluation._WalkForwardPoint("2026-01-07", 0.72, 0, -0.01),
            evaluation._WalkForwardPoint("2026-01-08", 0.30, 1, 0.01),
        ]

        with patch("analysis.evaluation._collect_walk_forward_points", return_value=points):
            result = evaluate_walk_forward(
                [_bar(0, 10.0, 10.2, 9.8, 10.0)],
                "robust_ensemble",
                validation_ratio=0.5,
                thresholds=(0.5, 0.7),
                min_validation_samples=4,
                min_test_samples=4,
                min_validation_trades=1,
            )

        self.assertEqual(result.threshold, 0.7)
        self.assertEqual(result.test.sample_count, 4)
        self.assertEqual(result.test.selected_count, 2)
        self.assertAlmostEqual(result.test.precision, 0.5)
        self.assertAlmostEqual(result.test.coverage, 0.5)
        self.assertAlmostEqual(result.test.average_net_return, 0.005)
        self.assertAlmostEqual(result.test.brier_score, 0.292725)
        self.assertAlmostEqual(result.test.base_rate, 0.75)
        self.assertAlmostEqual(result.test.lift, 2 / 3)
        self.assertLess(result.test.precision_ci_low, result.test.precision)
        self.assertGreater(result.test.precision_ci_high, result.test.precision)
        self.assertEqual(result.release_status, "NOT_READY")
        self.assertIn("仅入选 2 笔", result.release_reason)

    def test_sufficient_test_trades_remain_research_only(self) -> None:
        points = []
        for index in range(8):
            selected = index % 2 == 0
            points.append(
                evaluation._WalkForwardPoint(
                    f"2026-01-{index + 1:02d}",
                    0.8 if selected else 0.2,
                    1 if selected else 0,
                    0.02 if selected else -0.01,
                )
            )

        with patch("analysis.evaluation._collect_walk_forward_points", return_value=points):
            result = evaluate_walk_forward(
                [_bar(0, 10.0, 10.2, 9.8, 10.0)],
                "knn",
                validation_ratio=0.5,
                thresholds=(0.5,),
                min_validation_samples=4,
                min_test_samples=4,
                min_validation_trades=1,
                min_test_trades=2,
            )

        self.assertEqual(result.release_status, "RESEARCH_ONLY")
        self.assertIn("仅供研究", result.release_reason)

    def test_negative_out_of_sample_result_fails_release_gate(self) -> None:
        points = [
            evaluation._WalkForwardPoint(
                f"2026-02-{index + 1:02d}",
                0.8,
                1 if index < 4 else 0,
                0.02 if index < 4 else -0.01,
            )
            for index in range(8)
        ]

        with patch("analysis.evaluation._collect_walk_forward_points", return_value=points):
            result = evaluate_walk_forward(
                [_bar(0, 10.0, 10.2, 9.8, 10.0)],
                "knn",
                validation_ratio=0.5,
                thresholds=(0.5,),
                min_validation_samples=4,
                min_test_samples=4,
                min_validation_trades=1,
                min_test_trades=4,
            )

        self.assertEqual(result.release_status, "FAILED_OOS")
        self.assertIn("平均净收益不为正", result.release_reason)

    def test_insufficient_walk_forward_samples_raise_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "walk-forward 样本不足"):
            evaluate_walk_forward(
                _build_bars(60),
                "robust_ensemble",
                thresholds=(0.5,),
                min_validation_samples=2,
                min_test_samples=2,
                min_validation_trades=1,
            )

    def test_out_of_order_bars_are_rejected(self) -> None:
        bars = _build_bars(60)
        bars[-1], bars[-2] = bars[-2], bars[-1]

        with self.assertRaisesRegex(ValueError, "严格升序"):
            evaluate_walk_forward(
                bars,
                "robust_ensemble",
                min_validation_samples=1,
                min_test_samples=1,
            )


def _build_bars(count: int) -> list[PriceVolumeBar]:
    bars = []
    for index in range(count):
        center = 10 + index * 0.015 + ((index % 9) - 4) * 0.035
        open_price = center * (1 + ((index % 5) - 2) * 0.0015)
        bars.append(
            _bar(
                index,
                open_price=open_price,
                high=max(open_price, center) + 0.08,
                low=min(open_price, center) - 0.08,
                close=center,
                volume=1000 + (index % 7) * 40,
            )
        )
    return bars


def _bar(
    day_index: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1000,
) -> PriceVolumeBar:
    return PriceVolumeBar(
        name="测试股票",
        trade_date=date(2025, 1, 1) + timedelta(days=day_index),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        amount=close * volume,
    )


if __name__ == "__main__":
    unittest.main()
