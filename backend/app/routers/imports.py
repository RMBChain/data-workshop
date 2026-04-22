from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.app.config import get_settings
from backend.app.db import get_connection, row_to_dict

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
