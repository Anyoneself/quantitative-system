from __future__ import annotations

import json
import os
import re
from pathlib import Path
from threading import Lock
from hashlib import sha256

from research.config import parse_config
from research.dataset import build_demo_dataset, parse_dataset
from research.experiment import run_experiment, save_experiment


_EXPERIMENT_LOCK = Lock()
_RUN_ID = re.compile(r"\d{8}T\d{6}-[0-9a-f]{12}")


def experiment_root() -> Path:
    return Path(os.environ.get("QUANT_EXPERIMENT_DIR", "data/experiments"))


def build_experiment_response(payload: dict) -> dict:
    if set(payload) - {"demo", "dataset", "config"}:
        raise ValueError("研究请求包含未知字段。")
    demo = payload.get("demo", False)
    if not isinstance(demo, bool) or demo == ("dataset" in payload):
        raise ValueError("必须且只能选择 demo=true 或上传 dataset。")
    config = parse_config(payload.get("config"))
    dataset = build_demo_dataset() if demo else parse_dataset(payload["dataset"])
    # 单用户服务一次只运行一个组合实验，避免多次点击耗尽本地计算资源。
    if not _EXPERIMENT_LOCK.acquire(blocking=False):
        raise RuntimeError("已有组合实验运行中，请稍后重试。")
    try:
        report = run_experiment(dataset, *config)
        output = save_experiment(report, dataset, experiment_root())
        return {"ok": True, "experiment": load_experiment(output.name)}
    finally:
        _EXPERIMENT_LOCK.release()


def load_experiment(run_id: str) -> dict:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("实验编号格式错误。")
    path = experiment_root() / run_id / "report.json"
    if not path.is_file():
        raise LookupError("未找到该实验。")
    manifest = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("实验清单格式错误。")
    for name in ("report.json", "dataset.json", "config.json", "report.md"):
        if manifest.get(name) != sha256((path.parent / name).read_bytes()).hexdigest():
            raise ValueError("实验留档校验失败，文件可能被修改。")
    return json.loads(path.read_text(encoding="utf-8"))


def list_experiments() -> list[dict]:
    paths = sorted(experiment_root().glob("*/report.json"), reverse=True)
    reports = []
    for path in paths:
        if not _RUN_ID.fullmatch(path.parent.name):
            continue
        try:
            report = load_experiment(path.parent.name)
            reports.append({
                "run_id": report["run_id"], "created_at": report["created_at"],
                "synthetic": report["synthetic"], "release_status": report["release_status"],
                "research_status": report["research_status"], "summary": report["out_of_sample"]["summary"],
            })
        except (ValueError, KeyError, TypeError, OSError):
            reports.append({"run_id": path.parent.name, "error": "报告损坏，需检查留档文件。"})
        if len(reports) >= 50:
            break
    return reports
