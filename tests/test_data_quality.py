from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from data.errors import DataQualityError
from data.models import PriceVolumeBar
from data.public_sources import (
    SHANGHAI_TIMEZONE,
    _exclude_incomplete_current_bar,
    _persist_raw_response,
    _to_market_symbol,
)
from data.quality import validate_price_bars


class DataQualityTest(unittest.TestCase):
    def test_shanghai_benchmark_uses_index_market_prefix(self) -> None:
        self.assertEqual(_to_market_symbol("000300"), "sh000300")
        self.assertEqual(_to_market_symbol("000001"), "sz000001")

    def test_valid_bars_are_returned_without_modification(self) -> None:
        bars = [_bar(0), _bar(1)]

        result = validate_price_bars(bars, minimum_count=2)

        self.assertIs(result, bars)

    def test_duplicate_date_is_rejected(self) -> None:
        bars = [_bar(0), _bar(0)]

        with self.assertRaisesRegex(DataQualityError, "重复交易日期"):
            validate_price_bars(bars)

    def test_descending_date_is_rejected(self) -> None:
        bars = [_bar(1), _bar(0)]

        with self.assertRaisesRegex(DataQualityError, "严格升序"):
            validate_price_bars(bars)

    def test_invalid_ohlc_is_rejected(self) -> None:
        invalid = _bar(0, high=9.5, close=10.0)

        with self.assertRaisesRegex(DataQualityError, "不能覆盖"):
            validate_price_bars([invalid])

    def test_non_finite_price_is_rejected(self) -> None:
        invalid = _bar(0, close=float("nan"))

        with self.assertRaisesRegex(DataQualityError, "有限数"):
            validate_price_bars([invalid])

    def test_negative_volume_is_rejected(self) -> None:
        invalid = _bar(0, volume=-1)

        with self.assertRaisesRegex(DataQualityError, "成交量"):
            validate_price_bars([invalid])

    def test_tencent_index_day_field_does_not_allow_stock_adjustment_fallback(self) -> None:
        """Given 指数仅返回 day When 读取前复权序列 Then 指数可读但股票仍禁止未复权兜底"""
        from data.errors import DataRefreshError
        from data.public_sources import _fetch_tencent_stock_bars, _to_market_symbol

        for symbol in ["000300", "600001"]:
            payload = {"data": {_to_market_symbol(symbol): {
                "day": [["2026-09-29", "10", "11", "12", "9", "10000"]],
            }}}
            with self.subTest(symbol=symbol), patch("data.public_sources._fetch_json", return_value=payload):
                if symbol == "000300":
                    bars = _fetch_tencent_stock_bars(symbol, None)
                    self.assertEqual(bars[0].close, 11)
                    self.assertEqual(bars[0].trade_date, date(2026, 9, 29))
                else:
                    with self.assertRaises(DataRefreshError):
                        _fetch_tencent_stock_bars(symbol, None)

    def test_future_bar_is_rejected(self) -> None:
        invalid = _bar(0, trade_date=date(2099, 1, 5))

        with self.assertRaisesRegex(DataQualityError, "晚于上海市场"):
            validate_price_bars([invalid])

    def test_raw_response_contains_hash_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            with patch.dict("os.environ", {"PUBLIC_DATA_RAW_DIR": temporary_dir}):
                _persist_raw_response("https://web.ifzq.gtimg.cn/example", '{"ok": true}')

            paths = list(Path(temporary_dir).rglob("*.json"))
            self.assertEqual(len(paths), 1)
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["source"], "tencent")
            self.assertEqual(payload["body"], '{"ok": true}')
            self.assertEqual(len(payload["content_sha256"]), 64)

    def test_current_daily_bar_is_excluded_before_shanghai_close(self) -> None:
        bars = [
            _bar(0, trade_date=date(2026, 7, 28)),
            _bar(1, trade_date=date(2026, 7, 29)),
        ]

        result = _exclude_incomplete_current_bar(
            bars,
            datetime(2026, 7, 29, 14, 59, tzinfo=SHANGHAI_TIMEZONE),
        )

        self.assertEqual([bar.trade_date for bar in result], [date(2026, 7, 28)])

    def test_current_daily_bar_is_kept_at_shanghai_close(self) -> None:
        bars = [_bar(0, trade_date=date(2026, 7, 29))]

        result = _exclude_incomplete_current_bar(
            bars,
            datetime(2026, 7, 29, 15, 0, tzinfo=SHANGHAI_TIMEZONE),
        )

        self.assertEqual(result, bars)

    def test_weekend_does_not_remove_last_completed_trading_day(self) -> None:
        bars = [_bar(0, trade_date=date(2026, 7, 31))]

        result = _exclude_incomplete_current_bar(
            bars,
            datetime(2026, 8, 1, 10, 0, tzinfo=SHANGHAI_TIMEZONE),
        )

        self.assertEqual(result, bars)


def _bar(index: int, **changes) -> PriceVolumeBar:
    values = {
        "name": "测试股票",
        "trade_date": date(2026, 1, 1) + timedelta(days=index),
        "open": 10.0,
        "high": 10.3,
        "low": 9.8,
        "close": 10.1,
        "volume": 1000.0,
        "amount": 10_000.0,
    }
    values.update(changes)
    return PriceVolumeBar(**values)


if __name__ == "__main__":
    unittest.main()
