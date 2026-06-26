from __future__ import annotations

from dataclasses import dataclass
from math import exp, sqrt

from data.models import PriceVolumeBar


@dataclass(frozen=True)
class MlPrediction:
    algorithm: str
    algorithm_name: str
    buy_probability: float
    sample_count: int
    positive_count: int
    neighbor_count: int


ALGORITHMS = {
    "knn": "KNN 相似样本",
    "weighted_knn": "加权 KNN 相似样本",
    "logistic_regression": "逻辑回归",
}


def predict_next_day_buy_probability(
    bars: list[PriceVolumeBar],
    algorithm: str = "knn",
) -> MlPrediction | None:
    if len(bars) < 45:
        return None

    samples: list[tuple[list[float], int]] = []
    for index in range(20, len(bars) - 1):
        features = _build_features(bars, index)
        label = 1 if bars[index + 1].close > bars[index].close else 0
        samples.append((features, label))

    if len(samples) < 20:
        return None

    predict_features = _build_features(bars, len(bars) - 1)
    if algorithm == "logistic_regression":
        return _predict_with_logistic(samples, predict_features)

    normalized_samples, normalized_predict = _standardize(samples, predict_features)
    neighbor_count = _choose_neighbor_count(len(normalized_samples))
    neighbors = sorted(
        normalized_samples,
        key=lambda sample: _distance(sample[0], normalized_predict),
    )[:neighbor_count]
    if algorithm == "weighted_knn":
        probability = _weighted_probability(neighbors, normalized_predict)
        positive_count = round(probability * neighbor_count)
    else:
        algorithm = "knn"
        positive_count = sum(label for _, label in neighbors)
        probability = positive_count / neighbor_count

    return MlPrediction(
        algorithm=algorithm,
        algorithm_name=ALGORITHMS[algorithm],
        buy_probability=probability,
        sample_count=len(samples),
        positive_count=positive_count,
        neighbor_count=neighbor_count,
    )


def _predict_with_logistic(
    samples: list[tuple[list[float], int]],
    predict_features: list[float],
) -> MlPrediction:
    normalized_samples, normalized_predict = _standardize(samples, predict_features)
    weights = [0.0 for _ in normalized_predict]
    bias = 0.0
    learning_rate = 0.08
    for _ in range(180):
        bias_gradient = 0.0
        weight_gradients = [0.0 for _ in weights]
        for features, label in normalized_samples:
            probability = _sigmoid(_dot(weights, features) + bias)
            error = probability - label
            bias_gradient += error
            for index, value in enumerate(features):
                weight_gradients[index] += error * value
        scale = 1 / len(normalized_samples)
        bias -= learning_rate * bias_gradient * scale
        for index in range(len(weights)):
            weights[index] -= learning_rate * weight_gradients[index] * scale

    probability = _sigmoid(_dot(weights, normalized_predict) + bias)
    return MlPrediction(
        algorithm="logistic_regression",
        algorithm_name=ALGORITHMS["logistic_regression"],
        buy_probability=probability,
        sample_count=len(samples),
        positive_count=round(probability * len(samples)),
        neighbor_count=0,
    )


def _weighted_probability(
    neighbors: list[tuple[list[float], int]],
    predict_features: list[float],
) -> float:
    weighted_sum = 0.0
    total_weight = 0.0
    for features, label in neighbors:
        weight = 1 / (_distance(features, predict_features) + 0.000001)
        weighted_sum += weight * label
        total_weight += weight
    if total_weight == 0:
        return 0.0
    return weighted_sum / total_weight


def _choose_neighbor_count(sample_count: int) -> int:
    neighbor_count = round(sqrt(sample_count))
    neighbor_count = max(7, min(21, neighbor_count))
    if neighbor_count % 2 == 0:
        neighbor_count += 1
    return min(neighbor_count, sample_count)


def _build_features(bars: list[PriceVolumeBar], index: int) -> list[float]:
    window = bars[index - 20 : index + 1]
    current = window[-1]
    previous = window[-2]
    closes = [bar.close for bar in window]
    highs = [bar.high for bar in window]
    volumes = [bar.volume for bar in window]

    ma5 = _average(closes[-5:])
    ma20 = _average(closes)
    ma20_prev = _average(closes[:-1])
    volume_ma5 = _average(volumes[-6:-1])
    volume_ma20 = _average(volumes[:-1])
    high_20d_prev = max(highs[:-1])

    return [
        _return_rate(current.close, previous.close),
        _return_rate(current.close, closes[-6]),
        _return_rate(current.close, closes[0]),
        _safe_ratio(current.close - ma5, ma5),
        _safe_ratio(current.close - ma20, ma20),
        _safe_ratio(ma20 - ma20_prev, ma20_prev),
        _safe_ratio(current.volume, volume_ma5),
        _safe_ratio(current.volume, volume_ma20),
        _safe_ratio(current.close - high_20d_prev, high_20d_prev),
    ]


def _standardize(
    samples: list[tuple[list[float], int]],
    predict_features: list[float],
) -> tuple[list[tuple[list[float], int]], list[float]]:
    feature_count = len(predict_features)
    means = []
    stds = []
    for feature_index in range(feature_count):
        values = [features[feature_index] for features, _ in samples]
        mean = _average(values)
        variance = _average([(value - mean) ** 2 for value in values])
        std = sqrt(variance) or 1.0
        means.append(mean)
        stds.append(std)

    normalized_samples = [
        ([_normalize(value, means[index], stds[index]) for index, value in enumerate(features)], label)
        for features, label in samples
    ]
    normalized_predict = [
        _normalize(value, means[index], stds[index])
        for index, value in enumerate(predict_features)
    ]
    return normalized_samples, normalized_predict


def _distance(left: list[float], right: list[float]) -> float:
    return sqrt(sum((left[index] - right[index]) ** 2 for index in range(len(left))))


def _normalize(value: float, mean: float, std: float) -> float:
    return (value - mean) / std


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + exp(-value))
    exp_value = exp(value)
    return exp_value / (1 + exp_value)


def _dot(left: list[float], right: list[float]) -> float:
    return sum(left[index] * right[index] for index in range(len(left)))


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def _return_rate(current: float, previous: float) -> float:
    return _safe_ratio(current - previous, previous)


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
