from __future__ import annotations

import httpx
from fastapi import APIRouter

from backend.app.config import get_settings

router = APIRouter(tags=["label-studio"])


@router.get("/label-studio/status")
async def label_studio_status() -> dict:
    """探测 compose 中的 Label Studio 是否可达（不校验 Token）。"""
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
