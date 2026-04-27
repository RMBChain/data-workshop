from __future__ import annotations

import logging
import shutil
from pathlib import Path

from backend.app.db import get_connection, merge_export_zip_upsert
from backend.app.services.paths import resolve_under_workspace

_log = logging.getLogger("workshop.merge_export")


def _safe_filename_part(job_id: str) -> str:
    s = "".join(c for c in (job_id or "").strip() if c.isalnum() or c in "-_")
    return s or "export"


def create_and_record_merged_zip(workspace: Path, training_job_id: str, output_relpath: str) -> str | None:
    """将合并输出目录打成 zip，写入 merge_export_zips。返回工作区相对路径；跳过或失败时返回 None。"""
    tid = (training_job_id or "").strip()
    if not tid:
        _log.warning("合并 zip 跳过：training_job_id 为空")
        return None
    rel_out = (output_relpath or "").strip().replace("\\", "/")
    if not rel_out:
        _log.warning("合并 zip 跳过：output_path 为空")
        return None
    try:
        out_abs = resolve_under_workspace(workspace, rel_out)
    except ValueError as e:
        _log.error("合并 zip 输出路径非法: %s", e)
        return None
    if not out_abs.is_dir():
        _log.error("合并 zip 输出不是目录: %s", out_abs)
        return None
    root = workspace.resolve()
    exports = root / "exports"
    try:
        exports.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _log.error("合并 zip 无法创建 exports：%s", e)
        return None
    stem = _safe_filename_part(tid)
    base = exports / f"merged-model-{stem}"
    zip_file = base.with_suffix(".zip")
    try:
        if zip_file.is_file():
            zip_file.unlink()
    except OSError:
        pass
    try:
        zip_abs = Path(shutil.make_archive(str(base), "zip", root_dir=str(out_abs)))
    except OSError:
        _log.exception("合并 zip make_archive 失败")
        return None
    try:
        zip_rel = zip_abs.resolve().relative_to(root).as_posix()
    except ValueError:
        _log.error("合并 zip 落在工作区外: %s", zip_abs)
        return None
    conn = get_connection(workspace)
    merge_export_zip_upsert(conn, tid, zip_rel, rel_out)
    _log.info("合并 zip 已写入 training_job_id=%s path=%s", tid, zip_rel)
    return zip_rel
