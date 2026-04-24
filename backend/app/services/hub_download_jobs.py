from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from backend.app.services import modelscope_manager as mscm

_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def enrich_job_state(st: dict[str, Any]) -> dict[str, Any]:
    """与 `GET /api/models/hub/download/{job_id}` 一致，补全各百分比字段。"""
    out = dict(st)
    cur_t = int(out.get("current_file_total") or 0)
    cur_d = int(out.get("current_file_done") or 0)
    out["current_file_percent"] = (
        round(100.0 * cur_d / cur_t, 1) if cur_t > 0 else None
    )
    tb = int(out.get("total_bytes_expected") or 0)
    bd = int(out.get("bytes_downloaded") or 0)
    ft = int(out.get("files_total_expected") or 0)
    fc = int(out.get("files_completed") or 0)
    overall: float | None = None
    if tb > 0:
        overall = min(100.0, max(0.0, round(100.0 * bd / tb, 2)))
    elif ft > 0:
        part = (cur_d / cur_t) if cur_t > 0 else 0.0
        overall = min(100.0, max(0.0, round(100.0 * (fc + part) / ft, 2)))
    out["overall_percent"] = overall
    return out


def list_running_jobs() -> list[dict[str, Any]]:
    """供列表 API 在刷新页面时恢复进行中的任务状态。"""
    out: list[dict[str, Any]] = []
    with _jobs_lock:
        for entry in _jobs.values():
            st = entry["state"]
            if st.get("status") != "running":
                continue
            with entry["lock"]:
                snap = dict(entry["state"])
            out.append(enrich_job_state(snap))
    return out


def _prune_stale_jobs(max_age_sec: float = 1800) -> None:
    now = time.time()
    with _jobs_lock:
        dead: list[str] = []
        for jid, entry in _jobs.items():
            st = entry["state"]
            if st["status"] in ("completed", "failed") and st.get("finished_at"):
                if now - float(st["finished_at"]) > max_age_sec:
                    dead.append(jid)
        for jid in dead:
            del _jobs[jid]


def _make_progress_class(state: dict[str, Any], state_lock: threading.Lock):
    from modelscope.hub.callback import ProgressCallback

    class JobProgress(ProgressCallback):
        def __init__(self, filename: str, file_size: int):
            super().__init__(filename, file_size)
            with state_lock:
                state["current_file"] = filename
                state["current_file_total"] = int(file_size) if file_size else 0
                state["current_file_done"] = 0

        def update(self, size: int) -> None:
            n = int(size)
            with state_lock:
                state["current_file_done"] = int(state.get("current_file_done") or 0) + n
                state["bytes_downloaded"] = int(state.get("bytes_downloaded") or 0) + n

        def end(self) -> None:
            with state_lock:
                state["files_completed"] = int(state.get("files_completed") or 0) + 1

    return JobProgress


def _make_minimal_progress_class(
    state: dict[str, Any], state_lock: threading.Lock
) -> type:
    """
    与 `ProgressCallback` 子类相同的状态更新接口，但无需依赖 modelscope；
    供 Hugging Face 逐文件下载时复用与魔搭任务一致的 `GET .../job` 轮询结构。
    """

    class JobProgress:
        def __init__(self, filename: str, file_size: int):
            with state_lock:
                state["current_file"] = filename
                state["current_file_total"] = int(file_size) if file_size else 0
                state["current_file_done"] = 0

        def update(self, size: int) -> None:
            n = int(size)
            with state_lock:
                state["current_file_done"] = int(
                    state.get("current_file_done") or 0
                ) + n
                state["bytes_downloaded"] = int(
                    state.get("bytes_downloaded") or 0
                ) + n

        def end(self) -> None:
            with state_lock:
                state["files_completed"] = int(
                    state.get("files_completed") or 0
                ) + 1

    return JobProgress


def _run_job(
    model_id: str,
    state: dict[str, Any],
    state_lock: threading.Lock,
    source: str,
    hf_endpoint: str | None = None,
) -> None:
    if source == "huggingface":
        progress_cls = _make_minimal_progress_class(state, state_lock)
        try:
            path = mscm.download_hf_snapshot(
                model_id,
                progress_callbacks=[progress_cls],
                hf_endpoint=hf_endpoint,
            )
        except Exception as e:
            with state_lock:
                state["status"] = "failed"
                state["error"] = str(e)
                state["finished_at"] = time.time()
            mscm.record_hub_download_failed(model_id, str(e))
            return
        with state_lock:
            state["status"] = "completed"
            state["local_path"] = path
            state["finished_at"] = time.time()
            state["current_file"] = ""
            state["current_file_total"] = 0
            state["current_file_done"] = 0
            fc = int(state.get("files_completed") or 0)
            tbe = int(state.get("total_bytes_expected") or 0)
        mscm.record_hub_download_success(
            model_id,
            path,
            files_completed=fc,
            total_bytes_expected=tbe,
        )
        return

    progress_cls = _make_progress_class(state, state_lock)
    try:
        path = mscm.download_snapshot(model_id, progress_callbacks=[progress_cls])
        with state_lock:
            state["status"] = "completed"
            state["local_path"] = path
            state["finished_at"] = time.time()
            state["current_file"] = ""
            state["current_file_total"] = 0
            state["current_file_done"] = 0
            fc = int(state.get("files_completed") or 0)
            tbe = int(state.get("total_bytes_expected") or 0)
        mscm.record_hub_download_success(
            model_id,
            path,
            files_completed=fc,
            total_bytes_expected=tbe,
        )
    except Exception as e:
        with state_lock:
            state["status"] = "failed"
            state["error"] = str(e)
            state["finished_at"] = time.time()
        mscm.record_hub_download_failed(model_id, str(e))


def start_download_job(
    model_id: str,
    source: str = "modelscope",
    hf_resolved_endpoint: str | None = None,
) -> dict[str, Any]:
    _prune_stale_jobs()
    if source == "huggingface":
        total_b, n_files = mscm.estimate_hf_download_totals(
            model_id, hf_endpoint=hf_resolved_endpoint
        )
    else:
        total_b, n_files = mscm.estimate_hub_model_download_totals(model_id)
    job_id = uuid.uuid4().hex
    state: dict[str, Any] = {
        "job_id": job_id,
        "model_id": model_id,
        "status": "running",
        "error": None,
        "local_path": None,
        "current_file": "",
        "current_file_total": 0,
        "current_file_done": 0,
        "bytes_downloaded": 0,
        "files_completed": 0,
        "total_bytes_expected": total_b,
        "files_total_expected": n_files,
        "started_at": time.time(),
        "finished_at": None,
    }
    state_lock = threading.Lock()
    with _jobs_lock:
        _jobs[job_id] = {"state": state, "lock": state_lock}
    mscm.record_hub_download_start(model_id)
    hf_ep = hf_resolved_endpoint if source == "huggingface" else None
    t = threading.Thread(
        target=_run_job,
        args=(model_id, state, state_lock, source, hf_ep),
        daemon=True,
    )
    t.start()
    return {
        "job_id": job_id,
        "model_id": model_id,
        "total_bytes_expected": total_b,
        "files_total_expected": n_files,
    }


def get_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        entry = _jobs.get(job_id)
    if not entry:
        return None
    with entry["lock"]:
        return dict(entry["state"])
