from __future__ import annotations

import json
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.db import get_connection, json_dumps
from backend.app.services.paths import resolve_under_workspace


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
    if prepend_image_token and "<image>" not in user_msg:
        user_msg = f"<image>{user_msg}"
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
        test_ratio: int,
        seed: int | None,
        note: str | None,
    ) -> DatasetBuildJob:
        if train_ratio + val_ratio + test_ratio != 100:
            raise ValueError("训练/验证/测试比例之和须为 100")
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

        t = threading.Thread(
            target=self._run,
            args=(job_id, add_image_token, train_ratio, val_ratio, test_ratio, seed, note or ""),
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
        te: int,
        seed: int | None,
        note: str,
    ) -> None:
        job = self.get(job_id)
        if not job:
            return
        job.status = "running"
        _db_update_job(self._workspace, job_id, "running", None, 0.05, None)

        conn = get_connection(self._workspace)
        rows = conn.execute(
            "SELECT image_rel, raw_json FROM import_tasks WHERE batch_id = ? AND image_rel IS NOT NULL",
            (job.import_batch_id,),
        ).fetchall()
        lines: list[dict[str, Any]] = []
        for row in rows:
            rel, raw = row[0], row[1]
            if not rel or rel.startswith("http://") or rel.startswith("https://"):
                continue
            try:
                resolve_under_workspace(self._workspace, rel)
            except Exception:
                continue
            ans = _extract_answer_from_ls_task(raw)
            ut = _default_question()
            obj = _build_line_messages(
                self._workspace, rel, ut, ans, prepend_image_token=add_image_token
            )
            if obj:
                lines.append(obj)
            if job.cancel_event.is_set():
                _finish_cancel(self._workspace, job_id, job)
                return

        job.progress = 0.4
        _db_update_job(self._workspace, job_id, "running", None, 0.4, None)

        rng = random.Random(seed if seed is not None else int(time.time()))
        rng.shuffle(lines)
        n = len(lines)
        if n == 0:
            job.status = "failed"
            job.error_message = "没有可用的本地图片样本，请检查导入任务路径是否位于工作区内"
            job.finished_at = time.time()
            _db_update_job(self._workspace, job_id, "failed", job.error_message, 1.0, None)
            return

        n_train = n * tr // 100
        n_val = n * vr // 100
        n_test = n - n_train - n_val
        if n > 0 and n_train == 0 and tr > 0:
            n_train = 1
            n_test = max(0, n - n_train - n_val)
        if n_test < 0:
            n_test = 0
        a = lines[:n_train]
        b = lines[n_train : n_train + n_val]
        c = lines[n_train + n_val :]

        version_id = uuid.uuid4().hex[:12]
        rel_dir = f"versions/{version_id}"
        vdir = self._workspace / rel_dir.replace("/", Path.sep)
        vdir.mkdir(parents=True, exist_ok=True)

        def _write(p: Path, items: list[dict[str, Any]]) -> str:
            p.write_text(
                "\n".join(json_dumps(x) for x in items) + ("\n" if items else ""),
                encoding="utf-8",
            )
            return str(p.relative_to(self._workspace)).replace("\\", "/")

        train_p = vdir / "train.jsonl"
        val_p = vdir / "val.jsonl"
        test_p = vdir / "test.jsonl"
        tr_rel = _write(train_p, a)
        va_rel = _write(val_p, b)
        te_rel = _write(test_p, c)
        (vdir / "meta.json").write_text(
            json_dumps(
                {
                    "import_batch_id": job.import_batch_id,
                    "note": note,
                    "counts": {"train": len(a), "val": len(b), "test": len(c), "total": n},
                }
            ),
            encoding="utf-8",
        )

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = get_connection(self._workspace)
        conn.execute(
            """
            INSERT INTO dataset_versions (id, import_batch_id, note, rel_dir, train_relpath, val_relpath, test_relpath, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (version_id, job.import_batch_id, note or "", rel_dir, tr_rel, va_rel, te_rel, now),
        )
        from backend.app.db import app_kv_set

        app_kv_set(conn, "active_dataset_version", version_id)
        conn.commit()

        job.status = "succeeded"
        job.finished_at = time.time()
        job.progress = 1.0
        job.result_version_id = version_id
        _db_update_job(self._workspace, job_id, "succeeded", None, 1.0, version_id)

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


_dataset_mgr: DatasetBuildManager | None = None


def get_dataset_manager(workspace: Path) -> DatasetBuildManager:
    global _dataset_mgr
    if _dataset_mgr is None or _dataset_mgr._workspace != workspace.resolve():
        _dataset_mgr = DatasetBuildManager(workspace)
    return _dataset_mgr
