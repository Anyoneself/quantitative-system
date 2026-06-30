from __future__ import annotations

from dataclasses import dataclass

from data.models import FundFlowBar


@dataclass(frozen=True)
class FundFlowResult:
    latest_main_net_inflow: float
    latest_main_net_inflow_ratio: float
    main_inflow_3d: float
    main_inflow_5d: float
    main_inflow_10d: float
    positive_days_5d: int
    positive_days_10d: int
    super_large_inflow_5d: float
    retail_outflow_5d: float
    fund_flow_score_adjustment: int
    signal: str
    explanation: str


def analyze_fund_flow(fund_flows: list[FundFlowBar]) -> FundFlowResult | None:
    if not fund_flows:
        return None

    bars = sorted(fund_flows, key=lambda item: item.trade_date)
    latest = bars[-1]
    recent_3 = bars[-3:]
    recent_5 = bars[-5:]
    recent_10 = bars[-10:]
    main_inflow_3d = _sum_main(recent_3)
    main_inflow_5d = _sum_main(recent_5)
    main_inflow_10d = _sum_main(recent_10)
    positive_days_5d = _positive_days(recent_5)
    positive_days_10d = _positive_days(recent_10)
    super_large_inflow_5d = sum(item.super_large_net_inflow for item in recent_5)
    retail_outflow_5d = -sum(item.small_net_inflow for item in recent_5)
    score_adjustment = _score_adjustment(
        latest,
        main_inflow_5d,
        main_inflow_10d,
        positive_days_5d,
        positive_days_10d,
        super_large_inflow_5d,
        retail_outflow_5d,
    )
    signal = _signal(main_inflow_5d, main_inflow_10d, positive_days_5d)
    explanation = _explanation(signal, latest, main_inflow_5d, main_inflow_10d, positive_days_5d, score_adjustment)

    return FundFlowResult(
        latest_main_net_inflow=latest.main_net_inflow,
        latest_main_net_inflow_ratio=latest.main_net_inflow_ratio,
        main_inflow_3d=main_inflow_3d,
        main_inflow_5d=main_inflow_5d,
        main_inflow_10d=main_inflow_10d,
        positive_days_5d=positive_days_5d,
        positive_days_10d=positive_days_10d,
        super_large_inflow_5d=super_large_inflow_5d,
        retail_outflow_5d=retail_outflow_5d,
        fund_flow_score_adjustment=score_adjustment,
        signal=signal,
        explanation=explanation,
    )


def _score_adjustment(
    latest: FundFlowBar,
    main_inflow_5d: float,
    main_inflow_10d: float,
    positive_days_5d: int,
    positive_days_10d: int,
    super_large_inflow_5d: float,
    retail_outflow_5d: float,
) -> int:
    score = 0
    if main_inflow_5d > 0:
        score += 4
    else:
        score -= 5
    if main_inflow_10d > 0:
        score += 4
    else:
        score -= 5
    if positive_days_5d >= 3:
        score += 4
    if positive_days_10d <= 3:
        score -= 3
    if latest.main_net_inflow_ratio > 0.03:
        score += 5
    elif latest.main_net_inflow_ratio < -0.03:
        score -= 6
    if super_large_inflow_5d > 0:
        score += 4
    if retail_outflow_5d > 0 and main_inflow_5d > 0:
        score += 3
    return max(-20, min(20, score))


def _signal(main_inflow_5d: float, main_inflow_10d: float, positive_days_5d: int) -> str:
    if main_inflow_5d > 0 and main_inflow_10d > 0 and positive_days_5d >= 3:
        return "持续流入"
    if main_inflow_5d < 0 and main_inflow_10d < 0 and positive_days_5d <= 2:
        return "持续流出"
    return "资金分歧"


def _explanation(
    signal: str,
    latest: FundFlowBar,
    main_inflow_5d: float,
    main_inflow_10d: float,
    positive_days_5d: int,
    score_adjustment: int,
) -> str:
    return (
        f"资金流信号为{signal}；当日主力净流入 {_format_amount(latest.main_net_inflow)}，"
        f"占比 {latest.main_net_inflow_ratio * 100:.2f}%；5 日主力净流入 {_format_amount(main_inflow_5d)}，"
        f"10 日主力净流入 {_format_amount(main_inflow_10d)}，5 日内 {positive_days_5d} 天净流入，"
        f"资金流调整分 {score_adjustment:+d}。"
    )


def _sum_main(bars: list[FundFlowBar]) -> float:
    return sum(item.main_net_inflow for item in bars)


def _positive_days(bars: list[FundFlowBar]) -> int:
    return sum(1 for item in bars if item.main_net_inflow > 0)


def _format_amount(value: float) -> str:
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿元"
    if abs(value) >= 10_000:
        return f"{value / 10_000:.2f} 万元"
    return f"{value:.2f} 元"