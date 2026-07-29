from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence


MINIMUM_RETURN_SAMPLES = 20


@dataclass(frozen=True)
class RiskThresholds:
    volatility_warning: float = 0.30
    volatility_high: float = 0.45
    var_warning: float = 0.03
    var_high: float = 0.05
    drawdown_warning: float = 0.15
    drawdown_high: float = 0.25


@dataclass(frozen=True)
class RiskResult:
    annualized_volatility: float | None
    sharpe_ratio: float | None
    beta: float | None
    var_95: float | None
    win_rate: float | None
    profit_loss_ratio: float | None
    sample_size: int
    sample_status: str
    risk_level: str
    reasons: tuple[str, ...]


def calculate_risk(
    daily_returns: Sequence[float | None],
    benchmark_returns: Sequence[float | None] | None = None,
    realized_pnls: Sequence[float] | None = None,
    max_drawdown: float | None = None,
    annual_risk_free_rate: float = 0.0,
    thresholds: RiskThresholds | None = None,
) -> RiskResult:
    thresholds = thresholds or RiskThresholds()
    returns = [float(value) for value in daily_returns if value is not None and math.isfinite(value)]
    reasons: list[str] = []

    volatility = _annualized_volatility(returns)
    sharpe = _sharpe_ratio(returns, volatility, annual_risk_free_rate)
    var_95 = _historical_var_95(returns)
    if len(returns) < MINIMUM_RETURN_SAMPLES:
        reasons.append(f"有效日收益样本少于 {MINIMUM_RETURN_SAMPLES} 个。")
    elif volatility == 0:
        reasons.append("日收益没有波动，夏普比率无法计算。")

    beta = _beta(daily_returns, benchmark_returns)
    if benchmark_returns is None:
        reasons.append("缺少比较基准收益，Beta 无法计算。")
    elif beta is None:
        reasons.append("比较基准有效样本不足或方差为零，Beta 无法计算。")

    pnls = [
        float(value)
        for value in (realized_pnls or [])
        if math.isfinite(value)
    ]
    win_rate = _win_rate(pnls)
    profit_loss_ratio = _profit_loss_ratio(pnls)
    if not pnls:
        reasons.append("没有已实现交易盈亏，胜率和盈亏比无法计算。")
    elif profit_loss_ratio is None:
        reasons.append("盈利或亏损样本缺失，盈亏比无法计算。")

    risk_level = _risk_level(
        volatility,
        var_95,
        max_drawdown,
        thresholds,
    )
    if not returns and not pnls and max_drawdown is None:
        risk_level = "NO_DATA"
    sample_status = (
        f"已计算 {len(returns)} 个日收益样本"
        if len(returns) >= MINIMUM_RETURN_SAMPLES
        else "样本不足"
    )
    return RiskResult(
        annualized_volatility=volatility,
        sharpe_ratio=sharpe,
        beta=beta,
        var_95=var_95,
        win_rate=win_rate,
        profit_loss_ratio=profit_loss_ratio,
        sample_size=len(returns),
        sample_status=sample_status,
        risk_level=risk_level,
        reasons=tuple(reasons),
    )


def _annualized_volatility(returns: list[float]) -> float | None:
    if len(returns) < MINIMUM_RETURN_SAMPLES:
        return None
    return statistics.stdev(returns) * math.sqrt(252)


def _sharpe_ratio(
    returns: list[float],
    volatility: float | None,
    annual_risk_free_rate: float,
) -> float | None:
    if volatility in (None, 0):
        return None
    annualized_return = statistics.mean(returns) * 252
    return (annualized_return - annual_risk_free_rate) / volatility


def _historical_var_95(returns: list[float]) -> float | None:
    if len(returns) < MINIMUM_RETURN_SAMPLES:
        return None
    ordered = sorted(returns)
    percentile_index = max(0, math.ceil(len(ordered) * 0.05) - 1)
    return max(0.0, -ordered[percentile_index])


def _beta(
    daily_returns: Sequence[float | None],
    benchmark_returns: Sequence[float | None] | None,
) -> float | None:
    if benchmark_returns is None:
        return None
    aligned = [
        (float(portfolio), float(benchmark))
        for portfolio, benchmark in zip(daily_returns, benchmark_returns)
        if portfolio is not None
        and benchmark is not None
        and math.isfinite(portfolio)
        and math.isfinite(benchmark)
    ]
    if len(aligned) < MINIMUM_RETURN_SAMPLES:
        return None
    portfolio_values = [item[0] for item in aligned]
    benchmark_values = [item[1] for item in aligned]
    benchmark_variance = statistics.variance(benchmark_values)
    if benchmark_variance == 0:
        return None
    covariance = statistics.covariance(portfolio_values, benchmark_values)
    return covariance / benchmark_variance


def _win_rate(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    return sum(value > 0 for value in pnls) / len(pnls)


def _profit_loss_ratio(pnls: list[float]) -> float | None:
    profits = [value for value in pnls if value > 0]
    losses = [abs(value) for value in pnls if value < 0]
    if not profits or not losses:
        return None
    return statistics.mean(profits) / statistics.mean(losses)


def _risk_level(
    volatility: float | None,
    var_95: float | None,
    max_drawdown: float | None,
    thresholds: RiskThresholds,
) -> str:
    drawdown_size = abs(max_drawdown) if max_drawdown is not None else None
    # 高风险阈值用于提示优先收缩风险敞口，不代表自动触发交易。
    if (
        (volatility is not None and volatility >= thresholds.volatility_high)
        or (var_95 is not None and var_95 >= thresholds.var_high)
        or (drawdown_size is not None and drawdown_size >= thresholds.drawdown_high)
    ):
        return "HIGH"
    # 预警阈值用于提示复核仓位、流动性和止损纪律。
    if (
        (volatility is not None and volatility >= thresholds.volatility_warning)
        or (var_95 is not None and var_95 >= thresholds.var_warning)
        or (drawdown_size is not None and drawdown_size >= thresholds.drawdown_warning)
    ):
        return "WARNING"
    return "NORMAL"
