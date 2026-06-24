from __future__ import annotations

from dataclasses import dataclass

from quant_system.analysis.indicators import IndicatorResult


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
    low_liquidity: bool
    high_20d_return: bool
    volume_drop_below_ma20: bool


def detect_patterns(indicators: IndicatorResult) -> PatternResult:
    close_above_ma20 = indicators.close > indicators.ma20
    ma20_up = indicators.ma20 > indicators.ma20_prev
    volume_shrink = indicators.volume_ratio_5d < 0.8
    volume_expand = indicators.volume_ratio_5d > 1.5
    high_20d_return = indicators.return_20d > 0.30

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
        low_liquidity=indicators.amount_ma20 > 0 and indicators.amount_ma20 < 50_000_000,
        high_20d_return=high_20d_return,
        volume_drop_below_ma20=(
            indicators.return_1d < -0.02
            and indicators.close < indicators.ma20
            and volume_expand
        ),
    )

