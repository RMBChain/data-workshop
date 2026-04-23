from __future__ import annotations

import json
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

from backend.app.db import get_connection, json_dumps


class TrainJobCreate(BaseModel):
    """与 backend/scripts/train.py CLI 对齐的训练任务参数（均为相对仓库根的路径，除非为 ModelScope 模型 id）。"""

    model: str = Field(
        default="",
        description="须与「模型管理」中本机已下载的 ModelScope 模型 id 一致，例如 Qwen/Qwen3-VL-2B-Instruct",
    )
    train_dataset: str = "data/train.jsonl"
    val_dataset: str = "data/val.jsonl"
    output_dir: str = "output/"
    lora_rank: int = 1
    lora_alpha: int = 2
    target_modules: str = "all-linear"
    freeze_vit: bool = True
    num_train_epochs: int = 1
    per_device_train_batch_size: int = 1
    per_device_eval_batch_size: int = 1
    gradient_accumulation_steps: int = 1
    learning_rate: float = 1e-4
    dataloader_num_workers: int = 0
    max_length: int = 128
    logging_steps: int = 1000
    save_steps: int = 1_000_000
    eval_steps: int = 1_000_000
    save_total_limit: int = 1
    warmup_ratio: float = 0.005
    lr_scheduler_type: str = "cosine"
    gradient_checkpointing: bool = True
    packing: bool = False
    image_max_token_num: int = 64
    video_max_token_num: int = 16
    # 与 ms-swift 一致；仅「继续训练」时写入，为相对仓库根目录的 checkpoint 路径
    resume_from_checkpoint: str | None = Field(default=None, description="从该 checkpoint 目录继续，如 output/.../checkpoint-8")
    # 仅用于列表/展示，不参与 train.py 命令行
    job_name: str = Field(default="", description="展示用：训练任务名称")
    project_title: str = Field(default="", description="展示用：导入项目名")
    batch_name: str = Field(default="", description="展示用：导入批次名")
    dataset_name: str = Field(default="", description="展示用：数据集版本展示名")


def _resolve_output_dir_for_new_job(body: TrainJobCreate, job_id: str) -> TrainJobCreate:
    """占位路径 output / output/ 在创建任务后展开为 output/<job_id>，与 UI 默认一致。"""
    od = str(body.output_dir or "").strip().replace("\\", "/")
    core = od.rstrip("/")
    if core == "" or core == "output":
        return body.model_copy(update={"output_dir": f"output/{job_id}"})
    return body


def _latest_checkpoint_relpath(workspace: Path, output_dir: str) -> str | None:
    """在 output_dir 下查找最新的 checkpoint-*，返回相对 workspace 的 posix 路径。"""
    out = (workspace / (output_dir or "").strip()).resolve()
    if not out.is_dir():
        return None
    best_step = -1
    best_path: Path | None = None
    non_numeric: list[Path] = []
    for p in out.iterdir():
        if not p.is_dir() or not p.name.startswith("checkpoint-"):
            continue
        rest = p.name[len("checkpoint-") :]
        if not rest:
            continue
        try:
            step = int(rest)
        except ValueError:
            non_numeric.append(p)
            continue
        if step > best_step:
            best_step = step
            best_path = p
    if best_path is None and non_numeric:
        # 仅有 checkpoint-last 等名称时，按修改时间选最新
        best_path = max(non_numeric, key=lambda q: q.stat().st_mtime)
    if best_path is None:
        return None
    try:
        rel = best_path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return str(best_path).replace("\\", "/")
    return rel.as_posix()


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
        self._workspace = workspace_root.resolve()
        self._jobs: dict[str, TrainJob] = {}
        self._lock = threading.Lock()
        self._hydrate_from_db()

    def _log_path_resolved(self, job_id: str, stored: str | None) -> Path:
        if stored and str(stored).strip():
            p = Path(stored)
            if p.is_absolute():
                return p
            return (self._workspace / p).resolve()
        return (self._workspace / "output" / "workshop-jobs" / f"{job_id}.log").resolve()

    def _rel_log_path_for_db(self, job: TrainJob) -> str | None:
        if not job.log_path:
            return None
        try:
            return str(job.log_path.resolve().relative_to(self._workspace))
        except ValueError:
            return str(job.log_path)

    def _save_job_to_db(self, job: TrainJob) -> None:
        conn = get_connection(self._workspace)
        conn.execute(
            """
            INSERT INTO training_jobs_persist (
                id, status, created_at, finished_at, return_code, error_message, request_json, log_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                finished_at = excluded.finished_at,
                return_code = excluded.return_code,
                error_message = excluded.error_message,
                request_json = excluded.request_json,
                log_path = excluded.log_path
            """,
            (
                job.id,
                job.status,
                str(job.created_at),
                str(job.finished_at) if job.finished_at is not None else None,
                job.return_code,
                job.error_message,
                json_dumps(job.request or {}),
                self._rel_log_path_for_db(job),
            ),
        )
        conn.commit()

    def _delete_job_from_db(self, job_id: str) -> None:
        conn = get_connection(self._workspace)
        conn.execute("DELETE FROM training_jobs_persist WHERE id = ?", (job_id,))
        conn.commit()

    def _hydrate_from_db(self) -> None:
        conn = get_connection(self._workspace)
        rows = conn.execute(
            "SELECT * FROM training_jobs_persist ORDER BY CAST(created_at AS REAL) ASC"
        ).fetchall()
        now = time.time()
        for row in rows:
            d = dict(row)
            jid = str(d["id"])
            status = str(d.get("status") or "")
            err_msg = d.get("error_message")
            fin = d.get("finished_at")
            ret_code: int | None = d.get("return_code")
            if status in ("pending", "running"):
                prev = status
                status = "failed"
                note = f"服务已重启，任务已中断（原状态：{prev}）"
                err_msg = note if not (err_msg and str(err_msg).strip()) else f"{note}；{err_msg}"
                fin = str(now) if not fin else fin
                ret_code = -1
                conn.execute(
                    "UPDATE training_jobs_persist SET status = ?, error_message = ?, finished_at = ?, return_code = ? WHERE id = ?",
                    ("failed", err_msg, fin, ret_code, jid),
                )
            req_raw = d.get("request_json") or "{}"
            try:
                req = json.loads(req_raw) if isinstance(req_raw, str) else {}
            except json.JSONDecodeError:
                req = {}
            if not isinstance(req, dict):
                req = {}
            job = TrainJob(
                id=jid,
                status=status,
                created_at=float(d["created_at"]),
                finished_at=float(fin) if fin is not None and str(fin).strip() else None,
                log_path=self._log_path_resolved(jid, d.get("log_path")),
                process=None,
                return_code=ret_code,
                error_message=str(err_msg) if err_msg is not None else None,
                request=req,
            )
            self._jobs[jid] = job
        conn.commit()

    def list_jobs(self) -> list[TrainJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get_job(self, job_id: str) -> TrainJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def create_job(self, body: TrainJobCreate) -> TrainJob:
        job_id = str(uuid.uuid4())
        body = _resolve_output_dir_for_new_job(body, job_id)
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
            self._save_job_to_db(job)
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
        self._save_job_to_db(job)

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
            self._save_job_to_db(job)
            return job

        job.process = proc
        job.status = "running"
        self._save_job_to_db(job)

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
                    self._save_job_to_db(j)
                    return
                if code == 0:
                    j.status = "succeeded"
                else:
                    j.status = "failed"
                    j.error_message = _format_train_exit_message(code)
                self._save_job_to_db(j)

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
            self._save_job_to_db(job)
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
            self._delete_job_from_db(job_id)
            return True

    def update_job_name(self, job_id: str, job_name: str) -> TrainJob | None:
        name = (job_name or "").strip()
        if not name:
            return None
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return None
            j.request = dict(j.request or {})
            j.request["job_name"] = name
        self._save_job_to_db(j)
        return j

    def retry_job(self, job_id: str) -> TrainJob | None:
        """失败/取消后的「继续训练」：同一套超参 + 同一 output_dir，从最新 checkpoint 恢复。"""
        j = self.get_job(job_id)
        if not j or not j.request:
            return None
        if j.status not in ("failed", "cancelled"):
            return None
        body = TrainJobCreate.model_validate(j.request)
        ckpt = _latest_checkpoint_relpath(self._workspace, body.output_dir)
        if not ckpt:
            return None
        body = body.model_copy(update={"resume_from_checkpoint": ckpt})
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
        rfc = p.get("resume_from_checkpoint")
        if rfc:
            cmd.extend(["--resume_from_checkpoint", str(rfc)])
        return cmd
