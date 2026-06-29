from __future__ import annotations

from dataclasses import dataclass

from analysis.chan_theory import ChanStructureResult, analyze_chan_structure
from analysis.indicators import IndicatorResult, calculate_indicators
from analysis.patterns import PatternResult, detect_patterns
from data.models import PriceVolumeBar


@dataclass(frozen=True)
class SellKeyLevels:
    cost_price: float | None
    close: float
    ma20: float
    ma250: float
    chan_center_lower: float | None
    recent_high_close: float


@dataclass(frozen=True)
class SellAdvice:
    action: str
    sell_risk_score: int
    holding_return: float | None
    reasons: list[str]
    risks: list[str]
    observations: list[str]
    key_levels: SellKeyLevels | None
    indicators: IndicatorResult | None
    patterns: PatternResult | None
    chan_structure: ChanStructureResult | None


def make_no_sell_data_advice(message: str) -> SellAdvice:
    return SellAdvice(
        action="NO_POSITION",
        sell_risk_score=0,
        holding_return=None,
        reasons=[message],
        risks=["数据不足时不应做卖出判断。"],
        observations=["请先补充至少 21 个交易日的价格和成交量数据。"],
        key_levels=None,
        indicators=None,
        patterns=None,
        chan_structure=None,
    )


def build_sell_advice(
    symbol: str,
    bars: list[PriceVolumeBar],
    cost_price: float | None = None,
    quantity: float | None = None,
    max_loss_rate: float = 0.08,
    target_profit_rate: float = 0.20,
) -> SellAdvice:
    if not bars:
        return make_no_sell_data_advice(f"行情接口没有返回股票 {symbol} 的价格和成交量数据。")
    indicators = calculate_indicators(symbol, bars)
    if not indicators:
        return make_no_sell_data_advice(f"股票 {symbol} 数据不足，至少需要 21 个交易日。")

    patterns = detect_patterns(indicators)
    chan_structure = analyze_chan_structure(bars[-90:])
    recent_high_close = max(bar.close for bar in bars[-20:])
    key_levels = SellKeyLevels(
        cost_price=cost_price,
        close=indicators.close,
        ma20=indicators.ma20,
        ma250=indicators.ma250,
        chan_center_lower=chan_structure.center.lower if chan_structure.center else None,
        recent_high_close=recent_high_close,
    )

    if cost_price is None or cost_price <= 0:
        return _technical_risk_advice(indicators, patterns, chan_structure, key_levels)

    holding_return = (indicators.close - cost_price) / cost_price
    score = 0
    reasons: list[str] = []
    risks: list[str] = []
    observations: list[str] = []

    if holding_return <= -abs(max_loss_rate):
        score += 35
        risks.append(
            f"当前持仓收益率 {_format_percent(holding_return)}，已超过最大可承受亏损 "
            f"{_format_percent(abs(max_loss_rate))}，优先控制亏损。"
        )
    elif holding_return > 0:
        reasons.append(f"当前持仓收益率 {_format_percent(holding_return)}，仍有盈利缓冲。")
    else:
        observations.append(f"当前持仓收益率 {_format_percent(holding_return)}，尚未触发硬止损。")

    score = _apply_technical_sell_risks(score, indicators, patterns, chan_structure, risks, reasons, observations)

    drawdown_from_recent_high = _safe_ratio(recent_high_close - indicators.close, recent_high_close)
    if drawdown_from_recent_high >= 0.08 and holding_return > 0:
        score += 12
        risks.append(
            f"当前价格较近 20 日最高收盘价回撤 {_format_percent(drawdown_from_recent_high)}，"
            "已有利润出现回吐，需要考虑移动止盈。"
        )

    high_position_risk = patterns.high_volume_stagnation or patterns.high_20d_return or chan_structure.risk_signal in {
        "顶背驰候选",
        "高位缩量候选",
    }
    if holding_return >= target_profit_rate and high_position_risk:
        score += 15
        risks.append(
            f"当前持仓收益率达到 {_format_percent(holding_return)}，超过目标止盈 "
            f"{_format_percent(target_profit_rate)}，且出现高位风险信号，适合止盈或分批减仓。"
        )
    elif holding_return >= target_profit_rate:
        observations.append(
            f"当前持仓收益率达到 {_format_percent(holding_return)}，已进入止盈观察区，后续重点看是否放量滞涨或跌破结构位。"
        )

    if patterns.pullback_low_volume and indicators.close > indicators.ma20:
        score -= 10
        reasons.append("回调缩量且仍在 20 日均线上方，趋势暂未明显破坏，可继续观察。")
    if patterns.close_above_ma250 and patterns.ma250_turning_up:
        score -= 8
        reasons.append("价格在年线上方且年线向上，长期趋势仍有支撑。")

    score = _clamp_score(score)
    action = _sell_action(score, holding_return, max_loss_rate, target_profit_rate, high_position_risk)
    _append_action_reason(action, reasons, risks, observations)
    if quantity and quantity > 0:
        observations.append(f"按持仓数量 {quantity:.0f} 股估算，卖出决策应同时考虑单票仓位占比。")

    return SellAdvice(
        action=action,
        sell_risk_score=score,
        holding_return=holding_return,
        reasons=reasons,
        risks=risks or ["未发现必须立刻卖出的硬风险，但仍需关注趋势破坏信号。"],
        observations=observations,
        key_levels=key_levels,
        indicators=indicators,
        patterns=patterns,
        chan_structure=chan_structure,
    )


def _technical_risk_advice(
    indicators: IndicatorResult,
    patterns: PatternResult,
    chan_structure: ChanStructureResult,
    key_levels: SellKeyLevels,
) -> SellAdvice:
    score = 0
    reasons: list[str] = []
    risks = ["未输入持仓成本，系统只能给技术风险提示，不能判断真实止盈或止损。"]
    observations = ["输入持仓成本后，可以计算持仓收益率、止损线和止盈观察区。"]
    score = _apply_technical_sell_risks(score, indicators, patterns, chan_structure, risks, reasons, observations)
    score = _clamp_score(score)
    return SellAdvice(
        action="NO_POSITION",
        sell_risk_score=score,
        holding_return=None,
        reasons=reasons,
        risks=risks,
        observations=observations,
        key_levels=key_levels,
        indicators=indicators,
        patterns=patterns,
        chan_structure=chan_structure,
    )


def _apply_technical_sell_risks(
    score: int,
    indicators: IndicatorResult,
    patterns: PatternResult,
    chan_structure: ChanStructureResult,
    risks: list[str],
    reasons: list[str],
    observations: list[str],
) -> int:
    if patterns.volume_drop_below_ma20:
        score += 25
        risks.append("放量跌破 20 日均线，短中期趋势破坏风险较高。")
    elif not patterns.close_above_ma20:
        score += 12
        risks.append(f"收盘价 {indicators.close:.2f} 低于 20 日均线 {indicators.ma20:.2f}，趋势偏弱。")

    if indicators.ma250 > 0 and not patterns.close_above_ma250:
        score += 20
        risks.append(f"收盘价 {indicators.close:.2f} 低于年线 {indicators.ma250:.2f}，长期趋势尚未修复。")
    elif indicators.ma250 > 0 and patterns.ma250_turning_up:
        reasons.append("价格处在年线修复结构中，未跌破年线前不必机械卖出。")

    if patterns.high_volume_stagnation:
        score += 15
        risks.append("高位放量滞涨，可能存在筹码松动，适合止盈或减仓观察。")
    if patterns.high_volume_drop or patterns.price_down_volume_up:
        score += 12
        risks.append("价格下跌同时成交量放大，卖压上升。")
    if patterns.high_20d_return:
        score += 10
        observations.append("近 20 日涨幅较高，若继续放量滞涨或跌破支撑，应提高止盈优先级。")

    if chan_structure.risk_signal == "跌破中枢候选":
        score += 20
        risks.append("缠论结构出现跌破中枢候选，持仓需要防止趋势继续走弱。")
    elif chan_structure.risk_signal == "顶背驰候选":
        score += 18
        risks.append("缠论结构出现顶背驰候选，适合止盈或分批减仓。")
    elif chan_structure.risk_signal == "下降结构延续":
        score += 18
        risks.append("缠论结构显示下降结构延续，不适合继续重仓持有。")
    elif chan_structure.risk_signal == "高位缩量候选":
        score += 12
        risks.append("缠论结构出现高位缩量候选，继续追高和重仓持有风险上升。")
    else:
        observations.append("缠论结构暂未给出明确卖出风险，可继续观察关键支撑位。")
    return score


def _sell_action(
    score: int,
    holding_return: float,
    max_loss_rate: float,
    target_profit_rate: float,
    high_position_risk: bool,
) -> str:
    if holding_return <= -abs(max_loss_rate) and score >= 50:
        return "STOP_LOSS"
    if holding_return >= target_profit_rate and high_position_risk and score >= 30:
        return "TAKE_PROFIT"
    if score >= 70:
        return "STOP_LOSS" if holding_return < 0 else "TAKE_PROFIT"
    if score >= 50:
        return "TAKE_PROFIT" if holding_return > 0 else "STOP_LOSS"
    if score >= 30:
        return "REDUCE"
    return "HOLD"


def _append_action_reason(action: str, reasons: list[str], risks: list[str], observations: list[str]) -> None:
    if action == "HOLD":
        reasons.insert(0, "结论：继续持有。当前卖出风险不高，趋势尚未出现必须离场的硬破坏。")
    elif action == "REDUCE":
        risks.insert(0, "结论：减仓观察。趋势或结构风险上升，不适合继续重仓持有。")
    elif action == "TAKE_PROFIT":
        risks.insert(0, "结论：建议止盈或分批减仓。已有盈利且高位风险增加。")
    elif action == "STOP_LOSS":
        risks.insert(0, "结论：建议止损。当前风险已超过继续观察范围，应优先控制亏损。")
    observations.append("卖出建议是风险控制辅助，不等于一定能卖在最高点。")


def _clamp_score(score: int) -> int:
    return max(0, min(100, score))


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"