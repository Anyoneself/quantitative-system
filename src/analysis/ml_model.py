from __future__ import annotations

from dataclasses import dataclass
from math import exp, sqrt
from typing import TypeAlias

from data.models import PriceVolumeBar


Sample: TypeAlias = tuple[list[float], int, int]


@dataclass(frozen=True)
class MlPrediction:
    algorithm: str
    algorithm_name: str
    buy_probability: float
    sample_count: int
    positive_count: int
    neighbor_count: int
    horizon_days: int = 1


ALGORITHMS = {
    "knn": "KNN 相似样本",
    "weighted_knn": "加权 KNN 相似样本",
    "time_decay_knn": "时间衰减 KNN（5日）",
    "robust_ensemble": "稳健集成模型（5日）",
    "logistic_regression": "逻辑回归",
}


def predict_next_day_buy_probability(
    bars: list[PriceVolumeBar],
    algorithm: str = "knn",
) -> MlPrediction | None:
    if len(bars) < 45:
        return None

    horizon_days = 5 if algorithm in {"robust_ensemble", "time_decay_knn"} else 1
    min_return = 0.012 if horizon_days > 1 else 0.0
    samples = _build_samples(bars, horizon_days, min_return)

    if len(samples) < 20:
        return None

    predict_features = _build_features(bars, len(bars) - 1)
    if algorithm == "logistic_regression":
        return _predict_with_logistic(samples, predict_features, horizon_days)

    normalized_samples, normalized_predict = _standardize(samples, predict_features)
    neighbor_count = _choose_neighbor_count(len(normalized_samples))
    neighbors = sorted(
        normalized_samples,
        key=lambda sample: _distance(sample[0], normalized_predict),
    )[:neighbor_count]
    base_rate = _base_rate(samples)
    if algorithm == "robust_ensemble":
        probability = _predict_with_robust_ensemble(bars, samples, predict_features, normalized_predict, neighbors)
        positive_count = round(probability * neighbor_count)
    elif algorithm == "time_decay_knn":
        probability = _weighted_probability(neighbors, normalized_predict, use_time_decay=True)
        probability = _shrink_probability(probability, base_rate, len(samples), strength=90)
        positive_count = round(probability * neighbor_count)
    elif algorithm == "weighted_knn":
        probability = _weighted_probability(neighbors, normalized_predict, use_time_decay=False)
        probability = _shrink_probability(probability, base_rate, len(samples), strength=60)
        positive_count = round(probability * neighbor_count)
    else:
        algorithm = "knn"
        positive_count = sum(label for _, label, _ in neighbors)
        probability = positive_count / neighbor_count
        probability = _shrink_probability(probability, base_rate, len(samples), strength=50)

    return MlPrediction(
        algorithm=algorithm,
        algorithm_name=ALGORITHMS[algorithm],
        buy_probability=probability,
        sample_count=len(samples),
        positive_count=positive_count,
        neighbor_count=neighbor_count,
        horizon_days=horizon_days,
    )


def _build_samples(bars: list[PriceVolumeBar], horizon_days: int, min_return: float) -> list[Sample]:
    samples: list[Sample] = []
    for index in range(20, len(bars) - horizon_days):
        features = _build_features(bars, index)
        current_close = bars[index].close
        future_close = bars[index + horizon_days].close
        future_low = min(bar.low for bar in bars[index + 1 : index + horizon_days + 1])
        forward_return = _return_rate(future_close, current_close)
        forward_drawdown = _return_rate(future_low, current_close)
        label = 1 if forward_return >= min_return and forward_drawdown > -0.06 else 0
        samples.append((features, label, index))
    return samples


def _predict_with_logistic(
    samples: list[Sample],
    predict_features: list[float],
    horizon_days: int,
) -> MlPrediction:
    probability = _logistic_probability(samples, predict_features)
    probability = _shrink_probability(probability, _base_rate(samples), len(samples), strength=75)
    return MlPrediction(
        algorithm="logistic_regression",
        algorithm_name=ALGORITHMS["logistic_regression"],
        buy_probability=probability,
        sample_count=len(samples),
        positive_count=round(probability * len(samples)),
        neighbor_count=0,
        horizon_days=horizon_days,
    )


def _predict_with_robust_ensemble(
    bars: list[PriceVolumeBar],
    samples: list[Sample],
    predict_features: list[float],
    normalized_predict: list[float],
    neighbors: list[Sample],
) -> float:
    base_rate = _base_rate(samples)
    knn_probability = _weighted_probability(neighbors, normalized_predict, use_time_decay=True)
    logistic_probability = _logistic_probability(samples, predict_features)
    technical_probability = _technical_prior(bars)
    blended = knn_probability * 0.42 + logistic_probability * 0.36 + technical_probability * 0.22
    return _shrink_probability(blended, base_rate, len(samples), strength=110)


def _logistic_probability(samples: list[Sample], predict_features: list[float]) -> float:
    normalized_samples, normalized_predict = _standardize(samples, predict_features)
    weights = [0.0 for _ in normalized_predict]
    bias = 0.0
    learning_rate = 0.06
    for _ in range(220):
        bias_gradient = 0.0
        weight_gradients = [0.0 for _ in weights]
        for features, label, _ in normalized_samples:
            probability = _sigmoid(_dot(weights, features) + bias)
            error = probability - label
            bias_gradient += error
            for index, value in enumerate(features):
                weight_gradients[index] += error * value
        scale = 1 / len(normalized_samples)
        bias -= learning_rate * bias_gradient * scale
        for index in range(len(weights)):
            weights[index] -= learning_rate * weight_gradients[index] * scale
    return _sigmoid(_dot(weights, normalized_predict) + bias)


def _weighted_probability(
    neighbors: list[Sample],
    predict_features: list[float],
    use_time_decay: bool,
) -> float:
    weighted_sum = 0.0
    total_weight = 0.0
    min_index = min(sample[2] for sample in neighbors)
    max_index = max(sample[2] for sample in neighbors)
    index_range = max(max_index - min_index, 1)
    for features, label, sample_index in neighbors:
        weight = 1 / (_distance(features, predict_features) + 0.000001)
        if use_time_decay:
            recency = (sample_index - min_index) / index_range
            weight *= 0.55 + recency * 0.90
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
    lows = [bar.low for bar in window]
    volumes = [bar.volume for bar in window]

    ma5 = _average(closes[-5:])
    ma10 = _average(closes[-10:])
    ma20 = _average(closes)
    ma20_prev = _average(closes[:-1])
    volume_ma5 = _average(volumes[-6:-1])
    volume_ma20 = _average(volumes[:-1])
    high_20d_prev = max(highs[:-1])
    low_20d_prev = min(lows[:-1])
    volatility_10d = _volatility([_return_rate(closes[item], closes[item - 1]) for item in range(11, 21)])
    volatility_20d = _volatility([_return_rate(closes[item], closes[item - 1]) for item in range(1, 21)])

    return [
        _return_rate(current.close, previous.close),
        _return_rate(current.close, closes[-6]),
        _return_rate(current.close, closes[-11]),
        _return_rate(current.close, closes[0]),
        _safe_ratio(current.close - ma5, ma5),
        _safe_ratio(current.close - ma10, ma10),
        _safe_ratio(current.close - ma20, ma20),
        _safe_ratio(ma20 - ma20_prev, ma20_prev),
        _safe_ratio(current.volume, volume_ma5),
        _safe_ratio(current.volume, volume_ma20),
        _safe_ratio(current.close - high_20d_prev, high_20d_prev),
        _safe_ratio(current.close - low_20d_prev, low_20d_prev),
        volatility_10d,
        volatility_20d,
    ]


def _standardize(
    samples: list[Sample],
    predict_features: list[float],
) -> tuple[list[Sample], list[float]]:
    feature_count = len(predict_features)
    means = []
    stds = []
    for feature_index in range(feature_count):
        values = [features[feature_index] for features, _, _ in samples]
        mean = _average(values)
        variance = _average([(value - mean) ** 2 for value in values])
        std = sqrt(variance) or 1.0
        means.append(mean)
        stds.append(std)

    normalized_samples = [
        ([_normalize(value, means[index], stds[index]) for index, value in enumerate(features)], label, sample_index)
        for features, label, sample_index in samples
    ]
    normalized_predict = [
        _normalize(value, means[index], stds[index])
        for index, value in enumerate(predict_features)
    ]
    return normalized_samples, normalized_predict


def _base_rate(samples: list[Sample]) -> float:
    return sum(label for _, label, _ in samples) / len(samples)


def _shrink_probability(probability: float, base_rate: float, sample_count: int, strength: int) -> float:
    sample_weight = sample_count / (sample_count + strength)
    return probability * sample_weight + base_rate * (1 - sample_weight)


def _technical_prior(bars: list[PriceVolumeBar]) -> float:
    window = bars[-21:]
    closes = [bar.close for bar in window]
    volumes = [bar.volume for bar in window]
    close = closes[-1]
    ma5 = _average(closes[-5:])
    ma20 = _average(closes[-20:])
    ma20_prev = _average(closes[-21:-1])
    volume_ratio = _safe_ratio(volumes[-1], _average(volumes[-6:-1]))
    return_20d = _return_rate(close, closes[0])
    score = 0.50
    if close > ma20:
        score += 0.08
    else:
        score -= 0.08
    if ma20 > ma20_prev:
        score += 0.07
    else:
        score -= 0.06
    if close > ma5 and volume_ratio >= 0.85:
        score += 0.05
    if volume_ratio > 1.8 and close < ma5:
        score -= 0.08
    if return_20d > 0.30:
        score -= 0.10
    if return_20d < -0.20:
        score -= 0.06
    return max(0.05, min(0.95, score))


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


def _volatility(values: list[float]) -> float:
    mean = _average(values)
    return sqrt(_average([(value - mean) ** 2 for value in values]))


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def _return_rate(current: float, previous: float) -> float:
    return _safe_ratio(current - previous, previous)


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
