from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class StockInfo:
    symbol: str
    name: str


@dataclass(frozen=True)
class PriceVolumeBar:
    name: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
