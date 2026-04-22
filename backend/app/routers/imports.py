from __future__ import annotations

import logging
import math
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.app.config import get_settings
from backend.app.db import get_connection, row_to_dict
from backend.app.services.paths import resolve_under_workspace

log = logging.getLogger(__name__)

router = APIRouter(tags=["imports"])


@router.get("/imports")
async def list_imports() -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    rows = conn.execute(
        "SELECT id, project_id, project_title, label_studio_base, task_count, workspace_dir, created_at "
        "FROM import_batches ORDER BY created_at DESC"
    ).fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


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
    items: list[dict[str, Any]] = []
    for r in rows:
        d = row_to_dict(r)
        d["raw_json"] = None  # 列表不返回全文以减小体积
        d["has_raw"] = bool(r["raw_json"])
        items.append(d)
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": n,
        "pages": max(1, math.ceil(n / page_size) if page_size else 1),
    }


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
