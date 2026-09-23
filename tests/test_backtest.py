from __future__ import annotations

import math
import unittest
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from backtest.engine import BacktestConfig, run_backtest
from backtest.formatter import format_backtest_result
from data.market_rules import price_limit_value
from data.models import PriceVolumeBar


BUY_ADVICE = SimpleNamespace(score=90, action="BUY")
HOLD_ADVICE = SimpleNamespace(sell_risk_score=0, action="HOLD")
SELL_ADVICE = SimpleNamespace(sell_risk_score=80, action="TAKE_PROFIT")


class BacktestTest(unittest.TestCase):
    def test_default_stamp_duty_uses_current_rate(self) -> None:
        self.assertEqual(BacktestConfig().stamp_duty_rate, 0.0005)

    def test_buy_signal_executes_on_next_open_and_records_signal_date(self) -> None:
        bars = _build_backtest_bars(46)
        config = BacktestConfig(
            buy_score_threshold=60,
            sell_risk_threshold=80,
            slippage_rate=0,
            liquidate_at_end=False,
        )

        with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE):
            result = run_backtest("600519", bars, "knn", config)

        self.assertEqual(result.trade_count, 1)
        self.assertEqual(result.closed_trade_count, 0)
        self.assertEqual(result.open_position_count, 1)
        self.assertIsNone(result.win_rate)
        self.assertEqual(result.trades[0].side, "BUY")
        self.assertEqual(result.trades[0].signal_date, bars[44].trade_date.isoformat())
        self.assertEqual(result.trades[0].trade_date, bars[45].trade_date.isoformat())
        self.assertEqual(result.equity_curve[0].trade_date, bars[45].trade_date.isoformat())

    def test_default_final_liquidation_deducts_sell_fee(self) -> None:
        bars = _build_backtest_bars(47)
        config = BacktestConfig(
            commission_rate=0,
            min_commission=0,
            stamp_duty_rate=0.0005,
            slippage_rate=0,
        )

        with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE), patch(
            "backtest.engine.build_sell_advice", return_value=HOLD_ADVICE,
        ):
            result = run_backtest("600519", bars, "knn", config)

        buy_trade, sell_trade = result.trades
        expected_equity = (
            config.initial_cash
            - buy_trade.amount
            - buy_trade.fee
            + sell_trade.amount
            - sell_trade.fee
        )
        self.assertEqual([trade.side for trade in result.trades], ["BUY", "SELL"])
        self.assertIn("期末", sell_trade.reason)
        self.assertEqual(result.closed_trade_count, 1)
        self.assertEqual(result.open_position_count, 0)
        self.assertAlmostEqual(result.final_equity, expected_equity, places=2)
        self.assertAlmostEqual(sell_trade.fee, sell_trade.amount * 0.0005, delta=0.01)

    def test_final_liquidation_respects_t_plus_one(self) -> None:
        bars = _build_backtest_bars(46)
        with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE):
            result = run_backtest("600519", bars, "knn")
        self.assertEqual([trade.side for trade in result.trades], ["BUY"])
        self.assertEqual(result.open_position_count, 1)
        self.assertTrue(any("T+1" in note for note in result.notes))

    def test_final_liquidation_respects_limit_down(self) -> None:
        bars = _build_backtest_bars(47)
        price = price_limit_value(bars[-2].close, 0.1, "DOWN")
        bars[-1] = _bar_with_prices(bars[-1], price, price)
        with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE), patch(
            "backtest.engine.build_sell_advice", return_value=HOLD_ADVICE,
        ):
            result = run_backtest("600519", bars, "knn")
        self.assertEqual([trade.side for trade in result.trades], ["BUY"])
        self.assertEqual(result.open_position_count, 1)

    def test_main_board_limit_up_skips_buy_but_growth_board_ten_percent_does_not(self) -> None:
        bars = _build_backtest_bars(46)
        previous_close = bars[44].close
        open_price = price_limit_value(previous_close, 0.10, "UP")
        bars[45] = _bar_with_prices(bars[45], open_price, open_price)
        config = BacktestConfig(slippage_rate=0, liquidate_at_end=False)

        with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE):
            main_board_result = run_backtest("600519", bars, "knn", config)
            growth_board_result = run_backtest("300001", bars, "knn", config)

        self.assertEqual(main_board_result.trade_count, 0)
        self.assertEqual(growth_board_result.trade_count, 1)

    def test_twenty_and_thirty_percent_boards_use_their_own_limits(self) -> None:
        config = BacktestConfig(slippage_rate=0, liquidate_at_end=False)
        cases = [("688001", 0.20), ("830001", 0.30)]

        for symbol, rate in cases:
            with self.subTest(symbol=symbol):
                bars = _build_backtest_bars(46)
                previous_close = bars[44].close
                open_price = price_limit_value(previous_close, rate, "UP")
                bars[45] = _bar_with_prices(bars[45], open_price, open_price)
                with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE):
                    result = run_backtest(symbol, bars, "knn", config)
                self.assertEqual(result.trade_count, 0)

    def test_zero_volume_and_invalid_open_do_not_trade(self) -> None:
        cases = [
            {"volume": 0},
            {"open": 0},
            {"open": 20},
        ]
        config = BacktestConfig(slippage_rate=0, liquidate_at_end=False)

        for changes in cases:
            with self.subTest(changes=changes):
                bars = _build_backtest_bars(46)
                bars[45] = _replace_bar(bars[45], **changes)
                with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE):
                    result = run_backtest("600519", bars, "knn", config)
                self.assertEqual(result.trade_count, 0)

    def test_slippage_prices_are_bounded_by_daily_high_and_low(self) -> None:
        bars = _build_backtest_bars(47)
        bars[45] = _bar_with_prices(bars[45], 10.0, 10.1, low=9.9, high=10.2)
        bars[46] = _bar_with_prices(bars[46], 11.0, 11.0, low=10.9, high=11.1)
        config = BacktestConfig(
            slippage_rate=0.10,
            commission_rate=0,
            stamp_duty_rate=0,
            min_commission=0,
            liquidate_at_end=False,
        )

        with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE):
            with patch("backtest.engine.build_sell_advice", return_value=SELL_ADVICE):
                result = run_backtest("600519", bars, "knn", config)

        self.assertEqual(result.trades[0].price, bars[45].high)
        self.assertEqual(result.trades[1].price, bars[46].low)

    def test_round_trip_metrics_cover_win_and_loss(self) -> None:
        bars = _build_backtest_bars(49)
        bars[45] = _bar_with_prices(bars[45], 10.0, 10.1)
        bars[46] = _bar_with_prices(bars[46], 12.0, 12.0)
        bars[47] = _bar_with_prices(bars[47], 10.0, 10.0)
        bars[48] = _bar_with_prices(bars[48], 9.5, 9.5)
        config = BacktestConfig(
            slippage_rate=0,
            commission_rate=0,
            stamp_duty_rate=0,
            min_commission=0,
            liquidate_at_end=False,
        )

        with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE):
            with patch("backtest.engine.build_sell_advice", return_value=SELL_ADVICE):
                result = run_backtest("600519", bars, "knn", config)

        self.assertEqual(result.trade_count, 4)
        self.assertEqual(result.closed_trade_count, 2)
        self.assertEqual(result.open_position_count, 0)
        self.assertEqual(result.win_rate, 0.5)
        self.assertGreater(result.average_win, 0)
        self.assertLess(result.average_loss, 0)
        self.assertGreater(result.profit_factor, 1)
        self.assertAlmostEqual(
            result.expectancy,
            sum(trade.pnl for trade in result.round_trips) / 2,
        )
        self.assertEqual(result.exposure_rate, 0.5)
        self.assertGreater(result.sharpe_ratio, 0)
        self.assertGreater(result.calmar_ratio, 0)
        self.assertTrue(all(point.drawdown >= 0 for point in result.equity_curve))
        self.assertGreater(result.max_drawdown, 0)

    def test_failed_final_liquidation_keeps_open_position(self) -> None:
        bars = _build_backtest_bars(47)
        bars[46] = _replace_bar(bars[46], volume=0)
        config = BacktestConfig(slippage_rate=0)

        with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE):
            with patch("backtest.engine.build_sell_advice", return_value=HOLD_ADVICE):
                result = run_backtest("600519", bars, "knn", config)

        self.assertEqual(result.trade_count, 1)
        self.assertEqual(result.open_position_count, 1)
        self.assertEqual(result.closed_trade_count, 0)
        self.assertIsNone(result.win_rate)
        self.assertTrue(any("未能强制平仓" in note for note in result.notes))

    def test_formatter_handles_empty_trade_metrics(self) -> None:
        bars = _build_backtest_bars(46)
        config = BacktestConfig(slippage_rate=0, liquidate_at_end=False)

        with patch("backtest.engine._build_historical_buy_advice", return_value=BUY_ADVICE):
            result = run_backtest("600519", bars, "knn", config)
        text = format_backtest_result(result)

        self.assertIn("胜率：暂无已平仓交易", text)
        self.assertIn(f"信号日 {bars[44].trade_date.isoformat()}", text)
        self.assertIn("未平仓头寸数：1", text)

    def test_invalid_config_is_rejected(self) -> None:
        bars = _build_backtest_bars(46)
        invalid_configs = [
            BacktestConfig(initial_cash=math.nan),
            BacktestConfig(initial_cash=math.inf),
            BacktestConfig(max_position_rate=1.5),
            BacktestConfig(max_position_rate=math.nan),
            BacktestConfig(buy_score_threshold=-1),
            BacktestConfig(sell_risk_threshold=101),
            replace(BacktestConfig(), buy_score_threshold=60.5),
            BacktestConfig(commission_rate=0.11),
            BacktestConfig(stamp_duty_rate=math.inf),
            BacktestConfig(slippage_rate=0.11),
            BacktestConfig(min_commission=math.nan),
            BacktestConfig(lot_size=0),
            replace(BacktestConfig(), lot_size=1.5),
            replace(BacktestConfig(), liquidate_at_end=1),
        ]

        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    run_backtest("600519", bars, "knn", config)


def _build_backtest_bars(count: int) -> list[PriceVolumeBar]:
    start = date(2026, 1, 1)
    bars = []
    for index in range(count):
        close = 10 + index * 0.02
        bars.append(
            PriceVolumeBar(
                name="测试股票",
                trade_date=start + timedelta(days=index),
                open=close - 0.01,
                high=close + 0.08,
                low=close - 0.08,
                close=close,
                volume=1000,
                amount=80_000_000,
            )
        )
    return bars


def _bar_with_prices(
    bar: PriceVolumeBar,
    open_price: float,
    close_price: float,
    *,
    low: float | None = None,
    high: float | None = None,
) -> PriceVolumeBar:
    return _replace_bar(
        bar,
        open=open_price,
        close=close_price,
        low=low if low is not None else min(open_price, close_price) - 0.1,
        high=high if high is not None else max(open_price, close_price) + 0.1,
    )


def _replace_bar(bar: PriceVolumeBar, **changes) -> PriceVolumeBar:
    values = {
        "name": bar.name,
        "trade_date": bar.trade_date,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "amount": bar.amount,
    }
    values.update(changes)
    return PriceVolumeBar(**values)


if __name__ == "__main__":
    unittest.main()
