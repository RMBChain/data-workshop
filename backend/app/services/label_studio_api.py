from __future__ import annotations

import time
from typing import Any

import httpx

# (headers, monotonic_expiry or None=legacy Token 长期有效)
_AUTH_CACHE: dict[str, tuple[dict[str, str], float | None]] = {}
_BEARER_TTL_SEC = 240.0


def _cache_key(base: str, token: str) -> str:
    return f"{base.rstrip('/')}\n{(token or '').strip()}"


def _invalidate_auth(base: str, token: str) -> None:
    _AUTH_CACHE.pop(_cache_key(base, token), None)


def _snippet(text: str, max_len: int = 280) -> str:
    s = (text or "").strip().replace("\n", " ")
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


async def get_auth_headers(client: httpx.AsyncClient, base: str, token: str) -> dict[str, str]:
    """
    Label Studio 两种密钥：
    - Legacy：HTTP 头 `Authorization: Token <token>`，见官方文档 Legacy tokens。
    - PAT：需先 POST /api/token/refresh，再用 `Authorization: Bearer <access>`。
    """
    b = base.rstrip("/")
    t = (token or "").strip()
    if not t:
        raise ValueError("缺少 Token")

    key = _cache_key(b, t)
    now = time.monotonic()
    hit = _AUTH_CACHE.get(key)
    if hit is not None:
        headers, exp = hit
        if exp is None or exp > now:
            return headers
        _AUTH_CACHE.pop(key, None)

    base_h: dict[str, str] = {"Content-Type": "application/json"}
    r = await client.get(
        f"{b}/api/projects",
        headers={**base_h, "Authorization": f"Token {t}"},
        params={"page_size": 1},
    )
    if r.status_code == 200:
        h = {**base_h, "Authorization": f"Token {t}"}
        _AUTH_CACHE[key] = (h, None)
        return h

    if r.status_code not in (401, 403):
        raise ValueError(
            f"Label Studio 返回 HTTP {r.status_code}（请检查基址、端口与 LS 进程）。"
            f" 响应片段：{_snippet(r.text)}"
        )

    r2 = await client.post(f"{b}/api/token/refresh", headers=base_h, json={"refresh": t})
    try:
        data2 = r2.json()
    except Exception:
        data2 = {}
    access = data2.get("access") if isinstance(data2, dict) else None
    if r2.status_code == 200 and isinstance(access, str) and access:
        h = {**base_h, "Authorization": f"Bearer {access}"}
        _AUTH_CACHE[key] = (h, now + _BEARER_TTL_SEC)
        return h

    refresh_snip = _snippet(r2.text)
    raise ValueError(
        "无法认证：Legacy 方式 GET /api/projects 为 HTTP "
        f"{r.status_code}，且 PAT 刷新 POST /api/token/refresh 为 HTTP {r2.status_code}。"
        f" 若使用 Personal Access Token，请确认 LS 已启用 PAT 且组织允许；若使用 Legacy Token，"
        f"请在账户设置使用「Legacy」类密钥。刷新接口响应：{refresh_snip}"
    )


async def _request_json_with_auth_retry(
    client: httpx.AsyncClient,
    base: str,
    token: str,
    method: str,
    url: str,
    *,
    expect_status: tuple[int, ...] = (200,),
    **kwargs: Any,
) -> httpx.Response:
    last: httpx.Response | None = None
    for attempt in range(2):
        headers = await get_auth_headers(client, base, token)
        kw = {**kwargs, "headers": headers}
        r = await client.request(method, url, **kw)
        last = r
        if r.status_code == 401 and attempt == 0:
            _invalidate_auth(base, token)
            continue
        if r.status_code in expect_status:
            return r
        return r
    assert last is not None
    return last


async def test_connection(base_url: str, token: str) -> dict[str, Any]:
    """验证 Token（Legacy 或 PAT）并拉取项目列表第一页摘要。"""
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=15.0) as client:
        headers = await get_auth_headers(client, base, token)
        r = await client.get(f"{base}/api/projects", headers=headers, params={"page_size": 1})
        try:
            data = r.json()
        except Exception:
            data = {}
    ok = r.status_code == 200
    out: dict[str, Any] = {"ok": ok, "http_status": r.status_code}
    if not ok:
        out["error_body"] = _snippet(r.text)
    if isinstance(data, dict) and "results" in data:
        out["project_total_hint"] = data.get("count", len(data.get("results", [])))
    return out


async def list_projects(base_url: str, token: str) -> list[dict[str, Any]]:
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await _request_json_with_auth_retry(
            client,
            base,
            token,
            "GET",
            f"{base}/api/projects",
            params={"page_size": 1000},
        )
        r.raise_for_status()
        data = r.json()
    if isinstance(data, list):
        return [_normalize_project(p) for p in data]
    if isinstance(data, dict) and "results" in data:
        return [_normalize_project(p) for p in data["results"]]
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
            r = await _request_json_with_auth_retry(
                client,
                base,
                token,
                "GET",
                f"{base}/api/tasks",
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
