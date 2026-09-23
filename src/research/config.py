from __future__ import annotations

from dataclasses import dataclass, fields
from math import isfinite


@dataclass(frozen=True)
class StrategyConfig:
    lookback: int = 60
    trend_window: int = 120
    volatility_window: int = 20
    rebalance_days: int = 20
    max_positions: int = 5
    max_weight: float = 0.20
    gross_limit: float = 0.80
    target_volatility: float = 0.12
    min_average_amount: float = 20_000_000


@dataclass(frozen=True)
class SimulationConfig:
    initial_cash: float = 100_000
    commission_rate: float = 0.0003
    min_commission: float = 5
    stamp_duty_rate: float = 0.0005
    slippage_rate: float = 0.001
    participation_rate: float = 0.01
    max_drawdown: float = 0.12
    daily_loss_limit: float = 0.03
    max_stale_days: int = 3


@dataclass(frozen=True)
class ValidationConfig:
    train_days: int = 252
    test_days: int = 63
    min_oos_days: int = 126
    min_folds: int = 3
    min_trades: int = 10
    min_sharpe: float = 0.5
    bootstrap_samples: int = 400


def parse_config(payload: dict | None = None) -> tuple[StrategyConfig, SimulationConfig, ValidationConfig]:
    payload = {} if payload is None else payload
    if not isinstance(payload, dict) or set(payload) - {"strategy", "simulation", "validation"}:
        raise ValueError("配置仅支持 strategy、simulation、validation 三个对象。")
    strategy = _build(StrategyConfig, payload.get("strategy", {}))
    simulation = _build(SimulationConfig, payload.get("simulation", {}))
    validation = _build(ValidationConfig, payload.get("validation", {}))
    validate_config(strategy, simulation, validation)
    return strategy, simulation, validation


def validate_config(
    strategy: StrategyConfig,
    simulation: SimulationConfig,
    validation: ValidationConfig | None = None,
) -> None:
    for config in (strategy, simulation, validation):
        if config is None:
            continue
        for field in fields(config):
            value = getattr(config, field.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"{field.name} 必须是有限数值。")
            if field.type in (int, "int") and not isinstance(value, int):
                raise ValueError(f"{field.name} 必须是整数。")
    for name in ("lookback", "trend_window", "volatility_window"):
        if not 2 <= getattr(strategy, name) <= 500:
            raise ValueError(f"{name} 必须在 2 到 500 之间。")
    if not 1 <= strategy.rebalance_days <= 252 or not 1 <= strategy.max_positions <= 20:
        raise ValueError("调仓周期或最大持仓数超出范围。")
    for name in ("max_weight", "gross_limit", "target_volatility"):
        if not 0 < getattr(strategy, name) <= 1:
            raise ValueError(f"{name} 必须大于 0 且不超过 1。")
    if strategy.max_weight > strategy.gross_limit:
        raise ValueError("单证券仓位上限不能高于总仓位上限。")
    if not 0 <= strategy.min_average_amount <= 1e12:
        raise ValueError("最低日均成交额超出范围。")
    if not 0 < simulation.initial_cash <= 1e12:
        raise ValueError("初始资金必须大于 0 且不超过 1 万亿元。")
    for name in ("commission_rate", "stamp_duty_rate", "slippage_rate"):
        if not 0 <= getattr(simulation, name) <= 0.10:
            raise ValueError(f"{name} 必须在 0 到 0.10 之间。")
    if not 0 <= simulation.min_commission <= 2e6:
        raise ValueError("最低佣金超出范围。")
    for name in ("participation_rate", "max_drawdown", "daily_loss_limit"):
        if not 0 < getattr(simulation, name) <= 1:
            raise ValueError(f"{name} 必须大于 0 且不超过 1。")
    if not 0 <= simulation.max_stale_days <= 20:
        raise ValueError("允许陈旧交易日数必须在 0 到 20 之间。")
    if validation is not None:
        if not 60 <= validation.train_days <= 1500 or not 20 <= validation.test_days <= 252:
            raise ValueError("训练段需 60 到 1500 日，测试段需 20 到 252 日。")
        if not 20 <= validation.min_oos_days <= 3000 or not 1 <= validation.min_folds <= 20:
            raise ValueError("样本外日数或分段数超出范围。")
        if not 1 <= validation.min_trades <= 10000 or not 0 <= validation.min_sharpe <= 10:
            raise ValueError("验收交易数或 Sharpe 阈值超出范围。")
        if not 100 <= validation.bootstrap_samples <= 2000:
            raise ValueError("重采样次数必须在 100 到 2000 之间。")


def _build(config_type, payload: dict):
    if not isinstance(payload, dict) or set(payload) - {field.name for field in fields(config_type)}:
        raise ValueError(f"{config_type.__name__} 包含未知配置字段。")
    return config_type(**payload)
