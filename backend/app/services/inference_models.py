from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.app.db import get_connection
from backend.app.services.job_manager import _latest_checkpoint_relpath


def _safe_workspace_rel(rel: str) -> str | None:
    s = (rel or "").strip().replace("\\", "/").rstrip("/")
    if not s or s == "—":
        return None
    p = Path(s)
    if p.is_absolute():
        return None
    if ".." in p.parts:
        return None
    return s


def _training_job_display_name(req: dict[str, Any], job_id: str) -> str:
    n = str(req.get("job_name") or "").strip()
    if n and n != "—":
        return n
    return f"训练 · {job_id[:8]}"


def _adapter_relpath_for_registered_training(workspace: Path, req: dict[str, Any]) -> str | None:
    base = _safe_workspace_rel(str(req.get("swift_run_relpath") or req.get("output_dir") or ""))
    if not base:
        return None
    ws = workspace.resolve()
    out = ws / base
    ckpt_rel: str | None = None
    if out.is_dir():
        ckpt_rel = _latest_checkpoint_relpath(workspace, base)
        if ckpt_rel:
            ckpt = ws / ckpt_rel.replace("\\", "/")
            if ckpt.is_dir() and (ckpt / "adapter_config.json").is_file():
                return ckpt_rel.replace("\\", "/")
        if (out / "adapter_config.json").is_file():
            return base
        adapters = sorted(
            (p for p in out.rglob("adapter_config.json") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if adapters:
            try:
                return adapters[0].parent.resolve().relative_to(ws).as_posix()
            except ValueError:
                pass
    fallback = ckpt_rel or base
    return fallback.replace("\\", "/")


def _ms_swift_train_version_segment(req: dict[str, Any], adapter_rel: str) -> str | None:
    """ms-swift --add_version 下子目录名（如 v0-…），作「训练版本」；无则 None。"""
    for src in (str(req.get("swift_run_relpath") or ""), adapter_rel):
        s = src.strip().replace("\\", "/")
        if not s:
            continue
        for part in s.split("/"):
            if re.match(r"^v\d+-", part):
                return part
    return None


def list_registered_training_models(workspace: Path) -> list[dict[str, Any]]:
    """仅 `training_jobs_persist` 中状态为 succeeded 的训练任务；路径由 request 与（若存在）磁盘上的 checkpoint/adapter 推断。"""
    root = workspace.resolve()
    conn = get_connection(root)
    rows = conn.execute(
        "SELECT id, request_json FROM training_jobs_persist "
        "WHERE status = 'succeeded' "
        "ORDER BY CAST(created_at AS REAL) DESC"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        jid = str(row["id"])
        raw = row["request_json"] or "{}"
        try:
            req = json.loads(raw) if isinstance(raw, str) else {}
        except json.JSONDecodeError:
            req = {}
        if not isinstance(req, dict):
            req = {}
        adapter_rel = _adapter_relpath_for_registered_training(root, req)
        if not adapter_rel:
            continue
        jn = _training_job_display_name(req, jid)
        tbm = str(req.get("model") or "").strip()
        project_title = str(req.get("project_title") or "").strip() or None
        batch_name = str(req.get("batch_name") or "").strip() or None
        dataset_name = str(req.get("dataset_name") or "").strip() or None
        dvid = str(req.get("dataset_version_id") or "").strip() or None
        swift_tv = _ms_swift_train_version_segment(req, adapter_rel)
        out.append(
            {
                "id": f"lora:{adapter_rel}",
                "kind": "lora",
                "path": adapter_rel,
                "train_base_model": tbm or None,
                "job_id": jid,
                "job_name": jn,
                "label": jn,
                "project_title": project_title,
                "batch_name": batch_name,
                "dataset_name": dataset_name,
                "dataset_version_id": dvid,
                "swift_train_version": swift_tv,
            }
        )
    return out[:200]


def list_workspace_models(workspace: Path) -> list[dict[str, Any]]:
    """粗扫工作区下可能的 LoRA/合并结果目录，供 UI 下拉里展示。"""
    root = workspace.resolve()
    out: list[dict[str, Any]] = []
    out_dir = root / "output"
    if not out_dir.is_dir():
        return []
    for adapter in out_dir.rglob("adapter_config.json"):
        rel = adapter.parent.relative_to(root)
        out.append(
            {
                "id": f"lora:{rel.as_posix()}",
                "kind": "lora",
                "path": rel.as_posix(),
                "label": f"LoRA · {rel.as_posix()}",
            }
        )
    for cfg in out_dir.rglob("config.json"):
        if "checkpoint" in str(cfg) or "v0-" in str(cfg) or "merged" in str(cfg).lower():
            d = cfg.parent
            if (d / "model.safetensors").is_file() or (d / "adapter_config.json").is_file():
                continue
        rel = cfg.parent.relative_to(root)
        if any(x["path"] == rel.as_posix() for x in out):
            continue
        if rel.parts and rel.parts[0] == "merge-jobs":
            continue
        out.append(
            {
                "id": f"merged:{rel.as_posix()}",
                "kind": "merge",
                "path": rel.as_posix(),
                "label": f"合并/权重 · {rel.as_posix()}",
            }
        )
    return out[:200]
