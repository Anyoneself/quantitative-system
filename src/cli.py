from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from analysis.evaluation import WalkForwardEvaluation, evaluate_walk_forward
from analysis.ml_model import ALGORITHMS
from analysis.scanner import load_symbols_from_file, parse_symbols, run_entry_signal_loop, run_scan_loop
from analysis.service import analyze_stock, analyze_stock_sell
from backtest.engine import BacktestConfig, run_backtest
from backtest.formatter import format_backtest_result
from data.errors import DataRefreshError
from data.source import fetch_bars
from output.formatter import format_advice, format_sell_advice
from research.config import parse_config
from research.audit import audit_dataset
from research.dataset import build_demo_dataset, load_dataset
from research.experiment import run_experiment, save_experiment
from research.capture import capture_dataset, capture_historical_dataset
from research.dataset import canonical_json
from research.paper import (
    advance_from_public, advance_session, create_session, load_session, pause_session,
    resume_session, recover_session, get_session_view, watch_session,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "advise":
        return _run_advise(args)
    if args.command == "scan":
        return _run_scan(args)
    if args.command == "entry-scan":
        return _run_entry_scan(args)
    if args.command == "sell":
        return _run_sell(args)
    if args.command == "backtest":
        return _run_backtest(args)
    if args.command == "evaluate":
        return _run_evaluate(args)
    if args.command == "research":
        return _run_research(args)
    if args.command == "research-fetch":
        return _run_capture(args)
    if args.command == "research-audit":
        return _run_audit(args)
    if args.command == "paper":
        return _run_paper(args)

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant", description="股票量价分析 CLI")
    subparsers = parser.add_subparsers(dest="command")

    advise_parser = subparsers.add_parser("advise", help="分析单只股票并输出买卖建议")
    advise_parser.add_argument("symbol", help="股票代码，例如 600519")
    advise_parser.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="robust_ensemble", help="机器学习算法")
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
    scan_parser.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="robust_ensemble", help="机器学习算法")
    scan_parser.add_argument("--top", type=int, default=10, help="输出前 N 只股票，默认 10")
    scan_parser.add_argument("--min-score", type=int, default=52, help="最低机器学习评分，默认 52")
    scan_parser.add_argument("--interval", type=int, default=600, help="扫描间隔秒数，默认 600")
    scan_parser.add_argument("--once", action="store_true", help="只扫描一次，不循环")

    entry_scan_parser = subparsers.add_parser("entry-scan", help="扫描底背离且成交额较低的入场候选")
    entry_scan_parser.add_argument("--symbols", help="逗号分隔的股票代码，例如 601988,600519")
    entry_scan_parser.add_argument("--pool-file", help="股票池文件，每行一个股票代码")
    entry_scan_parser.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="robust_ensemble", help="机器学习算法")
    entry_scan_parser.add_argument("--top", type=int, default=10, help="输出前 N 只股票，默认 10")
    entry_scan_parser.add_argument("--max-amount", type=float, default=50_000_000, help="当日成交额上限，默认 50000000；设置为 0 表示不限制")
    entry_scan_parser.add_argument("--max-amount-ratio", type=float, default=0.8, help="当日成交额 / 20 日均成交额上限，默认 0.8")
    entry_scan_parser.add_argument("--min-confidence", type=int, default=40, help="底背离最低置信度，默认 40")
    entry_scan_parser.add_argument("--interval", type=int, default=600, help="扫描间隔秒数，默认 600")
    entry_scan_parser.add_argument("--once", action="store_true", help="只扫描一次，不循环")

    backtest_parser = subparsers.add_parser("backtest", help="回测单只股票的买卖评分策略")
    backtest_parser.add_argument("symbol", help="股票代码，例如 600519")
    backtest_parser.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="robust_ensemble", help="机器学习算法")
    backtest_parser.add_argument("--initial-cash", type=float, default=100_000, help="初始资金，默认 100000")
    backtest_parser.add_argument("--buy-score", type=int, default=60, help="买入评分阈值，默认 60")
    backtest_parser.add_argument("--sell-risk", type=int, default=45, help="卖出风险阈值，默认 45")
    backtest_parser.add_argument("--max-position-rate", type=float, default=0.95, help="最大仓位比例，默认 0.95")
    backtest_parser.add_argument("--commission-rate", type=float, default=0.0003, help="佣金比例，默认 0.0003")
    backtest_parser.add_argument("--min-commission", type=float, default=5.0, help="最低佣金，默认 5")
    backtest_parser.add_argument("--stamp-duty-rate", type=float, default=0.0005, help="卖出印花税比例，默认 0.0005")
    backtest_parser.add_argument("--slippage-rate", type=float, default=0.0005, help="滑点比例，默认 0.0005")
    backtest_parser.add_argument("--no-liquidate-at-end", action="store_true", help="期末保留持仓，不按最后收盘价强制平仓")
    backtest_parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")

    evaluate_parser = subparsers.add_parser("evaluate", help="走步验证模型并输出冻结阈值后的样本外表现")
    evaluate_parser.add_argument("symbol", help="股票代码，例如 600519")
    evaluate_parser.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="robust_ensemble", help="机器学习算法")
    evaluate_parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    research_parser = subparsers.add_parser("research", help="运行可复现的组合走步验证和成本压力测试")
    dataset_group = research_parser.add_mutually_exclusive_group(required=True)
    dataset_group.add_argument("--dataset", help="含历史证券池快照的本地 JSON 行情文件")
    dataset_group.add_argument("--demo", action="store_true", help="使用合成数据验收工程，不代表真实业绩")
    research_parser.add_argument("--config", help="研究配置 JSON，默认使用保守仓位和风险阈值")
    research_parser.add_argument("--output", default="data/experiments", help="实验留档根目录")
    research_parser.add_argument("--format", choices=["text", "json"], default="text")
    capture_parser = subparsers.add_parser("research-fetch", help="采集未复权公开行情，今日证券池不回填历史")
    capture_source = capture_parser.add_mutually_exclusive_group(required=True)
    capture_source.add_argument("--symbols")
    capture_source.add_argument("--universe", help="用户提供的历史证券池清单 JSON，需 source 与 universe")
    capture_parser.add_argument("--benchmark", default="000300")
    capture_parser.add_argument("--days", type=int, default=1200)
    capture_parser.add_argument("--output", required=True)
    audit_parser = subparsers.add_parser("research-audit", help="独立检查快照日历、覆盖与异常跳价")
    audit_parser.add_argument("--dataset", required=True)
    paper_parser = subparsers.add_parser("paper", help="冻结参数的持久化前向模拟，不连接真实交易")
    paper_commands = paper_parser.add_subparsers(dest="paper_action", required=True)
    create_parser = paper_commands.add_parser("create")
    create_source = create_parser.add_mutually_exclusive_group(required=True)
    create_source.add_argument("--symbols")
    create_source.add_argument("--dataset")
    create_parser.add_argument("--benchmark", default="000300")
    create_parser.add_argument("--config")
    for action in ("advance", "status", "pause", "resume", "recover", "watch"):
        action_parser = paper_commands.add_parser(action)
        action_parser.add_argument("session_id")
        if action not in {"status", "watch"}:
            action_parser.add_argument("--expected-revision", type=int)
        if action == "advance":
            action_parser.add_argument("--dataset")
        if action == "watch":
            action_parser.add_argument("--interval", type=int, default=1800)
            action_parser.add_argument("--once", action="store_true")
    return parser


def _run_audit(args: argparse.Namespace) -> int:
    try:
        report = audit_dataset(load_dataset(args.dataset))
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if report["status"] == "BLOCKED" else 0
    except (ValueError, OSError) as exc:
        print(f"数据体检失败：{exc}", file=sys.stderr)
        return 1


def _run_capture(args: argparse.Namespace) -> int:
    try:
        dataset = (capture_historical_dataset(json.loads(Path(args.universe).read_text(encoding="utf-8")),
                                              args.benchmark, days=args.days) if args.universe else
                   capture_dataset(parse_symbols(args.symbols), args.benchmark, days=args.days))
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 禁止覆盖冻结快照，避免用户不知情地失去原始研究输入。
        with target.open("x", encoding="utf-8") as stream:
            stream.write(canonical_json(dataset.to_dict()) + "\n")
        print(json.dumps({"path": str(target.resolve()), "data_hash": dataset.fingerprint(),
                          "known_at": dataset.universe[0].known_at.isoformat(),
                          "last_date": dataset.bars[dataset.benchmark][-1].trade_date.isoformat(),
                          "source": dataset.source}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, DataRefreshError) as exc:
        print(f"快照采集失败：{exc}", file=sys.stderr)
        return 1


def _run_paper(args: argparse.Namespace) -> int:
    try:
        if args.paper_action == "watch":
            watch_session(args.session_id, interval_seconds=args.interval, once=args.once)
            return 0
        if args.paper_action == "create":
            strategy, simulation, _ = parse_config(json.loads(Path(args.config).read_text()) if args.config else {})
            dataset = (load_dataset(args.dataset) if args.dataset else
                       capture_dataset(parse_symbols(args.symbols), args.benchmark))
            state = create_session(dataset, strategy, simulation)
        elif args.paper_action == "advance":
            state = (advance_session(args.session_id, load_dataset(args.dataset), expected_revision=args.expected_revision)
                     if args.dataset else advance_from_public(args.session_id, expected_revision=args.expected_revision))
        elif args.paper_action in {"pause", "resume", "recover"}:
            state = {"pause": pause_session, "resume": resume_session, "recover": recover_session}[args.paper_action](
                args.session_id, expected_revision=args.expected_revision)
        else:
            state = load_session(args.session_id)
        print(json.dumps(get_session_view(state["session_id"]), ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, LookupError, RuntimeError) as exc:
        print(f"前向模拟失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


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


def _run_entry_scan(args: argparse.Namespace) -> int:
    symbols = _resolve_symbols(args)
    if not symbols:
        print("请通过 --symbols 或 --pool-file 提供股票池。", file=sys.stderr)
        return 2

    invalid_symbols = [symbol for symbol in symbols if not _normalize_symbol(symbol)]
    if invalid_symbols:
        print(f"股票代码格式不正确：{', '.join(invalid_symbols)}", file=sys.stderr)
        return 2
    if args.max_amount < 0:
        print("当日成交额上限不能小于 0。", file=sys.stderr)
        return 2
    if args.max_amount_ratio <= 0:
        print("成交额比例上限必须大于 0。", file=sys.stderr)
        return 2
    if args.min_confidence < 0 or args.min_confidence > 100:
        print("底背离置信度必须在 0 到 100 之间。", file=sys.stderr)
        return 2

    run_entry_signal_loop(
        symbols=symbols,
        algorithm=args.algorithm,
        top_n=args.top,
        max_amount=args.max_amount,
        max_amount_ratio=args.max_amount_ratio,
        min_confidence=args.min_confidence,
        interval_seconds=args.interval,
        once=args.once,
    )
    return 0


def _run_backtest(args: argparse.Namespace) -> int:
    symbol = _normalize_symbol(args.symbol)
    if not symbol:
        print("股票代码格式不正确，只支持 6 位数字代码。", file=sys.stderr)
        return 2

    try:
        config = BacktestConfig(
            initial_cash=args.initial_cash,
            buy_score_threshold=args.buy_score,
            sell_risk_threshold=args.sell_risk,
            max_position_rate=args.max_position_rate,
            commission_rate=args.commission_rate,
            min_commission=args.min_commission,
            stamp_duty_rate=args.stamp_duty_rate,
            slippage_rate=args.slippage_rate,
            liquidate_at_end=not getattr(args, "no_liquidate_at_end", False),
        )
        bars = fetch_bars(symbol)
        result = run_backtest(symbol, bars, args.algorithm, config)
    except (DataRefreshError, ValueError) as exc:
        print(f"回测失败：{exc}", file=sys.stderr)
        return 1

    print(format_backtest_result(result, args.format))
    return 0


def _run_evaluate(args: argparse.Namespace) -> int:
    symbol = _normalize_symbol(args.symbol)
    if not symbol:
        print("股票代码格式不正确，只支持 6 位数字代码。", file=sys.stderr)
        return 2

    try:
        bars = fetch_bars(symbol)
        result = evaluate_walk_forward(bars, args.algorithm)
    except (DataRefreshError, ValueError) as exc:
        print(f"评估失败：{exc}", file=sys.stderr)
        return 1

    print(_format_evaluation_result(symbol, result, args.format))
    return 0


def _run_research(args: argparse.Namespace) -> int:
    try:
        dataset = build_demo_dataset() if args.demo else load_dataset(args.dataset)
        config_payload = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else {}
        strategy, simulation, validation = parse_config(config_payload)
        report = run_experiment(dataset, strategy, simulation, validation)
        output = save_experiment(report, dataset, args.output).resolve()
    except (OSError, ValueError, DataRefreshError) as exc:
        print(f"组合研究失败：{exc}", file=sys.stderr)
        return 1
    summary = {
        "report_path": str(output / "report.json"), "summary": report["out_of_sample"]["summary"],
        "research_status": report["research_status"], "release_status": report["release_status"],
        "synthetic": report["synthetic"], "live_trading_enabled": False,
    }
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        metrics = summary["summary"]
        print("\n".join([
            f"数据类型：{'合成演示，非真实业绩' if dataset.synthetic else '外部历史快照'}",
            f"样本外：{metrics['days']} 日，{metrics['trade_count']} 笔成交",
            f"成本后收益：{metrics['total_return']:.2%}；最大回撤：{metrics['max_drawdown']:.2%}",
            f"研究检查：{report['research_status']}",
            f"发布状态：{report['release_status']}；实盘交易禁用",
            f"完整报告：{output / 'report.md'}",
            f"数据、订单、净值与配置：{output}",
        ]))
    return 0


def _format_evaluation_result(
    symbol: str,
    result: WalkForwardEvaluation,
    output_format: str,
) -> str:
    if output_format == "json":
        return json.dumps({"symbol": symbol, **asdict(result)}, ensure_ascii=False, indent=2)

    test = result.test
    return "\n".join(
        [
            f"股票代码：{symbol}",
            f"算法：{ALGORITHMS[result.algorithm]}（{result.algorithm}）",
            f"预测周期：{result.horizon_days} 个交易日",
            f"验证区间：{result.validation_start_date} 至 {result.validation_end_date}",
            f"验证集冻结阈值：{_format_optional_percent(result.threshold)}",
            f"样本外区间：{result.test_start_date} 至 {result.test_end_date}",
            f"样本外 Precision：{_format_optional_percent(test.precision)}",
            (
                "Precision 95% 区间："
                f"{_format_optional_percent(test.precision_ci_low)} 至 "
                f"{_format_optional_percent(test.precision_ci_high)}"
            ),
            f"样本外基准达标率：{_format_optional_percent(test.base_rate)}",
            f"样本外达标率 Lift：{_format_optional_number(test.lift)}",
            f"样本外 Coverage：{_format_optional_percent(test.coverage)}",
            f"样本外平均净收益：{_format_optional_percent(test.average_net_return)}",
            f"样本外 Brier 分数：{test.brier_score:.6f}",
            f"样本外样本数：{test.sample_count}",
            f"样本外入选数：{test.selected_count}",
            f"样本外达标数：{test.positive_count}",
            f"发布状态：{result.release_status}",
            f"发布说明：{result.release_reason}",
        ]
    )


def _format_optional_percent(value: float | None) -> str:
    if value is None:
        return "暂无入选样本"
    return f"{value * 100:.2f}%"


def _format_optional_number(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value:.2f}"


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
