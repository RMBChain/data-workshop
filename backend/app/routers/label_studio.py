from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.db import app_kv_get, app_kv_set, get_connection, json_dumps
from backend.app.services import label_studio_api as ls
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["label-studio"])
log = logging.getLogger(__name__)

# 持久化在 SQLite app_kv 表（与「活跃数据集」等键值同表）
_KV_LABEL_STUDIO_BASE_URL = "label_studio.ui_base_url"
_KV_LABEL_STUDIO_API_TOKEN = "label_studio.ui_api_token"


async def _resolve_import_image(
    client: httpx.AsyncClient,
    root: Path,
    base: str,
    token: str,
    imp_dir: Path,
    ls_task_id: int | None,
    img: str,
    note: str | None,
) -> tuple[str | None, int, str | None]:
    """
    得到写入 import_tasks 的 image_rel、resolved、thumb_note。
    优先工作区内已有文件，否则从 LS/外链下载到 imports/<batch>/files/。
    """
    s = (img or "").strip()
    if not s:
        return None, 0, note
    b = base.rstrip("/")
    if not s.startswith("http://") and not s.startswith("https://"):
        try:
            p = (root / s.replace("\\", "/").lstrip("/")).resolve()
            p.relative_to(root)
            if p.is_file():
                return str(p.relative_to(root)).replace("\\", "/"), 1, note
        except Exception:
            pass

    fetch_url = ls.source_to_fetch_url(b, s)
    raw_name = Path(urlparse(fetch_url).path).name
    base_fn = ls.safe_import_filename(raw_name, f"task{ls_task_id or 0}")
    if "." not in base_fn:
        base_fn = f"{base_fn}.jpg"
    dest = imp_dir / "files" / f"{int(ls_task_id) if ls_task_id is not None else 0}_{base_fn}"
    ok = await ls.fetch_image_to_path(client, b, token, s, dest)
    if ok:
        return str(dest.resolve().relative_to(root.resolve())).replace("\\", "/"), 1, note
    if s.startswith("http://") or s.startswith("https://"):
        return s, 0, ((note or "") + "（未下载到工作区，保留 URL）").strip() or "（未下载到工作区，保留 URL）"
    return s, 0, ((note or "") + "（图片下载失败，请检查基址与网络）").strip() or "（图片下载失败）"


class TestConnectionBody(BaseModel):
    base_url: str = Field(..., description="Label Studio 根 URL，如 http://127.0.0.1:8080")
    token: str = Field(..., min_length=1, description="LS API Token")


class LabelStudioImportBody(BaseModel):
    project_id: int
    base_url: str | None = None
    token: str
    task_filter: str | None = Field(None, description="预留；MVP 不筛选")


class LabelStudioConnectionBody(BaseModel):
    base_url: str = Field("", description="Label Studio 根 URL")
    token: str = Field("", description="LS API Token，可空以清空已存 Token")


@router.get("/label-studio/connection")
async def get_label_studio_connection() -> dict[str, Any]:
    """从数据库 app_kv 读取界面保存的基址与 Token；基址未设置时回退为配置默认。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    raw_base = (app_kv_get(conn, _KV_LABEL_STUDIO_BASE_URL) or "").strip()
    if not raw_base:
        raw_base = (settings.label_studio_url or "").strip()
    base_url = raw_base.rstrip("/")
    token = app_kv_get(conn, _KV_LABEL_STUDIO_API_TOKEN) or ""
    return {"base_url": base_url, "token": token}


@router.put("/label-studio/connection")
async def put_label_studio_connection(body: LabelStudioConnectionBody) -> dict[str, Any]:
    """将连接设置写入数据库 app_kv。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    base_url = (body.base_url or "").strip().rstrip("/")
    token = body.token or ""
    app_kv_set(conn, _KV_LABEL_STUDIO_BASE_URL, base_url)
    app_kv_set(conn, _KV_LABEL_STUDIO_API_TOKEN, token)
    conn.commit()
    return {"base_url": base_url, "token": token}


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
        extra = r.get("error_body")
        msg = f"无法访问或 Token 无效（HTTP {r.get('http_status')}）"
        if isinstance(extra, str) and extra.strip():
            msg = f"{msg} {extra.strip()}"
        raise HTTPException(status_code=400, detail=msg)
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
    batch_name = datetime.now().strftime("%Y%m%d-%H%M%S")
    conn.execute(
        """
        INSERT INTO import_batches (id, project_id, project_title, label_studio_base, task_count, workspace_dir, created_at, batch_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (batch_id, int(body.project_id), str(proj.get("title") or ""), base, len(tasks), rel_dir, now, batch_name),
    )

    (imp_dir / "files").mkdir(parents=True, exist_ok=True)

    stored = 0
    resolved_count = 0
    log.info(
        "Label Studio 导入开始: batch_id=%s project_id=%s title=%s task_count=%d base=%s",
        batch_id,
        int(body.project_id),
        str(proj.get("title") or ""),
        len(tasks),
        base,
    )
    async with httpx.AsyncClient(timeout=120.0) as dl_client:
        n_tasks = len(tasks)
        for t in tasks:
            tid = t.get("id")
            data = t.get("data") or {}
            if not isinstance(data, dict):
                data = {}
            img, thumb_note = ls.pick_image_from_task_data(data)
            resolved = 0
            image_rel: str | None = None
            if img:
                image_rel, resolved, thumb_note = await _resolve_import_image(
                    dl_client,
                    root,
                    base,
                    body.token,
                    imp_dir,
                    int(tid) if tid is not None else None,
                    img,
                    thumb_note,
                )
                if resolved:
                    resolved_count += 1
            row_id = f"{batch_id}-{tid}"
            conn.execute(
                """
                INSERT INTO import_tasks (id, batch_id, ls_task_id, image_rel, resolved, thumb_note, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (row_id, batch_id, int(tid) if tid is not None else 0, image_rel, resolved, thumb_note, json_dumps(t)),
            )
            stored += 1
            if stored == 1 or stored % 10 == 0 or stored == n_tasks:
                log.info(
                    "Label Studio 导入进度: %d/%d 已写入 (resolved 本地/落盘=%d) batch_id=%s",
                    stored,
                    n_tasks,
                    resolved_count,
                    batch_id,
                )

    log.info(
        "Label Studio 导入完成: batch_id=%s 任务行=%d 条图片标记为已解析(resolved)=%d 目录=%s",
        batch_id,
        stored,
        resolved_count,
        rel_dir,
    )

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
