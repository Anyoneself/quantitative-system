from __future__ import annotations

import json

from backtest.engine import BacktestResult


def format_backtest_result(result: BacktestResult, output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    return _format_text(result)


def _format_text(result: BacktestResult) -> str:
    lines = [
        f"股票代码：{result.symbol}",
        f"回测区间：{result.start_date} 至 {result.end_date}",
        f"初始资金：{result.initial_cash:.2f}",
        f"期末权益：{result.final_equity:.2f}",
        f"总收益率：{_format_percent(result.total_return)}",
        f"年化收益率：{_format_percent(result.annualized_return)}",
        f"最大回撤：{_format_percent(result.max_drawdown)}",
        f"成交笔数：{result.trade_count}",
        f"完整交易数：{result.closed_trade_count}",
        f"未平仓头寸数：{result.open_position_count}",
        f"胜率：{_format_optional_percent(result.win_rate)}",
        f"利润因子：{_format_optional_ratio(result.profit_factor)}",
        f"单笔期望收益：{_format_optional_amount(result.expectancy)}",
        f"平均盈利：{_format_optional_amount(result.average_win)}",
        f"平均亏损：{_format_optional_amount(result.average_loss)}",
        f"夏普比率：{_format_optional_ratio(result.sharpe_ratio)}",
        f"Calmar 比率：{_format_optional_ratio(result.calmar_ratio)}",
        f"资金暴露比例：{_format_percent(result.exposure_rate)}",
        "",
        "成交记录：",
    ]
    if result.trades:
        for trade in result.trades:
            lines.append(
                f"- 信号日 {trade.signal_date}，成交日 {trade.trade_date}，"
                f"{trade.side} {trade.quantity} 股，成交价 {trade.price:.4f}，"
                f"费用 {trade.fee:.2f}，原因：{trade.reason}"
            )
    else:
        lines.append("- 无成交。")

    lines.extend(["", "完整交易："])
    if result.round_trips:
        for trade in result.round_trips:
            lines.append(
                f"- {trade.entry_date} 买入，{trade.exit_date} 卖出，持有 {trade.holding_trade_days} 个交易日，"
                f"净盈亏 {trade.pnl:.2f}，收益率 {_format_percent(trade.return_rate)}"
            )
    else:
        lines.append("- 暂无完整买卖交易。")

    lines.extend(["", "回测假设："])
    lines.extend(f"- {note}" for note in result.notes)
    return "\n".join(lines)


def _format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _format_optional_percent(value: float | None) -> str:
    if value is None:
        return "暂无已平仓交易"
    return _format_percent(value)


def _format_optional_ratio(value: float | None) -> str:
    if value is None:
        return "暂无有效样本"
    return f"{value:.4f}"


def _format_optional_amount(value: float | None) -> str:
    if value is None:
        return "暂无有效样本"
    return f"{value:.2f}"
