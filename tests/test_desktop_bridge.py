from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from desktop.tonghuashun import (
    BridgeError, TonghuashunBridge, parse_snapshot, parse_watchlist, validate_local_request,
)


def node(role, value="", children=None):
    return {"role": role, "text": value, "children": children or []}


def account_page(market="0.00", positions=None, *, fund_overrides=None):
    funds = [("总资产", str(10000 + float(market))), ("总市值", market),
             ("资金余额", "10000.00"), ("可用金额", "9000.00"), ("可取金额", "8000.00")]
    headers = ["证券代码", "证券名称", "实际数量", "可用余额", "市价", "成本价", "市值"]
    return node("AXWindow", children=[
        node("AXStaticText", "平安证券 ––"),
        node("AXTable", children=[
            node("AXRow", children=[node("AXStaticText", label),
                                   node("AXStaticText", (fund_overrides or {}).get(label, value))])
            for label, value in funds
        ]),
        node("AXTable", children=[
            node("AXGroup", children=[node("AXButton", text) for text in headers]),
            *[node("AXRow", children=[node("AXCell", children=[node("AXStaticText", cell)])
                                     for cell in row]) for row in (positions or [])],
        ]),
    ])


def watchlist_page(stocks=None, count=None, selected="自选股"):
    stocks = [("000001", "测试证券"), ("688001", "测试科创")] if stocks is None else stocks
    groups = ["自选股", "量化"]
    return node("AXSheet", children=[
        node("AXStaticText", "自选分组设置（共2组）："),
        node("AXStaticText", f"所选分组成分股设置（共{len(stocks) if count is None else count}只）："),
        node("AXTable", children=[
            {**node("AXRow", children=[node("AXCell", children=[node("AXStaticText", name)])]),
             "selected": name == selected} for name in groups
        ]),
        node("AXTable", children=[
            node("AXRow", children=[node("AXCell", children=[
                node("AXStaticText", symbol), node("AXStaticText", name),
            ])]) for symbol, name in stocks
        ]),
    ])


class FakeClient:
    def __init__(self):
        self.page = account_page()
        self.state = {"code": "READY_TO_READ", "installed": True, "running": True,
                      "accessibility": True, "version": "5.3.5", "runtime": "/test/python"}
        self.read_count = 0
        self.watchlist_page = watchlist_page()
        self.watchlist_read_count = 0
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

    def read_watchlist(self):
        self.watchlist_read_count += 1
        if isinstance(self.watchlist_page, Exception):
            raise self.watchlist_page
        return copy.deepcopy(self.watchlist_page)

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

    def test_cash_balance_is_the_preferred_display_basis(self):
        """Scenario: 优先核对资金余额
        Given 总资产与市值加资金余额一致，可用金额可能也一致
        When 解析客户端快照
        Then 记录资金余额口径且仅声明显示值核对
        """
        for available in ["9000.00", "10000.00"]:
            with self.subTest(available=available):
                result = parse_snapshot(account_page(fund_overrides={"可用金额": available}))
                self.assertEqual(result["funds_reconciliation"], {
                    "scope": "client_display", "cash_basis": "cash_balance",
                })

    def test_available_cash_display_basis_preserves_all_original_amounts(self):
        """Scenario: 资金余额与资产显示口径不同
        Given 总资产仅与市值加可用金额一致且持仓市值完整
        When 解析客户端快照
        Then 接受快照并明确口径，不用可用金额覆盖资金余额
        """
        page = account_page("1000", [["000001", "测试证券", "100", "100", "10", "9", "1000"]],
                            fund_overrides={"总资产": "9000", "可用金额": "8000", "可取金额": "7000"})
        result = parse_snapshot(page)
        self.assertEqual(result["funds"], {
            "total_assets": "9000.00", "market_value": "1000.00", "cash_balance": "10000.00",
            "available_cash": "8000.00", "withdrawable_cash": "7000.00",
        })
        self.assertEqual(result["funds_reconciliation"], {
            "scope": "client_display", "cash_basis": "available_cash",
        })
        self.assertEqual(len(result["positions"]), 1)
        self.assertFalse(result["account_id_verified"])

    def test_both_display_bases_keep_the_five_cent_tolerance(self):
        """Scenario: 不扩大资金校验容差
        Given 分别采用资金余额或可用金额口径
        When 合计误差为五分或六分
        Then 五分通过且六分拒绝
        """
        for total, basis in [("10000.05", "cash_balance"), ("9000.05", "available_cash")]:
            with self.subTest(total=total):
                result = parse_snapshot(account_page(fund_overrides={"总资产": total}))
                self.assertEqual(result["funds_reconciliation"]["cash_basis"], basis)
        for total in ["10000.06", "9999.94", "9000.06", "8999.94"]:
            with self.subTest(total=total), self.assertRaisesRegex(BridgeError, "资金合计"):
                parse_snapshot(account_page(fund_overrides={"总资产": total}))

    def test_neither_display_basis_or_invalid_cash_bounds_are_rejected(self):
        """Scenario: 显示口径兼容不掩盖异常
        Given 合计不匹配、只有可取金额匹配或可用/可取金额超过资金余额
        When 解析客户端快照
        Then 继续拒绝更新
        """
        for overrides in [
            {"总资产": "9500"}, {"总资产": "8000"},
            {"可用金额": "10001"}, {"可取金额": "10001"},
            {"总资产": "11000", "可用金额": "11000"},
            {"总资产": "9000", "可取金额": "10001"},
        ]:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(BridgeError, "资金合计"):
                parse_snapshot(account_page(fund_overrides=overrides))

    def test_available_cash_basis_still_requires_complete_positions(self):
        """Scenario: 资金匹配仍需校验持仓
        Given 总资产与市值加可用金额一致但持仓表为空或缺行
        When 解析客户端快照
        Then 拒绝更新而非伪造完整持仓
        """
        for rows in [[], [["000001", "测试证券", "10", "10", "10", "9", "100"]]]:
            with self.subTest(rows=rows), self.assertRaisesRegex(BridgeError, "持仓市值"):
                parse_snapshot(account_page("1000", rows, fund_overrides={"总资产": "10000"}))

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

    def test_available_cash_basis_never_bypasses_dialog_or_password_guards(self):
        """Scenario: 资金口径兼容不绕过登录保护
        Given 资金可按可用金额核对，但页面存在对话框或密码框
        When 同步账户
        Then 拒绝读取登录状态不明确的快照
        """
        for blocker in [node("AXSheet"), node("AXSecureTextField"),
                        {**node("AXWindow"), "subrole": "AXDialog"},
                        {**node("AXWindow"), "subrole": "AXSystemDialog"}]:
            with self.subTest(blocker=blocker):
                page = account_page(fund_overrides={"总资产": "9000"})
                page["children"].append(blocker)
                with self.assertRaises(BridgeError) as raised:
                    parse_snapshot(page)
                self.assertEqual(raised.exception.code, "TRADE_PAGE_REQUIRED")

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


class WatchlistScenarios(unittest.TestCase):
    def test_selected_group_is_complete_and_not_a_broker_account(self):
        """Scenario: 自选股只读同步
        Given 分组设置显示两个成员且选中自选股
        When 读取分组
        Then 保留代码前导零并核对总数，不声称是券商持仓
        """
        result = parse_watchlist(watchlist_page())
        self.assertEqual(result["group_name"], "自选股")
        self.assertEqual(result["stocks"], [
            {"symbol": "000001", "name": "测试证券"},
            {"symbol": "688001", "name": "测试科创"},
        ])
        self.assertEqual(result["declared_count"], 2)
        self.assertEqual(result["scope"], "selected_group")
        self.assertFalse(result["account_id_verified"])
        self.assertNotIn("quantity", result["stocks"][0])

    def test_combined_text_and_custom_selected_group(self):
        page = watchlist_page(selected="量化")
        page["children"][3]["children"][0] = node("AXRow", children=[
            node("AXStaticText", "000001 测试证券"),
        ])
        self.assertEqual(parse_watchlist(page)["group_name"], "量化")
        self.assertEqual(parse_watchlist(page)["stocks"][0]["symbol"], "000001")

    def test_zero_members_requires_explicit_zero_count(self):
        """Given 选中组显示零只 When 同步 Then 返回已核验空组而不是伪造缺失数据"""
        self.assertEqual(parse_watchlist(watchlist_page([]))["stocks"], [])
        with self.assertRaises(BridgeError):
            parse_watchlist(watchlist_page([], count=2))

    def test_incomplete_duplicate_and_ambiguous_members_fail_closed(self):
        pages = [
            watchlist_page(count=3),
            watchlist_page([("000001", "甲"), ("000001", "乙")]),
            watchlist_page([("123", "错误代码")]),
            watchlist_page(selected="不存在"),
        ]
        extra_table = watchlist_page()
        extra_table["children"].append(copy.deepcopy(extra_table["children"][3]))
        pages.append(extra_table)
        for page in pages:
            with self.subTest(page=page), self.assertRaises(BridgeError):
                parse_watchlist(page)

    def test_market_page_or_credentials_cannot_be_misread_as_watchlist(self):
        """Given 普通行情表或登录弹窗 When 同步自选 Then 不收集凭据、不误读行情池"""
        for page in [account_page(), node("AXWindow"), watchlist_page()]:
            if page["role"] == "AXSheet":
                page["children"].append(node("AXSecureTextField", "secret"))
            with self.subTest(page=page), self.assertRaises(BridgeError):
                parse_watchlist(page)

    def test_only_the_group_sheet_is_used(self):
        sheet = watchlist_page()
        tree = node("AXApplication", children=[
            node("AXWindow", children=[node("AXStaticText", "unrelated"), sheet]),
        ])
        self.assertEqual(len(parse_watchlist(tree)["stocks"]), 2)
        self.assertNotIn("unrelated", str(parse_watchlist(tree)))
        tree["children"].append(copy.deepcopy(sheet))
        with self.assertRaises(BridgeError):
            parse_watchlist(tree)


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

    def test_display_basis_sync_failure_preserves_snapshot_and_recovery_clears_error(self):
        """Scenario: 兼容口径的同步仍原子更新
        Given 已用可用金额口径成功同步
        When 下次合计无法核对，随后恢复一致
        Then 失败保留旧快照并标过期，恢复后更新快照并清除错误
        """
        self.client.page = account_page(fund_overrides={"总资产": "9000"})
        first = self.bridge.perform("sync")["snapshot"]
        self.client.page = account_page(fund_overrides={"总资产": "9500"})
        with self.assertRaisesRegex(BridgeError, "资金合计"):
            self.bridge.perform("sync")
        failed = self.bridge.status()
        self.assertEqual(failed["snapshot"], first)
        self.assertTrue(failed["stale"])
        self.assertEqual(failed["last_error"]["code"], "INCOMPLETE_SNAPSHOT")
        self.now += timedelta(seconds=1)
        self.client.page = account_page(fund_overrides={"总资产": "9000"})
        recovered = self.bridge.perform("sync")
        self.assertIsNone(recovered["last_error"])
        self.assertFalse(recovered["stale"])
        self.assertNotEqual(recovered["snapshot"]["captured_at"], first["captured_at"])
        self.assertEqual(recovered["snapshot"]["funds_reconciliation"]["cash_basis"], "available_cash")

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

    def test_watchlist_and_account_sync_have_independent_lifetimes(self):
        """Given 已同步自选股 When 未登录的持仓读取失败 Then 自选保留有效且账户不伪造空仓"""
        watchlist = self.bridge.perform("sync-watchlist")["watchlist"]
        self.assertEqual(len(watchlist["stocks"]), 2)
        self.assertEqual(self.client.read_count, 0)
        self.client.page = BridgeError("TRADE_PAGE_REQUIRED")
        with self.assertRaises(BridgeError):
            self.bridge.perform("sync")
        result = self.bridge.status()
        self.assertIsNone(result["snapshot"])
        self.assertFalse(result["watchlist_stale"])
        self.assertTrue(result["stale"])

    def test_failed_watchlist_sync_does_not_invalidate_verified_account(self):
        self.bridge.perform("sync")
        first = self.bridge.perform("sync-watchlist")["watchlist"]
        self.client.watchlist_page = watchlist_page(count=10)
        with self.assertRaises(BridgeError):
            self.bridge.perform("sync-watchlist")
        result = self.bridge.status()
        self.assertEqual(result["watchlist"], first)
        self.assertTrue(result["watchlist_stale"])
        self.assertFalse(result["stale"])
        self.assertIsNotNone(result["watchlist_last_error"])

    def test_watchlist_requires_permission_expires_and_clear_removes_both(self):
        self.client.state["code"] = "ACCESSIBILITY_REQUIRED"
        with self.assertRaises(BridgeError):
            self.bridge.perform("sync-watchlist")
        self.assertEqual(self.client.watchlist_read_count, 0)
        self.client.state["code"] = "READY_TO_READ"
        self.bridge.perform("sync-watchlist")
        self.bridge.perform("sync")
        self.now += timedelta(seconds=61)
        self.assertTrue(self.bridge.status()["watchlist_stale"])
        result = self.bridge.perform("clear")
        self.assertIsNone(result["watchlist"])
        self.assertIsNone(result["snapshot"])

    def test_status_does_not_read_watchlist_and_reports_read_capability(self):
        result = self.bridge.status()
        self.assertIsNone(result["watchlist"])
        self.assertEqual(self.client.watchlist_read_count, 0)
        self.assertTrue(result["capabilities"]["watchlist_sync"])


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
        industries = patch("web.INDUSTRIES.refresh")
        industries.start()
        self.addCleanup(industries.stop)

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

    def test_trade_draft_requires_same_origin_and_never_submits(self):
        """Given 买卖核对接口 When 跨站或携带凭据 Then 拒绝；合法请求只返回未提交草稿"""
        headers = {"Content-Type": "application/json", "X-Desktop-Token": self.server.desktop_bridge.token}
        body = {"side": "BUY", "symbol": "600001", "price": "10.00", "quantity": 100}
        for current_headers in [{**headers, "Origin": "https://attacker.invalid"}, {"Content-Type": "application/json"}]:
            status, _ = self.request("POST", "/api/client/prepare-trade", json.dumps(body), current_headers)
            self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/client/prepare-trade", json.dumps({**body, "password": "secret"}), headers)
        self.assertEqual(status, 400)
        self.assertEqual(self.client.read_count, 0)
        status, payload = self.request("POST", "/api/client/prepare-trade", json.dumps(body), headers)
        self.assertEqual(status, 200)
        self.assertEqual(payload["draft"]["status"], "NOT_SUBMITTED")
        self.assertEqual(self.client.launch_count, 0)

    def test_history_consent_requires_local_token_and_never_accepts_credentials(self):
        """Given 未授权历史查询 When 跨站或携带凭据申请 Then 拒绝且不读取账户"""
        headers = {"Content-Type": "application/json", "X-Desktop-Token": self.server.desktop_bridge.token}
        for invalid_headers, body, expected in [
            ({**headers, "Origin": "https://attacker.invalid"}, "{}", 403),
            ({"Content-Type": "application/json"}, "{}", 403),
            (headers, '{"password":"never-accept"}', 400),
        ]:
            status, _ = self.request("POST", "/api/client/history-enable", body, invalid_headers)
            self.assertEqual(status, expected)
            self.assertFalse(self.server.desktop_bridge.public_history_enabled)
        for action, enabled in [("history-enable", True), ("history-disable", False)]:
            status, payload = self.request("POST", f"/api/client/{action}", "{}", headers)
            self.assertEqual(status, 200)
            self.assertEqual(payload["connection"]["public_history_enabled"], enabled)
        self.assertEqual(self.client.read_count, 0)

    def test_live_configuration_is_local_only_and_start_remains_blocked(self):
        """Given 全自动实盘接口 When 保存或伪造启动 Then 同源校验生效且不会产生真实委托"""
        from test_live_automation import PLAN
        headers = {"Content-Type": "application/json", "X-Desktop-Token": self.server.desktop_bridge.token}
        body = json.dumps({"config": PLAN, "expected_revision": 0})
        for path in ("automation-save", "automation-start"):
            status, _ = self.request("POST", f"/api/client/{path}", body, {**headers, "Origin": "https://attacker.invalid"})
            self.assertEqual(status, 403)
            status, _ = self.request("POST", f"/api/client/{path}", body, {"Content-Type": "application/json"})
            self.assertEqual(status, 403)
        status, payload = self.request("POST", "/api/client/automation-save", body, headers)
        self.assertEqual(status, 200)
        self.assertFalse(payload["connection"]["automation"]["can_start"])
        status, _ = self.request("POST", "/api/client/automation-save", body, headers)
        self.assertEqual(status, 409)
        status, payload = self.request("POST", "/api/client/automation-start", '{"expected_revision":1}', headers)
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "LIVE_TRADING_UNAVAILABLE")
        status, _ = self.request("POST", "/api/client/automation-start", '{"expected_revision":1,"force":true}', headers)
        self.assertEqual(status, 400)
        self.assertEqual((self.client.read_count, self.client.launch_count), (0, 0))

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

    def test_watchlist_sync_requires_same_origin_token_and_has_no_account_data(self):
        """Given 本机自选设置页 When 经同源校验同步 Then 仅返回当前组并拒绝跨站请求"""
        headers = {"Content-Type": "application/json", "X-Desktop-Token": self.server.desktop_bridge.token}
        status, payload = self.request("POST", "/api/client/sync-watchlist", "{}", headers)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["connection"]["watchlist"]["stocks"]), 2)
        self.assertIsNone(payload["connection"]["snapshot"])
        status, _ = self.request("POST", "/api/client/sync-watchlist", "{}", {
            **headers, "Origin": "https://attacker.invalid",
        })
        self.assertEqual(status, 403)
        self.assertEqual(self.client.watchlist_read_count, 1)


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

    def test_reader_skips_only_the_exact_window_sharing_indicator(self):
        """Scenario: 窗口共享指示器不被误判为登录弹窗
        Given 额外窗口仅包含已识别的共享状态按钮
        When 原生读取器遍历该窗口
        Then 忽略该指示器，但保留其他对话框和含额外内容的窗口
        """
        from desktop.macos import AccessibilityReader
        import time
        indicator = node("AXButton", "WindowSharingSessionButton")
        for subrole, children, skip in [
            ("AXDialog", [indicator], True),
            ("AXDialog", [indicator, node("AXSecureTextField")], False),
            ("AXDialog", [indicator, node("AXStaticText", "登录已过期")], False),
            ("AXDialog", [node("AXButton", "登录")], False),
            ("AXDialog", [], False),
            ("AXSystemDialog", [indicator], False),
            ("AXStandardWindow", [indicator], False),
        ]:
            with self.subTest(subrole=subrole, children=children):
                reader = object.__new__(AccessibilityReader)
                reader.count = 0
                reader.started = time.monotonic()
                attributes = {"AXRole": "AXWindow", "AXSubrole": subrole}
                with patch.object(reader, "text", side_effect=lambda element, name: attributes.get(name, "")), \
                        patch.object(reader, "children", return_value=children):
                    result = reader.walk(123)
                if skip:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result, {**node("AXWindow", children=children), "subrole": subrole})

    def test_watchlist_discovery_skips_unrelated_tables_and_inputs(self):
        """Given 主窗口含行情大表与输入框 When 查找自选设置 Then 不遍历无关数据或凭据"""
        from desktop.macos import AccessibilityReader
        import time
        for role in ["AXTable", "AXTextField", "AXSecureTextField", "AXToolbar"]:
            reader = object.__new__(AccessibilityReader)
            reader.count = 0
            reader.started = time.monotonic()
            with patch.object(reader, "text", return_value=role), patch.object(reader, "children") as children:
                self.assertEqual(reader.find_sheets(123), [])
                children.assert_not_called()

    def test_watchlist_discovery_reads_only_sheet_subtree(self):
        from desktop.macos import AccessibilityReader
        import time
        reader = object.__new__(AccessibilityReader)
        reader.count = 0
        reader.started = time.monotonic()
        sheet = watchlist_page()
        with patch.object(reader, "text", return_value="AXSheet"), patch.object(reader, "walk", return_value=sheet):
            self.assertEqual(reader.find_sheets(123), [sheet])

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
