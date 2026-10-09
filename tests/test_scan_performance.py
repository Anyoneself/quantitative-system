import unittest
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from data import fund_flow
from data import public_sources
from data.errors import DataRefreshError


class ScanPerformanceScenarios(unittest.TestCase):
    def setUp(self):
        archive = tempfile.TemporaryDirectory()
        self.addCleanup(archive.cleanup)
        self.patches = [patch.object(fund_flow, "_FUND_FLOW_CACHE", {}),
                        patch.object(fund_flow, "_CONSECUTIVE_FAILURES", 0),
                        patch.object(fund_flow, "_BLOCKED_UNTIL", 0.0),
                        patch.object(public_sources, "_SCAN_SOURCE_RETRY_AT", {}),
                        patch.dict("os.environ", {"PUBLIC_DATA_RAW_DIR": archive.name})]
        self.archive_root = Path(archive.name)
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_repeated_provider_failure_uses_a_short_circuit_not_per_stock_timeouts(self):
        """Given 资金流服务连续失败 When 扫描后续股票 Then 暂停可选资金流请求而非每只等待超时"""
        with patch("data.fund_flow._fetch_json", side_effect=DataRefreshError("unavailable")) as fetch:
            for index in range(8):
                with self.assertRaises(DataRefreshError):
                    fund_flow.fetch_public_fund_flow_bars(f"600{index:03d}")
        self.assertEqual(fetch.call_count, 3)

    def test_success_cache_expires_and_does_not_keep_yesterdays_flow_forever(self):
        """Given 资金流短期缓存 When 超过有效期 Then 重新读取，不永久复用旧值"""
        response = {"data": {"klines": ["2026-09-30,1,2,3,4,5,6"]}}
        with patch("data.fund_flow._fetch_json", return_value=response) as fetch, \
             patch("data.fund_flow.time.monotonic", return_value=100):
            fund_flow.fetch_public_fund_flow_bars("600001")
            fund_flow.fetch_public_fund_flow_bars("600001")
        with patch("data.fund_flow._fetch_json", return_value=response) as refreshed, \
             patch("data.fund_flow.time.monotonic", return_value=1100):
            fund_flow.fetch_public_fund_flow_bars("600001")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(refreshed.call_count, 1)

    def test_permanent_http_failure_does_not_wait_through_retries(self):
        """Given 日线接口返回访问拦截 When 读取 Then 立即报告失败而非重复等待不可恢复错误"""
        error = HTTPError("https://example.invalid", 501, "blocked", {}, None)
        with patch("data.public_sources.urlopen", side_effect=error) as request, \
             patch("data.public_sources._wait_for_request_slot"), \
             patch("data.public_sources.time.sleep") as sleep:
            with self.assertRaises(DataRefreshError):
                public_sources._fetch_json("https://example.invalid", "https://example.invalid")
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_market_fallback_uses_whole_adjusted_series_but_account_loader_stays_tencent(self):
        """Given 腾讯公开接口不可用 When 扫描公开市场 Then 整段换用前复权备用源，不改变持仓授权来源"""
        payload = {"data": {"code": "600001", "name": "测试", "klines": [
            "2026-09-28,10,11,12,9,100,110000,1,1,1,1",
            "2026-09-29,11,12,13,10,200,240000,1,1,1,1",
        ]}}
        from data.errors import DataSourceUnavailable
        with patch("data.public_sources._fetch_tencent_stock_bars", side_effect=DataSourceUnavailable("公开行情接口请求失败：501")) as tencent, \
             patch("data.public_sources._fetch_json", side_effect=[
                 payload, {"data": {**payload["data"], "code": "600002"}},
             ]), \
             patch("data.public_sources.validate_price_bars", side_effect=lambda bars: bars):
            bars, source = public_sources.fetch_public_market_stock_bars("600001", "测试")
            self.assertEqual(source, "东方财富前复权")
            self.assertEqual(bars[0].volume, 10000)
            self.assertEqual(bars[0].amount, 110000)
            public_sources.fetch_public_market_stock_bars("600002", "测试")
            self.assertEqual(tencent.call_count, 1)
            with self.assertRaises(DataRefreshError):
                public_sources.fetch_public_stock_bars("600001", "测试")
            self.assertEqual(tencent.call_count, 2)

    def test_transport_errors_are_distinct_from_missing_stock_data(self):
        """Given 行情源拒绝请求 When 请求失败 Then 抛出数据源不可用而非个股分析失败"""
        from data.errors import DataSourceUnavailable
        with patch("data.public_sources.urlopen", side_effect=HTTPError("https://example.invalid", 501, "blocked", {}, None)), \
             patch("data.public_sources._wait_for_request_slot"):
            with self.assertRaises(DataSourceUnavailable):
                public_sources._fetch_json("https://example.invalid", "https://example.invalid")

    def _save_quote_archive(self, fetched_at, *, url_symbol="sh600001", data_symbol="sh600001", series="qfqday", age=0):
        day = fetched_at.date() - timedelta(days=age)
        body = json.dumps({"data": {data_symbol: {series: [
            [str(day - timedelta(days=1)), "10", "11", "12", "9", "100"],
            [str(day), "11", "12", "13", "10", "200"],
        ]}}})
        with patch("data.public_sources.datetime", wraps=datetime) as clock:
            clock.now.return_value = fetched_at
            public_sources._persist_raw_response(public_sources._tencent_kline_url(url_symbol), body)
        return next(self.archive_root.rglob("*.json"))

    def test_market_outage_reuses_verified_current_session_archive(self):
        """Given 同交易时段有真实前复权留档 When 两个在线源都不可用 Then 复用校验后的完整序列并标注本地缓存"""
        from data.errors import DataSourceUnavailable
        now = datetime.now(public_sources.SHANGHAI_TIMEZONE).replace(hour=19, minute=0)
        self._save_quote_archive(now - timedelta(hours=1))
        with patch("data.public_sources.datetime", wraps=datetime) as clock, \
             patch("data.public_sources._fetch_tencent_stock_bars", side_effect=DataSourceUnavailable("腾讯访问受限")) as tencent, \
             patch("data.public_sources._fetch_eastmoney_stock_bars", side_effect=DataSourceUnavailable("备用源断开")) as eastmoney:
            clock.now.return_value = now
            bars, source = public_sources.fetch_public_market_stock_bars("600001", "测试")
            self.assertEqual(len(bars), 2)
            self.assertEqual(bars[-1].close, 12)
            self.assertEqual(bars[-1].trade_date, now.date())
            self.assertIn("本地缓存", source)
            self.assertIn("18:00", source)
            public_sources.fetch_public_market_stock_bars("600001", "测试")
            self.assertEqual(tencent.call_count, 1)
            self.assertEqual(eastmoney.call_count, 1)
            with self.assertRaises(DataRefreshError):
                public_sources.fetch_public_stock_bars("600001", "测试")

    def test_archive_rejects_corrupt_stale_wrong_identity_or_wrong_adjustment(self):
        """Given 留档哈希错误过期代码不符或未复权 When 在线源不可用 Then 不把不合格留档伪装成可用行情"""
        from data.errors import DataSourceUnavailable
        now = datetime.now(public_sources.SHANGHAI_TIMEZONE).replace(hour=19, minute=0)
        cases = ["checksum", "previous_day", "pre_close", "future", "url_symbol", "data_symbol", "unadjusted", "stale_bars"]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root, \
                 patch.dict("os.environ", {"PUBLIC_DATA_RAW_DIR": root}), \
                 patch("data.public_sources.datetime", wraps=datetime) as clock, \
                 patch("data.public_sources._fetch_tencent_stock_bars", side_effect=DataSourceUnavailable("访问受限")), \
                 patch("data.public_sources._fetch_eastmoney_stock_bars", side_effect=DataSourceUnavailable("断开")):
                self.archive_root = Path(root)
                fetched = now - timedelta(hours=1)
                if case == "previous_day": fetched -= timedelta(days=1)
                if case == "pre_close": fetched = fetched.replace(hour=14)
                if case == "future": fetched = now + timedelta(hours=1)
                path = self._save_quote_archive(fetched,
                    url_symbol="sh600002" if case == "url_symbol" else "sh600001",
                    data_symbol="sh600002" if case == "data_symbol" else "sh600001",
                    series="day" if case == "unadjusted" else "qfqday", age=8 if case == "stale_bars" else 0)
                if case == "checksum":
                    record = json.loads(path.read_text())
                    record["content_sha256"] = "wrong"
                    path.write_text(json.dumps(record))
                clock.now.return_value = now
                with self.assertRaises(DataSourceUnavailable):
                    public_sources.fetch_public_market_stock_bars("600001", "测试")

    def test_fallback_rejects_missing_or_mismatched_stock_identity(self):
        """Given 备用行情未确认证券身份 When 返回代码缺失或不符 Then 不将其他证券日线用于分析"""
        data = {"name": "测试", "klines": ["2026-09-29,10,11,12,9,100,110000,1,1,1,1"]}
        for identity in ({}, {"code": "600002"}):
            with self.subTest(identity=identity), \
                 patch("data.public_sources._fetch_json", return_value={"data": {**data, **identity}}):
                with self.assertRaises(DataRefreshError):
                    public_sources._fetch_eastmoney_stock_bars("600001", "测试")


if __name__ == "__main__":
    unittest.main()
