from __future__ import annotations

from data.fund_flow import fetch_public_fund_flow_bars
from data.models import FundFlowBar, PriceVolumeBar
from data.public_sources import fetch_public_stock_bars, iter_public_a_stock_symbols


def fetch_bars(symbol: str, name: str | None = None) -> list[PriceVolumeBar]:
    return fetch_public_stock_bars(symbol, name)


def fetch_fund_flow_bars(symbol: str, limit: int = 20) -> list[FundFlowBar]:
    return fetch_public_fund_flow_bars(symbol, limit)


def iter_a_stock_symbols():
    yield from iter_public_a_stock_symbols()
