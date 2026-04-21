from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.app.config import get_settings
from backend.app.services.inference_service import infer_image
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["inference"])


@router.post("/inference/chat")
async def inference_chat(
    prompt: str = Form(..., description="文本提示词"),
    base_model: str = Form(default="Qwen/Qwen3-VL-2B-Instruct"),
    adapter_path: str | None = Form(default=None, description="相对 workspace 的 LoRA 目录，可空"),
    max_new_tokens: int = Form(default=256, ge=8, le=4096),
    image: UploadFile = File(..., description="图片文件"),
) -> dict:
    """多模态推理（CPU，首次请求会加载模型，可能较慢）。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    ext = Path(image.filename or "image.png").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
        ext = ".png"
    uid = uuid.uuid4().hex
    rel = f"uploads/inference/{uid}{ext}"
    try:
        dest = resolve_under_workspace(root, rel)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await image.read())
    try:
        ap = (adapter_path or "").strip() or None
        text = infer_image(
            root,
            dest,
            prompt,
            base_model=base_model,
            adapter_rel=ap,
            max_new_tokens=max_new_tokens,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"推理失败: {e}") from e
    return {"text": text, "image_path": rel.replace("\\", "/")}
