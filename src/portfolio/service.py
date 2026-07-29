from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from storage.connection import connect_database
from storage.market_repository import upsert_instrument
from storage.migrations import initialize_database
from storage.models import (
    InstrumentRecord,
    PortfolioRecord,
    PositionRecord,
    TransactionRecord,
)
from storage.portfolio_repository import (
    get_portfolio as repository_get_portfolio,
    get_transaction_by_request_id as repository_get_transaction_by_request_id,
    insert_portfolio,
    insert_transaction,
    list_portfolios as repository_list_portfolios,
    list_positions,
    list_transactions,
    replace_positions,
    touch_portfolio,
)


MAX_LEDGER_CENTS = 100_000_000_000_000


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash_cents: int
    positions: tuple[PositionRecord, ...]
    realized_pnls_cents: tuple[int, ...]


@dataclass(frozen=True)
class TransactionWriteResult:
    transaction: TransactionRecord
    created: bool


def create_portfolio(
    name: str,
    initial_cash_cents: int,
    base_date: str,
    benchmark_symbol: str = "000300",
    db_path: str | Path | None = None,
) -> PortfolioRecord:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("投资组合名称不能为空。")
    _validate_user_text(cleaned_name, "投资组合名称", 100)
    if not _is_positive_integer(initial_cash_cents):
        raise ValueError("初始资金必须是大于 0 的整数分。")
    _validate_ledger_amount(initial_cash_cents, "初始资金")
    parsed_base_date = _parse_date(base_date, "组合起始日")
    if parsed_base_date > _shanghai_today():
        raise ValueError("组合起始日不能晚于今天。")
    normalized_benchmark = _normalize_symbol(benchmark_symbol)
    if not normalized_benchmark:
        raise ValueError("比较基准代码必须是 6 位数字。")

    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        now = _utc_now()
        portfolio = insert_portfolio(
            connection,
            cleaned_name,
            initial_cash_cents,
            normalized_benchmark,
            base_date,
            now,
        )
        connection.commit()
        return portfolio
    finally:
        connection.close()


def get_portfolio(
    portfolio_id: int,
    db_path: str | Path | None = None,
) -> PortfolioRecord:
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        return repository_get_portfolio(connection, portfolio_id)
    finally:
        connection.close()


def list_portfolios(
    db_path: str | Path | None = None,
) -> list[PortfolioRecord]:
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        return repository_list_portfolios(connection)
    finally:
        connection.close()


def get_transactions(
    portfolio_id: int,
    db_path: str | Path | None = None,
) -> list[TransactionRecord]:
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        repository_get_portfolio(connection, portfolio_id)
        return list_transactions(connection, portfolio_id)
    finally:
        connection.close()


def find_transaction_by_request_id(
    portfolio_id: int,
    request_id: str,
    db_path: str | Path | None = None,
) -> TransactionRecord | None:
    normalized_request_id = _normalize_request_id(request_id)
    if normalized_request_id is None:
        return None
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        repository_get_portfolio(connection, portfolio_id)
        return repository_get_transaction_by_request_id(
            connection,
            portfolio_id,
            normalized_request_id,
        )
    finally:
        connection.close()


def record_transaction(
    portfolio_id: int,
    symbol: str,
    side: str,
    trade_date: str,
    price_cents: int,
    quantity: int,
    commission_cents: int = 0,
    stamp_tax_cents: int = 0,
    note: str | None = None,
    request_id: str | None = None,
    db_path: str | Path | None = None,
) -> TransactionRecord:
    return record_transaction_with_status(
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
    ).transaction


def record_transaction_with_status(
    portfolio_id: int,
    symbol: str,
    side: str,
    trade_date: str,
    price_cents: int,
    quantity: int,
    commission_cents: int = 0,
    stamp_tax_cents: int = 0,
    note: str | None = None,
    request_id: str | None = None,
    db_path: str | Path | None = None,
) -> TransactionWriteResult:
    normalized_symbol = _normalize_symbol(symbol)
    normalized_side = side.strip().upper()
    if not normalized_symbol:
        raise ValueError("证券代码必须是 6 位数字。")
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError("交易方向只支持 BUY 或 SELL。")
    transaction_date = _parse_date(trade_date, "交易日期")
    if transaction_date > _shanghai_today():
        raise ValueError("交易日期不能晚于今天。")
    if not _is_positive_integer(price_cents):
        raise ValueError("成交价格必须是大于 0 的整数分。")
    if not _is_positive_integer(quantity):
        raise ValueError("成交数量必须是大于 0 的整数。")
    if not _is_non_negative_integer(commission_cents) or not _is_non_negative_integer(stamp_tax_cents):
        raise ValueError("佣金和印花税必须是非负整数分。")
    _validate_ledger_amount(price_cents, "成交价格")
    _validate_ledger_amount(commission_cents, "佣金")
    _validate_ledger_amount(stamp_tax_cents, "印花税")
    gross_amount = price_cents * quantity
    _validate_ledger_amount(gross_amount, "成交金额")
    normalized_note = note.strip() if note and note.strip() else None
    if normalized_note is not None:
        _validate_user_text(normalized_note, "交易备注", 500)
    normalized_request_id = _normalize_request_id(request_id)

    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        portfolio = repository_get_portfolio(connection, portfolio_id)
        if transaction_date < _parse_date(portfolio.base_date, "组合起始日"):
            raise ValueError("交易日期不能早于组合起始日。")
        if normalized_request_id is not None:
            existing_transaction = repository_get_transaction_by_request_id(
                connection,
                portfolio_id,
                normalized_request_id,
            )
            if existing_transaction is not None:
                _validate_idempotent_transaction(
                    existing_transaction,
                    symbol=normalized_symbol,
                    side=normalized_side,
                    trade_date=trade_date,
                    price_cents=price_cents,
                    quantity=quantity,
                    commission_cents=commission_cents,
                    stamp_tax_cents=stamp_tax_cents,
                    note=normalized_note,
                )
                connection.commit()
                return TransactionWriteResult(
                    transaction=existing_transaction,
                    created=False,
                )
        _ensure_instrument(connection, normalized_symbol)
        now = _utc_now()
        transaction = insert_transaction(
            connection,
            portfolio_id,
            normalized_symbol,
            normalized_side,
            trade_date,
            price_cents,
            quantity,
            commission_cents,
            stamp_tax_cents,
            normalized_note,
            normalized_request_id,
            now,
        )
        snapshot = replay_transactions(
            portfolio,
            list_transactions(connection, portfolio_id),
            now,
        )
        replace_positions(connection, portfolio_id, list(snapshot.positions))
        touch_portfolio(connection, portfolio_id, now)
        connection.commit()
        return TransactionWriteResult(
            transaction=transaction,
            created=True,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def rebuild_positions(
    portfolio_id: int,
    db_path: str | Path | None = None,
) -> PortfolioSnapshot:
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        portfolio = repository_get_portfolio(connection, portfolio_id)
        now = _utc_now()
        snapshot = replay_transactions(
            portfolio,
            list_transactions(connection, portfolio_id),
            now,
        )
        replace_positions(connection, portfolio_id, list(snapshot.positions))
        touch_portfolio(connection, portfolio_id, now)
        connection.commit()
        return snapshot
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_positions(
    portfolio_id: int,
    db_path: str | Path | None = None,
) -> list[PositionRecord]:
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        repository_get_portfolio(connection, portfolio_id)
        return list_positions(connection, portfolio_id)
    finally:
        connection.close()


def replay_transactions(
    portfolio: PortfolioRecord,
    transactions: list[TransactionRecord],
    updated_at: str | None = None,
) -> PortfolioSnapshot:
    cash_cents = portfolio.initial_cash_cents
    _validate_ledger_amount(cash_cents, "组合现金")
    state: dict[str, list[int]] = {}
    cycle_realized_pnls: dict[str, int] = {}
    realized_pnls: list[int] = []
    for transaction in sorted(transactions, key=lambda item: (item.trade_date, item.id)):
        gross_amount = transaction.price_cents * transaction.quantity
        fees = transaction.commission_cents + transaction.stamp_tax_cents
        if transaction.side == "BUY":
            total_cost = gross_amount + fees
            _validate_ledger_amount(total_cost, "买入总成本")
            if total_cost > cash_cents:
                raise ValueError(
                    f"{transaction.trade_date} 买入 {transaction.symbol} 时现金不足。"
                )
            cash_cents -= total_cost
            position = state.setdefault(transaction.symbol, [0, 0])
            next_quantity = position[0] + transaction.quantity
            next_cost = position[1] + total_cost
            _validate_ledger_amount(next_quantity, "累计持仓数量")
            _validate_ledger_amount(next_cost, "累计持仓成本")
            position[0] = next_quantity
            position[1] = next_cost
            continue

        position = state.get(transaction.symbol)
        if position is None or transaction.quantity > position[0]:
            raise ValueError(
                f"{transaction.trade_date} 卖出 {transaction.symbol} 的数量超过持仓。"
            )
        proceeds = gross_amount - fees
        if proceeds < 0:
            raise ValueError("卖出费用不能超过成交金额。")
        removed_cost = _proportional_cost(
            position[1],
            transaction.quantity,
            position[0],
        )
        next_cash = cash_cents + proceeds
        _validate_ledger_amount(next_cash, "组合现金")
        cash_cents = next_cash
        cycle_realized_pnls[transaction.symbol] = (
            cycle_realized_pnls.get(transaction.symbol, 0)
            + proceeds
            - removed_cost
        )
        position[0] -= transaction.quantity
        position[1] -= removed_cost
        if position[0] == 0:
            realized_pnls.append(cycle_realized_pnls.pop(transaction.symbol))
            del state[transaction.symbol]

    timestamp = updated_at or _utc_now()
    positions = tuple(
        PositionRecord(
            portfolio_id=portfolio.id,
            symbol=symbol,
            quantity=values[0],
            cost_basis_cents=values[1],
            updated_at=timestamp,
        )
        for symbol, values in sorted(state.items())
    )
    return PortfolioSnapshot(
        cash_cents=cash_cents,
        positions=positions,
        realized_pnls_cents=tuple(realized_pnls),
    )


def _ensure_instrument(
    connection: sqlite3.Connection,
    symbol: str,
) -> None:
    row = connection.execute(
        "SELECT 1 FROM instruments WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    if row is not None:
        return
    upsert_instrument(
        connection,
        InstrumentRecord(
            symbol=symbol,
            name=symbol,
            market=_market_for_symbol(symbol),
        ),
    )


def _market_for_symbol(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _proportional_cost(
    total_cost_cents: int,
    sold_quantity: int,
    held_quantity: int,
) -> int:
    if sold_quantity == held_quantity:
        return total_cost_cents
    # 使用整数四舍五入分摊成本，剩余持仓保留全部尾差，确保成本总额可重建。
    return (total_cost_cents * sold_quantity + held_quantity // 2) // held_quantity


def _normalize_symbol(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) == 6 and cleaned.isdigit():
        return cleaned
    return ""


def _normalize_request_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("request_id 必须是字符串。")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("request_id 不能为空。")
    if len(cleaned) > 128:
        raise ValueError("request_id 不能超过 128 个字符。")
    _validate_user_text(cleaned, "request_id", 128)
    return cleaned


def _validate_idempotent_transaction(
    transaction: TransactionRecord,
    *,
    symbol: str,
    side: str,
    trade_date: str,
    price_cents: int,
    quantity: int,
    commission_cents: int,
    stamp_tax_cents: int,
    note: str | None,
) -> None:
    submitted_content = (
        symbol,
        side,
        trade_date,
        price_cents,
        quantity,
        commission_cents,
        stamp_tax_cents,
        note,
    )
    stored_content = (
        transaction.symbol,
        transaction.side,
        transaction.trade_date,
        transaction.price_cents,
        transaction.quantity,
        transaction.commission_cents,
        transaction.stamp_tax_cents,
        transaction.note,
    )
    if submitted_content != stored_content:
        raise ValueError("同一 request_id 已用于不同的交易内容，请使用新的 request_id。")


def _parse_date(value: str, field_name: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是 YYYY-MM-DD 格式。") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name}必须是 YYYY-MM-DD 格式。")
    return parsed


def _shanghai_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_negative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_ledger_amount(value: int, field_name: str) -> None:
    if value < 0 or value > MAX_LEDGER_CENTS:
        raise ValueError(
            f"{field_name}超出本地账本安全上限 "
            f"{MAX_LEDGER_CENTS / 100:,.0f} 元。"
        )


def _validate_user_text(value: str, field_name: str, maximum_length: int) -> None:
    if len(value) > maximum_length:
        raise ValueError(f"{field_name}不能超过 {maximum_length} 个字符。")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError(f"{field_name}不能包含控制字符或无效 Unicode 字符。")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
