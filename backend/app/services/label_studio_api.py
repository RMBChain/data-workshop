from __future__ import annotations

from typing import Any

import httpx


def _headers(token: str) -> dict[str, str]:
    t = (token or "").strip()
    h: dict[str, str] = {"Content-Type": "application/json"}
    if t:
        h["Authorization"] = f"Token {t}"
    return h


async def test_connection(base_url: str, token: str) -> dict[str, Any]:
    """验证 Token 并返回用户摘要（若 API 支持）。"""
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{base}/api/projects", headers=_headers(token), params={"page_size": 1})
        try:
            data = r.json()
        except Exception:
            data = {}
    ok = r.status_code == 200
    out: dict[str, Any] = {"ok": ok, "http_status": r.status_code}
    if isinstance(data, dict) and "results" in data:
        out["project_total_hint"] = data.get("count", len(data.get("results", [])))
    return out


async def list_projects(base_url: str, token: str) -> list[dict[str, Any]]:
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(
            f"{base}/api/projects",
            headers=_headers(token),
            params={"page_size": 1000},
        )
        r.raise_for_status()
        data = r.json()
    if isinstance(data, list):
        return [ _normalize_project(p) for p in data ]
    if isinstance(data, dict) and "results" in data:
        return [ _normalize_project(p) for p in data["results"] ]
    return []


def _normalize_project(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p.get("id"),
        "title": p.get("title") or p.get("name") or "",
        "description": p.get("description", ""),
        "task_number": p.get("task_number") or p.get("num_tasks") or 0,
    }


async def iter_project_tasks(
    base_url: str,
    token: str,
    project_id: int,
    *,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    base = base_url.rstrip("/")
    out: list[dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient(timeout=120.0) as client:
        while True:
            r = await client.get(
                f"{base}/api/tasks",
                headers=_headers(token),
                params={"project": project_id, "page": page, "page_size": page_size},
            )
            r.raise_for_status()
            data = r.json()
            batch: list[dict[str, Any]]
            if isinstance(data, list):
                batch = data
            elif isinstance(data, dict) and "results" in data:
                batch = data["results"]
            else:
                batch = []
            out.extend(batch)
            if not batch or len(batch) < page_size:
                break
            page += 1
    return out


def pick_image_from_task_data(data: dict[str, Any]) -> tuple[str | None, str | None]:
    """
    从 Label Studio 任务 data 中抽取图片引用。
    返回 (路径或 URL, 说明)。
    """
    if not data:
        return None, None
    for key in ("image", "img", "ocr", "photo", "candidates"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip(), f"字段 {key}"
    # 单键 fallback：第一个非空字符串值
    for k, v in data.items():
        if isinstance(v, str) and ("/" in v or v.startswith("http") or "\\" in v):
            return v, f"字段 {k}"
    return None, None
