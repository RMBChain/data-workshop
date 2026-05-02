from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_log = logging.getLogger("workshop.db")

_DWS_TRAINS_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "status",
        "created_at",
        "finished_at",
        "return_code",
        "error_message",
        "request_json",
        "log_path",
        "merge_id",
    }
)

_DWS_MERGES_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "training_job_id",
        "status",
        "created_at",
        "finished_at",
        "log_path",
        "error_message",
        "request_json",
    }
)

_DWS_DATASETS_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "status",
        "progress",
        "error_message",
        "finished_at",
        "label_studio_project_id",
        "label_studio_project_title",
        "note",
        "name",
        "rel_dir",
        "train_relpath",
        "val_relpath",
        "test_relpath",
        "created_at",
        "label_studio_raw_json",
    }
)


_SCHEMA_CHECKS: tuple[tuple[str, frozenset[str]], ...] = (
    ("dws_trains", _DWS_TRAINS_COLUMNS),
    ("dws_merges", _DWS_MERGES_COLUMNS),
    ("dws_datasets", _DWS_DATASETS_COLUMNS),
)


def _table_column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows or []}


def _assert_fresh_schema(conn: sqlite3.Connection) -> None:
    """仅接受与当前 DDL 一致的库；旧文件无增量迁移，须删库重建。"""
    for tname, required in _SCHEMA_CHECKS:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (tname,),
        ).fetchone()
        if not exists:
            continue
        missing = required - _table_column_names(conn, tname)
        if missing:
            raise RuntimeError(
                f"workshop.db 中 {tname} 与当前版本不一致（缺少列: "
                f"{', '.join(sorted(missing))}"
                "）。本版本仅支持全新建库，请删除工作区 state/workshop.db 后重启。"
            )


def get_db_path(workspace: Path) -> Path:
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
            "SQLite 无法启用 WAL（%s），已改用 DELETE 日志。: %s",
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
    """以 DDL 为唯一真实来源，创建缺失的表。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dws_app_kv (
            k TEXT PRIMARY KEY,
            v TEXT
        );

        CREATE TABLE IF NOT EXISTS dws_datasets (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            progress REAL DEFAULT 0,
            error_message TEXT,
            finished_at TEXT,
            label_studio_project_id INTEGER,
            label_studio_project_title TEXT,
            note TEXT,
            name TEXT,
            rel_dir TEXT,
            train_relpath TEXT,
            val_relpath TEXT,
            test_relpath TEXT,
            created_at TEXT NOT NULL,
            label_studio_raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS dws_trains (
            id TEXT PRIMARY KEY,
            status TEXT,
            created_at TEXT,
            finished_at TEXT,
            return_code INTEGER,
            error_message TEXT,
            request_json TEXT,
            log_path TEXT,
            merge_id TEXT
        );

        CREATE TABLE IF NOT EXISTS dws_merges (
            id TEXT PRIMARY KEY,
            training_job_id TEXT NOT NULL,
            status TEXT,
            created_at TEXT,
            finished_at TEXT,
            log_path TEXT,
            error_message TEXT,
            request_json TEXT
        );

        CREATE TABLE IF NOT EXISTS dws_eval_jobs (
            id TEXT PRIMARY KEY,
            status TEXT,
            created_at TEXT,
            finished_at TEXT,
            log_path TEXT,
            error_message TEXT,
            request_json TEXT,
            summary_json TEXT
        );

        CREATE TABLE IF NOT EXISTS dws_inference_sessions (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            updated_at TEXT,
            model_key TEXT,
            messages_json TEXT
        );

        """
    )
    _assert_fresh_schema(conn)
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
    r = conn.execute("SELECT v FROM dws_app_kv WHERE k = ?", (k,)).fetchone()
    return r[0] if r else None


def app_kv_set(conn: sqlite3.Connection, k: str, v: str) -> None:
    conn.execute(
        "INSERT INTO dws_app_kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (k, v),
    )


def json_dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _dedupe_train_ids(training_job_ids: list[str]) -> list[str]:
    return list(dict.fromkeys([str(x).strip() for x in training_job_ids if str(x).strip()]))


def merge_jobs_delete_all_for_training_id(conn: sqlite3.Connection, training_job_id: str) -> None:
    """新建合并前：解除训练与合并行的关联并删除该训练下所有 `dws_merges` 行。"""
    tid = (training_job_id or "").strip()
    if not tid:
        return
    conn.execute("UPDATE dws_trains SET merge_id = NULL WHERE id = ?", (tid,))
    conn.execute("DELETE FROM dws_merges WHERE training_job_id = ?", (tid,))


def merge_job_persist_upsert(
    conn: sqlite3.Connection,
    job_id: str,
    status: str,
    created_at_s: str,
    finished_at_s: str | None,
    log_path_s: str | None,
    error_message: str | None,
    request: dict[str, Any] | None,
) -> None:
    """写入 `dws_merges` 并将 `dws_trains.merge_id` 指向该行（request 须含 training_job_id）。"""
    jid = (job_id or "").strip()
    if not jid:
        return
    req_json = json_dumps(request) if request is not None else "{}"
    req_dict = request if isinstance(request, dict) else {}
    t_train = str(req_dict.get("training_job_id") or "").strip()
    if not t_train:
        return
    conn.execute(
        """
        INSERT INTO dws_merges (
          id, training_job_id, status, created_at, finished_at,
          log_path, error_message, request_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          training_job_id = excluded.training_job_id,
          status = excluded.status,
          created_at = excluded.created_at,
          finished_at = excluded.finished_at,
          log_path = COALESCE(excluded.log_path, log_path),
          error_message = excluded.error_message,
          request_json = excluded.request_json
        """,
        (
            jid,
            t_train,
            (status or "").strip() or "unknown",
            created_at_s,
            finished_at_s,
            (log_path_s or "").strip() or None,
            (error_message or "").strip() or None,
            req_json,
        ),
    )
    conn.execute(
        "UPDATE dws_trains SET merge_id = ? WHERE id = ?",
        (jid, t_train),
    )
    conn.commit()


def merge_job_get_latest_by_training_id(
    conn: sqlite3.Connection, training_job_id: str
) -> dict[str, Any] | None:
    """取该训练当前关联的合并任务（`dws_trains.merge_id` → `dws_merges`）。"""
    tid = (training_job_id or "").strip()
    if not tid:
        return None
    return merge_job_latest_row_per_training_id_map(conn, [tid]).get(tid)


def merge_job_latest_row_per_training_id_map(
    conn: sqlite3.Connection, training_job_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """每个训练 job 至多一条关联合并（经 merge_id JOIN）。"""
    ids = _dedupe_train_ids(training_job_ids)
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    out: dict[str, dict[str, Any]] = {}
    rows = conn.execute(
        f"""
        SELECT t.id AS _train_id,
               m.id AS id,
               m.status AS status,
               m.created_at AS created_at,
               m.finished_at AS finished_at,
               m.log_path AS log_path,
               m.error_message AS error_message,
               m.request_json AS request_json
        FROM dws_trains t
        INNER JOIN dws_merges m ON m.id = t.merge_id
        WHERE t.id IN ({ph})
          AND t.merge_id IS NOT NULL
          AND trim(t.merge_id) != ''
        """,
        ids,
    ).fetchall()
    for row in rows or []:
        d = row_to_dict(row)  # type: ignore[arg-type]
        tid = str(d.pop("_train_id"))
        out[tid] = d
    return out
