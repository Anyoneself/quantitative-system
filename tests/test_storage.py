from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from storage.connection import connect_database
from storage.market_repository import save_daily_bars, save_instrument
from storage.migrations import initialize_database
from storage.models import DailyBarRecord, InstrumentRecord


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "app" / "test.db"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_initialize_database_is_idempotent(self) -> None:
        initialize_database(self.db_path)
        initialize_database(self.db_path)

        connection = connect_database(self.db_path)
        try:
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()

        self.assertEqual([int(row["version"]) for row in versions], [1, 2, 3, 4])
        self.assertTrue(
            {
                "schema_migrations",
                "instruments",
                "daily_bars",
                "portfolios",
                "transactions",
                "positions",
                "portfolio_equity",
            }.issubset(tables)
        )

    def test_transaction_request_id_has_portfolio_scoped_unique_index(self) -> None:
        initialize_database(self.db_path)

        connection = connect_database(self.db_path)
        try:
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(transactions)")
            }
            index = connection.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'index'
                  AND name = 'transactions_portfolio_request_id_uq'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertIn("request_id", columns)
        self.assertIsNotNone(index)
        normalized_sql = " ".join(str(index["sql"]).split())
        self.assertIn("CREATE UNIQUE INDEX", normalized_sql)
        self.assertIn(
            "ON transactions(portfolio_id, request_id)",
            normalized_sql,
        )
        self.assertIn("WHERE request_id IS NOT NULL", normalized_sql)

    def test_connection_enables_foreign_keys_wal_and_busy_timeout(self) -> None:
        initialize_database(self.db_path)
        connection = connect_database(self.db_path)
        try:
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(foreign_keys, 1)
        self.assertEqual(str(journal_mode).lower(), "wal")
        self.assertGreaterEqual(busy_timeout, 5_000)

    def test_concurrent_cold_database_initialization_is_safe(self) -> None:
        with ThreadPoolExecutor(max_workers=12) as executor:
            paths = list(
                executor.map(
                    lambda _: initialize_database(self.db_path),
                    range(24),
                )
            )

        self.assertTrue(all(path == self.db_path for path in paths))
        connection = connect_database(self.db_path)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(integrity, "ok")

    def test_daily_bars_are_upserted_and_foreign_key_is_enforced(self) -> None:
        save_instrument(
            InstrumentRecord("600519", "贵州茅台", "SH"),
            self.db_path,
        )
        first = _bar("600519", "2026-07-28", 150_00)
        updated = _bar("600519", "2026-07-28", 151_00)

        self.assertEqual(save_daily_bars([first], self.db_path), 1)
        self.assertEqual(save_daily_bars([updated], self.db_path), 1)

        connection = connect_database(self.db_path)
        try:
            row = connection.execute(
                "SELECT close_price FROM daily_bars WHERE symbol = ? AND trade_date = ?",
                ("600519", "2026-07-28"),
            ).fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO daily_bars(
                        symbol, trade_date, open_price, high_price, low_price, close_price,
                        volume, amount, adjustment_type, source, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("000001", "2026-07-28", 100, 101, 99, 100, 1, 100, "NONE", "test", "now"),
                )
        finally:
            connection.close()

        self.assertEqual(int(row["close_price"]), 151_00)

    def test_future_daily_bar_is_rejected(self) -> None:
        save_instrument(
            InstrumentRecord("600519", "贵州茅台", "SH"),
            self.db_path,
        )

        with self.assertRaisesRegex(ValueError, "不能晚于今天"):
            save_daily_bars(
                [_bar("600519", "2099-01-05", 150_00)],
                self.db_path,
            )


def _bar(symbol: str, trade_date: str, close_price_cents: int) -> DailyBarRecord:
    return DailyBarRecord(
        symbol=symbol,
        trade_date=trade_date,
        open_price_cents=close_price_cents - 100,
        high_price_cents=close_price_cents + 100,
        low_price_cents=close_price_cents - 200,
        close_price_cents=close_price_cents,
        volume=1_000,
        amount_cents=close_price_cents * 1_000,
        adjustment_type="NONE",
        source="test",
        fetched_at="2026-07-28T15:00:00+08:00",
    )


if __name__ == "__main__":
    unittest.main()
