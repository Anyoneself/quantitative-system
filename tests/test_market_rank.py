import unittest
import threading
from concurrent.futures import Future, wait
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from analysis.market_scanner import MarketScanner
from analysis.scanner import ScanResult
from data import public_sources
from data.errors import DataRefreshError
from data.models import StockInfo


def quote(index):
    return {"code": f"600{index:03d}", "name": f"测试{index}", "changepercent": 20 - index / 100}


class MarketRankScenarios(unittest.TestCase):
    def test_only_first_five_rank_pages_are_read(self):
        """Given 无板块过滤 When 读取涨幅前500名 Then 取满500即停且只请求五页降序榜单"""
        pages = [[quote(index) for index in range(start, start + 100)] for start in range(0, 500, 100)]
        with patch("data.public_sources._fetch_json", side_effect=pages) as fetch, \
             patch("data.public_sources._fetch_sina_stock_count") as count:
            stocks = public_sources.fetch_public_a_stock_gainers()
        self.assertEqual(len(stocks), 500)
        self.assertEqual(stocks[0].symbol, "600000")
        self.assertEqual(stocks[-1].symbol, "600499")
        count.assert_not_called()
        for page, call in enumerate(fetch.call_args_list, 1):
            query = parse_qs(urlsplit(call.args[0]).query)
            self.assertEqual(query["page"], [str(page)])
            self.assertEqual(query["num"], ["100"])
            self.assertEqual(query["sort"], ["changepercent"])
            self.assertEqual(query["asc"], ["0"])
            self.assertEqual(query["node"], ["hs_a"])

    def test_membership_is_refreshed_on_every_scan(self):
        """Given 上次涨幅榜已改变 When 再次取股票池 Then 重新读取榜单而不是沿用旧成员"""
        with patch("data.public_sources._fetch_json", side_effect=[[quote(1)], [quote(2)]]) as fetch:
            first = public_sources.fetch_public_a_stock_gainers(1)
            second = public_sources.fetch_public_a_stock_gainers(1)
        self.assertNotEqual(first, second)
        self.assertEqual(fetch.call_count, 2)

    def test_incomplete_duplicate_or_unsorted_rank_is_rejected(self):
        """Given 榜单缺页重复或顺序异常 When 冻结股票池 Then 拒绝不完整排名而不扩展到全市场补齐"""
        invalid = [
            [],
            [quote(1)],
            [quote(1), quote(1)],
            [quote(2), quote(1)],
            [quote(1), {**quote(2), "changepercent": "NaN"}],
            [quote(1), {**quote(2), "code": "invalid"}],
            [quote(1), {**quote(2), "name": ""}],
            {"error": "unavailable"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), patch("data.public_sources._fetch_json", return_value=payload):
                with self.assertRaises(DataRefreshError):
                    public_sources.fetch_public_a_stock_gainers(2)

    def test_rank_consistency_is_checked_across_page_boundaries(self):
        """Given 翻页过程中重复证券或排名倒序 When 汇总榜单 Then 不将漂移榜单伪装为完整前500"""
        first = [quote(index) for index in range(100)]
        for second in ([quote(99)], [quote(0)], [quote(100) | {"changepercent": 99}]):
            with self.subTest(second=second), patch("data.public_sources._fetch_json", side_effect=[first, second]):
                with self.assertRaises(DataRefreshError):
                    public_sources.fetch_public_a_stock_gainers(101)

    def test_top500_limit_is_applied_after_board_filtering(self):
        """Given 涨幅前500中含不支持板块 When 筛选 Then 排除项不占名额并用第501名补足500只"""
        scanner = MarketScanner()
        stocks = [StockInfo("300001", "创业板")] + [
            StockInfo(f"600{index:03d}", f"测试{index}") for index in range(500)
        ]
        with patch("analysis.market_scanner.fetch_public_a_stock_gainers", return_value=stocks), \
             patch("analysis.market_scanner._analyze_market_stock", return_value=None) as analyze:
            scanner.start("knn", 10)
            scanner._thread.join(5)
        state = scanner.snapshot()
        self.assertFalse(state.running)
        self.assertEqual(state.fetched_count, 501)
        self.assertEqual(state.total_count, 500)
        self.assertEqual(state.skipped_count, 1)
        self.assertEqual(analyze.call_count, 500)
        self.assertIn("600499", {call.args[0] for call in analyze.call_args_list})
        self.assertNotIn("300001", {call.args[0] for call in analyze.call_args_list})
        self.assertEqual(state.universe_limit, 500)
        self.assertEqual(state.universe_source, "新浪公开涨幅榜")
        self.assertTrue(state.universe_fetched_at)

    def test_rank_scan_continues_until_500_eligible_stocks(self):
        """Given 排名前150只是创业板 When 选择涨幅或跌幅500 Then 顺延七页收满500只主板并立即停止翻页"""
        for pool in ("gainers", "losers"):
            rows = [{**quote(i), "code": f"300{i:03d}" if i < 150 else f"600{i:03d}",
                     "changepercent": (-20 + i / 100) if pool == "losers" else (20 - i / 100)}
                    for i in range(800)]
            scanner = MarketScanner()
            with self.subTest(pool=pool), patch("data.public_sources._fetch_sina_stock_count", return_value=800), \
                 patch("data.public_sources._fetch_json", side_effect=[rows[i:i + 100] for i in range(0, 800, 100)]) as fetch, \
                 patch("analysis.market_scanner._analyze_market_stock", return_value=None) as analyze:
                scanner.start("knn", 10, pool=pool)
                scanner._thread.join(5)
            state = scanner.snapshot()
            self.assertEqual(state.status, "finished")
            self.assertEqual(state.total_count, 500)
            self.assertEqual(state.fetched_count, 650)
            self.assertEqual(state.skipped_count, 150)
            self.assertEqual(state.universe_shortfall, 0)
            self.assertEqual(fetch.call_count, 7)
            self.assertEqual(analyze.call_count, 500)
            self.assertTrue(all(call.args[0].startswith("600") for call in analyze.call_args_list))

    def test_limit_scan_refills_with_eligible_members_of_the_same_pool(self):
        """Given 涨跌停池前100有90只创业板 When 分析100只 Then 向后取满100只主板且不跨股票池"""
        for pool in ("limit_up", "limit_down"):
            change = 10 if pool == "limit_up" else -10
            rows = [{"c": f"300{i:03d}" if i < 90 else f"600{i:03d}", "n": "测试",
                     "zdp": change, "p": 10000, "amount": 500 - i} for i in range(200)]
            pages = [{"rc": 0, "data": {"tc": 200, "qdate": 20260930, "pool": rows[i:i + 100]}}
                     for i in (0, 100)]
            scanner = MarketScanner()
            with self.subTest(pool=pool), patch("analysis.market_scanner.datetime") as clock, \
                 patch("data.public_sources._fetch_json", side_effect=pages) as fetch, \
                 patch("analysis.market_scanner._analyze_market_stock", side_effect=lambda symbol, *args:
                       ScanResult(symbol, "测试", "BUY", 80, .7, "依据", "风险")) as analyze:
                clock.now.return_value = datetime(2026, 9, 30, 16)
                scanner.start("knn", 10, pool=pool)
                scanner._thread.join(5)
            state = scanner.snapshot()
            self.assertEqual(state.total_count, 100)
            self.assertEqual(state.fetched_count, 190)
            self.assertEqual(state.skipped_count, 90)
            self.assertEqual(state.successful_count, 100)
            self.assertEqual(state.universe_shortfall, 0)
            self.assertEqual(analyze.call_count, 100)
            self.assertEqual(len(state.top_results), 10)
            self.assertTrue(all(item["symbol"].startswith("600") for item in state.top_results))
            self.assertEqual([parse_qs(urlsplit(call.args[0]).query)["Pageindex"] for call in fetch.call_args_list], [["0"], ["1"]])

    def test_exhausted_pool_reports_actual_eligible_count_and_shortfall(self):
        """Given 整个涨停池仅100只且90只被排除 When 已遍历全池 Then 明示仅10只可分析和90只缺额"""
        rows = [{"c": f"300{i:03d}" if i < 90 else f"600{i:03d}", "n": "测试",
                 "zdp": 10, "p": 10000, "amount": 500 - i} for i in range(100)]
        scanner = MarketScanner()
        with patch("analysis.market_scanner.datetime") as clock, \
             patch("data.public_sources._fetch_json", return_value={"rc": 0, "data": {
                 "tc": 100, "qdate": 20260930, "pool": rows}}) as fetch, \
             patch("analysis.market_scanner._analyze_market_stock", return_value=None):
            clock.now.return_value = datetime(2026, 9, 30, 16)
            scanner.start("knn", 10, pool="limit_up")
            scanner._thread.join(5)
        state = scanner.snapshot()
        self.assertEqual(state.status, "finished")
        self.assertEqual(state.total_count, 10)
        self.assertEqual(state.universe_shortfall, 90)
        self.assertEqual(state.successful_count, 0)
        self.assertEqual(state.failed_count, 10)
        self.assertEqual(fetch.call_count, 1)

    def test_refill_failure_does_not_publish_partial_membership(self):
        """Given 后续补位页缺失 When 还未收满目标数量 Then 本轮报错且不将前页冒充完整股票池"""
        rows = [{**quote(i), "code": f"300{i:03d}"} for i in range(100)]
        scanner = MarketScanner()
        with patch("data.public_sources._fetch_sina_stock_count", return_value=600), \
             patch("data.public_sources._fetch_json", side_effect=[rows, []]), \
             patch("analysis.market_scanner._analyze_market_stock") as analyze:
            scanner.start("knn", 10)
            scanner._thread.join(3)
        self.assertEqual(scanner.snapshot().status, "error")
        self.assertEqual(scanner.snapshot().top_results, [])
        analyze.assert_not_called()

    def test_refill_checks_limit_pool_consistency_across_pages(self):
        """Given 补位跨页期间数量日期或排名发生变化 When 收集股票池 Then 阻断漂移或重复榜单"""
        first = {"rc": 0, "data": {"tc": 4, "qdate": 20260930, "pool": [
            {"c": "300001", "n": "排除1", "zdp": 20, "p": 10000, "amount": 500},
            {"c": "300002", "n": "排除2", "zdp": 20, "p": 10000, "amount": 400},
        ]}}
        second = {"tc": 4, "qdate": 20260930, "pool": [
            {"c": "600001", "n": "主板1", "zdp": 10, "p": 10000, "amount": 300},
            {"c": "600002", "n": "主板2", "zdp": 10, "p": 10000, "amount": 200},
        ]}
        invalid = [{**second, "tc": 5}, {**second, "qdate": 20260929},
                   {**second, "pool": first["data"]["pool"]},
                   {**second, "pool": [second["pool"][0] | {"amount": 600}, second["pool"][1]]}]
        for data in invalid:
            with self.subTest(data=data), patch("data.public_sources._fetch_json", side_effect=[first, {"rc": 0, "data": data}]):
                with self.assertRaises(DataRefreshError):
                    public_sources.fetch_public_a_stock_limit_pool("up", 2, trade_date=date(2026, 9, 30),
                                                                   include_symbol=lambda symbol: symbol.startswith("600"))

    def test_rank_refill_stops_when_complete_source_has_too_few_eligible_stocks(self):
        """Given 全榜仅120只且前100只被排除 When 收集500只 Then 完整末页确认仅20只合格并结束"""
        rows = [{**quote(i), "code": f"300{i:03d}" if i < 100 else f"600{i:03d}"} for i in range(120)]
        with patch("data.public_sources._fetch_sina_stock_count", return_value=120), \
             patch("data.public_sources._fetch_json", side_effect=[rows[:100], rows[100:]]) as fetch:
            stocks = public_sources.fetch_public_a_stock_gainers(include_symbol=lambda symbol: symbol.startswith("600"))
        self.assertEqual(sum(stock.symbol.startswith("600") for stock in stocks), 20)
        self.assertEqual(fetch.call_count, 2)

    def test_stop_interrupts_universe_refill_before_next_page(self):
        """Given 首批均被板块排除 When 用户停止或切换 Then 不再请求补位页面"""
        stop = threading.Event()

        def page(*args, **kwargs):
            stop.set()
            return [{**quote(i), "code": f"300{i:03d}"} for i in range(100)]

        with patch("data.public_sources._fetch_sina_stock_count", return_value=600), \
             patch("data.public_sources._fetch_json", side_effect=page) as fetch:
            stocks = public_sources.fetch_public_a_stock_gainers(include_symbol=lambda symbol: symbol.startswith("600"),
                                                                should_stop=stop.is_set)
        self.assertEqual(len(stocks), 100)
        self.assertEqual(fetch.call_count, 1)

    def test_rank_failure_does_not_analyze_or_reuse_last_ranking(self):
        """Given 榜单读取失败且上轮有候选 When 更新 Then 清空旧候选并停止而不是回退全市场"""
        scanner = MarketScanner()
        future = Future()
        future.set_result(ScanResult("600001", "测试", "BUY", 80, .7, "依据", "风险"))
        scanner._record_finished_future(future, 10)
        with patch("analysis.market_scanner.fetch_public_a_stock_gainers", side_effect=DataRefreshError("榜单缺页")), \
             patch("analysis.market_scanner._analyze_market_stock") as analyze:
            scanner.start("knn", 10)
            scanner._thread.join(5)
        self.assertEqual(scanner.snapshot().status, "error")
        self.assertEqual(scanner.snapshot().top_results, [])
        analyze.assert_not_called()

    def test_candidate_exposes_frozen_input_rank_not_result_rank(self):
        """Given 涨幅池顺序与模型评分不同 When 生成Top10 Then 同时保留原涨幅排名与模型排序"""
        scanner = MarketScanner()
        stocks = [StockInfo(f"600{index:03d}", f"测试{index}") for index in range(3)]

        def analyze(symbol, name, algorithm):
            return ScanResult(symbol, name, "BUY", 70 + int(symbol[-1]), .7, "依据", "风险")

        with patch("analysis.market_scanner.fetch_public_a_stock_gainers", return_value=stocks), \
             patch("analysis.market_scanner._analyze_market_stock", side_effect=analyze):
            scanner.start("knn", 10)
            scanner._thread.join(5)
        self.assertEqual([item["universe_rank"] for item in scanner.snapshot().top_results], [3, 2, 1])

    def test_losers_read_five_ascending_pages(self):
        """Given 无板块过滤 When 读取跌幅前500名 Then 请求涨跌幅升序且五页即取满"""
        rows = [{**quote(i), "changepercent": -20 + i / 100} for i in range(500)]
        with patch("data.public_sources._fetch_json", side_effect=[rows[i:i + 100] for i in range(0, 500, 100)]) as fetch:
            stocks = public_sources.fetch_public_a_stock_losers()
        self.assertEqual(len(stocks), 500)
        self.assertEqual(fetch.call_count, 5)
        for call in fetch.call_args_list:
            self.assertEqual(parse_qs(urlsplit(call.args[0]).query)["asc"], ["1"])
        with patch("data.public_sources._fetch_json", return_value=[rows[1], rows[0]]):
            with self.assertRaises(DataRefreshError):
                public_sources.fetch_public_a_stock_losers(2)

    def test_limit_pools_use_dedicated_membership_and_do_not_fill_to_100(self):
        """Given 当日真正涨跌停不足100只 When 获取 Then 使用专用池按成交额排序且不补入普通股票"""
        for direction, endpoint, change in [("up", "getTopicZTPool", 10), ("down", "getTopicDTPool", -10)]:
            payload = {"rc": 0, "data": {"tc": 2, "qdate": 20260930, "pool": [
                {"c": "600001", "n": "测试1", "zdp": change, "p": 10000, "amount": 200},
                {"c": "600002", "n": "测试2", "zdp": change, "p": 10000, "amount": 100},
            ]}}
            with self.subTest(direction=direction), patch("data.public_sources._fetch_json", return_value=payload) as fetch:
                stocks = public_sources.fetch_public_a_stock_limit_pool(direction, trade_date=date(2026, 9, 30))
            self.assertEqual(len(stocks), 2)
            self.assertEqual(fetch.call_count, 1)
            url = urlsplit(fetch.call_args.args[0])
            self.assertTrue(url.path.endswith(endpoint))
            self.assertEqual(parse_qs(url.query)["pagesize"], ["100"])
            self.assertEqual(parse_qs(url.query)["sort"], ["amount:desc"])
            self.assertEqual(parse_qs(url.query)["date"], ["20260930"])

    def test_empty_limit_pool_is_valid_but_stale_or_broken_data_is_not(self):
        """Given 日期准确的空涨停池 When 读取 Then 返回空集；旧日期缺页错码和假空集必须报错"""
        valid = {"rc": 0, "data": {"tc": 0, "qdate": 20260930, "pool": []}}
        with patch("data.public_sources._fetch_json", return_value=valid):
            self.assertEqual(public_sources.fetch_public_a_stock_limit_pool("up", trade_date=date(2026, 9, 30)), [])
        row = {"c": "600001", "n": "测试1", "zdp": 10, "p": 10000, "amount": 200}
        invalid = [
            {"rc": 1, "data": valid["data"]}, {"rc": 0, "data": None},
            {"rc": 0, "data": {**valid["data"], "qdate": 20260929}},
            {"rc": 0, "data": {**valid["data"], "tc": 1}},
            {"rc": 0, "data": {"tc": 2, "qdate": 20260930, "pool": [row, row]}},
            *[{"rc": 0, "data": {"tc": 1, "qdate": 20260930, "pool": [{**row, **change}]}}
              for change in ({"c": "invalid"}, {"zdp": -10}, {"amount": "NaN"}, {"p": 0})],
        ]
        for payload in invalid:
            with self.subTest(payload=payload), patch("data.public_sources._fetch_json", return_value=payload):
                with self.assertRaises(DataRefreshError):
                    public_sources.fetch_public_a_stock_limit_pool("up", trade_date=date(2026, 9, 30))

    def test_limit_pool_reads_at_most_100_and_checks_completeness(self):
        """Given 当日涨停超过100只 When 获取 Then 只读取成交额前100且少一只都不能称为完整池"""
        rows = [{"c": f"600{i:03d}", "n": "测试", "zdp": 10, "p": 10000, "amount": 200 - i} for i in range(100)]
        payload = {"rc": 0, "data": {"tc": 150, "qdate": 20260930, "pool": rows}}
        with patch("data.public_sources._fetch_json", return_value=payload) as fetch:
            stocks = public_sources.fetch_public_a_stock_limit_pool("up", trade_date=date(2026, 9, 30))
        self.assertEqual(len(stocks), 100)
        self.assertEqual(fetch.call_count, 1)
        for invalid in (rows[:99], list(reversed(rows)), rows + [rows[0]]):
            with patch("data.public_sources._fetch_json", return_value={
                "rc": 0, "data": {**payload["data"], "pool": invalid},
            }):
                with self.assertRaises(DataRefreshError):
                    public_sources.fetch_public_a_stock_limit_pool("up", trade_date=date(2026, 9, 30))

    def test_limit_pool_cap_and_empty_scan(self):
        """Given 选择涨跌停100 When 扫描 Then 最多取100且空股票池正常结束而不是请求全市场"""
        for pool in ("limit_up", "limit_down"):
            for size in (0, 105):
                scanner = MarketScanner()
                stocks = [StockInfo(f"600{i:03d}", "测试") for i in range(size)]
                with self.subTest(pool=pool, size=size), \
                     patch("analysis.market_scanner.fetch_public_a_stock_limit_pool", return_value=stocks), \
                     patch("analysis.market_scanner.fetch_public_a_stock_gainers") as gainers, \
                     patch("analysis.market_scanner._analyze_market_stock", return_value=None) as analyze:
                    scanner.start("knn", 10, pool=pool)
                    scanner._thread.join(3)
                state = scanner.snapshot()
                self.assertEqual(state.pool, pool)
                self.assertEqual(state.status, "finished")
                self.assertEqual(state.universe_limit, 100)
                self.assertEqual(state.fetched_count, min(size, 100))
                self.assertEqual(analyze.call_count, min(size, 100))
                self.assertTrue(state.universe_date)
                gainers.assert_not_called()

    def test_switch_loads_latest_pool_without_waiting_for_all_old_stocks(self):
        """Given 旧池占满分析线程 When 切换 Then 新池立即加载且任一线程释放即分析新池，不等旧池全部结束"""
        scanner = MarketScanner()
        waiting = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        loaded, analyzed = threading.Event(), []

        def analyze(symbol, *args):
            analyzed.append(symbol)
            if symbol in {"600001", "600002"}:
                index = int(symbol[-1]) - 1
                waiting[index].set()
                release[index].wait(5)
            return ScanResult(symbol, "测试", "BUY", 80, .7, "依据", "风险")

        def load(**kwargs):
            loaded.set()
            return [StockInfo("600004", "新池")]

        with patch.dict("os.environ", {"MARKET_SCAN_WORKERS": "2"}), \
             patch("analysis.market_scanner.fetch_public_a_stock_gainers",
                   return_value=[StockInfo(f"60000{i}", "旧池") for i in range(1, 4)]), \
             patch("analysis.market_scanner.fetch_public_a_stock_losers", side_effect=load), \
             patch("analysis.market_scanner._analyze_market_stock", side_effect=analyze):
            scanner.start("knn", 10)
            try:
                self.assertTrue(all(event.wait(2) for event in waiting))
                worker = scanner._thread
                scanner.start("knn", 10, pool="losers")
                self.assertIs(scanner._thread, worker)
                state = scanner.snapshot()
                self.assertEqual(state.pool, "losers")
                self.assertNotEqual(state.status, "switching")
                self.assertEqual(state.top_results, [])
                self.assertTrue(loaded.wait(1), "新池读取不能等待旧股票结束")
                self.assertNotIn("600004", analyzed, "分析线程总数仍受限")
                release[0].set()
                scanner._thread.join(2)
                self.assertFalse(scanner._thread.is_alive(), "一条线程释放后新池即可完成")
                self.assertEqual(scanner.snapshot().scanned_count, 1)
                self.assertEqual([item["symbol"] for item in scanner.snapshot().top_results], ["600004"])
                self.assertNotIn("600003", analyzed, "旧池未开始的股票必须取消")
            finally:
                for event in release:
                    event.set()
                scanner._thread.join(5)
                with scanner._lock:
                    inflight = list(scanner._inflight)
                wait(inflight, timeout=3)
        self.assertEqual(scanner.snapshot().scanned_count, 1)
        self.assertEqual([item["symbol"] for item in scanner.snapshot().top_results], ["600004"])
        self.assertNotIn("600001", [key[0] for key in scanner._cache])
        self.assertNotIn("600002", [key[0] for key in scanner._cache])

    def test_switch_does_not_wait_for_old_universe_request(self):
        """Given 旧池榜单网络请求未返回 When 切换 Then 新池能独立完成且旧榜单不会派发分析"""
        scanner = MarketScanner()
        waiting, release = threading.Event(), threading.Event()

        def load(**kwargs):
            waiting.set()
            release.wait(5)
            self.assertTrue(kwargs["should_stop"]())
            return [StockInfo("600001", "旧池")]

        with patch("analysis.market_scanner.fetch_public_a_stock_gainers", side_effect=load), \
             patch("analysis.market_scanner.fetch_public_a_stock_losers", return_value=[StockInfo("600002", "新池")]), \
             patch("analysis.market_scanner._analyze_market_stock",
                   return_value=ScanResult("600002", "测试", "BUY", 80, .7, "依据", "风险")) as analyze:
            scanner.start("knn", 10)
            try:
                self.assertTrue(waiting.wait(2))
                scanner.start("knn", 10, pool="losers")
                scanner._thread.join(2)
                self.assertFalse(scanner._thread.is_alive(), "新池不能等待旧榜单请求返回")
                self.assertEqual(scanner.snapshot().scanned_count, 1)
                analyze.assert_called_once_with("600002", "新池", "knn")
            finally:
                release.set()
                scanner._thread.join(5)
                with scanner._lock:
                    inflight = list(scanner._inflight)
                wait(inflight, timeout=3)
        self.assertEqual(scanner.snapshot().pool, "losers")

    def test_rapid_switches_reuse_bounded_executors_and_cancel_obsolete_queued_loads(self):
        """Given 两条旧榜单请求占满读取线程 When 快速切换 Then 复用有界线程池且空位只交给最终TAB"""
        scanner = MarketScanner()
        waiting = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        latest_queued = threading.Event()

        def load(index, **kwargs):
            waiting[index].set()
            release[index].wait(5)
            return [StockInfo("600001", "旧池")]

        submit = scanner._submit

        def record_submit(executor, operation, *args):
            future = submit(executor, operation, *args)
            if args[0] == "limit_up":
                latest_queued.set()
            return future

        with patch.object(scanner, "_submit", side_effect=record_submit), \
             patch("analysis.market_scanner.fetch_public_a_stock_gainers", side_effect=lambda **kw: load(0, **kw)) as gainers, \
             patch("analysis.market_scanner.fetch_public_a_stock_losers", side_effect=lambda **kw: load(1, **kw)) as losers, \
             patch("analysis.market_scanner.fetch_public_a_stock_limit_pool",
                   return_value=[StockInfo("600002", "新池")]) as limits, \
             patch("analysis.market_scanner._analyze_market_stock",
                   return_value=ScanResult("600002", "测试", "BUY", 80, .7, "依据", "风险")) as analyze:
            scanner.start("knn", 10)
            try:
                self.assertTrue(waiting[0].wait(2))
                executors = (scanner._executor, scanner._universe_executor)
                scanner.start("knn", 10, pool="losers")
                self.assertTrue(waiting[1].wait(2))
                for pool in ["limit_down", "gainers", "losers"] * 10 + ["limit_up"]:
                    scanner.start("knn", 10, pool=pool)
                    self.assertEqual((scanner._executor, scanner._universe_executor), executors)
                self.assertTrue(latest_queued.wait(2))
                limits.assert_not_called()
                release[0].set()
                scanner._thread.join(2)
                self.assertFalse(scanner._thread.is_alive())
                gainers.assert_called_once()
                losers.assert_called_once()
                self.assertEqual([call.args[0] for call in limits.call_args_list], ["up"])
                analyze.assert_called_once_with("600002", "新池", "knn")
                self.assertEqual(scanner.snapshot().pool, "limit_up")
            finally:
                for event in release:
                    event.set()
                scanner._thread.join(5)
                with scanner._lock:
                    inflight = list(scanner._inflight)
                wait(inflight, timeout=3)

    def test_invalid_pool_is_rejected_without_changing_active_run(self):
        """Given 有正在分析的股票池 When 提交未知池 Then HTTP400且不停止或修改现有任务"""
        from web import QuantWebHandler
        scanner = MarketScanner()
        before = scanner.snapshot()
        with self.assertRaises(ValueError):
            scanner.start("knn", 10, pool="unknown")
        self.assertEqual(scanner.snapshot(), before)
        handler = QuantWebHandler.__new__(QuantWebHandler)
        handler._read_json = Mock(return_value={"pool": "unknown"})
        handler._send_json = Mock()
        with patch("web.MARKET_SCANNER", scanner), patch("web.INDUSTRIES.refresh") as refresh:
            handler._handle_market_scan_start()
        self.assertEqual(handler._send_json.call_args.kwargs["status"], 400)
        refresh.assert_not_called()

    def test_http_forwards_selected_pool_and_rejects_invalid_types(self):
        """Given 四个TAB请求 When HTTP启动 Then 保留所选池；未知池或错误类型返回400"""
        from web import QuantWebHandler
        for pool in ("gainers", "losers", "limit_down", "limit_up", None, [], "unknown"):
            scanner = MarketScanner()
            handler = QuantWebHandler.__new__(QuantWebHandler)
            handler._read_json = Mock(return_value={"pool": pool})
            handler._send_json = Mock()
            with patch("web.MARKET_SCANNER", scanner), patch("web.INDUSTRIES.refresh"), \
                 patch.object(scanner, "_run_once", return_value=True):
                handler._handle_market_scan_start()
                if scanner._thread:
                    scanner._thread.join(3)
            if isinstance(pool, str) and pool != "unknown":
                self.assertEqual(handler._send_json.call_args.args[0]["snapshot"]["pool"], pool)
            else:
                self.assertEqual(handler._send_json.call_args.kwargs["status"], 400)

    def test_stop_is_immediate_while_universe_request_is_blocked_and_allows_retry(self):
        """Given 榜单网络请求未结束 When 停止 Then 立即显示已停止且可启动另一池，不分析迟到的旧榜单"""
        scanner = MarketScanner()
        waiting, release = threading.Event(), threading.Event()

        def load(**kwargs):
            waiting.set()
            release.wait(5)
            return [StockInfo("600001", "测试")]

        with patch("analysis.market_scanner.fetch_public_a_stock_gainers", side_effect=load), \
             patch("analysis.market_scanner.fetch_public_a_stock_losers", return_value=[]) as losers, \
             patch("analysis.market_scanner._analyze_market_stock") as analyze:
            scanner.start("knn", 10)
            try:
                self.assertTrue(waiting.wait(2))
                scanner.stop()
                self.assertFalse(scanner.snapshot().running)
                self.assertEqual(scanner.snapshot().status, "stopped")
                scanner.start("knn", 10, pool="losers")
                scanner._thread.join(2)
                self.assertFalse(scanner._thread.is_alive())
                losers.assert_called_once()
            finally:
                release.set()
                scanner._thread.join(3)
                with scanner._lock:
                    inflight = list(scanner._inflight)
                wait(inflight, timeout=3)
            self.assertFalse(scanner.snapshot().running)
            analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
