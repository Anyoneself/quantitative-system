from __future__ import annotations

from dataclasses import asdict, dataclass

from analysis.advisor import generate_advice
from analysis.divergence import analyze_divergence
from analysis.indicators import calculate_indicators
from analysis.ml_model import predict_next_day_buy_probability
from analysis.patterns import detect_patterns
from analysis.sell_advisor import build_sell_advice
from data.models import PriceVolumeBar


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    buy_score_threshold: int = 60
    sell_risk_threshold: int = 45
    max_position_rate: float = 0.95
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.001
    slippage_rate: float = 0.0005
    lot_size: int = 100


@dataclass(frozen=True)
class Trade:
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
    win_rate: float
    trades: list[Trade]
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
    peak_equity = config.initial_cash
    trades: list[Trade] = []
    equity_curve: list[EquityPoint] = []
    wins = 0
    closed_trades = 0
    notes = [
        "信号在收盘后生成，下一交易日开盘成交，避免使用未来数据。",
        "成交价包含滑点；买入含佣金，卖出含佣金和印花税。",
        "涨停开盘不追买，跌停开盘不强卖；未处理 ST 股票特殊涨跌停。",
    ]

    for signal_index in range(44, len(bars) - 1):
        history = bars[: signal_index + 1]
        signal_bar = bars[signal_index]
        trade_bar = bars[signal_index + 1]

        if position <= 0:
            advice = _build_historical_buy_advice(symbol, history, algorithm)
            if advice and advice.score >= config.buy_score_threshold and advice.action in {"BUY", "WATCH"}:
                if not _is_limit_up_open(trade_bar, signal_bar.close):
                    cash, position, position_cost, trade = _buy_at_open(
                        trade_bar,
                        cash,
                        config,
                        f"买入评分 {advice.score}，建议 {advice.action}",
                    )
                    if trade:
                        trades.append(trade)
        else:
            average_cost = position_cost / position if position > 0 else None
            sell_advice = build_sell_advice(symbol, history, cost_price=average_cost)
            should_sell = (
                sell_advice.sell_risk_score >= config.sell_risk_threshold
                or sell_advice.action in {"STOP_LOSS", "TAKE_PROFIT"}
            )
            if should_sell and not _is_limit_down_open(trade_bar, signal_bar.close):
                cash, realized_pnl, trade = _sell_at_open(
                    trade_bar,
                    cash,
                    position,
                    position_cost,
                    config,
                    f"卖出风险 {sell_advice.sell_risk_score}，建议 {sell_advice.action}",
                )
                trades.append(trade)
                position = 0
                position_cost = 0.0
                closed_trades += 1
                if realized_pnl > 0:
                    wins += 1

        market_value = position * trade_bar.close
        total_equity = cash + market_value
        peak_equity = max(peak_equity, total_equity)
        drawdown = _safe_ratio(total_equity - peak_equity, peak_equity)
        equity_curve.append(
            EquityPoint(
                trade_date=trade_bar.trade_date.isoformat(),
                close=trade_bar.close,
                cash=round(cash, 2),
                position=position,
                market_value=round(market_value, 2),
                total_equity=round(total_equity, 2),
                drawdown=drawdown,
            )
        )

    if not equity_curve:
        raise ValueError("回测没有生成净值曲线。")

    final_equity = equity_curve[-1].total_equity
    total_return = _safe_ratio(final_equity - config.initial_cash, config.initial_cash)
    years = max(1 / 252, len(equity_curve) / 252)
    annualized_return = (final_equity / config.initial_cash) ** (1 / years) - 1
    max_drawdown = min(point.drawdown for point in equity_curve)
    win_rate = _safe_ratio(wins, closed_trades)

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
        win_rate=win_rate,
        trades=trades,
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
) -> tuple[float, int, float, Trade | None]:
    price = bar.open * (1 + config.slippage_rate)
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
) -> tuple[float, float, Trade]:
    price = bar.open * (1 - config.slippage_rate)
    amount = price * quantity
    fee = _sell_fee(amount, config)
    cash_after = cash + amount - fee
    realized_pnl = amount - fee - position_cost
    trade = Trade(
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


def _buy_fee(amount: float, config: BacktestConfig) -> float:
    return max(config.min_commission, amount * config.commission_rate)


def _sell_fee(amount: float, config: BacktestConfig) -> float:
    commission = max(config.min_commission, amount * config.commission_rate)
    return commission + amount * config.stamp_duty_rate


def _is_limit_up_open(bar: PriceVolumeBar, previous_close: float) -> bool:
    # A 股普通股票涨停附近通常无法保证买入成交，这里用近似阈值保守过滤。
    return _safe_ratio(bar.open - previous_close, previous_close) >= 0.098


def _is_limit_down_open(bar: PriceVolumeBar, previous_close: float) -> bool:
    # 跌停开盘时强制卖出可能无法成交，回测中跳过以避免过度乐观。
    return _safe_ratio(bar.open - previous_close, previous_close) <= -0.098


def _validate_config(config: BacktestConfig) -> None:
    if config.initial_cash <= 0:
        raise ValueError("初始资金必须大于 0。")
    if not 0 < config.max_position_rate <= 1:
        raise ValueError("最大仓位比例必须在 0 到 1 之间。")
    if config.lot_size <= 0:
        raise ValueError("交易手数必须大于 0。")
    if config.commission_rate < 0 or config.stamp_duty_rate < 0 or config.slippage_rate < 0:
        raise ValueError("手续费、印花税和滑点不能为负数。")


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
