from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.db import app_kv_get, get_connection, row_to_dict
from backend.app.services.dataset_build import get_dataset_manager
from backend.app.services.paths import resolve_under_workspace

router = APIRouter(tags=["datasets"])
log = logging.getLogger(__name__)


def _version_split_counts(workspace_root: Path, rel_dir: Any) -> tuple[int | None, int | None]:
    """从版本目录下的 meta.json 读取 train/val 条数；缺失或异常时返回 (None, None)。"""
    if rel_dir is None:
        return None, None
    s = str(rel_dir).strip()
    if not s:
        return None, None
    try:
        meta_path = resolve_under_workspace(workspace_root, f"{s.rstrip('/')}/meta.json")
    except ValueError:
        return None, None
    if not meta_path.is_file():
        return None, None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, None
    if not isinstance(meta, dict):
        return None, None
    c = meta.get("counts")
    if not isinstance(c, dict):
        return None, None

    def _n(key: str) -> int | None:
        v = c.get(key)
        if v is None or isinstance(v, bool):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return _n("train"), _n("val")


def _read_jsonl_raw_preview(workspace_root: Path, rel_path: Any, max_lines: int) -> str:
    """读取 JSONL 文件前若干条**原始行**（不解析合并），供界面按文件形态展示。"""
    if rel_path is None or max_lines <= 0:
        return ""
    s = str(rel_path).strip()
    if not s:
        return ""
    try:
        path = resolve_under_workspace(workspace_root, s)
    except ValueError:
        return ""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines_out: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if len(lines_out) >= max_lines:
            break
        lines_out.append(line)
    return "\n".join(lines_out)


class DatasetVersionNameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)


class DatasetBuildFromLabelStudioBody(BaseModel):
    project_id: int = Field(..., description="Label Studio 项目 ID")
    base_url: str | None = Field(None, description="LS 根 URL；空则用配置默认")
    token: str = Field(..., min_length=1, description="LS API Token")
    project_title: str | None = Field(
        None, max_length=500, description="项目名称（可选，用于默认数据集名称）"
    )
    add_image_token: bool = True
    train_ratio: int = Field(80, ge=1, le=100, description="至少 1%，否则训练集为空会导致 ms-swift 报错")
    val_ratio: int = Field(20, ge=0, le=100)
    random_seed: int | None = None
    note: str | None = None
    version_name: str | None = Field(None, max_length=500, description="数据集展示名；留空则使用「项目名-时间戳」")


@router.post("/datasets/build-from-label-studio")
async def dataset_build_from_label_studio(body: DatasetBuildFromLabelStudioBody) -> dict[str, Any]:
    """从 Label Studio 拉取任务并生成数据集。"""
    if body.train_ratio + body.val_ratio != 100:
        raise HTTPException(status_code=400, detail="训练/验证比例之和须为 100")
    vn = body.version_name.strip() if body.version_name else ""
    version_name_out: str | None = vn if vn else None
    settings = get_settings()
    root = settings.workspace_root.resolve()
    base = (body.base_url or settings.label_studio_url).rstrip("/")
    from backend.app.services import label_studio_api as ls

    try:
        projects = await ls.list_projects(base, body.token)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法访问 Label Studio：{e}") from e
    proj = next((p for p in projects if int(p.get("id") or 0) == int(body.project_id)), None)
    if not proj:
        raise HTTPException(status_code=404, detail="未找到该 project_id 对应项目")
    title = (body.project_title or "").strip() or str(proj.get("title") or "").strip() or None
    try:
        mgr = get_dataset_manager(root)
        job = mgr.start_build_from_label_studio(
            label_studio_project_id=int(body.project_id),
            label_studio_base=base,
            label_studio_token=body.token,
            label_studio_project_title=title,
            add_image_token=body.add_image_token,
            train_ratio=body.train_ratio,
            val_ratio=body.val_ratio,
            seed=body.random_seed,
            note=body.note,
            version_name=version_name_out,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    log.info(
        "已提交 LS 直连数据集构建: job_id=%s project_id=%s",
        job.id,
        body.project_id,
    )
    return {"job_id": job.id, "status": job.status}


@router.get("/datasets/jobs/{job_id}")
async def get_dataset_job(job_id: str) -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    j = get_dataset_manager(root).get(job_id)
    if j:
        return {
            "id": j.id,
            "status": j.status,
            "progress": j.progress,
            "error_message": j.error_message,
            "result_version_id": j.result_version_id,
        }
    row = get_connection(root).execute(
        "SELECT id, status, created_at, finished_at, error_message, progress, result_version_id "
        "FROM dataset_build_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    d = row_to_dict(row)
    return {
        "id": d["id"],
        "status": d["status"],
        "progress": d["progress"] or 0,
        "error_message": d["error_message"],
        "result_version_id": d["result_version_id"],
    }


@router.post("/datasets/jobs/{job_id}/cancel")
async def cancel_dataset_job(job_id: str) -> dict[str, bool]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    ok = get_dataset_manager(root).cancel(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="无法取消该任务")
    return {"ok": True}


@router.get("/datasets/versions")
async def list_dataset_versions() -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    active = app_kv_get(get_connection(root), "active_dataset_version")
    rows = get_connection(root).execute(
        "SELECT v.id, v.note, v.name, v.rel_dir, v.train_relpath, v.val_relpath, v.created_at, "
        "v.label_studio_project_id, v.label_studio_project_title AS project_title "
        "FROM dw_dataset v "
        "ORDER BY v.created_at DESC"
    ).fetchall()
    items = [row_to_dict(r) for r in rows]
    for it in items:
        it["is_active"] = it["id"] == active
        tr_n, va_n = _version_split_counts(root, it.get("rel_dir"))
        it["train_count"] = tr_n
        it["val_count"] = va_n
        rd = it.get("rel_dir")
        it["dataset"] = str(rd).strip().replace("\\", "/") if rd else None
    return {"active_version_id": active, "items": items}


@router.post("/datasets/versions/{version_id}/rollback")
async def rollback_version(version_id: str) -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute("SELECT id FROM dw_dataset WHERE id = ?", (version_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="版本不存在")
    from backend.app.db import app_kv_set

    app_kv_set(conn, "active_dataset_version", version_id)
    conn.commit()
    return {"ok": True, "active_version_id": version_id}


@router.patch("/datasets/versions/{version_id}")
async def update_dataset_version(version_id: str, body: DatasetVersionNameBody) -> dict[str, Any]:
    """更新数据集版本展示名称。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute("SELECT id FROM dw_dataset WHERE id = ?", (version_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="版本不存在")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    conn.execute("UPDATE dw_dataset SET name = ? WHERE id = ?", (name, version_id))
    conn.commit()
    return {"ok": True, "id": version_id, "name": name}


@router.get("/datasets/versions/{version_id}/data")
async def get_version_dataset_data(
    version_id: str,
    per_split: int = Query(8, ge=1, le=50, description="训练/验证每个划分最多返回的样本条数"),
) -> dict[str, Any]:
    """返回版本 meta.json 与 train/val JSONL 预览。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute(
        "SELECT id, rel_dir, train_relpath, val_relpath FROM dw_dataset WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="版本不存在")
    d = row_to_dict(row)
    rel_dir = d.get("rel_dir")
    meta: Any = None
    if rel_dir and str(rel_dir).strip():
        try:
            meta_path = resolve_under_workspace(root, f"{str(rel_dir).strip().rstrip('/')}/meta.json")
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (ValueError, json.JSONDecodeError, OSError):
            meta = None

    rel_dir_str = (str(rel_dir).strip().replace("\\", "/") if rel_dir and str(rel_dir).strip() else None)
    return {
        "version_id": version_id,
        "dataset": rel_dir_str,
        "meta": meta,
        "train_jsonl_preview": _read_jsonl_raw_preview(root, d.get("train_relpath"), per_split),
        "val_jsonl_preview": _read_jsonl_raw_preview(root, d.get("val_relpath"), per_split),
    }


@router.get("/datasets/file-text")
async def dataset_file_text(
    relpath: str = Query(..., min_length=1, description="工作区根目录下的相对路径"),
    max_bytes: int = Query(1_048_576, ge=1, le=5_242_880, description="最多返回的字节数，超出则截断"),
) -> dict[str, Any]:
    """读取工作区内文本文件（如 jsonl）的原始内容，供训练页等界面预览。路径须落在 workspace 内。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    s = relpath.strip()
    if not s:
        raise HTTPException(status_code=400, detail="路径无效")
    try:
        path = resolve_under_workspace(root, s)
    except ValueError:
        raise HTTPException(status_code=400, detail="路径非法或越界")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或不是普通文件")
    try:
        size = path.stat().st_size
    except OSError as e:
        raise HTTPException(status_code=500, detail="无法访问文件") from e
    try:
        with path.open("rb") as f:
            raw = f.read(max_bytes + 1)
    except OSError as e:
        raise HTTPException(status_code=500, detail="无法读取文件") from e
    truncated = len(raw) > max_bytes
    chunk = raw[:max_bytes] if truncated else raw
    text = chunk.decode("utf-8", errors="replace")
    return {
        "relpath": s,
        "size_bytes": size,
        "truncated": truncated,
        "max_bytes": max_bytes,
        "text": text,
    }


@router.delete("/datasets/versions/{version_id}")
async def delete_dataset_version(version_id: str) -> dict[str, Any]:
    """删除数据集版本：清除数据库记录与 versions/ 目录；若为当前活跃版本则清除活跃标记。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute(
        "SELECT id, rel_dir FROM dw_dataset WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="版本不存在")
    rel_dir = row["rel_dir"] or ""

    # 若为活跃版本，清除标记
    active = app_kv_get(conn, "active_dataset_version")
    if active == version_id:
        conn.execute("DELETE FROM app_kv WHERE k = ?", ("active_dataset_version",))

    conn.execute("DELETE FROM dw_dataset WHERE id = ?", (version_id,))
    conn.commit()

    if rel_dir and str(rel_dir).strip():
        try:
            p = resolve_under_workspace(root, str(rel_dir).strip())
            if p.exists() and p.is_dir():
                shutil.rmtree(p)
        except (ValueError, OSError) as e:
            log.warning("已删除数据库记录，但清理版本目录失败: %s", e, exc_info=True)
            return {
                "ok": True,
                "id": version_id,
                "warning": f"版本记录已删除，但目录清理失败: {e}",
            }

    log.info("数据集版本已删除: version_id=%s dir=%s", version_id, rel_dir)
    return {"ok": True, "id": version_id}


@router.get("/datasets/versions/{version_id}/export")
async def export_version(version_id: str) -> dict[str, Any]:
    """返回版本目录的清单路径；完整 zip 可后续扩展。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    row = get_connection(root).execute(
        "SELECT rel_dir FROM dw_dataset WHERE id = ?", (version_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="版本不存在")
    rel = row[0]
    p = resolve_under_workspace(root, rel)
    if not p.is_dir():
        raise HTTPException(status_code=404, detail="版本目录不存在")
    zip_name = f"{version_id}_export.zip"
    dest = root / "exports" / zip_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        dest.unlink()
    ar = str(shutil.make_archive(str(dest.with_suffix("")), "zip", root_dir=str(p)))
    return {
        "version_id": version_id,
        "zip_path": str(Path(ar).relative_to(root)).replace("\\", "/"),
        "message": "已生成 zip，路径相对于工作区根目录。",
    }


@router.get("/datasets/preview")
async def dataset_preview(version_id: str | None = Query(None)) -> dict[str, Any]:
    """返回一条与需求 §1.2 形态一致的 JSON 样例。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    vid = version_id
    if not vid:
        vid = app_kv_get(conn, "active_dataset_version")
    if not vid:
        raise HTTPException(status_code=400, detail="请指定 version_id 或先构建数据集")
    r = conn.execute(
        "SELECT train_relpath FROM dw_dataset WHERE id = ?",
        (vid,),
    ).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="版本不存在")
    tr = resolve_under_workspace(root, r[0])
    if not tr.is_file():
        raise HTTPException(status_code=404, detail="训练集文件不存在")
    first = tr.read_text(encoding="utf-8").splitlines()[:1]
    sample: Any = None
    if first:
        try:
            sample = json.loads(first[0])
        except Exception:
            sample = {"raw": first[0][:2000]}
    return {"version_id": vid, "sample": sample}
