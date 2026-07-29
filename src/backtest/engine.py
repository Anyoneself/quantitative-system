from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from math import isfinite, sqrt

from analysis.advisor import generate_advice
from analysis.divergence import analyze_divergence
from analysis.indicators import calculate_indicators
from analysis.ml_model import predict_next_day_buy_probability
from analysis.patterns import detect_patterns
from analysis.sell_advisor import build_sell_advice
from data.market_rules import is_limit_price
from data.models import PriceVolumeBar


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    buy_score_threshold: int = 60
    sell_risk_threshold: int = 45
    max_position_rate: float = 0.95
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    slippage_rate: float = 0.0005
    lot_size: int = 100
    liquidate_at_end: bool = True


@dataclass(frozen=True)
class Trade:
    signal_date: str
    trade_date: str
    side: str
    price: float
    quantity: int
    amount: float
    fee: float
    cash_after: float
    position_after: int
    reason: str


@dataclass(frozen=True)
class RoundTrip:
    entry_signal_date: str
    entry_date: str
    exit_signal_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    entry_amount: float
    exit_amount: float
    entry_fee: float
    exit_fee: float
    pnl: float
    return_rate: float
    holding_trade_days: int
    entry_reason: str
    exit_reason: str


@dataclass(frozen=True)
class EquityPoint:
    trade_date: str
    close: float
    cash: float
    position: int
    market_value: float
    total_equity: float
    drawdown: float


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    start_date: str
    end_date: str
    initial_cash: float
    final_equity: float
    total_return: float
    annualized_return: float
    max_drawdown: float
    trade_count: int
    closed_trade_count: int
    open_position_count: int
    win_rate: float | None
    profit_factor: float | None
    expectancy: float | None
    average_win: float | None
    average_loss: float | None
    sharpe_ratio: float | None
    calmar_ratio: float | None
    exposure_rate: float
    trades: list[Trade]
    round_trips: list[RoundTrip]
    equity_curve: list[EquityPoint]
    notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def run_backtest(
    symbol: str,
    bars: list[PriceVolumeBar],
    algorithm: str,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    config = config or BacktestConfig()
    _validate_config(config)
    if len(bars) < 46:
        raise ValueError("回测至少需要 46 个交易日，用于生成信号并在下一交易日成交。")

    cash = config.initial_cash
    position = 0
    position_cost = 0.0
    trades: list[Trade] = []
    round_trips: list[RoundTrip] = []
    equity_curve: list[EquityPoint] = []
    entry_trade: Trade | None = None
    entry_trade_index: int | None = None
    exposed_periods = 0
    notes = [
        "信号在收盘后生成，下一交易日开盘成交，避免使用未来数据。",
        "成交价包含滑点并限制在当日最高价和最低价之间；买入含佣金，卖出含佣金和印花税。",
        "成交量为零、开盘价非法或开盘价超出当日高低价时不成交。",
        "涨跌停按股票代码和可用名称近似区分主板、创业板、科创板、北交所及 ST；行情名称不是点时数据时无法可靠还原历史 ST 状态。",
    ]

    for signal_index in range(44, len(bars) - 1):
        history = bars[: signal_index + 1]
        signal_bar = bars[signal_index]
        trade_bar = bars[signal_index + 1]
        trade_index = signal_index + 1
        signal_date = signal_bar.trade_date.isoformat()

        if position <= 0:
            advice = _build_historical_buy_advice(symbol, history, algorithm)
            if advice and advice.score >= config.buy_score_threshold and advice.action in {"BUY", "WATCH"}:
                if not _is_limit_up_open(trade_bar, signal_bar.close, symbol):
                    cash, position, position_cost, trade = _buy_at_open(
                        trade_bar,
                        cash,
                        config,
                        f"买入评分 {advice.score}，建议 {advice.action}",
                        signal_date=signal_date,
                    )
                    if trade:
                        trades.append(trade)
                        entry_trade = trade
                        entry_trade_index = trade_index
        else:
            average_cost = position_cost / position if position > 0 else None
            sell_advice = build_sell_advice(symbol, history, cost_price=average_cost)
            should_sell = (
                sell_advice.sell_risk_score >= config.sell_risk_threshold
                or sell_advice.action in {"STOP_LOSS", "TAKE_PROFIT"}
            )
            if should_sell and not _is_limit_down_open(trade_bar, signal_bar.close, symbol):
                cash, realized_pnl, trade = _sell_at_open(
                    trade_bar,
                    cash,
                    position,
                    position_cost,
                    config,
                    f"卖出风险 {sell_advice.sell_risk_score}，建议 {sell_advice.action}",
                    signal_date=signal_date,
                )
                if trade:
                    trades.append(trade)
                    if entry_trade is not None and entry_trade_index is not None:
                        round_trips.append(
                            _build_round_trip(
                                entry_trade,
                                trade,
                                realized_pnl,
                                position_cost,
                                trade_index - entry_trade_index,
                            )
                        )
                    position = 0
                    position_cost = 0.0
                    entry_trade = None
                    entry_trade_index = None

        if position > 0:
            exposed_periods += 1
        market_value = position * trade_bar.close
        total_equity = cash + market_value
        equity_curve.append(
            EquityPoint(
                trade_date=trade_bar.trade_date.isoformat(),
                close=trade_bar.close,
                cash=round(cash, 2),
                position=position,
                market_value=round(market_value, 2),
                total_equity=round(total_equity, 2),
                drawdown=0.0,
            )
        )

    if not equity_curve:
        raise ValueError("回测没有生成净值曲线。")

    if position > 0 and config.liquidate_at_end:
        last_bar = bars[-1]
        cash_after, realized_pnl, liquidation_trade = _sell_at_close(
            last_bar,
            cash,
            position,
            position_cost,
            config,
            "回测期末按最后收盘价强制平仓",
            signal_date=last_bar.trade_date.isoformat(),
        )
        if liquidation_trade:
            trades.append(liquidation_trade)
            if entry_trade is not None and entry_trade_index is not None:
                round_trips.append(
                    _build_round_trip(
                        entry_trade,
                        liquidation_trade,
                        realized_pnl,
                        position_cost,
                        len(bars) - 1 - entry_trade_index,
                    )
                )
            cash = cash_after
            position = 0
            position_cost = 0.0
            entry_trade = None
            entry_trade_index = None
            equity_curve[-1] = replace(
                equity_curve[-1],
                cash=round(cash, 2),
                position=0,
                market_value=0.0,
                total_equity=round(cash, 2),
            )
            notes.append("回测期末持仓已按最后收盘价考虑滑点和卖出费用后强制平仓。")
        else:
            notes.append("回测期末行情不可成交，持仓未能强制平仓，期末权益仍按最后收盘价估值。")
    elif position > 0:
        notes.append("配置为期末不强制平仓，未平仓头寸按最后收盘价估值且未扣除退出费用。")

    equity_curve = _apply_drawdowns(equity_curve, config.initial_cash)
    final_equity = equity_curve[-1].total_equity
    total_return = _safe_ratio(final_equity - config.initial_cash, config.initial_cash)
    annualized_return = _annualized_return(final_equity, config.initial_cash, len(equity_curve))
    max_drawdown = max(point.drawdown for point in equity_curve)
    trade_metrics = _calculate_trade_metrics(round_trips)
    sharpe_ratio = _calculate_sharpe_ratio(equity_curve, config.initial_cash)
    calmar_ratio = annualized_return / max_drawdown if max_drawdown > 0 else None
    exposure_rate = exposed_periods / len(equity_curve)

    return BacktestResult(
        symbol=symbol,
        start_date=equity_curve[0].trade_date,
        end_date=equity_curve[-1].trade_date,
        initial_cash=config.initial_cash,
        final_equity=round(final_equity, 2),
        total_return=total_return,
        annualized_return=annualized_return,
        max_drawdown=max_drawdown,
        trade_count=len(trades),
        closed_trade_count=len(round_trips),
        open_position_count=1 if position > 0 else 0,
        win_rate=trade_metrics["win_rate"],
        profit_factor=trade_metrics["profit_factor"],
        expectancy=trade_metrics["expectancy"],
        average_win=trade_metrics["average_win"],
        average_loss=trade_metrics["average_loss"],
        sharpe_ratio=sharpe_ratio,
        calmar_ratio=calmar_ratio,
        exposure_rate=exposure_rate,
        trades=trades,
        round_trips=round_trips,
        equity_curve=equity_curve,
        notes=notes,
    )


def _build_historical_buy_advice(symbol: str, history: list[PriceVolumeBar], algorithm: str):
    indicators = calculate_indicators(symbol, history)
    if not indicators:
        return None
    patterns = detect_patterns(indicators)
    prediction = predict_next_day_buy_probability(history, algorithm)
    divergence = analyze_divergence(history)
    return generate_advice(indicators, patterns, prediction, None, divergence)


def _buy_at_open(
    bar: PriceVolumeBar,
    cash: float,
    config: BacktestConfig,
    reason: str,
    signal_date: str | None = None,
) -> tuple[float, int, float, Trade | None]:
    if not _can_trade_at_open(bar):
        return cash, 0, 0.0, None
    price = _bounded_execution_price(bar.open * (1 + config.slippage_rate), bar)
    target_cash = cash * config.max_position_rate
    quantity = int(target_cash // (price * config.lot_size)) * config.lot_size
    while quantity > 0:
        amount = price * quantity
        fee = _buy_fee(amount, config)
        if amount + fee <= cash:
            break
        quantity -= config.lot_size
    if quantity <= 0:
        return cash, 0, 0.0, None

    amount = price * quantity
    fee = _buy_fee(amount, config)
    cash_after = cash - amount - fee
    trade = Trade(
        signal_date=signal_date or bar.trade_date.isoformat(),
        trade_date=bar.trade_date.isoformat(),
        side="BUY",
        price=round(price, 4),
        quantity=quantity,
        amount=round(amount, 2),
        fee=round(fee, 2),
        cash_after=round(cash_after, 2),
        position_after=quantity,
        reason=reason,
    )
    return cash_after, quantity, amount + fee, trade


def _sell_at_open(
    bar: PriceVolumeBar,
    cash: float,
    quantity: int,
    position_cost: float,
    config: BacktestConfig,
    reason: str,
    signal_date: str | None = None,
) -> tuple[float, float, Trade | None]:
    if not _can_trade_at_open(bar):
        return cash, 0.0, None
    price = _bounded_execution_price(bar.open * (1 - config.slippage_rate), bar)
    return _sell_at_price(bar, price, cash, quantity, position_cost, config, reason, signal_date)


def _sell_at_close(
    bar: PriceVolumeBar,
    cash: float,
    quantity: int,
    position_cost: float,
    config: BacktestConfig,
    reason: str,
    signal_date: str | None = None,
) -> tuple[float, float, Trade | None]:
    if not _can_trade_at_close(bar):
        return cash, 0.0, None
    price = _bounded_execution_price(bar.close * (1 - config.slippage_rate), bar)
    return _sell_at_price(bar, price, cash, quantity, position_cost, config, reason, signal_date)


def _sell_at_price(
    bar: PriceVolumeBar,
    price: float,
    cash: float,
    quantity: int,
    position_cost: float,
    config: BacktestConfig,
    reason: str,
    signal_date: str | None,
) -> tuple[float, float, Trade]:
    amount = price * quantity
    fee = _sell_fee(amount, config)
    cash_after = cash + amount - fee
    realized_pnl = amount - fee - position_cost
    trade = Trade(
        signal_date=signal_date or bar.trade_date.isoformat(),
        trade_date=bar.trade_date.isoformat(),
        side="SELL",
        price=round(price, 4),
        quantity=quantity,
        amount=round(amount, 2),
        fee=round(fee, 2),
        cash_after=round(cash_after, 2),
        position_after=0,
        reason=reason,
    )
    return cash_after, realized_pnl, trade


def _build_round_trip(
    entry_trade: Trade,
    exit_trade: Trade,
    realized_pnl: float,
    position_cost: float,
    holding_trade_days: int,
) -> RoundTrip:
    return RoundTrip(
        entry_signal_date=entry_trade.signal_date,
        entry_date=entry_trade.trade_date,
        exit_signal_date=exit_trade.signal_date,
        exit_date=exit_trade.trade_date,
        entry_price=entry_trade.price,
        exit_price=exit_trade.price,
        quantity=entry_trade.quantity,
        entry_amount=entry_trade.amount,
        exit_amount=exit_trade.amount,
        entry_fee=entry_trade.fee,
        exit_fee=exit_trade.fee,
        pnl=round(realized_pnl, 2),
        return_rate=_safe_ratio(realized_pnl, position_cost),
        holding_trade_days=max(0, holding_trade_days),
        entry_reason=entry_trade.reason,
        exit_reason=exit_trade.reason,
    )


def _calculate_trade_metrics(round_trips: list[RoundTrip]) -> dict[str, float | None]:
    if not round_trips:
        return {
            "win_rate": None,
            "profit_factor": None,
            "expectancy": None,
            "average_win": None,
            "average_loss": None,
        }

    profits = [trade.pnl for trade in round_trips if trade.pnl > 0]
    losses = [trade.pnl for trade in round_trips if trade.pnl < 0]
    gross_profit = sum(profits)
    gross_loss = abs(sum(losses))
    return {
        "win_rate": len(profits) / len(round_trips),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "expectancy": sum(trade.pnl for trade in round_trips) / len(round_trips),
        "average_win": sum(profits) / len(profits) if profits else None,
        "average_loss": sum(losses) / len(losses) if losses else None,
    }


def _apply_drawdowns(equity_curve: list[EquityPoint], initial_cash: float) -> list[EquityPoint]:
    peak_equity = initial_cash
    points = []
    for point in equity_curve:
        peak_equity = max(peak_equity, point.total_equity)
        drawdown = max(0.0, _safe_ratio(peak_equity - point.total_equity, peak_equity))
        points.append(replace(point, drawdown=drawdown))
    return points


def _calculate_sharpe_ratio(equity_curve: list[EquityPoint], initial_cash: float) -> float | None:
    returns = []
    previous_equity = initial_cash
    for point in equity_curve:
        if previous_equity <= 0:
            return None
        returns.append((point.total_equity - previous_equity) / previous_equity)
        previous_equity = point.total_equity
    if len(returns) < 2:
        return None
    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
    standard_deviation = sqrt(variance)
    if standard_deviation == 0:
        return None
    return mean_return / standard_deviation * sqrt(252)


def _annualized_return(final_equity: float, initial_cash: float, periods: int) -> float:
    if final_equity <= 0:
        return -1.0
    years = max(1 / 252, periods / 252)
    return (final_equity / initial_cash) ** (1 / years) - 1


def _buy_fee(amount: float, config: BacktestConfig) -> float:
    return max(config.min_commission, amount * config.commission_rate)


def _sell_fee(amount: float, config: BacktestConfig) -> float:
    commission = max(config.min_commission, amount * config.commission_rate)
    return commission + amount * config.stamp_duty_rate


def _is_limit_up_open(bar: PriceVolumeBar, previous_close: float, symbol: str = "") -> bool:
    if not _is_positive_finite(bar.open) or not _is_positive_finite(previous_close):
        return False
    return is_limit_price(
        bar.open,
        previous_close,
        symbol,
        bar.name,
        bar.trade_date,
        direction="UP",
    )


def _is_limit_down_open(bar: PriceVolumeBar, previous_close: float, symbol: str = "") -> bool:
    if not _is_positive_finite(bar.open) or not _is_positive_finite(previous_close):
        return False
    return is_limit_price(
        bar.open,
        previous_close,
        symbol,
        bar.name,
        bar.trade_date,
        direction="DOWN",
    )


def _can_trade_at_open(bar: PriceVolumeBar) -> bool:
    return _has_valid_range(bar) and _is_positive_finite(bar.open) and bar.low <= bar.open <= bar.high


def _can_trade_at_close(bar: PriceVolumeBar) -> bool:
    return _has_valid_range(bar) and _is_positive_finite(bar.close) and bar.low <= bar.close <= bar.high


def _has_valid_range(bar: PriceVolumeBar) -> bool:
    return (
        _is_positive_finite(bar.volume)
        and _is_positive_finite(bar.low)
        and _is_positive_finite(bar.high)
        and bar.low <= bar.high
    )


def _bounded_execution_price(price: float, bar: PriceVolumeBar) -> float:
    return min(bar.high, max(bar.low, price))


def _validate_config(config: BacktestConfig) -> None:
    _validate_finite_number("初始资金", config.initial_cash)
    if config.initial_cash <= 0 or config.initial_cash > 1_000_000_000_000:
        raise ValueError("初始资金必须大于 0 且不超过 1 万亿元。")
    _validate_score("买入评分阈值", config.buy_score_threshold)
    _validate_score("卖出风险阈值", config.sell_risk_threshold)
    _validate_finite_number("最大仓位比例", config.max_position_rate)
    if not 0 < config.max_position_rate <= 1:
        raise ValueError("最大仓位比例必须在 0 到 1 之间。")
    if isinstance(config.lot_size, bool) or not isinstance(config.lot_size, int):
        raise ValueError("交易手数必须是整数。")
    if config.lot_size <= 0 or config.lot_size > 1_000_000:
        raise ValueError("交易手数必须大于 0 且不超过 1000000。")
    _validate_rate("佣金比例", config.commission_rate)
    _validate_rate("印花税比例", config.stamp_duty_rate)
    _validate_rate("滑点比例", config.slippage_rate)
    _validate_finite_number("最低佣金", config.min_commission)
    if config.min_commission < 0 or config.min_commission > 1_000_000:
        raise ValueError("最低佣金必须在 0 到 1000000 之间。")
    if not isinstance(config.liquidate_at_end, bool):
        raise ValueError("期末强制平仓配置必须是布尔值。")


def _validate_score(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError(f"{name}必须是 0 到 100 之间的整数。")


def _validate_rate(name: str, value: float) -> None:
    _validate_finite_number(name, value)
    if not 0 <= value <= 0.10:
        raise ValueError(f"{name}必须在 0 到 0.10 之间。")


def _validate_finite_number(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name}必须是有限数值。")


def _is_positive_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
