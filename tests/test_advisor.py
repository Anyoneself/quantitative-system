from __future__ import annotations

import unittest
import threading
from concurrent.futures import Future
from unittest.mock import patch

from quant_system.analysis.advisor import generate_advice
from quant_system.analysis.indicators import calculate_indicators
from quant_system.analysis.market_scanner import MarketScanner
from quant_system.analysis.ml_model import predict_next_day_buy_probability
from quant_system.analysis.scanner import ScanResult, format_scan_report, scan_top_stocks
from quant_system.analysis.service import build_advice
from quant_system.analysis.patterns import detect_patterns
from quant_system.data.errors import DataRefreshError
from quant_system.data.models import PriceVolumeBar, StockInfo
from quant_system.output.formatter import format_advice


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

    def test_logistic_regression_prediction_returns_probability(self) -> None:
        bars = _build_bars_for_limit_up()

        prediction = predict_next_day_buy_probability(bars, "logistic_regression")

        self.assertIsNotNone(prediction)
        self.assertEqual(prediction.algorithm, "logistic_regression")
        self.assertGreaterEqual(prediction.buy_probability, 0)
        self.assertLessEqual(prediction.buy_probability, 1)

    def test_build_advice_accepts_algorithm(self) -> None:
        bars = _build_bars_for_limit_up()

        advice = build_advice("600519", bars, "logistic_regression")

        self.assertIsNotNone(advice.ml_prediction)
        self.assertEqual(advice.ml_prediction.algorithm, "logistic_regression")

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
        with patch("quant_system.analysis.market_scanner.iter_a_stock_symbols", return_value=iter(stocks)):
            with patch("quant_system.analysis.market_scanner._analyze_market_stock", side_effect=fake_market_analyzer):
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
        with patch("quant_system.analysis.market_scanner.iter_a_stock_symbols", side_effect=DataRefreshError("列表为空")):
            scanner._run_id = 1
            result = scanner._run_once(1, threading.Event(), "knn", 10)

        snapshot = scanner.snapshot()

        self.assertFalse(result)
        self.assertEqual(snapshot.status, "error")
        self.assertIn("列表为空", snapshot.error)

    def test_market_scanner_restart_creates_new_run(self) -> None:
        scanner = MarketScanner()
        with patch.object(scanner, "_run_loop"):
            scanner.start("knn", 10, 600)
            first_run_id = scanner._run_id
            scanner.start("weighted_knn", 10, 600)

        self.assertGreater(scanner._run_id, first_run_id)

    def test_market_scanner_skips_timed_out_future(self) -> None:
        scanner = MarketScanner()
        future = Future()
        submitted_at = {future: 0.0}

        with patch("quant_system.analysis.market_scanner.time.time", return_value=31.0):
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

if __name__ == "__main__":
    unittest.main()
