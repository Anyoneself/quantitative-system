from __future__ import annotations

from datetime import date
from math import sqrt
from statistics import mean, stdev

from data.models import PriceVolumeBar
from research.config import StrategyConfig


def target_weights(
    histories: dict[str, list[PriceVolumeBar]],
    members: tuple[str, ...],
    as_of: date,
    config: StrategyConfig,
) -> dict[str, float]:
    candidates = []
    minimum_bars = max(config.lookback + 1, config.trend_window, config.volatility_window + 1)
    for symbol in members:
        # 在接口边界再次截断历史，禁止未来价格进入因子或仓位计算。
        rows = [bar for bar in histories.get(symbol, []) if bar.trade_date <= as_of]
        if len(rows) < minimum_bars or rows[-1].trade_date != as_of or rows[-1].volume <= 0:
            continue
        close = rows[-1].close
        momentum = close / rows[-config.lookback - 1].close - 1
        if momentum <= 0 or close <= mean(bar.close for bar in rows[-config.trend_window:]):
            continue
        if mean(bar.amount for bar in rows[-config.volatility_window:]) < config.min_average_amount:
            continue
        returns = [right.close / left.close - 1 for left, right in zip(
            rows[-config.volatility_window - 1:-1], rows[-config.volatility_window:],
        )]
        volatility = max(stdev(returns) * sqrt(252), 0.01)
        candidates.append((symbol, momentum / volatility, volatility))
    selected = sorted(candidates, key=lambda item: (-item[1], item[0]))[:config.max_positions]
    denominator = sum(1 / item[2] for item in selected)
    if not denominator:
        return {}
    weights = {symbol: min(config.max_weight, config.gross_limit / volatility / denominator)
               for symbol, _, volatility in selected}
    # 使用完全正相关假设的波动率上界缩放，不靠不稳定相关性估计放大杠杆。
    upper_volatility = sum(weights[symbol] * volatility for symbol, _, volatility in selected)
    scale = min(1.0, config.target_volatility / upper_volatility) if upper_volatility > 0 else 1.0
    return {symbol: weight * scale for symbol, weight in weights.items()}
