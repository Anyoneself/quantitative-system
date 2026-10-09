"""Synthetic I/O-bound scan comparison; not a live-market speed or investment-performance claim."""
import json
import time
from unittest.mock import patch

from analysis.market_scanner import MarketScanner
from analysis.scanner import ScanResult
from data.models import StockInfo


def main():
    stocks = [StockInfo(f"600{index:03d}", f"Fixture {index}") for index in range(64)]
    calls = 0

    def analyze(symbol, name, algorithm):
        nonlocal calls
        calls += 1
        time.sleep(.02)
        return ScanResult(symbol, name, "BUY", 80, .7, "fixture", "fixture")

    def run(scanner, workers):
        start = time.perf_counter()
        with patch.dict("os.environ", {"MARKET_SCAN_WORKERS": str(workers)}):
            scanner.start("robust_ensemble", 10)
            scanner._thread.join(10)
        assert not scanner._thread.is_alive()
        assert scanner.snapshot().scanned_count == len(stocks)
        return round(time.perf_counter() - start, 4)

    with patch("analysis.market_scanner.fetch_public_a_stock_gainers", return_value=stocks), \
         patch("analysis.market_scanner._analyze_market_stock", side_effect=analyze):
        baseline = run(MarketScanner(), 4)
        scanner = MarketScanner()
        cold = run(scanner, 8)
        before = calls
        warm = run(scanner, 8)
    assert calls == before
    print(json.dumps({"scope": "synthetic_64_stocks_20ms_io", "four_workers_seconds": baseline,
                      "eight_workers_seconds": cold, "cached_seconds": warm,
                      "cached_count": scanner.snapshot().cached_count,
                      "warm_analysis_calls": calls - before}))


if __name__ == "__main__":
    main()
