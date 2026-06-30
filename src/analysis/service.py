from __future__ import annotations

from analysis.advisor import Advice, generate_advice, make_no_data_advice
from analysis.chan_theory import analyze_chan_structure
from analysis.divergence import DivergenceResult, analyze_divergence
from analysis.fund_flow import FundFlowResult, analyze_fund_flow
from analysis.indicators import calculate_indicators
from analysis.ml_model import predict_next_day_buy_probability
from analysis.patterns import detect_patterns
from analysis.sell_advisor import SellAdvice, build_sell_advice
from data.errors import DataRefreshError
from data.models import PriceVolumeBar
from data.source import fetch_bars, fetch_fund_flow_bars


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
    chan_structure = analyze_chan_structure(bars[-90:])
    fund_flow = _safe_fund_flow(symbol)
    divergence = analyze_divergence(bars, fund_flow)
    return generate_advice(indicators, patterns, ml_prediction, chan_structure, fund_flow, divergence)


def analyze_stock_sell(
    symbol: str,
    cost_price: float | None,
    quantity: float | None = None,
    max_loss_rate: float = 0.08,
    target_profit_rate: float = 0.20,
    name: str | None = None,
) -> SellAdvice:
    try:
        bars = fetch_bars(symbol, name)
    except DataRefreshError as exc:
        return build_sell_advice(symbol, [], cost_price, quantity, max_loss_rate, target_profit_rate)
    fund_flow = _safe_fund_flow(symbol)
    divergence = analyze_divergence(bars, fund_flow)
    return build_sell_advice(symbol, bars, cost_price, quantity, max_loss_rate, target_profit_rate, fund_flow, divergence)


def _safe_fund_flow(symbol: str) -> FundFlowResult | None:
    try:
        return analyze_fund_flow(fetch_fund_flow_bars(symbol, 20))
    except DataRefreshError:
        return None
