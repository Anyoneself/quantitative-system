from datetime import timedelta
from decimal import Decimal
import re


class TradeInputError(ValueError):
    pass


def validate_trade_input(payload):
    if not isinstance(payload, dict) or set(payload) != {"side", "symbol", "price", "quantity"}:
        raise TradeInputError("委托仅接受方向、股票代码、限价和股数，不接受登录凭据。")
    side, symbol, price, quantity = (payload[key] for key in ("side", "symbol", "price", "quantity"))
    if not isinstance(side, str) or side not in ("BUY", "SELL"):
        raise TradeInputError("请选择买入或卖出。")
    if not isinstance(symbol, str) or not re.fullmatch(r"(?:600|601|603|605|000|001|002|003)[0-9]{3}", symbol):
        raise TradeInputError("当前委托核对仅支持沪深主板 A 股的6位股票代码。")
    if (type(price) not in (str, int, float)
            or not re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,2})?", str(price)) or Decimal(str(price)) <= 0):
        raise TradeInputError("限价须大于0，最多两位小数，且不超过99999.99元。")
    if type(quantity) is not int or not 0 < quantity <= 1_000_000:
        raise TradeInputError("委托股数须为1至1000000之间的整数。")
    if side == "BUY" and quantity % 100:
        raise TradeInputError("主板股票买入股数须为100的整数倍。")
    return {"side": side, "symbol": symbol, "price": str(Decimal(str(price)).quantize(Decimal(".01"))),
            "quantity": quantity}


def build_trade_draft(ticket, snapshot, now):
    amount = (Decimal(ticket["price"]) * ticket["quantity"]).quantize(Decimal(".01"))
    cash = Decimal(snapshot["funds"]["available_cash"])
    position = next((item for item in snapshot["positions"] if item["symbol"] == ticket["symbol"]), None)
    available = position["available"] if position else 0
    # 仅为核对时的资金余量，不代表该账户的真实佣金或券商可买数量。
    reserve = max(Decimal("5"), amount * Decimal(".001")).quantize(Decimal(".01"))
    if ticket["side"] == "BUY" and amount + reserve > cash:
        raise TradeInputError("可用资金不足，或未留出手续费余量；请降低金额后重新核对。")
    if ticket["side"] == "SELL":
        if ticket["quantity"] > available:
            raise TradeInputError(f"委托超过客户端显示的可卖数量（{available}股）。")
        if ticket["quantity"] % 100 and ticket["quantity"] != available:
            raise TradeInputError("非整手卖出仅支持一次卖出全部可卖余额，请核对客户端规则。")
    return {**ticket, "name": position["name"] if position else "待客户端核对名称",
            "status": "NOT_SUBMITTED", "submitted": False, "amount": str(amount),
            "fee_reserve": str(reserve) if ticket["side"] == "BUY" else None,
            "available_cash": str(cash.quantize(Decimal(".01"))), "available_quantity": available,
            "snapshot_id": snapshot["snapshot_id"], "account_id_verified": False,
            "checked_at": now.isoformat(), "expires_at": (now + timedelta(seconds=60)).isoformat()}
