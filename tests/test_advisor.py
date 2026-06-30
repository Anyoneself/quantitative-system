from __future__ import annotations

import unittest
import threading
from concurrent.futures import Future
from unittest.mock import patch

from analysis.chan_theory import analyze_chan_structure
from analysis.advisor import generate_advice
from analysis.divergence import analyze_divergence
from analysis.fund_flow import analyze_fund_flow
from analysis.indicators import calculate_indicators
from analysis.market_scanner import MarketScanner
from analysis.ml_model import predict_next_day_buy_probability
from analysis.scanner import ScanResult, format_scan_report, scan_top_stocks
from analysis.service import build_advice
from analysis.sell_advisor import build_sell_advice
from analysis.patterns import detect_patterns
from data.errors import DataRefreshError
from data.models import FundFlowBar, PriceVolumeBar, StockInfo
from output.formatter import format_advice


class AdvisorTest(unittest.TestCase):
    def test_limit_up_low_volume_gets_positive_score(self) -> None:
        bars = _build_bars_for_limit_up()
        indicators = calculate_indicators("600519", bars)
        self.assertIsNotNone(indicators)

        patterns = detect_patterns(indicators)
        prediction = predict_next_day_buy_probability(bars)
        advice = generate_advice(indicators, patterns, prediction)

        self.assertTrue(patterns.limit_up_low_volume)
        self.assertIsNotNone(prediction)
        self.assertIn(advice.action, {"BUY", "WATCH", "HOLD", "AVOID"})
        self.assertGreaterEqual(advice.score, 0)
        self.assertIn("股票名称：贵州茅台", format_advice(advice))
        self.assertIn("样本区间：最近 21 个交易日", format_advice(advice))
        self.assertIn("机器学习预测：", format_advice(advice))
        self.assertIn("数据证据：", format_advice(advice))
        self.assertTrue(advice.evidence)

    def test_ml_prediction_returns_probability(self) -> None:
        bars = _build_bars_for_limit_up()

        prediction = predict_next_day_buy_probability(bars, "weighted_knn")

        self.assertIsNotNone(prediction)
        self.assertEqual(prediction.algorithm, "weighted_knn")
        self.assertGreaterEqual(prediction.buy_probability, 0)
        self.assertLessEqual(prediction.buy_probability, 1)
        self.assertEqual(prediction.neighbor_count, 7)

    def test_knn_neighbor_count_grows_with_sample_count(self) -> None:
        bars = _build_bars(250)

        prediction = predict_next_day_buy_probability(bars, "knn")

        self.assertIsNotNone(prediction)
        self.assertEqual(prediction.sample_count, 229)
        self.assertEqual(prediction.neighbor_count, 15)

    def test_chan_structure_detects_points_strokes_and_center(self) -> None:
        bars = _build_chan_bars()

        result = analyze_chan_structure(bars)

        self.assertGreaterEqual(len(result.points), 4)
        self.assertGreaterEqual(len(result.strokes), 3)
        self.assertIsNotNone(result.center)
        self.assertIn(result.buy_signal, {"二买候选", "三买候选", "无"})
        self.assertTrue(result.recommendation)

    def test_build_advice_includes_chan_structure(self) -> None:
        bars = _build_bars(250)

        advice = build_advice("600519", bars, "knn")

        self.assertIsNotNone(advice.chan_structure)
        self.assertTrue(any("缠论结构" in item for item in advice.evidence))
        self.assertIn("缠论结构辅助", format_advice(advice))

    def test_yearline_below_adds_risk_and_score_penalty(self) -> None:
        bars = _build_yearline_bars(last_close=9.0, last_volume=900)

        indicators = calculate_indicators("600519", bars)
        self.assertIsNotNone(indicators)
        patterns = detect_patterns(indicators)
        advice = build_advice("600519", bars, "knn")

        self.assertFalse(patterns.close_above_ma250)
        self.assertTrue(any("低于年线" in item for item in advice.risks))
        self.assertTrue(any("年线" in item for item in advice.evidence))

    def test_yearline_pullback_low_volume_adds_positive_reason(self) -> None:
        bars = _build_yearline_bars(last_close=10.35, last_volume=500)

        indicators = calculate_indicators("600519", bars)
        self.assertIsNotNone(indicators)
        patterns = detect_patterns(indicators)
        advice = build_advice("600519", bars, "knn")

        self.assertTrue(patterns.yearline_pullback_low_volume)
        self.assertTrue(any("强势股回档" in item for item in advice.reasons))
        self.assertIn("年线 MA250", format_advice(advice))

    def test_low_position_rebound_signal_adds_buy_reason(self) -> None:
        bars = _build_rebound_bars()

        indicators = calculate_indicators("600519", bars)
        self.assertIsNotNone(indicators)
        patterns = detect_patterns(indicators)
        prediction = predict_next_day_buy_probability(bars, "knn")
        advice = generate_advice(indicators, patterns, prediction)

        self.assertTrue(patterns.long_lower_shadow)
        self.assertTrue(patterns.low_position_rebound)
        self.assertTrue(any("止跌反弹" in item or "下影线" in item for item in advice.reasons))

    def test_low_position_rebound_reduces_sell_risk(self) -> None:
        bars = _build_rebound_bars()

        advice = build_sell_advice("600519", bars, cost_price=10.5)

        self.assertTrue(any("止跌反弹" in item for item in advice.reasons))

    def test_macd_golden_cross_adds_buy_reason(self) -> None:
        bars = _build_macd_cross_bars(golden=True)

        indicators = calculate_indicators("600519", bars)
        self.assertIsNotNone(indicators)
        patterns = detect_patterns(indicators)
        prediction = predict_next_day_buy_probability(bars, "knn")
        advice = generate_advice(indicators, patterns, prediction)

        self.assertTrue(patterns.macd_golden_cross)
        self.assertTrue(any("MACD" in item and "金叉" in item for item in advice.reasons))

    def test_macd_dead_cross_adds_sell_risk(self) -> None:
        bars = _build_macd_cross_bars(golden=False)

        indicators = calculate_indicators("600519", bars)
        self.assertIsNotNone(indicators)
        patterns = detect_patterns(indicators)
        sell_advice = build_sell_advice("600519", bars, cost_price=10.0)

        self.assertTrue(patterns.macd_dead_cross)
        self.assertTrue(any("MACD" in item and "死叉" in item for item in sell_advice.risks))

    def test_bullish_divergence_supports_buy_score(self) -> None:
        bars = _build_divergence_bars(bullish=True)

        divergence = analyze_divergence(bars)
        self.assertIsNotNone(divergence)

        self.assertTrue(divergence.bullish_divergence)
        self.assertGreater(divergence.bullish_score_adjustment, 0)

    def test_bearish_divergence_supports_sell_risk(self) -> None:
        bars = _build_divergence_bars(bullish=False)

        divergence = analyze_divergence(bars)
        self.assertIsNotNone(divergence)

        self.assertTrue(divergence.bearish_divergence)
        self.assertGreater(divergence.bearish_risk_adjustment, 0)

    def test_no_clear_divergence_returns_neutral_signal(self) -> None:
        bars = _build_no_divergence_bars()

        divergence = analyze_divergence(bars)
        self.assertIsNotNone(divergence)

        self.assertFalse(divergence.bullish_divergence)
        self.assertFalse(divergence.bearish_divergence)

    def test_logistic_regression_prediction_returns_probability(self) -> None:
        bars = _build_bars_for_limit_up()

        prediction = predict_next_day_buy_probability(bars, "logistic_regression")

        self.assertIsNotNone(prediction)
        self.assertEqual(prediction.algorithm, "logistic_regression")
        self.assertGreaterEqual(prediction.buy_probability, 0)
        self.assertLessEqual(prediction.buy_probability, 1)

    def test_robust_ensemble_uses_five_day_horizon(self) -> None:
        bars = _build_bars(260)

        prediction = predict_next_day_buy_probability(bars, "robust_ensemble")

        self.assertIsNotNone(prediction)
        self.assertEqual(prediction.algorithm, "robust_ensemble")
        self.assertEqual(prediction.horizon_days, 5)
        self.assertGreaterEqual(prediction.buy_probability, 0)
        self.assertLessEqual(prediction.buy_probability, 1)

    def test_time_decay_knn_uses_five_day_horizon(self) -> None:
        bars = _build_bars(260)

        prediction = predict_next_day_buy_probability(bars, "time_decay_knn")

        self.assertIsNotNone(prediction)
        self.assertEqual(prediction.algorithm, "time_decay_knn")
        self.assertEqual(prediction.horizon_days, 5)
        self.assertGreater(prediction.neighbor_count, 0)

    def test_build_advice_accepts_algorithm(self) -> None:
        bars = _build_bars_for_limit_up()

        advice = build_advice("600519", bars, "logistic_regression")

        self.assertIsNotNone(advice.ml_prediction)
        self.assertEqual(advice.ml_prediction.algorithm, "logistic_regression")

    def test_fund_flow_positive_adjusts_buy_score(self) -> None:
        bars = _build_bars_for_limit_up()
        indicators = calculate_indicators("600519", bars)
        self.assertIsNotNone(indicators)
        patterns = detect_patterns(indicators)
        prediction = predict_next_day_buy_probability(bars)
        fund_flow = analyze_fund_flow(_build_fund_flow_bars(positive=True))

        advice = generate_advice(indicators, patterns, prediction, fund_flow=fund_flow)

        self.assertIsNotNone(advice.fund_flow)
        self.assertGreater(advice.fund_flow.fund_flow_score_adjustment, 0)
        self.assertTrue(any("资金流向" in item for item in advice.evidence))

    def test_fund_flow_negative_adjusts_sell_risk(self) -> None:
        bars = _build_yearline_bars(last_close=9.8, last_volume=900)
        fund_flow = analyze_fund_flow(_build_fund_flow_bars(positive=False))

        without_flow = build_sell_advice("600519", bars, cost_price=9.7)
        with_flow = build_sell_advice("600519", bars, cost_price=9.7, fund_flow=fund_flow)

        self.assertIsNotNone(with_flow.fund_flow)
        self.assertGreater(with_flow.sell_risk_score, without_flow.sell_risk_score)
        self.assertTrue(any("资金流" in item for item in with_flow.risks))

    def test_sell_advice_stop_loss_when_loss_exceeds_limit(self) -> None:
        bars = _build_yearline_bars(last_close=9.0, last_volume=2000)

        advice = build_sell_advice("600519", bars, cost_price=10.2, max_loss_rate=0.08)

        self.assertEqual(advice.action, "STOP_LOSS")
        self.assertGreaterEqual(advice.sell_risk_score, 50)
        self.assertTrue(any("止损" in item for item in advice.risks))

    def test_sell_advice_take_profit_on_high_position_risk(self) -> None:
        bars = _build_high_position_stagnation_bars()

        advice = build_sell_advice("600519", bars, cost_price=10.0, target_profit_rate=0.2)

        self.assertEqual(advice.action, "TAKE_PROFIT")
        self.assertIsNotNone(advice.holding_return)
        self.assertTrue(any("止盈" in item for item in advice.risks))

    def test_sell_advice_reduce_on_technical_break(self) -> None:
        bars = _build_yearline_bars(last_close=9.8, last_volume=900)

        advice = build_sell_advice("600519", bars, cost_price=9.7)

        self.assertEqual(advice.action, "REDUCE")
        self.assertTrue(any("低于年线" in item for item in advice.risks))

    def test_sell_advice_hold_when_trend_is_intact(self) -> None:
        bars = _build_yearline_bars(last_close=10.35, last_volume=500)

        advice = build_sell_advice("600519", bars, cost_price=10.0)

        self.assertEqual(advice.action, "HOLD")
        self.assertLess(advice.sell_risk_score, 30)

    def test_sell_advice_without_cost_only_returns_technical_risk(self) -> None:
        bars = _build_yearline_bars(last_close=9.0, last_volume=900)

        advice = build_sell_advice("600519", bars, cost_price=None)

        self.assertEqual(advice.action, "NO_POSITION")
        self.assertIsNone(advice.holding_return)
        self.assertTrue(any("未输入持仓成本" in item for item in advice.risks))

    def test_scan_top_stocks_sorts_by_score(self) -> None:
        def fake_analyzer(symbol, algorithm):
            bars = _build_bars_for_limit_up()
            advice = build_advice(symbol, bars, "knn")
            if symbol == "000001":
                return advice
            return advice.__class__(
                action=advice.action,
                score=advice.score + 10,
                reasons=advice.reasons,
                evidence=advice.evidence,
                risks=advice.risks,
                observations=advice.observations,
                indicators=advice.indicators,
                patterns=advice.patterns,
                ml_prediction=advice.ml_prediction,
                chan_structure=advice.chan_structure,
            )

        results = scan_top_stocks(
            symbols=["000001", "600519"],
            algorithm="knn",
            top_n=1,
            min_score=0,
            analyzer=fake_analyzer,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].symbol, "600519")
        self.assertIn("Top 推荐股票", format_scan_report(results))

    def test_market_scanner_keeps_top_10_without_min_score(self) -> None:
        stocks = [
            StockInfo(symbol="300001", name="创业板1"),
            StockInfo(symbol="301001", name="创业板2"),
            StockInfo(symbol="688001", name="科创板1"),
            StockInfo(symbol="689001", name="科创板2"),
            *[StockInfo(symbol=f"0000{index:02d}", name=f"股票{index}") for index in range(12)],
        ]
        analyzed_symbols = []

        def fake_market_analyzer(symbol, name, algorithm):
            analyzed_symbols.append(symbol)
            score = int(symbol[-2:])
            return ScanResult(
                symbol=symbol,
                name=f"股票{score}",
                action="AVOID",
                score=score,
                probability=score / 100,
                reason="测试理由",
                risk="测试风险",
        )

        scanner = MarketScanner()
        with patch("analysis.market_scanner.iter_a_stock_symbols", return_value=iter(stocks)):
            with patch("analysis.market_scanner._analyze_market_stock", side_effect=fake_market_analyzer):
                scanner._run_id = 1
                scanner._run_once(1, threading.Event(), "knn", 10)

        snapshot = scanner.snapshot()

        self.assertEqual(snapshot.fetched_count, 16)
        self.assertEqual(snapshot.skipped_count, 4)
        self.assertEqual(snapshot.scanned_count, 12)
        self.assertEqual(snapshot.total_count, 12)
        self.assertNotIn("300001", analyzed_symbols)
        self.assertNotIn("301001", analyzed_symbols)
        self.assertNotIn("688001", analyzed_symbols)
        self.assertNotIn("689001", analyzed_symbols)
        self.assertEqual(snapshot.status_message, "本轮全量完成")
        self.assertEqual(len(snapshot.top_results), 10)
        self.assertEqual(snapshot.top_results[0]["score"], 11)
        self.assertEqual(snapshot.top_results[-1]["score"], 2)

    def test_market_scanner_keeps_error_when_universe_empty(self) -> None:
        scanner = MarketScanner()
        with patch("analysis.market_scanner.iter_a_stock_symbols", side_effect=DataRefreshError("列表为空")):
            scanner._run_id = 1
            result = scanner._run_once(1, threading.Event(), "knn", 10)

        snapshot = scanner.snapshot()

        self.assertFalse(result)
        self.assertEqual(snapshot.status, "error")
        self.assertIn("列表为空", snapshot.error)

    def test_market_scanner_restart_creates_new_run(self) -> None:
        scanner = MarketScanner()
        with patch.object(scanner, "_run_loop"):
            scanner.start("knn", 10)
            first_run_id = scanner._run_id
            scanner.start("weighted_knn", 10)

        self.assertGreater(scanner._run_id, first_run_id)

    def test_market_scanner_skips_timed_out_future(self) -> None:
        scanner = MarketScanner()
        future = Future()
        submitted_at = {future: 0.0}

        with patch("analysis.market_scanner.time.time", return_value=31.0):
            remaining = scanner._remove_timed_out_futures({future}, submitted_at, 30)

        snapshot = scanner.snapshot()

        self.assertEqual(remaining, set())
        self.assertEqual(snapshot.scanned_count, 1)
        self.assertEqual(snapshot.failed_count, 1)
        self.assertIn("超时", snapshot.error)


def _build_bars_for_limit_up():
    from datetime import date, timedelta

    bars = []
    start = date(2026, 1, 1)
    for index in range(60):
        close = 10 + index * 0.05 + (0.2 if index % 3 == 0 else 0)
        bars.append(
            PriceVolumeBar(
                name="贵州茅台",
                trade_date=start + timedelta(days=index),
                open=close - 0.05,
                high=close + 0.1,
                low=close - 0.1,
                close=close,
                volume=1000,
                amount=80_000_000,
            )
        )

    previous = bars[-1]
    bars.append(
        PriceVolumeBar(
            name="贵州茅台",
            trade_date=start + timedelta(days=60),
            open=previous.close * 1.08,
            high=previous.close * 1.10,
            low=previous.close * 1.07,
            close=previous.close * 1.10,
            volume=600,
            amount=80_000_000,
        )
    )
    return bars


def _build_bars(count: int):
    from datetime import date, timedelta

    bars = []
    start = date(2025, 1, 1)
    for index in range(count):
        close = 10 + index * 0.03 + (0.15 if index % 7 == 0 else 0)
        bars.append(
            PriceVolumeBar(
                name="测试股票",
                trade_date=start + timedelta(days=index),
                open=close - 0.03,
                high=close + 0.08,
                low=close - 0.08,
                close=close,
                volume=1000 + index * 3,
                amount=80_000_000,
            )
        )
    return bars


def _build_chan_bars():
    from datetime import date, timedelta

    closes = [10, 11, 12, 11, 10, 11, 12.2, 11.1, 10.4, 11.4, 12.6, 11.6, 10.8, 11.9, 13.0, 12.4, 12.1, 12.8]
    bars = []
    start = date(2026, 1, 1)
    for index, close in enumerate(closes):
        bars.append(
            PriceVolumeBar(
                name="测试股票",
                trade_date=start + timedelta(days=index),
                open=close - 0.08,
                high=close + 0.22,
                low=close - 0.22,
                close=close,
                volume=1000 + index * 20,
                amount=80_000_000,
            )
        )
    return bars


def _build_yearline_bars(last_close: float, last_volume: float):
    from datetime import date, timedelta

    bars = []
    start = date(2025, 1, 1)
    for index in range(259):
        close = 10 + index * 0.002
        bars.append(
            PriceVolumeBar(
                name="测试股票",
                trade_date=start + timedelta(days=index),
                open=close - 0.04,
                high=close + 0.08,
                low=close - 0.08,
                close=close,
                volume=1000,
                amount=80_000_000,
            )
        )
    bars.append(
        PriceVolumeBar(
            name="测试股票",
            trade_date=start + timedelta(days=259),
            open=last_close + 0.08,
            high=last_close + 0.12,
            low=last_close - 0.12,
            close=last_close,
            volume=last_volume,
            amount=80_000_000,
        )
    )
    return bars


def _build_high_position_stagnation_bars():
    from datetime import date, timedelta

    bars = []
    start = date(2025, 1, 1)
    for index in range(240):
        close = 10 + index * 0.005
        bars.append(
            PriceVolumeBar(
                name="测试股票",
                trade_date=start + timedelta(days=index),
                open=close - 0.04,
                high=close + 0.08,
                low=close - 0.08,
                close=close,
                volume=1000,
                amount=80_000_000,
            )
        )
    base = bars[-1].close
    for index in range(19):
        close = base * (1.02 + index * 0.018)
        bars.append(
            PriceVolumeBar(
                name="测试股票",
                trade_date=start + timedelta(days=240 + index),
                open=close - 0.05,
                high=close + 0.1,
                low=close - 0.1,
                close=close,
                volume=1000,
                amount=80_000_000,
            )
        )
    last_close = bars[-1].close * 1.005
    bars.append(
        PriceVolumeBar(
            name="测试股票",
            trade_date=start + timedelta(days=259),
            open=last_close - 0.02,
            high=last_close + 0.08,
            low=last_close - 0.08,
            close=last_close,
            volume=2500,
            amount=80_000_000,
        )
    )
    return bars


def _build_rebound_bars():
    from datetime import date, timedelta

    bars = []
    start = date(2025, 1, 1)
    for index in range(259):
        close = 12 - index * 0.008
        bars.append(
            PriceVolumeBar(
                name="测试股票",
                trade_date=start + timedelta(days=index),
                open=close + 0.03,
                high=close + 0.10,
                low=close - 0.10,
                close=close,
                volume=1000,
                amount=80_000_000,
            )
        )
    previous_close = bars[-1].close
    bars.append(
        PriceVolumeBar(
            name="测试股票",
            trade_date=start + timedelta(days=259),
            open=previous_close - 0.10,
            high=previous_close - 0.04,
            low=previous_close - 0.52,
            close=previous_close - 0.08,
            volume=950,
            amount=80_000_000,
        )
    )
    return bars


def _build_fund_flow_bars(positive: bool):
    from datetime import date, timedelta

    bars = []
    start = date(2026, 1, 1)
    direction = 1 if positive else -1
    for index in range(10):
        main = direction * (10_000_000 + index * 1_000_000)
        bars.append(
            FundFlowBar(
                trade_date=start + timedelta(days=index),
                main_net_inflow=main,
                super_large_net_inflow=direction * 4_000_000,
                large_net_inflow=direction * 3_000_000,
                medium_net_inflow=-direction * 1_000_000,
                small_net_inflow=-direction * 6_000_000,
                main_net_inflow_ratio=direction * 0.04,
            )
        )
    return bars


def _build_macd_cross_bars(golden: bool):
    from datetime import date, timedelta

    if golden:
        closes = [12 - index * 0.05 for index in range(45)] + [9.6, 9.6, 9.6, 9.8]
    else:
        closes = [9 + index * 0.05 for index in range(45)] + [11.4, 11.4, 11.4, 11.2]
    bars = []
    start = date(2026, 1, 1)
    for index, close in enumerate(closes):
        bars.append(
            PriceVolumeBar(
                name="测试股票",
                trade_date=start + timedelta(days=index),
                open=close - 0.03,
                high=close + 0.08,
                low=close - 0.08,
                close=close,
                volume=1000 + index * 5,
                amount=80_000_000,
            )
        )
    return bars


def _build_divergence_bars(bullish: bool):
    from datetime import date, timedelta

    if bullish:
        closes = [13 - index * 0.035 for index in range(70)] + [10.35, 10.15, 10.25, 10.05, 10.18, 9.98]
    else:
        closes = [8 + index * 0.035 for index in range(70)] + [10.65, 10.85, 10.75, 10.95, 10.82, 11.02]
    bars = []
    start = date(2026, 1, 1)
    for index, close in enumerate(closes):
        bars.append(
            PriceVolumeBar(
                name="测试股票",
                trade_date=start + timedelta(days=index),
                open=close - 0.04,
                high=close + 0.08,
                low=close - 0.08,
                close=close,
                volume=1000 + (index % 6) * 20,
                amount=80_000_000,
            )
        )
    return bars


def _build_no_divergence_bars():
    from datetime import date, timedelta

    closes = [10 + ((index % 6) - 3) * 0.03 for index in range(90)]
    bars = []
    start = date(2026, 1, 1)
    for index, close in enumerate(closes):
        bars.append(
            PriceVolumeBar(
                name="测试股票",
                trade_date=start + timedelta(days=index),
                open=close - 0.02,
                high=close + 0.05,
                low=close - 0.05,
                close=close,
                volume=1000,
                amount=80_000_000,
            )
        )
    return bars

if __name__ == "__main__":
    unittest.main()
