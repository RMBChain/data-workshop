from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.app.config import get_settings
from backend.app.services.inference_models import list_registered_training_models
from backend.app.services.merge_job_manager import MergeJobCreate, MergeJobManager
from backend.app.services.merge_log_progress import parse_merge_log_progress
from backend.app.services.merge_training_status import training_merge_status_by_job_id
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["merge"])

_merge_manager: MergeJobManager | None = None


def get_merge_manager() -> MergeJobManager:
    global _merge_manager
    if _merge_manager is None:
        _merge_manager = MergeJobManager(get_settings().workspace_root.resolve())
    return _merge_manager


@router.post("/merge/jobs")
async def create_merge_job(body: MergeJobCreate) -> dict[str, Any]:
    root = get_settings().workspace_root.resolve()
    try:
        resolve_under_workspace(root, body.output_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    job = get_merge_manager().create_job(body)
    return {
        "id": job.id,
        "status": job.status,
        "log_path": str(job.log_path) if job.log_path else None,
        "error_message": job.error_message,
    }


@router.get("/merge/training-status")
async def get_merge_training_status() -> dict[str, Any]:
    """各训练 job_id 对应的 LoRA 合并态：未合并 / 合并中 / 已取消 / 失败 / 成功。
    内存中最新 MergeJob 优先（避免磁盘成功掩盖后续失败尝试），无内存记录时回退到磁盘 workshop_merge_meta.json 检测。
    output_path_by_job_id：仅合并且成功解析到输出目录时非 null（成功合并任务 request.output_path 或磁盘 meta 旁目录）。"""
    root = get_settings().workspace_root.resolve()
    rows = list_registered_training_models(root)
    m = get_merge_manager()
    by_jid, output_by_jid = training_merge_status_by_job_id(root, rows, m)
    return {
        "status_by_job_id": by_jid,
        "output_path_by_job_id": output_by_jid,
    }


@router.get("/merge/jobs/{job_id}")
async def get_merge_job(job_id: str) -> dict[str, Any]:
    j = get_merge_manager().get(job_id)
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
    j = get_merge_manager().get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在")
    text, truncated = get_merge_manager().read_log(job_id)
    return {"text": text, "truncated": truncated, "progress": parse_merge_log_progress(text)}


@router.post("/merge/jobs/{job_id}/cancel")
async def cancel_merge_job(job_id: str) -> dict[str, bool]:
    ok = get_merge_manager().cancel(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="无法取消该任务")
    return {"ok": True}


@router.post("/merge/jobs/{job_id}/validate")
async def validate_merge_job(job_id: str) -> dict[str, Any]:
    j = get_merge_manager().get(job_id)
    if not j or j.status != "succeeded":
        raise HTTPException(status_code=400, detail="仅成功完成的合并任务可校验")
    out = (j.request or {}).get("output_path", "")
    root = get_settings().workspace_root.resolve()
    p = resolve_under_workspace(root, out)
    if not p.is_dir():
        return {"ok": False, "detail": "输出目录不存在"}
    cfg = p / "config.json"
    has_weights = (
        (p / "model.safetensors").is_file()
        or (p / "pytorch_model.bin").is_file()
        or any(p.glob("model-*-of-*.safetensors"))
    )
    if not cfg.is_file() and not has_weights and not (p / "adapter_config.json").is_file():
        return {"ok": False, "detail": "未检测到常见模型或适配器元数据文件。"}
    return {
        "ok": True,
        "path": out.replace("\\", "/"),
        "message": "目录存在，可用于推理沙盒中选用（路径需为工作区相对路径）。",
    }
