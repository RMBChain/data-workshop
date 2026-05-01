from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

from backend.app.db import merge_job_latest_row_per_training_id_map
from backend.app.services.merge_job_manager import MergeJob, MergeJobManager

_merge_status_log = logging.getLogger("workshop.merge")

MergeUiStatus = Literal["none", "merging", "interrupted", "failed", "success"]


def _norm_lora_relpath(s: str) -> str:
    return s.strip().replace("\\", "/")


def _output_relpath_from_request(req: dict[str, Any]) -> str | None:
    o = (req or {}).get("output_path")
    if o is None:
        return None
    s = str(o).strip().replace("\\", "/")
    return s or None


def _request_dict_from_merge_row(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("request_json") or "{}"
    try:
        o = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        return {}
    return o if isinstance(o, dict) else {}


def _sqlite_persist_success_maps(
    conn: sqlite3.Connection,
    training_job_ids: list[str],
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, Any]]]:
    """SQLite `dws_merge_jobs` 中 status=succeeded 的最新记录：lora 首路径 -> 输出目录、training_job_id -> 输出目录；以及每 tid 最新一行（任意 status）。"""
    latest = merge_job_latest_row_per_training_id_map(conn, training_job_ids)
    lora_to_out: dict[str, str] = {}
    tid_to_out: dict[str, str] = {}
    for tid, row in latest.items():
        if str(row.get("status") or "").strip() != "succeeded":
            continue
        req = _request_dict_from_merge_row(row)
        out_rel = _output_relpath_from_request(req)
        if not out_rel:
            continue
        tid_to_out[tid] = out_rel
        paths = req.get("lora_paths")
        if isinstance(paths, list) and paths:
            first = str(paths[0]).strip()
            if first:
                lora_to_out[_norm_lora_relpath(first)] = out_rel
    return lora_to_out, tid_to_out, latest


def _latest_job_per_lora(manager: MergeJobManager) -> dict[str, MergeJob]:
    by_lora: dict[str, list[MergeJob]] = {}
    for j in manager.list_jobs():
        req: dict[str, Any] = j.request or {}
        paths = req.get("lora_paths")
        if not isinstance(paths, list) or not paths:
            continue
        first = str(paths[0]).strip()
        if not first:
            continue
        k = _norm_lora_relpath(first)
        by_lora.setdefault(k, []).append(j)
    out: dict[str, MergeJob] = {}
    for k, jobs in by_lora.items():
        latest = max(jobs, key=lambda x: x.created_at)
        out[k] = latest
    return out


def _latest_job_per_training_id(manager: MergeJobManager) -> dict[str, MergeJob]:
    """按 request.training_job_id 索引最新 MergeJob。当列表中 LoRA 相对路径与合并请求中不一致时，仍能对上该训练任务。"""
    by_id: dict[str, list[MergeJob]] = {}
    for j in manager.list_jobs():
        req: dict[str, Any] = j.request or {}
        tid = str(req.get("training_job_id") or "").strip()
        if not tid:
            continue
        by_id.setdefault(tid, []).append(j)
    out: dict[str, MergeJob] = {}
    for tid, jobs in by_id.items():
        out[tid] = max(jobs, key=lambda x: x.created_at)
    return out


def _merge_job_for_training_row(
    jtid: dict[str, MergeJob],
    jmap: dict[str, MergeJob],
    training_job_id: str,
    lora_key: str,
) -> MergeJob | None:
    """必须用本行 job_id 对应的内存任务决定 UI 态。按 LoRA 聚合的 jmap 在多条训练共用同一路径时，
    会把「别人的卡住任务」当成本条的状态，导致本条已合并成功仍显示合并中。"""
    jid = (training_job_id or "").strip()
    m = jtid.get(jid) if jid else None
    if m is not None:
        return m
    fallback = jmap.get(lora_key)
    if fallback is None:
        return None
    req_tid = str((fallback.request or {}).get("training_job_id") or "").strip()
    if not req_tid or req_tid == jid:
        return fallback
    return None


def _ui_status(mem: MergeJob | None, persist_success: bool) -> MergeUiStatus:
    """优先内存 MergeJob；无内存或内存未覆盖成功态时，回退到 SQLite 中已持久化的 succeeded 记录。"""
    if mem:
        if mem.status in ("pending", "running"):
            return "merging"
        if mem.status == "succeeded":
            return "success"
        if mem.status == "failed":
            return "failed"
        if mem.status == "cancelled":
            return "interrupted"
    if persist_success:
        return "success"
    return "none"


def _merge_output_relpath_for_lora(
    mem: MergeJob | None,
    persist_success: bool,
    lora_key: str,
    persist_lora_map: dict[str, str],
    training_job_id: str,
    tid_to_out: dict[str, str],
) -> str | None:
    st = _ui_status(mem, persist_success)
    if st != "success":
        return None
    if mem and mem.status == "succeeded":
        o = _output_relpath_from_request(mem.request or {})
        if o:
            return o
    return persist_lora_map.get(lora_key) or tid_to_out.get(training_job_id)


def training_merge_status_by_job_id(
    workspace: Path,
    training_rows: list[dict[str, Any]],
    manager: MergeJobManager,
    conn: sqlite3.Connection,
) -> tuple[dict[str, MergeUiStatus], dict[str, str | None]]:
    t0 = time.perf_counter()
    tids = [
        str(row.get("job_id") or "").strip()
        for row in training_rows
        if str(row.get("job_id") or "").strip()
    ]
    persist_lora, persist_tid, sqlite_latest = _sqlite_persist_success_maps(conn, tids)
    persist_lora_keys = set(persist_lora.keys())
    jmap = _latest_job_per_lora(manager)
    jtid = _latest_job_per_training_id(manager)
    out: dict[str, MergeUiStatus] = {}
    paths: dict[str, str | None] = {}
    for row in training_rows:
        jid = str(row.get("job_id") or "").strip()
        path = str(row.get("path") or "").strip()
        if not jid:
            continue
        if not path:
            out[jid] = "none"
            paths[jid] = None
            continue
        k = _norm_lora_relpath(path)
        mem = _merge_job_for_training_row(jtid, jmap, jid, k)
        db_row = sqlite_latest.get(jid)
        if mem is None and db_row is not None:
            db_st = str(db_row.get("status") or "").strip()
            if db_st == "failed":
                out[jid] = "failed"
                paths[jid] = None
                continue
            if db_st == "cancelled":
                out[jid] = "interrupted"
                paths[jid] = None
                continue
            if db_st in ("pending", "running"):
                out[jid] = "none"
                paths[jid] = None
                continue
        persist_ok = k in persist_lora_keys or bool(jid and jid in persist_tid)
        out[jid] = _ui_status(mem, persist_ok)
        paths[jid] = _merge_output_relpath_for_lora(
            mem, persist_ok, k, persist_lora, jid, persist_tid
        )
    mem_jobs = len(manager.list_jobs())
    elapsed_ms = (time.perf_counter() - t0) * 1000
    _merge_status_log.info(
        "training_merge_status_by_job_id: training_rows=%d mem_merge_jobs=%d "
        "sqlite_latest_tids=%d persist_success_tids=%d status_keys=%d total_ms=%.1f workspace=%s",
        len(training_rows),
        mem_jobs,
        len(sqlite_latest),
        len(persist_tid),
        len(out),
        elapsed_ms,
        workspace.resolve(),
    )
    return out, paths
