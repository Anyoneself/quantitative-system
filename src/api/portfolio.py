from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import unicodedata
from zoneinfo import ZoneInfo

from data.errors import DataRefreshError
from data.models import PriceVolumeBar
from data.source import fetch_unadjusted_bars
from portfolio.performance import market_data_fingerprint, rebuild_portfolio_equity
from portfolio.risk import calculate_risk
from portfolio.service import (
    MAX_LEDGER_CENTS,
    create_portfolio,
    find_transaction_by_request_id,
    get_portfolio,
    get_transactions,
    list_portfolios,
    replay_transactions,
    record_transaction,
    record_transaction_with_status,
)
from storage.connection import connect_database
from storage.market_repository import list_daily_bars, save_daily_bars, save_instrument
from storage.migrations import initialize_database
from storage.models import DailyBarRecord, InstrumentRecord
from storage.portfolio_repository import (
    get_portfolio as repository_get_portfolio,
    list_equity,
    list_transactions,
)


MAX_MONEY_YUAN = Decimal("1000000000000")
MAX_QUANTITY = Decimal("1000000000")


@dataclass(frozen=True)
class _PortfolioReadState:
    portfolio: object
    snapshot: object
    transactions: list
    positions: list
    equity: list
    bars: list[DailyBarRecord]
    as_of_date: str | None
    latest_prices: dict[str, int]


def build_portfolio_list_response(db_path: str | Path | None = None) -> dict:
    portfolios = list_portfolios(db_path)
    return {
        "ok": True,
        "portfolios": [_portfolio_payload(item) for item in portfolios],
    }


def build_create_portfolio_response(
    payload: dict,
    db_path: str | Path | None = None,
) -> dict:
    name = _required_user_text(payload.get("name"), "投资组合名称", 100)
    initial_cash_cents = _money_to_cents(payload.get("initial_cash"), "初始资金")
    base_date = str(payload.get("base_date") or _shanghai_today().isoformat())
    benchmark_symbol = str(payload.get("benchmark_symbol") or "000300")
    portfolio = create_portfolio(
        name=name,
        initial_cash_cents=initial_cash_cents,
        base_date=base_date,
        benchmark_symbol=benchmark_symbol,
        db_path=db_path,
    )
    return {
        "ok": True,
        "portfolio": _portfolio_payload(portfolio),
    }


def build_portfolio_detail_response(
    portfolio_id: int,
    db_path: str | Path | None = None,
) -> dict:
    state = _read_portfolio_state(portfolio_id, db_path)
    position_payloads = []
    for position in state.positions:
        latest_price_cents = state.latest_prices.get(position.symbol)
        market_value_cents = (
            latest_price_cents * position.quantity
            if latest_price_cents is not None
            else None
        )
        position_payloads.append(
            {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "cost_basis": _yuan(position.cost_basis_cents),
                "average_cost": _yuan(position.average_cost_cents),
                "latest_price": _yuan(latest_price_cents),
                "market_value": _yuan(market_value_cents),
                "unrealized_profit": (
                    _yuan(market_value_cents - position.cost_basis_cents)
                    if market_value_cents is not None
                    else None
                ),
                "as_of_date": state.as_of_date,
                "estimated": latest_price_cents is None,
            }
        )
    total_assets_cents, valuation_estimated = _current_total_assets_cents(
        state.snapshot,
        state.positions,
        state.latest_prices,
    )
    valuation_estimated = valuation_estimated or _has_transactions_after_valuation(state)
    equity_is_current = _equity_is_current(
        state,
        total_assets_cents,
    )
    return {
        "ok": True,
        "portfolio": _portfolio_payload(state.portfolio),
        "summary": {
            "cash": _yuan(state.snapshot.cash_cents),
            "total_assets": _yuan(total_assets_cents),
            "position_count": len(state.positions),
            "transaction_count": len(state.transactions),
            "as_of_date": state.as_of_date,
            "valuation_estimated": valuation_estimated,
            "equity_stale": bool(state.equity) and not equity_is_current,
        },
        "positions": position_payloads,
        "transactions": [
            _transaction_payload(item)
            for item in state.transactions
        ],
        "equity": (
            [_equity_payload(item) for item in state.equity]
            if equity_is_current
            else []
        ),
    }


def build_record_transaction_response(
    portfolio_id: int,
    payload: dict,
    db_path: str | Path | None = None,
) -> dict:
    symbol = str(payload.get("symbol") or "").strip()
    side = str(payload.get("side") or "").strip().upper()
    if not symbol.isdigit() or len(symbol) != 6:
        raise ValueError("证券代码必须是 6 位数字。")
    if side not in {"BUY", "SELL"}:
        raise ValueError("交易方向只支持 BUY 或 SELL。")
    trade_date = str(payload.get("trade_date") or _shanghai_today().isoformat())
    request_id = _required_request_id(payload.get("request_id"))
    note = _optional_user_text(payload.get("note"), "交易备注", 500)
    _validate_trade_date(trade_date)
    price_cents = _money_to_cents(payload.get("price"), "成交价格")
    quantity = _positive_integer(payload.get("quantity"), "成交数量")
    if side == "BUY" and quantity % 100 != 0:
        raise ValueError("A 股普通买入数量必须是 100 股的整数倍。")

    gross_cents = price_cents * quantity
    if gross_cents > MAX_LEDGER_CENTS:
        raise ValueError("成交金额过大，无法安全保存。")
    commission_cents = _optional_money_to_cents(payload.get("commission"), "佣金")
    stamp_tax_cents = _optional_money_to_cents(payload.get("stamp_tax"), "印花税")
    if commission_cents is None:
        commission_cents = max(500, _rate_cents(gross_cents, Decimal("0.0003")))
    if stamp_tax_cents is None:
        stamp_tax_cents = (
            _rate_cents(gross_cents, Decimal("0.0005"))
            if side == "SELL"
            else 0
        )

    existing_transaction = find_transaction_by_request_id(
        portfolio_id,
        request_id,
        db_path,
    )
    if existing_transaction is not None:
        transaction = record_transaction(
            portfolio_id=portfolio_id,
            symbol=symbol,
            side=side,
            trade_date=trade_date,
            price_cents=price_cents,
            quantity=quantity,
            commission_cents=commission_cents,
            stamp_tax_cents=stamp_tax_cents,
            note=note,
            request_id=request_id,
            db_path=db_path,
        )
        try:
            response = build_portfolio_detail_response(portfolio_id, db_path)
        except Exception:
            response = {
                "ok": True,
                "warning": "原交易已存在，但组合详情暂时无法读取。",
            }
        response["transaction"] = _transaction_payload(transaction)
        response["idempotent_replay"] = True
        return response

    _refresh_symbol(symbol, db_path)
    _validate_saved_trade_date(symbol, trade_date, db_path)

    write_result = record_transaction_with_status(
        portfolio_id=portfolio_id,
        symbol=symbol,
        side=side,
        trade_date=trade_date,
        price_cents=price_cents,
        quantity=quantity,
        commission_cents=commission_cents,
        stamp_tax_cents=stamp_tax_cents,
        note=note,
        request_id=request_id,
        db_path=db_path,
    )
    transaction = write_result.transaction
    warnings = []
    try:
        warning = _refresh_after_transaction(portfolio_id, symbol, db_path)
        if warning:
            warnings.append(warning)
    except Exception:
        # 流水已经提交，后处理失败必须返回成功，避免客户端重试造成重复交易。
        warnings.append("交易已保存，但行情或净值后处理暂时失败。")
    try:
        response = build_portfolio_detail_response(portfolio_id, db_path)
    except Exception:
        response = {"ok": True}
        warnings.append("交易已保存，但组合详情暂时无法读取。")
    response["transaction"] = _transaction_payload(transaction)
    response["idempotent_replay"] = not write_result.created
    if warnings:
        response["warning"] = "；".join(dict.fromkeys(warnings))
    return response


def build_refresh_portfolio_response(
    portfolio_id: int,
    db_path: str | Path | None = None,
) -> dict:
    portfolio = get_portfolio(portfolio_id, db_path)
    symbols = {
        transaction.symbol
        for transaction in get_transactions(portfolio_id, db_path)
    }
    symbols.add(portfolio.benchmark_symbol)
    errors = []
    for symbol in sorted(symbols):
        try:
            _refresh_symbol(symbol, db_path)
        except DataRefreshError as exc:
            errors.append(f"{symbol}：{exc}")
    if get_transactions(portfolio_id, db_path):
        try:
            rebuild_portfolio_equity(portfolio_id, db_path)
        except ValueError as exc:
            errors.append(str(exc))
    response = build_portfolio_detail_response(portfolio_id, db_path)
    if errors:
        response["warning"] = "；".join(errors)
    return response


def build_dashboard_response(
    portfolio_id: int | None = None,
    db_path: str | Path | None = None,
) -> dict:
    portfolios = list_portfolios(db_path)
    if not portfolios:
        return {"ok": True, "dashboard": _empty_dashboard()}
    selected = (
        get_portfolio(portfolio_id, db_path)
        if portfolio_id is not None
        else portfolios[0]
    )
    state = _read_portfolio_state(selected.id, db_path)
    allocation = _allocation_payload(
        state.snapshot.cash_cents,
        state.positions,
        state.latest_prices,
    )
    latest = state.equity[-1] if state.equity else None
    previous = state.equity[-2] if len(state.equity) > 1 else None
    total_assets_cents, valuation_estimated = _current_total_assets_cents(
        state.snapshot,
        state.positions,
        state.latest_prices,
    )
    valuation_estimated = valuation_estimated or _has_transactions_after_valuation(state)
    equity_is_current = _equity_is_current(
        state,
        total_assets_cents,
    )
    current_equity = state.equity if equity_is_current else []
    benchmark_values = _benchmark_values(
        state.portfolio.benchmark_symbol,
        current_equity,
        state.bars,
    )
    realized_pnls = [
        value / 100
        for value in state.snapshot.realized_pnls_cents
    ]
    risk = calculate_risk(
        [point.daily_return for point in current_equity],
        benchmark_returns=(
            _benchmark_returns(benchmark_values)
            if current_equity
            else None
        ),
        realized_pnls=realized_pnls if current_equity else [],
        max_drawdown=min(
            (point.drawdown for point in current_equity),
            default=None,
        ),
    )
    max_drawdown = abs(
        min((point.drawdown for point in current_equity), default=0.0)
    )
    risk_payload = {
        **asdict(risk),
        "risk_level_text": _risk_level_text(risk.risk_level),
        "win_rate_label": "完整持仓周期胜率",
        "explanation": _join_reasons(risk.reasons),
        "stale": bool(state.equity) and not equity_is_current,
        "as_of_date": latest.trade_date if latest else None,
    }
    if state.equity and not equity_is_current:
        risk_payload.update(
            {
                "sample_status": "净值待刷新",
                "risk_level": "STALE",
                "risk_level_text": "待刷新",
                "explanation": "当前流水晚于或不匹配已保存净值，风险指标已暂停展示。",
            }
        )
    dashboard = {
        "portfolio": _portfolio_payload(state.portfolio),
        "summary": {
            "total_assets": _yuan(total_assets_cents),
            "daily_profit": (
                _yuan(latest.total_assets_cents - previous.total_assets_cents)
                if equity_is_current and previous
                else None
            ),
            "total_return": (
                total_assets_cents / state.portfolio.initial_cash_cents - 1
                if state.portfolio.initial_cash_cents
                else None
            ),
            "max_drawdown": max_drawdown if current_equity else None,
            "trade_date": state.as_of_date,
            "valuation_estimated": valuation_estimated,
            "total_assets_note": (
                f"{state.portfolio.name} · "
                + ("含成本估值" if valuation_estimated else "同一截止日估值")
            ),
            "daily_profit_note": (
                "相较上一条有效净值记录"
                if equity_is_current
                else "净值等待显式刷新"
            ),
            "total_return_note": "已扣录入的交易费用",
            "max_drawdown_note": (
                "基于与当前流水一致的未复权净值序列"
                if current_equity
                else "净值等待显式刷新"
            ),
        },
        "equity": [
            {
                **_equity_payload(point),
                "benchmark_net_value": benchmark_values[index],
            }
            for index, point in enumerate(current_equity)
        ],
        "risk": risk_payload,
        "allocation": allocation,
        "strategies": _strategy_payloads(),
        "transaction_count": len(state.transactions),
        "updated_at": state.portfolio.updated_at,
    }
    return {"ok": True, "dashboard": dashboard}


def _refresh_after_transaction(
    portfolio_id: int,
    symbol: str,
    db_path: str | Path | None,
) -> str | None:
    portfolio = get_portfolio(portfolio_id, db_path)
    errors = []
    for target in {portfolio.benchmark_symbol} - {symbol}:
        try:
            _refresh_symbol(target, db_path)
        except DataRefreshError as exc:
            errors.append(f"{target} 行情刷新失败：{exc}")
    try:
        rebuild_portfolio_equity(portfolio_id, db_path)
    except ValueError as exc:
        errors.append(str(exc))
    return "；".join(errors) or None


def _refresh_symbol(
    symbol: str,
    db_path: str | Path | None,
) -> None:
    bars = fetch_unadjusted_bars(symbol)
    if not bars:
        raise DataRefreshError(f"公开行情没有返回 {symbol} 的数据。")
    save_instrument(
        InstrumentRecord(
            symbol=symbol,
            name=bars[-1].name or symbol,
            market=_market_for_symbol(symbol),
        ),
        db_path,
    )
    save_daily_bars(
        [_daily_bar_record(symbol, bar) for bar in bars],
        db_path,
    )


def _daily_bar_record(symbol: str, bar: PriceVolumeBar) -> DailyBarRecord:
    return DailyBarRecord(
        symbol=symbol,
        trade_date=bar.trade_date.isoformat(),
        open_price_cents=_decimal_cents(bar.open),
        high_price_cents=_decimal_cents(bar.high),
        low_price_cents=_decimal_cents(bar.low),
        close_price_cents=_decimal_cents(bar.close),
        volume=max(0, int(round(bar.volume))),
        amount_cents=max(0, _decimal_cents(bar.amount)),
        adjustment_type="NONE",
        source="public_unadjusted",
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def _benchmark_values(
    benchmark_symbol: str,
    equity: list,
    bars: list[DailyBarRecord],
) -> list[float | None]:
    if not equity:
        return []
    by_date = {
        bar.trade_date: bar.close_price_cents
        for bar in bars
        if bar.symbol == benchmark_symbol
        and equity[0].trade_date <= bar.trade_date <= equity[-1].trade_date
    }
    latest_price = None
    first_price = None
    values = []
    for point in equity:
        if point.trade_date in by_date:
            latest_price = by_date[point.trade_date]
            first_price = first_price or latest_price
        values.append(
            latest_price / first_price
            if latest_price is not None and first_price
            else None
        )
    return values


def _benchmark_returns(values: list[float | None]) -> list[float | None]:
    returns: list[float | None] = []
    previous = None
    for value in values:
        if value is None or previous in (None, 0):
            returns.append(None)
        else:
            returns.append(value / previous - 1)
        if value is not None:
            previous = value
    return returns


def _read_portfolio_state(
    portfolio_id: int,
    db_path: str | Path | None,
) -> _PortfolioReadState:
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        connection.execute("BEGIN")
        portfolio = repository_get_portfolio(connection, portfolio_id)
        transactions = list_transactions(connection, portfolio_id)
        equity = list_equity(connection, portfolio_id)
        symbols = sorted(
            {transaction.symbol for transaction in transactions}
            | {portfolio.benchmark_symbol}
        )
        bars = list_daily_bars(
            connection,
            symbols,
            start_date=portfolio.base_date,
            adjustment_type="NONE",
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    snapshot = replay_transactions(
        portfolio,
        transactions,
        updated_at=portfolio.updated_at,
    )
    positions = list(snapshot.positions)
    as_of_date = _valuation_date(positions, bars, equity)
    latest_prices = _prices_at_or_before(
        positions,
        bars,
        as_of_date,
    )
    return _PortfolioReadState(
        portfolio=portfolio,
        snapshot=snapshot,
        transactions=transactions,
        positions=positions,
        equity=equity,
        bars=bars,
        as_of_date=as_of_date,
        latest_prices=latest_prices,
    )


def _valuation_date(positions, bars, equity) -> str | None:
    if not positions:
        return equity[-1].trade_date if equity else None
    latest_dates = []
    for position in positions:
        dates = [
            bar.trade_date
            for bar in bars
            if bar.symbol == position.symbol
        ]
        if dates:
            latest_dates.append(max(dates))
    # 统一到最新可用截止日；停牌标的沿用该日之前的最后收盘价。
    return max(latest_dates) if latest_dates else None


def _prices_at_or_before(
    positions,
    bars: list[DailyBarRecord],
    as_of_date: str | None,
) -> dict[str, int]:
    if as_of_date is None:
        return {}
    prices = {}
    for position in positions:
        candidates = [
            bar
            for bar in bars
            if bar.symbol == position.symbol
            and bar.trade_date <= as_of_date
        ]
        if candidates:
            prices[position.symbol] = max(
                candidates,
                key=lambda bar: bar.trade_date,
            ).close_price_cents
    return prices


def _current_total_assets_cents(
    snapshot,
    positions,
    latest_prices: dict[str, int],
) -> tuple[int, bool]:
    total_assets_cents = snapshot.cash_cents
    if total_assets_cents > MAX_LEDGER_CENTS:
        raise ValueError("组合总资产超出本地账本安全上限。")
    estimated = False
    for position in positions:
        latest_price = latest_prices.get(position.symbol)
        if latest_price is None:
            total_assets_cents += position.cost_basis_cents
            estimated = True
        else:
            total_assets_cents += latest_price * position.quantity
        if total_assets_cents > MAX_LEDGER_CENTS:
            raise ValueError("组合总资产超出本地账本安全上限。")
    return total_assets_cents, estimated


def _has_transactions_after_valuation(state: _PortfolioReadState) -> bool:
    if not state.transactions:
        return False
    if state.as_of_date is None:
        return True
    return any(
        transaction.trade_date > state.as_of_date
        for transaction in state.transactions
    )


def _equity_is_current(
    state: _PortfolioReadState,
    total_assets_cents: int,
) -> bool:
    if not state.equity:
        return False
    latest = state.equity[-1]
    return bool(
        latest.trade_date == state.as_of_date
        and latest.ledger_revision
        == max((transaction.id for transaction in state.transactions), default=0)
        and latest.market_fingerprint == market_data_fingerprint(state.bars)
        and latest.cash_cents == state.snapshot.cash_cents
        and latest.market_value_cents
        == total_assets_cents - state.snapshot.cash_cents
        and latest.total_assets_cents == total_assets_cents
        and not _has_transactions_after_valuation(state)
    )


def _allocation_payload(snapshot_cash_cents, positions, latest_prices) -> list[dict]:
    items = [
        {
            "name": "现金",
            "symbol": "CASH",
            "amount_cents": snapshot_cash_cents,
            "estimated": False,
        }
    ]
    for position in positions:
        latest_price = latest_prices.get(position.symbol)
        items.append(
            {
                "name": position.symbol,
                "symbol": position.symbol,
                "amount_cents": (
                    latest_price * position.quantity
                    if latest_price is not None
                    else position.cost_basis_cents
                ),
                "estimated": latest_price is None,
            }
        )
    total = sum(item["amount_cents"] for item in items)
    for item in items:
        amount_cents = item.pop("amount_cents")
        item["amount"] = _yuan(amount_cents)
        item["ratio"] = amount_cents / total if total else 0.0
    return items


def _empty_dashboard() -> dict:
    return {
        "summary": {
            "total_assets": None,
            "daily_profit": None,
            "total_return": None,
            "max_drawdown": None,
            "trade_date": None,
        },
        "equity": [],
        "risk": {
            "annualized_volatility": None,
            "sharpe_ratio": None,
            "beta": None,
            "var_95": None,
            "win_rate": None,
            "profit_loss_ratio": None,
            "win_rate_label": "完整持仓周期胜率",
            "sample_status": "尚未创建组合",
            "risk_level": "NO_DATA",
            "risk_level_text": "待计算",
            "explanation": "创建组合并录入交易后开始计算。",
        },
        "allocation": [],
        "strategies": _strategy_payloads(),
        "updated_at": None,
    }


def _strategy_payloads() -> list[dict]:
    return [
        {
            "name": "稳健集成模型",
            "strategy_type": "robust_ensemble",
            "status_text": "可验证",
            "annualized_return": None,
            "sharpe_ratio": None,
            "max_drawdown": None,
            "horizon_text": "未来 5 日",
        },
        {
            "name": "时间衰减 KNN",
            "strategy_type": "time_decay_knn",
            "status_text": "可验证",
            "annualized_return": None,
            "sharpe_ratio": None,
            "max_drawdown": None,
            "horizon_text": "未来 5 日",
        },
        {
            "name": "距离加权 KNN",
            "strategy_type": "weighted_knn",
            "status_text": "可验证",
            "annualized_return": None,
            "sharpe_ratio": None,
            "max_drawdown": None,
            "horizon_text": "下一日",
        },
        {
            "name": "逻辑回归",
            "strategy_type": "logistic_regression",
            "status_text": "可验证",
            "annualized_return": None,
            "sharpe_ratio": None,
            "max_drawdown": None,
            "horizon_text": "下一日",
        },
    ]


def _portfolio_payload(portfolio) -> dict:
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "initial_cash": _yuan(portfolio.initial_cash_cents),
        "benchmark_symbol": portfolio.benchmark_symbol,
        "base_date": portfolio.base_date,
        "created_at": portfolio.created_at,
        "updated_at": portfolio.updated_at,
    }


def _transaction_payload(transaction) -> dict:
    return {
        "id": transaction.id,
        "portfolio_id": transaction.portfolio_id,
        "symbol": transaction.symbol,
        "side": transaction.side,
        "trade_date": transaction.trade_date,
        "price": _yuan(transaction.price_cents),
        "quantity": transaction.quantity,
        "commission": _yuan(transaction.commission_cents),
        "stamp_tax": _yuan(transaction.stamp_tax_cents),
        "note": transaction.note,
        "request_id": transaction.request_id,
        "created_at": transaction.created_at,
    }


def _equity_payload(point) -> dict:
    return {
        "trade_date": point.trade_date,
        "cash": _yuan(point.cash_cents),
        "market_value": _yuan(point.market_value_cents),
        "total_assets": _yuan(point.total_assets_cents),
        "net_value": point.net_value,
        "daily_return": point.daily_return,
        "drawdown": point.drawdown,
    }


def _money_to_cents(value: object, field_name: str) -> int:
    cents = _optional_money_to_cents(value, field_name)
    if cents is None or cents <= 0:
        raise ValueError(f"{field_name}必须大于 0。")
    return cents


def _optional_money_to_cents(value: object, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name}格式不正确。")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError(f"{field_name}不能为负数或非有限数。")
        if amount > MAX_MONEY_YUAN:
            raise ValueError(f"{field_name}不能超过 {MAX_MONEY_YUAN} 元。")
        return int(
            (amount * 100).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
    except (InvalidOperation, OverflowError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"{field_name}格式不正确。") from exc


def _positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name}必须是正整数。")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, OverflowError, ValueError) as exc:
        raise ValueError(f"{field_name}必须是正整数。") from exc
    if (
        not number.is_finite()
        or number <= 0
        or number > MAX_QUANTITY
        or number != number.to_integral_value()
    ):
        raise ValueError(f"{field_name}必须是正整数。")
    return int(number)


def _required_request_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("request_id 必须是字符串。")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("request_id 不能为空。")
    if len(cleaned) > 128:
        raise ValueError("request_id 不能超过 128 个字符。")
    _validate_user_text_characters(cleaned, "request_id")
    return cleaned


def _required_user_text(
    value: object,
    field_name: str,
    maximum_length: int,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name}必须是字符串。")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name}不能为空。")
    if len(cleaned) > maximum_length:
        raise ValueError(f"{field_name}不能超过 {maximum_length} 个字符。")
    _validate_user_text_characters(cleaned, field_name)
    return cleaned


def _optional_user_text(
    value: object,
    field_name: str,
    maximum_length: int,
) -> str | None:
    if value in (None, ""):
        return None
    return _required_user_text(value, field_name, maximum_length)


def _validate_user_text_characters(value: str, field_name: str) -> None:
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError(f"{field_name}不能包含控制字符或无效 Unicode 字符。")


def _rate_cents(amount_cents: int, rate: Decimal) -> int:
    return int(
        (Decimal(amount_cents) * rate).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _decimal_cents(value: object) -> int:
    return int(
        (Decimal(str(value)) * 100).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _yuan(cents: int | float | None) -> float | None:
    if cents is None:
        return None
    return round(float(cents) / 100, 2)


def _market_for_symbol(symbol: str) -> str:
    if symbol in {"000016", "000300", "000852", "000905"}:
        return "SH"
    if symbol.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _risk_level_text(level: str) -> str:
    return {
        "NO_DATA": "待计算",
        "STALE": "待刷新",
        "NORMAL": "正常",
        "WARNING": "需要关注",
        "HIGH": "高风险",
    }.get(level, level)


def _validate_trade_date(value: str) -> None:
    try:
        trade_date = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("交易日期必须是 YYYY-MM-DD 格式。") from exc
    if trade_date.isoformat() != value:
        raise ValueError("交易日期必须是 YYYY-MM-DD 格式。")
    if trade_date > _shanghai_today():
        raise ValueError("交易日期不能晚于今天。")
    if trade_date.weekday() >= 5:
        raise ValueError("交易日期不能是周末。")


def _validate_saved_trade_date(
    symbol: str,
    trade_date: str,
    db_path: str | Path | None,
) -> None:
    connection = connect_database(db_path)
    try:
        exists = connection.execute(
            """
            SELECT 1 FROM daily_bars
            WHERE symbol = ?
              AND trade_date = ?
              AND adjustment_type = 'NONE'
            """,
            (symbol, trade_date),
        ).fetchone()
    finally:
        connection.close()
    if exists is None:
        raise ValueError("交易日期不在当前可验证的未复权行情中，或该日不是交易日。")


def _join_reasons(reasons) -> str:
    cleaned = [str(reason).rstrip("。；") for reason in reasons if reason]
    if not cleaned:
        return "当前已计算指标未触发预警阈值。"
    return "；".join(cleaned) + "。"


def _shanghai_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()
