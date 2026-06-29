from __future__ import annotations

import argparse
import sys
from pathlib import Path

from analysis.ml_model import ALGORITHMS
from analysis.scanner import load_symbols_from_file, parse_symbols, run_scan_loop
from analysis.service import analyze_stock, analyze_stock_sell
from output.formatter import format_advice, format_sell_advice


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "advise":
        return _run_advise(args)
    if args.command == "scan":
        return _run_scan(args)
    if args.command == "sell":
        return _run_sell(args)

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant", description="股票量价分析 CLI")
    subparsers = parser.add_subparsers(dest="command")

    advise_parser = subparsers.add_parser("advise", help="分析单只股票并输出买卖建议")
    advise_parser.add_argument("symbol", help="股票代码，例如 600519")
    advise_parser.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="knn", help="机器学习算法")
    advise_parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")

    sell_parser = subparsers.add_parser("sell", help="分析单只股票的持仓卖出风险")
    sell_parser.add_argument("symbol", help="股票代码，例如 600519")
    sell_parser.add_argument("--cost-price", type=float, required=True, help="持仓成本价")
    sell_parser.add_argument("--quantity", type=float, help="持仓数量，可选")
    sell_parser.add_argument("--max-loss-rate", type=float, default=0.08, help="最大可承受亏损比例，默认 0.08")
    sell_parser.add_argument("--target-profit-rate", type=float, default=0.20, help="目标止盈比例，默认 0.20")
    sell_parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")

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


def _run_sell(args: argparse.Namespace) -> int:
    symbol = _normalize_symbol(args.symbol)
    if not symbol:
        print("股票代码格式不正确，只支持 6 位数字代码。", file=sys.stderr)
        return 2
    if args.cost_price <= 0:
        print("持仓成本价必须大于 0。", file=sys.stderr)
        return 2

    advice = analyze_stock_sell(
        symbol=symbol,
        cost_price=args.cost_price,
        quantity=args.quantity,
        max_loss_rate=args.max_loss_rate,
        target_profit_rate=args.target_profit_rate,
    )
    print(format_sell_advice(advice, args.format))
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
