from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.app.db import (
    get_connection,
    merge_export_merged_path_map,
    merge_export_zip_get,
    merge_export_zip_map,
    merge_job_get_latest_by_training_id,
)
from backend.app.deps import WorkspaceRoot
from backend.app.services.inference_models import list_merge_page_training_rows
from backend.app.services.job_manager import get_training_manager
from backend.app.services.merge_job_manager import (
    MergeJobCreate,
    get_merge_manager,
    read_merge_log_file,
)
from backend.app.services.merge_training_status import training_merge_status_by_job_id
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["merge"])


@router.post("/merge/jobs")
async def create_merge_job(root: WorkspaceRoot, body: MergeJobCreate) -> dict[str, Any]:
    try:
        resolve_under_workspace(root, "output/merged-workshop")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    job = get_merge_manager().create_job(body)
    return {
        "id": job.id,
        "status": job.status,
        "log_path": str(job.log_path) if job.log_path else None,
        "error_message": job.error_message,
    }


@router.get("/merge/training-candidates")
async def list_merge_training_candidates(root: WorkspaceRoot) -> dict[str, Any]:
    """合并页列表：训练成功/失败等任务（与训练页列表数据来源一致但范围更广，用于合并与只读回看的分流）。"""
    return {"items": list_merge_page_training_rows(root)}


@router.get("/merge/training-status")
async def get_merge_training_status(root: WorkspaceRoot) -> dict[str, Any]:
    """各训练 job_id 对应的 LoRA 合并态：未合并 / 合并中 / 已取消 / 失败 / 成功。
    内存中最新 MergeJob 优先（避免磁盘成功掩盖后续失败尝试），无内存记录时回退到磁盘 workshop_merge_meta.json 检测。
    output_path_by_job_id：仅合并且成功解析到输出目录时非 null（成功合并任务 request.output_path 或磁盘 meta 旁目录）。
    zip_path_by_job_id：合并成功且已打包入库时非 null（工作区相对路径）。"""
    rows = list_merge_page_training_rows(root)
    m = get_merge_manager()
    by_jid, output_by_jid = training_merge_status_by_job_id(root, rows, m)
    conn = get_connection(root.resolve())
    jids = [str(r.get("job_id") or "").strip() for r in rows if str(r.get("job_id") or "").strip()]
    zip_by = merge_export_zip_map(conn, jids)
    merged_path_by = merge_export_merged_path_map(conn, jids)
    for jid in jids:
        if not jid:
            continue
        if not zip_by.get(jid):
            continue
        st = by_jid.get(jid, "none")
        if st == "none":
            by_jid[jid] = "success"
        o = output_by_jid.get(jid)
        if o is not None and str(o).strip():
            continue
        mp = merged_path_by.get(jid)
        if mp and str(mp).strip():
            output_by_jid[jid] = str(mp).strip().replace("\\", "/")
    return {
        "status_by_job_id": by_jid,
        "output_path_by_job_id": output_by_jid,
        "zip_path_by_job_id": zip_by,
    }


@router.get("/merge/training-jobs/{training_job_id}/train-run-logs")
async def get_train_run_logs_on_merge_page(root: WorkspaceRoot, training_job_id: str) -> dict[str, Any]:
    """合并页专用：子进程 **训练** 日志（与 `/api/training/jobs/.../logs` 同源，路径独立便于前端与训练页区分）。"""
    tid = (training_job_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="缺少 training_job_id")
    m = get_training_manager(root)
    if not m.get_job(tid):
        raise HTTPException(status_code=404, detail="训练任务不存在或尚未加载")
    text, truncated = m.read_log(tid)
    return {"text": text, "truncated": truncated}


@router.get("/merge/training-jobs/{training_job_id}/logs")
async def get_merge_logs_for_training_job(root: WorkspaceRoot, training_job_id: str) -> dict[str, Any]:
    """该训练 job 最近一次合并任务的日志（内存中最新任务或 SQLite `merge_jobs` + 磁盘 .log 文件）。"""
    tid = (training_job_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="缺少 training_job_id")
    m = get_merge_manager()
    mem = m.latest_job_for_training_id(tid)
    if mem and mem.log_path and mem.log_path.is_file():
        text, truncated = m.read_log(mem.id)
        return {
            "text": text,
            "truncated": truncated,
            "merge_job_id": mem.id,
        }
    conn = get_connection(root.resolve())
    row = merge_job_get_latest_by_training_id(conn, tid)
    if not row:
        return {"text": "", "truncated": False, "merge_job_id": None}
    mid = str(row.get("id") or "").strip() or None
    lp = str(row.get("log_path") or "").strip()
    p = Path(lp) if lp else Path()
    if p.is_file():
        text, truncated = read_merge_log_file(p)
        return {
            "text": text,
            "truncated": truncated,
            "merge_job_id": mid,
        }
    return {"text": "", "truncated": False, "merge_job_id": mid}


@router.get("/merge/training-jobs/{training_job_id}/export-zip")
async def download_merge_export_zip(root: WorkspaceRoot, training_job_id: str) -> FileResponse:
    conn = get_connection(root.resolve())
    rel = merge_export_zip_get(conn, training_job_id)
    if not rel:
        raise HTTPException(status_code=404, detail="暂无导出包，请先成功完成合并")
    path = resolve_under_workspace(root, rel)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在，请重新合并")
    return FileResponse(str(path), filename=path.name, media_type="application/zip")


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
    return {"text": text, "truncated": truncated}


@router.post("/merge/jobs/{job_id}/cancel")
async def cancel_merge_job(job_id: str) -> dict[str, bool]:
    ok = get_merge_manager().cancel(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="无法取消该任务")
    return {"ok": True}


@router.post("/merge/jobs/{job_id}/validate")
async def validate_merge_job(root: WorkspaceRoot, job_id: str) -> dict[str, Any]:
    j = get_merge_manager().get(job_id)
    if not j or j.status != "succeeded":
        raise HTTPException(status_code=400, detail="仅成功完成的合并任务可校验")
    out = (j.request or {}).get("output_path", "")
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
