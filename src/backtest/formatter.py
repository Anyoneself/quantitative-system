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
        f"交易次数：{result.trade_count}",
        f"胜率：{_format_percent(result.win_rate)}",
        "",
        "成交记录：",
    ]
    if result.trades:
        for trade in result.trades:
            lines.append(
                f"- {trade.trade_date} {trade.side} {trade.quantity} 股，成交价 {trade.price:.4f}，"
                f"费用 {trade.fee:.2f}，原因：{trade.reason}"
            )
    else:
        lines.append("- 无成交。")

    lines.extend(["", "回测假设："])
    lines.extend(f"- {note}" for note in result.notes)
    return "\n".join(lines)


def _format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"
