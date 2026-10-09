from __future__ import annotations

import json
import random
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from statistics import mean
from uuid import uuid4

from research.config import SimulationConfig, StrategyConfig, ValidationConfig, validate_config
from research.audit import audit_dataset
from research.dataset import ResearchDataset, canonical_json, parse_dataset
from research.engine import return_metrics, simulate_portfolio


def run_experiment(
    dataset: ResearchDataset,
    strategy: StrategyConfig,
    simulation: SimulationConfig,
    validation: ValidationConfig,
) -> dict:
    validate_config(strategy, simulation, validation)
    if max(simulation.commission_rate, simulation.stamp_duty_rate, simulation.slippage_rate) > 0.05 or simulation.min_commission > 1e6:
        raise ValueError("基础费率/滑点不能超过 5%，最低佣金不能超过 100 万元，以支持双倍成本压力测试。")
    dataset = parse_dataset(dataset.to_dict())
    data_audit = audit_dataset(dataset)
    if dataset.adjustment != "none":
        raise ValueError("现金与股数模拟必须使用未复权行情，不能把前复权价格当成历史成交价。")
    calendar = [bar.trade_date for bar in dataset.bars[dataset.benchmark]]
    candidate_lookbacks = sorted({max(2, strategy.lookback // 2), strategy.lookback, min(500, strategy.lookback * 2)})
    warmup = max(strategy.trend_window, strategy.volatility_window + 1, max(candidate_lookbacks) + 1)
    first_test = warmup + validation.train_days
    development_end = len(calendar) - validation.holdout_days
    if development_end - first_test < 20:
        raise ValueError(f"数据不足：含最终留出期至少需要 {first_test + 20 + validation.holdout_days} 个基准交易日，实际 {len(calendar)} 个。")
    if not dataset.members_on(calendar[warmup - 1]):
        raise ValueError("训练起点缺少当时已知的历史证券池，禁止以当前股票池回填。")
    folds, schedule = [], {}
    for offset in range(first_test, development_end, validation.test_days):
        stop = min(development_end, offset + validation.test_days)
        if development_end - stop < 20:
            stop = development_end
        train_start = calendar[offset - validation.train_days]
        train_end = calendar[offset - 1]
        candidates = []
        for lookback in candidate_lookbacks:
            candidate = replace(strategy, lookback=lookback)
            result = simulate_portfolio(dataset, candidate, simulation, start=train_start, end=train_end)
            metrics = result["summary"]
            # 参数排名只使用该测试段之前的数据；所有候选和失败结果一起留档。
            score = metrics["sharpe"] - 2 * metrics["max_drawdown"] if metrics["sharpe"] is not None else None
            candidates.append({"lookback": lookback, "score": score, "metrics": metrics})
        selected = max(candidates, key=lambda item: (item["score"] if item["score"] is not None else -1e9, -item["lookback"]))
        schedule[calendar[offset]] = replace(strategy, lookback=selected["lookback"])
        folds.append({
            "train_start": train_start.isoformat(), "train_end": train_end.isoformat(),
            "test_start": calendar[offset].isoformat(), "test_end": calendar[stop - 1].isoformat(),
            "selected_lookback": selected["lookback"], "candidates": candidates,
        })
        if stop == development_end:
            break
    start, end = calendar[first_test], calendar[development_end - 1]
    base = simulate_portfolio(dataset, strategy, simulation, start=start, end=end, schedule=schedule)
    # 压力测试冻结同一参数路径，仅翻倍费用和滑点，绝不重新选参。
    stressed_config = replace(
        simulation, commission_rate=simulation.commission_rate * 2,
        min_commission=simulation.min_commission * 2,
        stamp_duty_rate=simulation.stamp_duty_rate * 2,
        slippage_rate=simulation.slippage_rate * 2,
    )
    stress = simulate_portfolio(dataset, strategy, stressed_config, start=start, end=end, schedule=schedule)
    benchmark_rows = dataset.bars[dataset.benchmark]
    benchmark_returns = [
        right.close / left.close - 1
        for left, right in zip(benchmark_rows, benchmark_rows[1:])
        if start <= right.trade_date <= end
    ]
    benchmark = return_metrics(benchmark_returns)
    returns = [point["return"] for point in base["equity_curve"]]
    confidence = block_bootstrap_interval(returns, validation.bootstrap_samples)
    for fold in folds:
        fold["oos"] = return_metrics([
            point["return"] for point in base["equity_curve"]
            if fold["test_start"] <= point["date"] <= fold["test_end"]
        ])
    checks = _research_checks(base, stress, benchmark, confidence, folds, simulation, validation)
    # 最终留出期完全不参与选参，沿用最后冻结参数、现金、持仓和熔断状态。
    continuous = simulate_portfolio(dataset, strategy, simulation, start=start, end=calendar[-1], schedule=schedule)
    stressed_continuous = simulate_portfolio(dataset, strategy, stressed_config, start=start, end=calendar[-1], schedule=schedule)
    holdout_start = calendar[development_end].isoformat()
    holdout_curve = [point for point in continuous["equity_curve"] if point["date"] >= holdout_start]
    holdout_returns = [point["return"] for point in holdout_curve]
    holdout_metrics = return_metrics(holdout_returns)
    holdout_stress = return_metrics([point["return"] for point in stressed_continuous["equity_curve"]
                                     if point["date"] >= holdout_start])
    holdout_ci = block_bootstrap_interval(holdout_returns, validation.bootstrap_samples)
    for key, name, value, threshold, passed_check in (
        ("holdout_return", "最终留出期成本后收益", holdout_metrics["total_return"], 0, holdout_metrics["total_return"] > 0),
        ("holdout_stress", "最终留出期双倍成本收益", holdout_stress["total_return"], 0, holdout_stress["total_return"] > 0),
        ("holdout_confidence", "最终留出期收益区间下界", holdout_ci["low"], 0, holdout_ci["low"] is not None and holdout_ci["low"] > 0),
        ("no_halt", "全程未触发永久熔断", int(continuous["summary"]["halted"]), 0, not continuous["summary"]["halted"]),
        ("data_integrity", "数据完整性检查无阻断", int(data_audit["status"] == "BLOCKED"
         or bool(data_audit["missing_member_bars"])), 0,
         data_audit["status"] != "BLOCKED" and not data_audit["missing_member_bars"]),
        ("ledger_reconciliation", "模拟账本独立对账", 1, 1, all(
            result["reconciliation"]["status"] == "BALANCED" for result in (base, stress, continuous, stressed_continuous))),
    ):
        checks.append({"id": key, "name": name, "value": value, "threshold": threshold, "passed": bool(passed_check)})
    passed = all(item["passed"] for item in checks)
    limitations = [
        "合成数据不能证明任何现实市场盈利能力。" if dataset.synthetic else "外部行情和历史证券池来源为提供方声明，尚未独立核验。",
        "尚无完整历史公司行动、ST/特殊上市状态、退市清算和历史费率库。",
        "仅有日线容量近似，没有逐笔盘口、队列和冲击成本校准。",
        "尚未进行真实时间推进的前向模拟观察；本报告不是实盘准入许可。",
        "95% 历史抽样区间不是未来盈利概率；留出期不能消除反复试验及人工筛选造成的偏差。",
    ]
    if dataset.adjustment == "qfq":
        limitations.append("前复权价格用于研究，不是历史实际成交价；绝对股数、费用和涨跌停可能失真。")
    if any(point["stale_symbols"] for point in base["equity_curve"]):
        limitations.append("样本外存在缺失持仓行情，使用最近已知价格估值，不能视为可变现权益。")
    return {
        "schema_version": 1,
        "strategy": "momentum_trend_inverse_volatility",
        "data_hash": dataset.fingerprint(), "data_source": dataset.source, "data_audit": data_audit,
        "synthetic": dataset.synthetic, "adjustment": dataset.adjustment,
        "config": {"strategy": asdict(strategy), "simulation": asdict(simulation), "validation": asdict(validation)},
        "candidate_lookbacks": candidate_lookbacks, "folds": folds,
        "out_of_sample": base, "cost_stress": stress,
        "holdout": {
            "start": holdout_start, "end": calendar[-1].isoformat(),
            "frozen_strategy": asdict(schedule[max(schedule)]),
            "summary": holdout_metrics, "cost_stress": holdout_stress,
            "mean_daily_return_ci95": holdout_ci, "equity_curve": holdout_curve,
            "orders": [order for order in continuous["orders"] if order["trade_date"] >= holdout_start],
            "risk_events": [event for event in continuous["risk_events"] if event["date"] >= holdout_start],
        },
        "benchmark": {"symbol": dataset.benchmark, "cost_basis": "gross_close_to_close", **benchmark},
        "cash_benchmark_return": 0.0, "mean_daily_return_ci95": confidence,
        "checks": checks,
        "research_status": "PASSED_RESEARCH_CHECKS" if passed else "FAILED_RESEARCH_CHECKS",
        "release_status": "BLOCKED_SYNTHETIC" if dataset.synthetic else "BLOCKED_DATA_AND_FORWARD_TEST",
        "live_trading_enabled": False, "limitations": limitations,
        "references": [{
            "title": "The Probability of Backtest Overfitting",
            "authors": "Bailey, Borwein, Lopez de Prado, Zhu",
            "date": "2015-02-27",
            "url": "https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf",
            "application": "限制候选数量、保留全部试验及失败结果；留出期不构成多重试验校正。本系统未实现该文 CSCV/PBO 估计。",
        }],
        "protocol": [
            "候选动量窗口在实验前固定为基准窗口的 0.5/1/2 倍；训练段按 Sharpe - 2×回撤排名。",
            "滚动训练，下一测试段冻结参数；样本外现金、持仓、费用和熔断状态连续，不在分段边界重置。",
            "日收益置信区间为固定随机种子的 20 日循环分块重采样，仅表达历史抽样不确定性。",
            "基准为同区间收盘到收盘毛收益，不扣费用；不把它当成可成交的组合。",
            "所有模拟订单均不发送到券商；观察测试结果后修改策略，会消耗该数据的样本外资格。",
            "最终留出期在滚动研究之后，仅评估最后冻结的参数；资金与风险状态不重置。反复查看后调参会使留出期失效。",
        ],
    }


def block_bootstrap_interval(returns: list[float], samples: int, seed: int = 230923) -> dict:
    if len(returns) < 20:
        return {"low": None, "high": None, "block_days": 20, "samples": samples}
    randomizer = random.Random(seed)
    estimates = []
    for _ in range(samples):
        sampled = []
        while len(sampled) < len(returns):
            start = randomizer.randrange(len(returns))
            sampled.extend(returns[(start + index) % len(returns)] for index in range(20))
        estimates.append(mean(sampled[:len(returns)]))
    estimates.sort()
    return {
        "low": estimates[int((samples - 1) * 0.025)],
        "high": estimates[int((samples - 1) * 0.975)],
        "block_days": 20, "samples": samples,
    }


def save_experiment(report: dict, dataset: ResearchDataset, root: str | Path = "data/experiments") -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12]
    pending = root / f".{run_id}.pending"
    target = root / run_id
    pending.mkdir()
    snapshot = canonical_json(dataset.to_dict())
    if sha256(snapshot.encode()).hexdigest() != report["data_hash"]:
        raise ValueError("报告数据指纹与待存档快照不一致。")
    payload = {**report, "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
               "code_hash": _code_hash()}
    prior_uses = 0
    for previous_path in root.glob("*/report.json"):
        if previous_path.parent.name.startswith("."):
            continue
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        if previous.get("data_hash") == report["data_hash"]:
            prior_uses += 1
    payload["prior_reports_for_same_data"] = prior_uses
    if prior_uses:
        payload["limitations"] = [*payload["limitations"],
                                 f"本数据快照已有 {prior_uses} 份实验报告；再次查看或调参后不能声称留出期仍未被使用。"]
    (pending / "dataset.json").write_text(snapshot + "\n", encoding="utf-8")
    (pending / "config.json").write_text(json.dumps(report["config"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (pending / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (pending / "report.md").write_text(_markdown_report(payload), encoding="utf-8")
    manifest = {path.name: sha256(path.read_bytes()).hexdigest() for path in sorted(pending.iterdir())}
    (pending / "manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    # 完整目录一次改名发布，读取方不会把中断留下的半份报告当成成功实验。
    pending.rename(target)
    return target


def _code_hash() -> str:
    source = Path(__file__).resolve().parents[1]
    hasher = sha256()
    for path in sorted(source.rglob("*.py")):
        hasher.update(path.relative_to(source).as_posix().encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _research_checks(base, stress, benchmark, confidence, folds, simulation, validation) -> list[dict]:
    metrics = base["summary"]
    definitions = [
        ("oos_days", "样本外日数", metrics["days"], validation.min_oos_days, metrics["days"] >= validation.min_oos_days),
        ("folds", "独立时间测试分段", len(folds), validation.min_folds, len(folds) >= validation.min_folds),
        ("trades", "样本外成交笔数", metrics["trade_count"], validation.min_trades, metrics["trade_count"] >= validation.min_trades),
        ("net_return", "成本后收益为正", metrics["total_return"], 0, metrics["total_return"] > 0),
        ("benchmark", "超过同期基准毛收益", metrics["total_return"] - benchmark["total_return"], 0,
         metrics["total_return"] > benchmark["total_return"]),
        ("sharpe", "样本外 Sharpe", metrics["sharpe"], validation.min_sharpe,
         metrics["sharpe"] is not None and metrics["sharpe"] >= validation.min_sharpe),
        ("drawdown", "最大回撤", metrics["max_drawdown"], simulation.max_drawdown,
         metrics["max_drawdown"] <= simulation.max_drawdown),
        ("cost_stress", "双倍费用滑点后收益", stress["summary"]["total_return"], 0, stress["summary"]["total_return"] > 0),
        ("confidence", "日均收益 95% 区间下界", confidence["low"], 0, confidence["low"] is not None and confidence["low"] > 0),
        ("positive_folds", "盈利测试段比例", sum(fold["oos"]["total_return"] > 0 for fold in folds) / len(folds), 0.5,
         sum(fold["oos"]["total_return"] > 0 for fold in folds) / len(folds) > 0.5),
        ("fresh_marks", "无陈旧持仓估值", sum(bool(point["stale_symbols"]) for point in base["equity_curve"]), 0,
         not any(point["stale_symbols"] for point in base["equity_curve"])),
    ]
    return [{"id": key, "name": name, "value": value, "threshold": threshold, "passed": bool(passed)}
            for key, name, value, threshold, passed in definitions]


def _markdown_report(report: dict) -> str:
    metrics = report["out_of_sample"]["summary"]
    lines = [
        "# 组合研究实验", "", f"- 实验编号：`{report['run_id']}`",
        f"- 数据类型：{'合成演示，非真实业绩' if report['synthetic'] else '外部历史数据'}",
        f"- 数据指纹：`{report['data_hash']}`", f"- 源码指纹：`{report['code_hash']}`",
        f"- 研究验收：`{report['research_status']}`", f"- 发布状态：`{report['release_status']}`",
        f"- 样本外收益：{metrics['total_return']:.2%}", f"- 最大回撤：{metrics['max_drawdown']:.2%}",
        f"- 实际费用：{metrics['fees']:.2f} 元", "", "## 验收检查", "",
        "| 检查 | 结果 | 数值 |", "|---|---|---|",
    ]
    lines.extend(f"| {item['name']} | {'通过' if item['passed'] else '未通过'} | {item['value']} |" for item in report["checks"])
    lines.extend(["", "## 数据体检", "", f"- 结论：{report['data_audit']['status']}",
                  f"- 日历：{report['data_audit']['calendar_status']}",
                  f"- 缺失基准日：{len(report['data_audit']['missing_benchmark_dates'])}",
                  f"- 异常价格跳变：{report['data_audit']['price_jump_count']}",
                  "", "## 样本外账本对账与盈亏", "",
                  f"- 对账：{report['out_of_sample']['reconciliation']['status']}",
                  "| 证券 | 已实现盈亏 | 未实现盈亏 | 净盈亏 | 费用 |", "|---|---|---|---|---|"])
    for item in report["out_of_sample"]["reconciliation"]["attribution"]:
        lines.append(f"| {item['symbol']} | {item['realized_pnl']:.2f} | {item['unrealized_pnl']:.2f} | "
                     f"{item['net_pnl']:.2f} | {item['fees']:.2f} |")
    lines.extend(["", "## 方法", ""] + [f"- {item}" for item in report["protocol"]])
    if report.get("holdout"):
        holdout = report["holdout"]
        lines.extend(["", "## 最终留出期", "",
                      f"- 日期：{holdout['start']} 至 {holdout['end']}",
                      f"- 成本后收益：{holdout['summary']['total_return']:.2%}",
                      f"- 双倍成本收益：{holdout['cost_stress']['total_return']:.2%}",
                      f"- 最大回撤：{holdout['summary']['max_drawdown']:.2%}"])
    lines.extend(["", "## 尚未解决的限制", ""] + [f"- {item}" for item in report["limitations"]])
    lines.extend(["", "## 调研依据", ""] + [
        f"- {item['title']} ({item['date']})：{item['url']}；{item['application']}"
        for item in report.get("references", [])
    ])
    return "\n".join(lines) + "\n"
