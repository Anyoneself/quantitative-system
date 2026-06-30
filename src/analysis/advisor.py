from __future__ import annotations

from dataclasses import dataclass

from analysis.chan_theory import ChanStructureResult
from analysis.divergence import DivergenceResult
from analysis.fund_flow import FundFlowResult
from analysis.indicators import IndicatorResult
from analysis.ml_model import MlPrediction
from analysis.patterns import PatternResult


@dataclass(frozen=True)
class Advice:
    action: str
    score: int
    reasons: list[str]
    evidence: list[str]
    risks: list[str]
    observations: list[str]
    indicators: IndicatorResult | None
    patterns: PatternResult | None
    ml_prediction: MlPrediction | None
    chan_structure: ChanStructureResult | None = None
    fund_flow: FundFlowResult | None = None
    divergence: DivergenceResult | None = None


def make_no_data_advice(message: str) -> Advice:
    return Advice(
        action="NO_DATA",
        score=0,
        reasons=[message],
        evidence=[],
        risks=["数据不足时不应做买卖判断。"],
        observations=["请先补充至少 21 个交易日的价格和成交量数据。"],
        indicators=None,
        patterns=None,
        ml_prediction=None,
    )


def generate_advice(
    indicators: IndicatorResult,
    patterns: PatternResult,
    ml_prediction: MlPrediction | None,
    chan_structure: ChanStructureResult | None = None,
    fund_flow: FundFlowResult | None = None,
    divergence: DivergenceResult | None = None,
) -> Advice:
    score = 0
    reasons: list[str] = []
    evidence: list[str] = []
    risks: list[str] = []
    observations: list[str] = []

    if ml_prediction:
        score = round(ml_prediction.buy_probability * 100)
        horizon_text = _horizon_text(ml_prediction.horizon_days)
        reasons.append(
            f"{ml_prediction.algorithm_name}模型基于历史量价数据，给出的{horizon_text}历史达标占比为 "
            f"{ml_prediction.buy_probability * 100:.2f}%。"
        )
        if ml_prediction.neighbor_count > 0:
            evidence.append(
                f"模型训练样本 {ml_prediction.sample_count} 个，最近邻样本 "
                f"{ml_prediction.neighbor_count} 个，其中{horizon_text}达标样本 {ml_prediction.positive_count} 个，"
                f"相似样本达标占比 {_format_percent(ml_prediction.buy_probability)}。"
            )
        else:
            evidence.append(
                f"模型训练样本 {ml_prediction.sample_count} 个，用{horizon_text}历史结果拟合当前量价特征，"
                f"输出上涨概率 {_format_percent(ml_prediction.buy_probability)}。"
            )
        if ml_prediction.buy_probability < 0.45:
            risks.append(f"机器学习模型{horizon_text}历史达标占比低于 45%，当前不建议买进。")
        elif ml_prediction.buy_probability < 0.60:
            observations.append("机器学习模型尚未给出高置信买入信号，建议等待更强确认。")
        else:
            reasons.append("机器学习模型给出较高买入概率，可进入买入观察。")
    else:
        risks.append("历史样本不足，机器学习模型无法形成可靠预测。")

    if chan_structure:
        score = _clamp_score(score + chan_structure.score_adjustment)
        evidence.append(
            f"缠论结构：{chan_structure.trend}，当前位置：{chan_structure.position}，"
            f"买点候选：{chan_structure.buy_signal}，风险结构：{chan_structure.risk_signal}，"
            f"结构调整分 {chan_structure.score_adjustment:+d}。"
        )
        if chan_structure.score_adjustment > 0:
            reasons.append(chan_structure.recommendation)
        elif chan_structure.score_adjustment < 0:
            risks.append(chan_structure.recommendation)
        else:
            observations.append(chan_structure.recommendation)

    if fund_flow:
        score = _clamp_score(score + fund_flow.fund_flow_score_adjustment)
        evidence.append(f"资金流向：{fund_flow.explanation}")
        if fund_flow.fund_flow_score_adjustment > 0:
            reasons.append(f"资金流向确认偏正面：{fund_flow.explanation}")
        elif fund_flow.fund_flow_score_adjustment < 0:
            risks.append(f"资金流向偏负面：{fund_flow.explanation}")
        else:
            observations.append(f"资金流向分歧：{fund_flow.explanation}")

    if divergence:
        score = _clamp_score(score + divergence.bullish_score_adjustment)
        evidence.append(f"背驰信号：{divergence.signal}，置信度 {divergence.confidence}，依据：{'；'.join(divergence.reasons)}")
        if divergence.bullish_divergence:
            reasons.append(
                f"出现{divergence.signal}，买入评分调整 {divergence.bullish_score_adjustment:+d}，"
                "说明下跌动能可能衰竭，可进入买入观察。"
            )
        if divergence.bearish_divergence:
            risks.append(
                f"出现{divergence.signal}，卖出风险调整 {divergence.bearish_risk_adjustment:+d}，"
                "上涨动能可能衰减，追高风险上升。"
            )

    evidence.extend(_build_evidence(indicators, patterns))

    if patterns.limit_up_low_volume:
        reasons.append(
            f"今日涨停且成交量只有 5 日均量的 {indicators.volume_ratio_5d:.2f} 倍，"
            "属于涨停缩量，卖压暂时较小。"
        )
    if patterns.breakout_with_volume:
        reasons.append(
            f"收盘价 {indicators.close:.2f} 突破前 20 日高点 {indicators.high_20d_prev:.2f}，"
            f"成交量达到 5 日均量 {indicators.volume_ratio_5d:.2f} 倍，突破有效性较强。"
        )
    if patterns.close_above_ma20:
        reasons.append(
            f"收盘价 {indicators.close:.2f} 高于 20 日均线 {indicators.ma20:.2f}，"
            f"高出 {_format_percent(_relative_gap(indicators.close, indicators.ma20))}，短期趋势偏强。"
        )
    else:
        risks.append(
            f"收盘价 {indicators.close:.2f} 低于 20 日均线 {indicators.ma20:.2f}，"
            f"低出 {_format_percent(abs(_relative_gap(indicators.close, indicators.ma20)))}，短期趋势偏弱。"
        )
    if patterns.ma20_up:
        reasons.append(
            f"20 日均线从 {indicators.ma20_prev:.2f} 升至 {indicators.ma20:.2f}，"
            f"变化 {_format_percent(_relative_gap(indicators.ma20, indicators.ma20_prev))}，中期趋势有支撑。"
        )
    else:
        risks.append(
            f"20 日均线从 {indicators.ma20_prev:.2f} 降至 {indicators.ma20:.2f}，"
            f"变化 {_format_percent(_relative_gap(indicators.ma20, indicators.ma20_prev))}，中期趋势仍需修复。"
        )
    if patterns.pullback_low_volume:
        reasons.append(
            f"当日回调 {_format_percent(indicators.return_1d)}，但成交量只有 5 日均量 "
            f"{indicators.volume_ratio_5d:.2f} 倍，且仍在 20 日均线上方，抛压相对可控。"
        )
    if patterns.macd_golden_cross_above_zero:
        score = _clamp_score(score + 6)
        reasons.append("MACD 在零轴上方形成金叉，趋势动能偏强，买入观察分提高。")
    elif patterns.macd_golden_cross:
        score = _clamp_score(score + 3)
        reasons.append("MACD 形成金叉，短线动能有修复迹象，但仍需结合价格位置和资金流确认。")
    if patterns.macd_histogram_expanding:
        score = _clamp_score(score + 3)
        reasons.append("MACD 柱体为正且继续扩大，动能正在增强。")
    if patterns.macd_dead_cross:
        score = _clamp_score(score - 6)
        risks.append("MACD 形成死叉，短线动能转弱，降低买入评分。")
    elif patterns.macd_histogram_shrinking:
        score = _clamp_score(score - 3)
        risks.append("MACD 柱体走弱，动能边际下降，需要等待进一步确认。")
    if patterns.doji_reversal:
        score = _clamp_score(score + 4)
        reasons.append(
            "低位附近出现十字星形态，说明短线多空分歧加大，可能进入止跌观察区。"
        )
    if patterns.long_lower_shadow:
        score = _clamp_score(score + 4)
        reasons.append(
            "当日 K 线出现较长下影线，盘中下探后有资金承接，短线抛压有缓和迹象。"
        )
    if patterns.low_position_rebound:
        score = _clamp_score(score + 6)
        reasons.append(
            f"价格接近 20 日低点 {indicators.low_20d_prev:.2f} 后出现止跌反弹信号，可进入反弹观察。"
        )
    if indicators.return_1d > 0 and indicators.volume_ratio_5d > 1:
        reasons.append(
            f"当日上涨 {_format_percent(indicators.return_1d)}，成交量为 5 日均量 "
            f"{indicators.volume_ratio_5d:.2f} 倍，资金参与度提升。"
        )
    if indicators.amount_ma20 <= 0:
        observations.append("当前数据缺少成交额，暂不评价成交额流动性。")
    elif patterns.low_liquidity:
        risks.append(f"20 日平均成交额约 {_format_amount(indicators.amount_ma20)}，偏低，可能存在流动性风险。")
    else:
        reasons.append(f"20 日平均成交额约 {_format_amount(indicators.amount_ma20)}，满足基础流动性要求。")
    if patterns.volume_drop_below_ma20:
        risks.append(
            f"当日下跌 {_format_percent(indicators.return_1d)} 且成交量为 5 日均量 "
            f"{indicators.volume_ratio_5d:.2f} 倍，同时跌破 20 日均线，趋势转弱风险较高。"
        )
    if patterns.high_volume_stagnation:
        risks.append(
            f"近 20 日涨幅 {_format_percent(indicators.return_20d)}，当日成交量为 5 日均量 "
            f"{indicators.volume_ratio_5d:.2f} 倍但价格变化有限，可能存在筹码松动。"
        )
    if patterns.high_20d_return:
        risks.append(f"近 20 日涨幅 {_format_percent(indicators.return_20d)}，超过 30%，继续追高的回撤风险上升。")
    if patterns.price_down_volume_up:
        risks.append(
            f"价格下跌 {_format_percent(indicators.return_1d)}，但成交量放大到 5 日均量 "
            f"{indicators.volume_ratio_5d:.2f} 倍，短线卖压上升。"
        )
    if patterns.first_day_high_breakout_with_volume:
        score = _clamp_score(score + 8)
        reasons.append(
            f"新股样本内放量突破首日高点 {indicators.first_day_high:.2f}，"
            f"成交量为 5 日均量 {indicators.volume_ratio_5d:.2f} 倍，说明上市初期压力位被资金主动突破。"
        )
    if patterns.yearline_breakout_with_volume:
        score = _clamp_score(score + 10)
        reasons.append(
            f"收盘价 {indicators.close:.2f} 放量站上年线 {indicators.ma250:.2f}，"
            f"且年线从 {indicators.ma250_prev:.2f} 抬升至 {indicators.ma250:.2f}，长期趋势出现修复信号。"
        )
    if patterns.yearline_pullback_low_volume:
        score = _clamp_score(score + 12)
        reasons.append(
            f"股价站上年线后缩量回踩，收盘价 {indicators.close:.2f} 距年线 {indicators.ma250:.2f} 较近，"
            f"成交量只有 5 日均量 {indicators.volume_ratio_5d:.2f} 倍，符合强势股回档观察思路。"
        )
    if patterns.ma250_turning_up and not patterns.yearline_breakout_with_volume:
        score = _clamp_score(score + 4)
        reasons.append(
            f"年线从 {indicators.ma250_prev:.2f} 抬升至 {indicators.ma250:.2f}，长期趋势开始向上拐头。"
        )
    if indicators.ma250 > 0 and not patterns.close_above_ma250:
        score = _clamp_score(score - 8)
        risks.append(
            f"收盘价 {indicators.close:.2f} 仍低于年线 {indicators.ma250:.2f}，"
            "长期趋势尚未修复，按强势股回档策略应先等重新站上年线。"
        )

    observations.extend(_build_observations(indicators, patterns))
    action = _map_score_to_action(score)
    reasons.insert(0, _build_action_summary(action, score, indicators, ml_prediction))
    if not reasons:
        reasons.append("当前量价结构没有出现明确买入信号。")
    if not risks:
        risks.append("未发现主要量价风险，但仍需控制单票仓位。")

    return Advice(
        action=action,
        score=score,
        reasons=reasons,
        evidence=evidence,
        risks=risks,
        observations=observations,
        indicators=indicators,
        patterns=patterns,
        ml_prediction=ml_prediction,
        chan_structure=chan_structure,
        fund_flow=fund_flow,
        divergence=divergence,
    )


def _map_score_to_action(score: int) -> str:
    if score >= 60:
        return "BUY"
    if score >= 52:
        return "WATCH"
    if score >= 45:
        return "HOLD"
    return "AVOID"


def _clamp_score(score: int) -> int:
    return max(0, min(100, score))


def _horizon_text(horizon_days: int) -> str:
    if horizon_days <= 1:
        return "次日"
    return f"未来 {horizon_days} 日"


def _build_observations(indicators: IndicatorResult, patterns: PatternResult) -> list[str]:
    observations = []
    if not patterns.breakout_with_volume:
        observations.append("观察后续是否放量突破过去 20 日高点。")
    if indicators.close >= indicators.ma20:
        observations.append("观察回调时是否继续守住 20 日均线。")
    else:
        observations.append("观察能否重新站上 20 日均线。")
    if indicators.ma250 > 0:
        if patterns.close_above_ma250:
            observations.append("观察回调时是否缩量守住年线，若放量跌回年线下方需要降低权重。")
        else:
            observations.append("观察后续能否放量站上年线，年线下方暂不按强势股处理。")
    elif indicators.first_day_high > 0:
        observations.append("观察是否能放量突破上市初期首日高点，并在突破后缩量回踩不破。")
    if patterns.limit_up_low_volume:
        observations.append("若次日放量开板并收弱，需要降低该信号权重。")
    elif patterns.low_position_rebound:
        observations.append("观察反弹信号后能否放量站回 5 日均线或 20 日均线，若继续缩量横盘则只作弱反弹处理。")
    else:
        observations.append("观察成交量是否在上涨时放大、回调时收缩。")
    return observations


def _build_action_summary(
    action: str,
    score: int,
    indicators: IndicatorResult,
    ml_prediction: MlPrediction | None,
) -> str:
    probability_text = "样本不足"
    if ml_prediction:
        probability_text = _format_percent(ml_prediction.buy_probability)
    if action == "BUY":
        return (
            f"结论：可以列入买入观察。评分 {score}，历史达标占比 {probability_text}；"
            f"当前收盘价相对 20 日均线 {_format_percent(_relative_gap(indicators.close, indicators.ma20))}，"
            f"成交量为 20 日均量 {indicators.volume_ratio_20d:.2f} 倍。"
        )
    if action == "WATCH":
        return (
            f"结论：继续观察，暂不适合无脑追买。评分 {score}，历史达标占比 {probability_text}；"
            f"需要确认价格能否站稳 20 日均线和成交量是否继续配合。"
        )
    if action == "HOLD":
        return (
            f"结论：信号中性。评分 {score}，历史达标占比 {probability_text}；"
            "当前证据不足以支持新手直接买入。"
        )
    return (
        f"结论：暂不建议买进。评分 {score}，历史达标占比 {probability_text}；"
        "当前模型或量价结构没有给出足够强的买入证据。"
    )


def _build_evidence(indicators: IndicatorResult, patterns: PatternResult) -> list[str]:
    evidence = [
        f"价格趋势：收盘价 {indicators.close:.2f}，5 日均线 {indicators.ma5:.2f}，"
        f"20 日均线 {indicators.ma20:.2f}，相对 20 日均线 "
        f"{_format_percent(_relative_gap(indicators.close, indicators.ma20))}。",
        f"K 线结构：开盘 {indicators.open:.2f}，最高 {indicators.high:.2f}，最低 {indicators.low:.2f}，收盘 {indicators.close:.2f}。",
        f"MACD：DIF {indicators.macd_dif:.4f}，DEA {indicators.macd_dea:.4f}，MACD 柱 {indicators.macd_histogram:.4f}。",
        f"涨跌幅：1 日 {_format_percent(indicators.return_1d)}，5 日 "
        f"{_format_percent(indicators.return_5d)}，20 日 {_format_percent(indicators.return_20d)}。",
        f"量能：当日成交量 {indicators.volume:.0f}，5 日均量 {indicators.volume_ma5:.0f}，"
        f"20 日均量 {indicators.volume_ma20:.0f}；当前量能分别是 5 日均量 "
        f"{indicators.volume_ratio_5d:.2f} 倍、20 日均量 {indicators.volume_ratio_20d:.2f} 倍。",
        f"位置：前 20 日高点 {indicators.high_20d_prev:.2f}，低点 {indicators.low_20d_prev:.2f}；"
        f"当前距离 20 日高点 {_format_percent(_relative_gap(indicators.close, indicators.high_20d_prev))}，"
        f"距离 20 日低点 {_format_percent(_relative_gap(indicators.close, indicators.low_20d_prev))}。",
    ]
    if indicators.ma250 > 0:
        evidence.append(
            f"年线：250 日均线 {indicators.ma250:.2f}，当前相对年线 "
            f"{_format_percent(_relative_gap(indicators.close, indicators.ma250))}。"
        )
    elif indicators.first_day_high > 0:
        evidence.append(
            f"新股位置：当前可用日线不足 250 个交易日，首日高点 {indicators.first_day_high:.2f}，"
            f"当前相对首日高点 {_format_percent(_relative_gap(indicators.close, indicators.first_day_high))}。"
        )
    if indicators.amount_ma20 > 0:
        evidence.append(f"流动性：20 日平均成交额约 {_format_amount(indicators.amount_ma20)}。")
    if patterns.close_above_ma20 and patterns.ma20_up:
        evidence.append("趋势证据：价格站上 20 日均线，且 20 日均线向上，趋势证据偏正面。")
    elif not patterns.close_above_ma20 and not patterns.ma20_up:
        evidence.append("趋势证据：价格低于 20 日均线，且 20 日均线向下，趋势证据偏负面。")
    if patterns.price_down_volume_up:
        evidence.append("卖压证据：价格下跌同时成交量放大，说明卖出力量增强。")
    if patterns.breakout_with_volume:
        evidence.append("突破证据：价格突破前 20 日高点且成交量放大，属于较强正面证据。")
    return evidence


def _relative_gap(current: float, base: float) -> float:
    if base == 0:
        return 0
    return (current - base) / base


def _format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _format_amount(value: float) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿元"
    if value >= 10_000:
        return f"{value / 10_000:.2f} 万元"
    return f"{value:.2f} 元"
