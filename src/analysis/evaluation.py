from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from analysis.ml_model import (
    ALGORITHMS,
    calculate_forward_outcome,
    predict_next_day_buy_probability,
    prediction_horizon_days,
    prediction_target_return,
)
from data.models import PriceVolumeBar


DEFAULT_THRESHOLDS = (
    0.20,
    0.25,
    0.30,
    0.35,
    0.40,
    0.45,
    0.50,
    0.55,
    0.60,
    0.65,
    0.70,
)
DEFAULT_MIN_TEST_TRADES = 5


@dataclass(frozen=True)
class EvaluationMetrics:
    precision: float | None
    coverage: float
    average_net_return: float | None
    brier_score: float
    sample_count: int
    selected_count: int
    positive_count: int
    base_rate: float | None = None
    lift: float | None = None
    precision_ci_low: float | None = None
    precision_ci_high: float | None = None


@dataclass(frozen=True)
class WalkForwardEvaluation:
    algorithm: str
    horizon_days: int
    threshold: float
    validation_start_date: str
    validation_end_date: str
    test_start_date: str
    test_end_date: str
    validation: EvaluationMetrics
    test: EvaluationMetrics
    release_status: str = "RESEARCH_ONLY"
    release_reason: str = "样本外评估仅供研究，不构成可交易结论。"


@dataclass(frozen=True)
class _WalkForwardPoint:
    signal_date: str
    probability: float
    label: int
    net_return: float


def evaluate_walk_forward(
    bars: list[PriceVolumeBar],
    algorithm: str = "robust_ensemble",
    validation_ratio: float = 0.5,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
    min_validation_samples: int = 20,
    min_test_samples: int = 20,
    min_validation_trades: int = 5,
    min_test_trades: int = DEFAULT_MIN_TEST_TRADES,
) -> WalkForwardEvaluation:
    if algorithm not in ALGORITHMS:
        raise ValueError(f"不支持的算法：{algorithm}")
    if not 0 < validation_ratio < 1:
        raise ValueError("验证集比例必须在 0 到 1 之间。")
    if (
        min_validation_samples < 1
        or min_test_samples < 1
        or min_validation_trades < 1
        or min_test_trades < 1
    ):
        raise ValueError("最小样本数和最小交易数必须大于 0。")
    _validate_thresholds(thresholds)
    _validate_bar_order(bars)

    horizon_days = prediction_horizon_days(algorithm)
    points = _collect_walk_forward_points(bars, algorithm, horizon_days)
    split_index = int(len(points) * validation_ratio)
    validation_points = points[:split_index]
    test_points = points[split_index:]
    if len(validation_points) < min_validation_samples or len(test_points) < min_test_samples:
        raise ValueError(
            "walk-forward 样本不足："
            f"验证段 {len(validation_points)} 个，测试段 {len(test_points)} 个；"
            f"至少分别需要 {min_validation_samples} 和 {min_test_samples} 个。"
        )

    threshold, validation_metrics = _select_threshold(
        validation_points,
        thresholds,
        min_validation_trades,
    )
    test_metrics = _build_metrics(test_points, threshold)
    release_status, release_reason = _release_state(
        test_metrics,
        min_test_trades,
    )
    return WalkForwardEvaluation(
        algorithm=algorithm,
        horizon_days=horizon_days,
        threshold=threshold,
        validation_start_date=validation_points[0].signal_date,
        validation_end_date=validation_points[-1].signal_date,
        test_start_date=test_points[0].signal_date,
        test_end_date=test_points[-1].signal_date,
        validation=validation_metrics,
        test=test_metrics,
        release_status=release_status,
        release_reason=release_reason,
    )


def _collect_walk_forward_points(
    bars: list[PriceVolumeBar],
    algorithm: str,
    horizon_days: int,
) -> list[_WalkForwardPoint]:
    points = []
    target_return = prediction_target_return(algorithm)
    # 历史模型仍按日滚动训练；样本外信号按持有期步长抽样，避免相邻评估交易重叠。
    for signal_index in range(44, len(bars) - horizon_days, horizon_days):
        history = bars[: signal_index + 1]
        prediction = predict_next_day_buy_probability(history, algorithm)
        if prediction is None:
            continue
        outcome = calculate_forward_outcome(
            bars,
            signal_index,
            horizon_days,
            target_return,
        )
        if outcome is None:
            continue
        points.append(
            _WalkForwardPoint(
                signal_date=bars[signal_index].trade_date.isoformat(),
                probability=prediction.buy_probability,
                label=outcome.label,
                net_return=outcome.net_return,
            )
        )
    return points


def _release_state(
    test_metrics: EvaluationMetrics,
    min_test_trades: int,
) -> tuple[str, str]:
    if test_metrics.selected_count < min_test_trades:
        return (
            "NOT_READY",
            f"测试段仅入选 {test_metrics.selected_count} 笔，少于最低 "
            f"{min_test_trades} 笔，不可用于发布或交易决策。",
        )
    if (
        test_metrics.average_net_return is None
        or test_metrics.average_net_return <= 0
    ):
        return (
            "FAILED_OOS",
            "测试段入选信号的成本后平均净收益不为正，样本外验收未通过。",
        )
    if test_metrics.lift is None or test_metrics.lift <= 1:
        return (
            "FAILED_OOS",
            "测试段入选信号的达标率未高于基准达标率，样本外验收未通过。",
        )
    return "RESEARCH_ONLY", "样本外评估仅供研究，不构成可交易结论。"


def _select_threshold(
    points: list[_WalkForwardPoint],
    thresholds: tuple[float, ...],
    min_validation_trades: int,
) -> tuple[float, EvaluationMetrics]:
    candidates = []
    for threshold in sorted(set(thresholds)):
        metrics = _build_metrics(points, threshold)
        if metrics.selected_count < min_validation_trades:
            continue
        selected_return = (
            metrics.average_net_return * metrics.selected_count
            if metrics.average_net_return is not None
            else 0.0
        )
        expected_return_per_signal = selected_return / metrics.sample_count
        candidates.append(
            (
                expected_return_per_signal,
                metrics.average_net_return,
                metrics.precision,
                metrics.coverage,
                -threshold,
                threshold,
                metrics,
            )
        )
    if not candidates:
        raise ValueError(
            f"验证段没有候选阈值达到至少 {min_validation_trades} 笔交易，无法冻结阈值。"
        )
    best = max(candidates, key=lambda item: item[:5])
    return best[5], best[6]


def _build_metrics(points: list[_WalkForwardPoint], threshold: float) -> EvaluationMetrics:
    selected = [point for point in points if point.probability >= threshold]
    selected_count = len(selected)
    positive_count = sum(point.label for point in selected)
    precision = positive_count / selected_count if selected else None
    average_net_return = (
        sum(point.net_return for point in selected) / selected_count
        if selected
        else None
    )
    base_rate = sum(point.label for point in points) / len(points)
    lift = (
        precision / base_rate
        if precision is not None and base_rate > 0
        else None
    )
    precision_ci_low, precision_ci_high = _wilson_interval(
        positive_count,
        selected_count,
    )
    brier_score = sum(
        (point.probability - point.label) ** 2
        for point in points
    ) / len(points)
    return EvaluationMetrics(
        precision=precision,
        coverage=selected_count / len(points),
        average_net_return=average_net_return,
        brier_score=brier_score,
        sample_count=len(points),
        selected_count=selected_count,
        positive_count=positive_count,
        base_rate=base_rate,
        lift=lift,
        precision_ci_low=precision_ci_low,
        precision_ci_high=precision_ci_high,
    )


def _wilson_interval(
    positive_count: int,
    sample_count: int,
    z_score: float = 1.96,
) -> tuple[float | None, float | None]:
    if sample_count <= 0:
        return None, None
    proportion = positive_count / sample_count
    denominator = 1 + z_score**2 / sample_count
    center = (
        proportion + z_score**2 / (2 * sample_count)
    ) / denominator
    margin = (
        z_score
        * sqrt(
            proportion * (1 - proportion) / sample_count
            + z_score**2 / (4 * sample_count**2)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _validate_thresholds(thresholds: tuple[float, ...]) -> None:
    if not thresholds:
        raise ValueError("至少需要一个候选阈值。")
    if any(threshold < 0 or threshold > 1 for threshold in thresholds):
        raise ValueError("候选阈值必须在 0 到 1 之间。")


def _validate_bar_order(bars: list[PriceVolumeBar]) -> None:
    if any(left.trade_date >= right.trade_date for left, right in zip(bars, bars[1:])):
        raise ValueError("行情必须按交易日期严格升序排列，且不能包含重复日期。")
