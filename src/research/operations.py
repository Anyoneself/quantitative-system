from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from research.session_store import atomic_write, list_revisions


def write_heartbeat(path: Path, worker_id: str, interval: int, phase: str, now: datetime) -> None:
    atomic_write(path / "runtime.json", {
        "worker_id": worker_id, "pid": os.getpid(), "status": phase,
        "heartbeat_at": now.isoformat(), "lease_expires_at": (now + timedelta(seconds=interval + 300)).isoformat(),
        "interval_seconds": interval,
    })


def operational_status(state: dict, path: Path, now: datetime, *, code_changed: bool) -> dict:
    worker = {"status": "NOT_STARTED", "online": False}
    heartbeat = path / "runtime.json"
    if heartbeat.exists():
        try:
            if heartbeat.is_symlink() or heartbeat.stat().st_size > 10000:
                raise ValueError("invalid heartbeat")
            worker = json.loads(heartbeat.read_text(encoding="utf-8"))
            recorded = datetime.fromisoformat(worker["heartbeat_at"])
            expires = datetime.fromisoformat(worker["lease_expires_at"])
            interval = worker["interval_seconds"]
            if (recorded.tzinfo is None or expires.tzinfo is None or recorded > now
                    or type(interval) is not int or not 60 <= interval <= 86400
                    or expires != recorded + timedelta(seconds=interval + 300)
                    or worker["status"] not in {"WAITING", "UPDATING", "ERROR", "STOPPED"}):
                raise ValueError("invalid heartbeat")
            worker["online"] = worker["status"] != "STOPPED" and now <= expires
            if worker["status"] != "STOPPED" and not worker["online"]:
                worker["status"] = "HEARTBEAT_EXPIRED"
        except (OSError, ValueError, KeyError, TypeError):
            worker = {"status": "INVALID_HEARTBEAT", "online": False}
    alerts = []
    calendar = state["dataset"].get("calendar")
    calendar_expired = bool(calendar and calendar["sessions"][-1] < now.date().isoformat())
    for code, active, message in (
        ("WORKER_OFFLINE", not worker["online"], "后台观察进程离线；页面自动检查不是后台服务。"),
        ("PAUSED", state["status"] == "PAUSED", "模拟已暂停，恢复观察需单独确认。"),
        ("RISK_HALT", state["result"]["summary"]["halted"], "永久熔断保留，继续观察不会重新获得买入资格。"),
        ("CODE_CHANGED", code_changed, "源码与冻结版本不同，禁止继续推进旧模拟。"),
        ("CALENDAR_EXPIRED", calendar_expired, "声明日历已用尽，需补充未来日历；未知日期不视为休市。"),
        ("OBSERVATION_FAILED", bool(state.get("last_error")), state.get("last_error")),
        ("DATA_UNVERIFIED", True, "数据来源、公司行动及证券状态未独立核验，实盘禁用。"),
    ):
        if active:
            alerts.append({"code": code, "message": message})
    return {"worker": worker, "alerts": alerts, "code_changed": code_changed,
            "revision": state.get("storage_revision", 0), "revisions": [
                item for item in list_revisions(path) if item["revision"] <= state.get("storage_revision", 0)
            ][-20:],
            "reconciliation": state["result"].get("reconciliation", {"status": "NOT_CHECKED"})}
