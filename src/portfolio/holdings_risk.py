from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from data.errors import DataRefreshError
from data.quality import validate_price_bars
from portfolio.risk import MINIMUM_RETURN_SAMPLES, calculate_risk


BENCHMARK = "000300"
MAX_HISTORY_HOLDINGS = 20
LOOKBACK_RETURNS = 252
SHANGHAI = ZoneInfo("Asia/Shanghai")


def analyze_holdings(snapshot, histories, *, now=None):
    """Current-weight historical scenarios, never a reconstruction of account returns."""
    now = now or datetime.now(timezone.utc)
    total = Decimal(snapshot["funds"]["total_assets"])
    market = Decimal(snapshot["funds"]["market_value"])
    positions = snapshot["positions"]
    weights = {item["symbol"]: float(Decimal(item["market_value"]) / total)
               for item in positions if total > 0 and Decimal(item["market_value"]) > 0}
    exposure = float(market / total) if total > 0 else None
    maximum = max(weights.values(), default=0) if total > 0 else None
    locked = (sum(weights.get(item["symbol"], 0) * (1 - item["available"] / item["quantity"])
                  for item in positions) if total > 0 else None)
    report = {
        **asdict(calculate_risk([])),
        "scope": "current_holdings", "status": "PARTIAL",
        "snapshot_id": snapshot["snapshot_id"], "captured_at": snapshot["captured_at"],
        "analyzed_at": now.isoformat(), "benchmark_symbol": BENCHMARK,
        "stock_exposure": exposure, "max_position_weight": maximum, "locked_weight": locked,
        "stress_loss_10": float(market * Decimal("0.10")),
        "history_curve": [], "scenario_drawdown": None, "as_of_date": None,
        "missing_symbols": [], "warnings": [],
        "risk_level": "INSUFFICIENT", "risk_level_text": "数据不足",
        "explanation": "每日按当前资产权重重放历史价格变动，资产差额收益按零计；非账户真实净值。"
                       "不计交易成本，公开复权数据未经独立核验；VaR 不是未来亏损上限。",
    }
    if not positions:
        report.update(status="EMPTY", sample_status="无股票持仓",
                      risk_level="NO_STOCK_EXPOSURE", risk_level_text="无股票敞口")
        return report
    if maximum is not None and maximum >= .25:
        report["warnings"].append("最大单股占总资产达到系统集中度提示阈值（预警 25%，高集中 50%）。")
        report.update(risk_level="HIGH" if maximum >= .5 else "WARNING",
                      risk_level_text="高集中度" if maximum >= .5 else "集中度预警")
    if total <= 0 or total < market or not weights:
        report["warnings"].append("资产分母或持仓市值不足，无法计算历史风险。")
        return report
    if len(weights) > MAX_HISTORY_HOLDINGS:
        report["warnings"].append(f"持仓超过 {MAX_HISTORY_HOLDINGS} 只，仅显示结构指标。")
        return report
    if histories is None:
        report.update(status="LOCAL_ONLY", sample_status="仅本地结构风险")
        report["warnings"].append("未启用腾讯公开行情查询，历史波动、Beta 与 VaR 暂不可用。")
        return report

    captured = datetime.fromisoformat(snapshot["captured_at"]).astimezone(SHANGHAI)
    valid = {}
    for symbol in [BENCHMARK, *weights]:
        try:
            bars = validate_price_bars(histories.get(symbol, []), minimum_count=2)
            bars = [bar for bar in bars if bar.trade_date < captured.date()
                    or bar.trade_date == captured.date() and captured.time() >= time(15)]
            if len(bars) < 2:
                raise DataRefreshError("insufficient closed bars")
            valid[symbol] = bars
        except (DataRefreshError, ValueError, TypeError):
            report["missing_symbols"].append(symbol)
    if report["missing_symbols"]:
        report["warnings"].append("部分持仓或基准行情不可用，未补零、未剔除缺失仓位。")
        return report
    benchmark = valid[BENCHMARK]
    as_of = benchmark[-1].trade_date
    report["as_of_date"] = as_of.isoformat()
    if (now.astimezone(SHANGHAI).date() - as_of).days > 7:
        report["warnings"].append("基准行情距今超过 7 个自然日，历史风险估计暂停；交易日历未独立核验。")
        return report
    # 用基准相邻交易日配对；缺行后重新起算，不能把跨日跳变视为单日收益。
    returns_by_symbol = {
        symbol: {(previous.trade_date, current.trade_date): current.close / previous.close - 1
                 for previous, current in zip(bars, bars[1:])}
        for symbol, bars in valid.items()
    }
    samples = []
    for previous, current in zip(benchmark, benchmark[1:]):
        pair = (previous.trade_date, current.trade_date)
        if any(pair not in returns_by_symbol[symbol] for symbol in weights):
            samples = []
            continue
        daily = sum(weight * returns_by_symbol[symbol][pair] for symbol, weight in weights.items())
        samples.append((pair, daily, returns_by_symbol[BENCHMARK][pair]))
    samples = samples[-LOOKBACK_RETURNS:]
    report["sample_size"] = len(samples)
    if len(samples) < MINIMUM_RETURN_SAMPLES:
        report["warnings"].append(f"连续共同日收益不足 {MINIMUM_RETURN_SAMPLES} 个，未生成历史情景。")
        return report
    equity = benchmark_equity = peak = 1.0
    drawdown = 0.0
    curve = [{"trade_date": samples[0][0][0].isoformat(), "net_value": 1, "benchmark_net_value": 1}]
    for pair, daily, benchmark_return in samples:
        equity *= 1 + daily
        benchmark_equity *= 1 + benchmark_return
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
        curve.append({"trade_date": pair[1].isoformat(), "net_value": equity,
                      "benchmark_net_value": benchmark_equity})
    risk = calculate_risk([row[1] for row in samples], [row[2] for row in samples], max_drawdown=drawdown)
    report.update(
        status="READY", annualized_volatility=risk.annualized_volatility, beta=risk.beta, var_95=risk.var_95,
        sample_status=f"当前持仓历史情景 · {len(samples)} 日", history_curve=curve,
        scenario_drawdown=abs(drawdown), reasons=risk.reasons,
    )
    levels = {"INSUFFICIENT": 0, "NORMAL": 0, "WARNING": 1, "HIGH": 2}
    if levels[risk.risk_level] >= levels[report["risk_level"]]:
        report.update(risk_level=risk.risk_level,
                      risk_level_text={"NORMAL": "未触发阈值", "WARNING": "风险预警", "HIGH": "高风险提示"}[risk.risk_level])
    if risk.beta is None:
        report["warnings"].append("基准方差为零或有效配对不足，Beta 暂不可计算。")
    return report
