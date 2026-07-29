from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentRecord:
    symbol: str
    name: str
    market: str
    instrument_type: str = "STOCK"
    listed_date: str | None = None
    delisted_date: str | None = None


@dataclass(frozen=True)
class DailyBarRecord:
    symbol: str
    trade_date: str
    open_price_cents: int
    high_price_cents: int
    low_price_cents: int
    close_price_cents: int
    volume: int
    amount_cents: int
    adjustment_type: str
    source: str
    fetched_at: str


@dataclass(frozen=True)
class PortfolioRecord:
    id: int
    name: str
    initial_cash_cents: int
    benchmark_symbol: str
    base_date: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TransactionRecord:
    id: int
    portfolio_id: int
    symbol: str
    side: str
    trade_date: str
    price_cents: int
    quantity: int
    commission_cents: int
    stamp_tax_cents: int
    note: str | None
    created_at: str
    request_id: str | None = None


@dataclass(frozen=True)
class PositionRecord:
    portfolio_id: int
    symbol: str
    quantity: int
    cost_basis_cents: int
    updated_at: str

    @property
    def average_cost_cents(self) -> float:
        return self.cost_basis_cents / self.quantity


@dataclass(frozen=True)
class EquityRecord:
    portfolio_id: int
    trade_date: str
    cash_cents: int
    market_value_cents: int
    total_assets_cents: int
    net_value: float
    daily_return: float | None
    drawdown: float
    ledger_revision: int = 0
    market_fingerprint: str = ""
