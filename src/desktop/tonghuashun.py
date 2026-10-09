from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import ipaddress
import re
import secrets
import threading

from portfolio.holdings_risk import BENCHMARK, MAX_HISTORY_HOLDINGS, analyze_holdings
from analysis.sell_advisor import build_snapshot_sell_advice


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
    "WATCHLIST_PAGE_REQUIRED": "请在同花顺打开自选股的“分组设置”，选中要同步的分组后重试。",
    "INCOMPLETE_WATCHLIST": "自选分组、成员或总数无法核对，未更新自选股快照。",
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
    tolerance = Decimal("0.05")
    # 仅核对客户端显示关系，不用可用金额覆盖资金余额或推断券商账本。
    cash_basis = next((key for key in ("cash_balance", "available_cash")
                       if abs(amounts["total_assets"] - amounts["market_value"] - amounts[key]) <= tolerance), None)
    if (cash_basis is None
            or amounts["available_cash"] > amounts["cash_balance"] + tolerance
            or amounts["withdrawable_cash"] > amounts["cash_balance"] + tolerance):
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
        "funds_reconciliation": {"scope": "client_display", "cash_basis": cash_basis},
        "positions": positions,
    }


def parse_watchlist(tree):
    all_nodes = list(_nodes(tree))
    if any(item.get("role") == "AXSecureTextField" for item in all_nodes):
        raise BridgeError("WATCHLIST_PAGE_REQUIRED")
    sheets = [item for item in all_nodes if item.get("role") == "AXSheet"]
    if len(sheets) != 1:
        raise BridgeError("WATCHLIST_PAGE_REQUIRED")
    sheet = sheets[0]
    text = " ".join(_texts(sheet))
    groups_count = re.findall(r"自选分组设置[（(]共(\d+)组[）)]", text)
    stocks_count = re.findall(r"所选分组成分股设置[（(]共(\d+)只[）)]", text)
    if len(groups_count) != 1 or len(stocks_count) != 1:
        raise BridgeError("WATCHLIST_PAGE_REQUIRED")
    if any(item.get("role") in ("AXTextField", "AXTextArea") for item in _nodes(sheet)):
        raise BridgeError("INCOMPLETE_WATCHLIST")
    group_count, declared_count = int(groups_count[0]), int(stocks_count[0])
    if not 1 <= group_count <= 200 or not 0 <= declared_count <= 2000:
        raise BridgeError("INCOMPLETE_WATCHLIST")
    tables = [item for item in _nodes(sheet) if item.get("role") == "AXTable"]
    if len(tables) != 2:
        raise BridgeError("INCOMPLETE_WATCHLIST")
    # 通过已选分组及成员总数校验，不从自绘行情表的空值或价格描述中猜股票代码。
    group_tables = []
    for table in tables:
        rows = [item for item in _nodes(table) if item.get("role") == "AXRow"]
        names = [" ".join(_texts(row)) for row in rows]
        if (len(rows) != group_count or len(set(names)) != len(names)
                or any(not name or len(name) > 80 or re.match(r"\d{6}\s", name) for name in names)):
            continue
        selected = [name for row, name in zip(rows, names)
                    if any(item.get("selected") is True for item in _nodes(row))]
        if len(selected) == 1:
            group_tables.append((table, selected[0]))
    if len(group_tables) != 1:
        raise BridgeError("INCOMPLETE_WATCHLIST")
    group_table, group_name = group_tables[0]
    stock_table = next(table for table in tables if table is not group_table)
    stocks = []
    for row in _nodes(stock_table):
        if row.get("role") != "AXRow":
            continue
        match = re.fullmatch(r"([0-9]{6})\s+(\S[^\r\n]{0,79})", " ".join(_texts(row)))
        if not match:
            raise BridgeError("INCOMPLETE_WATCHLIST")
        stocks.append({"symbol": match[1], "name": match[2]})
    if len(stocks) != declared_count or len({item["symbol"] for item in stocks}) != len(stocks):
        raise BridgeError("INCOMPLETE_WATCHLIST")
    return {
        "source": "tonghuashun_macos_accessibility", "scope": "selected_group",
        "account_id_verified": False, "group_name": group_name,
        "declared_count": declared_count, "stocks": stocks,
    }


class TonghuashunBridge:
    def __init__(self, client=None, clock=None, history_loader=None, automation_path=None):
        if client is None:
            from desktop.macos import MacClient
            client = MacClient()
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        from desktop.automation import LiveAutomationPlan
        self.automation = LiveAutomationPlan(self.clock, automation_path)
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.snapshot = None
        self.last_error = None
        self.last_attempt = None
        self.watchlist = None
        self.watchlist_last_error = None
        self.watchlist_last_attempt = None
        if history_loader is None:
            from data.source import fetch_bars
            history_loader = fetch_bars
        self.history_loader = history_loader
        self.history_cache = {}
        self.public_history_enabled = False
        self.risk = None
        self.holding_advice = None
        self.risk_pending = None
        self.risk_running = False
        self.risk_thread = None

    def _schedule_risk(self, snapshot):
        self.risk = analyze_holdings(snapshot, {} if self.public_history_enabled else None, now=self.clock())
        self.holding_advice = build_snapshot_sell_advice(
            snapshot, {} if self.public_history_enabled else None, now=self.clock())
        self.risk_pending = None
        if (not self.public_history_enabled or not snapshot["positions"]
                or len(snapshot["positions"]) > MAX_HISTORY_HOLDINGS):
            return
        self.risk.update(status="ANALYZING", sample_status="正在获取公开历史行情")
        self.risk_pending = copy.deepcopy(snapshot)
        if not self.risk_running:
            self.risk_running = True
            self.risk_thread = threading.Thread(target=self._analyze_pending_risk, daemon=True)
            self.risk_thread.start()

    def _analyze_pending_risk(self):
        # 单个后台读取者处理最新快照；清空或换仓后，旧任务不能回写新账户结果。
        while True:
            with self.lock:
                snapshot = self.risk_pending
                self.risk_pending = None
                if snapshot is None:
                    self.risk_running = False
                    return
            histories = {}
            symbols = [BENCHMARK, *sorted({item["symbol"] for item in snapshot["positions"]
                                         if Decimal(item["market_value"]) > 0})]
            for symbol in symbols:
                with self.lock:
                    if (not self.public_history_enabled or self.snapshot is None
                            or self.snapshot["snapshot_id"] != snapshot["snapshot_id"]):
                        break
                now = self.clock()
                cached = self.history_cache.get(symbol)
                if cached and 0 <= (now - cached[0]).total_seconds() < (900 if cached[1] else 60):
                    histories[symbol] = cached[1]
                else:
                    try:
                        bars = self.history_loader(symbol)
                    except Exception:
                        bars = []
                    if len(self.history_cache) >= 64:
                        self.history_cache.pop(next(iter(self.history_cache)))
                    self.history_cache[symbol] = (now, bars)
                    histories[symbol] = bars
                suggestions = build_snapshot_sell_advice(snapshot, histories, now=self.clock())
                with self.lock:
                    if (self.public_history_enabled and self.snapshot is not None
                            and self.snapshot["snapshot_id"] == snapshot["snapshot_id"]):
                        self.holding_advice = suggestions
            try:
                report = analyze_holdings(snapshot, histories, now=self.clock())
                suggestions = build_snapshot_sell_advice(snapshot, histories, now=self.clock())
            except Exception:
                report = analyze_holdings(snapshot, {}, now=self.clock())
                report["warnings"].append("历史风险分析失败，账户快照未受影响。")
                suggestions = build_snapshot_sell_advice(snapshot, now=self.clock())
            with self.lock:
                if (self.public_history_enabled and self.snapshot is not None
                        and self.snapshot["snapshot_id"] == snapshot["snapshot_id"]):
                    self.risk = report
                    self.holding_advice = suggestions

    def _state(self):
        try:
            return self.client.inspect()
        except Exception:
            return {"code": "READ_FAILED", "installed": False, "running": False,
                    "accessibility": False, "version": None}

    def _response(self, state):
        snapshot = copy.deepcopy(self.snapshot)
        watchlist = copy.deepcopy(self.watchlist)
        age = None
        if snapshot:
            age = max(0, (self.clock() - datetime.fromisoformat(snapshot["captured_at"])).total_seconds())
        watchlist_age = None
        if watchlist:
            watchlist_age = max(0, (self.clock() - datetime.fromisoformat(watchlist["captured_at"])).total_seconds())
        stale = snapshot is None or age >= 60 or self.last_error is not None or state["code"] != "READY_TO_READ"
        risk = copy.deepcopy(self.risk)
        if risk:
            risk["stale"] = stale
        suggestions = copy.deepcopy(self.holding_advice)
        if suggestions:
            from data.industry import INDUSTRIES
            for item in suggestions["items"]:
                item.update(INDUSTRIES.lookup(item["symbol"]))
            suggestions["stale"] = stale
        return {
            "client": {**state, "message": MESSAGES.get(state["code"], MESSAGES["READ_FAILED"])},
            "snapshot": snapshot,
            "stale": stale, "risk": risk, "public_history_enabled": self.public_history_enabled,
            "holding_advice": suggestions,
            "expires_after_seconds": 60, "last_attempt": self.last_attempt,
            "last_error": copy.deepcopy(self.last_error),
            "watchlist": watchlist,
            "watchlist_stale": (watchlist is None or watchlist_age >= 60
                               or self.watchlist_last_error is not None or state["code"] != "READY_TO_READ"),
            "watchlist_last_attempt": self.watchlist_last_attempt,
            "watchlist_last_error": copy.deepcopy(self.watchlist_last_error),
            "capabilities": {"launch": bool(state.get("installed")) and state["code"] not in (
                                "MULTIPLE_CLIENTS", "CLIENT_LOCATION_REQUIRED"),
                             "read_account": state["code"] == "READY_TO_READ",
                             "live_trading": False, "oauth_callback": False,
                             "watchlist_sync": state["code"] == "READY_TO_READ", "market_feed": False},
            "request_token": self.token,
            "automation": self.automation.status(client_ready=state["code"] == "READY_TO_READ",
                                                  snapshot_fresh=not stale),
        }

    def status(self):
        with self.lock:
            return self._response(self._state())

    def prepare_trade(self, payload):
        from desktop.trading import build_trade_draft, validate_trade_input
        ticket = validate_trade_input(payload)
        state = self.perform("sync")
        if state["stale"]:
            raise BridgeError("READ_FAILED")
        return {"connection": state, "draft": build_trade_draft(ticket, state["snapshot"], self.clock())}

    def configure_automation(self, payload):
        if not self.lock.acquire(blocking=False):
            raise BridgeError("BUSY", "客户端操作进行中，请稍后重试。")
        try:
            self.automation.save(payload)
            return self._response(self._state())
        except RuntimeError:
            raise BridgeError("BUSY", "实盘草案正由其他进程更新，请稍后重新检查。") from None
        except OSError:
            from desktop.trading import TradeInputError
            raise TradeInputError("实盘草案存储不可用，未启动任何交易。") from None
        finally:
            self.lock.release()

    def start_automation(self, payload):
        from desktop.trading import TradeInputError
        if (not isinstance(payload, dict) or set(payload) != {"expected_revision"}
                or type(payload["expected_revision"]) is not int or payload["expected_revision"] < 0):
            raise TradeInputError("实盘启动请求只接受明确的配置版本，不能传入强制启动或就绪标志。")
        raise BridgeError("LIVE_TRADING_UNAVAILABLE", "实盘执行通道尚未接通，自动下单、撤单和成交回报未验证，禁止启动。")

    def perform(self, action):
        if action not in {"launch", "sync", "sync-watchlist", "permission", "clear",
                          "history-enable", "history-disable"}:
            raise BridgeError("ACTION_UNAVAILABLE", "只读连接不支持下单、撤单、回调或其他客户端操作。")
        if not self.lock.acquire(blocking=False):
            raise BridgeError("BUSY", "客户端操作进行中，请稍后重试。")
        try:
            if action == "clear":
                self.snapshot = self.last_error = self.last_attempt = None
                self.watchlist = self.watchlist_last_error = self.watchlist_last_attempt = None
                self.risk = self.risk_pending = None
                self.holding_advice = None
                self.public_history_enabled = False
            elif action in {"history-enable", "history-disable"}:
                self.public_history_enabled = action == "history-enable"
                self.risk_pending = None
                if self.snapshot:
                    self._schedule_risk(self.snapshot)
            elif action == "launch":
                self.client.launch()
            elif action == "permission":
                self.client.request_permission()
            else:
                watchlist_action = action == "sync-watchlist"
                if watchlist_action:
                    self.watchlist_last_attempt = self.clock().isoformat()
                else:
                    self.last_attempt = self.clock().isoformat()
                state = self._state()
                if state["code"] != "READY_TO_READ":
                    raise BridgeError(state["code"])
                snapshot = (parse_watchlist(self.client.read_watchlist()) if watchlist_action
                            else parse_snapshot(self.client.read()))
                snapshot["captured_at"] = self.clock().isoformat()
                snapshot["client_version"] = state["version"]
                if watchlist_action:
                    self.watchlist = snapshot
                    self.watchlist_last_error = None
                else:
                    snapshot["snapshot_id"] = secrets.token_hex(16)
                    self.snapshot = snapshot
                    self.last_error = None
                    self._schedule_risk(snapshot)
            return self._response(self._state())
        except Exception as exc:
            error = exc if isinstance(exc, BridgeError) else BridgeError("READ_FAILED")
            error_data = {"code": error.code, "message": str(error)}
            if action == "sync-watchlist":
                self.watchlist_last_error = error_data
            else:
                self.last_error = error_data
            raise error from None
        finally:
            self.lock.release()
