from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from backend.app.services.merge_job_manager import MergeJob, MergeJobManager

MergeUiStatus = Literal["none", "merging", "interrupted", "failed", "success"]


def _norm_lora_relpath(s: str) -> str:
    return s.strip().replace("\\", "/")


def _lora_relpath_from_meta_lora_used(workspace: Path, lora_used: str) -> str | None:
    s = (lora_used or "").strip()
    if not s:
        return None
    p = Path(s)
    try:
        if p.is_absolute():
            return p.resolve().relative_to(workspace.resolve()).as_posix()
    except (ValueError, OSError):
        return None
    return _norm_lora_relpath(str(p))


def _merged_output_dir_looks_valid(out_dir: Path) -> bool:
    if not out_dir.is_dir():
        return False
    if not (out_dir / "workshop_merge_meta.json").is_file():
        return False
    has_weights = (out_dir / "model.safetensors").is_file() or (out_dir / "pytorch_model.bin").is_file()
    return (out_dir / "config.json").is_file() or has_weights or (out_dir / "adapter_config.json").is_file()


def _lora_rels_with_disk_merge(workspace: Path) -> set[str]:
    root = workspace.resolve()
    out = root / "output"
    ok: set[str] = set()
    if not out.is_dir():
        return ok
    for meta_path in out.rglob("workshop_merge_meta.json"):
        if "merge-jobs" in meta_path.parts:
            continue
        parent = meta_path.parent
        if not _merged_output_dir_looks_valid(parent):
            continue
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        rel = _lora_relpath_from_meta_lora_used(root, str(raw.get("lora_used") or ""))
        if rel:
            ok.add(_norm_lora_relpath(rel))
    return ok


def _latest_job_per_lora(manager: MergeJobManager) -> dict[str, MergeJob]:
    by_lora: dict[str, list[MergeJob]] = {}
    for j in manager.list_jobs():
        req: dict[str, Any] = j.request or {}
        paths = req.get("lora_paths")
        if not isinstance(paths, list) or not paths:
            continue
        first = str(paths[0]).strip()
        if not first:
            continue
        k = _norm_lora_relpath(first)
        by_lora.setdefault(k, []).append(j)
    out: dict[str, MergeJob] = {}
    for k, jobs in by_lora.items():
        latest = max(jobs, key=lambda x: x.created_at)
        out[k] = latest
    return out


def _ui_status(mem: MergeJob | None, on_disk: bool) -> MergeUiStatus:
    if mem and mem.status in ("pending", "running"):
        return "merging"
    if mem and mem.status == "succeeded":
        return "success"
    if on_disk:
        return "success"
    if mem and mem.status == "failed":
        return "failed"
    if mem and mem.status == "cancelled":
        return "interrupted"
    return "none"


def training_merge_status_by_job_id(
    workspace: Path,
    training_rows: list[dict[str, Any]],
    manager: MergeJobManager,
) -> dict[str, MergeUiStatus]:
    disk = _lora_rels_with_disk_merge(workspace)
    jmap = _latest_job_per_lora(manager)
    out: dict[str, MergeUiStatus] = {}
    for row in training_rows:
        jid = str(row.get("job_id") or "").strip()
        path = str(row.get("path") or "").strip()
        if not jid or not path:
            continue
        k = _norm_lora_relpath(path)
        out[jid] = _ui_status(jmap.get(k), k in disk)
    return out
