from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.app.db import json_dumps


class MergeJobCreate(BaseModel):
    base_model_path: str = Field(..., description="基座：ModelScope id 或工作区内相对路径或绝对本地目录")
    lora_paths: list[str] = Field(..., min_length=1)
    output_path: str = Field(..., min_length=1, description="工作区内相对路径")
    merge_lora_only: bool = Field(
        True,
        description="与 workshop_merge --merge_lora_only 一致：为 True 时将 LoRA 合并进基座并保存全量；为 False 时仅导出 PEFT 适配器目录",
    )


@dataclass
class MergeJob:
    id: str
    status: str
    created_at: float
    finished_at: float | None = None
    log_path: Path | None = None
    process: subprocess.Popen[bytes] | None = None
    return_code: int | None = None
    error_message: str | None = None
    request: dict[str, Any] = field(default_factory=dict)


class MergeJobManager:
    def __init__(self, workspace_root: Path) -> None:
        self._workspace = workspace_root.resolve()
        self._jobs: dict[str, MergeJob] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> MergeJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[MergeJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def create_job(self, body: MergeJobCreate) -> MergeJob:
        job_id = str(uuid.uuid4())
        out = self._workspace / "output" / "merge-jobs"
        out.mkdir(parents=True, exist_ok=True)
        log_path = out / f"{job_id}.log"
        job = MergeJob(
            id=job_id,
            status="pending",
            created_at=time.time(),
            log_path=log_path,
            request=body.model_dump(),
        )
        script = self._workspace / "backend" / "scripts" / "workshop_merge.py"
        if not script.is_file():
            job.status = "failed"
            job.error_message = f"未找到合并脚本: {script}"
            job.finished_at = time.time()
            with self._lock:
                self._jobs[job_id] = job
            return job
        out_rel = body.output_path.strip().replace("\\", "/")
        cmd = [sys.executable, "-u", str(script), "--base", body.base_model_path, "--output", out_rel]
        for p in body.lora_paths:
            cmd.extend(["--lora", p.replace("\\", "/")])
        cmd.extend(["--merge_lora_only", "true" if body.merge_lora_only else "false"])
        cmd.append("--extra")
        cmd.append(json_dumps([x for x in body.lora_paths[1:]]))
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = ""
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["WORKSHOP_MERGE_JOB_ID"] = job_id
        with self._lock:
            self._jobs[job_id] = job
        log_f = open(log_path, "w", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self._workspace),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
            )
        except OSError as e:
            log_f.close()
            job.status = "failed"
            job.error_message = str(e)
            job.finished_at = time.time()
            return job
        job.process = proc
        job.status = "running"

        def _pump() -> None:
            assert proc.stdout is not None
            try:
                for raw in iter(proc.stdout.readline, b""):
                    if not raw:
                        break
                    log_f.write(raw.decode("utf-8", errors="replace"))
                    log_f.flush()
            finally:
                log_f.close()

        def _wait() -> None:
            threading.Thread(target=_pump, daemon=True).start()
            code = proc.wait()
            with self._lock:
                j = self._jobs.get(job_id)
                if not j:
                    return
                j.return_code = code
                j.finished_at = time.time()
                if j.status == "cancelled":
                    return
                if code == 0:
                    j.status = "succeeded"
                else:
                    j.status = "failed"
                    j.error_message = f"进程退出码 {code}"

        threading.Thread(target=_wait, daemon=True).start()
        return job

    def read_log(self, job_id: str, *, max_bytes: int = 800_000) -> tuple[str, bool]:
        j = self.get(job_id)
        if not j or not j.log_path or not j.log_path.is_file():
            return "", False
        data = j.log_path.read_bytes()
        truncated = len(data) > max_bytes
        text = data[-max_bytes:].decode("utf-8", errors="replace") if truncated else data.decode("utf-8", errors="replace")
        return text, truncated

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j or j.status not in ("pending", "running"):
                return False
            if j.process and j.process.poll() is None:
                j.process.terminate()
            j.status = "cancelled"
            j.finished_at = time.time()
            return True
