from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP


RISK_WARNING_LIMIT_CHANGE_DATE = date(2026, 7, 6)


def price_limit_rate(symbol: str, name: str, trade_date: date) -> float:
    normalized_name = name.upper().replace(" ", "")
    if symbol.startswith(("300", "301", "688", "689")):
        return 0.20
    if symbol.startswith(("4", "8", "92")):
        return 0.30
    if _is_risk_warning_name(normalized_name) and trade_date < RISK_WARNING_LIMIT_CHANGE_DATE:
        # 2026 年 7 月 6 日前，沪深主板风险警示股票通常适用 5% 涨跌幅限制
        return 0.05
    return 0.10


def price_limit_value(previous_close: float, rate: float, direction: str) -> float:
    multiplier = 1 + rate if direction == "UP" else 1 - rate
    # A 股股票价格最小变动单位通常为 0.01 元，涨跌停价按该精度四舍五入
    value = Decimal(str(previous_close)) * Decimal(str(multiplier))
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def is_limit_price(
    price: float,
    previous_close: float,
    symbol: str,
    name: str,
    trade_date: date,
    direction: str,
) -> bool:
    if previous_close <= 0 or price <= 0:
        return False
    rate = price_limit_rate(symbol, name, trade_date)
    limit_value = price_limit_value(previous_close, rate, direction)
    if direction == "UP":
        return price >= limit_value - 0.001
    return price <= limit_value + 0.001


def _is_risk_warning_name(name: str) -> bool:
    return name.startswith(("ST", "*ST", "S*ST", "SST"))
