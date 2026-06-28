from __future__ import annotations

import json
from dataclasses import asdict

from analysis.advisor import Advice


def format_advice(advice: Advice, output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(_advice_to_dict(advice), ensure_ascii=False, indent=2)
    return _format_text(advice)


def _format_text(advice: Advice) -> str:
    lines: list[str] = []
    indicators = advice.indicators

    if indicators:
        stock_name = indicators.name or "未知"
        lines.extend(
            [
                f"股票代码：{indicators.symbol}",
                f"股票名称：{stock_name}",
                f"分析日期：{indicators.analysis_date}",
                f"数据截止日：{indicators.data_end_date}",
                f"样本区间：最近 {indicators.sample_days} 个交易日",
                f"建议：{advice.action}",
                f"综合评分：{advice.score}",
                "",
            ]
        )
    else:
        lines.extend([f"建议：{advice.action}", f"综合评分：{advice.score}", ""])

    lines.append("核心理由：")
    lines.extend(_numbered_lines(advice.reasons))
    lines.append("")
    lines.append("数据证据：")
    lines.extend(_numbered_lines(advice.evidence))
    lines.append("")
    lines.append("风险提示：")
    lines.extend(_numbered_lines(advice.risks))

    if indicators:
        lines.extend(["", "关键指标："])
        lines.extend(_indicator_lines(indicators))

    if advice.ml_prediction:
        lines.extend(["", "机器学习预测："])
        lines.extend(_ml_lines(advice.ml_prediction))

    if advice.chan_structure:
        lines.extend(["", "缠论结构辅助："])
        lines.extend(_chan_lines(advice.chan_structure))

    lines.append("")
    lines.append("后续观察：")
    lines.extend(_numbered_lines(advice.observations))
    return "\n".join(lines)


def _numbered_lines(items: list[str]) -> list[str]:
    return [f"{index}. {item}" for index, item in enumerate(items, start=1)]


def _indicator_lines(indicators) -> list[str]:
    return [
        f"- 收盘价：{indicators.close:.2f}",
        f"- 1 日涨跌幅：{_format_percent(indicators.return_1d)}",
        f"- 5 日涨跌幅：{_format_percent(indicators.return_5d)}",
        f"- 近 20 交易日涨跌幅：{_format_percent(indicators.return_20d)}",
        f"- 成交量 / 5 日均量：{indicators.volume_ratio_5d:.2f}",
        f"- 成交量 / 20 日均量：{indicators.volume_ratio_20d:.2f}",
        f"- 年线 MA250：{indicators.ma250:.2f}" if indicators.ma250 > 0 else "- 年线 MA250：数据不足",
        f"- 首日高点：{indicators.first_day_high:.2f}" if indicators.first_day_high > 0 else "- 首日高点：不适用",
        f"- 是否涨停：{_yes_no(indicators.is_limit_up)}",
        f"- 是否跌停：{_yes_no(indicators.is_limit_down)}",
    ]


def _ml_lines(prediction) -> list[str]:
    lines = [
        f"- 算法：{prediction.algorithm_name}",
        f"- 历史相似上涨占比：{_format_percent(prediction.buy_probability)}",
        f"- 训练样本数：{prediction.sample_count}",
    ]
    if prediction.neighbor_count > 0:
        lines.extend(
            [
                f"- 最近邻样本数：{prediction.neighbor_count}",
                f"- 最近邻上涨样本数：{prediction.positive_count}",
            ]
        )
    return lines


def _chan_lines(chan_structure) -> list[str]:
    lines = [
        f"- 结构状态：{chan_structure.trend}",
        f"- 当前位置：{chan_structure.position}",
        f"- 买点候选：{chan_structure.buy_signal}",
        f"- 风险结构：{chan_structure.risk_signal}",
        f"- 结构调整分：{chan_structure.score_adjustment:+d}",
        f"- 结构建议：{chan_structure.recommendation}",
    ]
    if chan_structure.center:
        lines.append(f"- 最近中枢：{chan_structure.center.lower:.2f} - {chan_structure.center.upper:.2f}")
    return lines


def _format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def advice_to_dict(advice: Advice) -> dict:
    return asdict(advice)


def _advice_to_dict(advice: Advice) -> dict:
    return advice_to_dict(advice)
