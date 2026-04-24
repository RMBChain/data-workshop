from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.db import get_connection
from backend.app.services.inference_models import (
    _adapter_relpath_for_registered_training,
    list_workspace_models,
)
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["eval"])

_DEFAULT_VAL_JSONL = "data/val.jsonl"


def _suggest_val_jsonl_from_lora_used(workspace: Path, lora_used: str) -> str:
    """根据合并元数据中的 LoRA 路径，在已成功训练任务中匹配并返回当时使用的 val 集相对路径。"""
    try:
        lora_p = Path(lora_used).resolve()
        lora_rel = lora_p.relative_to(workspace.resolve()).as_posix()
    except (ValueError, OSError):
        return _DEFAULT_VAL_JSONL
    conn = get_connection(workspace)
    rows = conn.execute(
        "SELECT request_json FROM training_jobs_persist WHERE status = 'succeeded' "
        "ORDER BY CAST(created_at AS REAL) DESC"
    ).fetchall()
    for row in rows or []:
        raw = row["request_json"] or "{}"
        try:
            req = json.loads(raw) if isinstance(raw, str) else {}
        except json.JSONDecodeError:
            continue
        if not isinstance(req, dict):
            continue
        adapter = _adapter_relpath_for_registered_training(workspace, req)
        if not adapter:
            continue
        if adapter.replace("\\", "/") == lora_rel.replace("\\", "/"):
            vd = str(req.get("val_dataset") or "").strip().replace("\\", "/")
            return vd if vd else _DEFAULT_VAL_JSONL
    return _DEFAULT_VAL_JSONL


@router.get("/eval/merged-models")
async def list_merged_models_for_eval() -> dict[str, Any]:
    """工作区内通过 LoRA 合并产出的模型目录，及建议的验证集 jsonl 相对路径（与对应训练任务一致，否则为 data/val.jsonl）。"""
    root = get_settings().workspace_root.resolve()
    by_path: dict[str, dict[str, Any]] = {}
    out_dir = root / "output"
    if out_dir.is_dir():
        for meta_path in out_dir.rglob("workshop_merge_meta.json"):
            if "merge-jobs" in meta_path.parts:
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict):
                continue
            rel = meta_path.parent.relative_to(root).as_posix()
            lora_raw = str(meta.get("lora_used") or "").strip()
            val_jsonl = (
                _suggest_val_jsonl_from_lora_used(root, lora_raw) if lora_raw else _DEFAULT_VAL_JSONL
            )
            by_path[rel] = {
                "id": f"merged:{rel}",
                "path": rel,
                "label": f"合并模型 · {meta_path.parent.name}",
                "val_jsonl": val_jsonl,
            }
    for m in list_workspace_models(root):
        if m.get("kind") != "merge":
            continue
        p = str(m.get("path") or "").strip().replace("\\", "/")
        if not p or p in by_path:
            continue
        by_path[p] = {
            "id": m.get("id") or f"merged:{p}",
            "path": p,
            "label": str(m.get("label") or f"合并/权重 · {p}"),
            "val_jsonl": _DEFAULT_VAL_JSONL,
        }
    items = sorted(by_path.values(), key=lambda x: str(x.get("path") or ""))
    return {"items": items, "default_val_jsonl": _DEFAULT_VAL_JSONL}


class EvalJobCreate(BaseModel):
    data_jsonl: str = Field(..., description="工作区内 jsonl 相对路径，或经数据集构建的 train/val 路径")
    enable_accuracy: bool = True
    enable_bleu: bool = True
    enable_rouge: bool = True


@dataclass
class _EvalJob:
    id: str
    status: str
    created_at: float
    finished_at: float | None = None
    request: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    error_message: str | None = None


_jobs: dict[str, _EvalJob] = {}
_lock = threading.Lock()


@router.post("/eval/jobs")
async def create_eval_job(body: EvalJobCreate) -> dict[str, Any]:
    root = get_settings().workspace_root.resolve()
    p = resolve_under_workspace(root, body.data_jsonl)
    if not p.is_file():
        raise HTTPException(status_code=400, detail="数据文件不存在或不可访问")
    job_id = uuid.uuid4().hex
    j = _EvalJob(id=job_id, status="pending", created_at=time.time(), request=body.model_dump())
    with _lock:
        _jobs[job_id] = j

    def _run() -> None:
        with _lock:
            jj = _jobs.get(job_id)
            if not jj:
                return
            jj.status = "running"
        time.sleep(0.2)
        try:
            lines = p.read_text(encoding="utf-8").splitlines()[:200]
            n = 0
            items: list[dict[str, Any]] = []
            for i, line in enumerate(lines):
                n += 1
                rec = json.loads(line) if line.strip() else {}
                # MVP：占位式逐条结果
                items.append(
                    {
                        "index": i,
                        "ok": True,
                        "message": "示例占位：可接入与 golden 的对比。",
                    }
                )
            summary = {
                "line_count": n,
                "metrics": {
                    "accuracy": 0.0 if body.enable_accuracy else None,
                    "bleu": 0.0 if body.enable_bleu else None,
                    "rougeL": 0.0 if body.enable_rouge else None,
                },
                "note": "MVP 预留指标位；完整评测需与上游标注格式对齐后接入。",
            }
            rep = root / "output" / "eval-reports"
            rep.mkdir(parents=True, exist_ok=True)
            (rep / f"{job_id}.json").write_text(
                json.dumps({"summary": summary, "items": items}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with _lock:
                jj = _jobs.get(job_id)
                if jj and jj.status != "cancelled":
                    jj.status = "succeeded"
                    jj.finished_at = time.time()
                    jj.summary = summary
                    jj.items = items
        except Exception as e:
            with _lock:
                jj = _jobs.get(job_id)
                if jj:
                    jj.status = "failed"
                    jj.finished_at = time.time()
                    jj.error_message = str(e)

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "pending"}


@router.get("/eval/jobs/{job_id}")
async def get_eval_job(job_id: str) -> dict[str, Any]:
    with _lock:
        j = _jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "id": j.id,
        "status": j.status,
        "summary": j.summary,
        "error_message": j.error_message,
    }


@router.get("/eval/jobs/{job_id}/items")
async def get_eval_items(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    with _lock:
        j = _jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在")
    total = len(j.items)
    start = (page - 1) * page_size
    chunk = j.items[start : start + page_size]
    return {"items": chunk, "page": page, "page_size": page_size, "total": total}


@router.get("/eval/reports")
async def list_eval_reports() -> dict[str, Any]:
    root = get_settings().workspace_root.resolve() / "output" / "eval-reports"
    if not root.is_dir():
        return {"items": []}
    items = [
        {
            "id": p.stem,
            "path": str(p.relative_to(get_settings().workspace_root)).replace("\\", "/"),
            "mtime": p.stat().st_mtime,
        }
        for p in root.glob("*.json")
    ]
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"items": items}


@router.get("/eval/reports/{report_id}/export")
async def export_eval_report(report_id: str) -> dict[str, str]:
    root = get_settings().workspace_root.resolve()
    p = root / "output" / "eval-reports" / f"{report_id}.json"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="报告不存在")
    return {
        "path": str(p.relative_to(root)).replace("\\", "/"),
        "message": "JSON 报告，路径相对于工作区。",
    }
