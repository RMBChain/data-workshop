from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


def source_to_fetch_url(base: str, source_ref: str) -> str:
    """将任务里出现的图片引用转为可 HTTP GET 的 URL。"""
    s = (source_ref or "").strip()
    if not s:
        return s
    if s.startswith("http://") or s.startswith("https://"):
        return s
    b = base.rstrip("/")
    if s.startswith("/"):
        return f"{b}{s}"
    return f"{b}/{s.lstrip('/')}"


def _origin_key(url: str) -> tuple[str, str, int | None]:
    p = urlparse(url)
    port = p.port
    if port is None and p.scheme == "http":
        port = 80
    elif port is None and p.scheme == "https":
        port = 443
    host = (p.hostname or "").lower()
    return (p.scheme.lower(), host, port)


def is_same_label_studio_origin(base: str, fetch_url: str) -> bool:
    """是否同一 Label Studio 站点（可带 API Token 拉取 /data/upload 等）。"""
    return _origin_key(base) == _origin_key(fetch_url)


async def fetch_image_to_path(
    client: httpx.AsyncClient,
    base: str,
    token: str,
    source_ref: str,
    dest: Path,
) -> bool:
    """
    从 LS 或外链拉取图片到本地文件。
    与当前 `base` 同源的请求带鉴权，外链仅普通 GET（无 Token）。
    """
    url = source_to_fetch_url(base, source_ref)
    if not url:
        return False
    try:
        if is_same_label_studio_origin(base, url):
            headers = await get_auth_headers(client, base, token)
            r = await client.get(url, headers=headers, follow_redirects=True, timeout=120.0)
        else:
            r = await client.get(url, follow_redirects=True, timeout=120.0)
        if r.status_code != 200 or not r.content:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return True
    except Exception:
        return False


def safe_import_filename(suggested: str, fallback_stem: str) -> str:
    base = Path(suggested or "").name or fallback_stem
    cleaned = re.sub(r"[^\w.\-]+", "_", base).strip("._") or "image"
    return cleaned[:160]


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
            elif isinstance(data, dict):
                # DRF: { count, next, results }；新 API：{ total, tasks, ... }
                if "results" in data and isinstance(data["results"], list):
                    batch = data["results"]
                elif "tasks" in data and isinstance(data["tasks"], list):
                    batch = data["tasks"]
                else:
                    batch = []
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
