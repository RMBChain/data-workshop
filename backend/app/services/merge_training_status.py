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


def _scan_disk_merge_outputs(
    workspace: Path,
) -> tuple[dict[str, str], dict[str, str]]:
    """扫描 workshop_merge_meta.json。
    返回 (lora 相对路径 -> 合并输出目录, 训练 job_id -> 合并输出目录)。后者来自 meta 中的 training_job_id 字段，用于
    当前列表中 LoRA 路径与合并时不一致时仍能识别成功状态。"""
    root = workspace.resolve()
    lora_to_out: dict[str, str] = {}
    tid_to_out: dict[str, str] = {}
    tid_mtimes: dict[str, float] = {}
    out_root = root / "output"
    if not out_root.is_dir():
        return lora_to_out, tid_to_out
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
        try:
            out_rel = parent.resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            continue
        tid = str(raw.get("training_job_id") or "").strip()
        if tid:
            try:
                mtime = parent.stat().st_mtime
            except OSError:
                mtime = 0.0
            if mtime > tid_mtimes.get(tid, -1.0):
                tid_to_out[tid] = out_rel
                tid_mtimes[tid] = mtime
        rel = _lora_relpath_from_meta_lora_used(root, str(raw.get("lora_used") or ""))
        if not rel:
            continue
        k = _norm_lora_relpath(rel)
        lora_to_out[k] = out_rel
    return lora_to_out, tid_to_out


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


def _latest_job_per_training_id(manager: MergeJobManager) -> dict[str, MergeJob]:
    """按 request.training_job_id 索引最新 MergeJob。当列表中 LoRA 相对路径与合并请求中不一致时，仍能对上该训练任务。"""
    by_id: dict[str, list[MergeJob]] = {}
    for j in manager.list_jobs():
        req: dict[str, Any] = j.request or {}
        tid = str(req.get("training_job_id") or "").strip()
        if not tid:
            continue
        by_id.setdefault(tid, []).append(j)
    out: dict[str, MergeJob] = {}
    for tid, jobs in by_id.items():
        out[tid] = max(jobs, key=lambda x: x.created_at)
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
    mem: MergeJob | None,
    on_disk: bool,
    lora_key: str,
    disk_map: dict[str, str],
    training_job_id: str,
    tid_to_out: dict[str, str],
) -> str | None:
    st = _ui_status(mem, on_disk)
    if st != "success":
        return None
    if mem and mem.status == "succeeded":
        o = _output_relpath_from_request(mem.request or {})
        if o:
            return o
    return disk_map.get(lora_key) or tid_to_out.get(training_job_id)


def training_merge_status_by_job_id(
    workspace: Path,
    training_rows: list[dict[str, Any]],
    manager: MergeJobManager,
) -> tuple[dict[str, MergeUiStatus], dict[str, str | None]]:
    disk_map, tid_to_out = _scan_disk_merge_outputs(workspace)
    disk = set(disk_map.keys())
    jmap = _latest_job_per_lora(manager)
    jtid = _latest_job_per_training_id(manager)
    out: dict[str, MergeUiStatus] = {}
    paths: dict[str, str | None] = {}
    for row in training_rows:
        jid = str(row.get("job_id") or "").strip()
        path = str(row.get("path") or "").strip()
        if not jid:
            continue
        if not path:
            out[jid] = "none"
            paths[jid] = None
            continue
        k = _norm_lora_relpath(path)
        on_disk = k in disk or bool(jid and jid in tid_to_out)
        mem = jmap.get(k) if jmap.get(k) is not None else jtid.get(jid)
        out[jid] = _ui_status(mem, on_disk)
        paths[jid] = _merge_output_relpath_for_lora(
            mem, on_disk, k, disk_map, jid, tid_to_out
        )
    return out, paths
