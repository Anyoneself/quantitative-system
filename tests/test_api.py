from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from api.portfolio import (
    build_create_portfolio_response,
    build_dashboard_response,
    build_portfolio_detail_response,
    build_record_transaction_response,
)
from api.research import build_backtest_response, build_evaluation_response
from data.errors import DataRefreshError
from portfolio.performance import rebuild_portfolio_equity
from portfolio.service import get_portfolio, record_transaction
from storage.market_repository import save_daily_bars, save_instrument
from storage.models import DailyBarRecord, InstrumentRecord


class PortfolioApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "api.db"
        response = build_create_portfolio_response(
            {
                "name": "稳健组合",
                "initial_cash": 100_000,
                "base_date": "2026-01-01",
            },
            self.db_path,
        )
        self.portfolio_id = response["portfolio"]["id"]

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_empty_portfolio_dashboard_uses_initial_cash(self) -> None:
        updated_at = get_portfolio(self.portfolio_id, self.db_path).updated_at
        dashboard = build_dashboard_response(self.portfolio_id, self.db_path)["dashboard"]
        build_portfolio_detail_response(self.portfolio_id, self.db_path)

        self.assertEqual(dashboard["summary"]["total_assets"], 100_000)
        self.assertIsNone(dashboard["summary"]["daily_profit"])
        self.assertEqual(dashboard["risk"]["risk_level"], "NO_DATA")
        self.assertEqual(
            get_portfolio(self.portfolio_id, self.db_path).updated_at,
            updated_at,
        )

    def test_transaction_response_uses_yuan_and_records_default_fees(self) -> None:
        with (
            patch("api.portfolio._refresh_symbol"),
            patch("api.portfolio._validate_saved_trade_date"),
            patch(
                "api.portfolio._refresh_after_transaction",
                return_value="行情暂不可用",
            ),
        ):
            response = build_record_transaction_response(
                self.portfolio_id,
                {
                    "request_id": "00000000-0000-4000-8000-000000000001",
                    "symbol": "600519",
                    "side": "BUY",
                    "trade_date": "2026-01-02",
                    "price": 10,
                    "quantity": 100,
                },
                self.db_path,
            )

        detail = build_portfolio_detail_response(self.portfolio_id, self.db_path)
        self.assertEqual(response["transaction"]["commission"], 5)
        self.assertEqual(response["transaction"]["stamp_tax"], 0)
        self.assertEqual(detail["summary"]["cash"], 98_995)
        self.assertEqual(detail["positions"][0]["average_cost"], 10.05)
        self.assertIn("warning", response)

    def test_same_request_id_returns_original_without_refetching(self) -> None:
        payload = {
            "request_id": "00000000-0000-4000-8000-000000000010",
            "symbol": "600519",
            "side": "BUY",
            "trade_date": "2026-01-02",
            "price": 10,
            "quantity": 100,
        }
        with (
            patch("api.portfolio._refresh_symbol"),
            patch("api.portfolio._validate_saved_trade_date"),
            patch("api.portfolio._refresh_after_transaction", return_value=None),
        ):
            first = build_record_transaction_response(
                self.portfolio_id,
                payload,
                self.db_path,
            )

        with patch("api.portfolio._refresh_symbol") as refresh_mock:
            repeated = build_record_transaction_response(
                self.portfolio_id,
                payload,
                self.db_path,
            )

        refresh_mock.assert_not_called()
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(repeated["idempotent_replay"])
        self.assertEqual(first["transaction"]["id"], repeated["transaction"]["id"])
        detail = build_portfolio_detail_response(self.portfolio_id, self.db_path)
        self.assertEqual(detail["summary"]["transaction_count"], 1)

    def test_concurrent_same_request_id_reports_one_replay(self) -> None:
        payload = {
            "request_id": "00000000-0000-4000-8000-000000000015",
            "symbol": "600519",
            "side": "BUY",
            "trade_date": "2026-01-02",
            "price": 10,
            "quantity": 100,
        }
        barrier = threading.Barrier(2)

        def synchronize_refresh(*_args) -> None:
            barrier.wait(timeout=5)

        with (
            patch(
                "api.portfolio.find_transaction_by_request_id",
                return_value=None,
            ),
            patch(
                "api.portfolio._refresh_symbol",
                side_effect=synchronize_refresh,
            ),
            patch("api.portfolio._validate_saved_trade_date"),
            patch("api.portfolio._refresh_after_transaction", return_value=None),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(
                    executor.map(
                        lambda _: build_record_transaction_response(
                            self.portfolio_id,
                            payload,
                            self.db_path,
                        ),
                        range(2),
                    )
                )

        self.assertEqual(
            sorted(response["idempotent_replay"] for response in responses),
            [False, True],
        )
        self.assertEqual(
            len({response["transaction"]["id"] for response in responses}),
            1,
        )
        detail = build_portfolio_detail_response(self.portfolio_id, self.db_path)
        self.assertEqual(detail["summary"]["transaction_count"], 1)

    def test_request_id_is_required_by_public_api(self) -> None:
        with patch("api.portfolio._refresh_symbol") as refresh_mock:
            with self.assertRaisesRegex(ValueError, "request_id"):
                build_record_transaction_response(
                    self.portfolio_id,
                    {
                        "symbol": "600519",
                        "side": "BUY",
                        "trade_date": "2026-01-02",
                        "price": 10,
                        "quantity": 100,
                    },
                    self.db_path,
                )
        refresh_mock.assert_not_called()

    def test_buy_quantity_must_use_board_lot(self) -> None:
        with self.assertRaisesRegex(ValueError, "100 股"):
            build_record_transaction_response(
                self.portfolio_id,
                {
                    "request_id": "00000000-0000-4000-8000-000000000002",
                    "symbol": "600519",
                    "side": "BUY",
                    "trade_date": "2026-01-02",
                    "price": 10,
                    "quantity": 99,
                },
                self.db_path,
            )

    def test_weekend_transaction_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "周末"):
            build_record_transaction_response(
                self.portfolio_id,
                {
                    "request_id": "00000000-0000-4000-8000-000000000003",
                    "symbol": "600519",
                    "side": "BUY",
                    "trade_date": "2026-01-03",
                    "price": 10,
                    "quantity": 100,
                },
                self.db_path,
            )

    def test_post_processing_failure_still_returns_created_transaction(self) -> None:
        with patch("api.portfolio._refresh_symbol"), patch(
            "api.portfolio._validate_saved_trade_date"
        ):
            with patch(
                "api.portfolio._refresh_after_transaction",
                side_effect=RuntimeError("测试后处理失败"),
            ):
                with patch(
                    "api.portfolio.build_portfolio_detail_response",
                    side_effect=RuntimeError("测试详情失败"),
                ):
                    response = build_record_transaction_response(
                        self.portfolio_id,
                        {
                            "request_id": "00000000-0000-4000-8000-000000000004",
                            "symbol": "600519",
                            "side": "BUY",
                            "trade_date": "2026-01-02",
                            "price": 10,
                            "quantity": 100,
                            "commission": 0,
                        },
                        self.db_path,
                    )

        self.assertTrue(response["ok"])
        self.assertEqual(response["transaction"]["symbol"], "600519")
        detail = build_portfolio_detail_response(self.portfolio_id, self.db_path)
        self.assertEqual(detail["summary"]["transaction_count"], 1)

    def test_stale_equity_never_overrides_current_ledger(self) -> None:
        with patch("api.portfolio._refresh_symbol"), patch(
            "api.portfolio._validate_saved_trade_date"
        ):
            with patch("api.portfolio._refresh_after_transaction", return_value=None):
                build_record_transaction_response(
                    self.portfolio_id,
                    {
                        "request_id": "00000000-0000-4000-8000-000000000005",
                        "symbol": "600519",
                        "side": "BUY",
                        "trade_date": "2026-01-02",
                        "price": 10,
                        "quantity": 100,
                        "commission": 0,
                    },
                    self.db_path,
                )
        save_instrument(
            InstrumentRecord("600519", "测试股票", "SH"),
            self.db_path,
        )
        save_daily_bars([_daily_bar("600519", "2026-01-02", 900)], self.db_path)
        rebuild_portfolio_equity(self.portfolio_id, self.db_path)

        with patch("api.portfolio._refresh_symbol"), patch(
            "api.portfolio._validate_saved_trade_date"
        ):
            with patch("api.portfolio._refresh_after_transaction", return_value="净值刷新失败"):
                build_record_transaction_response(
                    self.portfolio_id,
                    {
                        "request_id": "00000000-0000-4000-8000-000000000006",
                        "symbol": "600519",
                        "side": "SELL",
                        "trade_date": "2026-01-02",
                        "price": 12,
                        "quantity": 100,
                        "commission": 0,
                        "stamp_tax": 0,
                    },
                    self.db_path,
                )

        detail = build_portfolio_detail_response(self.portfolio_id, self.db_path)
        dashboard = build_dashboard_response(
            self.portfolio_id,
            self.db_path,
        )["dashboard"]
        self.assertEqual(detail["summary"]["total_assets"], 100_200)
        self.assertEqual(detail["positions"], [])
        self.assertTrue(detail["summary"]["equity_stale"])
        self.assertEqual(detail["equity"], [])
        self.assertEqual(dashboard["risk"]["risk_level"], "STALE")
        self.assertTrue(dashboard["risk"]["stale"])
        self.assertEqual(dashboard["risk"]["sample_status"], "净值待刷新")
        self.assertIsNone(dashboard["summary"]["max_drawdown"])
        self.assertEqual(dashboard["equity"], [])

    def test_equity_revision_detects_round_trip_with_same_terminal_assets(self) -> None:
        save_instrument(
            InstrumentRecord("000300", "沪深300", "SH", instrument_type="INDEX"),
            self.db_path,
        )
        save_daily_bars(
            [
                _daily_bar("000300", "2026-01-05", 4_000),
                _daily_bar("000300", "2026-01-06", 4_010),
            ],
            self.db_path,
        )
        rebuild_portfolio_equity(self.portfolio_id, self.db_path)

        payloads = [
            {
                "request_id": "00000000-0000-4000-8000-000000000011",
                "symbol": "600519",
                "side": "BUY",
                "trade_date": "2026-01-05",
                "price": 10,
                "quantity": 100,
                "commission": 0,
                "stamp_tax": 0,
            },
            {
                "request_id": "00000000-0000-4000-8000-000000000012",
                "symbol": "600519",
                "side": "SELL",
                "trade_date": "2026-01-06",
                "price": 10,
                "quantity": 100,
                "commission": 0,
                "stamp_tax": 0,
            },
        ]
        for payload in payloads:
            with (
                patch("api.portfolio._refresh_symbol"),
                patch("api.portfolio._validate_saved_trade_date"),
                patch(
                    "api.portfolio._refresh_after_transaction",
                    side_effect=RuntimeError("测试净值中断"),
                ),
            ):
                build_record_transaction_response(
                    self.portfolio_id,
                    payload,
                    self.db_path,
                )

        stale_detail = build_portfolio_detail_response(
            self.portfolio_id,
            self.db_path,
        )
        stale_dashboard = build_dashboard_response(
            self.portfolio_id,
            self.db_path,
        )["dashboard"]
        self.assertEqual(stale_detail["summary"]["total_assets"], 100_000)
        self.assertTrue(stale_detail["summary"]["equity_stale"])
        self.assertEqual(stale_dashboard["risk"]["risk_level"], "STALE")
        self.assertEqual(stale_dashboard["equity"], [])

        save_instrument(
            InstrumentRecord("600519", "测试股票", "SH"),
            self.db_path,
        )
        save_daily_bars(
            [
                _daily_bar("600519", "2026-01-05", 900),
                _daily_bar("600519", "2026-01-06", 1_000),
            ],
            self.db_path,
        )
        rebuilt = rebuild_portfolio_equity(self.portfolio_id, self.db_path)
        current_detail = build_portfolio_detail_response(
            self.portfolio_id,
            self.db_path,
        )
        by_date = {point.trade_date: point for point in rebuilt}
        self.assertEqual(by_date["2026-01-05"].total_assets_cents, 9_990_000)
        self.assertFalse(current_detail["summary"]["equity_stale"])
        self.assertGreater(len(current_detail["equity"]), 0)

        save_daily_bars(
            [_daily_bar("600519", "2026-01-05", 500)],
            self.db_path,
        )
        revised_detail = build_portfolio_detail_response(
            self.portfolio_id,
            self.db_path,
        )
        revised_dashboard = build_dashboard_response(
            self.portfolio_id,
            self.db_path,
        )["dashboard"]
        self.assertTrue(revised_detail["summary"]["equity_stale"])
        self.assertEqual(revised_dashboard["risk"]["risk_level"], "STALE")

        revised = rebuild_portfolio_equity(self.portfolio_id, self.db_path)
        revised_by_date = {point.trade_date: point for point in revised}
        self.assertEqual(
            revised_by_date["2026-01-05"].total_assets_cents,
            9_950_000,
        )

    def test_future_trade_date_is_rejected_without_network_or_write(self) -> None:
        with patch("api.portfolio._refresh_symbol") as refresh_mock:
            with self.assertRaisesRegex(ValueError, "不能晚于今天"):
                build_record_transaction_response(
                    self.portfolio_id,
                    {
                        "request_id": "00000000-0000-4000-8000-000000000007",
                        "symbol": "600519",
                        "side": "BUY",
                        "trade_date": "2099-01-05",
                        "price": 10,
                        "quantity": 100,
                    },
                    self.db_path,
                )
        refresh_mock.assert_not_called()

        detail = build_portfolio_detail_response(self.portfolio_id, self.db_path)
        self.assertEqual(detail["summary"]["transaction_count"], 0)

    def test_unverified_past_trade_date_is_rejected_without_writing(self) -> None:
        with patch("api.portfolio._refresh_symbol"):
            with self.assertRaisesRegex(ValueError, "未复权行情"):
                build_record_transaction_response(
                    self.portfolio_id,
                    {
                        "request_id": "00000000-0000-4000-8000-000000000013",
                        "symbol": "600519",
                        "side": "BUY",
                        "trade_date": "2026-01-05",
                        "price": 10,
                        "quantity": 100,
                    },
                    self.db_path,
                )

        detail = build_portfolio_detail_response(self.portfolio_id, self.db_path)
        self.assertEqual(detail["summary"]["transaction_count"], 0)

    def test_non_canonical_trade_date_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            build_record_transaction_response(
                self.portfolio_id,
                {
                    "request_id": "00000000-0000-4000-8000-000000000014",
                    "symbol": "600519",
                    "side": "BUY",
                    "trade_date": "20260105",
                    "price": 10,
                    "quantity": 100,
                },
                self.db_path,
            )

    def test_market_refresh_failure_blocks_transaction(self) -> None:
        with patch(
            "api.portfolio._refresh_symbol",
            side_effect=DataRefreshError("公开行情不可用"),
        ):
            with self.assertRaises(DataRefreshError):
                build_record_transaction_response(
                    self.portfolio_id,
                    {
                        "request_id": "00000000-0000-4000-8000-000000000008",
                        "symbol": "600519",
                        "side": "BUY",
                        "trade_date": "2026-01-02",
                        "price": 10,
                        "quantity": 100,
                    },
                    self.db_path,
                )

        detail = build_portfolio_detail_response(self.portfolio_id, self.db_path)
        self.assertEqual(detail["summary"]["transaction_count"], 0)

    def test_portfolio_valuation_ignores_adjusted_bars(self) -> None:
        record_transaction(
            self.portfolio_id,
            "600519",
            "BUY",
            "2026-01-02",
            1_000,
            100,
            request_id="00000000-0000-4000-8000-000000000009",
            db_path=self.db_path,
        )
        save_instrument(
            InstrumentRecord("600519", "测试股票", "SH"),
            self.db_path,
        )
        adjusted_bar = _daily_bar("600519", "2026-01-02", 900)
        save_daily_bars(
            [
                DailyBarRecord(
                    **{
                        **adjusted_bar.__dict__,
                        "adjustment_type": "SOURCE_NATIVE",
                    }
                )
            ],
            self.db_path,
        )

        detail = build_portfolio_detail_response(self.portfolio_id, self.db_path)

        self.assertIsNone(detail["positions"][0]["latest_price"])
        self.assertTrue(detail["positions"][0]["estimated"])

    def test_extreme_money_is_rejected_as_validation_error(self) -> None:
        with self.assertRaises(ValueError):
            build_create_portfolio_response(
                {
                    "name": "极端输入",
                    "initial_cash": "1e100000",
                    "base_date": "2026-01-01",
                },
                self.db_path,
            )
        with self.assertRaisesRegex(ValueError, "不能超过"):
            build_create_portfolio_response(
                {
                    "name": "超出精度",
                    "initial_cash": "999999999999999.99",
                    "base_date": "2026-01-01",
                },
                self.db_path,
            )

    def test_control_and_invalid_unicode_text_are_rejected(self) -> None:
        for invalid_name in ("\x00", "组合\x00名称", "\ud800"):
            with self.subTest(invalid_name=repr(invalid_name)):
                with self.assertRaisesRegex(
                    ValueError,
                    "控制字符或无效 Unicode",
                ):
                    build_create_portfolio_response(
                        {
                            "name": invalid_name,
                            "initial_cash": 100_000,
                            "base_date": "2026-01-01",
                        },
                        self.db_path,
                    )

        with self.assertRaisesRegex(ValueError, "控制字符或无效 Unicode"):
            build_record_transaction_response(
                self.portfolio_id,
                {
                    "request_id": "invalid-note-request",
                    "symbol": "600519",
                    "side": "BUY",
                    "trade_date": "2026-01-05",
                    "price": 10,
                    "quantity": 100,
                    "note": "备注\x00内容",
                },
                self.db_path,
            )


class ResearchApiTest(unittest.TestCase):
    def test_invalid_symbol_is_rejected_before_network_request(self) -> None:
        with patch("api.research.fetch_bars") as fetch_mock:
            with self.assertRaisesRegex(ValueError, "6 位"):
                build_evaluation_response({"symbol": "ABC"})
        fetch_mock.assert_not_called()

    def test_invalid_backtest_boolean_is_rejected(self) -> None:
        with patch("api.research.fetch_bars") as fetch_mock:
            with self.assertRaisesRegex(ValueError, "布尔"):
                build_backtest_response(
                    {
                        "symbol": "600519",
                        "liquidate_at_end": "yes",
                    }
                )
        fetch_mock.assert_not_called()


def _daily_bar(
    symbol: str,
    trade_date: str,
    close_price_cents: int,
) -> DailyBarRecord:
    return DailyBarRecord(
        symbol=symbol,
        trade_date=trade_date,
        open_price_cents=close_price_cents,
        high_price_cents=close_price_cents + 10,
        low_price_cents=close_price_cents - 10,
        close_price_cents=close_price_cents,
        volume=1_000,
        amount_cents=close_price_cents * 1_000,
        adjustment_type="NONE",
        source="test",
        fetched_at="2026-01-02T15:00:00+08:00",
    )


if __name__ == "__main__":
    unittest.main()
