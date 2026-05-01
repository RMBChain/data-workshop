from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# 构建数据集时最长边上限（像素），避免过大原图；仅缩小、不放大
_DATASET_IMAGE_MAX_EDGE = 2048

from backend.app.db import get_connection, json_dumps
from backend.app.services import label_studio_api as ls_api
from backend.app.services.paths import resolve_under_workspace

log = logging.getLogger(__name__)


@dataclass
class DatasetBuildJob:
    id: str
    status: str
    created_at: float
    finished_at: float | None = None
    error_message: str | None = None
    progress: float = 0.0
    cancel_event: threading.Event = field(default_factory=threading.Event)
    # 从 Label Studio 拉取；token 仅内存、不入库
    ls_project_id: int | None = None
    label_studio_base: str | None = None
    label_studio_token: str | None = field(default=None, repr=False)
    label_studio_project_title: str | None = None


def _default_question() -> str:
    return "Detect all objects in the image and output their bounding boxes and labels."


DEFAULT_SFT_SYSTEM = "You are a helpful assistant."


def _user_text_with_image_token(user_text: str, add_image_token: bool) -> str:
    """在 user 的纯文本中前置 ``<image>``，与 qwen-vl 等模板的图像占位指代一致；已含则不再重复添加。"""
    t = (user_text or "").strip()
    if not add_image_token:
        return user_text
    if "<image>" in t:
        return user_text
    if not t:
        return "<image>"
    return f"<image>{t}"


def _default_dataset_version_name(project_title: str | None) -> str:
    """新建数据集版本时的展示名：项目名 + 本地时间戳 YYYYMMdd-HHmmss。"""
    pt = (project_title or "").strip() or "未命名项目"
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{pt}-{ts}"


def _rectanglelabel_compact_piece(r: dict[str, Any]) -> str | None:
    """单框：``Class: [x1, y1, x2, y2]``，坐标为 0~1（LS 百分数/100，x/w 相对宽、y/h 相对高）。"""
    v = r.get("value") if isinstance(r.get("value"), dict) else {}
    names = v.get("rectanglelabels") or v.get("labels") or []
    if not isinstance(names, list) or not names:
        return None
    try:
        x = float(v.get("x", 0))
        y = float(v.get("y", 0))
        w = float(v.get("width", 0))
        h = float(v.get("height", 0))
    except (TypeError, ValueError):
        return None
    x1 = x / 100.0
    y1 = y / 100.0
    x2 = x1 + w / 100.0
    y2 = y1 + h / 100.0
    lab = ", ".join(str(n) for n in names)
    return f"{lab}: [{x1:.4f}, {y1:.4f}, {x2:.4f}, {y2:.4f}]"


def _format_one_ls_result(r: dict[str, Any]) -> str | None:
    """从单条 Label Studio result 生成写入 response 的文本片段（不含 rectanglelabels）。"""
    t = r.get("type")
    v = r.get("value") if isinstance(r.get("value"), dict) else {}
    if t == "textarea":
        texts = v.get("text")
        if isinstance(texts, list) and texts:
            return str(texts[0]).strip() or None
        if isinstance(texts, str) and texts.strip():
            return texts.strip()
        return None
    if t == "choices":
        ch = v.get("choices")
        if isinstance(ch, list) and ch:
            return "；".join(str(x) for x in ch)
        return None
    if t == "labels":
        labels = v.get("labels") or v.get("label")
        if isinstance(labels, list) and labels:
            return "标签：" + "，".join(str(x) for x in labels)
        if isinstance(labels, str) and labels.strip():
            return f"标签：{labels.strip()}"
        if "text" in v:
            return str(v.get("text", "")).strip() or None
        return None
    if t == "polygonlabels":
        pts = v.get("points")
        names = v.get("polygonlabels") or v.get("labels") or []
        if not isinstance(names, list) or not names:
            return None
        lab = "，".join(str(x) for x in names)
        if isinstance(pts, list) and pts:
            flat: list[str] = []
            for p in pts:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    try:
                        flat.append(f"({float(p[0]) / 100.0:.6f},{float(p[1]) / 100.0:.6f})")
                    except (TypeError, ValueError):
                        continue
            if flat:
                return f"{lab}：多边形（归一化顶点 0~1）" + " ".join(flat)
        return f"{lab}：多边形标注"
    return None


def _extract_answer_from_ls_task(task_row_json: str | None) -> str:
    if not task_row_json:
        return "（暂无标注，占位回答）"
    try:
        t = json.loads(task_row_json)
    except Exception:
        return "（暂无标注，占位回答）"
    rect_parts: list[str] = []
    other_chunks: list[str] = []
    sources: list[Any] = []
    anns = t.get("annotations")
    if isinstance(anns, list) and anns:
        sources.extend(anns)
    if not sources:
        drafts = t.get("drafts")
        if isinstance(drafts, list) and drafts:
            sources.extend(drafts)
    if not sources:
        preds = t.get("predictions")
        if isinstance(preds, list) and preds:
            sources.extend(preds)
    for ann in sources:
        if not isinstance(ann, dict):
            continue
        res = ann.get("result") or []
        if not isinstance(res, list):
            continue
        for r in res:
            if not isinstance(r, dict):
                continue
            if r.get("type") == "rectanglelabels":
                rp = _rectanglelabel_compact_piece(r)
                if rp:
                    rect_parts.append(rp)
                continue
            piece = _format_one_ls_result(r)
            if piece:
                other_chunks.append(piece)
    out_parts: list[str] = []
    if rect_parts:
        out_parts.append("; ".join(rect_parts))
    if other_chunks:
        out_parts.append("\n".join(other_chunks))
    if out_parts:
        return "\n".join(out_parts)[:8000]
    return "（暂无标注，占位回答）"


def _prepare_image_for_dataset(src_abs: Path, dest_dir: Path, stem: str) -> Path | None:
    """
    将源图转为 RGB，最长边不超过 _DATASET_IMAGE_MAX_EDGE（超过则按比例缩小），写入 JPEG。
    返回产出文件绝对路径；失败时尝试原样复制，仍失败则返回 None。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_abs = dest_dir / f"{stem}.jpg"
    try:
        from PIL import Image
    except ImportError:
        try:
            shutil.copy2(src_abs, dest_abs)
            log.debug("未安装 Pillow，数据集图像已原样复制: %s", dest_abs.name)
            return dest_abs
        except OSError as e:
            log.warning("复制图像失败 stem=%s: %s", stem, e)
            return None
    try:
        with Image.open(src_abs) as im:
            im = im.convert("RGB")
            w, h = im.size
            m = max(w, h)
            if m > _DATASET_IMAGE_MAX_EDGE:
                scale = _DATASET_IMAGE_MAX_EDGE / m
                nw = max(1, int(round(w * scale)))
                nh = max(1, int(round(h * scale)))
                resample = getattr(Image, "Resampling", Image).LANCZOS
                im = im.resize((nw, nh), resample)
            im.save(dest_abs, format="JPEG", quality=95, optimize=True)
        return dest_abs
    except Exception as e:
        log.warning("图像归一化失败 stem=%s，尝试原样复制: %s", stem, e)
        try:
            shutil.copy2(src_abs, dest_abs)
            return dest_abs
        except OSError:
            return None


def _build_dataset_line(
    workspace: Path,
    image_rel: str,
    user_text: str,
    answer: str,
    *,
    prepend_image_token: bool = True,
) -> dict[str, Any] | None:
    """单图 VLM 行：ms-swift 等可用的 ``system`` / ``query`` / ``response`` / ``images`` 结构。"""
    u = (image_rel or "").strip()
    query = _user_text_with_image_token(user_text, prepend_image_token)
    if u.startswith("http://") or u.startswith("https://"):
        return {
            "system": DEFAULT_SFT_SYSTEM,
            "query": query,
            "response": answer,
            "images": [u],
        }
    try:
        p = resolve_under_workspace(workspace, image_rel) if not image_rel.startswith("http") else None
    except Exception:
        p = None
    if p is not None and p.is_file():
        img_abs = str(p.resolve())
    else:
        q = Path(image_rel)
        if q.is_file():
            try:
                q.resolve().relative_to(workspace.resolve())
                img_abs = str(q.resolve())
            except Exception:
                return None
        else:
            return None
    return {
        "system": DEFAULT_SFT_SYSTEM,
        "query": query,
        "response": answer,
        "images": [img_abs],
    }


async def _async_collect_lines_from_label_studio(
    workspace: Path,
    job: DatasetBuildJob,
    vdir: Path,
    img_dir: Path,
    add_image_token: bool,
) -> tuple[list[dict[str, Any]], int, int, int, str | None, str | None]:
    """从 Label Studio API 拉取任务并生成 JSONL 行；临时文件在 vdir/_ls_staging，结束后删除。

    返回最后一个元素为从 LS 同步的任务列表 JSON（含 project_id），供入库 ``label_studio_raw_json``。
    """
    pid = job.ls_project_id
    if pid is None:
        return [], 0, 0, 0, None, None
    base = (job.label_studio_base or "").rstrip("/")
    token = job.label_studio_token or ""
    staging = vdir / "_ls_staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "files").mkdir(parents=True, exist_ok=True)

    tasks = await ls_api.iter_project_tasks(base, token, int(pid))
    n = len(tasks)
    ls_raw_json = json_dumps(
        {
            "label_studio_project_id": int(pid),
            "task_count": n,
            "tasks": tasks,
        }
    )
    lines: list[dict[str, Any]] = []
    skipped_resolve = 0
    skipped_build = 0

    resolved_title: str | None = (job.label_studio_project_title or "").strip() or None
    if not resolved_title:
        try:
            projects = await ls_api.list_projects(base, token)
            for p in projects:
                if int(p.get("id") or 0) == int(pid):
                    resolved_title = str(p.get("title") or "").strip() or None
                    break
        except Exception:
            pass

    async with httpx.AsyncClient(timeout=120.0) as client:
        for t in tasks:
            if job.cancel_event.is_set():
                break
            tid = t.get("id")
            data = t.get("data") or {}
            if not isinstance(data, dict):
                data = {}
            img, thumb_note = ls_api.pick_image_from_task_data(data)
            raw = json_dumps(t)
            if not img:
                skipped_build += 1
                continue
            rel, _resolved, _tn = await ls_api.resolve_task_image_for_import(
                client,
                workspace,
                base,
                token,
                staging,
                int(tid) if tid is not None else None,
                img,
                thumb_note,
            )
            if not rel:
                skipped_build += 1
                continue
            is_url = rel.startswith("http://") or rel.startswith("https://")
            if not is_url:
                try:
                    resolve_under_workspace(workspace, rel)
                except Exception:
                    skipped_resolve += 1
                    continue
            image_rel_for_line = rel
            if not is_url:
                try:
                    src_p = resolve_under_workspace(workspace, rel)
                except Exception:
                    src_p = None
                if src_p is not None and src_p.is_file():
                    try:
                        tid_int = int(tid) if tid is not None else 0
                    except (TypeError, ValueError):
                        tid_int = 0
                    out_abs = _prepare_image_for_dataset(src_p, img_dir, f"task_{tid_int}")
                    if out_abs is not None:
                        image_rel_for_line = str(out_abs.relative_to(workspace)).replace("\\", "/")
            ans = _extract_answer_from_ls_task(raw)
            ut = _default_question()
            obj = _build_dataset_line(
                workspace, image_rel_for_line, ut, ans, prepend_image_token=add_image_token
            )
            if obj:
                lines.append(obj)
            else:
                skipped_build += 1

    try:
        shutil.rmtree(staging, ignore_errors=True)
    except OSError:
        pass

    return lines, skipped_resolve, skipped_build, n, resolved_title, ls_raw_json


class DatasetBuildManager:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        self._jobs: dict[str, DatasetBuildJob] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> DatasetBuildJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start_build_from_label_studio(
        self,
        *,
        label_studio_project_id: int,
        label_studio_base: str,
        label_studio_token: str,
        label_studio_project_title: str | None,
        add_image_token: bool,
        train_ratio: int,
        val_ratio: int,
        seed: int | None,
        note: str | None,
        version_name: str | None = None,
    ) -> DatasetBuildJob:
        if train_ratio + val_ratio != 100:
            raise ValueError("训练/验证比例之和须为 100")
        job_id = uuid.uuid4().hex
        now = time.time()
        job = DatasetBuildJob(
            id=job_id,
            status="pending",
            created_at=now,
            ls_project_id=int(label_studio_project_id),
            label_studio_base=label_studio_base.rstrip("/"),
            label_studio_token=label_studio_token,
            label_studio_project_title=(label_studio_project_title or "").strip() or None,
        )
        with self._lock:
            self._jobs[job_id] = job

        conn = get_connection(self._workspace)
        created_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        provisional_name = (version_name or "").strip() or None
        note_s = (note or "").strip() if note else ""
        ls_title = (label_studio_project_title or "").strip() or None
        conn.execute(
            """
            INSERT INTO dws_datasets (
                id, status, progress,
                label_studio_project_id, label_studio_project_title,
                note, name, rel_dir, train_relpath, val_relpath, test_relpath, created_at
            )
            VALUES (?, 'pending', 0, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)
            """,
            (
                job_id,
                int(label_studio_project_id),
                ls_title,
                note_s,
                provisional_name,
                created_iso,
            ),
        )
        conn.commit()

        log.info(
            "数据集构建任务已入队(LS 直连): job_id=%s project_id=%s train/val=%d/%d",
            job_id,
            label_studio_project_id,
            train_ratio,
            val_ratio,
        )
        t = threading.Thread(
            target=self._run,
            args=(job_id, add_image_token, train_ratio, val_ratio, seed, note or "", version_name),
            daemon=True,
        )
        t.start()
        return job

    def _run(
        self,
        job_id: str,
        add_image_token: bool,
        tr: int,
        vr: int,
        seed: int | None,
        note: str,
        version_name: str | None,
    ) -> None:
        job = self.get(job_id)
        if not job:
            return
        job.status = "running"
        _db_update_job(self._workspace, job_id, "running", None, 0.05)
        log.info("数据集构建开始: job_id=%s (Label Studio 直连)", job_id)

        rel_dir = f"dataset/{job_id}"
        vdir = self._workspace / rel_dir
        vdir.mkdir(parents=True, exist_ok=True)
        img_dir = vdir / "images"

        lines: list[dict[str, Any]] = []
        skipped_resolve = 0
        skipped_build = 0
        n_src = 0
        resolved_proj_title: str | None = None

        if job.ls_project_id is None:
            try:
                shutil.rmtree(vdir)
            except OSError:
                pass
            job.status = "failed"
            job.error_message = "内部错误：缺少 Label Studio 项目信息"
            job.finished_at = time.time()
            _db_update_job(self._workspace, job_id, "failed", job.error_message, 1.0)
            return

        lines, skipped_resolve, skipped_build, n_src, resolved_proj_title, ls_raw_json = asyncio.run(
            _async_collect_lines_from_label_studio(
                self._workspace, job, vdir, img_dir, add_image_token
            )
        )
        job.label_studio_token = None

        job.progress = 0.4
        _db_update_job(self._workspace, job_id, "running", None, 0.4)
        log.info(
            "数据集构建: 可写入样本行=%d 跳过(路径/解析)=%d 跳过(建样本失败)=%d",
            len(lines),
            skipped_resolve,
            skipped_build,
        )

        rng = random.Random(seed if seed is not None else int(time.time()))
        rng.shuffle(lines)
        n = len(lines)
        if n == 0:
            try:
                shutil.rmtree(vdir)
            except OSError:
                pass
            job.status = "failed"
            job.error_message = "没有可用的图片样本，请检查 Label Studio 任务中的图片路径与网络可达性"
            job.finished_at = time.time()
            _db_update_job(
                self._workspace,
                job_id,
                "failed",
                job.error_message,
                1.0,
                label_studio_raw_json=ls_raw_json,
            )
            log.error(
                "数据集构建失败 job_id=%s: 无有效样本 (源任务行=%d skip_resolve=%d skip_build=%d)",
                job_id,
                n_src,
                skipped_resolve,
                skipped_build,
            )
            return

        n_val = n * vr // 100
        n_train = n - n_val
        log.info(
            "数据集构建: 划分 train=%d val=%d (总 %d 条, 比例 %d/%d)",
            n_train,
            n_val,
            n,
            tr,
            vr,
        )
        # ms-swift 需要非空训练集；0% 训练 / 100% 验证时须至少留 1 条在 train.jsonl
        if n > 0 and n_train == 0:
            n_train = 1
            n_val = n - n_train
        # 验证比例 >0 但样本少导致 n_val=0 时，至少分 1 条到验证集（在仍有训练样本的前提下）
        if n > 1 and n_val == 0 and vr > 0:
            n_val = 1
            n_train = n - n_val
        a = lines[:n_train]
        b = lines[n_train:]

        def _write(p: Path, items: list[dict[str, Any]]) -> str:
            p.write_text(
                "\n".join(json_dumps(x) for x in items) + ("\n" if items else ""),
                encoding="utf-8",
            )
            return str(p.relative_to(self._workspace)).replace("\\", "/")

        train_p = vdir / "train.jsonl"
        val_p = vdir / "val.jsonl"
        tr_rel = _write(train_p, a)
        va_rel = _write(val_p, b)
        ls_pid_ins = job.ls_project_id
        ls_ptitle_ins = resolved_proj_title or job.label_studio_project_title
        (vdir / "meta.json").write_text(
            json_dumps(
                {
                    "source": "label_studio",
                    "label_studio_project_id": ls_pid_ins,
                    "label_studio_project_title": ls_ptitle_ins,
                    "note": note,
                    "counts": {"train": len(a), "val": len(b), "total": n},
                    "image_preprocess": {
                        "rgb": True,
                        "max_edge_px": _DATASET_IMAGE_MAX_EDGE,
                        "output_format": "JPEG",
                        "annotation_coords": "rectanglelabels：response 中为 Class: [x1,y1,x2,y2]（0~1，LS 百分数/100 后 x2=x1+w/100、y2=y1+h/100）",
                    },
                }
            ),
            encoding="utf-8",
        )

        conn = get_connection(self._workspace)
        proj_for_name = (resolved_proj_title or job.label_studio_project_title or "").strip() or None
        custom_vn = (version_name or "").strip()
        display_name = (
            custom_vn
            if custom_vn
            else _default_dataset_version_name(proj_for_name)
        )
        fin = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn.execute(
            """
            UPDATE dws_datasets SET
                status = 'succeeded',
                error_message = NULL,
                progress = 1.0,
                finished_at = ?,
                label_studio_project_id = ?,
                label_studio_project_title = ?,
                note = ?,
                name = ?,
                rel_dir = ?,
                train_relpath = ?,
                val_relpath = ?,
                test_relpath = NULL,
                label_studio_raw_json = ?
            WHERE id = ?
            """,
            (
                fin,
                ls_pid_ins,
                ls_ptitle_ins,
                note or "",
                display_name,
                rel_dir,
                tr_rel,
                va_rel,
                ls_raw_json,
                job_id,
            ),
        )
        from backend.app.db import app_kv_set

        app_kv_set(conn, "active_dataset_version", job_id)
        conn.commit()

        job.status = "succeeded"
        job.finished_at = time.time()
        job.progress = 1.0
        log.info(
            "数据集构建成功: dataset_id=%s 目录=%s",
            job_id,
            rel_dir,
        )

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j or j.status not in ("pending", "running"):
                return False
            j.cancel_event.set()
        return True


def _db_update_job(
    workspace: Path,
    job_id: str,
    status: str,
    err: str | None,
    progress: float,
    *,
    label_studio_raw_json: str | None = None,
) -> None:
    conn = get_connection(workspace)
    terminal = status in ("failed", "succeeded", "cancelled")
    fin = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if terminal else None
    if label_studio_raw_json is not None:
        if fin:
            conn.execute(
                """
                UPDATE dws_datasets
                SET status = ?, error_message = ?, progress = ?, finished_at = ?,
                    label_studio_raw_json = ?
                WHERE id = ?
                """,
                (status, err, progress, fin, label_studio_raw_json, job_id),
            )
        else:
            conn.execute(
                """
                UPDATE dws_datasets
                SET status = ?, error_message = ?, progress = ?, finished_at = NULL,
                    label_studio_raw_json = ?
                WHERE id = ?
                """,
                (status, err, progress, label_studio_raw_json, job_id),
            )
    elif fin:
        conn.execute(
            """
            UPDATE dws_datasets
            SET status = ?, error_message = ?, progress = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, err, progress, fin, job_id),
        )
    else:
        conn.execute(
            "UPDATE dws_datasets SET status = ?, error_message = ?, progress = ?, finished_at = NULL WHERE id = ?",
            (status, err, progress, job_id),
        )
    conn.commit()


def _finish_cancel(workspace: Path, job_id: str, job: DatasetBuildJob) -> None:
    job.status = "cancelled"
    job.finished_at = time.time()
    _db_update_job(workspace, job_id, "cancelled", None, 1.0)
    log.info("数据集构建已取消: job_id=%s", job_id)


def mark_stale_build_jobs_failed_on_restart(workspace: Path) -> int:
    """
    服务重启后内存中的构建线程与 DatasetBuildManager 内状态已丢失；
    若库中任务仍为 pending/running，接口将一直返回该状态。启动时记为 failed。
    返回被更新的行数。
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    msg = "服务已重启，该构建任务已中断。请重新点击「生成数据集」。"
    conn = get_connection(workspace)
    conn.execute(
        """
        UPDATE dws_datasets
        SET status = 'failed', error_message = ?, progress = 1.0, finished_at = ?
        WHERE status IN ('pending', 'running')
        """,
        (msg, now),
    )
    n_row = int(conn.execute("SELECT changes()").fetchone()[0])
    conn.commit()
    if n_row:
        log.info("已标记 %d 条未完成的构建任务为 failed（服务重启）", n_row)
    return n_row


_dataset_mgr: DatasetBuildManager | None = None


def get_dataset_manager(workspace: Path) -> DatasetBuildManager:
    global _dataset_mgr
    if _dataset_mgr is None or _dataset_mgr._workspace != workspace.resolve():
        _dataset_mgr = DatasetBuildManager(workspace)
    return _dataset_mgr
