from __future__ import annotations

import sqlite3

from storage.models import (
    EquityRecord,
    PortfolioRecord,
    PositionRecord,
    TransactionRecord,
)


def insert_portfolio(
    connection: sqlite3.Connection,
    name: str,
    initial_cash_cents: int,
    benchmark_symbol: str,
    base_date: str,
    now: str,
) -> PortfolioRecord:
    cursor = connection.execute(
        """
        INSERT INTO portfolios(
            name, initial_cash, benchmark_symbol, base_date, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, initial_cash_cents, benchmark_symbol, base_date, now, now),
    )
    return get_portfolio(connection, int(cursor.lastrowid))


def get_portfolio(
    connection: sqlite3.Connection,
    portfolio_id: int,
) -> PortfolioRecord:
    row = connection.execute(
        "SELECT * FROM portfolios WHERE id = ?",
        (portfolio_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"投资组合 {portfolio_id} 不存在。")
    return _portfolio_from_row(row)


def list_portfolios(connection: sqlite3.Connection) -> list[PortfolioRecord]:
    rows = connection.execute(
        "SELECT * FROM portfolios ORDER BY id"
    ).fetchall()
    return [_portfolio_from_row(row) for row in rows]


def touch_portfolio(
    connection: sqlite3.Connection,
    portfolio_id: int,
    updated_at: str,
) -> None:
    connection.execute(
        "UPDATE portfolios SET updated_at = ? WHERE id = ?",
        (updated_at, portfolio_id),
    )


def insert_transaction(
    connection: sqlite3.Connection,
    portfolio_id: int,
    symbol: str,
    side: str,
    trade_date: str,
    price_cents: int,
    quantity: int,
    commission_cents: int,
    stamp_tax_cents: int,
    note: str | None,
    request_id: str | None,
    now: str,
) -> TransactionRecord:
    cursor = connection.execute(
        """
        INSERT INTO transactions(
            portfolio_id, symbol, side, trade_date, price, quantity,
            commission, stamp_tax, note, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            portfolio_id,
            symbol,
            side,
            trade_date,
            price_cents,
            quantity,
            commission_cents,
            stamp_tax_cents,
            note,
            request_id,
            now,
        ),
    )
    row = connection.execute(
        "SELECT * FROM transactions WHERE id = ?",
        (int(cursor.lastrowid),),
    ).fetchone()
    return _transaction_from_row(row)


def get_transaction_by_request_id(
    connection: sqlite3.Connection,
    portfolio_id: int,
    request_id: str,
) -> TransactionRecord | None:
    row = connection.execute(
        """
        SELECT * FROM transactions
        WHERE portfolio_id = ? AND request_id = ?
        """,
        (portfolio_id, request_id),
    ).fetchone()
    if row is None:
        return None
    return _transaction_from_row(row)


def list_transactions(
    connection: sqlite3.Connection,
    portfolio_id: int,
) -> list[TransactionRecord]:
    rows = connection.execute(
        """
        SELECT * FROM transactions
        WHERE portfolio_id = ?
        ORDER BY trade_date, id
        """,
        (portfolio_id,),
    ).fetchall()
    return [_transaction_from_row(row) for row in rows]


def replace_positions(
    connection: sqlite3.Connection,
    portfolio_id: int,
    positions: list[PositionRecord],
) -> None:
    connection.execute(
        "DELETE FROM positions WHERE portfolio_id = ?",
        (portfolio_id,),
    )
    connection.executemany(
        """
        INSERT INTO positions(
            portfolio_id, symbol, quantity, cost_basis, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                position.portfolio_id,
                position.symbol,
                position.quantity,
                position.cost_basis_cents,
                position.updated_at,
            )
            for position in positions
        ],
    )


def list_positions(
    connection: sqlite3.Connection,
    portfolio_id: int,
) -> list[PositionRecord]:
    rows = connection.execute(
        """
        SELECT * FROM positions
        WHERE portfolio_id = ?
        ORDER BY symbol
        """,
        (portfolio_id,),
    ).fetchall()
    return [
        PositionRecord(
            portfolio_id=int(row["portfolio_id"]),
            symbol=str(row["symbol"]),
            quantity=int(row["quantity"]),
            cost_basis_cents=int(row["cost_basis"]),
            updated_at=str(row["updated_at"]),
        )
        for row in rows
    ]


def replace_equity(
    connection: sqlite3.Connection,
    portfolio_id: int,
    points: list[EquityRecord],
) -> None:
    connection.execute(
        "DELETE FROM portfolio_equity WHERE portfolio_id = ?",
        (portfolio_id,),
    )
    connection.executemany(
        """
        INSERT INTO portfolio_equity(
            portfolio_id, trade_date, cash, market_value, total_assets,
            net_value, daily_return, drawdown, ledger_revision, market_fingerprint
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                point.portfolio_id,
                point.trade_date,
                point.cash_cents,
                point.market_value_cents,
                point.total_assets_cents,
                _decimal_text(point.net_value),
                _decimal_text(point.daily_return) if point.daily_return is not None else None,
                _decimal_text(point.drawdown),
                point.ledger_revision,
                point.market_fingerprint,
            )
            for point in points
        ],
    )


def list_equity(
    connection: sqlite3.Connection,
    portfolio_id: int,
) -> list[EquityRecord]:
    rows = connection.execute(
        """
        SELECT * FROM portfolio_equity
        WHERE portfolio_id = ?
        ORDER BY trade_date
        """,
        (portfolio_id,),
    ).fetchall()
    return [
        EquityRecord(
            portfolio_id=int(row["portfolio_id"]),
            trade_date=str(row["trade_date"]),
            cash_cents=int(row["cash"]),
            market_value_cents=int(row["market_value"]),
            total_assets_cents=int(row["total_assets"]),
            net_value=float(row["net_value"]),
            daily_return=float(row["daily_return"]) if row["daily_return"] is not None else None,
            drawdown=float(row["drawdown"]),
            ledger_revision=int(row["ledger_revision"]),
            market_fingerprint=str(row["market_fingerprint"]),
        )
        for row in rows
    ]


def _portfolio_from_row(row: sqlite3.Row) -> PortfolioRecord:
    return PortfolioRecord(
        id=int(row["id"]),
        name=str(row["name"]),
        initial_cash_cents=int(row["initial_cash"]),
        benchmark_symbol=str(row["benchmark_symbol"]),
        base_date=str(row["base_date"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _transaction_from_row(row: sqlite3.Row) -> TransactionRecord:
    return TransactionRecord(
        id=int(row["id"]),
        portfolio_id=int(row["portfolio_id"]),
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        trade_date=str(row["trade_date"]),
        price_cents=int(row["price"]),
        quantity=int(row["quantity"]),
        commission_cents=int(row["commission"]),
        stamp_tax_cents=int(row["stamp_tax"]),
        note=str(row["note"]) if row["note"] is not None else None,
        created_at=str(row["created_at"]),
        request_id=(
            str(row["request_id"])
            if row["request_id"] is not None
            else None
        ),
    )


def _decimal_text(value: float) -> str:
    return format(value, ".15g")
