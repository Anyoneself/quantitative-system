from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from backtest.engine import BacktestConfig, run_backtest
from data.models import PriceVolumeBar


class BacktestTest(unittest.TestCase):
    def test_buy_signal_executes_on_next_open(self) -> None:
        bars = _build_backtest_bars(46)
        config = BacktestConfig(buy_score_threshold=60, sell_risk_threshold=80, slippage_rate=0)

        with patch("backtest.engine._build_historical_buy_advice", return_value=SimpleNamespace(score=90, action="BUY")):
            result = run_backtest("600519", bars, "knn", config)

        self.assertEqual(result.trade_count, 1)
        self.assertEqual(result.trades[0].side, "BUY")
        self.assertEqual(result.trades[0].trade_date, bars[45].trade_date.isoformat())
        self.assertEqual(result.equity_curve[0].trade_date, bars[45].trade_date.isoformat())

    def test_limit_up_open_skips_buy(self) -> None:
        bars = _build_backtest_bars(46)
        signal_close = bars[44].close
        bars[45] = _replace_bar(bars[45], open=signal_close * 1.10, close=signal_close * 1.10)
        config = BacktestConfig(slippage_rate=0)

        with patch("backtest.engine._build_historical_buy_advice", return_value=SimpleNamespace(score=90, action="BUY")):
            result = run_backtest("600519", bars, "knn", config)

        self.assertEqual(result.trade_count, 0)
        self.assertEqual(result.final_equity, config.initial_cash)

    def test_sell_signal_closes_position_and_counts_win(self) -> None:
        bars = _build_backtest_bars(47)
        bars[45] = _replace_bar(bars[45], open=10.0, close=10.2)
        bars[46] = _replace_bar(bars[46], open=11.0, close=11.0)
        config = BacktestConfig(
            buy_score_threshold=60,
            sell_risk_threshold=45,
            slippage_rate=0,
            commission_rate=0,
            stamp_duty_rate=0,
            min_commission=0,
        )
        buy_advice = SimpleNamespace(score=90, action="BUY")
        sell_advice = SimpleNamespace(sell_risk_score=80, action="TAKE_PROFIT")

        with patch("backtest.engine._build_historical_buy_advice", return_value=buy_advice):
            with patch("backtest.engine.build_sell_advice", return_value=sell_advice):
                result = run_backtest("600519", bars, "knn", config)

        self.assertEqual([trade.side for trade in result.trades], ["BUY", "SELL"])
        self.assertEqual(result.trades[-1].position_after, 0)
        self.assertGreater(result.final_equity, config.initial_cash)
        self.assertEqual(result.win_rate, 1.0)

    def test_invalid_config_is_rejected(self) -> None:
        bars = _build_backtest_bars(46)
        config = BacktestConfig(max_position_rate=1.5)

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
