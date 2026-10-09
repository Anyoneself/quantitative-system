from concurrent.futures import Future
from dataclasses import fields, replace
from datetime import datetime, timezone
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from analysis.market_scanner import MarketScanner
from analysis.advisor import Advice
from analysis.patterns import PatternResult
from analysis.scanner import ScanResult
from analysis.sell_advisor import build_snapshot_sell_advice
from data.models import StockInfo
from desktop.tonghuashun import TonghuashunBridge
from test_desktop_bridge import FakeClient, account_page
from test_holdings_risk import NOW, histories, snapshot


def candidate(index, score=80, action="BUY"):
    return ScanResult(f"600{index:03d}", f"测试{index}", action, score, .7,
                      "测试依据", "测试风险", "2026-09-30", 10)


class MarketWorkbenchScenarios(unittest.TestCase):
    def test_market_risk_is_brief_and_full_analysis_is_retained_in_snapshot(self):
        """Given 多项风险及缓存行情 When 生成榜单 Then 风险摘要精简且完整分析随快照供行内展开"""
        from analysis.market_scanner import _analyze_market_stock
        patterns = PatternResult(**{field.name: False for field in fields(PatternResult)})
        patterns = replace(patterns, volume_drop_below_ma20=True, low_liquidity=True)
        advice = Advice("AVOID", 30, ["完整分析理由"], ["完整量价证据"],
                        ["放量跌破20日均线，趋势转弱风险较高。", "成交额偏低，存在流动性风险。"],
                        ["观察是否重新站上20日均线。"],
                        SimpleNamespace(analysis_date=datetime.now(timezone.utc).date().isoformat(),
                                        name="测试", close=10, ma250=12),
                        patterns, SimpleNamespace(buy_probability=.3))
        source = "腾讯前复权 · 本地缓存（采集 09-30 18:00）"
        with patch("analysis.market_scanner.fetch_public_market_stock_bars", return_value=([], source)), \
             patch("analysis.market_scanner.build_advice", return_value=advice):
            result = _analyze_market_stock("600001", "测试", "knn")
        self.assertLessEqual(len(result.risk), 50)
        self.assertIn("放量破位", result.risk)
        self.assertIn("模型信号偏弱", result.risk)
        self.assertIn("缓存非实时", result.risk)
        self.assertIn("未计资金流", result.risk)
        self.assertNotIn("成交额偏低", result.risk)
        scanner = MarketScanner()
        future = Future()
        future.set_result(result)
        scanner._record_finished_future(future, 10)
        item = scanner.snapshot().top_results[0]
        self.assertEqual(list(item["reasons"]), advice.reasons)
        self.assertEqual(list(item["evidence"]), advice.evidence)
        self.assertEqual(list(item["observations"]), advice.observations)
        self.assertEqual(list(item["risks"][:2]), advice.risks)
        self.assertIn("资金流未参与评分。", item["risks"])
        self.assertTrue(any("非实时行情" in risk for risk in item["risks"]))
        self.assertEqual(item["quote_source"], source)
        self.assertEqual(len(advice.risks), 2, "精简不得改写原始分析")

    def test_risk_summary_uses_confirmed_signals_without_inventing_missing_data(self):
        """Given 模型及资金流有效且有追高风险 When 精简 Then 仅提示已确认风险而非无条件添加缺失说明"""
        from analysis.market_scanner import _summarize_market_risk
        patterns = PatternResult(**{field.name: False for field in fields(PatternResult)})
        patterns = replace(patterns, close_above_ma20=True, ma20_up=True, high_20d_return=True)
        advice = Advice("BUY", 70, [], [], ["近20日涨幅超过30%，追高回撤风险上升。"], [],
                        SimpleNamespace(ma250=0), patterns, SimpleNamespace(buy_probability=.7),
                        SimpleNamespace(fund_flow_score_adjustment=0))
        self.assertEqual(_summarize_market_risk(advice, "腾讯前复权"), "追高回撤风险。")
        neutral = replace(advice, patterns=replace(patterns, high_20d_return=False), risks=[])
        self.assertEqual(_summarize_market_risk(neutral, "腾讯前复权"), "仍需控制仓位。")

    def test_risk_summary_covers_each_existing_negative_signal(self):
        """Given 各类已确认负面形态 When 分别精简 Then 关键风险不会被泛化为无风险"""
        from analysis.market_scanner import _summarize_market_risk
        patterns = PatternResult(**{field.name: False for field in fields(PatternResult)})
        patterns = replace(patterns, close_above_ma20=True, ma20_up=True, close_above_ma250=True)
        advice = Advice("WATCH", 55, [], [], [], [], SimpleNamespace(ma250=10), patterns,
                        SimpleNamespace(buy_probability=.6), SimpleNamespace(fund_flow_score_adjustment=0))
        for flag, enabled, summary in [
            ("low_liquidity", True, "流动性偏低"), ("high_volume_stagnation", True, "放量滞涨"),
            ("price_down_volume_up", True, "放量下跌"), ("macd_dead_cross", True, "MACD死叉"),
            ("macd_histogram_shrinking", True, "MACD动能减弱"),
            ("close_above_ma20", False, "未站上20日线"), ("ma20_up", False, "20日线未走强"),
            ("close_above_ma250", False, "未站上年线"),
        ]:
            with self.subTest(flag=flag):
                current = replace(advice, patterns=replace(patterns, **{flag: enabled}))
                self.assertIn(summary, _summarize_market_risk(current, "腾讯前复权"))
        self.assertIn("资金流偏弱", _summarize_market_risk(
            replace(advice, fund_flow=SimpleNamespace(fund_flow_score_adjustment=-5)), "腾讯前复权"))
        self.assertIn("顶背驰", _summarize_market_risk(
            replace(advice, divergence=SimpleNamespace(bearish_divergence=True)), "腾讯前复权"))

    def test_buy_reason_keeps_only_two_confirmed_signals_and_preserves_details(self):
        """Given 买入结果含长篇模型解释及多个形态 When 生成榜单 Then 仅保留两项实际信号且不修改详情"""
        from analysis.market_scanner import _summarize_market_reason
        patterns = PatternResult(**{field.name: False for field in fields(PatternResult)})
        patterns = replace(patterns, breakout_with_volume=True, macd_golden_cross_above_zero=True,
                           close_above_ma20=True, ma20_up=True)
        advice = Advice("BUY", 80, ["完整评分与概率说明"], ["训练样本及完整证据"],
                        ["原始风险"], [], None, patterns, None)
        self.assertEqual(_summarize_market_reason(advice), "放量突破20日高点；MACD零轴上方金叉。")
        self.assertEqual(advice.reasons, ["完整评分与概率说明"])
        self.assertEqual(advice.evidence, ["训练样本及完整证据"])
        self.assertEqual(advice.score, 80)
        self.assertEqual(advice.risks, ["原始风险"])

    def test_unconfirmed_actions_do_not_present_buying_reasons(self):
        """Given 观察中性或回避结论 When 精简核心理由 Then 不将正面形态包装为已达到买入条件"""
        from analysis.market_scanner import _summarize_market_reason
        for action in ("WATCH", "HOLD", "AVOID"):
            with self.subTest(action=action):
                advice = Advice(action, 50, [], [], [], [], None, None, None)
                reason = _summarize_market_reason(advice)
                self.assertIn("买入", reason)
                self.assertLessEqual(len(reason), 40)
                self.assertNotIn("放量突破", reason)

    def test_buy_reason_without_pattern_confirmation_does_not_invent_signals(self):
        """Given 仅模型达到观察门槛且没有已确认形态 When 精简 Then 不虚构突破或资金流入"""
        from analysis.market_scanner import _summarize_market_reason
        patterns = PatternResult(**{field.name: False for field in fields(PatternResult)})
        advice = Advice("BUY", 60, [], [], [], [], None, patterns, None)
        self.assertEqual(_summarize_market_reason(advice), "模型达到买入观察门槛，量价仍待确认。")

    def test_every_holding_has_individual_decision_and_cost_thresholds(self):
        """Given 盈利亏损及成本缺失的多只持仓 When 同步 Then 每只都有对应建议及可计算的提醒价格"""
        current = snapshot()
        current["positions"] = [
            {"symbol": "600001", "quantity": 100, "available": 100, "price": "8", "cost": "10", "market_value": "800"},
            {"symbol": "600002", "quantity": 100, "available": 0, "price": "13", "cost": "10", "market_value": "1300"},
            {"symbol": "600003", "quantity": 100, "available": 100, "price": "10", "cost": "0", "market_value": "1000"},
        ]
        items = build_snapshot_sell_advice(current, now=NOW)["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual(len({item["decision"] for item in items}), 3)
        self.assertAlmostEqual(items[0]["stop_loss_price"], 9.2)
        self.assertAlmostEqual(items[1]["profit_watch_price"], 12)
        self.assertIn("止盈观察", items[1]["decision"])
        self.assertIn("不可卖", items[1]["execution_note"])
        self.assertIsNone(items[2]["stop_loss_price"])
        self.assertIn("成本", items[2]["decision"])
        for item in items:
            self.assertTrue(item["next_step"])

    def test_one_missing_history_does_not_remove_another_holdings_advice(self):
        """Given 某只持仓行情缺失 When 逐股分析 Then 其他持仓仍有完整建议而缺失项注明原因"""
        current = snapshot()
        current["positions"] = [
            {"symbol": symbol, "quantity": 100, "available": 100, "price": "10", "cost": "9", "market_value": "1000"}
            for symbol in ("600001", "600002")
        ]
        data = histories()
        del data["600002"]
        items = build_snapshot_sell_advice(current, data, now=NOW)["items"]
        self.assertEqual(items[0]["status"], "READY")
        self.assertEqual(items[1]["status"], "PARTIAL")
        self.assertTrue(items[1]["decision"])
        self.assertIn("不足", items[1]["history_note"])

    def test_holding_advice_is_published_before_another_slow_holding_finishes(self):
        """Given 第二只持仓行情尚未完成 When 第一只已算好 Then 立即返回第一只建议不等待整批"""
        waiting, release = threading.Event(), threading.Event()
        data = histories()

        def loader(symbol):
            if symbol == "600002":
                waiting.set()
                release.wait(3)
            return data[symbol]

        client = FakeClient()
        client.page = account_page("2000", [
            [symbol, "测试", "100", "100", "10", "9", "1000"] for symbol in ("600001", "600002")
        ])
        bridge = TonghuashunBridge(client, clock=lambda: NOW, history_loader=loader)
        bridge.perform("history-enable")
        bridge.perform("sync")
        try:
            self.assertTrue(waiting.wait(2))
            items = bridge.status()["holding_advice"]["items"]
            self.assertEqual(items[0]["status"], "READY")
            self.assertEqual(items[1]["status"], "PARTIAL")
        finally:
            release.set()
            bridge.risk_thread.join(3)

    def test_completed_stock_publishes_while_another_ranked_stock_is_blocked(self):
        """Given 涨幅股票池已读取但另一只分析较慢 When 一只完成 Then 立即推送榜单不等待整批"""
        scanner = MarketScanner()
        waiting, release = threading.Event(), threading.Event()

        def analyze(symbol, *args):
            if symbol == "600002":
                waiting.set()
                release.wait(3)
            else:
                waiting.wait(2)
            return candidate(int(symbol[-1]))

        with patch("analysis.market_scanner.fetch_public_a_stock_gainers",
                   return_value=[StockInfo("600001", "测试1"), StockInfo("600002", "测试2")]), \
             patch("analysis.market_scanner._analyze_market_stock", side_effect=analyze):
            scanner.start("knn", 10)
            try:
                self.assertTrue(waiting.wait(2))
                version = scanner.snapshot().version
                for _ in range(8):
                    state = scanner.wait_for_update(version, .1)
                    version = state.version
                    if state.scanned_count:
                        break
                self.assertEqual(state.scanned_count, 1)
                self.assertTrue(state.running)
                self.assertEqual(state.top_results[0]["symbol"], "600001")
            finally:
                release.set()
                scanner._thread.join(3)

    def test_each_completion_has_an_ordered_event_and_ranking_is_capped_at_ten(self):
        """Given 连续完成十二只股票 When 消费更新 Then 不合并丢失完成事件且榜单最多十只"""
        scanner = MarketScanner()
        version = scanner.snapshot().version
        for index in range(12):
            future = Future()
            future.set_result(candidate(index, 60 + index))
            scanner._record_finished_future(future, 10)
        counts = []
        for _ in range(12):
            state = scanner.wait_for_update(version, 0)
            counts.append(state.scanned_count)
            version = state.version
        self.assertEqual(counts, list(range(1, 13)))
        self.assertEqual(len(state.top_results), 10)
        self.assertEqual(state.top_results[0]["score"], 71)

    def test_first_five_successful_analyses_all_appear_with_their_original_actions(self):
        """Given 前五只包含观察中性回避及买入信号 When 逐只分析完成 Then 榜单逐只增加到五行且不改写结论"""
        scanner = MarketScanner()
        actions = ["WATCH", "HOLD", "AVOID", "WATCH", "BUY"]
        scanner._total_count = 500
        version = scanner.snapshot().version
        for index, action in enumerate(actions):
            future = Future()
            future.set_result(candidate(index, score=60 + index, action=action))
            scanner._record_finished_future(future, 10)
            state = scanner.wait_for_update(version, 0)
            version = state.version
            self.assertEqual(state.successful_count, index + 1)
            self.assertEqual(len(state.top_results), index + 1)
            self.assertEqual([item["action"] for item in state.top_results], actions[:index + 1][::-1])
            self.assertEqual(state.top_results[0]["symbol"], f"600{index:03d}")

    def test_failed_analysis_is_not_reported_as_success_or_ranked(self):
        """Given 已处理五只但两只行情失败 When 展示状态 Then 明确有效三只失败两只且榜单只有三条真实结论"""
        scanner = MarketScanner()
        scanner._status = "scanning"
        scanner._total_count = 500
        for index, action in enumerate(["WATCH", "NO_DATA", "AVOID", "HOLD", "NO_DATA"]):
            future = Future()
            future.set_result(candidate(index, action=action))
            scanner._record_finished_future(future, 10)
        state = scanner.snapshot()
        self.assertEqual(len(state.top_results), 3)
        self.assertEqual(state.successful_count, 3)
        self.assertEqual(state.failed_count, 2)
        self.assertIn("已处理 5/500", state.status_message)
        self.assertIn("有效 3", state.status_message)
        self.assertIn("失败 2", state.status_message)
        self.assertNotIn("已完成", state.status_message)

    def test_source_outage_is_deferred_not_500_stock_failures_and_can_retry(self):
        """Given 行情源全部不可用 When 筛选500只 Then 记为待重试而非500只分析失败，恢复后可重新分析"""
        from data.errors import DataSourceUnavailable
        scanner = MarketScanner()
        stocks = [StockInfo(f"600{index:03d}", "测试") for index in range(500)]
        with patch("analysis.market_scanner.fetch_public_a_stock_gainers", return_value=stocks), \
             patch("analysis.market_scanner._analyze_market_stock", side_effect=DataSourceUnavailable("数据源暂不可用")):
            scanner.start("knn", 10)
            scanner._thread.join(3)
        state = scanner.snapshot()
        self.assertFalse(state.running)
        self.assertEqual(state.status, "data_unavailable")
        self.assertEqual(state.scanned_count, 0)
        self.assertEqual(state.failed_count, 0)
        self.assertEqual(state.deferred_count, 500)
        self.assertEqual(scanner._cache, {}, "数据源暂停不得缓存成个股失败")
        with patch("analysis.market_scanner.fetch_public_a_stock_gainers", return_value=stocks[:2]), \
             patch("analysis.market_scanner._analyze_market_stock", side_effect=lambda symbol, *args: candidate(int(symbol[-3:]))):
            scanner.start("knn", 10)
            scanner._thread.join(3)
        self.assertEqual(scanner.snapshot().successful_count, 2)
        self.assertEqual(scanner.snapshot().deferred_count, 0)

    def test_source_outage_does_not_discard_other_cached_analysis_results(self):
        """Given 部分股票可用缓存而另一只无可用数据 When 筛选 Then 保留有效榜单并单独统计待重试股票"""
        from data.errors import DataSourceUnavailable
        scanner = MarketScanner()
        with patch("analysis.market_scanner.fetch_public_a_stock_gainers",
                   return_value=[StockInfo("600001", "测试1"), StockInfo("600002", "测试2")]), \
             patch("analysis.market_scanner._analyze_market_stock", side_effect=[candidate(1), DataSourceUnavailable("暂停")]):
            scanner.start("knn", 10)
            scanner._thread.join(3)
        state = scanner.snapshot()
        self.assertEqual(state.successful_count, 1)
        self.assertEqual(len(state.top_results), 1)
        self.assertEqual(state.failed_count, 0)
        self.assertEqual(state.deferred_count, 1)

    def test_stream_replays_early_completions_from_scan_start_cursor(self):
        """Given 快速缓存扫描已完成 When 浏览器刚建立事件流 Then 补发本轮早于连接完成的逐股更新"""
        from web import QuantWebHandler
        scanner = MarketScanner()
        for index in range(3):
            future = Future()
            future.set_result(candidate(index))
            scanner._record_finished_future(future, 10)
        handler = QuantWebHandler.__new__(QuantWebHandler)
        handler.path = "/api/market-scan/stream?after=0"
        handler.send_response = handler.send_header = handler.end_headers = Mock()
        counts = []

        def send(state):
            counts.append(state.scanned_count)
            return len(counts) < 3

        handler._send_event = send
        with patch("web.MARKET_SCANNER", scanner):
            handler._handle_market_scan_stream()
        self.assertEqual(counts, [1, 2, 3])

    def test_status_cursor_replays_each_completion_after_stream_disconnects(self):
        """Given 实时连接中断期间完成三只股票 When 按游标读取状态 Then 逐只补发且最后才返回结束状态"""
        from web import QuantWebHandler
        scanner = MarketScanner()
        for index, action in enumerate(["BUY", "WATCH", "NO_DATA"]):
            future = Future()
            future.set_result(candidate(index, action=action))
            scanner._record_finished_future(future, 10)
        with scanner._lock:
            scanner._status = "finished"
            scanner._mark_changed_locked()
        handler = QuantWebHandler.__new__(QuantWebHandler)
        handler._send_json = Mock()
        version, states = 0, []
        with patch("web.MARKET_SCANNER", scanner):
            for _ in range(4):
                handler.path = f"/api/market-scan/status?after={version}"
                handler.do_GET()
                state = handler._send_json.call_args.args[0]["snapshot"]
                states.append(state)
                version = state["version"]
        self.assertEqual([state["scanned_count"] for state in states], [1, 2, 3, 3])
        self.assertEqual([state["failed_count"] for state in states], [0, 0, 1, 1])
        self.assertEqual([len(state["top_results"]) for state in states], [1, 2, 2, 2])
        self.assertEqual(states[-1]["status"], "finished")

    def test_status_cursor_waits_for_next_completion_instead_of_fixed_interval(self):
        """Given 游标已追上进度 When 降级读取 Then 长轮询等待下一次更新而非每秒只读最新值"""
        from web import QuantWebHandler
        scanner = MarketScanner()
        handler = QuantWebHandler.__new__(QuantWebHandler)
        handler.path = "/api/market-scan/status?after=0"
        handler._send_json = Mock()
        with patch("web.MARKET_SCANNER", scanner), \
             patch.object(scanner, "wait_for_update", return_value=scanner.snapshot()) as wait:
            handler.do_GET()
        wait.assert_called_once_with(0, 25)

    def test_status_without_valid_cursor_still_returns_latest_snapshot(self):
        """Given 首次打开或无效游标 When 读取状态 Then 兼容原状态接口并立即返回最新快照"""
        from web import QuantWebHandler
        scanner = MarketScanner()
        handler = QuantWebHandler.__new__(QuantWebHandler)
        handler._send_json = Mock()
        for query in ["", "?after=invalid", "?after="]:
            with self.subTest(query=query), patch("web.MARKET_SCANNER", scanner), \
                 patch.object(scanner, "wait_for_update") as wait:
                handler.path = f"/api/market-scan/status{query}"
                handler.do_GET()
                wait.assert_not_called()
                self.assertEqual(handler._send_json.call_args.args[0]["snapshot"]["version"], 0)

    def test_warm_scan_reuses_results_without_repeating_analysis(self):
        """Given 同一日线时段内已有有效分析 When 再次筛选 Then 使用短期缓存并逐只更新榜单"""
        scanner = MarketScanner()
        stocks = [StockInfo(f"600{index:03d}", f"测试{index}") for index in range(3)]
        with patch("analysis.market_scanner.fetch_public_a_stock_gainers", return_value=stocks), \
             patch("analysis.market_scanner._analyze_market_stock",
                   side_effect=lambda symbol, name, algorithm: candidate(int(symbol[-3:]))) as analyze:
            scanner.start("knn", 10)
            scanner._thread.join(3)
            scanner.start("knn", 10)
            scanner._thread.join(3)
        self.assertEqual(analyze.call_count, 3)
        self.assertEqual(scanner.snapshot().scanned_count, 3)
        self.assertEqual(scanner.snapshot().cached_count, 3)

    def test_cache_is_invalidated_at_close_and_by_algorithm_or_age(self):
        """Given 盘前时段内缓存 When 跨越收盘、更换模型或缓存到期 Then 重新分析而非保留旧榜单"""
        scanner = MarketScanner()
        with patch("analysis.market_scanner._analyze_market_stock", return_value=candidate(1)) as analyze, \
             patch("analysis.market_scanner.datetime", wraps=datetime) as clock, \
             patch("analysis.market_scanner.time.monotonic", return_value=100) as monotonic:
            clock.now.return_value = datetime(2026, 9, 30, 14, tzinfo=timezone.utc)
            scanner._analyze_cached("600001", "测试", "knn")
            scanner._analyze_cached("600001", "测试", "knn")
            self.assertEqual(analyze.call_count, 1)
            clock.now.return_value = datetime(2026, 9, 30, 15, tzinfo=timezone.utc)
            scanner._analyze_cached("600001", "测试", "knn")
            scanner._analyze_cached("600001", "测试", "robust_ensemble")
            monotonic.return_value = 1000
            scanner._analyze_cached("600001", "测试", "robust_ensemble")
            self.assertEqual(analyze.call_count, 4)

    def test_late_result_from_previous_run_cannot_pollute_new_ranking(self):
        """Given 已结束任务仍有慢请求 When 新任务开始后旧结果到达 Then 不改写新任务榜单"""
        scanner = MarketScanner()
        scanner._run_id = 2
        scanner._running = True
        future = Future()
        future.set_result(candidate(1))
        scanner._record_finished_future(future, 10, run_id=1)
        self.assertEqual(scanner.snapshot().scanned_count, 0)

    def test_finished_scan_distinguishes_failed_coverage_from_valid_no_candidates(self):
        """Given 行情全部或部分失败 When 本轮结束 Then 状态明确覆盖失败而非宣称全量有效完成"""
        for actions, message in [
            (["NO_DATA", "NO_DATA"], "全部获取失败"),
            (["NO_DATA", "WATCH"], "覆盖不完整"),
            (["WATCH", "WATCH"], "本轮筛选完成"),
        ]:
            with self.subTest(actions=actions):
                scanner = MarketScanner()
                scanner._status = "finished"
                scanner._fetched_count = scanner._total_count = len(actions)
                for index, action in enumerate(actions):
                    future = Future()
                    future.set_result(candidate(index, action=action))
                    scanner._record_finished_future(future, 10)
                state = scanner.snapshot()
                self.assertIn(message, state.status_message)
                self.assertEqual(len(state.top_results), actions.count("WATCH"))
                self.assertTrue(all(item["action"] == "WATCH" for item in state.top_results))


if __name__ == "__main__":
    unittest.main()
