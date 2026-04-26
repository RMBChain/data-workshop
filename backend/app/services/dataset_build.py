from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.db import get_connection, json_dumps
from backend.app.services.paths import resolve_under_workspace

log = logging.getLogger(__name__)


@dataclass
class DatasetBuildJob:
    id: str
    status: str
    import_batch_id: str
    created_at: float
    finished_at: float | None = None
    error_message: str | None = None
    progress: float = 0.0
    result_version_id: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)


def _default_question() -> str:
    return "请描述图片中的内容。"


def _default_dataset_version_name(project_title: str | None, batch_name: str | None) -> str:
    """新建数据集版本时的展示名：项目名 + 批次名 + 本地时间戳 YYYYMMdd-HHmmss。"""
    pt = (project_title or "").strip() or "未命名项目"
    bn = (batch_name or "").strip() or "未命名批次"
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{pt}{bn}{ts}"


def _extract_answer_from_ls_task(task_row_json: str | None) -> str:
    if not task_row_json:
        return "（暂无标注，占位回答）"
    try:
        t = json.loads(task_row_json)
    except Exception:
        return "（暂无标注，占位回答）"
    anns = t.get("annotations") or t.get("drafts") or []
    for ann in anns:
        res = ann.get("result") or []
        for r in res:
            if r.get("type") == "textarea" and r.get("value", {}).get("text"):
                return str(r["value"]["text"][0])[:2000]
            if r.get("type") in ("labels", "choices") and r.get("value"):
                v = r.get("value", {})
                if "text" in v:
                    return str(v.get("text", ""))[:2000]
    return "（暂无标注，占位回答）"


def _build_line_messages(
    workspace: Path,
    image_rel: str,
    user_text: str,
    answer: str,
    *,
    prepend_image_token: bool = True,
) -> dict[str, Any] | None:
    """单图 VLM 样本。user 的 content 已含 {type:image} 时，ms-swift / Qwen-VL
    的模板会为图像插入占位，勿在 text 里再手动加 ``<image>``，否则会出现
    num_media=1 而 num_media_tags=2 的告警并影响训练。prepend_image_token 保留入参以兼容旧 API，现不再使用。"""

    _ = prepend_image_token  # 保持请求体字段兼容，逻辑见上文
    u = (image_rel or "").strip()
    if u.startswith("http://") or u.startswith("https://"):
        user_msg = user_text
        return {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": u},
                        {"type": "text", "text": user_msg},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": answer}],
                },
            ]
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
    user_msg = user_text
    return {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": img_abs},
                    {"type": "text", "text": user_msg},
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": answer}],
            },
        ]
    }


class DatasetBuildManager:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        self._jobs: dict[str, DatasetBuildJob] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> DatasetBuildJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start_build(
        self,
        *,
        import_batch_id: str,
        add_image_token: bool,
        train_ratio: int,
        val_ratio: int,
        seed: int | None,
        note: str | None,
    ) -> DatasetBuildJob:
        if train_ratio + val_ratio != 100:
            raise ValueError("训练/验证比例之和须为 100")
        job_id = uuid.uuid4().hex
        now = time.time()
        job = DatasetBuildJob(
            id=job_id,
            status="pending",
            import_batch_id=import_batch_id,
            created_at=now,
        )
        with self._lock:
            self._jobs[job_id] = job

        conn = get_connection(self._workspace)
        conn.execute(
            """
            INSERT INTO dataset_build_jobs (id, status, import_batch_id, created_at, progress)
            VALUES (?, ?, ?, ?, 0)
            """,
            (job_id, "pending", import_batch_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),),
        )
        conn.commit()

        log.info(
            "数据集构建任务已入队: job_id=%s import_batch_id=%s train/val=%d/%d seed=%s",
            job_id,
            import_batch_id,
            train_ratio,
            val_ratio,
            seed,
        )
        t = threading.Thread(
            target=self._run,
            args=(job_id, add_image_token, train_ratio, val_ratio, seed, note or ""),
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
    ) -> None:
        job = self.get(job_id)
        if not job:
            return
        job.status = "running"
        _db_update_job(self._workspace, job_id, "running", None, 0.05, None)
        log.info("数据集构建开始: job_id=%s batch_id=%s", job_id, job.import_batch_id)

        conn = get_connection(self._workspace)
        rows = conn.execute(
            "SELECT image_rel, raw_json FROM import_tasks WHERE batch_id = ? AND image_rel IS NOT NULL",
            (job.import_batch_id,),
        ).fetchall()
        log.info("数据集构建: 待处理 import 行数=%d (有 image_rel)", len(rows))
        lines: list[dict[str, Any]] = []
        skipped_resolve = 0
        skipped_build = 0
        for row in rows:
            rel, raw = row[0], row[1]
            if not rel:
                continue
            is_url = rel.startswith("http://") or rel.startswith("https://")
            if not is_url:
                try:
                    resolve_under_workspace(self._workspace, rel)
                except Exception:
                    skipped_resolve += 1
                    continue
            ans = _extract_answer_from_ls_task(raw)
            ut = _default_question()
            obj = _build_line_messages(
                self._workspace, rel, ut, ans, prepend_image_token=add_image_token
            )
            if obj:
                lines.append(obj)
            else:
                skipped_build += 1
            if job.cancel_event.is_set():
                _finish_cancel(self._workspace, job_id, job)
                return

        job.progress = 0.4
        _db_update_job(self._workspace, job_id, "running", None, 0.4, None)
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
            job.status = "failed"
            job.error_message = "没有可用的本地图片样本，请检查导入任务路径是否位于工作区内"
            job.finished_at = time.time()
            _db_update_job(self._workspace, job_id, "failed", job.error_message, 1.0, None)
            log.error(
                "数据集构建失败 job_id=%s: 无有效样本 (import行=%d skip_resolve=%d skip_build=%d)",
                job_id,
                len(rows),
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

        version_id = uuid.uuid4().hex[:12]
        rel_dir = f"versions/{version_id}"
        # Path 与 / 拼接时接受正斜杠子路径，勿用 Path.sep（不存在于 pathlib.Path）
        vdir = self._workspace / rel_dir
        vdir.mkdir(parents=True, exist_ok=True)

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
        (vdir / "meta.json").write_text(
            json_dumps(
                {
                    "import_batch_id": job.import_batch_id,
                    "note": note,
                    "counts": {"train": len(a), "val": len(b), "total": n},
                }
            ),
            encoding="utf-8",
        )

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = get_connection(self._workspace)
        brow = conn.execute(
            "SELECT project_title, batch_name FROM import_batches WHERE id = ?",
            (job.import_batch_id,),
        ).fetchone()
        version_name = _default_dataset_version_name(
            brow["project_title"] if brow else None,
            brow["batch_name"] if brow else None,
        )
        conn.execute(
            """
            INSERT INTO dataset_versions (id, import_batch_id, note, name, rel_dir, train_relpath, val_relpath, test_relpath, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (version_id, job.import_batch_id, note or "", version_name, rel_dir, tr_rel, va_rel, now),
        )
        from backend.app.db import app_kv_set

        app_kv_set(conn, "active_dataset_version", version_id)
        conn.commit()

        job.status = "succeeded"
        job.finished_at = time.time()
        job.progress = 1.0
        job.result_version_id = version_id
        _db_update_job(self._workspace, job_id, "succeeded", None, 1.0, version_id)
        log.info(
            "数据集构建成功: job_id=%s version_id=%s 目录=%s",
            job_id,
            version_id,
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
    result_version: str | None,
) -> None:
    conn = get_connection(workspace)
    terminal = status in ("failed", "succeeded", "cancelled")
    fin = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if terminal else None
    if result_version is not None:
        if fin:
            conn.execute(
                """
                UPDATE dataset_build_jobs
                SET status = ?, error_message = ?, progress = ?, finished_at = ?, result_version_id = ?
                WHERE id = ?
                """,
                (status, err, progress, fin, result_version, job_id),
            )
        else:
            conn.execute(
                """
                UPDATE dataset_build_jobs SET status = ?, error_message = ?, progress = ?, result_version_id = ?
                WHERE id = ?
                """,
                (status, err, progress, result_version, job_id),
            )
    elif fin:
        conn.execute(
            """
            UPDATE dataset_build_jobs
            SET status = ?, error_message = ?, progress = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, err, progress, fin, job_id),
        )
    else:
        conn.execute(
            "UPDATE dataset_build_jobs SET status = ?, error_message = ?, progress = ? WHERE id = ?",
            (status, err, progress, job_id),
        )
    conn.commit()


def _finish_cancel(workspace: Path, job_id: str, job: DatasetBuildJob) -> None:
    job.status = "cancelled"
    job.finished_at = time.time()
    _db_update_job(workspace, job_id, "cancelled", None, 1.0, None)
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
        UPDATE dataset_build_jobs
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
