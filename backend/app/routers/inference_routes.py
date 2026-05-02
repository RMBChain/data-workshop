from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.db import get_connection, json_dumps
from backend.app.services.inference_models import list_workspace_models
from backend.app.services.inference_service import infer_image
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["inference"])


@router.get("/inference/models")
async def list_models() -> dict[str, Any]:
    root = get_settings().workspace_root.resolve()
    return {"items": list_workspace_models(root)}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"] = "user"
    content: str = ""


class InferenceChatBody(BaseModel):
    base_model: str = Field(default="Qwen/Qwen3-VL-2B-Instruct", description="基座或合并目录（ModelScope id 或工作区相对/本地路径）")
    adapter_path: str | None = None
    model_id: str | None = Field(None, description="下拉 `id` 入参，覆盖 base/adapter 解析")
    prompt: str
    max_new_tokens: int = 256
    image_workspace_path: str | None = None
    import_task_id: str | None = None
    messages: list[ChatMessage] | None = None


@router.post("/inference/chat")
async def inference_chat_json(body: InferenceChatBody) -> dict[str, Any]:
    """
    目标契约：仅使用工作区内已登记路径，禁止随意 URL/本机未登记上传（与需求 §4 一致）。
    """
    settings = get_settings()
    root = settings.workspace_root.resolve()
    base = body.base_model
    adapter: str | None = (body.adapter_path or "").strip() or None
    if body.model_id:
        mid = body.model_id
        if mid.startswith("lora:"):
            adapter = mid.split(":", 1)[1]
        elif mid.startswith("merged:"):
            base = mid.split(":", 1)[1]
            adapter = None

    img_path = None
    if body.import_task_id:
        row = get_connection(root).execute(
            "SELECT image_rel FROM import_tasks WHERE id = ?",
            (body.import_task_id,),
        ).fetchone()
        if not row or not row[0]:
            raise HTTPException(status_code=400, detail="未找到导入任务或缺少图片路径")
        ir = str(row[0])
        if ir.startswith("http://") or ir.startswith("https://"):
            raise HTTPException(
                status_code=400,
                detail="该任务为远程图片 URL，按需求需使用工作区已解析的本地相对路径。",
            )
        img_path = resolve_under_workspace(root, ir)
    elif body.image_workspace_path:
        p = body.image_workspace_path.strip().replace("\\", "/")
        img_path = resolve_under_workspace(root, p)
    else:
        raise HTTPException(status_code=400, detail="请通过 image_workspace_path 或 import_task_id 指定图片。")

    if not img_path.is_file():
        raise HTTPException(status_code=400, detail="图片文件不存在于工作区指定路径下")

    if adapter:
        a = str(adapter).replace("\\", "/")
        ap = resolve_under_workspace(root, a)
        if not ap.is_dir():
            raise HTTPException(status_code=400, detail="LoRA 目录不存在于工作区")

    try:
        text = infer_image(
            root,
            img_path,
            body.prompt,
            base_model=base,
            adapter_rel=adapter,
            max_new_tokens=body.max_new_tokens,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"推理失败：{e}") from e
    try:
        rel_img = str(img_path.resolve().relative_to(root.resolve()))
    except ValueError:
        rel_img = str(img_path.resolve())
    return {"text": text, "image_path": rel_img.replace("\\", "/")}


class SessionCreateBody(BaseModel):
    model_id: str | None = None
    base_model: str = "Qwen/Qwen3-VL-2B-Instruct"
    adapter_path: str | None = None


@router.post("/inference/session")
async def create_session(body: SessionCreateBody) -> dict[str, str]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    sid = uuid.uuid4().hex
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    key = f"{body.base_model}::{body.adapter_path or ''}::{body.model_id or ''}"
    get_connection(root).execute(
        """
        INSERT INTO inference_sessions (id, created_at, updated_at, model_key, messages_json)
        VALUES (?, ?, ?, ?, '[]')
        """,
        (sid, now, now, key),
    )
    get_connection(root).commit()
    return {"session_id": sid}


@router.get("/inference/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    root = get_settings().workspace_root.resolve()
    r = get_connection(root).execute(
        "SELECT id, model_key, messages_json, updated_at FROM inference_sessions WHERE id = ?",
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
    session_id: str,
    export_format: str = Query("markdown", description="markdown 或 json"),
) -> dict[str, str]:
    root = get_settings().workspace_root.resolve()
    r = get_connection(root).execute(
        "SELECT messages_json FROM inference_sessions WHERE id = ?",
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
