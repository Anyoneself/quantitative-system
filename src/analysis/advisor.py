from __future__ import annotations

from dataclasses import dataclass

from analysis.chan_theory import ChanStructureResult
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
) -> Advice:
    score = 0
    reasons: list[str] = []
    evidence: list[str] = []
    risks: list[str] = []
    observations: list[str] = []

    if ml_prediction:
        score = round(ml_prediction.buy_probability * 100)
        reasons.append(
            f"{ml_prediction.algorithm_name}模型基于历史量价数据，给出的历史相似上涨占比为 "
            f"{ml_prediction.buy_probability * 100:.2f}%。"
        )
        if ml_prediction.neighbor_count > 0:
            evidence.append(
                f"模型训练样本 {ml_prediction.sample_count} 个，最近邻样本 "
                f"{ml_prediction.neighbor_count} 个，其中次日上涨样本 {ml_prediction.positive_count} 个，"
                f"相似样本上涨占比 {_format_percent(ml_prediction.buy_probability)}。"
            )
        else:
            evidence.append(
                f"模型训练样本 {ml_prediction.sample_count} 个，用历史涨跌结果拟合当前量价特征，"
                f"输出上涨概率 {_format_percent(ml_prediction.buy_probability)}。"
            )
        if ml_prediction.buy_probability < 0.45:
            risks.append("机器学习模型历史相似上涨占比低于 45%，下一个交易日不建议买进。")
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


def _build_observations(indicators: IndicatorResult, patterns: PatternResult) -> list[str]:
    observations = []
    if not patterns.breakout_with_volume:
        observations.append("观察后续是否放量突破过去 20 日高点。")
    if indicators.close >= indicators.ma20:
        observations.append("观察回调时是否继续守住 20 日均线。")
    else:
        observations.append("观察能否重新站上 20 日均线。")
    if patterns.limit_up_low_volume:
        observations.append("若次日放量开板并收弱，需要降低该信号权重。")
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
            f"结论：可以列入买入观察。评分 {score}，历史相似上涨占比 {probability_text}；"
            f"当前收盘价相对 20 日均线 {_format_percent(_relative_gap(indicators.close, indicators.ma20))}，"
            f"成交量为 20 日均量 {indicators.volume_ratio_20d:.2f} 倍。"
        )
    if action == "WATCH":
        return (
            f"结论：继续观察，暂不适合无脑追买。评分 {score}，历史相似上涨占比 {probability_text}；"
            f"需要确认价格能否站稳 20 日均线和成交量是否继续配合。"
        )
    if action == "HOLD":
        return (
            f"结论：信号中性。评分 {score}，历史相似上涨占比 {probability_text}；"
            "当前证据不足以支持新手直接买入。"
        )
    return (
        f"结论：暂不建议买进。评分 {score}，历史相似上涨占比 {probability_text}；"
        "当前模型或量价结构没有给出足够强的买入证据。"
    )


def _build_evidence(indicators: IndicatorResult, patterns: PatternResult) -> list[str]:
    evidence = [
        f"价格趋势：收盘价 {indicators.close:.2f}，5 日均线 {indicators.ma5:.2f}，"
        f"20 日均线 {indicators.ma20:.2f}，相对 20 日均线 "
        f"{_format_percent(_relative_gap(indicators.close, indicators.ma20))}。",
        f"涨跌幅：1 日 {_format_percent(indicators.return_1d)}，5 日 "
        f"{_format_percent(indicators.return_5d)}，20 日 {_format_percent(indicators.return_20d)}。",
        f"量能：当日成交量 {indicators.volume:.0f}，5 日均量 {indicators.volume_ma5:.0f}，"
        f"20 日均量 {indicators.volume_ma20:.0f}；当前量能分别是 5 日均量 "
        f"{indicators.volume_ratio_5d:.2f} 倍、20 日均量 {indicators.volume_ratio_20d:.2f} 倍。",
        f"位置：前 20 日高点 {indicators.high_20d_prev:.2f}，低点 {indicators.low_20d_prev:.2f}；"
        f"当前距离 20 日高点 {_format_percent(_relative_gap(indicators.close, indicators.high_20d_prev))}，"
        f"距离 20 日低点 {_format_percent(_relative_gap(indicators.close, indicators.low_20d_prev))}。",
    ]
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
