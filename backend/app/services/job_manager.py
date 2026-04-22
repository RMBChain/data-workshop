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


class TrainJobCreate(BaseModel):
    """与 backend/scripts/train.py CLI 对齐的训练任务参数（均为相对仓库根的路径，除非为 ModelScope 模型 id）。"""

    model: str = Field(default="Qwen/Qwen3-VL-2B-Instruct", description="ModelScope 模型 id 或本地路径")
    train_dataset: str = "data/train.jsonl"
    val_dataset: str = "data/val.jsonl"
    output_dir: str = "output/qwen3vl-2b-lora"
    lora_rank: int = 4
    lora_alpha: int = 8
    target_modules: str = "all-linear"
    freeze_vit: bool = True
    num_train_epochs: int = 3
    per_device_train_batch_size: int = 1
    per_device_eval_batch_size: int = 1
    gradient_accumulation_steps: int = 8
    learning_rate: float = 1e-4
    dataloader_num_workers: int = 0
    max_length: int = 512
    logging_steps: int = 20
    save_steps: int = 500
    eval_steps: int = 500
    save_total_limit: int = 1
    warmup_ratio: float = 0.03
    lr_scheduler_type: str = "cosine"
    gradient_checkpointing: bool = True
    packing: bool = False
    image_max_token_num: int = 256
    video_max_token_num: int = 64


def _format_train_exit_message(code: int) -> str:
    """子进程非 0 退出时的人类可读说明（Unix 下负数多为 -signal）。"""
    if code == -9 or code == 137:
        return f"进程退出码 {code}（SIGKILL：常见为内存不足 OOM、容器内存上限或手动 kill -9）"
    if code < 0:
        return f"进程退出码 {code}（被系统信号 {-code} 终止）"
    return f"进程退出码 {code}"


@dataclass
class TrainJob:
    id: str
    status: str  # pending | running | succeeded | failed | cancelled
    created_at: float
    finished_at: float | None = None
    log_path: Path | None = None
    process: subprocess.Popen[bytes] | None = None
    return_code: int | None = None
    error_message: str | None = None
    request: dict[str, Any] = field(default_factory=dict)


class TrainingJobManager:
    def __init__(self, workspace_root: Path) -> None:
        self._workspace = workspace_root
        self._jobs: dict[str, TrainJob] = {}
        self._lock = threading.Lock()

    def list_jobs(self) -> list[TrainJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get_job(self, job_id: str) -> TrainJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def create_job(self, body: TrainJobCreate) -> TrainJob:
        job_id = str(uuid.uuid4())
        jobs_dir = self._workspace / "output" / "workshop-jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        log_path = jobs_dir / f"{job_id}.log"

        job = TrainJob(
            id=job_id,
            status="pending",
            created_at=time.time(),
            log_path=log_path,
            request=body.model_dump(),
        )

        train_py = self._workspace / "backend" / "scripts" / "train.py"
        if not train_py.is_file():
            job.status = "failed"
            job.error_message = f"未找到训练脚本: {train_py}"
            job.finished_at = time.time()
            with self._lock:
                self._jobs[job_id] = job
            return job

        cmd = self._build_command(body)
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = ""
        env.setdefault("PYTHONUNBUFFERED", "1")
        # 降低多线程与 glibc arena 的内存尖峰，利于小内存 / 容器内训练
        env.setdefault("MALLOC_ARENA_MAX", "2")
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("MKL_NUM_THREADS", "1")

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
                    j.error_message = _format_train_exit_message(code)

        threading.Thread(target=_wait, daemon=True).start()
        return job

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in ("pending", "running"):
                return False
            if job.process and job.process.poll() is None:
                job.process.terminate()
            job.status = "cancelled"
            job.finished_at = time.time()
            return True

    def read_log(self, job_id: str, *, max_bytes: int = 800_000) -> tuple[str, bool]:
        job = self.get_job(job_id)
        if not job or not job.log_path or not job.log_path.is_file():
            return "", False
        data = job.log_path.read_bytes()
        truncated = len(data) > max_bytes
        text = data[-max_bytes:].decode("utf-8", errors="replace") if truncated else data.decode(
            "utf-8", errors="replace"
        )
        return text, truncated

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return False
            if j.status in ("pending", "running"):
                return False
            del self._jobs[job_id]
            if j.log_path and j.log_path.is_file():
                try:
                    j.log_path.unlink()
                except OSError:
                    pass
            return True

    def retry_job(self, job_id: str) -> TrainJob | None:
        j = self.get_job(job_id)
        if not j or not j.request:
            return None
        body = TrainJobCreate.model_validate(j.request)
        return self.create_job(body)

    def _build_command(self, body: TrainJobCreate) -> list[str]:
        exe = sys.executable
        p = body.model_dump()
        cmd: list[str] = [
            exe,
            "-u",
            str(self._workspace / "backend" / "scripts" / "train.py"),
            "--model",
            p["model"],
            "--train_dataset",
            p["train_dataset"],
            "--val_dataset",
            p["val_dataset"],
            "--output_dir",
            p["output_dir"],
            "--lora_rank",
            str(p["lora_rank"]),
            "--lora_alpha",
            str(p["lora_alpha"]),
            "--target_modules",
            p["target_modules"],
            "--freeze_vit",
            str(p["freeze_vit"]).lower(),
            "--num_train_epochs",
            str(p["num_train_epochs"]),
            "--per_device_train_batch_size",
            str(p["per_device_train_batch_size"]),
            "--per_device_eval_batch_size",
            str(p["per_device_eval_batch_size"]),
            "--gradient_accumulation_steps",
            str(p["gradient_accumulation_steps"]),
            "--learning_rate",
            str(p["learning_rate"]),
            "--dataloader_num_workers",
            str(p["dataloader_num_workers"]),
            "--max_length",
            str(p["max_length"]),
            "--logging_steps",
            str(p["logging_steps"]),
            "--save_steps",
            str(p["save_steps"]),
            "--eval_steps",
            str(p["eval_steps"]),
            "--save_total_limit",
            str(p["save_total_limit"]),
            "--warmup_ratio",
            str(p["warmup_ratio"]),
            "--lr_scheduler_type",
            p["lr_scheduler_type"],
            "--gradient_checkpointing",
            str(p["gradient_checkpointing"]).lower(),
            "--packing",
            str(p["packing"]).lower(),
            "--image_max_token_num",
            str(p["image_max_token_num"]),
            "--video_max_token_num",
            str(p["video_max_token_num"]),
        ]
        return cmd
