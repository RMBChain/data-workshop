from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from backend.app.db import get_connection, json_dumps
from backend.app.deps import WorkspaceRoot
from backend.app.services.inference_models import list_registered_training_models
from backend.app.services import inference_service
from backend.app.services.inference_service import infer_image
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["inference"])

_DEFAULT_BASE_MODEL = "Qwen/Qwen3-VL-2B-Instruct"
_MAX_IMAGE_BYTES = 25 * 1024 * 1024
_ALLOWED_IMAGE_SUFFIX = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _apply_model_id(base: str, adapter: str | None, model_id: str | None) -> tuple[str, str | None]:
    if not model_id:
        return base, adapter
    mid = model_id
    if mid.startswith("lora:"):
        return base, mid.split(":", 1)[1]
    if mid.startswith("merged:"):
        return mid.split(":", 1)[1], None
    return base, adapter


async def _save_playground_upload(root: Path, upload: UploadFile) -> Path:
    raw_name = (upload.filename or "").strip()
    suf = Path(raw_name).suffix.lower()
    if suf not in _ALLOWED_IMAGE_SUFFIX:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的图片扩展名（允许：{', '.join(sorted(_ALLOWED_IMAGE_SUFFIX))}）",
        )
    dest_dir = (root / "uploads" / "playground").resolve()
    try:
        dest_dir.relative_to(root.resolve())
    except ValueError as e:
        raise HTTPException(status_code=500, detail="上传目录配置无效") from e
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}{suf}"
    data = await upload.read(_MAX_IMAGE_BYTES + 1)
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail=f"图片过大（上限 {_MAX_IMAGE_BYTES // (1024 * 1024)}MB）")
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    dest.write_bytes(data)
    return dest


def _resolve_image_from_workspace_path(
    root: Path,
    *,
    image_workspace_path: str | None,
) -> Path | None:
    if image_workspace_path:
        p = image_workspace_path.strip().replace("\\", "/")
        return resolve_under_workspace(root, p)
    return None


def _run_infer(
    root: Path,
    img_path: Path | None,
    *,
    base: str,
    adapter: str | None,
    prompt: str,
    max_new_tokens: int,
) -> dict[str, Any]:
    if adapter:
        a = str(adapter).replace("\\", "/")
        ap = resolve_under_workspace(root, a)
        if not ap.is_dir():
            raise HTTPException(status_code=400, detail="LoRA 目录不存在于工作区")

    if img_path is not None and not img_path.is_file():
        raise HTTPException(status_code=400, detail="图片文件不存在于工作区指定路径下")

    try:
        text = infer_image(
            root,
            img_path,
            prompt,
            base_model=base,
            adapter_rel=adapter,
            max_new_tokens=max_new_tokens,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"推理失败：{e}") from e
    if img_path is None:
        return {"text": text, "image_path": None}
    try:
        rel_img = str(img_path.resolve().relative_to(root.resolve()))
    except ValueError:
        rel_img = str(img_path.resolve())
    return {"text": text, "image_path": rel_img.replace("\\", "/")}


@router.get("/inference/models")
async def list_models(root: WorkspaceRoot) -> dict[str, Any]:
    return {"items": list_registered_training_models(root)}


class InferenceLoadBody(BaseModel):
    base_model: str = Field(default=_DEFAULT_BASE_MODEL)
    adapter_path: str | None = None
    model_id: str | None = Field(None, description="与 /inference/chat 相同，可覆盖 base/adapter")


@router.post("/inference/load")
async def inference_load(root: WorkspaceRoot, body: InferenceLoadBody) -> dict[str, str]:
    """预加载与 chat 同键的基座+LoRA 到进程内缓存，减少首次点「发送」的等待时间。"""
    base = body.base_model
    adapter: str | None = (body.adapter_path or "").strip() or None
    base, adapter = _apply_model_id(base, adapter, body.model_id)
    try:
        await asyncio.to_thread(
            inference_service.preload_model,
            root,
            base_model=base,
            adapter_rel=adapter,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加载失败：{e}") from e
    return {"status": "ok"}


@router.post("/inference/unload")
async def inference_unload() -> dict[str, str]:
    await asyncio.to_thread(inference_service.unload_cached_model)
    return {"status": "ok"}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"] = "user"
    content: str = ""


class InferenceChatBody(BaseModel):
    base_model: str = Field(default=_DEFAULT_BASE_MODEL, description="基座或合并目录（ModelScope id 或工作区相对/本地路径）")
    adapter_path: str | None = None
    model_id: str | None = Field(None, description="下拉 `id` 入参，覆盖 base/adapter 解析")
    prompt: str
    max_new_tokens: int = 256
    image_workspace_path: str | None = None
    messages: list[ChatMessage] | None = None


@router.post("/inference/chat")
async def inference_chat(request: Request, root: WorkspaceRoot) -> dict[str, Any]:
    """
    JSON：可传工作区内 `image_workspace_path`（省略则仅文本/多轮）。
    multipart：字段同上，另可提供 `image` 文件；无图片时作纯文本推理。文件会保存到工作区 `uploads/playground/` 再推理。
    """
    ctype = (request.headers.get("content-type") or "").lower()

    if "multipart/form-data" in ctype:
        form = await request.form()
        base = str(form.get("base_model") or _DEFAULT_BASE_MODEL)
        adapter: str | None = (str(form.get("adapter_path") or "").strip() or None)
        model_id = str(form.get("model_id") or "").strip() or None
        prompt = str(form.get("prompt") or "")
        image_workspace_path = str(form.get("image_workspace_path") or "").strip() or None
        try:
            max_new_tokens = int(str(form.get("max_new_tokens") or "256"))
        except ValueError:
            max_new_tokens = 256
        base, adapter = _apply_model_id(base, adapter, model_id)

        raw_upload = form.get("image")
        img_path: Path | None = None
        if isinstance(raw_upload, UploadFile) and (raw_upload.filename or "").strip():
            img_path = await _save_playground_upload(root, raw_upload)
        if img_path is None:
            img_path = _resolve_image_from_workspace_path(
                root,
                image_workspace_path=image_workspace_path,
            )
        return _run_infer(
            root,
            img_path,
            base=base,
            adapter=adapter,
            prompt=prompt,
            max_new_tokens=max_new_tokens,
        )

    body = InferenceChatBody.model_validate(await request.json())
    base = body.base_model
    adapter = (body.adapter_path or "").strip() or None
    base, adapter = _apply_model_id(base, adapter, body.model_id)

    img_path = _resolve_image_from_workspace_path(
        root,
        image_workspace_path=(body.image_workspace_path or "").strip() or None,
    )

    return _run_infer(
        root,
        img_path,
        base=base,
        adapter=adapter,
        prompt=body.prompt,
        max_new_tokens=body.max_new_tokens,
    )


class SessionCreateBody(BaseModel):
    model_id: str | None = None
    base_model: str = "Qwen/Qwen3-VL-2B-Instruct"
    adapter_path: str | None = None


@router.post("/inference/session")
async def create_session(root: WorkspaceRoot, body: SessionCreateBody) -> dict[str, str]:
    sid = uuid.uuid4().hex
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    key = f"{body.base_model}::{body.adapter_path or ''}::{body.model_id or ''}"
    get_connection(root).execute(
        """
        INSERT INTO dws_inference_sessions (id, created_at, updated_at, model_key, messages_json)
        VALUES (?, ?, ?, ?, '[]')
        """,
        (sid, now, now, key),
    )
    get_connection(root).commit()
    return {"session_id": sid}


@router.get("/inference/sessions/{session_id}")
async def get_session(root: WorkspaceRoot, session_id: str) -> dict[str, Any]:
    r = get_connection(root).execute(
        "SELECT id, model_key, messages_json, updated_at FROM dws_inference_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "id": r[0],
        "model_key": r[1],
        "messages": json.loads(r[2] or "[]"),
        "updated_at": r[3],
    }


@router.post("/inference/sessions/{session_id}/export")
async def export_session(
    root: WorkspaceRoot,
    session_id: str,
    export_format: str = Query("markdown", description="markdown 或 json"),
) -> dict[str, str]:
    r = get_connection(root).execute(
        "SELECT messages_json FROM dws_inference_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = json.loads(r[0] or "[]")
    if export_format == "json":
        return {"format": "json", "content": json_dumps(messages)}
    # markdown
    lines: list[str] = ["# 数据工坊 推理会话", ""]
    for m in messages:
        who = m.get("role", "user")
        lines.append(f"## {who}\n\n{m.get('content', '')}\n")
    return {"format": "markdown", "content": "\n".join(lines)}
