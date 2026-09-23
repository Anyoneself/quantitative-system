from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import ipaddress
import re
import secrets
import threading


MESSAGES = {
    "READY_TO_READ": "客户端可访问，账户登录状态尚需通过同步检查。",
    "UNSUPPORTED_PLATFORM": "本机连接仅支持 macOS 原生服务，不支持 Docker 或远程服务器。",
    "NOT_INSTALLED": "未在应用程序目录找到同花顺。",
    "NOT_RUNNING": "同花顺尚未运行。",
    "ACCESSIBILITY_REQUIRED": "需要为运行本服务的 Python 或终端授予 macOS 辅助功能权限。",
    "UNSUPPORTED_VERSION": "客户端版本尚未验证，已停止读取。",
    "MULTIPLE_CLIENTS": "检测到多个同花顺实例，请只保留一个已安装的客户端。",
    "CLIENT_LOCATION_REQUIRED": "请关闭安装镜像等位置的同花顺实例，从应用程序目录重新打开。",
    "READ_FAILED": "客户端页面不可读；旧快照不再代表当前账户状态。",
    "TRADE_PAGE_REQUIRED": "未识别到平安证券账户，请在同花顺中登录并打开交易持仓页。",
    "INCOMPLETE_SNAPSHOT": "资金或持仓读取不完整，请保持交易持仓页可见后重试。",
}


class BridgeError(ValueError):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or MESSAGES.get(code, "本机连接暂不可用。"))


def validate_local_request(*, bind_host, peer_host, port, headers, mutation, token):
    try:
        local = (ipaddress.ip_address(bind_host).is_loopback
                 and ipaddress.ip_address(peer_host).is_loopback)
    except ValueError:
        local = False
    host = headers.get("Host", "")
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    origin = headers.get("Origin")
    fetch_site = headers.get("Sec-Fetch-Site")
    # 同时限制监听地址、Host 和来源，防止跨站请求及 DNS 重绑定控制本机客户端。
    if (not local or host not in allowed_hosts
            or (origin is not None and origin != f"http://{host}")
            or fetch_site not in (None, "none", "same-origin")):
        raise BridgeError("LOCAL_ONLY", "客户端连接只接受同源本机请求。")
    if mutation:
        content_type = headers.get("Content-Type", "").split(";")[0].strip().lower()
        supplied = headers.get("X-Desktop-Token", "")
        if content_type != "application/json" or not secrets.compare_digest(supplied.encode(), token.encode()):
            raise BridgeError("FORBIDDEN", "本机操作校验失败，请刷新页面后重试。")


def _nodes(root):
    stack = [root]
    count = 0
    while stack:
        item = stack.pop()
        count += 1
        if count > 8000 or not isinstance(item, dict):
            raise BridgeError("INCOMPLETE_SNAPSHOT")
        yield item
        stack.extend(reversed(item.get("children", [])))


def _texts(root):
    result = []
    for item in _nodes(root):
        if item.get("role") in ("AXTextField", "AXSecureTextField", "AXTextArea"):
            continue
        text = item.get("text", "").strip()
        if text and (not result or text != result[-1]):
            result.append(text)
    return result


def _money(text, *, signed=False):
    # 不把空白、隐藏金额或异常值当成零；金额按十进制解析，避免浮点误差。
    text = text.strip()
    if not re.fullmatch(r"-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,4})?", text):
        raise BridgeError("INCOMPLETE_SNAPSHOT", "金额缺失或格式异常，未更新快照。")
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise BridgeError("INCOMPLETE_SNAPSHOT") from exc
    if not value.is_finite() or abs(value) > Decimal("1000000000000") or (not signed and value < 0):
        raise BridgeError("INCOMPLETE_SNAPSHOT", "金额超出可验证范围，未更新快照。")
    return value


def parse_snapshot(tree):
    all_nodes = list(_nodes(tree))
    if any(item.get("role") in ("AXSecureTextField", "AXSheet")
           or item.get("subrole") in ("AXDialog", "AXSystemDialog") for item in all_nodes):
        raise BridgeError("TRADE_PAGE_REQUIRED")
    if not any(re.fullmatch(r"平安证券(?:\s+[—–\-]+)?", text) for text in _texts(tree)):
        raise BridgeError("TRADE_PAGE_REQUIRED")
    labels = {"总资产": "total_assets", "总市值": "market_value", "资金余额": "cash_balance",
              "可用金额": "available_cash", "可取金额": "withdrawable_cash"}
    tables = [item for item in all_nodes if item.get("role") == "AXTable"]
    fund_tables = [table for table in tables if set(labels).issubset(set(_texts(table)))]
    position_tables = [table for table in tables
                       if {"证券代码", "证券名称", "实际数量", "市值"}.issubset(set(_texts(table)))]
    if len(fund_tables) != 1 or len(position_tables) != 1:
        raise BridgeError("INCOMPLETE_SNAPSHOT", "未识别唯一的资金与持仓表，请打开交易持仓页。")

    amounts = {}
    for row in _nodes(fund_tables[0]):
        if row.get("role") != "AXRow":
            continue
        texts = _texts(row)
        if texts and texts[0] in labels:
            if len(texts) != 2 or labels[texts[0]] in amounts:
                raise BridgeError("INCOMPLETE_SNAPSHOT")
            amounts[labels[texts[0]]] = _money(texts[1])
    if len(amounts) != len(labels):
        raise BridgeError("INCOMPLETE_SNAPSHOT")
    if (abs(amounts["total_assets"] - amounts["cash_balance"] - amounts["market_value"]) > Decimal("0.05")
            or amounts["available_cash"] > amounts["cash_balance"] + Decimal("0.05")
            or amounts["withdrawable_cash"] > amounts["cash_balance"] + Decimal("0.05")):
        raise BridgeError("INCOMPLETE_SNAPSHOT", "资金合计不一致，未更新快照。")

    table = position_tables[0]
    headers = [item["text"].strip() for item in _nodes(table)
               if item.get("role") == "AXButton" and item.get("text", "").strip()]
    required = ["证券代码", "证券名称", "实际数量", "可用余额", "市价", "成本价", "市值"]
    if any(headers.count(label) != 1 for label in required):
        raise BridgeError("INCOMPLETE_SNAPSHOT", "持仓表头变化，未更新快照。")
    positions = []
    seen = set()
    for row in _nodes(table):
        if row.get("role") != "AXRow":
            continue
        cells = row.get("children", [])
        values = []
        for cell in cells:
            texts = _texts(cell)
            if len(texts) != 1:
                raise BridgeError("INCOMPLETE_SNAPSHOT")
            values.append(texts[0])
        if len(values) != len(headers):
            raise BridgeError("INCOMPLETE_SNAPSHOT")
        fields = dict(zip(headers, values))
        symbol = fields["证券代码"]
        if not re.fullmatch(r"\d{6}", symbol) or symbol in seen:
            raise BridgeError("INCOMPLETE_SNAPSHOT")
        seen.add(symbol)
        quantity = _money(fields["实际数量"])
        available = _money(fields["可用余额"])
        if quantity != quantity.to_integral_value() or available != available.to_integral_value():
            raise BridgeError("INCOMPLETE_SNAPSHOT")
        if quantity <= 0 or available > quantity:
            raise BridgeError("INCOMPLETE_SNAPSHOT")
        name = fields["证券名称"]
        if not name or len(name) > 80:
            raise BridgeError("INCOMPLETE_SNAPSHOT")
        positions.append({
            "symbol": symbol, "name": name, "quantity": int(quantity), "available": int(available),
            "price": str(_money(fields["市价"]).quantize(Decimal("0.0001"))),
            "cost": str(_money(fields["成本价"], signed=True).quantize(Decimal("0.0001"))),
            "market_value": str(_money(fields["市值"]).quantize(Decimal("0.01"))),
        })
    # 用账户总市值验证持仓表完整性，防止只读到可见行或加载中的空表。
    market_sum = sum((Decimal(item["market_value"]) for item in positions), Decimal(0))
    if ((not positions and amounts["market_value"] != 0)
            or abs(market_sum - amounts["market_value"]) > Decimal("0.05")):
        raise BridgeError("INCOMPLETE_SNAPSHOT", "持仓市值与账户市值不一致，未更新快照。")
    return {
        "broker": "平安证券", "source": "tonghuashun_macos_accessibility",
        "account_id_verified": False,
        "funds": {key: str(value.quantize(Decimal("0.01"))) for key, value in amounts.items()},
        "positions": positions,
    }


class TonghuashunBridge:
    def __init__(self, client=None, clock=None):
        if client is None:
            from desktop.macos import MacClient
            client = MacClient()
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.snapshot = None
        self.last_error = None
        self.last_attempt = None

    def _state(self):
        try:
            return self.client.inspect()
        except Exception:
            return {"code": "READ_FAILED", "installed": False, "running": False,
                    "accessibility": False, "version": None}

    def _response(self, state):
        snapshot = copy.deepcopy(self.snapshot)
        age = None
        if snapshot:
            age = max(0, (self.clock() - datetime.fromisoformat(snapshot["captured_at"])).total_seconds())
        return {
            "client": {**state, "message": MESSAGES.get(state["code"], MESSAGES["READ_FAILED"])},
            "snapshot": snapshot,
            "stale": snapshot is None or age > 60 or self.last_error is not None or state["code"] != "READY_TO_READ",
            "expires_after_seconds": 60, "last_attempt": self.last_attempt,
            "last_error": copy.deepcopy(self.last_error),
            "capabilities": {"launch": bool(state.get("installed")) and state["code"] not in (
                                "MULTIPLE_CLIENTS", "CLIENT_LOCATION_REQUIRED"),
                             "read_account": state["code"] == "READY_TO_READ",
                             "live_trading": False, "oauth_callback": False,
                             "watchlist_sync": False, "market_feed": False},
            "request_token": self.token,
        }

    def status(self):
        with self.lock:
            return self._response(self._state())

    def perform(self, action):
        if action not in {"launch", "sync", "permission", "clear"}:
            raise BridgeError("ACTION_UNAVAILABLE", "只读连接不支持下单、撤单、回调或其他客户端操作。")
        if not self.lock.acquire(blocking=False):
            raise BridgeError("BUSY", "客户端操作进行中，请稍后重试。")
        try:
            if action == "clear":
                self.snapshot = self.last_error = self.last_attempt = None
            elif action == "launch":
                self.client.launch()
            elif action == "permission":
                self.client.request_permission()
            else:
                self.last_attempt = self.clock().isoformat()
                state = self._state()
                if state["code"] != "READY_TO_READ":
                    raise BridgeError(state["code"])
                snapshot = parse_snapshot(self.client.read())
                snapshot["captured_at"] = self.clock().isoformat()
                snapshot["client_version"] = state["version"]
                self.snapshot = snapshot
                self.last_error = None
            return self._response(self._state())
        except Exception as exc:
            error = exc if isinstance(exc, BridgeError) else BridgeError("READ_FAILED")
            self.last_error = {"code": error.code, "message": str(error)}
            raise error from None
        finally:
            self.lock.release()
