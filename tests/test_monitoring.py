from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from statistics import mean, stdev
from unittest.mock import patch

from api.paper import build_paper_response
from research.monitoring import build_monitoring
from research.paper import (
    advance_session, advance_from_public, create_session, list_sessions, load_session,
    pause_session, resume_session, session_view,
)
from research.session_store import locked
from test_forward import observed_at, real_fixture
from test_research import SIMULATION, STRATEGY


class ForwardMonitoringScenarios(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = real_fixture(90)
        self.state = create_session(self.dataset, STRATEGY, SIMULATION, self.root, now=observed_at(self.dataset))
        self.id = self.state["session_id"]

    def advance(self, count):
        dataset = real_fixture(count)
        return advance_session(self.id, dataset, self.root, now=observed_at(dataset))

    def test_benchmark_uses_actual_aligned_closes_and_not_flat_cash(self):
        """Given 已登记策略 When 下一日观察 Then 基准从登记日收盘开始且超额收益独立计算"""
        state = self.advance(91)
        report = build_monitoring(state)
        benchmark = real_fixture(91).bars["000300"]
        expected = benchmark[-1].close / benchmark[-2].close - 1
        self.assertAlmostEqual(report["benchmark"]["total_return"], expected)
        self.assertEqual(report["benchmark"]["symbol"], "000300")
        self.assertEqual(report["comparison_curve"][0]["benchmark"], 1)
        self.assertAlmostEqual(report["comparison_curve"][-1]["benchmark"], 1 + expected)
        self.assertAlmostEqual(report["relative"]["excess_return"],
                               state["result"]["summary"]["total_return"] - expected)
        self.assertEqual(len(report["comparison_curve"]), 2)

    def test_small_samples_and_zero_active_variance_are_unknown_not_infinite(self):
        """Given 少量观察或零主动收益方差 When 体检 Then 不伪造信息比率或置信区间"""
        report = build_monitoring(self.state)
        self.assertIsNone(report["relative"]["information_ratio"])
        self.assertIsNone(report["relative"]["active_return_ci95"]["low"])
        self.assertEqual(report["evidence"]["status"], "INSUFFICIENT")
        self.assertIsNone(report["execution"]["fill_rate"])
        state = self.advance(115)
        with patch("research.monitoring.stdev", return_value=0):
            report = build_monitoring(state)
        self.assertIsNone(report["relative"]["information_ratio"])
        self.assertTrue(report["evidence"]["checks"])
        self.assertFalse(report["live_trading_enabled"])

    def test_positions_and_execution_diagnostics_match_the_ledger(self):
        """Given 已有模拟成交 When 体检 Then 真实持仓权重和成交费用都来自账本"""
        state = self.advance(92)
        report = build_monitoring(state)
        last = state["result"]["equity_curve"][-1]
        self.assertAlmostEqual(report["portfolio"]["gross_exposure"], last["market_value"] / last["equity"])
        self.assertEqual({row["symbol"]: row["quantity"] for row in report["portfolio"]["positions"]}, last["positions"])
        self.assertAlmostEqual(sum(row["market_value"] for row in report["portfolio"]["positions"]), last["market_value"])
        self.assertAlmostEqual(report["execution"]["fees"], state["result"]["summary"]["fees"])
        self.assertEqual(report["execution"]["orders"], len(state["result"]["orders"]))
        self.assertEqual(report["execution"]["modeled_opening_shortfall"], 0)

    def test_active_risk_and_information_ratio_use_paired_daily_returns(self):
        """Given 足够日收益样本 When 计算主动风险 Then 按同日收益差及252日口径核算"""
        state = self.advance(115)
        report = build_monitoring(state)
        prices = {row["trade_date"]: row["close"] for row in state["dataset"]["bars"]["000300"]}
        previous = prices[state["start_after"]]
        active = []
        for point in state["result"]["equity_curve"]:
            active.append(point["return"] - (prices[point["date"]] / previous - 1))
            previous = prices[point["date"]]
        expected_risk = stdev(active) * math.sqrt(252)
        self.assertAlmostEqual(report["relative"]["tracking_error"], expected_risk)
        self.assertAlmostEqual(report["relative"]["information_ratio"], mean(active) * 252 / expected_risk)

    def test_modeled_opening_shortfall_is_separate_from_fees(self):
        """Given 有滑点和佣金的成交 When 执行体检 Then 开盘价差与费用分列且不重复相加"""
        config = replace(SIMULATION, slippage_rate=.001, min_commission=5)
        state = create_session(self.dataset, STRATEGY, config, self.root, now=observed_at(self.dataset))
        incoming = real_fixture(91)
        state = advance_session(state["session_id"], incoming, self.root, now=observed_at(incoming))
        report = build_monitoring(state)
        expected = sum((order["price"] - incoming.bars[order["symbol"]][-1].open) * order["filled_quantity"]
                       for order in state["result"]["orders"])
        self.assertGreater(expected, 0)
        self.assertAlmostEqual(report["execution"]["modeled_opening_shortfall"], round(expected, 2))
        self.assertEqual(report["execution"]["fees"], state["result"]["summary"]["fees"])

    def test_rejected_orders_have_reasons_and_no_fake_fees(self):
        """Given 当日零成交量 When 体检 Then 拒单率和拒单原因可见且无虚构成交成本"""
        new = real_fixture(91)
        for symbol in ("600001", "600002"):
            new.bars[symbol][-1] = replace(new.bars[symbol][-1], volume=0, amount=0)
        state = advance_session(self.id, new, self.root, now=observed_at(new))
        report = build_monitoring(state)
        self.assertEqual(report["execution"]["fill_rate"], 0)
        self.assertEqual(report["execution"]["fees"], 0)
        self.assertEqual(report["execution"]["reasons"], [{"reason": "SUSPENDED", "count": 2}])

    def test_future_benchmark_rows_do_not_change_the_old_monitoring_report(self):
        """Given 冻结观察截止日 When 输入附加未来行情 Then 旧期体检不读取未来价格"""
        state = self.advance(91)
        before = build_monitoring(state)
        extended = copy.deepcopy(state)
        extended["dataset"] = real_fixture(95).to_dict()
        self.assertEqual(build_monitoring(extended), before)

    def test_resume_never_converts_a_gap_to_clean_forward_evidence(self):
        """Given 暂停后继续 When 体检 Then 证据缺口永久保留且不生成实盘准入结论"""
        self.advance(91)
        pause_session(self.id, self.root)
        state = resume_session(self.id, self.root)
        report = build_monitoring(state)
        checks = {row["id"]: row for row in report["evidence"]["checks"]}
        self.assertFalse(checks["no_replay_gap"]["passed"])
        self.assertFalse(report["live_trading_enabled"])
        self.assertIn("monitoring", session_view(state, root=self.root))

    def test_monitoring_is_read_only_and_does_not_create_a_revision(self):
        """Given 已保存的账本 When 多次刷新体检 Then 财务结果和版本号不变"""
        state = self.advance(91)
        original = copy.deepcopy(state)
        build_monitoring(state)
        build_monitoring(state)
        self.assertEqual(state, original)
        self.assertEqual(load_session(self.id, self.root), original)


class RiskPriorityScenarios(unittest.TestCase):
    def test_new_stale_data_halt_can_reduce_a_precommitted_position(self):
        """Given 昨日有买入目标 When 执行日持仓行情超期 Then 允许风控清仓并留痕而不是卡死"""
        with tempfile.TemporaryDirectory() as root:
            original = real_fixture(90)
            state = create_session(original, STRATEGY, replace(SIMULATION, max_stale_days=0),
                                   root, now=observed_at(original))
            first = real_fixture(91)
            state = advance_session(state["session_id"], first, root, now=observed_at(first))
            self.assertTrue(state["next_intent"]["weights"])
            second = real_fixture(92)
            second.bars["600001"].pop()
            state = advance_session(state["session_id"], second, root, now=observed_at(second))
            self.assertEqual(state["status"], "HALTED")
            self.assertEqual(state["observations"][-1]["intent_resolution"], "RISK_REDUCTION")
            self.assertTrue(state["observations"][-1]["risk_overrides"])
            orders = [row for row in state["result"]["orders"] if row["trade_date"] == state["last_date"]]
            self.assertTrue(orders)
            self.assertTrue(all(row["side"] == "SELL" for row in orders))
            self.assertTrue(any(row["reason"] == "MISSING_BAR" for row in orders))
            monitoring = build_monitoring(state)
            self.assertIn("STALE_VALUATION", [row["code"] for row in monitoring["alerts"]])
            self.assertEqual(monitoring["portfolio"]["positions"][0]["stale"], True)

    def test_risk_override_never_authorizes_unregistered_buying(self):
        """Given 预登记信号 When 计算结果擅自改为其他买入仓位 Then 仍拒绝推进"""
        from research.engine import simulate_portfolio
        with tempfile.TemporaryDirectory() as root:
            original, new = real_fixture(90), real_fixture(91)
            state = create_session(original, STRATEGY, SIMULATION, root, now=observed_at(original))
            result = simulate_portfolio(new, STRATEGY, SIMULATION, start=new.bars["000300"][-1].trade_date)
            result["decisions"][0]["weights"] = {"600001": 0.8}
            result["decisions"][0]["halted"] = True
            with patch("research.paper.simulate_portfolio", return_value=result), self.assertRaisesRegex(ValueError, "信号"):
                advance_session(state["session_id"], new, root, now=observed_at(new))
            self.assertEqual(load_session(state["session_id"], root)["result"], state["result"])


class ConcurrentControlScenarios(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        dataset = real_fixture(90)
        self.state = create_session(dataset, STRATEGY, SIMULATION, self.root, now=observed_at(dataset))
        self.id = self.state["session_id"]
        self.path = self.root / self.id

    def test_read_during_commit_is_busy_and_never_reported_as_corrupt(self):
        """Given 写进程持锁 When 页面读取 Then 返回更新中而不是诱导用户恢复账本"""
        with locked(self.path):
            with self.assertRaises(RuntimeError):
                load_session(self.id, self.root)
            listed = list_sessions(self.root)[0]
        self.assertTrue(listed["busy"])
        self.assertNotIn("error", listed)

    def test_stale_control_version_fails_without_changing_the_ledger(self):
        """Given 页面看到旧版本 When 后台已推进且用户暂停 Then 返回冲突并保持新账本"""
        new = real_fixture(91)
        latest = advance_session(self.id, new, self.root, now=observed_at(new))
        with self.assertRaisesRegex(RuntimeError, "版本"):
            pause_session(self.id, self.root, expected_revision=self.state["storage_revision"])
        self.assertEqual(load_session(self.id, self.root), latest)
        paused = pause_session(self.id, self.root, expected_revision=latest["storage_revision"])
        self.assertEqual(paused["status"], "PAUSED")

    def test_stale_public_advance_never_fetches_external_data(self):
        """Given 过期页面版本 When 请求公开数据推进 Then 在访问网络前拒绝冲突"""
        pause_session(self.id, self.root)
        with patch("research.paper.capture_dataset") as capture, self.assertRaises(RuntimeError):
            advance_from_public(self.id, self.root, expected_revision=self.state["storage_revision"])
        capture.assert_not_called()

    def test_api_accepts_only_valid_expected_revision(self):
        """Given 运行控制请求 When 版本为布尔或字符串 Then 拒绝而不隐式转换"""
        with patch.dict("os.environ", {"QUANT_PAPER_DIR": str(self.root)}):
            for revision in (True, "1", -1, 1.5, None):
                with self.subTest(revision=revision), self.assertRaises(ValueError):
                    build_paper_response({"expected_revision": revision}, self.id, "pause")
            with self.assertRaisesRegex(ValueError, "版本"):
                build_paper_response({}, self.id, "pause")
            response = build_paper_response({"expected_revision": self.state["storage_revision"]}, self.id, "pause")
            self.assertEqual(response["session"]["status"], "PAUSED")

    def test_watch_retries_busy_reads_without_calling_the_source(self):
        """Given 人工操作正在写账本 When 后台观察读取 Then 等待下一轮而不把冲突记为损坏"""
        from research.paper import watch_session
        with patch.dict("os.environ", {"QUANT_PAPER_DIR": str(self.root)}), locked(self.path), patch(
            "research.paper.advance_from_public",
        ) as advance:
            events = []
            watch_session(self.id, once=True, emit=events.append)
        self.assertEqual(json.loads(events[0])["status"], "BUSY")
        advance.assert_not_called()


class CaptureWindowScenarios(unittest.TestCase):
    def test_larger_fetch_window_never_backfills_the_frozen_input(self):
        """Given 登记时使用较短历史 When 次日采集窗口变长 Then 忽略窗口前新增历史并正常追加"""
        from research.capture import merge_observation
        original = real_fixture(90)
        original = replace(original, bars={symbol: rows[30:] for symbol, rows in original.bars.items()})
        incoming = real_fixture(91)
        merged = merge_observation(original, incoming)
        for symbol, rows in merged.bars.items():
            self.assertEqual(rows[:-1], original.bars[symbol])
            self.assertEqual(rows[-1], incoming.bars[symbol][-1])
        with tempfile.TemporaryDirectory() as root:
            state = create_session(original, STRATEGY, SIMULATION, root, now=observed_at(original))
            with patch("research.paper.capture_dataset", return_value=incoming), patch(
                "research.paper._now", return_value=observed_at(incoming),
            ):
                advanced = advance_from_public(state["session_id"], root)
            self.assertEqual(advanced["qualified_days"], 1)
            self.assertEqual(len(advanced["dataset"]["bars"]["000300"]), 61)


if __name__ == "__main__":
    unittest.main()
