from __future__ import annotations

import os
import re
import time
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from data.errors import DataRefreshError
from research.audit import audit_dataset, reconcile_result
from research.capture import capture_dataset, merge_observation
from research.config import SimulationConfig, StrategyConfig, parse_config, validate_config
from research.dataset import ResearchDataset, canonical_json, parse_dataset
from research.engine import simulate_portfolio
from research.experiment import _code_hash
from research.monitoring import build_monitoring
from research.operations import operational_status, write_heartbeat
from research.session_store import (
    locked as _locked, save_state as _save, read_state, latest_snapshot,
)
from research.strategy import target_weights

_LOADED_CODE_HASH = _code_hash()


def _check_code(state: dict | None = None) -> None:
    if _code_hash() != _LOADED_CODE_HASH:
        raise ValueError("代码已改变，必须重启服务后创建新的独立模拟。")
    if state is not None and state["code_hash"] != _LOADED_CODE_HASH:
        raise ValueError("代码已改变，旧模拟不得使用新代码重算；请创建新模拟。")


def paper_root() -> Path:
    return Path(os.environ.get("QUANT_PAPER_DIR", "data/paper"))


def _now(value: datetime | None) -> datetime:
    if value is not None and value.tzinfo is None:
        raise ValueError("观察时间必须包含时区。")
    return (value or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(ZoneInfo("Asia/Shanghai"))


def _directory(session_id: str, root: str | Path) -> Path:
    if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
        raise ValueError("前向模拟编号格式错误。")
    return Path(root) / session_id


def load_session(session_id: str, root: str | Path | None = None) -> dict:
    path = _directory(session_id, root or paper_root())
    with _locked(path, shared=True):
        return read_state(path)


def get_session_view(session_id: str, root: str | Path | None = None, *, now: datetime | None = None) -> dict:
    path = _directory(session_id, root or paper_root())
    with _locked(path, shared=True):
        return session_view(read_state(path), root=root, now=now)


def session_view(state: dict, *, root: str | Path | None = None, now: datetime | None = None) -> dict:
    # 返回展示数据，不反复向浏览器发送整个冻结行情集。
    view = {key: value for key, value in state.items() if key != "dataset"}
    view["monitoring"] = build_monitoring(state)
    view["evidence"] = view["monitoring"]["evidence"]
    view["operations"] = operational_status(
        state, _directory(state["session_id"], root or paper_root()), _now(now),
        code_changed=state["code_hash"] != _LOADED_CODE_HASH or _code_hash() != _LOADED_CODE_HASH,
    )
    return view


def list_sessions(root: str | Path | None = None) -> list[dict]:
    root = Path(root or paper_root())
    entries = []
    directories = [path for path in root.glob("*") if path.is_dir() and re.fullmatch(r"[0-9a-f]{32}", path.name)]
    for path in sorted(directories, key=lambda item: item.stat().st_mtime, reverse=True)[:50]:
        try:
            state = load_session(path.name, root)
            entries.append({key: state[key] for key in
                            ("session_id", "created_at", "last_date", "status", "qualified_days", "release_status")})
        except RuntimeError:
            entries.append({"session_id": path.name, "busy": True, "message": "正在更新，请稍后刷新"})
        except (ValueError, OSError, LookupError):
            entries.append({"session_id": path.name, "error": "模拟账本校验失败，需检查或显式恢复"})
    return entries


def _require_today(dataset: ResearchDataset, now: datetime) -> None:
    if dataset.synthetic or dataset.adjustment != "none":
        raise ValueError("前向模拟只接受真实市场未复权数据，合成样本不能作为前向证据。")
    if now.hour < 15 or dataset.bars[dataset.benchmark][-1].trade_date != now.date():
        raise ValueError("前向观察必须使用上海市场今日收盘后的基准日线，不能用陈旧或盘中行情。")
    if any(snapshot.known_at > now.date() or snapshot.effective_date > now.date() for snapshot in dataset.universe):
        raise ValueError("前向登记不接受未来证券池快照。")


def create_session(
    dataset: ResearchDataset, strategy: StrategyConfig, simulation: SimulationConfig,
    root: str | Path | None = None, *, now: datetime | None = None,
) -> dict:
    now = _now(now)
    _check_code()
    validate_config(strategy, simulation)
    dataset = parse_dataset(dataset.to_dict())
    _require_today(dataset, now)
    data_audit = audit_dataset(dataset)
    if data_audit["status"] == "BLOCKED":
        raise ValueError("数据体检未通过：请核对日历、价格跳变及成交口径。")
    last_date = dataset.bars[dataset.benchmark][-1].trade_date
    members = dataset.members_on(last_date)
    minimum = max(strategy.lookback + 1, strategy.trend_window, strategy.volatility_window + 1)
    if not members:
        raise ValueError("登记日缺少已知证券池。")
    for symbol in members:
        if len(dataset.bars[symbol]) < minimum or dataset.bars[symbol][-1].trade_date != last_date:
            raise ValueError(f"{symbol} 缺少足够历史或今日已完成日线。")
    session_id = uuid4().hex
    result = {"summary": {"days": 0, "initial_cash": simulation.initial_cash,
                         "final_equity": simulation.initial_cash, "total_return": 0, "max_drawdown": 0,
                         "sharpe": None, "trade_count": 0, "fees": 0, "halted": False},
              "equity_curve": [], "orders": [], "decisions": [], "risk_events": [], "terminal_positions": {}}
    result["reconciliation"] = reconcile_result(dataset, result, simulation)
    state = {
        "schema_version": 1, "session_id": session_id, "created_at": now.isoformat(), "updated_at": now.isoformat(),
        "start_after": last_date.isoformat(), "last_date": last_date.isoformat(),
        "code_hash": _LOADED_CODE_HASH, "config": {"strategy": asdict(strategy), "simulation": asdict(simulation)},
        "dataset": dataset.to_dict(), "data_hash": dataset.fingerprint(), "data_audit": data_audit,
        "status": "ACTIVE", "result": result, "observations": [], "qualified_days": 0,
        "has_replay_gap": False, "live_trading_enabled": False, "release_status": "BLOCKED_FORWARD_EVIDENCE",
        "last_error": None, "next_intent": _next_intent(dataset, strategy, simulation, result, now),
        "limitations": ["仅为日线前向模拟，不是券商成交或收益承诺。",
                        "冻结的是代码、参数和信号；填单仍采用日线开盘与容量假设。",
                        "公司行动、证券状态及完整交易日历尚未独立核验，实盘始终禁用。",
                        "本地哈希仅检查意外改写，不是第三方可信时间戳或防篡改审计。"],
    }
    path = _directory(session_id, root or paper_root())
    path.mkdir(parents=True)
    _save(path, state, event="CREATE")
    return state


def _next_intent(dataset, strategy, simulation, result, now):
    curve = result["equity_curve"]
    day = dataset.bars[dataset.benchmark][-1].trade_date
    risk_off = result["summary"]["halted"] or bool(curve and curve[-1]["return"] <= -simulation.daily_loss_limit)
    last = result["decisions"][-1] if result["decisions"] else None
    last_offset = next((index for index, point in enumerate(curve) if last and point["date"] == last["execution_date"]), -10000)
    members = dataset.members_on(day)
    removed = set(result["terminal_positions"]) - set(members)
    rebalance = risk_off or len(curve) - last_offset >= strategy.rebalance_days or bool(removed)
    weights = ({} if risk_off else target_weights(dataset.bars, members, day, strategy)) if rebalance else last["weights"]
    return {"signal_date": day.isoformat(), "recorded_at": now.isoformat(), "rebalance": rebalance,
            "weights": weights, "risk_off": risk_off}


def advance_session(
    session_id: str, incoming: ResearchDataset, root: str | Path | None = None,
    *, now: datetime | None = None, expected_revision: int | None = None,
) -> dict:
    root = Path(root or paper_root())
    with _locked(_directory(session_id, root)):
        state = read_state(_directory(session_id, root))
        _check_revision(state, expected_revision)
        clock = _now(now)
        try:
            return _advance(deepcopy(state), incoming, root, clock)
        except (ValueError, OSError, DataRefreshError) as exc:
            _record_failure(session_id, root, exc, clock)
            raise


def _record_failure(session_id, root, error, now):
    try:
        state = read_state(_directory(session_id, root))
        message = str(error)[:2000]
        if state.get("last_error") == message:
            return
        state.update(last_error=message, updated_at=now.isoformat())
        _save(_directory(session_id, root), state, event="OBSERVATION_FAILED")
    except (ValueError, OSError, LookupError):
        # 不以诊断日志覆盖中断发布留下的证据；调用方仍抛出原始错误。
        pass


def _check_revision(state: dict, expected_revision: int | None) -> None:
    if expected_revision is None:
        return
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("预期版本必须为非负整数。")
    if expected_revision != state.get("storage_revision", 0):
        raise RuntimeError("账本版本已更新，请刷新状态后再操作。")


def _resolve_intent(intent: dict, first: dict | None, day: str, result: dict) -> str:
    if first is None and not intent["rebalance"]:
        return "NO_REBALANCE"
    if first and first["signal_date"] == intent["signal_date"]:
        if intent["rebalance"] and first["weights"] == intent["weights"]:
            return "FROZEN_SIGNAL"
        overrides = [event for event in result["risk_events"]
                     if event["date"] == day and event["kind"] == "STALE_DATA_HALT" and event.get("symbols")]
        day_orders = [order for order in result["orders"] if order["trade_date"] == day]
        # 新发生的行情风控仅能将目标降到零，不能借此授权未登记的买入。
        if (first["halted"] is True and first["weights"] == {} and overrides
                and all(order["side"] == "SELL" for order in day_orders)):
            return "RISK_REDUCTION"
    raise ValueError("实际模拟决策与预先冻结的信号不一致。")


def _advance(state, incoming, root, now):
    if state["status"] == "PAUSED":
        raise ValueError("模拟已暂停；需显式继续观察才能推进。")
    _check_code(state)
    incoming = parse_dataset(incoming.to_dict())
    _require_today(incoming, now)
    dataset = merge_observation(parse_dataset(state["dataset"]), incoming)
    data_audit = audit_dataset(dataset)
    if data_audit["status"] == "BLOCKED":
        raise ValueError("数据体检未通过：日历缺日或价格跳变需人工核查，账本未推进。")
    dates = [bar.trade_date for bar in dataset.bars[dataset.benchmark] if bar.trade_date.isoformat() > state["last_date"]]
    if not dates:
        return state
    strategy, simulation, _ = parse_config(state["config"])
    start = next(bar.trade_date for bar in dataset.bars[dataset.benchmark] if bar.trade_date.isoformat() > state["start_after"])
    result = simulate_portfolio(dataset, strategy, simulation, start=start)
    old = state["result"]
    for key, date_field in (("equity_curve", "date"), ("orders", "trade_date"),
                            ("decisions", "execution_date"), ("risk_events", "date")):
        if [row for row in result[key] if row[date_field] <= state["last_date"]] != old[key]:
            raise ValueError("历史模拟结果不一致，账本拒绝更新。")
    first = next((row for row in result["decisions"] if row["execution_date"] == dates[0].isoformat()), None)
    intent = state["next_intent"]
    resolution = _resolve_intent(intent, first, dates[0].isoformat(), result)
    qualified = len(dates) == 1 and datetime.fromisoformat(intent["recorded_at"]).date() < dates[0]
    state["has_replay_gap"] = state["has_replay_gap"] or not qualified
    state["qualified_days"] += 1 if qualified else 0
    state["observations"].append({
        "observed_at": now.isoformat(), "date": dates[-1].isoformat(),
        "mode": "FORWARD" if qualified else "CATCH_UP", "days": len(dates),
        "prior_intent_hash": sha256(canonical_json(intent).encode()).hexdigest(),
        "data_hash": dataset.fingerprint(), "source": incoming.source,
        "intent_resolution": resolution,
        "risk_overrides": [event for event in result["risk_events"] if event["date"] == dates[0].isoformat()]
        if resolution == "RISK_REDUCTION" else [],
    })
    state.update({
        "dataset": dataset.to_dict(), "data_hash": dataset.fingerprint(), "result": result, "data_audit": data_audit,
        "last_date": dates[-1].isoformat(), "updated_at": now.isoformat(), "last_error": None,
        "status": "HALTED" if result["summary"]["halted"] else "ACTIVE",
        "next_intent": _next_intent(dataset, strategy, simulation, result, now),
    })
    state["evidence"] = _evidence(state)
    _save(_directory(state["session_id"], root), state, event="ADVANCE")
    return state


def _evidence(state):
    return build_monitoring(state)["evidence"]


def pause_session(session_id: str, root: str | Path | None = None, *, now: datetime | None = None,
                  expected_revision: int | None = None) -> dict:
    root = Path(root or paper_root())
    with _locked(_directory(session_id, root)):
        state = read_state(_directory(session_id, root))
        _check_revision(state, expected_revision)
        if state["status"] == "PAUSED":
            return state
        state.update(status="PAUSED", updated_at=_now(now).isoformat())
        _save(_directory(session_id, root), state, event="PAUSE")
        return state


def resume_session(session_id: str, root: str | Path | None = None, *, now: datetime | None = None,
                   expected_revision: int | None = None) -> dict:
    root = Path(root or paper_root())
    with _locked(_directory(session_id, root)):
        state = read_state(_directory(session_id, root))
        _check_revision(state, expected_revision)
        _check_code(state)
        if state["status"] != "PAUSED":
            return state
        clock = _now(now)
        dataset = parse_dataset(state["dataset"])
        strategy, simulation, _ = parse_config(state["config"])
        state["result"]["reconciliation"] = reconcile_result(dataset, state["result"], simulation)
        state.update(status="HALTED" if state["result"]["summary"]["halted"] else "ACTIVE",
                     updated_at=clock.isoformat(), last_error=None, has_replay_gap=True)
        state["next_intent"] = _next_intent(dataset, strategy, simulation, state["result"], clock)
        state["evidence"] = _evidence(state)
        _save(_directory(session_id, root), state, event="RESUME")
        return state


def recover_session(session_id: str, root: str | Path | None = None, *, now: datetime | None = None,
                    expected_revision: int | None = None) -> dict:
    path = _directory(session_id, root or paper_root())
    with _locked(path):
        envelope = latest_snapshot(path)
        state = deepcopy(envelope["state"])
        _check_revision(state, expected_revision)
        dataset = parse_dataset(state["dataset"])
        _, simulation, _ = parse_config(state["config"])
        reconcile_result(dataset, state["result"], simulation)
        state.update(status="PAUSED", has_replay_gap=True, updated_at=_now(now).isoformat(),
                     last_error="已恢复最新完整版本，模拟保持暂停；需核查后单独继续观察。")
        state["evidence"] = _evidence(state)
        _save(path, state, event="RECOVER", recovery=True)
        return state


def advance_from_public(session_id: str, root: str | Path | None = None, *,
                        expected_revision: int | None = None) -> dict:
    root = Path(root or paper_root())
    path = _directory(session_id, root)
    with _locked(path):
        state = read_state(path)
        _check_revision(state, expected_revision)
        try:
            if state["status"] == "PAUSED":
                raise ValueError("模拟已暂停。")
            _check_code(state)
            dataset = parse_dataset(state["dataset"])
            incoming = capture_dataset([symbol for symbol in dataset.bars if symbol != dataset.benchmark],
                                       dataset.benchmark, days=3000)
            incoming = replace(incoming, calendar=dataset.calendar)
            return _advance(deepcopy(state), incoming, root, _now(None))
        except (ValueError, OSError, DataRefreshError) as exc:
            _record_failure(session_id, root, exc, _now(None))
            raise


def watch_session(session_id: str, *, interval_seconds: int = 1800, once: bool = False, emit=print) -> None:
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or not 60 <= interval_seconds <= 86400:
        raise ValueError("观察间隔必须在 60 到 86400 秒之间。")
    path = _directory(session_id, paper_root())
    worker_id = uuid4().hex
    with _locked(path, ".watch.lock"):
        try:
            while True:
                try:
                    state = load_session(session_id)
                except RuntimeError:
                    write_heartbeat(path, worker_id, interval_seconds, "WAITING", _now(None))
                    emit(canonical_json({"session_id": session_id, "status": "BUSY"}))
                    if once:
                        return
                    time.sleep(interval_seconds)
                    continue
                if state["status"] == "PAUSED":
                    emit(canonical_json({"session_id": session_id, "status": "PAUSED"}))
                    return
                _check_code(state)
                now = _now(None)
                write_heartbeat(path, worker_id, interval_seconds, "WAITING", now)
                calendar = state["dataset"].get("calendar")
                trading_day = now.date().isoformat() in calendar["sessions"] if calendar else now.weekday() < 5
                if calendar and calendar["sessions"][-1] < now.date().isoformat():
                    write_heartbeat(path, worker_id, interval_seconds, "ERROR", now)
                    emit(canonical_json({"session_id": session_id, "status": "CALENDAR_EXPIRED",
                                         "error": "声明日历已用尽，需补充未来日期，未知日期不视为休市。"}))
                elif trading_day and now.hour >= 15 and state["last_date"] < now.date().isoformat():
                    try:
                        write_heartbeat(path, worker_id, interval_seconds, "UPDATING", now)
                        updated = advance_from_public(session_id)
                        write_heartbeat(path, worker_id, interval_seconds, "WAITING", _now(None))
                        emit(canonical_json({"session_id": session_id, "last_date": updated["last_date"],
                                             "status": updated["status"], "qualified_days": updated["qualified_days"]}))
                    except (ValueError, OSError, DataRefreshError, RuntimeError) as exc:
                        write_heartbeat(path, worker_id, interval_seconds, "ERROR", _now(None))
                        emit(canonical_json({"session_id": session_id, "error": str(exc), "live_trading_enabled": False}))
                else:
                    emit(canonical_json({"session_id": session_id, "status": "WAITING", "last_date": state["last_date"]}))
                if once:
                    return
                time.sleep(interval_seconds)
        finally:
            write_heartbeat(path, worker_id, interval_seconds, "STOPPED", _now(None))
