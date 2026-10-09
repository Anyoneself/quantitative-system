from __future__ import annotations

import copy
import json
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from research.audit import audit_dataset, reconcile_result
from research.capture import merge_observation
from research.dataset import parse_dataset
from research.engine import simulate_portfolio
from research.paper import (
    advance_from_public, advance_session, create_session, load_session, pause_session,
    recover_session, resume_session, session_view, watch_session,
)
from research.session_store import list_revisions
from research.operations import write_heartbeat
from research.experiment import run_experiment
from research.config import ValidationConfig
from cli import main
from test_forward import observed_at, real_fixture
from test_research import SIMULATION, STRATEGY, dataset_fixture


class DataAuditScenarios(unittest.TestCase):
    def test_calendar_is_optional_and_never_inferred_as_verified(self):
        """Given 只有基准日线 When 体检 Then 明确交易日历未经独立核验"""
        dataset = dataset_fixture(90)
        self.assertNotIn("calendar", dataset.to_dict())
        report = audit_dataset(dataset)
        self.assertEqual(report["calendar_status"], "UNVERIFIED")
        self.assertTrue(any(item["id"] == "calendar_source" and not item["passed"] for item in report["checks"]))

    def test_declared_calendar_catches_missing_benchmark_day(self):
        """Given 有来源的日历 When 基准丢失一个交易日 Then 不把缺日静默当休市"""
        original = dataset_fixture(90).to_dict()
        original["calendar"] = {"source": "测试日历声明，不是交易所认证",
                                "sessions": [row["trade_date"] for row in original["bars"]["000300"]]}
        parsed = parse_dataset(original)
        self.assertEqual(parsed.to_dict()["calendar"], original["calendar"])
        for rows in original["bars"].values():
            rows.pop(30)
        report = audit_dataset(parse_dataset(original))
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["missing_benchmark_dates"], ["2023-02-02"])

    def test_price_jump_and_missing_member_history_require_review(self):
        """Given 异常跳价和缺失成员行情 When 体检 Then 给出逐证券定位而不虚构公司行动"""
        dataset = dataset_fixture(90)
        dataset.bars["600001"].pop(40)
        row = dataset.bars["600002"][50]
        dataset.bars["600002"][50] = replace(row, open=row.open / 2, high=row.high / 2,
                                           low=row.low / 2, close=row.close / 2)
        report = audit_dataset(dataset)
        self.assertTrue(report["missing_member_bars"])
        self.assertTrue(report["price_jumps"])
        self.assertFalse(next(item for item in report["checks"] if item["id"] == "corporate_actions")["passed"])

    def test_benchmark_discontinuity_also_blocks_the_data_gate(self):
        """Given 基准价格断裂 When 数据体检 Then 基准也必须接受连续性检查"""
        dataset = dataset_fixture(90)
        row = dataset.bars["000300"][40]
        dataset.bars["000300"][40] = replace(row, open=row.open * 2, close=row.close * 2,
                                            high=row.high * 2, low=row.low * 2)
        report = audit_dataset(dataset)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(any(item["symbol"] == "000300" for item in report["price_jumps"]))

    def test_unknown_dataset_fields_and_invalid_calendars_are_rejected(self):
        """Given 错拼字段或无来源日历 When 导入 Then 不静默丢弃输入"""
        for change in ({"calender": {}}, {"calendar": {"source": "", "sessions": []}},
                       {"calendar": {"source": "测试", "sessions": ["2023-01-04", "2023-01-03"]}}):
            payload = dataset_fixture(90).to_dict()
            payload.update(change)
            with self.assertRaises(ValueError):
                parse_dataset(payload)

    def test_calendar_can_predeclare_future_sessions_but_not_rewrite_observed_dates(self):
        """Given 预先声明的未来日历 When 追加观察 Then 保留来源且禁止改写已观察日历"""
        payload = dataset_fixture(90).to_dict()
        days = [row["trade_date"] for row in payload["bars"]["000300"]]
        payload["calendar"] = {"source": "测试声明", "sessions": [*days, "2027-01-04"]}
        frozen = parse_dataset(payload)
        self.assertEqual(merge_observation(frozen, frozen).calendar, frozen.calendar)
        payload["calendar"]["sessions"].pop(30)
        with self.assertRaisesRegex(ValueError, "日历"):
            merge_observation(frozen, parse_dataset(payload))
        with self.assertRaisesRegex(ValueError, "日历"):
            merge_observation(frozen, dataset_fixture(90))

    def test_research_includes_data_gate_and_independent_reconciliation(self):
        """Given 缺失声明交易日 When 历史研究 Then 保留诊断且验收不通过"""
        payload = dataset_fixture(220).to_dict()
        payload["calendar"] = {"source": "测试", "sessions": [row["trade_date"] for row in payload["bars"]["000300"]]}
        for rows in payload["bars"].values():
            rows.pop(10)
        report = run_experiment(parse_dataset(payload), STRATEGY, SIMULATION,
                                ValidationConfig(train_days=60, test_days=20, bootstrap_samples=100))
        self.assertEqual(report["data_audit"]["status"], "BLOCKED")
        checks = {item["id"]: item for item in report["checks"]}
        self.assertFalse(checks["data_integrity"]["passed"])
        self.assertTrue(checks["ledger_reconciliation"]["passed"])
        self.assertEqual(report["research_status"], "FAILED_RESEARCH_CHECKS")

    def test_cli_audit_prints_diagnostics_and_returns_failure_for_blocking_data(self):
        """Given 阻断数据 When 独立体检 Then 输出定位信息并返回失败退出码"""
        payload = dataset_fixture(90).to_dict()
        payload["calendar"] = {"source": "测试", "sessions": [row["trade_date"] for row in payload["bars"]["000300"]]}
        for rows in payload["bars"].values():
            rows.pop(10)
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()) as stream:
            path = Path(root) / "dataset.json"
            path.write_text(json.dumps(payload))
            self.assertEqual(main(["research-audit", "--dataset", str(path)]), 1)
            self.assertTrue(json.loads(stream.getvalue())["missing_benchmark_dates"])


class ReconciliationScenarios(unittest.TestCase):
    def setUp(self):
        self.dataset = dataset_fixture(90)
        self.config = replace(SIMULATION, commission_rate=.0003, min_commission=5, stamp_duty_rate=.0005)
        self.result = simulate_portfolio(self.dataset, STRATEGY, self.config,
                                         start=self.dataset.bars["000300"][10].trade_date)

    def test_cash_inventory_and_symbol_pnl_reconcile_independently(self):
        """Given 模拟成交 When 独立重建账本 Then 现金持仓净值和逐股盈亏相等"""
        report = reconcile_result(self.dataset, self.result, self.config)
        self.assertEqual(report["status"], "BALANCED")
        self.assertEqual(report["days_checked"], len(self.result["equity_curve"]))
        self.assertAlmostEqual(sum(item["net_pnl"] for item in report["attribution"]),
                               self.result["summary"]["final_equity"] - self.config.initial_cash, places=2)
        self.assertTrue(all(order["order_id"] for order in self.result["orders"]))

    def test_edited_cash_fees_inventory_or_equity_fail_closed(self):
        """Given 现金费用持仓或净值被改写 When 对账 Then 不把不一致结果作为成功模拟"""
        mutations = [
            lambda r: r["orders"][0].update(cash_after=r["orders"][0]["cash_after"] + 100),
            lambda r: r["orders"][0].update(fee=1000),
            lambda r: r["equity_curve"][-1]["positions"].update({"600001": 999999}),
            lambda r: r["equity_curve"][0].update(equity=1),
            lambda r: r["summary"].update(fees=1),
            lambda r: r["terminal_positions"].clear(),
        ]
        for mutate in mutations:
            result = copy.deepcopy(self.result)
            mutate(result)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ValueError, "对账"):
                reconcile_result(self.dataset, result, self.config)

    def test_duplicate_orders_are_not_counted_twice(self):
        """Given 相同委托重复出现 When 重放对账 Then 拒绝而不是重复入账"""
        result = copy.deepcopy(self.result)
        result["orders"].insert(1, copy.deepcopy(result["orders"][0]))
        with self.assertRaisesRegex(ValueError, "对账"):
            reconcile_result(self.dataset, result, self.config)


class RecoveryScenarios(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = real_fixture(90)
        self.now = observed_at(self.dataset)
        self.state = create_session(self.dataset, STRATEGY, SIMULATION, self.root, now=self.now)
        self.id = self.state["session_id"]
        self.path = self.root / self.id

    def test_saved_revisions_form_a_chain_and_can_restore_only_latest(self):
        """Given 已推进后主文件损坏 When 显式恢复 Then 恢复最新完整成交且默认暂停"""
        new = real_fixture(91)
        advanced = advance_session(self.id, new, self.root, now=observed_at(new))
        self.assertEqual(len(list_revisions(self.path)), 2)
        (self.path / "state.json").write_text("broken")
        with self.assertRaises(ValueError):
            load_session(self.id, self.root)
        recovered = recover_session(self.id, self.root, now=observed_at(new))
        self.assertEqual(recovered["status"], "PAUSED")
        self.assertEqual(recovered["result"], advanced["result"])
        self.assertEqual(recovered["last_date"], advanced["last_date"])
        self.assertEqual(list_revisions(self.path)[-1]["event"], "RECOVER")

    def test_corrupt_latest_revision_cannot_fall_back_to_older_profits(self):
        """Given 最新存档也损坏 When 恢复 Then 拒绝回退到较早账本"""
        pause_session(self.id, self.root, now=self.now)
        latest = sorted((self.path / "revisions").glob("*.json"))[-1]
        latest.write_text("broken")
        with self.assertRaisesRegex(ValueError, "校验"):
            recover_session(self.id, self.root, now=self.now)

    def test_missing_latest_archive_and_primary_never_restore_an_older_version(self):
        """Given 最新存档与主文件丢失但提交索引存在 When 恢复 Then 拒绝回退"""
        pause_session(self.id, self.root, now=self.now)
        sorted((self.path / "revisions").glob("*.json"))[-1].unlink()
        (self.path / "state.json").unlink()
        with self.assertRaisesRegex(ValueError, "校验"):
            recover_session(self.id, self.root, now=self.now)

    def test_interrupted_cache_publish_requires_explicit_recovery(self):
        """Given 存档已完成但主文件发布中断 When 再次读取 Then 阻断并可恢复最近版本"""
        original_replace = Path.replace

        def interrupt(path, target):
            if Path(target).name == "state.json":
                raise OSError("模拟发布中断")
            return original_replace(path, target)

        with patch.object(Path, "replace", interrupt):
            with self.assertRaises(OSError):
                pause_session(self.id, self.root, now=self.now)
        with self.assertRaisesRegex(ValueError, "恢复"):
            load_session(self.id, self.root)
        recovered = recover_session(self.id, self.root, now=self.now)
        self.assertEqual(recovered["status"], "PAUSED")

    def test_failed_recovery_publish_never_restores_an_active_primary(self):
        """Given 主账本损坏 When 恢复发布也中断 Then 不短暂恢复为可交易状态"""
        (self.path / "state.json").write_text("broken")
        original_replace = Path.replace

        def interrupt(path, target):
            if Path(target).name == "state.json":
                raise OSError("恢复发布中断")
            return original_replace(path, target)

        with patch.object(Path, "replace", interrupt), self.assertRaises(OSError):
            recover_session(self.id, self.root, now=self.now)
        with self.assertRaises(ValueError):
            load_session(self.id, self.root)
        latest = json.loads(sorted((self.path / "revisions").glob("*.json"))[-1].read_text())
        self.assertEqual(latest["state"]["status"], "PAUSED")
        self.assertEqual(recover_session(self.id, self.root, now=self.now)["status"], "PAUSED")

    def test_corrupt_primary_envelope_metadata_can_be_recovered(self):
        """Given 主文件外层版本字段损坏 When 显式恢复 Then 使用完整存档而非未处理异常"""
        primary = self.path / "state.json"
        envelope = json.loads(primary.read_text())
        envelope["revision"] = "broken"
        primary.write_text(json.dumps(envelope))
        with self.assertRaises(ValueError):
            load_session(self.id, self.root)
        self.assertEqual(recover_session(self.id, self.root, now=self.now)["status"], "PAUSED")

    def test_pause_resume_is_audited_and_does_not_erase_drawdown_halt(self):
        """Given 永久熔断后暂停 When 用户继续 Then 不得恢复买入资格"""
        from research.paper import _save
        state = load_session(self.id, self.root)
        state["result"]["summary"]["halted"] = True
        state["next_intent"]["risk_off"] = True
        state["next_intent"]["weights"] = {}
        state["status"] = "HALTED"
        _save(self.path, state, event="HALT")
        pause_session(self.id, self.root, now=self.now)
        resumed = resume_session(self.id, self.root, now=self.now)
        self.assertEqual(resumed["status"], "HALTED")
        self.assertTrue(resumed["has_replay_gap"])
        self.assertEqual(resumed["next_intent"]["weights"], {})
        self.assertEqual(list_revisions(self.path)[-1]["event"], "RESUME")

    def test_duplicate_advance_creates_no_revision(self):
        """Given 当日已入账 When 重复推进 Then 不增加订单或存档版本"""
        new = real_fixture(91)
        advance_session(self.id, new, self.root, now=observed_at(new))
        before = list_revisions(self.path)
        advance_session(self.id, new, self.root, now=observed_at(new))
        self.assertEqual(before, list_revisions(self.path))

    def test_watch_heartbeat_distinguishes_stopped_from_running(self):
        """Given 观察进程已退出 When 查看运行状态 Then 不把旧心跳显示为在线"""
        with patch.dict("os.environ", {"QUANT_PAPER_DIR": str(self.root)}), patch(
            "research.paper._now", return_value=self.now.replace(hour=10),
        ):
            watch_session(self.id, once=True, emit=lambda message: None)
        state = load_session(self.id, self.root)
        view = session_view(state, root=self.root, now=self.now)
        self.assertEqual(view["operations"]["worker"]["status"], "STOPPED")
        self.assertFalse(view["operations"]["worker"]["online"])
        self.assertEqual(len(list_revisions(self.path)), 1)

    def test_only_one_watch_worker_can_own_a_session(self):
        """Given 已有后台观察进程 When 启动第二个 Then 不覆盖原心跳或重复调度"""
        from research.session_store import locked
        with patch.dict("os.environ", {"QUANT_PAPER_DIR": str(self.root)}), locked(self.path, ".watch.lock"):
            with self.assertRaises(RuntimeError):
                watch_session(self.id, once=True, emit=lambda message: None)
        self.assertFalse((self.path / "runtime.json").exists())

    def test_expired_or_invalid_heartbeat_never_claims_worker_is_online(self):
        """Given 心跳过期或损坏 When 查询状态 Then 显示离线并保持账本不变"""
        write_heartbeat(self.path, "test-worker", 60, "WAITING", self.now - timedelta(hours=1))
        view = session_view(self.state, root=self.root, now=self.now)
        self.assertEqual(view["operations"]["worker"]["status"], "HEARTBEAT_EXPIRED")
        self.assertFalse(view["operations"]["worker"]["online"])
        (self.path / "runtime.json").write_text('{"status":"WAITING"}')
        view = session_view(self.state, root=self.root, now=self.now)
        self.assertEqual(view["operations"]["worker"]["status"], "INVALID_HEARTBEAT")
        self.assertEqual(len(list_revisions(self.path)), 1)

    def test_expired_calendar_is_an_alert_not_an_assumed_holiday(self):
        """Given 日历声明已用尽 When 后台观察 Then 不将未知交易日显示为正常休市"""
        payload = self.dataset.to_dict()
        payload["calendar"] = {"source": "测试", "sessions": [row["trade_date"] for row in payload["bars"]["000300"]]}
        state = create_session(parse_dataset(payload), STRATEGY, SIMULATION, self.root, now=self.now)
        later = self.now + timedelta(days=7)
        with patch.dict("os.environ", {"QUANT_PAPER_DIR": str(self.root)}), patch(
            "research.paper._now", return_value=later,
        ), patch("research.paper.advance_from_public") as advance:
            events = []
            watch_session(state["session_id"], once=True, emit=events.append)
        advance.assert_not_called()
        self.assertEqual(json.loads(events[0])["status"], "CALENDAR_EXPIRED")
        view = session_view(state, root=self.root, now=later)
        self.assertIn("CALENDAR_EXPIRED", [item["code"] for item in view["operations"]["alerts"]])

    def test_legacy_state_migrates_with_original_snapshot_preserved(self):
        """Given 无版本历史的旧格式 When 暂停 Then 先归档旧状态再记录本次操作"""
        from research.dataset import canonical_json
        from hashlib import sha256
        legacy_id = "b" * 32
        path = self.root / legacy_id
        path.mkdir()
        state = copy.deepcopy(self.state)
        state.pop("storage_revision")
        state["session_id"] = legacy_id
        (path / "state.json").write_text(json.dumps({
            "state": state, "sha256": sha256(canonical_json(state).encode()).hexdigest(),
        }))
        pause_session(legacy_id, self.root, now=self.now)
        self.assertEqual([row["event"] for row in list_revisions(path)], ["LEGACY_IMPORT", "PAUSE"])

    def test_failed_manual_observation_is_recorded_for_diagnostics(self):
        """Given 不合法的今日行情 When 手动推进 Then 账本不变且错误进入运行记录"""
        new = real_fixture(91)
        with self.assertRaises(ValueError):
            advance_session(self.id, new, self.root, now=observed_at(new).replace(hour=14))
        after = load_session(self.id, self.root)
        self.assertEqual(after["result"], self.state["result"])
        self.assertIsNotNone(after["last_error"])
        self.assertEqual(list_revisions(self.path)[-1]["event"], "OBSERVATION_FAILED")


if __name__ == "__main__":
    unittest.main()
