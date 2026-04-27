from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.app.db import get_connection
from backend.app.services.job_manager import _latest_checkpoint_relpath

# 与 TrainJobCreate.workshop_pinned_lora_relpath 一致
WORKSHOP_PINNED_LORA_RELPATH = "workshop_pinned_lora_relpath"


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


def _max_step_adapter_relpath_under_out(out: Path, workspace: Path) -> str | None:
    """在 run 根目录下，在父路径中出现 checkpoint-数字 的目录中，选步数最大且含 adapter 的路径。"""
    ws = workspace.resolve()
    out_res = out.resolve()
    best: tuple[int, float, Path] | None = None
    try:
        for cfg in out.rglob("adapter_config.json"):
            if not cfg.is_file():
                continue
            par = cfg.parent
            step = -1
            q: Path = par
            for _ in range(128):
                m = re.match(r"^checkpoint-(\d+)$", q.name, re.IGNORECASE)
                if m:
                    step = int(m.group(1))
                    break
                if q == out or q == q.parent:
                    break
                try:
                    if q.resolve() == out_res:
                        break
                except OSError:
                    break
                q = q.parent
            if step < 0:
                continue
            try:
                mt = par.stat().st_mtime
            except OSError:
                mt = 0.0
            if best is None or step > best[0] or (step == best[0] and mt > best[1]):
                best = (step, mt, par)
    except OSError:
        return None
    if best is None:
        return None
    try:
        return best[2].resolve().relative_to(ws).as_posix()
    except ValueError:
        return None


def _mtime_newest_adapter_relpath_under_out(out: Path, workspace: Path) -> str | None:
    """无标准 checkpoint-步数 目录时的兜底：子树内最近修改的 adapter（仅用于未 pin 的兼容，不参与「已固定路径」）。"""
    ws = workspace.resolve()
    best_p: Path | None = None
    best_t = 0.0
    try:
        for cfg in out.rglob("adapter_config.json"):
            if not cfg.is_file():
                continue
            try:
                t = cfg.stat().st_mtime
            except OSError:
                continue
            if t > best_t:
                best_t = t
                best_p = cfg.parent
    except OSError:
        return None
    if best_p is None:
        return None
    try:
        return best_p.resolve().relative_to(ws).as_posix()
    except ValueError:
        return None


def adapter_relpath_by_max_checkpoint(workspace: Path, req: dict[str, Any]) -> str | None:
    """不读 pin。仅按「步数最大」的 checkpoint 目录 + 根目录 adapter 解析，用于训练成功时写入 pin 与未覆盖 pin 的列表。"""
    base = _safe_workspace_rel(str(req.get("swift_run_relpath") or req.get("output_dir") or ""))
    if not base:
        return None
    ws = workspace.resolve()
    out = ws / base
    if not out.is_dir():
        return base
    ckpt_rel = _latest_checkpoint_relpath(workspace, base)
    if ckpt_rel:
        ckpt = ws / ckpt_rel.replace("\\", "/")
        if ckpt.is_dir() and (ckpt / "adapter_config.json").is_file():
            return ckpt_rel.replace("\\", "/")
    if (out / "adapter_config.json").is_file():
        return base
    alt = _max_step_adapter_relpath_under_out(out, workspace)
    if alt:
        return alt
    return _mtime_newest_adapter_relpath_under_out(out, workspace)


def _adapter_relpath_for_registered_training(workspace: Path, req: dict[str, Any]) -> str | None:
    """优先用训练成功时写死的 LoRA 路径，避免同一数据集又产出新 checkpoint 后列表/合并/沙盒都跟着漂移。"""
    ws = workspace.resolve()
    raw_pin = (str(req.get(WORKSHOP_PINNED_LORA_RELPATH) or "")).strip()
    if raw_pin and raw_pin != "—":
        pr = _safe_workspace_rel(raw_pin.replace("\\", "/"))
        if pr and (ws / pr / "adapter_config.json").is_file():
            return pr
    return adapter_relpath_by_max_checkpoint(workspace, req)


def apply_workshop_pinned_lora_relpath_on_success(workspace: Path, job: Any) -> None:
    """训练进程成功退出后，将当时「步数最大」的 adapter 相对路径写入 request（供合并/推理/评测使用）。"""
    st = getattr(job, "status", None)
    if st != "succeeded":
        return
    req = getattr(job, "request", None)
    if not isinstance(req, dict):
        return
    rel = adapter_relpath_by_max_checkpoint(workspace, req)
    if not rel:
        return
    job.request = {**req, WORKSHOP_PINNED_LORA_RELPATH: rel}


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
    """仅 `training_jobs_persist` 中状态为 succeeded 的训练任务。path 以训练成功时落库的
    `workshop_pinned_lora_relpath` 为准（该次任务结束时步数最大的 checkpoint），未落库时按当前磁盘
    步数最大 adapter 解析；不随后续同目录新 checkpoint 漂移。"""
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


def list_merge_page_training_rows(workspace: Path) -> list[dict[str, Any]]:
    """合并页列表：含训练成功与失败等已落库任务（不含仅保存参数未开训）。含 `training_status`、`error_message`。"""
    root = workspace.resolve()
    conn = get_connection(root)
    rows = conn.execute(
        "SELECT id, request_json, status, error_message FROM training_jobs_persist "
        "WHERE status NOT IN ('parameters_saved') "
        "ORDER BY CAST(created_at AS REAL) DESC"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        jid = str(row["id"])
        db_status = str(row.get("status") or "").strip() or "unknown"
        err_db = row.get("error_message")
        err_s = str(err_db).strip() if err_db is not None and str(err_db).strip() else None
        raw = row["request_json"] or "{}"
        try:
            req = json.loads(raw) if isinstance(raw, str) else {}
        except json.JSONDecodeError:
            req = {}
        if not isinstance(req, dict):
            req = {}
        adapter_rel: str | None = None
        if db_status == "succeeded":
            adapter_rel = _adapter_relpath_for_registered_training(root, req)
        else:
            adapter_rel = adapter_relpath_by_max_checkpoint(workspace, req)
            if not adapter_rel:
                adapter_rel = _adapter_relpath_for_registered_training(root, req)
        jn = _training_job_display_name(req, jid)
        tbm = str(req.get("model") or "").strip()
        project_title = str(req.get("project_title") or "").strip() or None
        batch_name = str(req.get("batch_name") or "").strip() or None
        dataset_name = str(req.get("dataset_name") or "").strip() or None
        dvid = str(req.get("dataset_version_id") or "").strip() or None
        ad = adapter_rel or ""
        swift_tv = _ms_swift_train_version_segment(req, ad) if ad else None
        out.append(
            {
                "id": f"lora:{ad}" if ad else f"job:{jid}",
                "kind": "lora" if ad else "train_job",
                "path": ad,
                "train_base_model": tbm or None,
                "job_id": jid,
                "job_name": jn,
                "label": jn,
                "project_title": project_title,
                "batch_name": batch_name,
                "dataset_name": dataset_name,
                "dataset_version_id": dvid,
                "swift_train_version": swift_tv,
                "training_status": db_status,
                "error_message": err_s,
            }
        )
    return out[:300]


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
