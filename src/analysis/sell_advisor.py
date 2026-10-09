from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from analysis.divergence import DivergenceResult
from analysis.fund_flow import FundFlowResult
from analysis.indicators import IndicatorResult, calculate_indicators
from analysis.patterns import PatternResult, detect_patterns
from data.models import PriceVolumeBar
from data.errors import DataRefreshError
from data.quality import validate_price_bars


@dataclass(frozen=True)
class SellKeyLevels:
    cost_price: float | None
    close: float
    ma20: float
    ma250: float
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
    fund_flow: FundFlowResult | None = None
    divergence: DivergenceResult | None = None


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
        fund_flow=None,
        divergence=None,
    )


def build_sell_advice(
    symbol: str,
    bars: list[PriceVolumeBar],
    cost_price: float | None = None,
    quantity: float | None = None,
    max_loss_rate: float = 0.08,
    target_profit_rate: float = 0.20,
    fund_flow: FundFlowResult | None = None,
    divergence: DivergenceResult | None = None,
    current_price: float | None = None,
) -> SellAdvice:
    if not bars:
        return make_no_sell_data_advice(f"行情接口没有返回股票 {symbol} 的价格和成交量数据。")
    indicators = calculate_indicators(symbol, bars)
    if not indicators:
        return make_no_sell_data_advice(f"股票 {symbol} 数据不足，至少需要 21 个交易日。")

    patterns = detect_patterns(indicators)
    recent_high_close = max(bar.close for bar in bars[-20:])
    valuation_price = indicators.close if current_price is None else current_price
    key_levels = SellKeyLevels(
        cost_price=cost_price,
        close=valuation_price,
        ma20=indicators.ma20,
        ma250=indicators.ma250,
        recent_high_close=recent_high_close,
    )

    if cost_price is None or cost_price <= 0:
        return _technical_risk_advice(indicators, patterns, key_levels, fund_flow, divergence)

    holding_return = (valuation_price - cost_price) / cost_price
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

    score = _apply_technical_sell_risks(score, indicators, patterns, risks, reasons, observations)
    score = _apply_fund_flow_sell_risk(score, fund_flow, patterns, risks, reasons, observations)
    score = _apply_divergence_sell_risk(score, divergence, risks, reasons, observations)

    drawdown_from_recent_high = _safe_ratio(recent_high_close - valuation_price, recent_high_close)
    if drawdown_from_recent_high >= 0.08 and holding_return > 0:
        score += 12
        risks.append(
            f"当前价格较近 20 日最高收盘价回撤 {_format_percent(drawdown_from_recent_high)}，"
            "已有利润出现回吐，需要考虑移动止盈。"
        )

    high_position_risk = patterns.high_volume_stagnation or patterns.high_20d_return
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
    if patterns.low_position_rebound:
        score -= 6
        reasons.append("低位附近出现十字星/长下影等止跌反弹信号，短线不宜只因下跌机械卖出。")
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
        fund_flow=fund_flow,
        divergence=divergence,
    )


def build_snapshot_sell_advice(snapshot, histories=None, *, now=None):
    """Reuse closed-bar sell rules; value the holding only at the client's displayed price."""
    now = now or datetime.now(timezone.utc)
    shanghai = ZoneInfo("Asia/Shanghai")
    captured = datetime.fromisoformat(snapshot["captured_at"]).astimezone(shanghai)
    total_assets = float(snapshot["funds"]["total_assets"])
    items = []
    for position in snapshot["positions"]:
        price, cost = float(position["price"]), float(position["cost"])
        pnl = (price - cost) / cost if price > 0 and cost > 0 else None
        action = "STOP_LOSS" if pnl is not None and pnl <= -.08 else "NO_DATA"
        reason = ("客户端显示亏损已达到默认 8% 亏损提醒线，建议优先控制亏损。"
                  if action == "STOP_LOSS" else "历史行情尚不完整，暂不能判断是否应卖出。")
        if price <= 0:
            reason = "客户端当前价格不可用，暂不能判断是否应卖出。"
        if price <= 0:
            decision, next_step = "先核对当前价格", "价格恢复后重新同步，再判断盈亏与卖出条件。"
        elif cost <= 0:
            decision, next_step = "先核对持仓成本", "当前成本不能计算有效收益率，不据此推算止盈或止损。"
        elif action == "STOP_LOSS":
            decision, next_step = "优先控制亏损", "已触及默认亏损提醒线；先核对最新价格、可卖数量与自身风险限额。"
        elif pnl >= .20:
            decision, next_step = "进入止盈观察", "已达到默认止盈观察线；等待趋势转弱确认，再评估分批减仓，不因达线自动卖出。"
        elif pnl < 0:
            decision, next_step = "亏损未越线，等待确认", "关注亏损提醒价及趋势变化，行情不足时不追加卖出判断。"
        else:
            decision, next_step = "保留观察，等待确认", "关注止盈观察价及趋势变化，当前盈亏本身不足以决定卖出。"
        if price > 0 and cost > 0 and action != "STOP_LOSS":
            reason = f"客户端显示持仓收益率 {_format_percent(pnl)}。" + next_step
        item = {
            "symbol": position["symbol"], "action": action, "holding_return": pnl,
            "status": "LOCAL_ONLY" if histories is None else "PARTIAL", "reason": reason,
            "decision": decision, "next_step": next_step,
            "stop_loss_price": round(cost * .92, 4) if cost > 0 else None,
            "profit_watch_price": round(cost * 1.20, 4) if cost > 0 else None,
            "position_weight": float(position["market_value"]) / total_assets if total_assets > 0 else None,
            "history_note": "尚未授权公开持仓行情，仅依据本地盈亏提示。" if histories is None else "该股历史行情不足，暂不能确认技术卖点。",
            "as_of_date": None, "available_quantity": position["available"],
            "execution_note": ("当前全部持仓不可卖；建议不代表可以立即成交。" if not position["available"]
                               else f"客户端显示可卖 {position['available']} 股，未提交任何订单。"),
        }
        if histories is not None and price > 0:
            try:
                bars = validate_price_bars(histories.get(position["symbol"], []), minimum_count=21)
                bars = [bar for bar in bars if bar.trade_date < captured.date()
                        or bar.trade_date == captured.date() and captured.time() >= time(15)]
                if len(bars) < 21 or (now.astimezone(shanghai).date() - bars[-1].trade_date).days > 7:
                    raise DataRefreshError("insufficient recent closed bars")
                advice = build_sell_advice(position["symbol"], bars, cost_price=cost,
                                          quantity=position["quantity"], current_price=price)
                reasons = advice.risks if advice.action in {"STOP_LOSS", "TAKE_PROFIT", "REDUCE"} else advice.reasons
                item.update(action=advice.action, status="READY",
                            reason=" ".join(reasons[:2]) or reason, as_of_date=bars[-1].trade_date.isoformat(),
                            decision={"STOP_LOSS": "优先控制亏损", "TAKE_PROFIT": "考虑分批止盈",
                                      "REDUCE": "考虑减仓", "HOLD": "继续持有观察",
                                      "WATCH": "警惕趋势转弱", "NO_POSITION": "仅技术风险参考"}.get(advice.action, decision),
                            history_note="已结合该股完整收盘日线；不是实时成交信号。",
                            next_step={"STOP_LOSS": "已触发亏损或技术风控条件，先核对最新价格与可卖数量，再评估降低风险。",
                                       "TAKE_PROFIT": "已有盈利且出现技术风险，结合最新价格与可卖数量评估分批止盈。",
                                       "REDUCE": "已有趋势风险，结合最新价格与仓位评估减仓。",
                                       "HOLD": "暂未触发卖出规则，继续关注亏损线及趋势变化。",
                                       "WATCH": "趋势尚需确认，关注量价变化，不据此自动下单。"}.get(advice.action, next_step))
            except (DataRefreshError, ValueError, TypeError):
                pass
        if item["position_weight"] is not None and item["position_weight"] >= .30:
            item["next_step"] += f" 单股占总资产 {_format_percent(item['position_weight'])}，需同时复核集中度。"
        items.append(item)
    return {"snapshot_id": snapshot["snapshot_id"], "calculated_at": now.isoformat(), "items": items,
            "max_loss_rate": .08, "target_profit_rate": .20}


def _technical_risk_advice(
    indicators: IndicatorResult,
    patterns: PatternResult,
    key_levels: SellKeyLevels,
    fund_flow: FundFlowResult | None,
    divergence: DivergenceResult | None,
) -> SellAdvice:
    score = 0
    reasons: list[str] = []
    risks = ["未输入持仓成本，系统只能给技术风险提示，不能判断真实止盈或止损。"]
    observations = ["输入持仓成本后，可以计算持仓收益率、止损线和止盈观察区。"]
    score = _apply_technical_sell_risks(score, indicators, patterns, risks, reasons, observations)
    score = _apply_fund_flow_sell_risk(score, fund_flow, patterns, risks, reasons, observations)
    score = _apply_divergence_sell_risk(score, divergence, risks, reasons, observations)
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
        fund_flow=fund_flow,
        divergence=divergence,
    )


def _apply_divergence_sell_risk(
    score: int,
    divergence: DivergenceResult | None,
    risks: list[str],
    reasons: list[str],
    observations: list[str],
) -> int:
    if not divergence:
        observations.append("背驰信号样本不足，卖出评分未纳入背驰风险。")
        return score
    if divergence.bearish_divergence:
        risks.append(
            f"背驰增加卖出风险：{divergence.signal}，风险调整 {divergence.bearish_risk_adjustment:+d}，"
            f"依据：{'；'.join(divergence.reasons)}"
        )
        return score + divergence.bearish_risk_adjustment
    if divergence.bullish_divergence:
        reduction = min(8, max(3, divergence.bullish_score_adjustment // 2))
        reasons.append(
            f"下跌背驰降低卖出风险：{divergence.signal}，短线下跌动能可能衰竭。"
        )
        return score - reduction
    observations.append(f"背驰信号暂未改变卖出评分：{divergence.signal}。")
    return score


def _apply_fund_flow_sell_risk(
    score: int,
    fund_flow: FundFlowResult | None,
    patterns: PatternResult,
    risks: list[str],
    reasons: list[str],
    observations: list[str],
) -> int:
    if not fund_flow:
        observations.append("资金流数据暂不可用，卖出评分未纳入资金流风险。")
        return score
    adjustment = 0
    if fund_flow.signal == "持续流出":
        adjustment += 10
    if fund_flow.main_inflow_5d < 0 and patterns.volume_drop_below_ma20:
        adjustment += 15
    if fund_flow.main_inflow_5d > 0 and patterns.pullback_low_volume:
        adjustment -= 6
    if adjustment > 0:
        risks.append(f"资金流增加卖出风险：{fund_flow.explanation}")
    elif adjustment < 0:
        reasons.append(f"资金流降低卖出风险：{fund_flow.explanation}")
    else:
        observations.append(f"资金流暂未改变卖出评分：{fund_flow.explanation}")
    return score + adjustment


def _apply_technical_sell_risks(
    score: int,
    indicators: IndicatorResult,
    patterns: PatternResult,
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
    if patterns.macd_dead_cross:
        score += 8
        risks.append("MACD 形成死叉，动能转弱，卖出风险上升。")
    elif patterns.macd_histogram_shrinking:
        score += 4
        risks.append("MACD 柱体走弱，持仓需关注动能继续衰减风险。")
    if patterns.macd_golden_cross and not patterns.macd_dead_cross:
        score -= 4
        reasons.append("MACD 金叉显示短线动能修复，暂时降低部分卖出风险。")
    if patterns.high_20d_return:
        score += 10
        observations.append("近 20 日涨幅较高，若继续放量滞涨或跌破支撑，应提高止盈优先级。")

    return score


def _sell_action(
    score: int,
    holding_return: float,
    max_loss_rate: float,
    target_profit_rate: float,
    high_position_risk: bool,
) -> str:
    if holding_return <= -abs(max_loss_rate):
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
