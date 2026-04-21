from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.app.config import get_settings
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["data"])


@router.post("/data/upload-jsonl")
async def upload_jsonl(
    file: UploadFile = File(...),
    target: str = Form(default="data/uploaded.jsonl"),
) -> dict:
    """上传 JSONL 到工作区内（用于训练数据导入 MVP）。"""
    name = (file.filename or "").lower()
    if not name.endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="仅支持 .jsonl 文件")
    if not target.strip().lower().endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="target 必须以 .jsonl 结尾")

    settings = get_settings()
    root = settings.workspace_root.resolve()
    try:
        dest = resolve_under_workspace(root, target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    dest.parent.mkdir(parents=True, exist_ok=True)
    body = await file.read()
    dest.write_bytes(body)
    rel = str(dest.relative_to(root)).replace("\\", "/")
    return {"path": rel, "bytes": len(body)}


@router.post("/data/upload-image")
async def upload_image(
    file: UploadFile = File(...),
) -> dict:
    """上传单张图片到 uploads/images，供数据整理等使用。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    ext = _guess_image_suffix(file.filename or "image.png")
    uid = uuid.uuid4().hex
    rel = f"uploads/images/{uid}{ext}"
    dest = resolve_under_workspace(root, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = await file.read()
    dest.write_bytes(body)
    return {"path": rel.replace("\\", "/"), "bytes": len(body)}


def _guess_image_suffix(name: str) -> str:
    suf = Path(name).suffix.lower()
    if suf in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
        return suf
    return ".png"
