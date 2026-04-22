from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from backend.app.services import modelscope_manager as mscm

_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


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


def _run_job(model_id: str, state: dict[str, Any], state_lock: threading.Lock) -> None:
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
    except Exception as e:
        with state_lock:
            state["status"] = "failed"
            state["error"] = str(e)
            state["finished_at"] = time.time()


def start_download_job(model_id: str) -> dict[str, Any]:
    _prune_stale_jobs()
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
    t = threading.Thread(
        target=_run_job,
        args=(model_id, state, state_lock),
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
