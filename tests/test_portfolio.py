from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portfolio.performance import rebuild_portfolio_equity
from portfolio.service import (
    MAX_LEDGER_CENTS,
    create_portfolio,
    get_positions,
    get_transactions,
    list_portfolios,
    record_transaction,
    rebuild_positions,
)
from storage.connection import connect_database
from storage.market_repository import save_daily_bars
from storage.models import DailyBarRecord


class PortfolioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "test.db"
        self.portfolio = create_portfolio(
            "测试组合",
            initial_cash_cents=10_000_000,
            base_date="2026-01-01",
            db_path=self.db_path,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_create_list_and_read_portfolio(self) -> None:
        portfolios = list_portfolios(self.db_path)

        self.assertEqual(len(portfolios), 1)
        self.assertEqual(portfolios[0], self.portfolio)
        self.assertEqual(portfolios[0].initial_cash_cents, 10_000_000)

    def test_non_canonical_base_date_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            create_portfolio(
                "日期错误",
                10_000_000,
                "20260101",
                db_path=self.db_path,
            )

    def test_buy_partial_sell_and_rebuild_positions(self) -> None:
        record_transaction(
            self.portfolio.id,
            "600519",
            "BUY",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            commission_cents=500,
            db_path=self.db_path,
        )
        record_transaction(
            self.portfolio.id,
            "600519",
            "SELL",
            "2026-01-03",
            price_cents=1_200,
            quantity=40,
            commission_cents=500,
            stamp_tax_cents=480,
            db_path=self.db_path,
        )

        snapshot = rebuild_positions(self.portfolio.id, self.db_path)

        self.assertEqual(snapshot.cash_cents, 9_946_520)
        self.assertEqual(snapshot.realized_pnls_cents, ())
        self.assertEqual(len(snapshot.positions), 1)
        self.assertEqual(snapshot.positions[0].quantity, 60)
        self.assertEqual(snapshot.positions[0].cost_basis_cents, 60_300)
        self.assertEqual(get_positions(self.portfolio.id, self.db_path), list(snapshot.positions))

    def test_realized_pnl_is_counted_after_full_position_cycle_closes(self) -> None:
        record_transaction(
            self.portfolio.id,
            "600519",
            "BUY",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            commission_cents=500,
            db_path=self.db_path,
        )
        record_transaction(
            self.portfolio.id,
            "600519",
            "SELL",
            "2026-01-03",
            price_cents=1_200,
            quantity=40,
            commission_cents=500,
            stamp_tax_cents=480,
            db_path=self.db_path,
        )
        record_transaction(
            self.portfolio.id,
            "600519",
            "SELL",
            "2026-01-04",
            price_cents=1_100,
            quantity=60,
            commission_cents=500,
            stamp_tax_cents=660,
            db_path=self.db_path,
        )

        snapshot = rebuild_positions(self.portfolio.id, self.db_path)

        self.assertEqual(snapshot.positions, ())
        self.assertEqual(snapshot.realized_pnls_cents, (11_360,))
        self.assertEqual(snapshot.cash_cents, 10_011_360)
        self.assertEqual(
            [transaction.side for transaction in get_transactions(self.portfolio.id, self.db_path)],
            ["BUY", "SELL", "SELL"],
        )

    def test_cash_shortage_rolls_back_transaction(self) -> None:
        with self.assertRaisesRegex(ValueError, "现金不足"):
            record_transaction(
                self.portfolio.id,
                "600519",
                "BUY",
                "2026-01-02",
                price_cents=200_000,
                quantity=100,
                db_path=self.db_path,
            )

        connection = connect_database(self.db_path)
        try:
            count = connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_oversell_rolls_back_transaction(self) -> None:
        record_transaction(
            self.portfolio.id,
            "600519",
            "BUY",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            db_path=self.db_path,
        )

        with self.assertRaisesRegex(ValueError, "超过持仓"):
            record_transaction(
                self.portfolio.id,
                "600519",
                "SELL",
                "2026-01-03",
                price_cents=1_100,
                quantity=101,
                db_path=self.db_path,
            )

        positions = get_positions(self.portfolio.id, self.db_path)
        self.assertEqual(positions[0].quantity, 100)

    def test_cumulative_cash_overflow_rolls_back_sell(self) -> None:
        maximum_portfolio = create_portfolio(
            "上限组合",
            MAX_LEDGER_CENTS,
            "2026-01-01",
            db_path=self.db_path,
        )
        record_transaction(
            maximum_portfolio.id,
            "600519",
            "BUY",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            request_id="ledger-limit-buy",
            db_path=self.db_path,
        )

        with self.assertRaisesRegex(ValueError, "组合现金.*安全上限"):
            record_transaction(
                maximum_portfolio.id,
                "600519",
                "SELL",
                "2026-01-05",
                price_cents=1_100,
                quantity=100,
                request_id="ledger-limit-sell",
                db_path=self.db_path,
            )

        transactions = get_transactions(maximum_portfolio.id, self.db_path)
        positions = get_positions(maximum_portfolio.id, self.db_path)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(positions[0].quantity, 100)

    def test_same_request_id_returns_original_transaction(self) -> None:
        first = record_transaction(
            self.portfolio.id,
            "600519",
            "BUY",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            commission_cents=500,
            note="首笔买入",
            request_id=" trade-buy-001 ",
            db_path=self.db_path,
        )
        repeated = record_transaction(
            self.portfolio.id,
            "600519",
            "buy",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            commission_cents=500,
            note=" 首笔买入 ",
            request_id="trade-buy-001",
            db_path=self.db_path,
        )

        self.assertEqual(repeated, first)
        self.assertEqual(first.request_id, "trade-buy-001")
        self.assertEqual(len(get_transactions(self.portfolio.id, self.db_path)), 1)
        self.assertEqual(get_positions(self.portfolio.id, self.db_path)[0].quantity, 100)

    def test_same_request_id_with_different_content_is_rejected(self) -> None:
        record_transaction(
            self.portfolio.id,
            "600519",
            "BUY",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            request_id="trade-conflict-001",
            db_path=self.db_path,
        )

        with self.assertRaisesRegex(ValueError, "request_id.*不同"):
            record_transaction(
                self.portfolio.id,
                "600519",
                "BUY",
                "2026-01-02",
                price_cents=1_001,
                quantity=100,
                request_id="trade-conflict-001",
                db_path=self.db_path,
            )

        self.assertEqual(len(get_transactions(self.portfolio.id, self.db_path)), 1)
        self.assertEqual(get_positions(self.portfolio.id, self.db_path)[0].quantity, 100)

    def test_request_id_is_unique_within_each_portfolio(self) -> None:
        second_portfolio = create_portfolio(
            "第二组合",
            initial_cash_cents=10_000_000,
            base_date="2026-01-01",
            db_path=self.db_path,
        )

        first = record_transaction(
            self.portfolio.id,
            "600519",
            "BUY",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            request_id="shared-request-id",
            db_path=self.db_path,
        )
        second = record_transaction(
            second_portfolio.id,
            "600519",
            "BUY",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            request_id="shared-request-id",
            db_path=self.db_path,
        )

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.request_id, second.request_id)

    def test_rebuild_equity_uses_only_current_or_previous_prices(self) -> None:
        record_transaction(
            self.portfolio.id,
            "600519",
            "BUY",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            db_path=self.db_path,
        )
        save_daily_bars(
            [
                _bar("2026-01-02", 1_100),
                _bar("2026-01-04", 900),
            ],
            self.db_path,
        )

        points = rebuild_portfolio_equity(self.portfolio.id, self.db_path)
        by_date = {point.trade_date: point for point in points}

        self.assertEqual(by_date["2026-01-02"].market_value_cents, 110_000)
        self.assertEqual(by_date["2026-01-04"].market_value_cents, 90_000)
        self.assertAlmostEqual(by_date["2026-01-04"].net_value, 0.999)
        self.assertLess(by_date["2026-01-04"].drawdown, 0)

    def test_rebuild_equity_rejects_future_price(self) -> None:
        record_transaction(
            self.portfolio.id,
            "600519",
            "BUY",
            "2026-01-02",
            price_cents=1_000,
            quantity=100,
            db_path=self.db_path,
        )
        save_daily_bars([_bar("2026-01-03", 1_100)], self.db_path)

        with self.assertRaisesRegex(ValueError, "当日及之前"):
            rebuild_portfolio_equity(self.portfolio.id, self.db_path)


def _bar(trade_date: str, close_price_cents: int) -> DailyBarRecord:
    return DailyBarRecord(
        symbol="600519",
        trade_date=trade_date,
        open_price_cents=close_price_cents,
        high_price_cents=close_price_cents + 10,
        low_price_cents=close_price_cents - 10,
        close_price_cents=close_price_cents,
        volume=1_000,
        amount_cents=close_price_cents * 1_000,
        adjustment_type="NONE",
        source="test",
        fetched_at="2026-01-04T15:00:00+08:00",
    )


if __name__ == "__main__":
    unittest.main()
