from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from data.models import PriceVolumeBar


@dataclass(frozen=True)
class IndicatorResult:
    symbol: str
    name: str
    analysis_date: str
    data_end_date: str
    sample_days: int
    close: float
    return_1d: float
    return_5d: float
    return_20d: float
    ma5: float
    ma10: float
    ma20: float
    ma20_prev: float
    ma250: float
    ma250_prev: float
    first_day_high: float
    high_20d_prev: float
    low_20d_prev: float
    volume: float
    volume_ma5: float
    volume_ma20: float
    volume_ratio_5d: float
    volume_ratio_20d: float
    amount: float
    amount_ma20: float
    is_limit_up: bool
    is_limit_down: bool


def calculate_indicators(symbol: str, bars: list[PriceVolumeBar]) -> IndicatorResult | None:
    if len(bars) < 21:
        return None

    all_bars = bars
    bars = all_bars[-21:]
    current = all_bars[-1]
    previous = all_bars[-2]
    closes = [bar.close for bar in bars]
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    volumes = [bar.volume for bar in bars]
    amounts = [bar.amount for bar in bars]
    all_closes = [bar.close for bar in all_bars]

    ma20 = _average(closes[-20:])
    ma20_prev = _average(closes[-21:-1])
    ma250 = _average(all_closes[-250:]) if len(all_closes) >= 250 else 0.0
    ma250_prev = _average(all_closes[-251:-1]) if len(all_closes) >= 251 else 0.0
    volume_ma5 = _average(volumes[-6:-1])
    volume_ma20 = _average(volumes[-21:-1])
    amount_ma20 = _average(amounts[-21:-1])
    return_1d = _return_rate(current.close, previous.close)

    return IndicatorResult(
        symbol=symbol,
        name=current.name,
        analysis_date=date.today().isoformat(),
        data_end_date=current.trade_date.isoformat(),
        sample_days=len(bars),
        close=current.close,
        return_1d=return_1d,
        return_5d=_return_rate(current.close, closes[-6]),
        return_20d=_return_rate(current.close, closes[-21]),
        ma5=_average(closes[-5:]),
        ma10=_average(closes[-10:]),
        ma20=ma20,
        ma20_prev=ma20_prev,
        ma250=ma250,
        ma250_prev=ma250_prev,
        first_day_high=all_bars[0].high if len(all_bars) < 250 else 0.0,
        high_20d_prev=max(highs[-21:-1]),
        low_20d_prev=min(lows[-21:-1]),
        volume=current.volume,
        volume_ma5=volume_ma5,
        volume_ma20=volume_ma20,
        volume_ratio_5d=_safe_ratio(current.volume, volume_ma5),
        volume_ratio_20d=_safe_ratio(current.volume, volume_ma20),
        amount=current.amount,
        amount_ma20=amount_ma20,
        is_limit_up=return_1d >= 0.098,
        is_limit_down=return_1d <= -0.098,
    )


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def _return_rate(current: float, previous: float) -> float:
    return _safe_ratio(current - previous, previous)


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
