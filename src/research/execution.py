from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor

from data.market_rules import is_limit_price
from data.models import PriceVolumeBar
from research.config import SimulationConfig


@dataclass(frozen=True)
class Fill:
    quantity: int
    price: float
    fee: float
    reason: str


def simulate_fill(
    symbol: str,
    side: str,
    quantity: int,
    bar: PriceVolumeBar | None,
    previous: PriceVolumeBar | None,
    cash: float,
    config: SimulationConfig,
    acquired_on: date | None = None,
) -> Fill:
    if side not in {"BUY", "SELL"} or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("委托方向或数量不合法。")
    if not symbol.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return Fill(0, 0, 0, "UNSUPPORTED_BOARD")
    if bar is None or previous is None:
        return Fill(0, 0, 0, "MISSING_BAR")
    if bar.volume <= 0:
        return Fill(0, 0, 0, "SUSPENDED")
    if side == "SELL" and acquired_on is not None and acquired_on >= bar.trade_date:
        return Fill(0, 0, 0, "T_PLUS_ONE")
    direction = "UP" if side == "BUY" else "DOWN"
    # 开盘触及方向性涨跌停时保守地拒绝成交，不假设能在队列中成交。
    if is_limit_price(bar.open, previous.close, symbol, bar.name, bar.trade_date, direction):
        return Fill(0, 0, 0, "PRICE_LIMIT")
    price = round(bar.open * (1 + config.slippage_rate * (1 if side == "BUY" else -1)), 2)
    # 不用当天高低价裁小滑点；价格越界直接拒绝，避免成本压力测试反而降低成本。
    if not bar.low <= price <= bar.high or price <= 0:
        return Fill(0, 0, 0, "SLIPPAGE_OUTSIDE_BAR")
    # 昨日成交量决定订单容量，今日总量仅约束事后成交；日线不能证明开盘真实可用深度。
    capacity = floor(min(previous.volume, bar.volume) * config.participation_rate / 100) * 100
    filled = min(quantity // 100 * 100, capacity)
    if filled <= 0:
        return Fill(0, price, 0, "LIQUIDITY_OR_LOT")
    if side == "BUY":
        affordable = floor(max(0, cash - config.min_commission) / (price * (1 + config.commission_rate)) / 100) * 100
        filled = min(filled, affordable)
        while filled > 0 and round(price * filled + trading_fee(price * filled, side, config), 2) > cash:
            filled -= 100
    if filled <= 0:
        return Fill(0, price, 0, "INSUFFICIENT_CASH")
    fee = trading_fee(price * filled, side, config)
    if side == "SELL" and fee > price * filled:
        return Fill(0, price, 0, "FEES_EXCEED_PROCEEDS")
    return Fill(filled, price, fee, "FILLED" if filled == quantity else "PARTIAL")


def trading_fee(amount: float, side: str, config: SimulationConfig) -> float:
    # 费率是整个实验冻结的场景参数，不冒充跨年份完整历史费率表。
    commission = max(config.min_commission, amount * config.commission_rate)
    return round(commission + (amount * config.stamp_duty_rate if side == "SELL" else 0), 2)
