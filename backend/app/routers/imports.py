from __future__ import annotations

import json
import logging
import math
import mimetypes
import shutil
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.db import get_connection, row_to_dict
from backend.app.services.paths import resolve_under_workspace

log = logging.getLogger(__name__)

router = APIRouter(tags=["imports"])


def _import_task_rows_to_items(rows: Sequence[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for r in rows:
        d = row_to_dict(r)
        d["raw_json"] = None
        rj = r["raw_json"]
        d["has_raw"] = bool(rj)
        ann_count = 0
        if rj:
            try:
                obj = json.loads(rj)
                anns = obj.get("annotations")
                if isinstance(anns, list):
                    ann_count = len(anns)
            except (json.JSONDecodeError, TypeError):
                pass
        d["annotation_count"] = ann_count
        ir = d.get("image_rel")
        d["has_task_image"] = bool(ir and str(ir).strip())
        items.append(d)
    return items


@router.get("/imports")
async def list_imports(
    project_title: str | None = Query(None, description="按项目名称模糊匹配"),
    batch_name: str | None = Query(None, description="按批次名称模糊匹配"),
) -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    conditions: list[str] = []
    params: list[str] = []
    if project_title and project_title.strip():
        conditions.append("COALESCE(project_title, '') LIKE ?")
        params.append(f"%{project_title.strip()}%")
    if batch_name and batch_name.strip():
        conditions.append("COALESCE(batch_name, '') LIKE ?")
        params.append(f"%{batch_name.strip()}%")
    where = " AND ".join(conditions) if conditions else "1=1"
    sql = (
        "SELECT id, project_id, project_title, label_studio_base, task_count, workspace_dir, created_at, batch_name "
        f"FROM import_batches WHERE {where} ORDER BY created_at DESC"
    )
    rows = conn.execute(sql, params).fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


class ImportBatchUpdateBody(BaseModel):
    batch_name: str = Field("", description="批次显示名称，可清空")


@router.patch("/imports/{import_batch_id}")
async def update_import_batch(import_batch_id: str, body: ImportBatchUpdateBody) -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    one = conn.execute("SELECT 1 FROM import_batches WHERE id = ?", (import_batch_id,)).fetchone()
    if not one:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    stored = body.batch_name.strip() or None
    conn.execute("UPDATE import_batches SET batch_name = ? WHERE id = ?", (stored, import_batch_id))
    conn.commit()
    return {"ok": True, "id": import_batch_id, "batch_name": stored}


@router.get("/import-tasks")
async def search_import_tasks(
    project_title: str | None = Query(None, description="按导入批次的项目名称模糊匹配"),
    batch_name: str | None = Query(None, description="按导入批次的批次名称模糊匹配"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    conditions: list[str] = []
    params: list[str] = []
    if project_title and project_title.strip():
        conditions.append("COALESCE(b.project_title, '') LIKE ?")
        params.append(f"%{project_title.strip()}%")
    if batch_name and batch_name.strip():
        conditions.append("COALESCE(b.batch_name, '') LIKE ?")
        params.append(f"%{batch_name.strip()}%")
    where_batch = " AND ".join(conditions) if conditions else "1=1"
    from_clause = "import_tasks t INNER JOIN import_batches b ON b.id = t.batch_id"
    count_row = conn.execute(
        f"SELECT COUNT(*) FROM {from_clause} WHERE {where_batch}",
        params,
    ).fetchone()
    n = int(count_row[0]) if count_row else 0
    offset = (page - 1) * page_size
    list_params = [*params, page_size, offset]
    rows = conn.execute(
        f"""
        SELECT t.id, t.batch_id, t.ls_task_id, t.image_rel, t.resolved, t.thumb_note, t.raw_json,
               b.project_title, b.batch_name
        FROM {from_clause}
        WHERE {where_batch}
        ORDER BY b.created_at DESC, t.ls_task_id
        LIMIT ? OFFSET ?
        """,
        list_params,
    ).fetchall()
    items = _import_task_rows_to_items(rows)
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": n,
        "pages": max(1, math.ceil(n / page_size) if page_size else 1),
    }


@router.get("/imports/{import_batch_id}/tasks")
async def list_import_tasks(
    import_batch_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    one = conn.execute("SELECT 1 FROM import_batches WHERE id = ?", (import_batch_id,)).fetchone()
    if not one:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    total = conn.execute("SELECT COUNT(*) FROM import_tasks WHERE batch_id = ?", (import_batch_id,)).fetchone()
    n = int(total[0]) if total else 0
    offset = (page - 1) * page_size
    rows = conn.execute(
        "SELECT id, batch_id, ls_task_id, image_rel, resolved, thumb_note, raw_json "
        "FROM import_tasks WHERE batch_id = ? ORDER BY ls_task_id LIMIT ? OFFSET ?",
        (import_batch_id, page_size, offset),
    ).fetchall()
    items = _import_task_rows_to_items(rows)
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": n,
        "pages": max(1, math.ceil(n / page_size) if page_size else 1),
    }


@router.get("/imports/{import_batch_id}/tasks/{import_task_id}/raw")
async def get_import_task_raw(import_batch_id: str, import_task_id: str) -> dict[str, Any]:
    """返回该导入任务行保存的 Label Studio 任务 JSON（含 data、annotations 等）。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute(
        "SELECT batch_id, raw_json FROM import_tasks WHERE id = ? AND batch_id = ?",
        (import_task_id, import_batch_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    raw = row["raw_json"]
    if not raw:
        return {"raw": None}
    try:
        return {"raw": json.loads(raw)}
    except json.JSONDecodeError:
        return {"raw": None, "raw_text": raw}


@router.get("/imports/{import_batch_id}/tasks/{import_task_id}/image", response_model=None)
async def get_import_task_image(import_batch_id: str, import_task_id: str) -> FileResponse | RedirectResponse:
    """按导入任务行返回图片（本地文件或外链 302）。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute(
        "SELECT image_rel FROM import_tasks WHERE id = ? AND batch_id = ?",
        (import_task_id, import_batch_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    ir = (row["image_rel"] or "").strip()
    if not ir:
        raise HTTPException(status_code=404, detail="该任务无图片")
    if ir.startswith("http://") or ir.startswith("https://"):
        return RedirectResponse(url=ir, status_code=302)
    try:
        path = resolve_under_workspace(root, ir)
    except ValueError:
        raise HTTPException(status_code=404, detail="无效图片路径")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件不存在")
    media_type, _ = mimetypes.guess_type(str(path))
    return FileResponse(path, media_type=media_type or "application/octet-stream")


@router.delete("/imports/{import_batch_id}")
async def delete_import_batch(import_batch_id: str) -> dict[str, Any]:
    """删除源导入批次：清除数据库记录、工作区中该批次的导入目录；已生成的数据集版本保留，仅解除 batch 关联。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute(
        "SELECT id, workspace_dir FROM import_batches WHERE id = ?",
        (import_batch_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    rel = row["workspace_dir"] or ""
    conn.execute("UPDATE dataset_versions SET import_batch_id = NULL WHERE import_batch_id = ?", (import_batch_id,))
    conn.execute("DELETE FROM dataset_build_jobs WHERE import_batch_id = ?", (import_batch_id,))
    conn.execute("DELETE FROM import_batches WHERE id = ?", (import_batch_id,))
    conn.commit()

    if rel and str(rel).strip():
        try:
            imp = resolve_under_workspace(root, str(rel).strip())
            if imp.exists():
                shutil.rmtree(imp)
        except (ValueError, OSError) as e:
            log.warning("已删除数据库记录，但清理工作区目录失败: %s", e, exc_info=True)
            return {"ok": True, "id": import_batch_id, "warning": f"工作区文件未完全清理: {e}"}
    return {"ok": True, "id": import_batch_id}
