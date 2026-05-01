from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.deps import WorkspaceRoot
from backend.app.db import get_connection
from backend.app.services.inference_models import (
    _adapter_relpath_for_registered_training,
    list_workspace_models,
)
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["eval"])

_DEFAULT_VAL_JSONL = "data/val.jsonl"


def _val_relpath_for_training_request(conn: sqlite3.Connection, req: dict[str, Any]) -> str:
    """优先用「数据集版本」表中的 val 路径，与「数据集 / 训练」中配置一致；否则回退到训练请求中的 val_dataset。"""
    dvid = str(req.get("dataset_version_id") or "").strip()
    if dvid:
        row = conn.execute(
            "SELECT val_relpath FROM dws_datasets WHERE id = ? AND status = 'succeeded'",
            (dvid,),
        ).fetchone()
        if row and row["val_relpath"] is not None:
            s = str(row["val_relpath"] or "").strip().replace("\\", "/")
            if s:
                return s
    vd = str(req.get("val_dataset") or "").strip().replace("\\", "/")
    return vd if vd else _DEFAULT_VAL_JSONL


def _val_jsonl_and_dataset_id_for_lora(
    workspace: Path, lora_used: str
) -> tuple[str, str | None]:
    """
    根据合并使用的 LoRA 在已成功任务中反查训练请求：
    通过 dataset_version_id 用 dws_datasets.val_relpath 作为验证集（即 val jsonl 工作区相对路径）。
    """
    try:
        lora_p = Path(lora_used).resolve()
        lora_rel = lora_p.relative_to(workspace.resolve()).as_posix()
    except (ValueError, OSError):
        return _DEFAULT_VAL_JSONL, None
    conn = get_connection(workspace)
    rows = conn.execute(
        "SELECT request_json FROM dws_trains WHERE status = 'succeeded' "
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
            dvid = str(req.get("dataset_version_id") or "").strip() or None
            return _val_relpath_for_training_request(conn, req), dvid
    return _DEFAULT_VAL_JSONL, None


@router.get("/eval/merged-models")
async def list_merged_models_for_eval(root: WorkspaceRoot) -> dict[str, Any]:
    """工作区内通过 LoRA 合并产出的模型目录，及建议的验证集 jsonl 相对路径（与对应训练任务一致，否则为 data/val.jsonl）。"""
    by_path: dict[str, dict[str, Any]] = {}
    for base in (root / "merged", root / "output"):
        if not base.is_dir():
            continue
        for meta_path in base.rglob("workshop_merge_meta.json"):
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
            if lora_raw:
                val_jsonl, dvid = _val_jsonl_and_dataset_id_for_lora(root, lora_raw)
            else:
                val_jsonl, dvid = _DEFAULT_VAL_JSONL, None
            merge_jid = str(meta.get("merge_job_id") or "").strip() or None
            by_path[rel] = {
                "id": f"merged:{rel}",
                "path": rel,
                "label": f"合并模型 · {meta_path.parent.name}",
                "val_jsonl": val_jsonl,
                "dataset_version_id": dvid,
                "merge_job_id": merge_jid,
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
            "dataset_version_id": None,
            "merge_job_id": None,
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
async def create_eval_job(root: WorkspaceRoot, body: EvalJobCreate) -> dict[str, Any]:
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
                if line.strip():
                    json.loads(line)
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
async def list_eval_reports(ws: WorkspaceRoot) -> dict[str, Any]:
    rep_root = ws / "output" / "eval-reports"
    if not rep_root.is_dir():
        return {"items": []}
    items = [
        {
            "id": p.stem,
            "path": str(p.relative_to(ws)).replace("\\", "/"),
            "mtime": p.stat().st_mtime,
        }
        for p in rep_root.glob("*.json")
    ]
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"items": items}


@router.get("/eval/reports/{report_id}/export")
async def export_eval_report(root: WorkspaceRoot, report_id: str) -> dict[str, str]:
    p = root / "output" / "eval-reports" / f"{report_id}.json"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="报告不存在")
    return {
        "path": str(p.relative_to(root)).replace("\\", "/"),
        "message": "JSON 报告，路径相对于工作区。",
    }
