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
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["eval"])


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
