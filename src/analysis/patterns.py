from __future__ import annotations

from dataclasses import dataclass

from analysis.indicators import IndicatorResult


@dataclass(frozen=True)
class PatternResult:
    limit_up_low_volume: bool
    breakout_with_volume: bool
    pullback_low_volume: bool
    high_volume_drop: bool
    high_volume_stagnation: bool
    price_up_volume_down: bool
    price_down_volume_up: bool
    close_above_ma20: bool
    ma20_up: bool
    close_above_ma250: bool
    ma250_turning_up: bool
    macd_golden_cross: bool
    macd_golden_cross_above_zero: bool
    macd_dead_cross: bool
    macd_histogram_expanding: bool
    macd_histogram_shrinking: bool
    yearline_breakout_with_volume: bool
    yearline_pullback_low_volume: bool
    first_day_high_breakout_with_volume: bool
    doji_reversal: bool
    long_lower_shadow: bool
    low_position_rebound: bool
    low_liquidity: bool
    high_20d_return: bool
    volume_drop_below_ma20: bool


def detect_patterns(indicators: IndicatorResult) -> PatternResult:
    close_above_ma20 = indicators.close > indicators.ma20
    ma20_up = indicators.ma20 > indicators.ma20_prev
    close_above_ma250 = indicators.ma250 > 0 and indicators.close > indicators.ma250
    ma250_turning_up = indicators.ma250 > 0 and indicators.ma250_prev > 0 and indicators.ma250 > indicators.ma250_prev
    macd_golden_cross = indicators.macd_dif_prev <= indicators.macd_dea_prev and indicators.macd_dif > indicators.macd_dea
    macd_dead_cross = indicators.macd_dif_prev >= indicators.macd_dea_prev and indicators.macd_dif < indicators.macd_dea
    macd_golden_cross_above_zero = macd_golden_cross and indicators.macd_dif > 0 and indicators.macd_dea > 0
    macd_histogram_expanding = indicators.macd_histogram > 0 and indicators.macd_histogram > indicators.macd_histogram_prev
    macd_histogram_shrinking = indicators.macd_histogram < indicators.macd_histogram_prev
    volume_shrink = indicators.volume_ratio_5d < 0.8
    volume_expand = indicators.volume_ratio_5d > 1.5
    high_20d_return = indicators.return_20d > 0.30
    candle_range = max(indicators.high - indicators.low, indicators.close * 0.001)
    candle_body = abs(indicators.close - indicators.open)
    lower_shadow = min(indicators.open, indicators.close) - indicators.low
    upper_shadow = indicators.high - max(indicators.open, indicators.close)
    near_20d_low = indicators.close <= indicators.low_20d_prev * 1.06
    short_term_pullback = indicators.return_5d < -0.03 or indicators.return_20d < -0.08
    doji_reversal = candle_body / candle_range <= 0.18 and near_20d_low and short_term_pullback
    long_lower_shadow = lower_shadow / candle_range >= 0.45 and lower_shadow > upper_shadow * 1.4
    low_position_rebound = near_20d_low and (doji_reversal or long_lower_shadow) and indicators.return_1d >= -0.03

    return PatternResult(
        limit_up_low_volume=indicators.is_limit_up and volume_shrink,
        breakout_with_volume=indicators.close > indicators.high_20d_prev and volume_expand,
        pullback_low_volume=(
            -0.04 <= indicators.return_1d < 0
            and volume_shrink
            and close_above_ma20
        ),
        high_volume_drop=indicators.return_1d <= -0.05 and volume_expand,
        high_volume_stagnation=(
            abs(indicators.return_1d) <= 0.02
            and volume_expand
            and high_20d_return
        ),
        price_up_volume_down=indicators.return_1d > 0 and volume_shrink,
        price_down_volume_up=indicators.return_1d < 0 and volume_expand,
        close_above_ma20=close_above_ma20,
        ma20_up=ma20_up,
        close_above_ma250=close_above_ma250,
        ma250_turning_up=ma250_turning_up,
        macd_golden_cross=macd_golden_cross,
        macd_golden_cross_above_zero=macd_golden_cross_above_zero,
        macd_dead_cross=macd_dead_cross,
        macd_histogram_expanding=macd_histogram_expanding,
        macd_histogram_shrinking=macd_histogram_shrinking,
        yearline_breakout_with_volume=(
            close_above_ma250
            and ma250_turning_up
            and indicators.return_1d > 0
            and volume_expand
        ),
        yearline_pullback_low_volume=(
            close_above_ma250
            and indicators.close <= indicators.ma250 * 1.04
            and -0.05 <= indicators.return_1d < 0
            and volume_shrink
        ),
        first_day_high_breakout_with_volume=(
            indicators.first_day_high > 0
            and indicators.close > indicators.first_day_high
            and volume_expand
        ),
        doji_reversal=doji_reversal,
        long_lower_shadow=long_lower_shadow,
        low_position_rebound=low_position_rebound,
        low_liquidity=indicators.amount_ma20 > 0 and indicators.amount_ma20 < 50_000_000,
        high_20d_return=high_20d_return,
        volume_drop_below_ma20=(
            indicators.return_1d < -0.02
            and indicators.close < indicators.ma20
            and volume_expand
        ),
    )

