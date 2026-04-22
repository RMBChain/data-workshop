from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.db import app_kv_get, get_connection, row_to_dict
from backend.app.services.dataset_build import get_dataset_manager
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["datasets"])
log = logging.getLogger(__name__)


class DatasetBuildBody(BaseModel):
    import_batch_id: str
    add_image_token: bool = True
    train_ratio: int = Field(80, ge=0, le=100)
    val_ratio: int = Field(10, ge=0, le=100)
    test_ratio: int = Field(10, ge=0, le=100)
    random_seed: int | None = None
    note: str | None = None


@router.post("/datasets/build")
async def dataset_build(body: DatasetBuildBody) -> dict[str, Any]:
    if body.train_ratio + body.val_ratio + body.test_ratio != 100:
        raise HTTPException(status_code=400, detail="训练/验证/测试比例之和须为 100")
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    b = conn.execute("SELECT 1 FROM import_batches WHERE id = ?", (body.import_batch_id,)).fetchone()
    if not b:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    try:
        mgr = get_dataset_manager(root)
        job = mgr.start_build(
            import_batch_id=body.import_batch_id,
            add_image_token=body.add_image_token,
            train_ratio=body.train_ratio,
            val_ratio=body.val_ratio,
            test_ratio=body.test_ratio,
            seed=body.random_seed,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    log.info(
        "已提交数据集构建: job_id=%s import_batch_id=%s 比例 t/v/te=%d/%d/%d",
        job.id,
        body.import_batch_id,
        body.train_ratio,
        body.val_ratio,
        body.test_ratio,
    )
    return {"job_id": job.id, "status": job.status}


@router.get("/datasets/jobs/{job_id}")
async def get_dataset_job(job_id: str) -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    j = get_dataset_manager(root).get(job_id)
    if j:
        return {
            "id": j.id,
            "status": j.status,
            "import_batch_id": j.import_batch_id,
            "progress": j.progress,
            "error_message": j.error_message,
            "result_version_id": j.result_version_id,
        }
    row = get_connection(root).execute(
        "SELECT id, status, import_batch_id, created_at, finished_at, error_message, progress, result_version_id "
        "FROM dataset_build_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    d = row_to_dict(row)
    return {
        "id": d["id"],
        "status": d["status"],
        "import_batch_id": d["import_batch_id"],
        "progress": d["progress"] or 0,
        "error_message": d["error_message"],
        "result_version_id": d["result_version_id"],
    }


@router.post("/datasets/jobs/{job_id}/cancel")
async def cancel_dataset_job(job_id: str) -> dict[str, bool]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    ok = get_dataset_manager(root).cancel(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="无法取消该任务")
    return {"ok": True}


@router.get("/datasets/versions")
async def list_dataset_versions() -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    active = app_kv_get(get_connection(root), "active_dataset_version")
    rows = get_connection(root).execute(
        "SELECT id, import_batch_id, note, rel_dir, train_relpath, val_relpath, test_relpath, created_at "
        "FROM dataset_versions ORDER BY created_at DESC"
    ).fetchall()
    items = [row_to_dict(r) for r in rows]
    for it in items:
        it["is_active"] = it["id"] == active
    return {"active_version_id": active, "items": items}


@router.post("/datasets/versions/{version_id}/rollback")
async def rollback_version(version_id: str) -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute("SELECT id FROM dataset_versions WHERE id = ?", (version_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="版本不存在")
    from backend.app.db import app_kv_set

    app_kv_set(conn, "active_dataset_version", version_id)
    conn.commit()
    return {"ok": True, "active_version_id": version_id}


@router.get("/datasets/versions/{version_id}/export")
async def export_version(version_id: str) -> dict[str, Any]:
    """返回版本目录的清单路径；完整 zip 可后续扩展。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    row = get_connection(root).execute(
        "SELECT rel_dir FROM dataset_versions WHERE id = ?", (version_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="版本不存在")
    rel = row[0]
    p = resolve_under_workspace(root, rel)
    if not p.is_dir():
        raise HTTPException(status_code=404, detail="版本目录不存在")
    zip_name = f"{version_id}_export.zip"
    dest = root / "exports" / zip_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        dest.unlink()
    ar = str(shutil.make_archive(str(dest.with_suffix("")), "zip", root_dir=str(p)))
    return {
        "version_id": version_id,
        "zip_path": str(Path(ar).relative_to(root)).replace("\\", "/"),
        "message": "已生成 zip，路径相对于工作区根目录。",
    }


@router.get("/datasets/preview")
async def dataset_preview(version_id: str | None = Query(None)) -> dict[str, Any]:
    """返回一条与需求 §1.2 形态一致的 JSON 样例。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    vid = version_id
    if not vid:
        vid = app_kv_get(conn, "active_dataset_version")
    if not vid:
        raise HTTPException(status_code=400, detail="请指定 version_id 或先构建数据集")
    r = conn.execute(
        "SELECT train_relpath FROM dataset_versions WHERE id = ?",
        (vid,),
    ).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="版本不存在")
    tr = resolve_under_workspace(root, r[0])
    if not tr.is_file():
        raise HTTPException(status_code=404, detail="训练集文件不存在")
    first = tr.read_text(encoding="utf-8").splitlines()[:1]
    sample: Any = None
    if first:
        try:
            sample = json.loads(first[0])
        except Exception:
            sample = {"raw": first[0][:2000]}
    return {"version_id": vid, "sample": sample}
