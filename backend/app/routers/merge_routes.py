from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.app.config import get_settings
from backend.app.services.merge_job_manager import MergeJobCreate, MergeJobManager
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["merge"])

_mgr: MergeJobManager | None = None


def _mgr() -> MergeJobManager:
    global _mgr
    if _mgr is None:
        _mgr = MergeJobManager(get_settings().workspace_root.resolve())
    return _mgr


@router.post("/merge/jobs")
async def create_merge_job(body: MergeJobCreate) -> dict[str, Any]:
    root = get_settings().workspace_root.resolve()
    try:
        resolve_under_workspace(root, body.output_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    job = _mgr().create_job(body)
    return {
        "id": job.id,
        "status": job.status,
        "log_path": str(job.log_path) if job.log_path else None,
        "error_message": job.error_message,
    }


@router.get("/merge/jobs/{job_id}")
async def get_merge_job(job_id: str) -> dict[str, Any]:
    j = _mgr().get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "id": j.id,
        "status": j.status,
        "created_at": j.created_at,
        "finished_at": j.finished_at,
        "return_code": j.return_code,
        "error_message": j.error_message,
        "request": j.request,
        "log_path": str(j.log_path) if j.log_path else None,
    }


@router.get("/merge/jobs/{job_id}/logs")
async def get_merge_logs(job_id: str) -> dict[str, Any]:
    j = _mgr().get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在")
    text, truncated = _mgr().read_log(job_id)
    return {"text": text, "truncated": truncated}


@router.post("/merge/jobs/{job_id}/cancel")
async def cancel_merge_job(job_id: str) -> dict[str, bool]:
    ok = _mgr().cancel(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="无法取消该任务")
    return {"ok": True}


@router.post("/merge/jobs/{job_id}/validate")
async def validate_merge_job(job_id: str) -> dict[str, Any]:
    j = _mgr().get(job_id)
    if not j or j.status != "succeeded":
        raise HTTPException(status_code=400, detail="仅成功完成的合并任务可校验")
    out = (j.request or {}).get("output_path", "")
    root = get_settings().workspace_root.resolve()
    p = resolve_under_workspace(root, out)
    if not p.is_dir():
        return {"ok": False, "detail": "输出目录不存在"}
    cfg = p / "config.json"
    has_weights = (p / "model.safetensors").is_file() or (p / "pytorch_model.bin").is_file()
    if not cfg.is_file() and not has_weights and not (p / "adapter_config.json").is_file():
        return {"ok": False, "detail": "未检测到常见模型或适配器元数据文件。"}
    return {
        "ok": True,
        "path": out.replace("\\", "/"),
        "message": "目录存在，可用于推理沙盒中选用（路径需为工作区相对路径）。",
    }
