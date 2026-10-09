from __future__ import annotations

from research.capture import capture_dataset
from research.config import parse_config
from research.dataset import parse_dataset
from research.paper import (
    advance_from_public, advance_session, create_session, pause_session,
    resume_session, recover_session, get_session_view,
)


def build_paper_response(payload: dict, session_id: str | None = None, action: str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("模拟请求必须为对象。")
    expected = payload.get("expected_revision")
    if session_id is not None and action in {"advance", "pause", "resume"} and "expected_revision" not in payload:
        raise ValueError("操作缺少预期账本版本，请刷新页面后重试。")
    if "expected_revision" in payload and (type(expected) is not int or expected < 0):
        raise ValueError("预期版本必须为非负整数。")
    if session_id is None:
        if set(payload) - {"symbols", "benchmark", "dataset", "config"} or ("dataset" in payload) == ("symbols" in payload):
            raise ValueError("创建模拟必须且只能选择 symbols 或 dataset。")
        config = payload.get("config", {})
        if not isinstance(config, dict) or set(config) - {"strategy", "simulation"}:
            raise ValueError("前向模拟仅支持 strategy 和 simulation 冻结配置。")
        strategy, simulation, _ = parse_config(config)
        dataset = parse_dataset(payload["dataset"]) if "dataset" in payload else capture_dataset(
            payload["symbols"], payload.get("benchmark", "000300"))
        state = create_session(dataset, strategy, simulation)
    elif action == "advance":
        if set(payload) - {"dataset", "expected_revision"}:
            raise ValueError("推进只接受追加行情，不接受参数修改。")
        state = (advance_session(session_id, parse_dataset(payload["dataset"]), expected_revision=expected)
                 if "dataset" in payload else advance_from_public(session_id, expected_revision=expected))
    elif action in {"pause", "resume", "recover"}:
        if set(payload) - {"expected_revision"}:
            raise ValueError("运行控制不接受额外参数。")
        state = {"pause": pause_session, "resume": resume_session, "recover": recover_session}[action](
            session_id, expected_revision=expected)
    else:
        raise ValueError("未知模拟操作。")
    return {"ok": True, "session": get_session_view(state["session_id"])}
