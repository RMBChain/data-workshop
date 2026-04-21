from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.app.config import get_settings
from backend.app.services.job_manager import TrainJobCreate, TrainingJobManager

router = APIRouter(tags=["training"])

_manager: TrainingJobManager | None = None


def _manager_singleton() -> TrainingJobManager:
    global _manager
    if _manager is None:
        _manager = TrainingJobManager(get_settings().workspace_root.resolve())
    return _manager


@router.get("/training/jobs")
async def list_training_jobs() -> dict:
    jobs = _manager_singleton().list_jobs()
    return {
        "items": [
            {
                "id": j.id,
                "status": j.status,
                "created_at": j.created_at,
                "finished_at": j.finished_at,
                "return_code": j.return_code,
                "error_message": j.error_message,
                "request": j.request,
            }
            for j in jobs
        ]
    }


@router.post("/training/jobs")
async def create_training_job(body: TrainJobCreate) -> dict:
    job = _manager_singleton().create_job(body)
    return {
        "id": job.id,
        "status": job.status,
        "log_path": str(job.log_path) if job.log_path else None,
        "error_message": job.error_message,
    }


@router.get("/training/jobs/{job_id}")
async def get_training_job(job_id: str) -> dict:
    job = _manager_singleton().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "id": job.id,
        "status": job.status,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "return_code": job.return_code,
        "error_message": job.error_message,
        "request": job.request,
    }


@router.get("/training/jobs/{job_id}/logs")
async def get_training_logs(job_id: str) -> dict:
    job = _manager_singleton().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    text, truncated = _manager_singleton().read_log(job_id)
    return {"text": text, "truncated": truncated}


@router.post("/training/jobs/{job_id}/cancel")
async def cancel_training_job(job_id: str) -> dict:
    ok = _manager_singleton().cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="无法取消该任务")
    return {"ok": True}
