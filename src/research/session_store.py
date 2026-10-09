from __future__ import annotations

import json
import os
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from research.dataset import canonical_json


def _digest(value) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


@contextmanager
def locked(path: Path, name: str = ".lock", *, shared: bool = False):
    import fcntl

    if not path.is_dir():
        raise LookupError("未找到前向模拟。")
    with (path / name).open("a") as lock:
        try:
            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(lock, mode | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("该模拟正在更新或已有观察进程，请稍后重试。") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def atomic_write(path: Path, payload: dict) -> None:
    pending = path.parent / f".{uuid4().hex}.pending"
    try:
        with pending.open("x", encoding="utf-8") as stream:
            stream.write(canonical_json(payload) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        pending.unlink(missing_ok=True)


def _read(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > 50_000_000:
        raise ValueError("模拟存档校验失败：文件类型或大小无效。")
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        state = envelope["state"]
        if (not isinstance(state, dict) or _digest(state) != envelope["sha256"]
                or state["live_trading_enabled"] is not False):
            raise ValueError("模拟存档校验失败。")
        if "revision" in envelope:
            if (type(envelope["revision"]) is not int or envelope["revision"] < 1
                    or type(state.get("storage_revision")) is not int
                    or state["storage_revision"] != envelope["revision"]):
                raise ValueError("模拟存档校验失败：版本字段无效。")
        elif "storage_revision" in state:
            raise ValueError("模拟存档校验失败：缺少版本字段。")
        return envelope
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("模拟存档校验失败。") from exc


def _history(path: Path) -> tuple[list[dict], dict | None]:
    records, latest = [], None
    previous_hash = None
    for index, file in enumerate(sorted((path / "revisions").glob("*.json")), 1):
        if file.name != f"{index:08d}.json":
            raise ValueError("模拟存档校验失败：版本链存在缺口。")
        envelope = _read(file)
        if (type(envelope.get("revision")) is not int or envelope.get("revision") != index
                or envelope.get("previous_hash") != previous_hash
                or type(envelope["state"].get("storage_revision")) is not int
                or envelope["state"].get("storage_revision") != index
                or envelope["state"].get("session_id") != path.name
                or not isinstance(envelope.get("event"), str)
                or not isinstance(envelope["state"].get("updated_at"), str)):
            raise ValueError("模拟存档校验失败：版本链不一致。")
        previous_hash = _digest(envelope)
        records.append({"revision": index, "event": envelope["event"], "sha256": envelope["sha256"],
                        "previous_hash": envelope["previous_hash"], "envelope_hash": previous_hash,
                        "saved_at": envelope["state"]["updated_at"]})
        latest = envelope
    anchor = path / "head.json"
    if anchor.exists():
        try:
            if anchor.is_symlink() or anchor.stat().st_size > 1000:
                raise ValueError("模拟存档校验失败：提交索引无效。")
            head = json.loads(anchor.read_text(encoding="utf-8"))
            revision = head["revision"]
            if (type(revision) is not int or not 1 <= revision <= len(records)
                    or records[revision - 1]["envelope_hash"] != head["envelope_hash"]):
                raise ValueError("模拟存档校验失败：最新提交缺失，禁止回退。")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("模拟存档校验失败：提交索引损坏。") from exc
    return records, latest


def list_revisions(path: Path) -> list[dict]:
    return _history(path)[0]


def latest_snapshot(path: Path) -> dict:
    _, latest = _history(path)
    if latest is None:
        raise ValueError("没有可恢复的完整版本；不从损坏账本猜测资金或持仓。")
    try:
        primary = _read(path / "state.json")
    except (ValueError, OSError):
        primary = None
    if primary and primary.get("revision", 0) > latest["revision"]:
        raise ValueError("模拟存档校验失败：主账本指向更高版本，禁止回退。")
    return latest


def read_state(path: Path) -> dict:
    records, latest = _history(path)
    cache = path / "state.json"
    if not cache.is_file():
        if records:
            raise ValueError("主账本缺失，需要显式恢复最新完整版本。")
        raise LookupError("未找到前向模拟。")
    envelope = _read(cache)
    if envelope["state"].get("session_id") != path.name:
        raise ValueError("模拟账本校验失败：编号不一致。")
    if records and envelope != latest:
        raise ValueError("主账本与最新存档校验不一致，需要显式恢复。")
    if not records and envelope.get("revision"):
        raise ValueError("模拟存档校验失败：版本历史缺失。")
    return envelope["state"]


def save_state(path: Path, state: dict, *, event: str = "UPDATE", recovery: bool = False) -> None:
    records, latest = _history(path)
    if recovery:
        latest_snapshot(path)
    elif records:
        read_state(path)
    elif (path / "state.json").exists():
        # 旧格式首次更新先保存其真实原始状态，不虚构迁移前的历史操作。
        legacy = read_state(path)
        legacy["storage_revision"] = 1
        envelope = {"state": legacy, "sha256": _digest(legacy), "revision": 1,
                    "previous_hash": None, "event": "LEGACY_IMPORT"}
        (path / "revisions").mkdir(exist_ok=True)
        atomic_write(path / "revisions" / "00000001.json", envelope)
        publish_snapshot(path, envelope)
        records = [envelope]
        latest = envelope
    revision = len(records) + 1
    state["storage_revision"] = revision
    envelope = {"state": state, "sha256": _digest(state), "revision": revision, "event": event,
                "previous_hash": _digest(latest) if latest else None}
    (path / "revisions").mkdir(exist_ok=True)
    # 完整版本文件是提交点；主文件只是索引副本，二者不一致时必须显式恢复，不自动回退。
    atomic_write(path / "revisions" / f"{revision:08d}.json", envelope)
    publish_snapshot(path, envelope)


def publish_snapshot(path: Path, envelope: dict) -> None:
    atomic_write(path / "head.json", {"revision": envelope["revision"], "envelope_hash": _digest(envelope)})
    atomic_write(path / "state.json", envelope)
