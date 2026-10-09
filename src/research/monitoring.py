from __future__ import annotations

from collections import Counter
from math import sqrt
from statistics import mean, stdev

from research.audit import reconcile_result
from research.config import parse_config
from research.dataset import parse_dataset
from research.engine import return_metrics
from research.experiment import block_bootstrap_interval


def build_monitoring(state: dict) -> dict:
    dataset = parse_dataset(state["dataset"])
    strategy, simulation, _ = parse_config(state["config"])
    result = state["result"]
    reconciliation = reconcile_result(dataset, result, simulation)
    curve = result["equity_curve"]
    initial = simulation.initial_cash
    anchor, last_date = state["start_after"], state["last_date"]
    prices = {bar.trade_date.isoformat(): bar.close for bar in dataset.bars[dataset.benchmark]}
    if (anchor not in prices or (curve and (curve[0]["date"] <= anchor or curve[-1]["date"] != last_date))
            or (not curve and last_date != anchor)):
        raise ValueError("前向体检日期与冻结账本不一致。")
    expected_dates = [day for day in prices if anchor < day <= last_date]
    if [point["date"] for point in curve] != expected_dates:
        raise ValueError("前向体检缺少完整的基准日期对齐。")
    comparison = [{"date": anchor, "strategy": 1.0, "benchmark": 1.0}]
    returns, benchmark_returns, active_returns = [], [], []
    previous = prices[anchor]
    for point in curve:
        benchmark_return = prices[point["date"]] / previous - 1
        returns.append(point["return"])
        benchmark_returns.append(benchmark_return)
        active_returns.append(point["return"] - benchmark_return)
        comparison.append({"date": point["date"], "strategy": point["equity"] / initial,
                           "benchmark": prices[point["date"]] / prices[anchor]})
        previous = prices[point["date"]]
    benchmark = return_metrics(benchmark_returns)
    metrics = return_metrics(returns)
    active_ci = block_bootstrap_interval(active_returns, 400)
    net_ci = block_bootstrap_interval(returns, 400)
    tracking_error = stdev(active_returns) * sqrt(252) if len(active_returns) >= 20 else None
    information_ratio = (mean(active_returns) * 252 / tracking_error
                         if tracking_error is not None and tracking_error > 1e-12 else None)
    recent_excess = (return_metrics(returns[-20:])["total_return"]
                     - return_metrics(benchmark_returns[-20:])["total_return"]) if len(returns) >= 20 else None

    equity = result["summary"]["final_equity"]
    holdings = []
    for symbol, quantity in sorted(result["terminal_positions"].items()):
        mark = next(bar for bar in reversed(dataset.bars[symbol]) if bar.trade_date.isoformat() <= last_date)
        holdings.append({
            "symbol": symbol, "quantity": quantity, "price": mark.close,
            "market_value": round(quantity * mark.close, 2), "weight": quantity * mark.close / equity,
            "mark_date": mark.trade_date.isoformat(), "stale": mark.trade_date.isoformat() != last_date,
            "target_weight": state["next_intent"]["weights"].get(symbol, 0),
        })
    market_value = curve[-1]["market_value"] if curve else 0
    gross_exposure = market_value / equity
    orders = result["orders"]
    filled = [order for order in orders if order["filled_quantity"] > 0]
    rejected = sum(order["status"] == "REJECTED" for order in orders)
    partial = sum(order["status"] == "PARTIAL" for order in orders)
    reasons = Counter(order["reason"] for order in orders if order["status"] != "FILLED")
    traded_notional, opening_shortfall = 0.0, 0.0
    volume_participation = []
    indexed = {symbol: {bar.trade_date.isoformat(): bar for bar in rows} for symbol, rows in dataset.bars.items()}
    for order in filled:
        bar = indexed[order["symbol"]][order["trade_date"]]
        quantity = order["filled_quantity"]
        traded_notional += order["price"] * quantity
        # 相对当日开盘价的模型价差，不是经券商成交校准的实际滑点。
        opening_shortfall += (order["price"] - bar.open) * quantity * (1 if order["side"] == "BUY" else -1)
        volume_participation.append(quantity / bar.volume)
    stale_days = sum(bool(point["stale_symbols"]) for point in curve)
    definitions = [
        ("observed_days", "按时前向观察日数", state["qualified_days"], 126, state["qualified_days"] >= 126),
        ("no_replay_gap", "无补算或中断", int(not state["has_replay_gap"]), 1, not state["has_replay_gap"]),
        ("reconciliation", "账本独立对账平衡", 1, 1, reconciliation["status"] == "BALANCED"),
        ("fresh_marks", "无陈旧持仓估值", stale_days, 0, stale_days == 0),
        ("no_halt", "无永久熔断", int(result["summary"]["halted"]), 0, not result["summary"]["halted"]),
        ("net_return", "成本后收益为正", metrics["total_return"], 0, metrics["total_return"] > 0 if returns else None),
        ("benchmark", "超过同期基准毛收益", metrics["total_return"] - benchmark["total_return"], 0,
         metrics["total_return"] > benchmark["total_return"] if returns else None),
        ("net_ci", "日均净收益区间下界", net_ci["low"], 0, net_ci["low"] > 0 if net_ci["low"] is not None else None),
        ("active_ci", "日均主动收益区间下界", active_ci["low"], 0,
         active_ci["low"] > 0 if active_ci["low"] is not None else None),
    ]
    checks = [{"id": key, "name": name, "value": value, "threshold": threshold, "passed": passed}
              for key, name, value, threshold, passed in definitions]
    status = ("INSUFFICIENT" if state["qualified_days"] < 126 else
              "REVIEW_REQUIRED" if all(item["passed"] is True for item in checks) else "FAILED_CHECKS")
    alerts = []
    for code, active, severity, message in (
        ("STALE_VALUATION", any(row["stale"] for row in holdings), "critical", "持仓存在陈旧估值，净值不是可变现权益。"),
        ("RISK_HALT", result["summary"]["halted"], "critical", "永久熔断已触发，仅保留模拟退出。"),
        ("GROSS_EXPOSURE", gross_exposure > strategy.gross_limit + 1e-8, "warning", "日终总仓位超过冻结上限。"),
        ("POSITION_EXPOSURE", any(row["weight"] > strategy.max_weight + 1e-8 for row in holdings),
         "warning", "日终单证券仓位超过冻结上限。"),
        ("UNDERPERFORMING", recent_excess is not None and recent_excess < 0, "warning", "最近 20 个估值日跑输基准毛收益。"),
        ("EXECUTION_REJECTIONS", rejected > 0, "warning", "观察期存在拒单，目标仓位不等于实际持仓。"),
        ("EVIDENCE_GAP", state["has_replay_gap"], "warning", "存在中断或补算，结果不能视为连续前向证据。"),
    ):
        if active:
            alerts.append({"code": code, "severity": severity, "message": message})
    return {
        "schema_version": 1, "as_of": last_date, "live_trading_enabled": False,
        "benchmark": {"symbol": dataset.benchmark, "cost_basis": "gross_price_return_no_dividends", **benchmark},
        "relative": {"excess_return": metrics["total_return"] - benchmark["total_return"],
                     "relative_wealth_return": (1 + metrics["total_return"]) / (1 + benchmark["total_return"]) - 1,
                     "tracking_error": tracking_error, "information_ratio": information_ratio,
                     "recent_20_day_excess": recent_excess, "active_return_ci95": active_ci},
        "comparison_curve": comparison,
        "portfolio": {"cash": reconciliation["cash"], "market_value": market_value, "gross_exposure": gross_exposure,
                      "current_drawdown": curve[-1]["drawdown"] if curve else 0, "positions": holdings},
        "execution": {"orders": len(orders), "filled_orders": len(filled), "rejected_orders": rejected,
                      "partial_orders": partial, "fill_rate": len(filled) / len(orders) if orders else None,
                      "fees": reconciliation["fees"], "traded_notional": round(traded_notional, 2),
                      "turnover_on_initial_cash": traded_notional / initial,
                      "modeled_opening_shortfall": round(opening_shortfall, 2),
                      "maximum_daily_volume_participation": max(volume_participation) if volume_participation else None,
                      "reasons": [{"reason": reason, "count": count} for reason, count in sorted(reasons.items())]},
        "evidence": {
            "observed_days": state["qualified_days"], "minimum_days": 126, "status": status,
            "no_replay_gap": not state["has_replay_gap"], "positive_net_return": metrics["total_return"] > 0,
            "positive_ci_lower": net_ci["low"] is not None and net_ci["low"] > 0,
            "mean_daily_return_ci95": net_ci, "checks": checks,
        },
        "alerts": alerts,
        "limitations": [
            "基准采用同日期收盘价格毛收益，不含分红或交易成本，不是可成交的账户。",
            "超额收益为组合与基准累计收益之差；主动日收益为同日收益差。年化采用 252 日。",
            "主动风险及信息比率至少需要 20 日；零方差不显示无限比率。",
            "置信区间描述历史抽样不确定性，不是未来盈利概率；全部检查通过也仅进入人工复核。",
            "成交比例按有成交订单数计算，含部分成交；价差为开盘价参考的模型成本，不代表实际成交质量。",
        ],
    }
