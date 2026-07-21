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


@dataclass(frozen=True)
class EntrySignalResult:
    symbol: str
    name: str
    score: int
    probability: float
    current_amount: float
    amount_ma20: float
    amount_ratio_20d: float
    divergence_signal: str
    divergence_confidence: int
    reason: str
    risk: str


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
            )
        )

    results.sort(key=lambda item: (item.score, item.probability), reverse=True)
    return results[:top_n]


def scan_entry_signals(
    symbols: list[str],
    algorithm: str,
    top_n: int,
    max_amount: float,
    max_amount_ratio: float,
    min_confidence: int = 40,
    analyzer: Callable[[str, str], Advice] = analyze_stock,
) -> list[EntrySignalResult]:
    results = []
    for symbol in symbols:
        advice = analyzer(symbol, algorithm)
        if not advice.indicators or not advice.divergence:
            continue
        if not advice.divergence.bullish_divergence:
            continue
        if advice.divergence.confidence < min_confidence:
            continue
        if advice.indicators.amount <= 0 or advice.indicators.amount_ma20 <= 0:
            continue

        amount_ratio = advice.indicators.amount / advice.indicators.amount_ma20
        if amount_ratio > max_amount_ratio:
            continue
        if max_amount > 0 and advice.indicators.amount > max_amount:
            continue

        results.append(
            EntrySignalResult(
                symbol=symbol,
                name=advice.indicators.name or "未知",
                score=advice.score,
                probability=advice.ml_prediction.buy_probability if advice.ml_prediction else 0.0,
                current_amount=advice.indicators.amount,
                amount_ma20=advice.indicators.amount_ma20,
                amount_ratio_20d=amount_ratio,
                divergence_signal=advice.divergence.signal,
                divergence_confidence=advice.divergence.confidence,
                reason="；".join(advice.divergence.reasons[:2]),
                risk=advice.risks[0] if advice.risks else "未发现主要量价风险",
            )
        )

    results.sort(key=lambda item: (item.divergence_confidence, -item.amount_ratio_20d, item.score), reverse=True)
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
            ]
        )
    return "\n".join(lines)


def format_entry_signal_report(results: list[EntrySignalResult]) -> str:
    lines = [f"扫描时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "底背离低成交额候选："]
    if not results:
        lines.append("暂无符合条件的股票。")
        return "\n".join(lines)

    for index, item in enumerate(results, start=1):
        lines.extend(
            [
                f"{index}. {item.symbol} {item.name}",
                f"   信号：{item.divergence_signal}，置信度：{item.divergence_confidence}，评分：{item.score}",
                f"   成交额：{_format_amount(item.current_amount)}，20 日均额：{_format_amount(item.amount_ma20)}，比例：{item.amount_ratio_20d:.2f}",
                f"   历史达标占比：{item.probability * 100:.2f}%",
                f"   理由：{item.reason or '无明确理由'}",
                f"   风险：{item.risk}",
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


def run_entry_signal_loop(
    symbols: list[str],
    algorithm: str,
    top_n: int,
    max_amount: float,
    max_amount_ratio: float,
    min_confidence: int,
    interval_seconds: int,
    once: bool,
) -> None:
    while True:
        results = scan_entry_signals(symbols, algorithm, top_n, max_amount, max_amount_ratio, min_confidence)
        print(format_entry_signal_report(results), flush=True)
        if once:
            return
        time.sleep(interval_seconds)


def _join_scan_text(reasons: list[str], evidence: list[str]) -> str:
    items = []
    if reasons:
        items.append(reasons[0])
    items.extend(evidence[:2])
    return "；".join(items) if items else "无明确理由"


def _format_amount(value: float) -> str:
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿元"
    return f"{value / 10_000:.2f} 万元"
