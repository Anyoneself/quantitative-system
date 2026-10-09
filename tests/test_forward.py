from __future__ import annotations

import copy
import json
import tempfile
import unittest
import io
import http.client
import threading
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from research.capture import capture_dataset, capture_historical_dataset, merge_observation
from research.config import ValidationConfig
from research.dataset import parse_dataset
from research.experiment import run_experiment, save_experiment
from research.paper import advance_session, create_session, load_session, pause_session
from research.paper import _locked, advance_from_public, watch_session
from api.paper import build_paper_response
from data.errors import DataRefreshError
from cli import main
from test_research import SIMULATION, STRATEGY, dataset_fixture


def observed_at(dataset, hour=16):
    day = dataset.bars[dataset.benchmark][-1].trade_date
    return datetime(day.year, day.month, day.day, hour, tzinfo=ZoneInfo("Asia/Shanghai"))


def real_fixture(count=90):
    return replace(dataset_fixture(count), synthetic=False, source="测试替身，不是真实行情")


class CaptureScenarios(unittest.TestCase):
    def test_current_universe_is_never_backdated(self):
        """Given 今天选出的证券池 When 采集历史行情 Then 证券池仅从今天生效"""
        dataset = real_fixture()
        with patch("research.capture.fetch_history", side_effect=lambda symbol, days: dataset.bars[symbol]):
            result = capture_dataset(["600001", "600002"], "000300", now=observed_at(dataset))
        self.assertEqual(result.universe[0].known_at, observed_at(dataset).date())
        self.assertEqual(result.members_on(dataset.bars["000300"][0].trade_date), ())
        self.assertFalse(result.synthetic)
        self.assertIn("估算", result.source)

    def test_revision_and_missing_overlap_are_rejected(self):
        """Given 冻结的历史 When 提供方改写旧价或完全无重叠 Then 停止更新"""
        old, new = real_fixture(90), real_fixture(91)
        new.bars["600001"][20] = replace(new.bars["600001"][20], volume=12)
        with self.assertRaisesRegex(ValueError, "历史"):
            merge_observation(old, new)
        new = real_fixture(95)
        new = replace(new, bars={symbol: rows[-2:] for symbol, rows in new.bars.items()})
        with self.assertRaisesRegex(ValueError, "重叠"):
            merge_observation(old, new)

    def test_append_keeps_frozen_universe_and_provenance(self):
        """Given 已登记的证券池 When 追加行情 Then 不用新股票池改写登记记录"""
        old, new = real_fixture(90), real_fixture(91)
        result = merge_observation(old, new)
        self.assertEqual(result.universe, old.universe)
        self.assertEqual(result.source, old.source)
        self.assertEqual(len(result.bars["000300"]), 91)

    def test_deleted_history_and_previously_missing_bar_cannot_be_backfilled(self):
        """Given 已观察历史 When 删除旧日期或回填缺失旧日期 Then 拒绝改写"""
        old, new = real_fixture(90), real_fixture(91)
        new.bars["600001"].pop(30)
        with self.assertRaisesRegex(ValueError, "历史"):
            merge_observation(old, new)
        old, new = real_fixture(90), real_fixture(91)
        old.bars["600001"].pop(30)
        with self.assertRaisesRegex(ValueError, "历史"):
            merge_observation(old, new)

    def test_historical_membership_requires_explicit_source_and_dates(self):
        """Given 有来源的历史证券池 When 采集 Then 仅使用用户明确提供的历史成员"""
        old = real_fixture(90)
        manifest = {"source": "测试提供方的历史成员记录", "universe": old.to_dict()["universe"]}
        with patch("research.capture.fetch_history", side_effect=lambda symbol, days: old.bars[symbol]):
            captured = capture_historical_dataset(manifest)
        self.assertEqual(captured.universe, old.universe)
        self.assertIn("未经独立核验", captured.source)
        with patch("research.capture.fetch_history") as fetch:
            with self.assertRaises(ValueError):
                capture_historical_dataset({"source": "", "universe": manifest["universe"]})
            fetch.assert_not_called()


class FinalHoldoutScenarios(unittest.TestCase):
    def test_final_holdout_cannot_select_parameters_and_ledger_continues(self):
        """Given 留出期之前冻结参数 When 改变留出期价格 Then 训练排名及开发段不变"""
        dataset = dataset_fixture(220)
        validation = ValidationConfig(train_days=60, test_days=20, bootstrap_samples=100, holdout_days=40)
        report = run_experiment(dataset, STRATEGY, SIMULATION, validation)
        changed = copy.deepcopy(dataset)
        boundary = report["holdout"]["start"]
        for symbol, rows in changed.bars.items():
            changed.bars[symbol] = [
                replace(bar, open=bar.open * .9, close=bar.close * .9,
                        high=bar.high * .9, low=bar.low * .9)
                if bar.trade_date.isoformat() >= boundary else bar for bar in rows
            ]
        revised = run_experiment(changed, STRATEGY, SIMULATION, validation)
        self.assertEqual(report["folds"], revised["folds"])
        self.assertEqual(report["out_of_sample"], revised["out_of_sample"])
        self.assertEqual(report["holdout"]["frozen_strategy"], revised["holdout"]["frozen_strategy"])
        self.assertEqual(report["holdout"]["summary"]["days"], 40)
        self.assertAlmostEqual(
            report["holdout"]["equity_curve"][0]["return"],
            report["holdout"]["equity_curve"][0]["equity"] / report["out_of_sample"]["equity_curve"][-1]["equity"] - 1,
        )
        self.assertFalse(report["live_trading_enabled"])

    def test_adjusted_prices_are_not_used_for_cash_fills(self):
        """Given 前复权行情 When 请求现金股数回测 Then 拒绝把复权价作为成交价"""
        with self.assertRaisesRegex(ValueError, "未复权"):
            run_experiment(replace(dataset_fixture(), adjustment="qfq"), STRATEGY, SIMULATION,
                           ValidationConfig(train_days=60, test_days=20))

    def test_repeated_snapshot_reports_disclose_consumed_holdout(self):
        """Given 同一快照已有实验 When 再次实验 Then 提示留出期不再独立"""
        dataset = dataset_fixture(220)
        report = run_experiment(dataset, STRATEGY, SIMULATION,
                                ValidationConfig(train_days=60, test_days=20, bootstrap_samples=100))
        with tempfile.TemporaryDirectory() as root:
            first = save_experiment(report, dataset, root)
            second = save_experiment(report, dataset, root)
            self.assertEqual(json.loads((first / "report.json").read_text())["prior_reports_for_same_data"], 0)
            repeated = json.loads((second / "report.json").read_text())
            self.assertEqual(repeated["prior_reports_for_same_data"], 1)
            self.assertTrue(any("再次查看" in item for item in repeated["limitations"]))


class PaperScenarios(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = real_fixture(90)
        self.clock = observed_at(self.dataset)
        self.session = create_session(self.dataset, STRATEGY, SIMULATION, self.root, now=self.clock)

    def step(self, count=91):
        data = real_fixture(count)
        return advance_session(self.session["session_id"], data, self.root, now=observed_at(data))

    def test_session_is_frozen_before_future_data_and_step_is_idempotent(self):
        """Given 收盘后登记的参数 When 下一日推进两次 Then 订单仅入账一次且决策已预先冻结"""
        self.assertEqual(self.session["result"]["orders"], [])
        intent = self.session["next_intent"]
        self.assertEqual(intent["signal_date"], self.clock.date().isoformat())
        result = self.step()
        repeated = self.step()
        self.assertEqual(result, repeated)
        self.assertEqual(result["result"]["decisions"][0]["weights"], intent["weights"])
        self.assertEqual(result["qualified_days"], 1)
        self.assertFalse(result["live_trading_enabled"])
        self.assertEqual(result["release_status"], "BLOCKED_FORWARD_EVIDENCE")
        self.assertEqual(result["result"]["summary"]["days"], 1)

    def test_historical_catchup_does_not_count_as_forward_evidence(self):
        """Given 漏跑多日 When 补算 Then 保留模拟结果但永久标记为补算证据"""
        state = self.step(94)
        self.assertEqual(state["qualified_days"], 0)
        self.assertTrue(state["has_replay_gap"])
        self.assertEqual(state["observations"][-1]["mode"], "CATCH_UP")

    def test_old_observation_or_intraday_is_rejected_without_mutation(self):
        """Given 盘中或陈旧快照 When 推进 Then 不改变账本"""
        original = load_session(self.session["session_id"], self.root)
        new = real_fixture(91)
        for now in (observed_at(new, 14), observed_at(new) + timedelta(days=2)):
            with self.assertRaises(ValueError):
                advance_session(self.session["session_id"], new, self.root, now=now)
        after = load_session(self.session["session_id"], self.root)
        for key in ("result", "last_date", "observations", "dataset", "next_intent", "qualified_days"):
            self.assertEqual(after[key], original[key])
        self.assertIsNotNone(after["last_error"])

    def test_history_revision_or_code_change_blocks_advance(self):
        """Given 已运行账本 When 历史数据或代码变化 Then 不重算伪造原有业绩"""
        new = real_fixture(91)
        new.bars["600001"][0] = replace(new.bars["600001"][0], amount=123)
        with self.assertRaisesRegex(ValueError, "历史"):
            advance_session(self.session["session_id"], new, self.root, now=observed_at(new))
        with patch("research.paper._code_hash", return_value="modified"):
            with self.assertRaisesRegex(ValueError, "代码"):
                self.step()
            with self.assertRaisesRegex(ValueError, "重启"):
                create_session(self.dataset, STRATEGY, SIMULATION, self.root, now=self.clock)

    def test_pause_and_corruption_fail_closed(self):
        """Given 人工暂停或损坏账本 When 推进 Then 禁止产生模拟新订单"""
        paused = pause_session(self.session["session_id"], self.root, now=self.clock)
        self.assertEqual(paused["status"], "PAUSED")
        with self.assertRaisesRegex(ValueError, "暂停"):
            self.step()
        path = self.root / self.session["session_id"] / "state.json"
        envelope = json.loads(path.read_text())
        envelope["state"]["config"]["simulation"]["initial_cash"] = 1
        path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(ValueError, "校验"):
            load_session(self.session["session_id"], self.root)

    def test_synthetic_or_adjusted_session_is_rejected(self):
        """Given 合成或复权样本 When 创建真实前向模拟 Then 明确拒绝"""
        for dataset in (replace(self.dataset, synthetic=True), replace(self.dataset, adjustment="qfq")):
            with self.assertRaises(ValueError):
                create_session(dataset, STRATEGY, SIMULATION, self.root, now=self.clock)

    def test_future_universe_and_foreign_symbols_cannot_enter_session(self):
        """Given 冻结证券池 When 换入其他证券或基准 Then 拒绝推进"""
        new = real_fixture(91).to_dict()
        new["bars"]["600003"] = new["bars"].pop("600002")
        new["universe"][0]["symbols"] = ["600001", "600003"]
        with self.assertRaisesRegex(ValueError, "证券"):
            advance_session(self.session["session_id"], parse_dataset(new), self.root, now=observed_at(real_fixture(91)))

    def test_file_lock_prevents_duplicate_writers(self):
        """Given 同一模拟已被其他进程锁定 When 同时推进 Then 不允许重复入账"""
        with _locked(self.root / self.session["session_id"]):
            with self.assertRaises(RuntimeError):
                self.step()

    def test_source_failure_is_recorded_but_does_not_reset_ledger(self):
        """Given 行情接口失败 When 自动推进 Then 保留账本并记录错误"""
        self.step()
        before = load_session(self.session["session_id"], self.root)
        with patch("research.paper.capture_dataset", side_effect=DataRefreshError("行情不可用")):
            with self.assertRaises(DataRefreshError):
                advance_from_public(self.session["session_id"], self.root)
        after = load_session(self.session["session_id"], self.root)
        self.assertEqual(before["result"], after["result"])
        self.assertEqual(after["last_error"], "行情不可用")

    def test_sparse_rebalance_and_drawdown_halt_preserve_intents(self):
        """Given 多日调仓周期 When 每日推进且出现大跌 Then 保持原信号并持续风控退出"""
        self.session = create_session(self.dataset, replace(STRATEGY, rebalance_days=5),
                                      replace(SIMULATION, max_drawdown=0.01), self.root, now=self.clock)
        first = self.step()
        second = self.step(92)
        self.assertEqual(first["result"]["orders"], second["result"]["orders"])
        new = real_fixture(93)
        for symbol in ("600001", "600002"):
            last = new.bars[symbol][-1]
            new.bars[symbol][-1] = replace(last, open=last.open * .92, close=last.close * .92,
                                         high=last.high * .92, low=last.low * .92)
        halted = advance_session(self.session["session_id"], new, self.root, now=observed_at(new))
        self.assertEqual(halted["status"], "HALTED")
        self.assertEqual(halted["next_intent"]["weights"], {})
        self.assertTrue(halted["next_intent"]["risk_off"])

    def test_cli_status_and_api_input_boundaries(self):
        """Given 已冻结模拟 When 从 CLI 读取或用 API 注入参数 Then 可读状态且拒绝注入"""
        with patch.dict("os.environ", {"QUANT_PAPER_DIR": str(self.root)}):
            with redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(main(["paper", "status", self.session["session_id"]]), 0)
            payload = json.loads(stream.getvalue())
            self.assertNotIn("dataset", payload)
            self.assertFalse(payload["live_trading_enabled"])
            for data, session_id, action in (
                ({"dataset": self.dataset.to_dict(), "symbols": ["600001"]}, None, None),
                ({"config": {"strategy": {"lookback": 99}}}, self.session["session_id"], "advance"),
                ({}, self.session["session_id"], "buy"),
            ):
                with self.assertRaises(ValueError):
                    build_paper_response(data, session_id, action)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["paper", "status", "../escape"]), 1)

    def test_watch_waits_before_close_and_stops_after_pause(self):
        """Given 盘中或用户停止 When 自动观察 Then 不请求行情或产生订单"""
        with patch.dict("os.environ", {"QUANT_PAPER_DIR": str(self.root)}), patch(
            "research.paper._now", return_value=self.clock.replace(hour=10),
        ), patch("research.paper.advance_from_public") as advance:
            events = []
            watch_session(self.session["session_id"], once=True, emit=events.append)
            self.assertEqual(json.loads(events[0])["status"], "WAITING")
            advance.assert_not_called()
            pause_session(self.session["session_id"])
            watch_session(self.session["session_id"], once=True, emit=events.append)
            self.assertEqual(json.loads(events[-1])["status"], "PAUSED")


class PaperHttpScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web import _create_server
        cls.server = _create_server("127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, method, route, payload=None, **headers):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, route, body=None if payload is None else json.dumps(payload), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_cross_origin_or_missing_token_never_creates_session(self):
        """Given 非同源或缺失操作令牌 When 创建模拟 Then 返回403且不采集行情"""
        with patch("api.paper.capture_dataset") as capture:
            for headers in (
                {"Content-Type": "application/json"},
                {"Content-Type": "application/json", "X-Desktop-Token": self.server.desktop_bridge.token,
                 "Origin": "https://attacker.invalid"},
            ):
                self.assertEqual(self.request("POST", "/api/paper", {"symbols": ["600001"]}, **headers)[0], 403)
            capture.assert_not_called()

    def test_http_create_read_and_pause_never_expose_frozen_data(self):
        """Given 合法本机请求 When 创建读取停止 Then 正常保留账本但不返回完整行情"""
        dataset = real_fixture(90)
        with tempfile.TemporaryDirectory() as root, patch.dict("os.environ", {"QUANT_PAPER_DIR": root}), patch(
            "research.paper._now", return_value=observed_at(dataset),
        ):
            status, payload = self.request("GET", "/api/paper")
            self.assertEqual(status, 200)
            headers = {"Content-Type": "application/json", "X-Desktop-Token": payload["request_token"]}
            from dataclasses import asdict
            status, payload = self.request("POST", "/api/paper", {
                "dataset": dataset.to_dict(), "config": {"strategy": asdict(STRATEGY)},
            }, **headers)
            self.assertEqual(status, 201)
            session = payload["session"]
            self.assertNotIn("dataset", session)
            self.assertFalse(session["live_trading_enabled"])
            session_id = session["session_id"]
            self.assertEqual(self.request("GET", f"/api/paper/{session_id}")[0], 200)
            self.assertIn("monitoring", session)
            status, payload = self.request("POST", f"/api/paper/{session_id}/pause",
                                           {"expected_revision": session["storage_revision"]}, **headers)
            self.assertEqual(status, 200)
            self.assertEqual(payload["session"]["status"], "PAUSED")
            revision = payload["session"]["storage_revision"]
            self.assertEqual(self.request("POST", f"/api/paper/{session_id}/resume",
                                          {"expected_revision": session["storage_revision"]}, **headers)[0], 409)
            status, payload = self.request("POST", f"/api/paper/{session_id}/resume",
                                           {"expected_revision": revision}, **headers)
            self.assertEqual(status, 200)
            self.assertEqual(payload["session"]["status"], "ACTIVE")
            self.assertTrue(payload["session"]["has_replay_gap"])
            (Path(root) / session_id / "state.json").write_text("broken")
            self.assertEqual(self.request("GET", f"/api/paper/{session_id}")[0], 422)
            status, payload = self.request("POST", f"/api/paper/{session_id}/recover", {}, **headers)
            self.assertEqual(status, 200)
            self.assertEqual(payload["session"]["status"], "PAUSED")
            self.assertEqual(payload["session"]["operations"]["revisions"][-1]["event"], "RECOVER")
            self.assertEqual(self.request("POST", f"/api/paper/{session_id}/buy", {}, **headers)[0], 422)


if __name__ == "__main__":
    unittest.main()
