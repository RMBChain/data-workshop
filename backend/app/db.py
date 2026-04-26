from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from backend.app.config import get_settings

_lock = threading.Lock()
_log = logging.getLogger("workshop.db")


def get_db_path(workspace: Path) -> Path:
    s = get_settings()
    if s.db_path is not None:
        p = s.db_path.expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    state = workspace / "state"
    state.mkdir(parents=True, exist_ok=True)
    return state / "workshop.db"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # 禁止内存映射；NFS/部分 Docker 卷上映射数据库文件会触发 disk I/O error
    try:
        conn.execute("PRAGMA mmap_size=0;")
    except sqlite3.OperationalError:
        pass
    # WAL 在部分卷上不可用，回退为 DELETE
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.OperationalError as e:
        _log.warning(
            "SQLite 无法启用 WAL（%s），已改用 DELETE 日志；可设置 WORKSHOP_DB_PATH 指向容器本地盘。: %s",
            db_path,
            e,
        )
        try:
            conn.execute("PRAGMA journal_mode=DELETE;")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("PRAGMA synchronous=NORMAL;")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """以 DDL 为唯一真实来源；不兼容时删除 `state/workshop.db` 后重启即可，不做列级升级迁移。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_kv (
            k TEXT PRIMARY KEY,
            v TEXT
        );

        CREATE TABLE IF NOT EXISTS import_batches (
            id TEXT PRIMARY KEY,
            project_id INTEGER NOT NULL,
            project_title TEXT,
            label_studio_base TEXT,
            task_count INTEGER DEFAULT 0,
            workspace_dir TEXT,
            created_at TEXT NOT NULL,
            batch_name TEXT
        );

        CREATE TABLE IF NOT EXISTS import_tasks (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            ls_task_id INTEGER NOT NULL,
            image_rel TEXT,
            resolved INTEGER DEFAULT 0,
            thumb_note TEXT,
            raw_json TEXT,
            FOREIGN KEY (batch_id) REFERENCES import_batches(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS dataset_build_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            import_batch_id TEXT,
            created_at TEXT,
            finished_at TEXT,
            error_message TEXT,
            progress REAL DEFAULT 0,
            result_version_id TEXT
        );

        CREATE TABLE IF NOT EXISTS dataset_versions (
            id TEXT PRIMARY KEY,
            import_batch_id TEXT,
            note TEXT,
            name TEXT,
            rel_dir TEXT NOT NULL,
            train_relpath TEXT,
            val_relpath TEXT,
            test_relpath TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS training_jobs_persist (
            id TEXT PRIMARY KEY,
            status TEXT,
            created_at TEXT,
            finished_at TEXT,
            return_code INTEGER,
            error_message TEXT,
            request_json TEXT,
            log_path TEXT
        );

        CREATE TABLE IF NOT EXISTS merge_jobs (
            id TEXT PRIMARY KEY,
            status TEXT,
            created_at TEXT,
            finished_at TEXT,
            log_path TEXT,
            error_message TEXT,
            request_json TEXT
        );

        CREATE TABLE IF NOT EXISTS merge_export_zips (
            training_job_id TEXT PRIMARY KEY,
            zip_relpath TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS eval_jobs (
            id TEXT PRIMARY KEY,
            status TEXT,
            created_at TEXT,
            finished_at TEXT,
            log_path TEXT,
            error_message TEXT,
            request_json TEXT,
            summary_json TEXT
        );

        CREATE TABLE IF NOT EXISTS inference_sessions (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            updated_at TEXT,
            model_key TEXT,
            messages_json TEXT
        );

        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_import_tasks_batch ON import_tasks(batch_id);")
    conn.commit()


_db_singleton: tuple[Path, sqlite3.Connection] | None = None


def get_connection(workspace: Path) -> sqlite3.Connection:
    global _db_singleton
    path = get_db_path(workspace).resolve()
    with _lock:
        if _db_singleton and _db_singleton[0] == path:
            # 连接已缓存时仍跑 init：热更新后新增表可在下次请求生效
            init_schema(_db_singleton[1])
            return _db_singleton[1]
        if _db_singleton and _db_singleton[0] != path:
            try:
                _db_singleton[1].close()
            except Exception:
                pass
        conn = connect(path)
        init_schema(conn)
        _db_singleton = (path, conn)
        return conn


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def app_kv_get(conn: sqlite3.Connection, k: str) -> str | None:
    r = conn.execute("SELECT v FROM app_kv WHERE k = ?", (k,)).fetchone()
    return r[0] if r else None


def app_kv_set(conn: sqlite3.Connection, k: str, v: str) -> None:
    conn.execute(
        "INSERT INTO app_kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (k, v),
    )


def json_dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def merge_export_zip_upsert(conn: sqlite3.Connection, training_job_id: str, zip_relpath: str) -> None:
    from datetime import datetime, timezone

    tid = (training_job_id or "").strip()
    if not tid:
        return
    rel = (zip_relpath or "").strip().replace("\\", "/")
    if not rel:
        return
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO merge_export_zips(training_job_id, zip_relpath, updated_at)
        VALUES(?,?,?)
        ON CONFLICT(training_job_id) DO UPDATE SET
          zip_relpath = excluded.zip_relpath,
          updated_at = excluded.updated_at
        """,
        (tid, rel, now),
    )
    conn.commit()


def merge_export_zip_get(conn: sqlite3.Connection, training_job_id: str) -> str | None:
    tid = (training_job_id or "").strip()
    if not tid:
        return None
    r = conn.execute(
        "SELECT zip_relpath FROM merge_export_zips WHERE training_job_id = ?",
        (tid,),
    ).fetchone()
    if not r or r[0] is None:
        return None
    s = str(r[0]).strip().replace("\\", "/")
    return s or None


def merge_export_zip_map(conn: sqlite3.Connection, training_job_ids: list[str]) -> dict[str, str | None]:
    ids = list(dict.fromkeys([str(x).strip() for x in training_job_ids if str(x).strip()]))
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT training_job_id, zip_relpath FROM merge_export_zips WHERE training_job_id IN ({placeholders})",
        ids,
    ).fetchall()
    found: dict[str, str] = {}
    for row in rows or []:
        jid = str(row[0]).strip()
        z = str(row[1]).strip().replace("\\", "/") if row[1] else ""
        if jid and z:
            found[jid] = z
    return {jid: found.get(jid) for jid in ids}
