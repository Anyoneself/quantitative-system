from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from storage.connection import connect_database
from storage.migrations import initialize_database
from storage.models import DailyBarRecord, InstrumentRecord


def save_instrument(
    instrument: InstrumentRecord,
    db_path: str | Path | None = None,
) -> None:
    _validate_instrument(instrument)
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        upsert_instrument(connection, instrument)
        connection.commit()
    finally:
        connection.close()


def save_daily_bars(
    bars: list[DailyBarRecord],
    db_path: str | Path | None = None,
) -> int:
    if not bars:
        return 0
    for bar in bars:
        _validate_daily_bar(bar)
    initialize_database(db_path)
    connection = connect_database(db_path)
    try:
        upsert_daily_bars(connection, bars)
        connection.commit()
        return len(bars)
    finally:
        connection.close()


def list_daily_bars(
    connection: sqlite3.Connection,
    symbols: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    adjustment_type: str | None = None,
) -> list[DailyBarRecord]:
    if not symbols:
        return []
    placeholders = ", ".join("?" for _ in symbols)
    conditions = [f"symbol IN ({placeholders})"]
    parameters: list[object] = list(symbols)
    if start_date is not None:
        conditions.append("trade_date >= ?")
        parameters.append(start_date)
    if end_date is not None:
        conditions.append("trade_date <= ?")
        parameters.append(end_date)
    if adjustment_type is not None:
        conditions.append("adjustment_type = ?")
        parameters.append(adjustment_type)
    rows = connection.execute(
        f"""
        SELECT * FROM daily_bars
        WHERE {" AND ".join(conditions)}
        ORDER BY trade_date, symbol
        """,
        parameters,
    ).fetchall()
    return [
        DailyBarRecord(
            symbol=str(row["symbol"]),
            trade_date=str(row["trade_date"]),
            open_price_cents=int(row["open_price"]),
            high_price_cents=int(row["high_price"]),
            low_price_cents=int(row["low_price"]),
            close_price_cents=int(row["close_price"]),
            volume=int(row["volume"]),
            amount_cents=int(row["amount"]),
            adjustment_type=str(row["adjustment_type"]),
            source=str(row["source"]),
            fetched_at=str(row["fetched_at"]),
        )
        for row in rows
    ]


def upsert_instrument(
    connection: sqlite3.Connection,
    instrument: InstrumentRecord,
) -> None:
    _validate_instrument(instrument)
    connection.execute(
        """
        INSERT INTO instruments(
            symbol, name, market, instrument_type, listed_date, delisted_date, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            name = excluded.name,
            market = excluded.market,
            instrument_type = excluded.instrument_type,
            listed_date = excluded.listed_date,
            delisted_date = excluded.delisted_date,
            updated_at = excluded.updated_at
        """,
        (
            instrument.symbol,
            instrument.name,
            instrument.market,
            instrument.instrument_type,
            instrument.listed_date,
            instrument.delisted_date,
            _now(),
        ),
    )


def upsert_daily_bars(
    connection: sqlite3.Connection,
    bars: list[DailyBarRecord],
) -> None:
    for bar in bars:
        _validate_daily_bar(bar)
    connection.executemany(
        """
        INSERT INTO daily_bars(
            symbol, trade_date, open_price, high_price, low_price, close_price,
            volume, amount, adjustment_type, source, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, trade_date) DO UPDATE SET
            open_price = excluded.open_price,
            high_price = excluded.high_price,
            low_price = excluded.low_price,
            close_price = excluded.close_price,
            volume = excluded.volume,
            amount = excluded.amount,
            adjustment_type = excluded.adjustment_type,
            source = excluded.source,
            fetched_at = excluded.fetched_at
        """,
        [
            (
                bar.symbol,
                bar.trade_date,
                bar.open_price_cents,
                bar.high_price_cents,
                bar.low_price_cents,
                bar.close_price_cents,
                bar.volume,
                bar.amount_cents,
                bar.adjustment_type,
                bar.source,
                bar.fetched_at,
            )
            for bar in bars
        ],
    )


def _validate_instrument(instrument: InstrumentRecord) -> None:
    if not instrument.symbol.strip():
        raise ValueError("证券代码不能为空。")
    if not instrument.name.strip():
        raise ValueError("证券名称不能为空。")
    if not instrument.market.strip() or not instrument.instrument_type.strip():
        raise ValueError("证券市场和类型不能为空。")


def _validate_daily_bar(bar: DailyBarRecord) -> None:
    trade_date = _parse_date(bar.trade_date, "交易日期")
    if trade_date > datetime.now(ZoneInfo("Asia/Shanghai")).date():
        raise ValueError("交易日期不能晚于今天。")
    prices = (
        bar.open_price_cents,
        bar.high_price_cents,
        bar.low_price_cents,
        bar.close_price_cents,
    )
    if any(price <= 0 for price in prices):
        raise ValueError("日线价格必须大于 0 分。")
    if bar.high_price_cents < max(bar.open_price_cents, bar.low_price_cents, bar.close_price_cents):
        raise ValueError("最高价不能低于开盘价、最低价或收盘价。")
    if bar.low_price_cents > min(bar.open_price_cents, bar.high_price_cents, bar.close_price_cents):
        raise ValueError("最低价不能高于开盘价、最高价或收盘价。")
    if bar.volume < 0 or bar.amount_cents < 0:
        raise ValueError("成交量和成交额不能为负数。")


def _parse_date(value: str, field_name: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是 YYYY-MM-DD 格式。") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name}必须是 YYYY-MM-DD 格式。")
    return parsed


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
