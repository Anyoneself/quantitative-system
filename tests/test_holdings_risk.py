from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import threading
import unittest

from data.models import PriceVolumeBar
from desktop.tonghuashun import TonghuashunBridge
from portfolio.holdings_risk import analyze_holdings
from portfolio.risk import calculate_risk
from test_desktop_bridge import FakeClient, account_page


NOW = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)


def snapshot():
    return {
        "snapshot_id": "test-snapshot", "captured_at": NOW.isoformat(),
        "funds": {"total_assets": "20000", "market_value": "10000", "cash_balance": "12000",
                  "available_cash": "10000", "withdrawable_cash": "9000"},
        "positions": [{"symbol": "600001", "quantity": 1000, "available": 400, "market_value": "6000"},
                      {"symbol": "600002", "quantity": 200, "available": 200, "market_value": "4000"}],
    }


def histories():
    days = [date(2026, 8, 1) + timedelta(days=i) for i in range(61)]
    days = [day for day in days if day.weekday() < 5]
    result = {}
    for symbol, multiplier in [("000300", 1), ("600001", 2), ("600002", .5)]:
        close = 10.0
        rows = []
        for index, day in enumerate(days):
            if index:
                close *= 1 + multiplier * (.01 if index % 2 else -.012)
            rows.append(PriceVolumeBar(symbol, day, close, close, close, close, 10000, close * 10000))
        result[symbol] = rows
    return result


class HoldingsRiskScenarios(unittest.TestCase):
    def test_snapshot_risk_without_history_does_not_claim_low_risk(self):
        """Given 只有当前资产快照 When 自动分析 Then 计算结构风险并保留缺失历史指标"""
        report = analyze_holdings(snapshot(), {}, now=NOW)
        self.assertEqual(report["scope"], "current_holdings")
        self.assertAlmostEqual(report["stock_exposure"], .5)
        self.assertAlmostEqual(report["max_position_weight"], .3)
        self.assertAlmostEqual(report["locked_weight"], .18)
        self.assertAlmostEqual(report["stress_loss_10"], 1000)
        self.assertIsNone(report["annualized_volatility"])
        self.assertIsNone(report["var_95"])
        self.assertEqual(report["history_curve"], [])
        self.assertEqual(report["status"], "PARTIAL")
        self.assertNotEqual(report["risk_level"], "NORMAL")

    def test_current_weights_include_cash_without_renormalization(self):
        """Given 两只持仓及资产差额 When 使用同日期历史行情 Then 按总资产权重估算而非伪造账户业绩"""
        bars = histories()
        report = analyze_holdings(snapshot(), bars, now=NOW)
        benchmark = [current.close / previous.close - 1
                     for previous, current in zip(bars["000300"], bars["000300"][1:])]
        expected = calculate_risk([value * .7 for value in benchmark], benchmark)
        self.assertEqual(report["status"], "READY")
        self.assertAlmostEqual(report["annualized_volatility"], expected.annualized_volatility)
        self.assertAlmostEqual(report["var_95"], expected.var_95)
        self.assertAlmostEqual(report["beta"], .7)
        self.assertEqual(report["sample_size"], len(benchmark))
        self.assertFalse(any("有效日收益样本少于" in reason or "缺少比较基准" in reason
                             for reason in report["reasons"]))
        self.assertIsNone(report["sharpe_ratio"])
        self.assertIsNone(report["win_rate"])
        self.assertIsNone(report["profit_loss_ratio"])
        self.assertEqual(report["history_curve"][0]["net_value"], 1)
        self.assertIn("非账户真实净值", report["explanation"])

    def test_missing_holding_never_becomes_zero_return_or_dropped_weight(self):
        """Given 一只持仓行情不可用 When 分析 Then 不丢弃它或将收益补零"""
        bars = histories()
        del bars["600002"]
        report = analyze_holdings(snapshot(), bars, now=NOW)
        self.assertEqual(report["missing_symbols"], ["600002"])
        self.assertIsNone(report["var_95"])
        self.assertEqual(report["history_curve"], [])
        self.assertAlmostEqual(report["stock_exposure"], .5)

    def test_gaps_do_not_turn_multiday_changes_into_daily_returns(self):
        """Given 某持仓缺少最近一个中间交易日 When 分析 Then 只用缺口之后的连续共同样本"""
        bars = histories()
        del bars["600001"][-3]
        report = analyze_holdings(snapshot(), bars, now=NOW)
        self.assertLess(report["sample_size"], 20)
        self.assertIsNone(report["annualized_volatility"])
        self.assertEqual(report["history_curve"], [])

    def test_stale_or_missing_benchmark_and_invalid_bars_do_not_publish_metrics(self):
        """Given 行情过期、基准缺失或价格异常 When 分析 Then 保留结构风险但不发布历史估计"""
        for change in ["stale", "benchmark", "invalid"]:
            with self.subTest(change=change):
                bars = histories()
                now = NOW
                if change == "stale":
                    now += timedelta(days=15)
                elif change == "benchmark":
                    del bars["000300"]
                else:
                    bars["600001"][-1] = PriceVolumeBar("", date(2026, 9, 30), 0, 0, 0, 0, 1, 1)
                report = analyze_holdings(snapshot(), bars, now=now)
                self.assertIsNone(report["var_95"])
                self.assertEqual(report["status"], "PARTIAL")

    def test_intraday_and_post_snapshot_bars_are_excluded(self):
        """Given 盘中快照及当天动态日线 When 分析 Then 只使用快照之前已收盘日线"""
        current = snapshot()
        current["captured_at"] = "2026-09-30T02:00:00+00:00"
        report = analyze_holdings(current, histories(), now=NOW)
        self.assertEqual(report["as_of_date"], "2026-09-29")

    def test_concentration_and_empty_cash_account_are_not_invented_performance(self):
        """Given 单股重仓或纯现金账户 When 分析 Then 展示结构提示而非推算交易胜率"""
        current = snapshot()
        current["positions"] = [dict(current["positions"][0], market_value="10000")]
        report = analyze_holdings(current, histories(), now=NOW)
        self.assertEqual(report["risk_level"], "HIGH")
        current["positions"] = []
        current["funds"]["market_value"] = "0"
        report = analyze_holdings(current, {}, now=NOW)
        self.assertEqual(report["stock_exposure"], 0)
        self.assertEqual(report["locked_weight"], 0)
        self.assertIsNone(report["beta"])
        self.assertEqual(report["status"], "EMPTY")
        current["funds"]["total_assets"] = "0"
        report = analyze_holdings(current, {}, now=NOW)
        self.assertIsNone(report["stock_exposure"])


class AutomaticRiskScenarios(unittest.TestCase):
    def make_bridge(self, loader):
        client = FakeClient()
        client.page = account_page("1000", [["600001", "测试", "100", "100", "10", "9", "1000"]])
        bridge = TonghuashunBridge(client, clock=lambda: NOW, history_loader=loader)
        return client, bridge

    def test_successful_sync_starts_analysis_and_caches_public_bars(self):
        """Given 客户端已登录且允许公开行情查询 When 同步 Then 自动分析并缓存公开行情"""
        calls = []
        data = histories()

        def loader(symbol):
            calls.append(symbol)
            return data[symbol]

        client, bridge = self.make_bridge(loader)
        bridge.perform("history-enable")
        first = bridge.perform("sync")
        bridge.risk_thread.join(3)
        self.assertFalse(bridge.risk_thread.is_alive())
        report = bridge.status()["risk"]
        self.assertEqual(report["snapshot_id"], first["snapshot"]["snapshot_id"])
        self.assertEqual(report["status"], "READY")
        advice = bridge.status()["holding_advice"]
        self.assertEqual(advice["snapshot_id"], first["snapshot"]["snapshot_id"])
        self.assertEqual(advice["items"][0]["status"], "READY")
        self.assertEqual(set(calls), {"600001", "000300"})
        bridge.perform("sync")
        bridge.risk_thread.join(3)
        self.assertEqual(len(calls), 2)
        client.page = ValueError("secret")
        with self.assertRaises(ValueError):
            bridge.perform("sync")
        self.assertTrue(bridge.status()["risk"]["stale"])
        self.assertTrue(bridge.status()["holding_advice"]["stale"])
        self.assertNotIn("secret", str(bridge.status()))

    def test_clear_or_new_sync_cannot_receive_an_old_workers_result(self):
        """Given 历史行情读取尚未结束 When 清空或同步新持仓 Then 旧分析不得覆盖新快照"""
        for clear in [True, False]:
            with self.subTest(clear=clear):
                started, release = threading.Event(), threading.Event()

                def loader(symbol):
                    started.set()
                    release.wait(3)
                    return histories()[symbol]

                client, bridge = self.make_bridge(loader)
                bridge.perform("history-enable")
                old = bridge.perform("sync")["snapshot"]["snapshot_id"]
                self.assertTrue(started.wait(2))
                if clear:
                    bridge.perform("clear")
                else:
                    client.page = account_page()
                    bridge.perform("sync")
                release.set()
                bridge.risk_thread.join(3)
                state = bridge.status()
                if clear:
                    self.assertIsNone(state["risk"])
                    self.assertIsNone(state["holding_advice"])
                else:
                    self.assertNotEqual(state["risk"]["snapshot_id"], old)
                    self.assertEqual(state["risk"]["status"], "EMPTY")
                    self.assertEqual(state["holding_advice"]["snapshot_id"], state["snapshot"]["snapshot_id"])
                    self.assertEqual(state["holding_advice"]["items"], [])

    def test_history_failure_does_not_turn_successful_sync_into_account_failure(self):
        """Given 行情服务失败 When 账户同步成功 Then 资金持仓保留，历史风险独立显示不足"""
        def loader(symbol):
            raise RuntimeError("secret provider error")

        _, bridge = self.make_bridge(loader)
        bridge.perform("history-enable")
        bridge.perform("sync")
        bridge.risk_thread.join(3)
        state = bridge.status()
        self.assertIsNone(state["last_error"])
        self.assertFalse(state["stale"])
        self.assertEqual(state["risk"]["status"], "PARTIAL")
        self.assertNotIn("secret", str(state))

    def test_default_sync_only_analyzes_local_structure_until_enabled(self):
        """Given 未授权外部行情 When 自动同步 Then 本地结构分析不向提供方发送持仓代码"""
        calls = []

        def loader(symbol):
            calls.append(symbol)
            return histories()[symbol]

        _, bridge = self.make_bridge(loader)
        state = bridge.perform("sync")
        self.assertFalse(state["public_history_enabled"])
        self.assertEqual(state["risk"]["status"], "LOCAL_ONLY")
        self.assertGreater(state["risk"]["stock_exposure"], 0)
        self.assertIsNone(bridge.risk_thread)
        self.assertEqual(calls, [])
        bridge.perform("history-enable")
        bridge.risk_thread.join(3)
        self.assertEqual(bridge.status()["risk"]["status"], "READY")
        self.assertEqual(set(calls), {"000300", "600001"})
        bridge.perform("clear")
        self.assertFalse(bridge.status()["public_history_enabled"])

    def test_disabling_history_blocks_late_worker_and_further_requests(self):
        """Given 公开行情读取进行中 When 撤销授权 Then 不再请求持仓行情或回写旧历史指标"""
        started, release = threading.Event(), threading.Event()
        calls = []

        def loader(symbol):
            calls.append(symbol)
            started.set()
            release.wait(3)
            return histories()[symbol]

        _, bridge = self.make_bridge(loader)
        bridge.perform("history-enable")
        bridge.perform("sync")
        self.assertTrue(started.wait(2))
        bridge.perform("history-disable")
        release.set()
        bridge.risk_thread.join(3)
        state = bridge.status()
        self.assertEqual(calls, ["000300"])
        self.assertFalse(state["public_history_enabled"])
        self.assertEqual(state["risk"]["status"], "LOCAL_ONLY")
        self.assertIsNone(state["risk"]["annualized_volatility"])
        self.assertEqual(state["holding_advice"]["items"][0]["status"], "LOCAL_ONLY")
        self.assertIsNone(state["holding_advice"]["items"][0]["as_of_date"])


if __name__ == "__main__":
    unittest.main()
