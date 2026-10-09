from concurrent.futures import Future
from datetime import timedelta
import unittest
from unittest.mock import patch

from analysis.market_scanner import MarketScanner
from analysis.scanner import ScanResult
from analysis.sell_advisor import _sell_action, build_snapshot_sell_advice
from desktop.tonghuashun import TonghuashunBridge
from test_desktop_bridge import FakeClient, account_page
from test_holdings_risk import NOW, histories, snapshot


class DecisionScenarios(unittest.TestCase):
    def test_maximum_loss_is_not_cancelled_by_a_low_technical_score(self):
        """Given 亏损已越过提醒线 When 技术评分仍偏好 Then 优先给出止损风险建议"""
        self.assertEqual(_sell_action(0, -.09, .08, .20, False), "STOP_LOSS")

    def test_local_holdings_can_flag_loss_without_external_queries(self):
        """Given 未授权行情且当前显示亏损越线 When 分析持仓 Then 本地提示止损并标出不可卖数量"""
        current = snapshot()
        current["positions"] = [{"symbol": "600001", "name": "测试证券", "quantity": 100,
                                 "available": 0, "price": "8", "cost": "10", "market_value": "800"}]
        report = build_snapshot_sell_advice(current, None, now=NOW)
        item = report["items"][0]
        self.assertEqual(report["snapshot_id"], current["snapshot_id"])
        self.assertEqual(item["action"], "STOP_LOSS")
        self.assertEqual(item["available_quantity"], 0)
        self.assertEqual(item["status"], "LOCAL_ONLY")
        self.assertAlmostEqual(item["holding_return"], -.2)
        self.assertIn("不可卖", item["execution_note"])

    def test_missing_history_does_not_invent_a_hold_recommendation(self):
        """Given 盈亏未越线但行情不足 When 分析 Then 明确暂不能判断而非默认继续持有"""
        current = snapshot()
        current["positions"] = [{"symbol": "600001", "name": "测试证券", "quantity": 100,
                                 "available": 100, "price": "10", "cost": "10", "market_value": "1000"}]
        for bars in [None, {}]:
            report = build_snapshot_sell_advice(current, bars, now=NOW)
            self.assertEqual(report["items"][0]["action"], "NO_DATA")

    def test_unavailable_client_price_does_not_become_total_loss(self):
        """Given 客户端当前价为零 When 分析持仓 Then 显示价格不可用而非百分之百亏损止损"""
        current = snapshot()
        current["positions"] = [{"symbol": "600001", "name": "测试证券", "quantity": 100,
                                 "available": 100, "price": "0", "cost": "10", "market_value": "1000"}]
        for bars in [None, histories()]:
            item = build_snapshot_sell_advice(current, bars, now=NOW)["items"][0]
            self.assertEqual(item["action"], "NO_DATA")
            self.assertIsNone(item["holding_return"])
            self.assertIn("价格不可用", item["reason"])

    def test_history_and_snapshot_price_have_separate_roles(self):
        """Given 历史收盘价不同于客户端当前价 When 判断持仓盈亏 Then 使用客户端价格并注明历史日期"""
        current = snapshot()
        current["positions"] = [{"symbol": "600001", "name": "测试证券", "quantity": 100,
                                 "available": 40, "price": "8", "cost": "10", "market_value": "800"}]
        report = build_snapshot_sell_advice(current, histories(), now=NOW)
        item = report["items"][0]
        self.assertEqual(item["status"], "READY")
        self.assertEqual(item["action"], "STOP_LOSS")
        self.assertAlmostEqual(item["holding_return"], -.2)
        self.assertEqual(item["as_of_date"], "2026-09-30")
        self.assertEqual(item["available_quantity"], 40)

    def test_old_history_is_not_a_current_technical_signal(self):
        """Given 公开行情超过期限 When 判断卖出 Then 不发布过期技术结论"""
        current = snapshot()
        current["positions"] = [{"symbol": "600001", "name": "测试证券", "quantity": 100,
                                 "available": 100, "price": "10", "cost": "10", "market_value": "1000"}]
        item = build_snapshot_sell_advice(current, histories(), now=NOW + timedelta(days=10))["items"][0]
        self.assertEqual(item["action"], "NO_DATA")
        self.assertEqual(item["status"], "PARTIAL")

    def test_intraday_history_does_not_include_unfinished_or_future_bars(self):
        """Given 盘中快照 When 分析持仓 Then 排除当天未收盘以及快照之后的行情"""
        current = snapshot()
        current["captured_at"] = "2026-09-30T02:00:00+00:00"
        current["positions"] = [{"symbol": "600001", "name": "测试证券", "quantity": 100,
                                 "available": 100, "price": "10", "cost": "10", "market_value": "1000"}]
        report = build_snapshot_sell_advice(current, histories(), now=NOW)
        self.assertEqual(report["items"][0]["as_of_date"], "2026-09-29")
        current["captured_at"] = "2026-09-29T02:00:00+00:00"
        report = build_snapshot_sell_advice(current, histories(), now=NOW)
        self.assertEqual(report["items"][0]["as_of_date"], "2026-09-28")

    def test_client_sync_returns_suggestions_and_clear_removes_them(self):
        """Given 客户端只读同步 When 无外部授权 Then 返回本地建议并在清空时一起移除"""
        client = FakeClient()
        client.page = account_page("800", [["600001", "测试", "100", "0", "8", "10", "800"]])
        with patch("data.source.fetch_bars") as loader:
            bridge = TonghuashunBridge(client, clock=lambda: NOW)
            state = bridge.perform("sync")
            self.assertEqual(state["holding_advice"]["items"][0]["action"], "STOP_LOSS")
            loader.assert_not_called()
            self.assertIsNone(bridge.perform("clear")["holding_advice"])

    def test_analysis_ranking_keeps_nonbuy_visible_without_changing_buy_signals(self):
        """Given 混合的买入观察与回避信号 When 排名 Then 展示全部有效分析但只有原买入信号标记为买入"""
        scanner = MarketScanner()
        for index, (action, score) in enumerate([("AVOID", 90), ("WATCH", 58), ("BUY", 76), ("BUY", 85)]):
            result = ScanResult(f"60000{index}", "测试", action, score, .7, "测试依据", "测试风险")
            future = Future()
            future.set_result(result)
            scanner._record_finished_future(future, 5)
        result = scanner.snapshot()
        self.assertEqual([item["score"] for item in result.top_results], [90, 85, 76, 58])
        self.assertEqual([item["action"] for item in result.top_results], ["AVOID", "BUY", "BUY", "WATCH"])
        self.assertEqual([item["score"] for item in result.top_results if item["action"] == "BUY"], [85, 76])
        self.assertEqual(result.scanned_count, 4)
        self.assertEqual(result.failed_count, 0)

    def test_unexpected_scanner_failure_does_not_leave_it_running(self):
        """Given 筛选后台出现未预期异常 When 任务退出 Then 发布失败状态并允许重试"""
        scanner = MarketScanner()
        with patch.object(scanner, "_run_once", side_effect=RuntimeError("private diagnostic")):
            scanner.start("robust_ensemble", 5)
            scanner._thread.join(3)
        state = scanner.snapshot()
        self.assertFalse(state.running)
        self.assertEqual(state.status, "error")
        self.assertNotIn("private diagnostic", state.error)


if __name__ == "__main__":
    unittest.main()
