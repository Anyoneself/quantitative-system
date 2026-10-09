from datetime import datetime, timedelta, timezone
import unittest

from desktop.tonghuashun import BridgeError, TonghuashunBridge
from test_desktop_bridge import FakeClient, account_page


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


class TradeTicketScenarios(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.client.page = account_page("3500", [
            ["600001", "测试持仓", "350", "250", "10", "9", "3500"],
        ])
        self.bridge = TonghuashunBridge(self.client, clock=lambda: NOW)
        self.buy = {"side": "BUY", "symbol": "600002", "price": "10.25", "quantity": 100}

    def test_buy_draft_uses_fresh_account_without_submitting_or_changing_holdings(self):
        """Given 已登录同花顺 When 核对买入委托 Then 重新读取账户并生成未提交草稿，不调用交易或修改持仓"""
        result = self.bridge.prepare_trade(self.buy)
        draft = result["draft"]
        self.assertEqual(draft["status"], "NOT_SUBMITTED")
        self.assertEqual(draft["amount"], "1025.00")
        self.assertEqual(draft["available_cash"], "9000.00")
        self.assertEqual(draft["expires_at"], (NOW + timedelta(seconds=60)).isoformat())
        self.assertFalse(draft["account_id_verified"])
        self.assertEqual(self.client.read_count, 1)
        self.assertEqual(self.client.launch_count, 0)
        self.assertEqual(result["connection"]["snapshot"]["positions"][0]["quantity"], 350)
        self.assertFalse(result["connection"]["capabilities"]["live_trading"])

    def test_invalid_input_is_rejected_before_reading_client(self):
        """Given 凭据或不合规委托参数 When 核对 Then 拒绝且不访问客户端"""
        for change in [
            {"side": "CANCEL"}, {"side": []}, {"symbol": "688001"}, {"symbol": "300001"},
            {"symbol": "510300"}, {"symbol": "600001;open"}, {"price": "NaN"},
            {"price": "Infinity"}, {"price": "0"}, {"price": "-1"}, {"price": "10.001"},
            {"price": True}, {"price": []}, {"quantity": True}, {"quantity": 1.5},
            {"quantity": 0}, {"quantity": 150}, {"quantity": 1000001},
            {"password": "never-accept"}, {"url": "hexinstock://buy"}, {"confirm": True},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.bridge.prepare_trade({**self.buy, **change})
        self.assertEqual(self.client.read_count, 0)
        self.assertEqual(self.client.launch_count, 0)

    def test_sell_checks_available_not_total_and_allows_remaining_odd_lot(self):
        """Given 持仓350股可卖250股 When 卖出 Then 不可超卖，零股仅允许一次卖出全部可卖余额"""
        sell = {**self.buy, "side": "SELL", "symbol": "600001"}
        for quantity in (100, 200, 250):
            draft = self.bridge.prepare_trade({**sell, "quantity": quantity})["draft"]
            self.assertEqual(draft["available_quantity"], 250)
            self.assertEqual(draft["name"], "测试持仓")
        for quantity in (300, 50, 150):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                self.bridge.prepare_trade({**sell, "quantity": quantity})
        with self.assertRaises(ValueError):
            self.bridge.prepare_trade({**sell, "symbol": "600002"})

    def test_insufficient_cash_or_no_fee_buffer_blocks_buy(self):
        """Given 可用资金不足或没有手续费余量 When 核对买入 Then 不生成可继续的草稿"""
        for price in ("91", "90"):
            with self.subTest(price=price), self.assertRaises(ValueError):
                self.bridge.prepare_trade({**self.buy, "price": price})

    def test_disconnected_client_never_reuses_previous_success(self):
        """Given 旧账户快照存在但客户端已断线 When 核对 Then 拒绝旧草稿依据并标记账户过期"""
        self.bridge.prepare_trade(self.buy)
        self.client.page = BridgeError("TRADE_PAGE_REQUIRED")
        with self.assertRaises(BridgeError):
            self.bridge.prepare_trade(self.buy)
        self.assertTrue(self.bridge.status()["stale"])
        self.assertEqual(self.client.launch_count, 0)

    def test_changed_cash_is_read_again_on_each_review(self):
        """Given 上次余额足够但客户端余额已变化 When 再核对 Then 不复用旧余额"""
        self.bridge.prepare_trade(self.buy)
        self.client.page = account_page("3500", [
            ["600001", "测试持仓", "350", "250", "10", "9", "3500"],
        ], fund_overrides={"可用金额": "100"})
        with self.assertRaises(ValueError):
            self.bridge.prepare_trade(self.buy)
        self.assertEqual(self.client.read_count, 2)
