from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from storage.connection import connect_database, resolve_database_path


MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """
            CREATE TABLE instruments (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                instrument_type TEXT NOT NULL,
                listed_date TEXT,
                delisted_date TEXT,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE daily_bars (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open_price INTEGER NOT NULL CHECK (open_price > 0),
                high_price INTEGER NOT NULL CHECK (high_price > 0),
                low_price INTEGER NOT NULL CHECK (low_price > 0),
                close_price INTEGER NOT NULL CHECK (close_price > 0),
                volume INTEGER NOT NULL CHECK (volume >= 0),
                amount INTEGER NOT NULL CHECK (amount >= 0),
                adjustment_type TEXT NOT NULL,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (symbol, trade_date),
                FOREIGN KEY (symbol) REFERENCES instruments(symbol),
                CHECK (high_price >= open_price),
                CHECK (high_price >= close_price),
                CHECK (high_price >= low_price),
                CHECK (low_price <= open_price),
                CHECK (low_price <= close_price)
            )
            """,
            """
            CREATE TABLE portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK (length(trim(name)) > 0),
                initial_cash INTEGER NOT NULL CHECK (initial_cash > 0),
                benchmark_symbol TEXT NOT NULL,
                base_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                trade_date TEXT NOT NULL,
                price INTEGER NOT NULL CHECK (price > 0),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                commission INTEGER NOT NULL DEFAULT 0 CHECK (commission >= 0),
                stamp_tax INTEGER NOT NULL DEFAULT 0 CHECK (stamp_tax >= 0),
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE,
                FOREIGN KEY (symbol) REFERENCES instruments(symbol)
            )
            """,
            """
            CREATE INDEX transactions_portfolio_date_idx
            ON transactions(portfolio_id, trade_date, id)
            """,
            """
            CREATE TABLE positions (
                portfolio_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                cost_basis INTEGER NOT NULL CHECK (cost_basis >= 0),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (portfolio_id, symbol),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE,
                FOREIGN KEY (symbol) REFERENCES instruments(symbol)
            )
            """,
            """
            CREATE TABLE portfolio_equity (
                portfolio_id INTEGER NOT NULL,
                trade_date TEXT NOT NULL,
                cash INTEGER NOT NULL,
                market_value INTEGER NOT NULL CHECK (market_value >= 0),
                total_assets INTEGER NOT NULL CHECK (total_assets >= 0),
                net_value TEXT NOT NULL,
                daily_return TEXT,
                drawdown TEXT NOT NULL,
                PRIMARY KEY (portfolio_id, trade_date),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
            )
            """,
        ),
    ),
    (
        2,
        (
            """
            ALTER TABLE transactions
            ADD COLUMN request_id TEXT
            """,
            """
            CREATE UNIQUE INDEX transactions_portfolio_request_id_uq
            ON transactions(portfolio_id, request_id)
            WHERE request_id IS NOT NULL
            """,
        ),
    ),
    (
        3,
        (
            """
            ALTER TABLE portfolio_equity
            ADD COLUMN ledger_revision INTEGER NOT NULL DEFAULT 0
            CHECK (ledger_revision >= 0)
            """,
        ),
    ),
    (
        4,
        (
            """
            ALTER TABLE portfolio_equity
            ADD COLUMN market_fingerprint TEXT NOT NULL DEFAULT ''
            """,
        ),
    ),
)


def initialize_database(db_path: str | Path | None = None) -> Path:
    database_path = Path(db_path) if db_path is not None else None
    connection = connect_database(database_path)
    try:
        _create_migration_table(connection)
        applied_versions = {
            int(row["version"])
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        for version, statements in MIGRATIONS:
            if version in applied_versions:
                continue
            _apply_migration(connection, version, statements)
    finally:
        connection.close()
    return resolve_database_path(database_path)


def _create_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _apply_migration(
    connection: sqlite3.Connection,
    version: int,
    statements: tuple[str, ...],
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (version,),
        ).fetchone()
        if existing is not None:
            connection.commit()
            return
        for statement in statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (version, _utc_now()),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
