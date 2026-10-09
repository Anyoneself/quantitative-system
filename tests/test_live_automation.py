from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from desktop.tonghuashun import BridgeError, TonghuashunBridge
from test_desktop_bridge import FakeClient


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
PLAN = {
    "capital_limit": "10000.00", "max_order_amount": "1000.00",
    "max_position_percent": "20.00", "daily_loss_limit": "200.00",
    "symbols": ["600001", "000001"], "buy_rule": "测试买入条件，不是真实投资建议",
    "sell_rule": "测试卖出条件", "allow_existing_positions": False,
}


class LiveAutomationScenarios(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "live-plan.json"
        self.client = FakeClient()
        self.bridge = TonghuashunBridge(self.client, clock=lambda: NOW, automation_path=self.path)

    def save(self, config=None, revision=0):
        return self.bridge.configure_automation({"config": config or PLAN, "expected_revision": revision})

    def test_new_live_page_is_blocked_and_has_no_default_risk_budget(self):
        """Given 首次打开实盘栏目 When 读取状态 Then 预算为空且无任何真实交易操作"""
        state = self.bridge.status()["automation"]
        self.assertEqual(state["mode"], "LIVE")
        self.assertEqual(state["status"], "BLOCKED")
        self.assertFalse(state["running"])
        self.assertFalse(state["can_start"])
        self.assertIsNone(state["config"])
        self.assertEqual(state["revision"], 0)
        self.assertFalse(self.path.exists())
        self.assertEqual((self.client.launch_count, self.client.read_count), (0, 0))

    def test_saving_and_restarting_preserve_plan_but_never_enable_trading(self):
        """Given 完整规则草案 When 保存后重启服务 Then 仅恢复草案，不恢复或启动实盘"""
        saved = self.save()["automation"]
        self.assertEqual(saved["config"], PLAN)
        self.assertEqual(saved["revision"], 1)
        restarted = TonghuashunBridge(self.client, clock=lambda: NOW, automation_path=self.path)
        state = restarted.status()["automation"]
        self.assertEqual(state["config"], PLAN)
        self.assertFalse(state["running"])
        self.assertFalse(state["can_start"])
        self.assertTrue(next(item for item in state["checks"] if item["id"] == "configuration")["passed"])
        self.assertFalse(next(item for item in state["checks"] if item["id"] == "execution")["passed"])
        self.assertEqual((self.client.launch_count, self.client.read_count), (0, 0))

    def test_start_is_blocked_even_after_valid_save_and_fresh_account(self):
        """Given 草案已保存且账户可读 When 请求实盘启动 Then 仍阻断，不把只读能力当成交易通道"""
        self.save()
        self.bridge.perform("sync")
        with self.assertRaisesRegex(BridgeError, "实盘") as context:
            self.bridge.start_automation({"expected_revision": 1})
        self.assertEqual(context.exception.code, "LIVE_TRADING_UNAVAILABLE")
        self.assertFalse(self.bridge.status()["automation"]["can_start"])
        self.assertEqual(self.client.launch_count, 0)
        self.assertEqual(self.client.read_count, 1)

    def test_invalid_limits_unknown_fields_and_credentials_are_rejected(self):
        """Given 空预算、冲突限额、无效证券或额外凭据 When 保存 Then 不写入配置"""
        for change in [
            {"capital_limit": ""}, {"capital_limit": "NaN"}, {"capital_limit": True},
            {"capital_limit": "1e6"}, {"capital_limit": "10.001"}, {"daily_loss_limit": "0"},
            {"daily_loss_limit": "10001"}, {"max_order_amount": "2001"},
            {"max_position_percent": "101"}, {"max_position_percent": "0"},
            {"symbols": ["600001", "600001"]}, {"symbols": []}, {"symbols": ["300001"]},
            {"buy_rule": "   "}, {"buy_rule": "x" * 501}, {"sell_rule": []},
            {"allow_existing_positions": "true"}, {"password": "never-accept"},
            {"live_trading_enabled": True}, {"broker_ready": True},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.save({**PLAN, **change})
        self.assertFalse(self.path.exists())
        self.assertEqual((self.client.launch_count, self.client.read_count), (0, 0))

    def test_revision_conflict_preserves_newer_configuration(self):
        """Given 另一页面已保存配置 When 旧页面再次保存 Then 拒绝覆盖更新后的版本"""
        self.save()
        with self.assertRaises(BridgeError) as context:
            self.save({**PLAN, "capital_limit": "20000"})
        self.assertEqual(context.exception.code, "CONFIG_CONFLICT")
        self.assertEqual(self.bridge.status()["automation"]["config"], PLAN)
        self.save({**PLAN, "capital_limit": "20000"}, revision=1)
        self.assertEqual(self.bridge.status()["automation"]["revision"], 2)

    def test_corrupt_configuration_cannot_be_overwritten_or_treated_as_ready(self):
        """Given 配置损坏或被加入启动标志 When 读取保存启动 Then 明确报错且保持阻断"""
        self.save()
        content = json.loads(self.path.read_text())
        content["running"] = True
        self.path.write_text(json.dumps(content))
        state = self.bridge.status()["automation"]
        self.assertTrue(state["storage_error"])
        self.assertIsNone(state["config"])
        self.assertIsNone(state["revision"])
        self.assertFalse(state["running"])
        with self.assertRaises(ValueError):
            self.save(revision=1)
        with self.assertRaises(BridgeError):
            self.bridge.start_automation({"expected_revision": 1})
        self.assertTrue(json.loads(self.path.read_text())["running"])

    def test_returned_config_and_forged_capabilities_cannot_unlock_execution(self):
        """Given 状态数据被修改或请求伪造就绪字段 When 再读取或启动 Then 不解锁实盘"""
        result = self.save()
        result["automation"]["config"]["symbols"].append("600002")
        result["capabilities"]["live_trading"] = True
        self.assertEqual(self.bridge.status()["automation"]["config"], PLAN)
        for payload in ({"expected_revision": 1, "force": True}, {"expected_revision": True}, {}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.bridge.start_automation(payload)


if __name__ == "__main__":
    unittest.main()
