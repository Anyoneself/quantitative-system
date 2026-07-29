from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from portfolio.service import MAX_LEDGER_CENTS, replay_transactions
from storage.connection import connect_database
from storage.market_repository import list_daily_bars
from storage.migrations import initialize_database
from storage.models import EquityRecord
from storage.portfolio_repository import (
    get_portfolio,
    list_equity,
    list_transactions,
    replace_equity,
)


def rebuild_portfolio_equity(
    portfolio_id: int,
    db_path: str | Path | None = None,
) -> list[EquityRecord]:
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        portfolio = get_portfolio(connection, portfolio_id)
        transactions = list_transactions(connection, portfolio_id)
        symbols = sorted(
            {transaction.symbol for transaction in transactions}
            | {portfolio.benchmark_symbol}
        )
        bars = list_daily_bars(
            connection,
            symbols,
            start_date=portfolio.base_date,
            adjustment_type="NONE",
        )
        dates = sorted(
            {portfolio.base_date}
            | {transaction.trade_date for transaction in transactions}
            | {bar.trade_date for bar in bars}
        )
        points = _calculate_equity_points(
            portfolio,
            transactions,
            bars,
            dates,
            ledger_revision=max(
                (transaction.id for transaction in transactions),
                default=0,
            ),
            market_fingerprint=market_data_fingerprint(bars),
        )
        replace_equity(connection, portfolio_id, points)
        connection.commit()
        return points
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_portfolio_equity(
    portfolio_id: int,
    db_path: str | Path | None = None,
) -> list[EquityRecord]:
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        get_portfolio(connection, portfolio_id)
        return list_equity(connection, portfolio_id)
    finally:
        connection.close()


def _calculate_equity_points(
    portfolio,
    transactions,
    bars,
    dates: list[str],
    ledger_revision: int = 0,
    market_fingerprint: str = "",
) -> list[EquityRecord]:
    bars_by_date: dict[str, list] = {}
    for bar in bars:
        bars_by_date.setdefault(bar.trade_date, []).append(bar)
    latest_prices: dict[str, int] = {}
    points: list[EquityRecord] = []
    peak_assets = portfolio.initial_cash_cents
    previous_assets: int | None = None

    for trade_date in dates:
        for bar in bars_by_date.get(trade_date, []):
            latest_prices[bar.symbol] = bar.close_price_cents
        active_transactions = [
            transaction
            for transaction in transactions
            if transaction.trade_date <= trade_date
        ]
        snapshot = replay_transactions(
            portfolio,
            active_transactions,
            updated_at=trade_date,
        )
        market_value = 0
        for position in snapshot.positions:
            close_price = latest_prices.get(position.symbol)
            if close_price is None:
                raise ValueError(
                    f"{trade_date} 缺少 {position.symbol} 当日及之前的收盘价，无法计算净值。"
                )
            # 停牌或缺失交易日沿用最近一个已知收盘价，不读取未来价格。
            position_value = position.quantity * close_price
            _validate_equity_amount(position_value, "单项持仓市值")
            market_value += position_value
            _validate_equity_amount(market_value, "组合持仓市值")
        total_assets = snapshot.cash_cents + market_value
        _validate_equity_amount(total_assets, "组合总资产")
        peak_assets = max(peak_assets, total_assets)
        net_value = total_assets / portfolio.initial_cash_cents
        daily_return = (
            total_assets / previous_assets - 1
            if previous_assets not in (None, 0)
            else None
        )
        drawdown = total_assets / peak_assets - 1 if peak_assets else 0.0
        points.append(
            EquityRecord(
                portfolio_id=portfolio.id,
                trade_date=trade_date,
                cash_cents=snapshot.cash_cents,
                market_value_cents=market_value,
                total_assets_cents=total_assets,
                net_value=net_value,
                daily_return=daily_return,
                drawdown=drawdown,
                ledger_revision=ledger_revision,
                market_fingerprint=market_fingerprint,
            )
        )
        previous_assets = total_assets
    return points


def market_data_fingerprint(bars) -> str:
    digest = sha256()
    for bar in sorted(bars, key=lambda item: (item.symbol, item.trade_date)):
        values = (
            bar.symbol,
            bar.trade_date,
            bar.open_price_cents,
            bar.high_price_cents,
            bar.low_price_cents,
            bar.close_price_cents,
            bar.volume,
            bar.amount_cents,
            bar.adjustment_type,
            bar.source,
            bar.fetched_at,
        )
        digest.update(
            ("|".join(str(value) for value in values) + "\n").encode("utf-8")
        )
    return digest.hexdigest()


def _validate_equity_amount(value: int, field_name: str) -> None:
    if value < 0 or value > MAX_LEDGER_CENTS:
        raise ValueError(
            f"{field_name}超出本地账本安全上限 "
            f"{MAX_LEDGER_CENTS / 100:,.0f} 元。"
        )
