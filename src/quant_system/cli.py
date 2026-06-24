from __future__ import annotations

import argparse
import sys
from pathlib import Path

from quant_system.analysis.ml_model import ALGORITHMS
from quant_system.analysis.scanner import load_symbols_from_file, parse_symbols, run_scan_loop
from quant_system.analysis.service import analyze_stock
from quant_system.output.formatter import format_advice


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "advise":
        return _run_advise(args)
    if args.command == "scan":
        return _run_scan(args)

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant", description="股票量价分析 CLI")
    subparsers = parser.add_subparsers(dest="command")

    advise_parser = subparsers.add_parser("advise", help="分析单只股票并输出买卖建议")
    advise_parser.add_argument("symbol", help="股票代码，例如 600519")
    advise_parser.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="knn", help="机器学习算法")
    advise_parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")

    scan_parser = subparsers.add_parser("scan", help="定时扫描股票池并输出 Top 推荐股票")
    scan_parser.add_argument("--symbols", help="逗号分隔的股票代码，例如 601988,600519")
    scan_parser.add_argument("--pool-file", help="股票池文件，每行一个股票代码")
    scan_parser.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="knn", help="机器学习算法")
    scan_parser.add_argument("--top", type=int, default=10, help="输出前 N 只股票，默认 10")
    scan_parser.add_argument("--min-score", type=int, default=52, help="最低机器学习评分，默认 52")
    scan_parser.add_argument("--interval", type=int, default=600, help="扫描间隔秒数，默认 600")
    scan_parser.add_argument("--once", action="store_true", help="只扫描一次，不循环")
    return parser


def _run_advise(args: argparse.Namespace) -> int:
    symbol = _normalize_symbol(args.symbol)
    if not symbol:
        print("股票代码格式不正确，只支持 6 位数字代码。", file=sys.stderr)
        return 2

    advice = analyze_stock(symbol, args.algorithm)
    print(format_advice(advice, args.format))
    return 0


def _run_scan(args: argparse.Namespace) -> int:
    symbols = _resolve_symbols(args)
    if not symbols:
        print("请通过 --symbols 或 --pool-file 提供股票池。", file=sys.stderr)
        return 2

    invalid_symbols = [symbol for symbol in symbols if not _normalize_symbol(symbol)]
    if invalid_symbols:
        print(f"股票代码格式不正确：{', '.join(invalid_symbols)}", file=sys.stderr)
        return 2

    run_scan_loop(
        symbols=symbols,
        algorithm=args.algorithm,
        top_n=args.top,
        min_score=args.min_score,
        interval_seconds=args.interval,
        once=args.once,
    )
    return 0


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return parse_symbols(args.symbols)
    if args.pool_file:
        return load_symbols_from_file(Path(args.pool_file))
    return []


def _normalize_symbol(symbol: str) -> str:
    cleaned = symbol.strip()
    if cleaned.isdigit() and len(cleaned) == 6:
        return cleaned
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
