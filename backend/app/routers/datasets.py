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


def _read_jsonl_samples(workspace_root: Path, rel_path: Any, max_items: int) -> list[Any]:
    """读取 JSONL 文件前若干条解析后的对象。"""
    if rel_path is None or max_items <= 0:
        return []
    s = str(rel_path).strip()
    if not s:
        return []
    try:
        path = resolve_under_workspace(workspace_root, s)
    except ValueError:
        return []
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[Any] = []
    for line in text.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue
        if len(out) >= max_items:
            break
        try:
            out.append(json.loads(line_stripped))
        except json.JSONDecodeError:
            out.append({"_parse_error": True, "raw_preview": line_stripped[:2000]})
    return out


class DatasetVersionNameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)


class DatasetBuildBody(BaseModel):
    import_batch_id: str
    add_image_token: bool = True
    train_ratio: int = Field(80, ge=0, le=100)
    val_ratio: int = Field(20, ge=0, le=100)
    random_seed: int | None = None
    note: str | None = None


@router.post("/datasets/build")
async def dataset_build(body: DatasetBuildBody) -> dict[str, Any]:
    if body.train_ratio + body.val_ratio != 100:
        raise HTTPException(status_code=400, detail="训练/验证比例之和须为 100")
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    b = conn.execute("SELECT 1 FROM import_batches WHERE id = ?", (body.import_batch_id,)).fetchone()
    if not b:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    try:
        mgr = get_dataset_manager(root)
        job = mgr.start_build(
            import_batch_id=body.import_batch_id,
            add_image_token=body.add_image_token,
            train_ratio=body.train_ratio,
            val_ratio=body.val_ratio,
            seed=body.random_seed,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    log.info(
        "已提交数据集构建: job_id=%s import_batch_id=%s 比例 train/val=%d/%d",
        job.id,
        body.import_batch_id,
        body.train_ratio,
        body.val_ratio,
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
            "import_batch_id": j.import_batch_id,
            "progress": j.progress,
            "error_message": j.error_message,
            "result_version_id": j.result_version_id,
        }
    row = get_connection(root).execute(
        "SELECT id, status, import_batch_id, created_at, finished_at, error_message, progress, result_version_id "
        "FROM dataset_build_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    d = row_to_dict(row)
    return {
        "id": d["id"],
        "status": d["status"],
        "import_batch_id": d["import_batch_id"],
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
        "SELECT v.id, v.import_batch_id, v.note, v.name, v.rel_dir, v.train_relpath, v.val_relpath, "
        "v.created_at, b.project_title, b.batch_name "
        "FROM dataset_versions v "
        "LEFT JOIN import_batches b ON b.id = v.import_batch_id "
        "ORDER BY v.created_at DESC"
    ).fetchall()
    items = [row_to_dict(r) for r in rows]
    for it in items:
        it["is_active"] = it["id"] == active
        tr_n, va_n = _version_split_counts(root, it.get("rel_dir"))
        it["train_count"] = tr_n
        it["val_count"] = va_n
    return {"active_version_id": active, "items": items}


@router.post("/datasets/versions/{version_id}/rollback")
async def rollback_version(version_id: str) -> dict[str, Any]:
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute("SELECT id FROM dataset_versions WHERE id = ?", (version_id,)).fetchone()
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
    row = conn.execute("SELECT id FROM dataset_versions WHERE id = ?", (version_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="版本不存在")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    conn.execute("UPDATE dataset_versions SET name = ? WHERE id = ?", (name, version_id))
    conn.commit()
    return {"ok": True, "id": version_id, "name": name}


@router.get("/datasets/versions/{version_id}/data")
async def get_version_dataset_data(
    version_id: str,
    per_split: int = Query(8, ge=1, le=50, description="训练/验证每个划分最多返回的样本条数"),
) -> dict[str, Any]:
    """返回版本 meta.json 及 train/val JSONL 的前若干条样本，供界面查看。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute(
        "SELECT id, rel_dir, train_relpath, val_relpath FROM dataset_versions WHERE id = ?",
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

    return {
        "version_id": version_id,
        "meta": meta,
        "train_samples": _read_jsonl_samples(root, d.get("train_relpath"), per_split),
        "val_samples": _read_jsonl_samples(root, d.get("val_relpath"), per_split),
    }


@router.delete("/datasets/versions/{version_id}")
async def delete_dataset_version(version_id: str) -> dict[str, Any]:
    """删除数据集版本：清除数据库记录与 versions/ 目录；若为当前活跃版本则清除活跃标记。"""
    settings = get_settings()
    root = settings.workspace_root.resolve()
    conn = get_connection(root)
    row = conn.execute(
        "SELECT id, rel_dir FROM dataset_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="版本不存在")
    rel_dir = row["rel_dir"] or ""

    # 若为活跃版本，清除标记
    active = app_kv_get(conn, "active_dataset_version")
    if active == version_id:
        conn.execute("DELETE FROM app_kv WHERE k = ?", ("active_dataset_version",))

    conn.execute("DELETE FROM dataset_versions WHERE id = ?", (version_id,))
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
        "SELECT rel_dir FROM dataset_versions WHERE id = ?", (version_id,)
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
        "SELECT train_relpath FROM dataset_versions WHERE id = ?",
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
