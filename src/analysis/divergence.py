from __future__ import annotations

from dataclasses import dataclass

from analysis.fund_flow import FundFlowResult
from data.models import PriceVolumeBar


@dataclass(frozen=True)
class DivergenceResult:
    bullish_divergence: bool
    bearish_divergence: bool
    bullish_score_adjustment: int
    bearish_risk_adjustment: int
    signal: str
    confidence: int
    reasons: list[str]
    ma5_above_ma10: bool
    ma5_ma10_weaving: bool
    ma5_ma10_weaving_count: int
    ma5_ma10_first_bullish_weaving: bool
    ma5_ma10_late_bearish_weaving: bool


def analyze_divergence(
    bars: list[PriceVolumeBar],
    fund_flow: FundFlowResult | None = None,
    lookback: int = 30,
) -> DivergenceResult | None:
    if len(bars) < max(lookback + 35, 60):
        return None

    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    macd = _macd_series(closes)
    ma5_values = _moving_average_series(closes, 5)
    ma10_values = _moving_average_series(closes, 10)
    weave_state = _ma5_ma10_weave_state(closes, ma5_values, ma10_values)

    current_index = len(closes) - 1
    prior_start = max(0, current_index - lookback)
    prior_indexes = list(range(prior_start, current_index))
    prior_low_index = min(prior_indexes, key=lambda index: closes[index])
    prior_high_index = max(prior_indexes, key=lambda index: closes[index])

    current_close = closes[current_index]
    prior_low = closes[prior_low_index]
    prior_high = closes[prior_high_index]
    prior_range_ratio = _safe_ratio(prior_high - prior_low, current_close)
    current_dif, current_dea, current_histogram = macd[current_index]
    prior_low_dif, _, prior_low_histogram = macd[prior_low_index]
    prior_high_dif, _, prior_high_histogram = macd[prior_high_index]
    current_volume_ratio = _safe_ratio(volumes[current_index], _average(volumes[max(0, current_index - 5):current_index]))
    prior_low_volume_ratio = _safe_ratio(volumes[prior_low_index], _average(volumes[max(0, prior_low_index - 5):prior_low_index] or [volumes[prior_low_index]]))
    prior_high_volume_ratio = _safe_ratio(volumes[prior_high_index], _average(volumes[max(0, prior_high_index - 5):prior_high_index] or [volumes[prior_high_index]]))

    bullish_reasons: list[str] = []
    bearish_reasons: list[str] = []
    bullish_score = 0
    bearish_risk = 0
    bullish_confidence = 0
    bearish_confidence = 0

    near_new_low = prior_range_ratio >= 0.05 and current_close <= prior_low * 1.01
    if near_new_low and current_histogram > prior_low_histogram:
        bullish_score += 8
        bullish_confidence += 28
        bullish_reasons.append("价格接近或跌破近 30 日低点，但 MACD 柱没有同步创新低。")
    if near_new_low and current_dif > prior_low_dif:
        bullish_score += 6
        bullish_confidence += 22
        bullish_reasons.append("价格处在低位，但 DIF 强于前一轮低点，出现下跌背驰候选。")
    if near_new_low and current_volume_ratio < prior_low_volume_ratio:
        bullish_score += 3
        bullish_confidence += 10
        bullish_reasons.append("价格低位附近下跌时量能弱于上一轮低点，抛压有衰竭迹象。")
    if fund_flow and near_new_low and fund_flow.signal != "持续流出":
        bullish_score += 4
        bullish_confidence += 12
        bullish_reasons.append("低位背驰同时资金流不再持续流出，买入信号可信度提高。")
    if near_new_low and weave_state["ma5_ma10_late_bearish_weaving"]:
        bullish_score += 4
        bullish_confidence += 10
        bullish_reasons.append("空头排列末端出现多次 MA5/MA10 缠绕，下跌转折概率提高。")

    near_new_high = prior_range_ratio >= 0.05 and current_close >= prior_high * 0.99
    if near_new_high and current_histogram < prior_high_histogram:
        bearish_risk += 8
        bearish_confidence += 28
        bearish_reasons.append("价格接近或突破近 30 日高点，但 MACD 柱没有同步创新高。")
    if near_new_high and current_dif < prior_high_dif:
        bearish_risk += 6
        bearish_confidence += 22
        bearish_reasons.append("价格处在高位，但 DIF 弱于前一轮高点，出现上涨背驰候选。")
    if near_new_high and current_volume_ratio < prior_high_volume_ratio:
        bearish_risk += 3
        bearish_confidence += 10
        bearish_reasons.append("价格高位附近上涨时量能弱于上一轮高点，追高承接不足。")
    if fund_flow and near_new_high and fund_flow.signal == "持续流出":
        bearish_risk += 5
        bearish_confidence += 14
        bearish_reasons.append("高位背驰同时主力资金持续流出，卖出风险提高。")
    if near_new_high and weave_state["ma5_above_ma10"] and weave_state["ma5_ma10_weaving_count"] >= 3:
        bearish_risk += 4
        bearish_confidence += 10
        bearish_reasons.append("多头排列后出现多次 MA5/MA10 缠绕，上涨中继概率下降。")

    bullish_score = max(0, min(20, bullish_score))
    bearish_risk = max(0, min(25, bearish_risk))
    bullish = bullish_score >= 8
    bearish = bearish_risk >= 8
    if bullish and bearish:
        signal = "多空背驰分歧"
        confidence = max(bullish_confidence, bearish_confidence)
    elif bullish:
        signal = "下跌背驰候选"
        confidence = bullish_confidence
    elif bearish:
        signal = "上涨背驰候选"
        confidence = bearish_confidence
    else:
        signal = "无明显背驰"
        confidence = max(bullish_confidence, bearish_confidence)

    return DivergenceResult(
        bullish_divergence=bullish,
        bearish_divergence=bearish,
        bullish_score_adjustment=bullish_score,
        bearish_risk_adjustment=bearish_risk,
        signal=signal,
        confidence=max(0, min(100, confidence)),
        reasons=bullish_reasons + bearish_reasons or ["未发现价格新高/新低与 MACD、量能或资金流之间的明显背驰。"],
        ma5_above_ma10=weave_state["ma5_above_ma10"],
        ma5_ma10_weaving=weave_state["ma5_ma10_weaving"],
        ma5_ma10_weaving_count=weave_state["ma5_ma10_weaving_count"],
        ma5_ma10_first_bullish_weaving=weave_state["ma5_ma10_first_bullish_weaving"],
        ma5_ma10_late_bearish_weaving=weave_state["ma5_ma10_late_bearish_weaving"],
    )


def _ma5_ma10_weave_state(closes: list[float], ma5_values: list[float], ma10_values: list[float]) -> dict:
    valid_indexes = [index for index in range(len(closes)) if ma5_values[index] > 0 and ma10_values[index] > 0]
    recent_indexes = valid_indexes[-40:]
    cross_count = 0
    for left, right in zip(recent_indexes, recent_indexes[1:]):
        previous_spread = ma5_values[left] - ma10_values[left]
        current_spread = ma5_values[right] - ma10_values[right]
        if previous_spread == 0 or previous_spread * current_spread < 0:
            cross_count += 1
    latest_index = len(closes) - 1
    latest_spread_ratio = abs(ma5_values[latest_index] - ma10_values[latest_index]) / closes[latest_index]
    ma5_above_ma10 = ma5_values[latest_index] > ma10_values[latest_index]
    ma5_ma10_weaving = latest_spread_ratio <= 0.01 or cross_count >= 2
    return {
        "ma5_above_ma10": ma5_above_ma10,
        "ma5_ma10_weaving": ma5_ma10_weaving,
        "ma5_ma10_weaving_count": cross_count,
        "ma5_ma10_first_bullish_weaving": ma5_above_ma10 and ma5_ma10_weaving and cross_count <= 1,
        "ma5_ma10_late_bearish_weaving": (not ma5_above_ma10) and ma5_ma10_weaving and cross_count >= 2,
    }


def _macd_series(closes: list[float]) -> list[tuple[float, float, float]]:
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    dif_values = [short - long for short, long in zip(ema12, ema26)]
    dea_values = _ema_series(dif_values, 9)
    return [(dif, dea, 2 * (dif - dea)) for dif, dea in zip(dif_values, dea_values)]


def _ema_series(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    ema_values = [values[0]]
    for value in values[1:]:
        ema_values.append(value * alpha + ema_values[-1] * (1 - alpha))
    return ema_values


def _moving_average_series(values: list[float], period: int) -> list[float]:
    result = []
    for index in range(len(values)):
        if index + 1 < period:
            result.append(0.0)
            continue
        result.append(_average(values[index + 1 - period:index + 1]))
    return result


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator