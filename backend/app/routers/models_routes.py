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
    return {"items": mscm.list_hub_models(), "hub_root": str(mscm.modelscope_hub_root())}


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
    out = dict(j)
    cur_t = int(out.get("current_file_total") or 0)
    cur_d = int(out.get("current_file_done") or 0)
    out["current_file_percent"] = round(100.0 * cur_d / cur_t, 1) if cur_t > 0 else None

    tb = int(out.get("total_bytes_expected") or 0)
    bd = int(out.get("bytes_downloaded") or 0)
    ft = int(out.get("files_total_expected") or 0)
    fc = int(out.get("files_completed") or 0)
    overall: float | None = None
    if tb > 0:
        overall = min(100.0, max(0.0, round(100.0 * bd / tb, 2)))
    elif ft > 0:
        part = (cur_d / cur_t) if cur_t > 0 else 0.0
        overall = min(100.0, max(0.0, round(100.0 * (fc + part) / ft, 2)))
    out["overall_percent"] = overall
    return out


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
