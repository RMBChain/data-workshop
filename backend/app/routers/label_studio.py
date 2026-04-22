from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.db import get_connection, json_dumps
from backend.app.services import label_studio_api as ls
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["label-studio"])


class TestConnectionBody(BaseModel):
    base_url: str = Field(..., description="Label Studio 根 URL，如 http://127.0.0.1:8080")
    token: str = Field(..., min_length=1, description="LS API Token")


class LabelStudioImportBody(BaseModel):
    project_id: int
    base_url: str | None = None
    token: str
    task_filter: str | None = Field(None, description="预留；MVP 不筛选")


@router.get("/label-studio/status")
async def label_studio_status() -> dict[str, Any]:
    """探测配置文件中默认 LS 基址是否可达（不校验 Token）。"""
    settings = get_settings()
    base = settings.label_studio_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base}/", follow_redirects=True)
            return {
                "reachable": r.status_code < 500,
                "url": settings.label_studio_url,
                "http_status": r.status_code,
            }
    except Exception as e:
        return {
            "reachable": False,
            "url": settings.label_studio_url,
            "error": str(e),
        }


@router.post("/label-studio/test-connection")
async def label_studio_test_connection(body: TestConnectionBody) -> dict[str, Any]:
    try:
        r = await ls.test_connection(body.base_url, body.token)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"连接失败：{e}") from e
    if not r.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=f"无法访问或 Token 无效（HTTP {r.get('http_status')}）",
        )
    return {"ok": True, **r, "message": "连接成功，Token 有效"}


@router.get("/label-studio/projects")
async def label_studio_projects(
    base_url: str | None = None,
    token: str = "",
) -> dict[str, Any]:
    """
    列出 LS 项目。Query：`base_url` 可选，默认用配置；`token` 必传或 header（此处用 query 方便前端）。
    """
    if not (token or "").strip():
        raise HTTPException(status_code=400, detail="缺少 token 参数")
    settings = get_settings()
    b = (base_url or settings.label_studio_url).rstrip("/")
    try:
        items = await ls.list_projects(b, token)
        return {"items": items, "base_url": b}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"获取项目失败：{e}") from e


@router.post("/label-studio/import")
async def label_studio_import(body: LabelStudioImportBody) -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    base = (body.base_url or settings.label_studio_url).rstrip("/")

    projects = await ls.list_projects(base, body.token)
    proj = next((p for p in projects if int(p.get("id") or 0) == int(body.project_id)), None)
    if not proj:
        raise HTTPException(status_code=404, detail="未找到该 project_id 对应项目")

    tasks = await ls.iter_project_tasks(base, body.token, int(body.project_id))
    batch_id = uuid.uuid4().hex
    rel_dir = f"imports/{batch_id}"
    imp_dir = resolve_under_workspace(root, rel_dir)
    imp_dir.mkdir(parents=True, exist_ok=True)

    conn = get_connection(root)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """
        INSERT INTO import_batches (id, project_id, project_title, label_studio_base, task_count, workspace_dir, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (batch_id, int(body.project_id), str(proj.get("title") or ""), base, len(tasks), rel_dir, now),
    )

    stored = 0
    for t in tasks:
        tid = t.get("id")
        data = t.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        img, note = ls.pick_image_from_task_data(data)
        resolved = 0
        image_rel: str | None = None
        if img:
            # 本工作区内相对路径，或已存在于 workspace 下
            if not img.startswith("http://") and not img.startswith("https://"):
                try:
                    candidate = Path(img)
                    if not candidate.is_absolute():
                        p = (root / img.replace("\\", "/").lstrip("/")).resolve()
                        p.relative_to(root)
                        image_rel = str(p.relative_to(root)).replace("\\", "/")
                        resolved = 1 if p.is_file() else 0
                    else:
                        image_rel = img
                except Exception:
                    image_rel = img
            else:
                image_rel = img
        row_id = f"{batch_id}-{tid}"
        conn.execute(
            """
            INSERT INTO import_tasks (id, batch_id, ls_task_id, image_rel, resolved, thumb_note, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (row_id, batch_id, int(tid) if tid is not None else 0, image_rel, resolved, note, json_dumps(t)),
        )
        stored += 1

    manifest = imp_dir / "tasks_manifest.jsonl"
    with open(manifest, "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json_dumps(t) + "\n")
    conn.commit()
    return {
        "import_batch_id": batch_id,
        "task_count": stored,
        "project_title": proj.get("title"),
        "workspace_dir": rel_dir.replace("\\", "/"),
    }
