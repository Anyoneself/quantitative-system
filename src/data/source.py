from __future__ import annotations

from data.models import PriceVolumeBar
from data.public_sources import fetch_public_stock_bars, iter_public_a_stock_symbols


def fetch_bars(symbol: str, name: str | None = None) -> list[PriceVolumeBar]:
    return fetch_public_stock_bars(symbol, name)


def iter_a_stock_symbols():
    yield from iter_public_a_stock_symbols()
