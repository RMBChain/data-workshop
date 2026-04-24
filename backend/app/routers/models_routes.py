from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.services import hub_download_jobs
from backend.app.services import modelscope_manager as mscm

router = APIRouter(tags=["models"])


class DownloadBody(BaseModel):
    model_id: str = Field(
        ...,
        min_length=3,
        description="ModelScope 模型 id，如 Qwen/Qwen3-VL-2B-Instruct",
    )


class DeleteBody(BaseModel):
    model_id: str = Field(
        ...,
        min_length=3,
        description="与列表中 model_id 一致，仅删除本机 hub 缓存目录",
    )


@router.get("/models/hub")
async def list_hub() -> dict[str, Any]:
    running = await asyncio.to_thread(hub_download_jobs.list_running_jobs)
    active_mids = {str(j.get("model_id", "")).strip() for j in running if j.get("model_id")}
    await asyncio.to_thread(mscm.prune_stale_downloading_records, active_mids)
    return {
        "items": mscm.list_hub_models(),
        "hub_root": str(mscm.modelscope_hub_root()),
        "active_downloads": running,
    }


@router.post("/models/hub/download")
async def download_to_hub(body: DownloadBody) -> dict[str, Any]:
    """
    启动后台下载任务；通过 GET `/api/models/hub/download/{job_id}` 轮询进度。
    """
    mid = body.model_id.strip()
    if ".." in mid or mid.startswith(("/", ".")):
        raise HTTPException(status_code=400, detail="model_id 格式无效")
    meta = await asyncio.to_thread(hub_download_jobs.start_download_job, mid)
    return meta


@router.get("/models/hub/download/{job_id}")
async def download_job_status(job_id: str) -> dict[str, Any]:
    j = hub_download_jobs.get_job(job_id.strip())
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return hub_download_jobs.enrich_job_state(j)


@router.delete("/models/hub")
async def delete_hub_item(body: DeleteBody) -> dict[str, Any]:
    try:
        mscm.delete_hub_model(body.model_id.strip())
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="本地目录不存在") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e!s}") from e
    return {"ok": True}
