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


def _output_relpath_from_request(req: dict[str, Any]) -> str | None:
    o = (req or {}).get("output_path")
    if o is None:
        return None
    s = str(o).strip().replace("\\", "/")
    return s or None


def _disk_lora_to_output_relpath(workspace: Path) -> dict[str, str]:
    """已写入磁盘的合法合并：LoRA 工作区相对路径 -> 合并输出目录（工作区相对）。"""
    root = workspace.resolve()
    out_map: dict[str, str] = {}
    out_root = root / "output"
    if not out_root.is_dir():
        return out_map
    for meta_path in out_root.rglob("workshop_merge_meta.json"):
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
        if not rel:
            continue
        k = _norm_lora_relpath(rel)
        try:
            out_rel = parent.resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            continue
        out_map[k] = out_rel
    return out_map


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
    """优先使用内存中最新 MergeJob 的状态（反映最近尝试结果），仅在无内存记录时回退到磁盘成功标记。
    这避免了之前「磁盘成功掩盖后续失败尝试」的逻辑漏洞。"""
    if mem:
        if mem.status in ("pending", "running"):
            return "merging"
        if mem.status == "succeeded":
            return "success"
        if mem.status == "failed":
            return "failed"
        if mem.status == "cancelled":
            return "interrupted"
        # 其他状态（如旧的）回退到磁盘检查
    if on_disk:
        return "success"
    return "none"


def _merge_output_relpath_for_lora(
    mem: MergeJob | None, on_disk: bool, lora_key: str, disk_map: dict[str, str]
) -> str | None:
    st = _ui_status(mem, on_disk)
    if st != "success":
        return None
    if mem and mem.status == "succeeded":
        o = _output_relpath_from_request(mem.request or {})
        if o:
            return o
    return disk_map.get(lora_key)


def training_merge_status_by_job_id(
    workspace: Path,
    training_rows: list[dict[str, Any]],
    manager: MergeJobManager,
) -> tuple[dict[str, MergeUiStatus], dict[str, str | None]]:
    disk_map = _disk_lora_to_output_relpath(workspace)
    disk = set(disk_map.keys())
    jmap = _latest_job_per_lora(manager)
    out: dict[str, MergeUiStatus] = {}
    paths: dict[str, str | None] = {}
    for row in training_rows:
        jid = str(row.get("job_id") or "").strip()
        path = str(row.get("path") or "").strip()
        if not jid or not path:
            continue
        k = _norm_lora_relpath(path)
        on_disk = k in disk
        mem = jmap.get(k)
        out[jid] = _ui_status(mem, on_disk)
        paths[jid] = _merge_output_relpath_for_lora(mem, on_disk, k, disk_map)
    return out, paths
