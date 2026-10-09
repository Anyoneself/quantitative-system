from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import re

from desktop.trading import TradeInputError, validate_trade_input
from research.session_store import atomic_write, locked


def validate_live_plan(config):
    fields = {"capital_limit", "max_order_amount", "max_position_percent", "daily_loss_limit",
              "symbols", "buy_rule", "sell_rule", "allow_existing_positions"}
    if not isinstance(config, dict) or set(config) != fields:
        raise TradeInputError("实盘草案字段不完整或包含额外参数，不接受登录凭据和就绪标志。")
    result = deepcopy(config)
    labels = {"capital_limit": "投入上限", "max_order_amount": "单笔买入上限",
              "max_position_percent": "单股仓位上限", "daily_loss_limit": "单日亏损停机线"}
    for key, label in labels.items():
        value = config[key]
        if (type(value) not in (str, int, float)
                or not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,2})?", str(value))
                or Decimal(str(value)) <= 0):
            raise TradeInputError(f"{label}须明确填写大于0的数值，最多两位小数。")
        result[key] = str(Decimal(str(value)).quantize(Decimal(".01")))
    capital = Decimal(result["capital_limit"])
    weight = Decimal(result["max_position_percent"])
    if weight > 100:
        raise TradeInputError("单股仓位上限不能超过100%。")
    if Decimal(result["max_order_amount"]) > capital * weight / 100:
        raise TradeInputError("单笔买入上限不能超过投入上限乘以单股仓位上限。")
    if Decimal(result["daily_loss_limit"]) > capital:
        raise TradeInputError("单日亏损停机线不能高于投入上限。")
    symbols = config["symbols"]
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 30:
        raise TradeInputError("请明确1至30只沪深主板股票作为交易白名单。")
    for symbol in symbols:
        validate_trade_input({"side": "BUY", "symbol": symbol, "price": "1", "quantity": 100})
    if len(set(symbols)) != len(symbols):
        raise TradeInputError("交易白名单不能包含重复代码。")
    for key in ("buy_rule", "sell_rule"):
        rule = config[key]
        if not isinstance(rule, str) or not 1 <= len(rule.strip()) <= 500:
            raise TradeInputError("买入与卖出规则均须明确填写，分别不超过500字。")
        result[key] = rule.strip()
    if type(config["allow_existing_positions"]) is not bool:
        raise TradeInputError("请明确是否允许自动卖出现有持仓。")
    return result


class LiveAutomationPlan:
    """仅保存实盘规则草案并报告接入缺口，不包含订单执行器。"""

    def __init__(self, clock, path=None):
        self.clock = clock
        self.path = Path(path) if path is not None else None
        self.record = {"schema_version": 1, "revision": 0, "config": None, "saved_at": None}

    def _read(self):
        if self.path is None or not self.path.exists() and not self.path.is_symlink():
            return deepcopy(self.record)
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 20000:
                raise ValueError()
            record = json.loads(self.path.read_text(encoding="utf-8"))
            if (set(record) != {"schema_version", "revision", "config", "saved_at"}
                    or type(record["schema_version"]) is not int or record["schema_version"] != 1
                    or type(record["revision"]) is not int or record["revision"] < 1
                    or datetime.fromisoformat(record["saved_at"]).tzinfo is None
                    or validate_live_plan(record["config"]) != record["config"]):
                raise ValueError()
            return record
        except (OSError, ValueError, TypeError, KeyError):
            raise TradeInputError("实盘草案存档无法校验，已禁止覆盖与启动，请检查本地文件。") from None

    def save(self, payload):
        from desktop.tonghuashun import BridgeError
        if (not isinstance(payload, dict) or set(payload) != {"config", "expected_revision"}
                or type(payload["expected_revision"]) is not int or payload["expected_revision"] < 0):
            raise TradeInputError("保存草案需要配置和明确的预期版本。")
        config = validate_live_plan(payload["config"])
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with locked(self.path.parent, ".live-plan.lock") if self.path is not None else nullcontext():
            record = self._read()
            if payload["expected_revision"] != record["revision"]:
                raise BridgeError("CONFIG_CONFLICT", "实盘草案已被其他页面更新，请重新载入后再保存。")
            record = {"schema_version": 1, "revision": record["revision"] + 1,
                      "config": config, "saved_at": self.clock().isoformat()}
            if self.path is not None:
                try:
                    atomic_write(self.path, record)
                except OSError:
                    raise TradeInputError("实盘草案保存失败，请重新检查状态，未启动交易。") from None
            else:
                self.record = record

    def status(self, *, client_ready, snapshot_fresh):
        error = None
        try:
            record = self._read()
        except TradeInputError as exc:
            error = str(exc)
            record = {"revision": None, "config": None, "saved_at": None}
        checks = [
            ("configuration", "规则与限额草案", bool(record["config"]),
             error or ("已保存，仅为草案，尚未执行。" if record["config"] else "尚未明确风险限额、白名单及买卖规则。")),
            ("snapshot", "本机账户只读连接", client_ready and snapshot_fresh,
             "读取成功不代表具备交易权限。" if snapshot_fresh else "请先在买入卖出页同步账户，快照须在60秒内。"),
            ("identity", "交易账户与权限", False, "账户唯一标识和程序化交易权限未核验。"),
            ("orders", "下单、撤单与成交回报", False, "同花顺适配器为只读，无法确认委托编号、部分成交或撤单结果。"),
            ("market", "实时行情与交易日历", False, "目前为日线分析及界面快照，未接入已验证的实时未复权报价和交易日历。"),
            ("execution", "策略执行与订单风控", False, "买卖规则尚未转为可执行策略；幂等下单、额度占用与异常对账未实现。"),
        ]
        # 就绪状态只能由已验证的执行通道给出，不能由配置、登录或浏览器字段解锁。
        return {"mode": "LIVE", "status": "BLOCKED", "running": False, "can_start": False,
                "revision": record["revision"], "config": record["config"], "saved_at": record["saved_at"],
                "storage_error": error, "checked_at": self.clock().isoformat(),
                "checks": [{"id": key, "label": label, "passed": passed, "detail": detail}
                           for key, label, passed, detail in checks]}
