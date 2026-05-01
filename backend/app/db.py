from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_log = logging.getLogger("workshop.db")


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
            ls_import_id TEXT,
            label_studio_project_id INTEGER,
            label_studio_project_title TEXT,
            note TEXT,
            name TEXT,
            rel_dir TEXT,
            train_relpath TEXT,
            val_relpath TEXT,
            test_relpath TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dws_training_jobs_persist (
            id TEXT PRIMARY KEY,
            status TEXT,
            created_at TEXT,
            finished_at TEXT,
            return_code INTEGER,
            error_message TEXT,
            request_json TEXT,
            log_path TEXT
        );

        CREATE TABLE IF NOT EXISTS dws_merge_jobs (
            id TEXT PRIMARY KEY,
            status TEXT,
            created_at TEXT,
            finished_at TEXT,
            log_path TEXT,
            error_message TEXT,
            request_json TEXT
        );

        CREATE TABLE IF NOT EXISTS dws_merge_export_zips (
            training_job_id TEXT PRIMARY KEY,
            zip_relpath TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            merged_model_relpath TEXT
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
    for alter in (
        "ALTER TABLE dws_datasets ADD COLUMN label_studio_project_id INTEGER",
        "ALTER TABLE dws_datasets ADD COLUMN label_studio_project_title TEXT",
        "ALTER TABLE dws_datasets ADD COLUMN status TEXT NOT NULL DEFAULT 'succeeded'",
        "ALTER TABLE dws_datasets ADD COLUMN progress REAL DEFAULT 1.0",
        "ALTER TABLE dws_datasets ADD COLUMN error_message TEXT",
        "ALTER TABLE dws_datasets ADD COLUMN finished_at TEXT",
    ):
        try:
            conn.execute(alter)
        except sqlite3.OperationalError:
            pass
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


def merge_export_zip_upsert(
    conn: sqlite3.Connection,
    training_job_id: str,
    zip_relpath: str,
    merged_model_relpath: str | None = None,
) -> None:
    from datetime import datetime, timezone

    tid = (training_job_id or "").strip()
    if not tid:
        return
    rel = (zip_relpath or "").strip().replace("\\", "/")
    if not rel:
        return
    merged = (merged_model_relpath or "").strip().replace("\\", "/") or None
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO dws_merge_export_zips(
          training_job_id, zip_relpath, updated_at, merged_model_relpath
        ) VALUES(?,?,?,?)
        ON CONFLICT(training_job_id) DO UPDATE SET
          zip_relpath = excluded.zip_relpath,
          updated_at = excluded.updated_at,
          merged_model_relpath = COALESCE(
            excluded.merged_model_relpath, dws_merge_export_zips.merged_model_relpath
          )
        """,
        (tid, rel, now, merged),
    )
    conn.commit()


def merge_export_zip_get(conn: sqlite3.Connection, training_job_id: str) -> str | None:
    tid = (training_job_id or "").strip()
    if not tid:
        return None
    r = conn.execute(
        "SELECT zip_relpath FROM dws_merge_export_zips WHERE training_job_id = ?",
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
        f"SELECT training_job_id, zip_relpath FROM dws_merge_export_zips WHERE training_job_id IN ({placeholders})",
        ids,
    ).fetchall()
    found: dict[str, str] = {}
    for row in rows or []:
        jid = str(row[0]).strip()
        z = str(row[1]).strip().replace("\\", "/") if row[1] else ""
        if jid and z:
            found[jid] = z
    return {jid: found.get(jid) for jid in ids}


def merge_jobs_delete_all_for_training_id(conn: sqlite3.Connection, training_job_id: str) -> None:
    """删除该训练关联的全部 dws_merge_jobs 行（request_json.training_job_id 匹配）。"""
    tid = (training_job_id or "").strip()
    if not tid:
        return
    try:
        conn.execute(
            "DELETE FROM dws_merge_jobs WHERE json_extract(request_json, '$.training_job_id') = ?",
            (tid,),
        )
    except sqlite3.OperationalError:
        rows = conn.execute("SELECT id, request_json FROM dws_merge_jobs").fetchall()
        to_del: list[str] = []
        for r in rows or []:
            try:
                raw = r["request_json"] or "{}"
                o = json.loads(raw) if isinstance(raw, str) else {}
            except json.JSONDecodeError:
                o = {}
            t = str((o or {}).get("training_job_id") or "").strip() if isinstance(o, dict) else ""
            if t == tid:
                to_del.append(str(r["id"]))
        for old_id in to_del:
            conn.execute("DELETE FROM dws_merge_jobs WHERE id = ?", (old_id,))


def merge_jobs_prune_others_for_training(
    conn: sqlite3.Connection, training_job_id: str, keep_job_id: str
) -> None:
    """同一训练下仅保留 keep_job_id 一条 dws_merge_jobs 记录（按 request_json.training_job_id 匹配）。"""
    tid = (training_job_id or "").strip()
    kid = (keep_job_id or "").strip()
    if not tid or not kid:
        return
    try:
        conn.execute(
            """
            DELETE FROM dws_merge_jobs
            WHERE id != ?
              AND json_extract(request_json, '$.training_job_id') = ?
            """,
            (kid, tid),
        )
    except sqlite3.OperationalError:
        rows = conn.execute("SELECT id, request_json FROM dws_merge_jobs").fetchall()
        to_del: list[str] = []
        for r in rows or []:
            rid = str(r["id"])
            if rid == kid:
                continue
            try:
                raw = r["request_json"] or "{}"
                o = json.loads(raw) if isinstance(raw, str) else {}
            except json.JSONDecodeError:
                o = {}
            t = str((o or {}).get("training_job_id") or "").strip() if isinstance(o, dict) else ""
            if t == tid:
                to_del.append(rid)
        for old_id in to_del:
            conn.execute("DELETE FROM dws_merge_jobs WHERE id = ?", (old_id,))


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
    """将合并任务写入 `dws_merge_jobs`；若带 training_job_id 则同训练下仅保留本条（旧记录删除）。"""
    jid = (job_id or "").strip()
    if not jid:
        return
    req_json = json_dumps(request) if request is not None else "{}"
    conn.execute(
        """
        INSERT INTO dws_merge_jobs (id, status, created_at, finished_at, log_path, error_message, request_json)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          status = excluded.status,
          finished_at = excluded.finished_at,
          log_path = COALESCE(excluded.log_path, dws_merge_jobs.log_path),
          error_message = excluded.error_message,
          request_json = excluded.request_json
        """,
        (
            jid,
            (status or "").strip() or "unknown",
            created_at_s,
            finished_at_s,
            (log_path_s or "").strip() or None,
            (error_message or "").strip() or None,
            req_json,
        ),
    )
    req_dict = request if isinstance(request, dict) else {}
    t_train = str(req_dict.get("training_job_id") or "").strip()
    if t_train:
        merge_jobs_prune_others_for_training(conn, t_train, jid)
    conn.commit()


def merge_job_get_latest_by_training_id(
    conn: sqlite3.Connection, training_job_id: str
) -> dict[str, Any] | None:
    """取该 `training_job_id` 下最近一次合并任务（`request_json.training_job_id` 匹配）。"""
    tid = (training_job_id or "").strip()
    if not tid:
        return None
    try:
        row = conn.execute(
            """
            SELECT id, status, created_at, finished_at, log_path, error_message, request_json
            FROM dws_merge_jobs
            WHERE json_extract(request_json, '$.training_job_id') = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (tid,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    return row_to_dict(row)  # type: ignore[arg-type]


def merge_job_latest_row_per_training_id_map(
    conn: sqlite3.Connection, training_job_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """每个 training_job_id 对应 `dws_merge_jobs` 中最新一条（按 created_at），含任意 status。"""
    ids = list(dict.fromkeys([str(x).strip() for x in training_job_ids if str(x).strip()]))
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    out: dict[str, dict[str, Any]] = {}
    try:
        rows = conn.execute(
            f"""
            SELECT id, status, created_at, finished_at, log_path, error_message, request_json
            FROM (
              SELECT id, status, created_at, finished_at, log_path, error_message, request_json,
                row_number() OVER (
                  PARTITION BY json_extract(request_json, '$.training_job_id')
                  ORDER BY created_at DESC
                ) AS rn
              FROM dws_merge_jobs
              WHERE json_extract(request_json, '$.training_job_id') IN ({placeholders})
            ) AS sub
            WHERE sub.rn = 1
            """,
            ids,
        ).fetchall()
    except sqlite3.OperationalError:
        for tid in ids:
            row = merge_job_get_latest_by_training_id(conn, tid)
            if row:
                out[tid] = row
        return out
    for row in rows or []:
        d = row_to_dict(row)  # type: ignore[arg-type]
        raw = d.get("request_json") or "{}"
        try:
            obj = json.loads(raw) if isinstance(raw, str) else {}
        except json.JSONDecodeError:
            obj = {}
        tid = str((obj or {}).get("training_job_id") or "").strip() if isinstance(obj, dict) else ""
        if tid:
            out[tid] = d
    return out


def merge_export_merged_path_map(
    conn: sqlite3.Connection, training_job_ids: list[str]
) -> dict[str, str | None]:
    """已打包时记录的「合并后模型」工作区相对目录；新列缺失或旧行均为 NULL 时无值。"""
    ids = list(dict.fromkeys([str(x).strip() for x in training_job_ids if str(x).strip()]))
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    try:
        rows = conn.execute(
            f"SELECT training_job_id, merged_model_relpath FROM dws_merge_export_zips "
            f"WHERE training_job_id IN ({placeholders})",
            ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return {jid: None for jid in ids}
    found: dict[str, str] = {}
    for row in rows or []:
        jid = str(row[0]).strip()
        r = row[1]
        s = str(r).strip().replace("\\", "/") if r is not None else ""
        if jid and s:
            found[jid] = s
    return {jid: found.get(jid) for jid in ids}
