from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from analysis.advisor import Advice
from analysis.service import analyze_stock


@dataclass(frozen=True)
class ScanResult:
    symbol: str
    name: str
    action: str
    score: int
    probability: float
    reason: str
    risk: str
    chan_summary: str = ""


def load_symbols_from_file(path: Path) -> list[str]:
    symbols = []
    for line in path.read_text(encoding="utf-8").splitlines():
        symbol = line.strip()
        if not symbol or symbol.startswith("#"):
            continue
        symbols.append(symbol)
    return symbols


def parse_symbols(value: str) -> list[str]:
    return [symbol.strip() for symbol in value.split(",") if symbol.strip()]


def scan_top_stocks(
    symbols: list[str],
    algorithm: str,
    top_n: int,
    min_score: int = 52,
    analyzer: Callable[[str, str], Advice] = analyze_stock,
) -> list[ScanResult]:
    results = []
    for symbol in symbols:
        advice = analyzer(symbol, algorithm)
        if not advice.indicators or not advice.ml_prediction:
            continue
        if advice.score < min_score:
            continue
        results.append(
            ScanResult(
                symbol=symbol,
                name=advice.indicators.name or "未知",
                action=advice.action,
                score=advice.score,
                probability=advice.ml_prediction.buy_probability,
                reason=_join_scan_text(advice.reasons, advice.evidence),
                risk=advice.risks[0] if advice.risks else "未发现主要量价风险",
                chan_summary=_chan_scan_summary(advice),
            )
        )

    results.sort(key=lambda item: (item.score, item.probability), reverse=True)
    return results[:top_n]


def format_scan_report(results: list[ScanResult]) -> str:
    lines = [f"扫描时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "Top 推荐股票："]
    if not results:
        lines.append("暂无可推荐股票。")
        return "\n".join(lines)

    for index, item in enumerate(results, start=1):
        lines.extend(
            [
                f"{index}. {item.symbol} {item.name}",
                f"   建议：{item.action}，评分：{item.score}，历史达标占比：{item.probability * 100:.2f}%",
                f"   理由：{item.reason}",
                f"   风险：{item.risk}",
                f"   缠论：{item.chan_summary or '无结构摘要'}",
            ]
        )
    return "\n".join(lines)


def run_scan_loop(
    symbols: list[str],
    algorithm: str,
    top_n: int,
    min_score: int,
    interval_seconds: int,
    once: bool,
) -> None:
    while True:
        results = scan_top_stocks(symbols, algorithm, top_n, min_score)
        print(format_scan_report(results), flush=True)
        if once:
            return
        time.sleep(interval_seconds)


def _join_scan_text(reasons: list[str], evidence: list[str]) -> str:
    items = []
    if reasons:
        items.append(reasons[0])
    items.extend(evidence[:2])
    return "；".join(items) if items else "无明确理由"


def _chan_scan_summary(advice: Advice) -> str:
    if not advice.chan_structure:
        return ""
    chan = advice.chan_structure
    return f"{chan.trend} / {chan.position} / {chan.buy_signal} / {chan.risk_signal} / 调整{chan.score_adjustment:+d}"
