from __future__ import annotations

from datetime import date
from math import floor, sqrt
from statistics import mean, stdev

from data.models import PriceVolumeBar
from research.config import SimulationConfig, StrategyConfig, validate_config
from research.dataset import ResearchDataset
from research.execution import simulate_fill
from research.strategy import target_weights


def simulate_portfolio(
    dataset: ResearchDataset,
    strategy: StrategyConfig,
    config: SimulationConfig,
    *,
    start: date | None = None,
    end: date | None = None,
    schedule: dict[date, StrategyConfig] | None = None,
) -> dict:
    validate_config(strategy, config)
    schedule = schedule or {}
    for item in schedule.values():
        validate_config(item, config)
    calendar = [bar.trade_date for bar in dataset.bars[dataset.benchmark]]
    dates = [day for day in calendar if (start is None or day >= start) and (end is None or day <= end)]
    if len(dates) < 2 or dates[0] == calendar[0]:
        raise ValueError("模拟区间至少需两日，且首日前必须存在历史行情。")
    indexed = {symbol: {bar.trade_date: bar for bar in rows} for symbol, rows in dataset.bars.items()}
    histories = {symbol: [bar for bar in rows if bar.trade_date < dates[0]] for symbol, rows in dataset.bars.items()}
    cash = round(config.initial_cash, 2)
    positions: dict[str, int] = {}
    costs: dict[str, float] = {}
    acquired: dict[str, date] = {}
    stale_days: dict[str, int] = {}
    orders, curve, risk_events, decisions = [], [], [], []
    peak = previous_equity = config.initial_cash
    halted = False
    daily_pause = False
    weights: dict[str, float] = {}
    last_rebalance = -10_000
    current_strategy = strategy
    for offset, day in enumerate(dates):
        signal_day = calendar[calendar.index(day) - 1]
        changed = day in schedule
        if changed:
            current_strategy = schedule[day]
        for symbol in positions:
            stale_days[symbol] = 0 if day in indexed[symbol] else stale_days.get(symbol, 0) + 1
        stale = [symbol for symbol in positions if stale_days.get(symbol, 0) > config.max_stale_days]
        if stale and not halted:
            halted = True
            risk_events.append({"date": day.isoformat(), "kind": "STALE_DATA_HALT", "symbols": sorted(stale)})
        rebalance = offset - last_rebalance >= current_strategy.rebalance_days or changed
        if halted or daily_pause:
            weights = {}
            rebalance = True
        elif rebalance:
            weights = target_weights(histories, dataset.members_on(signal_day), signal_day, current_strategy)
        # 移出证券池仅能在该快照已生效且已知后触发退出，不能提前利用未来退市信息。
        members = set(dataset.members_on(signal_day))
        removed = set(positions) - members
        if removed:
            weights = {symbol: weight for symbol, weight in weights.items() if symbol in members}
            rebalance = True
        if rebalance:
            last_rebalance = offset
            targets = {}
            for symbol in set(positions) | set(weights):
                previous = histories[symbol][-1] if histories[symbol] else None
                targets[symbol] = (floor(previous_equity * weights.get(symbol, 0) / previous.close / 100) * 100
                                   if previous is not None else 0)
            decisions.append({
                "signal_date": signal_day.isoformat(), "execution_date": day.isoformat(),
                "weights": dict(sorted(weights.items())), "halted": halted, "daily_pause": daily_pause,
            })
            cash = _execute_targets(
                targets, positions, costs, acquired, cash, indexed, histories,
                signal_day, day, config, current_strategy, halted or daily_pause, orders,
            )
        market_value = 0.0
        for symbol, quantity in positions.items():
            bar = indexed[symbol].get(day)
            mark = bar or (histories[symbol][-1] if histories[symbol] else None)
            if mark is None:
                raise ValueError(f"{day} 的 {symbol} 持仓没有可用历史估值。")
            market_value += quantity * mark.close
        equity = round(cash + market_value, 2)
        if equity <= 0:
            raise ValueError("组合权益已非正，无法继续模拟。")
        daily_return = equity / previous_equity - 1
        peak = max(peak, equity)
        drawdown = 1 - equity / peak
        curve.append({
            "date": day.isoformat(), "cash": cash, "market_value": round(market_value, 2),
            "equity": equity, "return": daily_return, "drawdown": drawdown,
            "positions": dict(sorted(positions.items())), "stale_symbols": sorted(
                symbol for symbol in positions if day not in indexed[symbol]
            ),
        })
        # 回撤阈值代表整段实验的永久熔断；日亏损阈值只暂停次日并尝试降仓，不承诺止损价。
        if not halted and drawdown >= config.max_drawdown:
            halted = True
            risk_events.append({"date": day.isoformat(), "kind": "DRAWDOWN_HALT", "value": drawdown})
        daily_pause = daily_return <= -config.daily_loss_limit
        if daily_pause:
            risk_events.append({"date": day.isoformat(), "kind": "DAILY_LOSS_PAUSE", "value": daily_return})
        for symbol in histories:
            if day in indexed[symbol]:
                histories[symbol].append(indexed[symbol][day])
        previous_equity = equity
    returns = [point["return"] for point in curve]
    summary = return_metrics(returns)
    summary.update({
        "initial_cash": config.initial_cash, "final_equity": curve[-1]["equity"],
        "trade_count": sum(order["filled_quantity"] > 0 for order in orders),
        "rejected_count": sum(order["filled_quantity"] == 0 for order in orders),
        "fees": round(sum(order["fee"] for order in orders), 2),
        "turnover": sum(order["price"] * order["filled_quantity"] for order in orders) / config.initial_cash,
        "halted": halted, "open_positions": len(positions),
    })
    return {
        "summary": summary, "equity_curve": curve, "orders": orders,
        "risk_events": risk_events, "decisions": decisions,
        "terminal_positions": dict(sorted(positions.items())),
        "notes": [
            "收盘信号只读取当时历史，次日开盘尝试成交；买入按 100 股整手，仅模拟沪深主板。",
            "费用、印花税、滑点为冻结场景参数；成交量参与率不是开盘流动性保证。",
            "期末不强制平仓，权益包含未实现盈亏，未扣除尚未发生的退出费用。",
            "回撤熔断后持续尝试退出；停牌、跌停或流动性不足可能导致实际回撤超过阈值。",
        ],
    }


def _execute_targets(
    targets: dict[str, int], positions: dict[str, int], costs: dict[str, float],
    acquired: dict[str, date], cash: float, indexed: dict, histories: dict,
    signal_day: date, day: date, config: SimulationConfig, strategy: StrategyConfig,
    risk_off: bool, orders: list[dict],
) -> float:
    opening_prices = {
        symbol: (indexed[symbol][day].open if day in indexed[symbol] else histories[symbol][-1].close)
        for symbol in set(positions) | set(targets) if histories[symbol]
    }
    opening_equity = cash + sum(quantity * opening_prices[symbol] for symbol, quantity in positions.items())
    for side in ("SELL", "BUY"):
        for symbol in sorted(targets):
            held = positions.get(symbol, 0)
            delta = targets[symbol] - held
            if (side == "SELL" and delta >= 0) or (side == "BUY" and delta <= 0):
                continue
            quantity = abs(delta)
            bar = indexed[symbol].get(day)
            previous = histories[symbol][-1] if histories[symbol] else None
            rejection = None
            if side == "BUY":
                if risk_off:
                    rejection = "RISK_HALT"
                elif held == 0 and len(positions) >= strategy.max_positions:
                    rejection = "POSITION_COUNT_LIMIT"
                elif previous is None or previous.trade_date != signal_day:
                    rejection = "STALE_SIGNAL"
                elif bar is not None:
                    # 每笔买入先按开盘估值检查单股和总敞口，卖出失败不能释放虚构的额度。
                    gross = sum(amount * opening_prices[item] for item, amount in positions.items())
                    execution_price = round(bar.open * (1 + config.slippage_rate), 2)
                    headroom = min(
                        strategy.max_weight * opening_equity - held * bar.open,
                        strategy.gross_limit * opening_equity - gross,
                    )
                    quantity = min(quantity, max(0, floor(headroom / max(execution_price, 0.01) / 100) * 100))
                    if quantity == 0:
                        rejection = "EXPOSURE_LIMIT"
            fill = simulate_fill(symbol, side, quantity, bar, previous, cash, config, acquired.get(symbol)) if rejection is None else None
            filled = fill.quantity if fill else 0
            price, fee = (fill.price, fill.fee) if fill else (0.0, 0.0)
            realized = None
            if filled:
                amount = round(filled * price, 2)
                if side == "BUY":
                    cash = round(cash - amount - fee, 2)
                    positions[symbol] = held + filled
                    costs[symbol] = costs.get(symbol, 0) + amount + fee
                    acquired[symbol] = day
                else:
                    removed_cost = costs[symbol] * filled / held
                    realized = round(amount - fee - removed_cost, 2)
                    cash = round(cash + amount - fee, 2)
                    costs[symbol] -= removed_cost
                    positions[symbol] = held - filled
                    if not positions[symbol]:
                        del positions[symbol], costs[symbol], acquired[symbol]
            orders.append({
                "signal_date": signal_day.isoformat(), "trade_date": day.isoformat(),
                "symbol": symbol, "side": side, "requested_quantity": abs(delta),
                "filled_quantity": filled, "price": price, "fee": fee, "cash_after": cash,
                "realized_pnl": realized, "reason": rejection or fill.reason,
                "status": "REJECTED" if not filled else ("FILLED" if filled == abs(delta) else "PARTIAL"),
            })
    return cash


def return_metrics(returns: list[float]) -> dict:
    wealth = peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        wealth *= 1 + value
        peak = max(peak, wealth)
        max_drawdown = max(max_drawdown, 1 - wealth / peak)
    volatility = stdev(returns) * sqrt(252) if len(returns) >= 20 else None
    return {
        "days": len(returns), "total_return": wealth - 1,
        "annualized_return": wealth ** (252 / len(returns)) - 1 if returns else None,
        "max_drawdown": max_drawdown,
        "annualized_volatility": volatility,
        "sharpe": mean(returns) * 252 / volatility if volatility else None,
    }
