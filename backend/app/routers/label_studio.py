from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.db import app_kv_get, app_kv_set, get_connection
from backend.app.services import label_studio_api as ls

router = APIRouter(tags=["label-studio"])

# 持久化在 SQLite app_kv 表（与「活跃数据集」等键值同表）
_KV_LABEL_STUDIO_BASE_URL = "label_studio.ui_base_url"
_KV_LABEL_STUDIO_API_TOKEN = "label_studio.ui_api_token"


class TestConnectionBody(BaseModel):
    base_url: str = Field(..., description="Label Studio 根 URL，如 http://127.0.0.1:8080")
    token: str = Field(..., min_length=1, description="LS API Token")


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
