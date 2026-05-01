from __future__ import annotations

import logging
import time
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
_merge_route_log = logging.getLogger("workshop.merge")


@router.post("/merge/jobs")
async def create_merge_job(root: WorkspaceRoot, body: MergeJobCreate) -> dict[str, Any]:
    try:
        resolve_under_workspace(root, "merged")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    t0 = time.perf_counter()
    job = get_merge_manager().create_job(body)
    ms = (time.perf_counter() - t0) * 1000
    tid = (body.training_job_id or "").strip() or None
    _merge_route_log.info(
        "POST /merge/jobs: merge_job_id=%s training_job_id=%s status=%s create_ms=%.1f workspace=%s",
        job.id,
        tid,
        job.status,
        ms,
        root.resolve(),
    )
    return {
        "id": job.id,
        "status": job.status,
        "log_path": str(job.log_path) if job.log_path else None,
        "error_message": job.error_message,
    }


@router.get("/merge/training-candidates")
async def list_merge_training_candidates(root: WorkspaceRoot) -> dict[str, Any]:
    """合并页列表：训练成功/失败等任务（与训练页列表数据来源一致但范围更广，用于合并与只读回看的分流）。"""
    t0 = time.perf_counter()
    items = list_merge_page_training_rows(root)
    ms = (time.perf_counter() - t0) * 1000
    _merge_route_log.info(
        "GET /merge/training-candidates: count=%d ms=%.1f workspace=%s",
        len(items),
        ms,
        root.resolve(),
    )
    return {"items": items}


@router.get("/merge/training-status")
async def get_merge_training_status(root: WorkspaceRoot) -> dict[str, Any]:
    """各训练 job_id 对应的 LoRA 合并态：未合并 / 合并中 / 已取消 / 失败 / 成功。
    内存中最新 MergeJob 优先；无内存记录时以 SQLite `dws_merges`（训练行 merge_id）持久化结果为准（不扫磁盘）。
    output_path_by_job_id：成功时来自合并任务 request.output_path、打包表 merged_model_relpath 等。
    zip_path_by_job_id：合并成功且已打包入库时非 null（工作区相对路径）。"""
    t_all = time.perf_counter()
    conn = get_connection(root.resolve())
    t_rows = time.perf_counter()
    rows = list_merge_page_training_rows(root)
    ms_rows = (time.perf_counter() - t_rows) * 1000
    t_status = time.perf_counter()
    m = get_merge_manager()
    by_jid, output_by_jid = training_merge_status_by_job_id(root, rows, m, conn)
    ms_status = (time.perf_counter() - t_status) * 1000
    t_db = time.perf_counter()
    jids = [str(r.get("job_id") or "").strip() for r in rows if str(r.get("job_id") or "").strip()]
    zip_by = merge_export_zip_map(conn, jids)
    merged_path_by = merge_export_merged_path_map(conn, jids)
    ms_db = (time.perf_counter() - t_db) * 1000
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
    ms_total = (time.perf_counter() - t_all) * 1000
    merging_n = sum(1 for s in by_jid.values() if s == "merging")
    _merge_route_log.info(
        "GET /merge/training-status: rows=%d list_rows_ms=%.1f status_compute_ms=%.1f "
        "sqlite_ms=%.1f total_ms=%.1f merging=%d workspace=%s",
        len(rows),
        ms_rows,
        ms_status,
        ms_db,
        ms_total,
        merging_n,
        root.resolve(),
    )
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
    """该训练 job 最近一次合并任务的日志（内存中最新任务或 SQLite `dws_merges.log_path` + 磁盘 .log 文件）。"""
    t0 = time.perf_counter()
    tid = (training_job_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="缺少 training_job_id")
    m = get_merge_manager()
    mem = m.latest_job_for_training_id(tid)
    if mem and mem.log_path and mem.log_path.is_file():
        text, truncated = m.read_log(mem.id)
        _merge_route_log.debug(
            "GET /merge/training-jobs/.../logs: tid=%s source=memory merge_job_id=%s ms=%.1f chars=%d",
            tid,
            mem.id,
            (time.perf_counter() - t0) * 1000,
            len(text),
        )
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
        _merge_route_log.debug(
            "GET /merge/training-jobs/.../logs: tid=%s source=sqlite_file merge_job_id=%s ms=%.1f chars=%d",
            tid,
            mid,
            (time.perf_counter() - t0) * 1000,
            len(text),
        )
        return {
            "text": text,
            "truncated": truncated,
            "merge_job_id": mid,
        }
    _merge_route_log.debug(
        "GET /merge/training-jobs/.../logs: tid=%s source=empty ms=%.1f",
        tid,
        (time.perf_counter() - t0) * 1000,
    )
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
