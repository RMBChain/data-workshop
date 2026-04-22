from __future__ import annotations

import asyncio
import json
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.services.job_manager import TrainJobCreate, TrainingJobManager
from backend.app.services.training_metrics import parse_training_log_metrics, parse_training_progress

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


@router.delete("/training/jobs/{job_id}")
async def delete_training_job(job_id: str) -> dict:
    ok = _manager_singleton().delete_job(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="仅可删除已结束且非运行中任务，或请先用取消。")
    return {"ok": True}


@router.post("/training/jobs/{job_id}/retry")
async def retry_training_job(job_id: str) -> dict:
    j = _manager_singleton().retry_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="原任务不存在或参数缺失")
    return {
        "id": j.id,
        "status": j.status,
        "log_path": str(j.log_path) if j.log_path else None,
        "error_message": j.error_message,
    }


@router.get("/training/jobs/{job_id}/metrics")
async def get_training_metrics(job_id: str) -> dict:
    j = _manager_singleton().get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在")
    text, _t = _manager_singleton().read_log(job_id, max_bytes=2_000_000)
    ne: int | None = None
    if j.request and isinstance(j.request, dict):
        raw = j.request.get("num_train_epochs")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            ne = int(raw)
    prog = parse_training_progress(text, num_train_epochs=ne)
    st = j.status
    if st == "succeeded" and isinstance(prog.get("percent"), (int, float)) and float(prog["percent"]) < 100:
        prog = {**prog, "percent": 100.0, "label": "任务成功"}
    elif st in ("failed", "cancelled"):
        pl = prog.get("label")
        if isinstance(pl, str) and pl and prog.get("percent") is not None:
            base = f"已结束（{st}） · 最近进度: {pl}"
        elif prog.get("percent") is None:
            base = f"已结束（{st}）"
        else:
            base = f"已结束（{st}）"
        em = (j.error_message or "").strip()
        if em:
            base = f"{base} — {em}"
        elif j.return_code is not None and j.return_code != 0:
            base = f"{base}（子进程退出码 {j.return_code}）"
        prog = {**prog, "label": base}
    return {"job_id": job_id, "series": parse_training_log_metrics(text), "progress": prog}


@router.get("/training/jobs/{job_id}/logs/stream")
async def stream_training_logs(
    job_id: str,
    interval: float = Query(1.0, ge=0.2, le=5.0),
) -> Any:
    """简易 SSE：定期推送当前日志尾（与轮询等效，前端可二选一）。"""

    async def _gen() -> Any:
        last = ""
        while True:
            j = _manager_singleton().get_job(job_id)
            if not j:
                yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                return
            text, truncated = _manager_singleton().read_log(job_id)
            if text != last:
                last = text
                yield f"data: {json.dumps({'text': text, 'truncated': truncated, 'status': j.status}, ensure_ascii=False)}\n\n"
            if j.status in ("succeeded", "failed", "cancelled"):
                yield f"data: {json.dumps({'status': j.status, 'end': True}, ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(interval)

    return StreamingResponse(_gen(), media_type="text/event-stream")


class YamlBody(BaseModel):
    yaml: str = Field(..., min_length=1, description="训练 YAML 文本")


@router.post("/training/config/yaml/parse")
async def parse_training_yaml(body: YamlBody) -> dict[str, Any]:
    try:
        data = yaml.safe_load(body.yaml)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"YAML 无法解析: {e}") from e
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="根节点须为对象")
    try:
        t = TrainJobCreate.model_validate(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"与训练参数字段不完全匹配: {e}") from e
    return {"ok": True, "params": t.model_dump()}


@router.get("/training/config/yaml/export")
async def export_training_yaml(job_id: str | None = Query(None)) -> Response:
    if not job_id:
        t = TrainJobCreate()
    else:
        j = _manager_singleton().get_job(job_id)
        if not j or not j.request:
            raise HTTPException(status_code=404, detail="任务不存在或缺少参数")
        t = TrainJobCreate.model_validate(j.request)
    text = f"# 数据工坊 训练配置（可粘贴回「从 YAML 导入」）\n{yaml.safe_dump(t.model_dump(), allow_unicode=True, sort_keys=False)}"
    return Response(
        content=text,
        media_type="text/yaml; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="train-config.yaml"'},
    )
