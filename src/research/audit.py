from __future__ import annotations

from bisect import bisect_right
from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite

from research.config import SimulationConfig
from research.dataset import ResearchDataset


def audit_dataset(dataset: ResearchDataset) -> dict:
    benchmark = [bar.trade_date.isoformat() for bar in dataset.bars[dataset.benchmark]]
    declared = dataset.calendar
    expected = declared["sessions"] if declared else benchmark
    expected = [day for day in expected if benchmark[0] <= day <= benchmark[-1]]
    missing_benchmark = sorted(set(expected) - set(benchmark))
    unexpected_benchmark = sorted(set(benchmark) - set(expected)) if declared else []
    missing, jumps, coverage = [], [], []
    for symbol, rows in sorted(dataset.bars.items()):
        for previous, current in zip(rows, rows[1:]):
            change = current.open / previous.close - 1
            # 25% 仅为人工复核阈值，不是交易所涨跌停规则，也不据此虚构分红或拆股。
            if abs(change) > .25:
                jumps.append({"symbol": symbol, "date": current.trade_date.isoformat(), "open_gap": change})
        if symbol == dataset.benchmark:
            continue
        available = {row.trade_date.isoformat() for row in rows}
        member_dates = [day for day in expected if symbol in dataset.members_on(date.fromisoformat(day))]
        absent = [day for day in member_dates if day not in available]
        if absent:
            missing.append({"symbol": symbol, "count": len(absent), "dates": absent[:20]})
        coverage.append({"symbol": symbol, "bars": len(rows), "expected_member_days": len(member_dates),
                         "missing_days": len(absent), "first_date": rows[0].trade_date.isoformat(),
                         "last_date": rows[-1].trade_date.isoformat()})
    definitions = [
        ("calendar_source", "交易日历来源声明", declared is not None, "声明不等于交易所认证"),
        ("benchmark_calendar", "基准与声明日历一致", declared is not None and not missing_benchmark and not unexpected_benchmark,
         "无日历声明时无法发现全市场共同缺日"),
        ("member_coverage", "成员行情覆盖完整", not missing, "缺失行情不能自动视为停牌"),
        ("price_continuity", "无待核查价格跳变", not jumps, "异常跳变需核对公司行动或原始行情"),
        ("unadjusted", "采用未复权成交口径", dataset.adjustment == "none", "不使用复权价模拟现金成交"),
        ("corporate_actions", "公司行动及退市清算已核验", False, "尚未接入独立核验的完整事件库"),
        ("provenance_verified", "数据来源已独立核验", False, "当前仅记录提供方声明"),
    ]
    checks = [{"id": key, "name": name, "passed": passed, "detail": detail} for key, name, passed, detail in definitions]
    blocked = bool(missing_benchmark or unexpected_benchmark or jumps or dataset.adjustment != "none")
    return {"status": "BLOCKED" if blocked else "REVIEW_REQUIRED",
            "data_source": dataset.source, "synthetic": dataset.synthetic,
            "calendar_status": "DECLARED_NOT_VERIFIED" if declared else "UNVERIFIED",
            "calendar_source": declared["source"] if declared else None,
            "checks": checks, "missing_benchmark_dates": missing_benchmark,
            "unexpected_benchmark_dates": unexpected_benchmark, "missing_member_bars": missing,
            "price_jumps": jumps[:100], "price_jump_count": len(jumps), "coverage": coverage}


def _money(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("对账失败：金额不是有限数。")
    return Decimal(str(value))


def _equal(actual, expected, label, tolerance=Decimal("0.02")) -> None:
    if abs(_money(actual) - Decimal(expected)) > tolerance:
        raise ValueError(f"对账失败：{label} 不一致。")


def reconcile_result(dataset: ResearchDataset, result: dict, config: SimulationConfig) -> dict:
    """从成交而不是引擎持仓状态重建账本，检查已实现与未实现盈亏。"""
    try:
        return _reconcile(dataset, result, config)
    except (KeyError, TypeError, IndexError, InvalidOperation, OverflowError) as exc:
        raise ValueError("对账失败：账本结构或数值无效。") from exc


def _reconcile(dataset, result, config):
    cent = Decimal("0.01")
    cash = _money(config.initial_cash).quantize(cent)
    quantities, costs, realized, fees_by_symbol, acquired = {}, {}, {}, {}, {}
    orders_by_date, seen = {}, set()
    curve = result["equity_curve"]
    dates = [point["date"] for point in curve]
    benchmark = [bar.trade_date.isoformat() for bar in dataset.bars[dataset.benchmark]]
    if dates and dates != [day for day in benchmark if dates[0] <= day <= dates[-1]]:
        raise ValueError("对账失败：净值日期缺失、重复或不连续。")
    previous_order_day = ""
    for order in result["orders"]:
        day, symbol, side = order["trade_date"], order["symbol"], order["side"]
        order_id = order.get("order_id", f"{day}:{symbol}:{side}")
        if (order_id != f"{day}:{symbol}:{side}" or order_id in seen or day not in dates
                or day < previous_order_day or symbol not in dataset.bars
                or symbol == dataset.benchmark or side not in {"BUY", "SELL"} or not order["signal_date"] < day):
            raise ValueError("对账失败：订单重复、日期或证券无效。")
        seen.add(order_id)
        previous_order_day = day
        orders_by_date.setdefault(day, []).append(order)
    indexed_dates = {symbol: [bar.trade_date.isoformat() for bar in rows] for symbol, rows in dataset.bars.items()}
    prior_equity = peak = _money(config.initial_cash)
    total_fees, max_drawdown, filled_count = Decimal(0), 0.0, 0
    marks = {}
    for point in curve:
        day = point["date"]
        for order in orders_by_date.get(day, []):
            symbol, side = order["symbol"], order["side"]
            quantity, requested = order["filled_quantity"], order["requested_quantity"]
            if (type(quantity) is not int or type(requested) is not int
                    or not 0 <= quantity <= requested or requested <= 0 or quantity % 100):
                raise ValueError("对账失败：成交数量无效。")
            expected_status = "REJECTED" if quantity == 0 else "FILLED" if quantity == requested else "PARTIAL"
            if order["status"] != expected_status:
                raise ValueError("对账失败：订单状态与数量不一致。")
            fee, price = _money(order["fee"]), _money(order["price"])
            if quantity == 0:
                if fee != 0 or order["realized_pnl"] is not None:
                    raise ValueError("对账失败：拒单不得收取费用或实现盈亏。")
                _equal(order["cash_after"], cash, "拒单现金")
                continue
            position = quantities.get(symbol, 0)
            if price <= 0 or fee < 0:
                raise ValueError("对账失败：成交价格或费用无效。")
            row_index = bisect_right(indexed_dates[symbol], day) - 1
            if row_index < 0 or indexed_dates[symbol][row_index] != day:
                raise ValueError("对账失败：成交日缺少行情。")
            bar = dataset.bars[symbol][row_index]
            if not _money(bar.low) <= price <= _money(bar.high) or bar.volume <= 0:
                raise ValueError("对账失败：成交不在有效行情范围内。")
            amount = (price * quantity).quantize(cent)
            expected_fee = max(_money(config.min_commission), amount * _money(config.commission_rate))
            if side == "SELL":
                expected_fee += amount * _money(config.stamp_duty_rate)
            _equal(float(fee), expected_fee.quantize(cent), "手续费")
            if side == "BUY":
                if order["realized_pnl"] is not None:
                    raise ValueError("对账失败：买入不应实现卖出盈亏。")
                cash -= amount + fee
                quantities[symbol] = position + quantity
                costs[symbol] = costs.get(symbol, Decimal(0)) + amount + fee
                acquired[symbol] = day
            else:
                if quantity > position or acquired.get(symbol, day) >= day:
                    raise ValueError("对账失败：超卖或违反 T+1。")
                removed_cost = costs[symbol] * Decimal(quantity) / Decimal(position)
                pnl = amount - fee - removed_cost
                _equal(order["realized_pnl"], pnl, "已实现盈亏")
                realized[symbol] = realized.get(symbol, Decimal(0)) + pnl
                costs[symbol] -= removed_cost
                quantities[symbol] -= quantity
                cash += amount - fee
                if not quantities[symbol]:
                    del quantities[symbol], costs[symbol], acquired[symbol]
            cash = cash.quantize(cent)
            if cash < 0:
                raise ValueError("对账失败：现金透支。")
            _equal(order["cash_after"], cash, "成交后现金")
            total_fees += fee
            fees_by_symbol[symbol] = fees_by_symbol.get(symbol, Decimal(0)) + fee
            filled_count += 1
        market_value, stale = Decimal(0), []
        for symbol, quantity in quantities.items():
            index = bisect_right(indexed_dates[symbol], day) - 1
            if index < 0:
                raise ValueError("对账失败：缺少历史估值。")
            mark = dataset.bars[symbol][index]
            marks[symbol] = _money(mark.close)
            market_value += marks[symbol] * quantity
            if mark.trade_date.isoformat() != day:
                stale.append(symbol)
        equity = (cash + market_value).quantize(cent)
        if equity <= 0 or point["positions"] != quantities or sorted(point["stale_symbols"]) != sorted(stale):
            raise ValueError("对账失败：持仓、陈旧估值或权益无效。")
        _equal(point["cash"], cash, "日终现金")
        _equal(point["market_value"], market_value, "日终市值")
        _equal(point["equity"], equity, "日终净值")
        _equal(point["return"], equity / prior_equity - 1, "日收益", Decimal("0.00000001"))
        peak = max(peak, equity)
        drawdown = 1 - equity / peak
        _equal(point["drawdown"], drawdown, "回撤", Decimal("0.00000001"))
        max_drawdown = max(max_drawdown, float(drawdown))
        prior_equity = equity
    summary = result["summary"]
    if result["terminal_positions"] != quantities or summary["trade_count"] != filled_count or summary["days"] != len(curve):
        raise ValueError("对账失败：期末持仓或汇总数量不一致。")
    _equal(summary["final_equity"], prior_equity, "期末净值")
    _equal(summary["initial_cash"], config.initial_cash, "初始资金")
    _equal(summary["fees"], total_fees, "费用汇总")
    _equal(summary["total_return"], prior_equity / _money(config.initial_cash) - 1, "总收益", Decimal("0.00000001"))
    _equal(summary["max_drawdown"], max_drawdown, "最大回撤", Decimal("0.00000001"))
    attribution = []
    for symbol in sorted(set(fees_by_symbol) | set(quantities)):
        unrealized = marks.get(symbol, Decimal(0)) * quantities.get(symbol, 0) - costs.get(symbol, Decimal(0))
        net = realized.get(symbol, Decimal(0)) + unrealized
        attribution.append({"symbol": symbol, "realized_pnl": float(realized.get(symbol, Decimal(0)).quantize(cent)),
                            "unrealized_pnl": float(unrealized.quantize(cent)), "net_pnl": float(net.quantize(cent)),
                            "fees": float(fees_by_symbol.get(symbol, Decimal(0)))})
    _equal(sum(item["net_pnl"] for item in attribution), prior_equity - _money(config.initial_cash),
           "逐证券盈亏合计", Decimal("0.01") * max(2, len(attribution)))
    return {"status": "BALANCED", "days_checked": len(curve), "orders_checked": len(result["orders"]),
            "cash": float(cash), "fees": float(total_fees), "attribution": attribution,
            "net_pnl": float((prior_equity - _money(config.initial_cash)).quantize(cent)),
            "scope": "独立重建本地模拟账本；不代表券商对账或成交可实现性认证。"}
