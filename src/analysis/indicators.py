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
    open: float
    high: float
    low: float
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
    macd_dif: float
    macd_dea: float
    macd_histogram: float
    macd_dif_prev: float
    macd_dea_prev: float
    macd_histogram_prev: float
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
    macd_dif, macd_dea, macd_histogram, macd_dif_prev, macd_dea_prev, macd_histogram_prev = _macd(all_closes)
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
        open=current.open,
        high=current.high,
        low=current.low,
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
        macd_dif=macd_dif,
        macd_dea=macd_dea,
        macd_histogram=macd_histogram,
        macd_dif_prev=macd_dif_prev,
        macd_dea_prev=macd_dea_prev,
        macd_histogram_prev=macd_histogram_prev,
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


def _macd(closes: list[float]) -> tuple[float, float, float, float, float, float]:
    if len(closes) < 35:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    dif_values = [short - long for short, long in zip(ema12, ema26)]
    dea_values = _ema_series(dif_values, 9)
    histogram_values = [2 * (dif - dea) for dif, dea in zip(dif_values, dea_values)]
    return (
        dif_values[-1],
        dea_values[-1],
        histogram_values[-1],
        dif_values[-2],
        dea_values[-2],
        histogram_values[-2],
    )


def _ema_series(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    ema_values = [values[0]]
    for value in values[1:]:
        ema_values.append(value * alpha + ema_values[-1] * (1 - alpha))
    return ema_values


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
