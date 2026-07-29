from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path


DEFAULT_DATABASE_PATH = Path("data/app/quantitative_system.db")
DEFAULT_BUSY_TIMEOUT_MS = 5_000
_JOURNAL_MODE_LOCK = threading.Lock()


def resolve_database_path(db_path: str | Path | None = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    configured_path = os.getenv("QUANT_DB_PATH")
    return Path(configured_path) if configured_path else DEFAULT_DATABASE_PATH


def connect_database(db_path: str | Path | None = None) -> sqlite3.Connection:
    database_path = resolve_database_path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=DEFAULT_BUSY_TIMEOUT_MS / 1_000)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
    try:
        _ensure_wal_mode(connection)
    except Exception:
        connection.close()
        raise
    return connection


def _ensure_wal_mode(connection: sqlite3.Connection) -> None:
    # 冷库并发连接时串行切换日志模式，避免多个线程同时争抢数据库头锁。
    with _JOURNAL_MODE_LOCK:
        for attempt in range(4):
            try:
                current_mode = str(
                    connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower()
                if current_mode != "wal":
                    connection.execute("PRAGMA journal_mode = WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 3:
                    raise
                time.sleep(0.05 * (attempt + 1))
