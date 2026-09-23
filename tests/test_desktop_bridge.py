from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from desktop.tonghuashun import BridgeError, TonghuashunBridge, parse_snapshot, validate_local_request


def node(role, value="", children=None):
    return {"role": role, "text": value, "children": children or []}


def account_page(market="0.00", positions=None):
    funds = [("总资产", str(10000 + float(market))), ("总市值", market),
             ("资金余额", "10000.00"), ("可用金额", "9000.00"), ("可取金额", "8000.00")]
    headers = ["证券代码", "证券名称", "实际数量", "可用余额", "市价", "成本价", "市值"]
    return node("AXWindow", children=[
        node("AXStaticText", "平安证券 ––"),
        node("AXTable", children=[
            node("AXRow", children=[node("AXStaticText", label), node("AXStaticText", value)])
            for label, value in funds
        ]),
        node("AXTable", children=[
            node("AXGroup", children=[node("AXButton", text) for text in headers]),
            *[node("AXRow", children=[node("AXCell", children=[node("AXStaticText", cell)])
                                     for cell in row]) for row in (positions or [])],
        ]),
    ])


class FakeClient:
    def __init__(self):
        self.page = account_page()
        self.state = {"code": "READY_TO_READ", "installed": True, "running": True,
                      "accessibility": True, "version": "5.3.5", "runtime": "/test/python"}
        self.read_count = 0
        self.launch_count = 0

    def inspect(self):
        return dict(self.state)

    def read(self):
        self.read_count += 1
        if isinstance(self.page, Exception):
            raise self.page
        return copy.deepcopy(self.page)

    def launch(self):
        self.launch_count += 1

    def request_permission(self):
        return False


class SnapshotScenarios(unittest.TestCase):
    def test_verified_empty_positions(self):
        """Scenario: 可靠空仓
        Given 资金市值为零且持仓表存在
        When 解析客户端快照
        Then 返回经过校验的空仓而非读取失败
        """
        result = parse_snapshot(account_page())
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["funds"]["total_assets"], "10000.00")
        self.assertFalse(result["account_id_verified"])

    def test_positions_are_mapped_by_header_not_fixed_index(self):
        """Given 非空持仓与市值一致 When 同步 Then 按表头映射且保留证券代码前导零"""
        page = account_page("1000", [["000001", "测试证券", "100", "100", "10", "9", "1000"]])
        result = parse_snapshot(page)
        self.assertEqual(result["positions"][0]["symbol"], "000001")
        self.assertEqual(result["positions"][0]["quantity"], 100)
        self.assertEqual(result["positions"][0]["market_value"], "1000.00")

    def test_missing_positions_table_is_not_empty(self):
        page = account_page()
        page["children"].pop()
        with self.assertRaisesRegex(BridgeError, "持仓"):
            parse_snapshot(page)

    def test_nonzero_market_value_with_empty_table_is_rejected(self):
        for value in ["1000", "0.01"]:
            with self.subTest(value=value), self.assertRaisesRegex(BridgeError, "市值"):
                parse_snapshot(account_page(value))

    def test_partially_read_positions_are_rejected(self):
        with self.assertRaisesRegex(BridgeError, "市值"):
            parse_snapshot(account_page("2000", [["600000", "测试", "100", "100", "10", "9", "1000"]]))

    def test_missing_numeric_value_is_not_zero(self):
        page = account_page()
        page["children"][1]["children"][0]["children"][1]["text"] = "--"
        with self.assertRaises(BridgeError):
            parse_snapshot(page)

    def test_nonfinite_values_and_incoherent_cash_are_rejected(self):
        for value in ["NaN", "Infinity", "-1", "9e9999", "10,00", "10000000000000000"]:
            with self.subTest(value=value):
                page = account_page()
                page["children"][1]["children"][0]["children"][1]["text"] = value
                with self.assertRaises(BridgeError):
                    parse_snapshot(page)

    def test_wrong_broker_and_login_dialog_are_rejected(self):
        page = account_page()
        page["children"][0]["text"] = "其他证券"
        with self.assertRaises(BridgeError):
            parse_snapshot(page)
        with self.assertRaises(BridgeError):
            parse_snapshot(node("AXWindow", children=[node("AXTextField", "secret")]))

    def test_duplicate_tables_and_rows_are_rejected(self):
        page = account_page()
        page["children"].append(copy.deepcopy(page["children"][1]))
        with self.assertRaises(BridgeError):
            parse_snapshot(page)
        rows = [["000001", "测试", "100", "100", "10", "9", "1000"]] * 2
        with self.assertRaises(BridgeError):
            parse_snapshot(account_page("2000", rows))

    def test_invalid_quantity_and_missing_cells_are_rejected(self):
        for row in [["000001", "测试", "1.5", "1", "10", "9", "1000"],
                    ["000001", "测试", "100", "200", "10", "9", "1000"],
                    ["000001", "测试", "100", "100", "10", "9"]]:
            with self.subTest(row=row), self.assertRaises(BridgeError):
                parse_snapshot(account_page("1000", [row]))

    def test_private_fields_are_not_exported(self):
        page = account_page()
        page["children"].extend([node("AXTextField", "password"), node("AXStaticText", "客户姓名")])
        result = parse_snapshot(page)
        self.assertNotIn("password", str(result))
        self.assertNotIn("客户姓名", str(result))


class BridgeScenarios(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.now = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)
        self.bridge = TonghuashunBridge(self.client, clock=lambda: self.now)

    def test_status_does_not_read_account_or_claim_login(self):
        """Given 尚未同步 When 检查状态 Then 不读取资金且不声称账户已登录"""
        state = self.bridge.status()
        self.assertIsNone(state["snapshot"])
        self.assertEqual(self.client.read_count, 0)
        self.assertFalse(state["capabilities"]["live_trading"])

    def test_failed_sync_preserves_snapshot_and_marks_it_stale(self):
        """Given 已有成功快照 When 下次读取失败 Then 保留旧数据并明确标记过期"""
        first = self.bridge.perform("sync")["snapshot"]
        self.client.page = BridgeError("READ_FAILED", "页面不可读。")
        with self.assertRaises(BridgeError):
            self.bridge.perform("sync")
        state = self.bridge.status()
        self.assertEqual(state["snapshot"], first)
        self.assertTrue(state["stale"])
        self.assertEqual(state["last_error"]["code"], "READ_FAILED")

    def test_snapshot_expires_and_client_exit_invalidates_it(self):
        self.bridge.perform("sync")
        self.now += timedelta(seconds=61)
        self.assertTrue(self.bridge.status()["stale"])
        self.now -= timedelta(seconds=61)
        self.client.state["code"] = "NOT_RUNNING"
        self.client.state["running"] = False
        self.assertTrue(self.bridge.status()["stale"])

    def test_permission_failure_never_attempts_account_read(self):
        self.client.state["code"] = "ACCESSIBILITY_REQUIRED"
        self.client.state["accessibility"] = False
        with self.assertRaises(BridgeError):
            self.bridge.perform("sync")
        self.assertEqual(self.client.read_count, 0)

    def test_clear_and_launch_do_not_create_fake_account_data(self):
        self.bridge.perform("sync")
        self.assertIsNone(self.bridge.perform("clear")["snapshot"])
        self.bridge.perform("launch")
        self.assertEqual(self.client.launch_count, 1)
        self.assertIsNone(self.bridge.status()["snapshot"])

    def test_trade_actions_are_always_rejected(self):
        """Given 客户端已登录 When 请求下单或撤单 Then 拒绝且不操作客户端"""
        for action in ["buy", "sell", "cancel", "orders", "callback", "../launch"]:
            with self.subTest(action=action), self.assertRaises(BridgeError):
                self.bridge.perform(action)
        self.assertEqual(self.client.launch_count, 0)
        self.assertEqual(self.client.read_count, 0)

    def test_busy_sync_is_not_duplicated(self):
        self.bridge.lock.acquire()
        try:
            with self.assertRaisesRegex(BridgeError, "进行中"):
                self.bridge.perform("sync")
        finally:
            self.bridge.lock.release()
        self.assertEqual(self.client.read_count, 0)

    def test_client_exceptions_are_redacted(self):
        self.client.page = RuntimeError("secret token")
        with self.assertRaises(BridgeError):
            self.bridge.perform("sync")
        self.assertNotIn("secret", str(self.bridge.status()))


class LocalRequestScenarios(unittest.TestCase):
    def validate(self, **changes):
        values = dict(bind_host="127.0.0.1", peer_host="127.0.0.1", port=8000,
                      headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000",
                               "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json",
                               "X-Desktop-Token": "expected"},
                      mutation=True, token="expected")
        values.update(changes)
        return validate_local_request(**values)

    def test_local_same_origin_token_is_required(self):
        self.validate()
        for updates in [{"Host": "evil.example:8000"}, {"Origin": "http://evil.example"},
                        {"Sec-Fetch-Site": "cross-site"}, {"X-Desktop-Token": "wrong"},
                        {"X-Desktop-Token": "非法"},
                        {"Content-Type": "text/plain"}, {"Origin": "http://127.0.0.1:9000"}]:
            with self.subTest(updates=updates):
                headers = {"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000",
                           "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json",
                           "X-Desktop-Token": "expected", **updates}
                with self.assertRaises(BridgeError):
                    self.validate(headers=headers)

    def test_remote_bind_or_peer_is_rejected(self):
        for change in [{"bind_host": "0.0.0.0"}, {"peer_host": "192.168.1.2"},
                       {"bind_host": "192.168.1.1"}]:
            with self.subTest(change=change), self.assertRaises(BridgeError):
                self.validate(**change)

    def test_cross_origin_status_cannot_expose_snapshot_or_token(self):
        with self.assertRaises(BridgeError):
            self.validate(mutation=False, headers={"Host": "127.0.0.1:8000",
                                                  "Origin": "http://evil.example"})


class HttpBridgeScenarios(unittest.TestCase):
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

    def setUp(self):
        self.client = FakeClient()
        self.server.desktop_bridge = TonghuashunBridge(self.client)

    def request(self, method, route, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, route, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_given_local_page_when_sync_then_return_verified_snapshot(self):
        status, state = self.request("GET", "/api/client")
        self.assertEqual(status, 200)
        token = state["connection"]["request_token"]
        status, payload = self.request("POST", "/api/client/sync", "{}", {
            "Content-Type": "application/json", "X-Desktop-Token": token,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["connection"]["snapshot"]["positions"], [])

    def test_given_cross_site_when_launch_then_do_not_operate_client(self):
        status, payload = self.request("POST", "/api/client/launch", "{}", {
            "Origin": "https://attacker.invalid", "Content-Type": "application/json",
            "X-Desktop-Token": self.server.desktop_bridge.token,
        })
        self.assertEqual(status, 403)
        self.assertNotIn("connection", payload)
        self.assertEqual(self.client.launch_count, 0)

    def test_given_invalid_body_or_trade_action_then_no_side_effects(self):
        headers = {"Content-Type": "application/json", "X-Desktop-Token": self.server.desktop_bridge.token}
        for route, body, expected in [("/api/client/launch", '{"url":"hexinstock://buy"}', 400),
                                      ("/api/client/buy", "{}", 409),
                                      ("/api/client/sync", "[]", 400)]:
            with self.subTest(route=route):
                status, _ = self.request("POST", route, body, headers)
                self.assertEqual(status, expected)
        self.assertEqual(self.client.launch_count, 0)
        self.assertEqual(self.client.read_count, 0)

    def test_given_failed_sync_then_api_returns_old_stale_snapshot(self):
        self.server.desktop_bridge.perform("sync")
        self.client.page = BridgeError("READ_FAILED")
        status, payload = self.request("POST", "/api/client/sync", "{}", {
            "Content-Type": "application/json", "X-Desktop-Token": self.server.desktop_bridge.token,
        })
        self.assertEqual(status, 422)
        self.assertIsNotNone(payload["connection"]["snapshot"])
        self.assertTrue(payload["connection"]["stale"])

    def test_page_cannot_be_embedded_for_clickjacking(self):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
            self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy", ""))
            response.read()
        finally:
            connection.close()


class NativeGuardScenarios(unittest.TestCase):
    def test_non_mac_platform_never_starts_native_helper(self):
        from desktop.macos import MacClient
        with patch("desktop.macos.platform.system", return_value="Linux"):
            client = MacClient()
            with patch.object(client, "_helper") as helper:
                self.assertEqual(client.inspect()["code"], "UNSUPPORTED_PLATFORM")
                with self.assertRaises(BridgeError):
                    client.request_permission()
                helper.assert_not_called()

    def test_helper_timeout_is_redacted(self):
        import subprocess
        from desktop.macos import MacClient
        with patch("desktop.macos.subprocess.run", side_effect=subprocess.TimeoutExpired("secret", 1)):
            with self.assertRaises(BridgeError) as raised:
                MacClient()._helper("snapshot", 123)
        self.assertNotIn("secret", str(raised.exception))

    def test_reader_never_reads_input_values(self):
        from desktop.macos import AccessibilityReader
        import time
        for role in ["AXTextField", "AXSecureTextField", "AXTextArea"]:
            reader = object.__new__(AccessibilityReader)
            reader.count = 0
            reader.started = time.monotonic()
            with patch.object(reader, "text", return_value=role) as read_text:
                reader.walk(123)
            self.assertEqual(read_text.call_args_list[0].args, (123, "AXRole"))
            self.assertEqual(read_text.call_count, 1)

    def test_multiple_clients_are_not_guessed(self):
        from desktop.macos import MacClient
        with patch("desktop.macos.client_pids", return_value=[1, 2]):
            with self.assertRaises(BridgeError) as raised:
                MacClient().read()
        self.assertEqual(raised.exception.code, "MULTIPLE_CLIENTS")

    def test_mounted_or_other_client_location_is_rejected(self):
        from desktop.macos import client_pids
        import subprocess
        result = subprocess.CompletedProcess([], 0, stdout="123 /Volumes/同花顺/同花顺.app/Contents/MacOS/同花顺\n")
        with patch("desktop.macos.subprocess.run", return_value=result):
            with self.assertRaises(BridgeError) as raised:
                client_pids()
        self.assertEqual(raised.exception.code, "CLIENT_LOCATION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
