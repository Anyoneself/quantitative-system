from __future__ import annotations

from dataclasses import asdict

from analysis.evaluation import evaluate_walk_forward
from analysis.ml_model import ALGORITHMS
from backtest.engine import BacktestConfig, run_backtest
from data.source import fetch_bars


def build_evaluation_response(payload: dict) -> dict:
    symbol = _required_symbol(payload.get("symbol"))
    algorithm = _algorithm(payload.get("algorithm"))
    bars = fetch_bars(symbol)
    evaluation = evaluate_walk_forward(bars, algorithm)
    return {
        "ok": True,
        "symbol": symbol,
        "evaluation": asdict(evaluation),
        "disclaimer": "测试段仅用于评估冻结阈值，不代表未来收益，也不应据此继续调参。",
    }


def build_backtest_response(payload: dict) -> dict:
    symbol = _required_symbol(payload.get("symbol"))
    algorithm = _algorithm(payload.get("algorithm"))
    config = BacktestConfig(
        initial_cash=_float_value(payload.get("initial_cash"), 100_000.0),
        buy_score_threshold=_int_value(payload.get("buy_score"), 60),
        sell_risk_threshold=_int_value(payload.get("sell_risk"), 45),
        max_position_rate=_float_value(payload.get("max_position_rate"), 0.95),
        commission_rate=_float_value(payload.get("commission_rate"), 0.0003),
        min_commission=_float_value(payload.get("min_commission"), 5.0),
        stamp_duty_rate=_float_value(payload.get("stamp_duty_rate"), 0.0005),
        slippage_rate=_float_value(payload.get("slippage_rate"), 0.0005),
        liquidate_at_end=_bool_value(payload.get("liquidate_at_end"), True),
    )
    bars = fetch_bars(symbol)
    result = run_backtest(symbol, bars, algorithm, config)
    return {
        "ok": True,
        "backtest": result.to_dict(),
        "disclaimer": "回测已考虑基础交易成本，但仍无法完整复现历史成分股、流动性和历史 ST 状态。",
    }


def _required_symbol(value: object) -> str:
    symbol = str(value or "").strip()
    if not symbol.isdigit() or len(symbol) != 6:
        raise ValueError("股票代码必须是 6 位数字。")
    return symbol


def _algorithm(value: object) -> str:
    algorithm = str(value or "robust_ensemble")
    if algorithm not in ALGORITHMS:
        raise ValueError("机器学习算法不支持。")
    return algorithm


def _float_value(value: object, default: float) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError("数值参数不能是布尔值。")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("数值参数格式不正确。") from exc


def _int_value(value: object, default: int) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError("整数参数不能是布尔值。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("整数参数格式不正确。") from exc
    if not number.is_integer():
        raise ValueError("整数参数不能包含小数。")
    return int(number)


def _bool_value(value: object, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError("布尔参数必须是 true 或 false。")
