from __future__ import annotations

from analysis.advisor import Advice, generate_advice, make_no_data_advice
from analysis.indicators import calculate_indicators
from analysis.ml_model import predict_next_day_buy_probability
from analysis.patterns import detect_patterns
from data.errors import DataRefreshError
from data.models import PriceVolumeBar
from data.source import fetch_bars


def analyze_stock(symbol: str, algorithm: str, name: str | None = None) -> Advice:
    try:
        bars = fetch_bars(symbol, name)
    except DataRefreshError as exc:
        return make_no_data_advice(f"行情数据抓取失败：{exc}")

    return build_advice(symbol, bars, algorithm)


def build_advice(symbol: str, bars: list[PriceVolumeBar], algorithm: str) -> Advice:
    if not bars:
        return make_no_data_advice(f"行情接口没有返回股票 {symbol} 的价格和成交量数据。")

    indicators = calculate_indicators(symbol, bars)
    if not indicators:
        return make_no_data_advice(f"股票 {symbol} 数据不足，至少需要 21 个交易日。")

    patterns = detect_patterns(indicators)
    ml_prediction = predict_next_day_buy_probability(bars, algorithm)
    return generate_advice(indicators, patterns, ml_prediction)
