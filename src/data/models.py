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


@dataclass(frozen=True)
class FundFlowBar:
    trade_date: date
    main_net_inflow: float
    super_large_net_inflow: float
    large_net_inflow: float
    medium_net_inflow: float
    small_net_inflow: float
    main_net_inflow_ratio: float
