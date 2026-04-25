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
    _migrate_import_tasks_batch_id(conn)
    _migrate_import_batches_batch_name(conn)
    _migrate_dataset_versions_import_batch_id(conn)
    _migrate_dataset_versions_name(conn)
    _migrate_dataset_build_jobs_import_batch_id(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_import_tasks_batch ON import_tasks(batch_id);"
    )
    conn.commit()


def _migrate_import_tasks_batch_id(conn: sqlite3.Connection) -> None:
    """
    Legacy import_tasks used import_id or import_batch_id; current code uses batch_id.
    Also handle duplicate columns (e.g. import_id NOT NULL + added batch_id) from partial upgrades.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_tasks'"
    ).fetchone()
    if not row:
        return

    def _col_names() -> set[str]:
        return {str(r[1]) for r in conn.execute("PRAGMA table_info(import_tasks)").fetchall()}

    cols = _col_names()
    if not cols:
        return

    if "import_id" in cols and "batch_id" not in cols:
        conn.execute("ALTER TABLE import_tasks RENAME COLUMN import_id TO batch_id")
        cols = _col_names()
    if "import_batch_id" in cols and "batch_id" not in cols:
        conn.execute("ALTER TABLE import_tasks RENAME COLUMN import_batch_id TO batch_id")
        cols = _col_names()
    if "import_id" in cols and "batch_id" in cols:
        conn.execute(
            "UPDATE import_tasks SET batch_id = import_id "
            "WHERE (batch_id IS NULL OR TRIM(COALESCE(batch_id, '')) = '')"
        )
        _drop_column_import_tasks(conn, "import_id")
        cols = _col_names()
    if "batch_id" not in cols and cols:
        conn.execute("ALTER TABLE import_tasks ADD COLUMN batch_id TEXT")


def _rebuild_import_tasks_dropping_import_id(conn: sqlite3.Connection) -> None:
    """
    Rebuild import_tasks to canonical column set (batch_id, no import_id).
    We do not use ALTER TABLE DROP COLUMN: legacy tables had FOREIGN KEY (import_id) ...,
    and SQLite can reject DROP COLUMN with "unknown column import_id in foreign key definition".
    The script is re-entrant: clears any previous failed import_tasks__new.

    The staging table is created *without* a batch_id->import_batches FK. Legacy data often
    used import_id pointing at the old "imports" table, so some rows do not have a row in
    import_batches; inserting into a new table with FK would raise IntegrityError even with
    PRAGMA foreign_keys in some runtimes. fresh DBs still get the FK from init_schema.
    """
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.executescript(
            """
            PRAGMA foreign_keys=OFF;
            DROP TABLE IF EXISTS import_tasks__new;
            CREATE TABLE import_tasks__new (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                ls_task_id INTEGER NOT NULL,
                image_rel TEXT,
                resolved INTEGER DEFAULT 0,
                thumb_note TEXT,
                raw_json TEXT
            );
            INSERT INTO import_tasks__new (id, batch_id, ls_task_id, image_rel, resolved, thumb_note, raw_json)
            SELECT
                id,
                COALESCE(
                    NULLIF(TRIM(COALESCE(batch_id, '')), ''),
                    import_id
                ) AS batch_id,
                COALESCE(ls_task_id, 0),
                image_rel,
                COALESCE(resolved, 0),
                thumb_note,
                raw_json
            FROM import_tasks;
            DROP TABLE import_tasks;
            ALTER TABLE import_tasks__new RENAME TO import_tasks;
            """
        )
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _drop_column_import_tasks(conn: sqlite3.Connection, col: str) -> None:
    if col != "import_id":
        return
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(import_tasks)").fetchall()}
    if "import_id" not in cols:
        return
    _rebuild_import_tasks_dropping_import_id(conn)


def _migrate_import_batches_batch_name(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(import_batches)").fetchall()}
    if "batch_name" not in cols:
        conn.execute("ALTER TABLE import_batches ADD COLUMN batch_name TEXT")


def _migrate_dataset_versions_import_batch_id(conn: sqlite3.Connection) -> None:
    """
    Legacy dataset_versions used import_id (old imports table) or other names; app uses import_batch_id.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dataset_versions'"
    ).fetchone()
    if not row:
        return
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(dataset_versions)").fetchall()}
    if "import_batch_id" in cols:
        return
    if "import_id" in cols:
        conn.execute("ALTER TABLE dataset_versions RENAME COLUMN import_id TO import_batch_id")
        return
    if "batch_id" in cols:
        conn.execute("ALTER TABLE dataset_versions RENAME COLUMN batch_id TO import_batch_id")
        return
    conn.execute("ALTER TABLE dataset_versions ADD COLUMN import_batch_id TEXT")


def _migrate_dataset_versions_name(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(dataset_versions)").fetchall()}
    if "name" not in cols:
        conn.execute("ALTER TABLE dataset_versions ADD COLUMN name TEXT")


def _migrate_dataset_build_jobs_import_batch_id(conn: sqlite3.Connection) -> None:
    """
    Older DBs may have been created before dataset_build_jobs.import_batch_id existed;
    CREATE TABLE IF NOT EXISTS does not add new columns to existing tables.
    Some legacy tables used source_import_id or batch_id for the same reference.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dataset_build_jobs'"
    ).fetchone()
    if not row:
        return
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(dataset_build_jobs)").fetchall()}
    if "import_batch_id" in cols:
        if "source_import_id" in cols:
            conn.execute(
                "UPDATE dataset_build_jobs SET import_batch_id = source_import_id "
                "WHERE (import_batch_id IS NULL OR TRIM(COALESCE(import_batch_id, '')) = '') "
                "AND source_import_id IS NOT NULL"
            )
        return
    if "batch_id" in cols:
        conn.execute("ALTER TABLE dataset_build_jobs RENAME COLUMN batch_id TO import_batch_id")
        return
    if "source_import_id" in cols:
        conn.execute("ALTER TABLE dataset_build_jobs RENAME COLUMN source_import_id TO import_batch_id")
        return
    conn.execute("ALTER TABLE dataset_build_jobs ADD COLUMN import_batch_id TEXT")


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
