from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterator

_lock = threading.Lock()


def get_db_path(workspace: Path) -> Path:
    state = workspace / "state"
    state.mkdir(parents=True, exist_ok=True)
    return state / "workshop.db"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
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
        CREATE INDEX IF NOT EXISTS idx_import_tasks_batch ON import_tasks(batch_id);

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
    _migrate_import_batches_batch_name(conn)
    conn.commit()


def _migrate_import_batches_batch_name(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(import_batches)").fetchall()}
    if "batch_name" not in cols:
        conn.execute("ALTER TABLE import_batches ADD COLUMN batch_name TEXT")


_db_singleton: tuple[Path, sqlite3.Connection] | None = None


def get_connection(workspace: Path) -> sqlite3.Connection:
    global _db_singleton
    path = get_db_path(workspace).resolve()
    with _lock:
        if _db_singleton and _db_singleton[0] == path:
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


@contextmanager
def db_tx(workspace: Path) -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection(workspace)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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
