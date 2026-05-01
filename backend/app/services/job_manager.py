from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.db import get_connection, json_dumps
from backend.app.services import modelscope_manager as mscm
from backend.app.services.paths import resolve_under_workspace


class TrainJobCreate(BaseModel):
    """与 backend/scripts/train.py CLI 对齐的训练任务参数（均为相对仓库根的路径，除非为 ModelScope 模型 id）。"""

    model: str = Field(
        default="",
        description="须与「模型管理」中本机已下载的 model id 一致（作者/名称）；魔搭或 Hugging Face 下载均可，任务启动时会自动改为本机缓存目录并传给 ms-swift",
    )
    train_dataset: str = "data/train.jsonl"
    val_dataset: str = "data/val.jsonl"
    output_dir: str = "train/"
    lora_rank: int = 8
    lora_alpha: int = 32
    lora_dropout: float = Field(default=0.05, ge=0.0, le=1.0)
    target_modules: str = "all-linear"
    freeze_vit: bool = True
    num_train_epochs: int = 3
    per_device_train_batch_size: int = 1
    per_device_eval_batch_size: int = 1
    gradient_accumulation_steps: int = 4
    learning_rate: float = 1e-4
    dataloader_num_workers: int = Field(
        default=0,
        ge=0,
        le=128,
        description="ms-swift DataLoader worker 数，0 为主进程加载；可设为不超过 CPU 核心数",
    )
    max_length: int = 2048
    logging_steps: int = 10
    save_steps: int = 500
    eval_steps: int = 100
    save_total_limit: int = 3
    warmup_ratio: float = 0.1
    lr_scheduler_type: str = "cosine"
    gradient_checkpointing: bool = True
    packing: bool = False
    image_max_token_num: int = 64
    video_max_token_num: int = 16
    # 与 ms-swift 对齐的可选参数（见 swift 命令行文档）
    model_type: str = Field(default="", description="留空则 swift 按模型自动推断")
    template: str = Field(default="", description="对话模板类型，留空为自动")
    system: str = Field(default="", description="系统提示词，或 .txt 路径")
    torch_dtype: str = "float32"
    bf16: bool = False
    fp16: bool = False
    attn_impl: str = "eager"
    quant_method: str = Field(default="", description="QLoRA 时常用 bnb；可与 quant_bits 联用")
    quant_bits: int | None = Field(default=None, description="如 4 表示 4bit QLoRA；None 关闭")
    bnb_4bit_compute_dtype: str = ""
    bnb_4bit_quant_type: str = "nf4"
    bnb_4bit_use_double_quant: bool = True
    use_dora: bool = False
    lorap_lr_ratio: float | None = Field(default=None, description="LoRA+ B 矩阵 LR 倍率，常用 10～16")
    deepspeed: str = ""
    train_type: str = "lora"
    tuner_backend: str = ""
    merge_lora: bool = False
    adapters: str = Field(default="", description="逗号分隔的 adapter 路径，传给 swift --adapters")
    # 与 ms-swift 一致；仅「继续训练」时写入，为相对仓库根目录的 checkpoint 路径
    resume_from_checkpoint: str | None = Field(default=None, description="从该 checkpoint 目录继续，如 train/.../checkpoint-8")
    # 仅用于列表/展示，不参与 train.py 命令行
    job_name: str = Field(default="", description="展示用：训练任务名称")
    project_title: str = Field(default="", description="展示用：Label Studio 项目名")
    dataset_version_id: str = Field(
        default="",
        description="仅用于展开默认训练产出目录（train/<version_id>）；不参与 train 命令行。",
    )


def _norm_output_dir_key(s: str) -> str:
    return (s or "").strip().replace("\\", "/")


def _resolve_output_dir_for_new_job(body: TrainJobCreate, job_id: str) -> TrainJobCreate:
    """
    占位路径 train / train/ 在创建任务后展开为：
    - 有 dataset_version_id：train/<version_id>（同一数据集版本共用一个目录；多次训练由 ms-swift add_version 生成 v0-/v1-… 区分）
    - 否则：train/<job_id>（无版本信息时仍按任务分目录）
    有 resume_from_checkpoint 时沿用 request 中的 output_dir，不会进入占位分支。
    新任务仅允许占位 train/（由 create_job 在解析前写入）。
    """
    od = str(body.output_dir or "").strip().replace("\\", "/")
    core = od.rstrip("/")
    if core == "" or core == "train":
        vid = str(body.dataset_version_id or "").strip()
        if ".." in vid or "/" in vid or "\\" in vid:
            vid = ""
        if vid:
            return body.model_copy(update={"output_dir": f"train/{vid}"})
        return body.model_copy(update={"output_dir": f"train/{job_id}"})
    return body


def _latest_checkpoint_relpath(workspace: Path, output_dir: str) -> str | None:
    """在 output_dir 下查找最新的 checkpoint-*（含 ms-swift add_version 下的 v0-*/checkpoint-*）。"""
    out = (workspace / (output_dir or "").strip()).resolve()
    if not out.is_dir():
        return None
    best_step = -1
    best_path: Path | None = None
    non_numeric: list[Path] = []
    try:
        candidates = [p for p in out.rglob("checkpoint-*") if p.is_dir()]
    except OSError:
        return None
    for p in candidates:
        if not p.name.startswith("checkpoint-"):
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
        elif step == best_step and best_path is not None:
            try:
                if p.stat().st_mtime > best_path.stat().st_mtime:
                    best_path = p
            except OSError:
                pass
    if best_path is None and non_numeric:
        best_path = max(non_numeric, key=lambda q: q.stat().st_mtime)
    if best_path is None:
        return None
    try:
        rel = best_path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return str(best_path).replace("\\", "/")
    return rel.as_posix()


def _extract_swift_effective_output_dir_from_log(log_path: Path, *, max_bytes: int = 1_000_000) -> str | None:
    """从训练日志中解析 ms-swift 在 on_train_begin 时使用的 output_dir（add_version 下含 v0-/v1-…）。"""
    try:
        raw = log_path.read_bytes()[:max_bytes]
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace")
    for pat in (
        r"\[train_api\] on_train_begin \| output_dir=(.+?) \| max_steps=",
        r"\[train_api\] on_train_begin \| output_dir=(.+?)\s*\|",
    ):
        m = re.search(pat, text)
        if not m:
            continue
        fragment = m.group(1).strip()
        try:
            return str(ast.literal_eval(fragment))
        except (ValueError, SyntaxError):
            return fragment.strip("'\"")


def _output_path_as_workspace_rel(workspace: Path, p: str) -> str:
    p = (p or "").strip()
    if not p:
        return p
    path = Path(p)
    ws = workspace.resolve()
    if not path.is_absolute():
        return p.replace("\\", "/")
    try:
        return path.resolve().relative_to(ws).as_posix()
    except ValueError:
        return p.replace("\\", "/")


def _require_nonempty_train_jsonl(workspace: Path, train_relpath: str) -> None:
    """避免 train.jsonl 为空时 ms-swift 在「Generating train split: 0 examples」处失败。"""
    rel = (train_relpath or "").strip().replace("\\", "/")
    if not rel:
        raise ValueError("未指定训练集路径 train_dataset")
    try:
        p = resolve_under_workspace(workspace, rel)
    except ValueError as e:
        raise ValueError(f"训练集路径无效: {e}") from e
    if not p.is_file():
        raise ValueError(f"训练集文件不存在: {rel}")
    n = 0
    with p.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip():
                n += 1
    if n == 0:
        raise ValueError(
            "训练集 JSONL 无有效样本（至少 1 条）。若曾用 0% 训练比例生成版本，请重新划分数据集或新建版本。"
        )


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
    status: str  # parameters_saved | pending | running | succeeded | failed | cancelled
    created_at: float
    finished_at: float | None = None
    log_path: Path | None = None
    process: subprocess.Popen[bytes] | None = None
    return_code: int | None = None
    error_message: str | None = None
    request: dict[str, Any] = field(default_factory=dict)


def _attach_swift_run_relpath(workspace: Path, job: TrainJob) -> None:
    if not isinstance(job.request, dict) or job.request.get("swift_run_relpath"):
        return
    if not job.log_path or not job.log_path.is_file():
        return
    raw = _extract_swift_effective_output_dir_from_log(job.log_path)
    if not raw:
        return
    job.request = dict(job.request)
    job.request["swift_run_relpath"] = _output_path_as_workspace_rel(workspace, raw)


class TrainingJobManager:
    def __init__(self, workspace_root: Path) -> None:
        self._workspace = workspace_root.resolve()
        self._repo_root = get_settings().repo_root.resolve()
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
            INSERT INTO dws_trains (
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
        conn.execute("DELETE FROM dws_merges WHERE training_job_id = ?", (job_id,))
        conn.execute("DELETE FROM dws_trains WHERE id = ?", (job_id,))
        conn.commit()

    def _remove_directory_for_output_dir(self, rel: str | None) -> None:
        """
        删除该任务在 request 中记录的 output_dir 对应工作区子目录（checkpoint、LoRA 等一并清除）。
        不删除工作区根或单独的 output/、train/ 根目录，避免误伤其它任务。
        """
        if not isinstance(rel, str) or not rel.strip():
            return
        s = rel.strip().replace("\\", "/")
        if s.startswith(("/", "\\")) or ".." in s:
            return
        parts = [p for p in Path(s).parts if p and p not in (".",)]
        if ".." in parts:
            return
        target = (self._workspace / s).resolve()
        try:
            target.relative_to(self._workspace.resolve())
        except ValueError:
            return
        ws = self._workspace.resolve()
        if target == ws or target == (ws / "output") or target == (ws / "train"):
            return
        if not target.exists():
            return
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        except OSError:
            pass

    def _hydrate_from_db(self) -> None:
        conn = get_connection(self._workspace)
        rows = conn.execute(
            "SELECT * FROM dws_trains ORDER BY CAST(created_at AS REAL) ASC"
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
                    "UPDATE dws_trains SET status = ?, error_message = ?, finished_at = ?, return_code = ? WHERE id = ?",
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
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        for j in jobs:
            self._hydrate_swift_run_relpath_from_log(j)
        return jobs

    def get_job(self, job_id: str) -> TrainJob | None:
        with self._lock:
            j = self._jobs.get(job_id)
        if j is not None:
            self._hydrate_swift_run_relpath_from_log(j)
        return j

    def update_job_request_params(self, job_id: str, data: dict[str, Any]) -> TrainJob | None:
        """更新已有任务在库中的 request_json，经 TrainJobCreate 校验与 output_dir 展开，不启训练。"""
        body = TrainJobCreate.model_validate(data)
        rfc = body.resume_from_checkpoint
        if not (isinstance(rfc, str) and rfc.strip()):
            body = body.model_copy(update={"output_dir": "train/"})
        body = _resolve_output_dir_for_new_job(body, job_id)
        to_save: TrainJob | None = None
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return None
            new_req = body.model_dump()
            old = j.request
            if isinstance(old, dict):
                known = set(TrainJobCreate.model_fields.keys())
                for k, v in old.items():
                    if k not in known:
                        new_req[k] = v
            j.request = new_req
            to_save = j
        if to_save:
            self._save_job_to_db(to_save)
        return to_save

    def _hydrate_swift_run_relpath_from_log(self, job: TrainJob) -> None:
        """训练开始后日志会出现 on_train_begin 的实际 output_dir；解析后写入 request 并持久化。"""
        if not isinstance(job.request, dict) or job.request.get("swift_run_relpath"):
            return
        if not job.log_path or not job.log_path.is_file():
            return
        raw = _extract_swift_effective_output_dir_from_log(job.log_path)
        if not raw:
            return
        rel = _output_path_as_workspace_rel(self._workspace, raw)
        to_save: TrainJob | None = None
        with self._lock:
            j = self._jobs.get(job.id)
            if not j or not isinstance(j.request, dict) or j.request.get("swift_run_relpath"):
                return
            j.request = dict(j.request)
            j.request["swift_run_relpath"] = rel
            to_save = j
        if to_save:
            self._save_job_to_db(to_save)

    def _prepare_body_for_persisted_job(self, data: dict[str, Any], job_id: str) -> TrainJobCreate:
        body = TrainJobCreate.model_validate(data)
        rfc = body.resume_from_checkpoint
        if not (isinstance(rfc, str) and rfc.strip()):
            body = body.model_copy(update={"output_dir": "train/"})
        return _resolve_output_dir_for_new_job(body, job_id)

    def create_params_only_job(self, data: dict[str, Any]) -> TrainJob:
        """仅写入 request_json（状态 parameters_saved），不启训练子进程。用于「仅保存参数」新建。"""
        job_id = str(uuid.uuid4())
        body = self._prepare_body_for_persisted_job(data, job_id)
        job = TrainJob(
            id=job_id,
            status="parameters_saved",
            created_at=time.time(),
            log_path=None,
            request=body.model_dump(),
        )
        with self._lock:
            self._jobs[job_id] = job
        self._save_job_to_db(job)
        return job

    def _spawn_train_worker(self, job_id: str, body: TrainJobCreate) -> TrainJob:
        """任务已在 _jobs 中且已设 log_path、request。启动子进程并注册收尾线程。"""
        job = self._jobs.get(job_id)
        if not job or not job.log_path:
            raise RuntimeError("internal: job missing for spawn")

        try:
            _require_nonempty_train_jsonl(self._workspace, body.train_dataset)
        except ValueError as e:
            with self._lock:
                j = self._jobs.get(job_id)
                if j:
                    j.status = "failed"
                    j.error_message = str(e)
                    j.finished_at = time.time()
            self._save_job_to_db(self._jobs[job_id])
            return self._jobs[job_id]

        train_py = self._repo_root / "backend" / "scripts" / "train.py"
        if not train_py.is_file():
            with self._lock:
                j = self._jobs.get(job_id)
                if j:
                    j.status = "failed"
                    j.error_message = f"未找到训练脚本: {train_py}"
                    j.finished_at = time.time()
            self._save_job_to_db(self._jobs[job_id])
            return self._jobs[job_id]

        cmd = self._build_command(body)
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = ""
        env.setdefault("PYTHONUNBUFFERED", "1")
        # 降低多线程与 glibc arena 的内存尖峰，利于小内存 / 容器内训练
        env.setdefault("MALLOC_ARENA_MAX", "2")
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("MKL_NUM_THREADS", "1")

        log_path = job.log_path
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
            with self._lock:
                j = self._jobs.get(job_id)
                if j:
                    j.status = "failed"
                    j.error_message = str(e)
                    j.finished_at = time.time()
            self._save_job_to_db(self._jobs[job_id])
            return self._jobs[job_id]

        with self._lock:
            j = self._jobs.get(job_id)
            if j:
                j.process = proc
                j.status = "running"
        self._save_job_to_db(self._jobs[job_id])

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
                j2 = self._jobs.get(job_id)
                if not j2:
                    return
                j2.return_code = code
                j2.finished_at = time.time()
                if j2.status == "cancelled":
                    self._save_job_to_db(j2)
                    return
                if code == 0:
                    j2.status = "succeeded"
                else:
                    j2.status = "failed"
                    j2.error_message = _format_train_exit_message(code)
            j3 = self._jobs.get(job_id)
            if j3:
                _attach_swift_run_relpath(self._workspace, j3)
                if j3.status == "succeeded":
                    from backend.app.services import inference_models as _inference_models

                    _inference_models.apply_workshop_pinned_lora_relpath_on_success(self._workspace, j3)
                with self._lock:
                    self._save_job_to_db(j3)

        threading.Thread(target=_wait, daemon=True).start()
        return self._jobs[job_id]

    def start_params_saved_job(self, job_id: str) -> TrainJob | None:
        """将 parameters_saved 任务启动为真实训练（同 create_job 子进程）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "parameters_saved" or not isinstance(job.request, dict):
                return None
        try:
            body = TrainJobCreate.model_validate(job.request)
        except Exception:
            return None
        rfc = body.resume_from_checkpoint
        if not (isinstance(rfc, str) and rfc.strip()):
            body = body.model_copy(update={"output_dir": "train/"})
        body = _resolve_output_dir_for_new_job(body, job_id)
        jobs_dir = self._workspace / "output" / "workshop-jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        log_path = jobs_dir / f"{job_id}.log"
        with self._lock:
            j = self._jobs.get(job_id)
            if not j or j.status != "parameters_saved":
                return None
            j.log_path = log_path
            j.status = "pending"
            j.request = body.model_dump()
        self._save_job_to_db(self._jobs[job_id])
        return self._spawn_train_worker(job_id, body)

    def create_job(self, body: TrainJobCreate) -> TrainJob:
        job_id = str(uuid.uuid4())
        rfc = body.resume_from_checkpoint
        if not (isinstance(rfc, str) and rfc.strip()):
            body = body.model_copy(update={"output_dir": "train/"})
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

        train_py = self._repo_root / "backend" / "scripts" / "train.py"
        if not train_py.is_file():
            job.status = "failed"
            job.error_message = f"未找到训练脚本: {train_py}"
            job.finished_at = time.time()
            with self._lock:
                self._jobs[job_id] = job
            self._save_job_to_db(job)
            return job

        with self._lock:
            self._jobs[job_id] = job
        self._save_job_to_db(job)
        return self._spawn_train_worker(job_id, body)

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
            out_rel: str | None = None
            if j.request and isinstance(j.request.get("output_dir"), str):
                out_rel = j.request["output_dir"]
            # 多个任务可共享同一 output_dir（如「继续训练」新旧两条记录）；仅当再无其它任务引用时才删目录
            out_key = _norm_output_dir_key(str(out_rel)) if out_rel else ""
            share_with_others = False
            if out_key:
                for oid, oj in self._jobs.items():
                    if oid == job_id:
                        continue
                    ood = (oj.request or {}).get("output_dir")
                    if isinstance(ood, str) and _norm_output_dir_key(ood) == out_key:
                        share_with_others = True
                        break
            del self._jobs[job_id]
            if j.log_path and j.log_path.is_file():
                try:
                    j.log_path.unlink()
                except OSError:
                    pass
            if not share_with_others:
                self._remove_directory_for_output_dir(out_rel)
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
        model_arg = mscm.swift_model_arg_if_hub_cached(str(p.get("model") or ""))
        cmd: list[str] = [
            exe,
            "-u",
            str(self._repo_root / "backend" / "scripts" / "train.py"),
            "--model",
            model_arg,
            "--train_dataset",
            p["train_dataset"],
            "--val_dataset",
            p["val_dataset"],
            "--output_dir",
            p["output_dir"],
            "--add_version",
            "true",
            "--lora_rank",
            str(p["lora_rank"]),
            "--lora_alpha",
            str(p["lora_alpha"]),
            "--lora_dropout",
            str(p["lora_dropout"]),
            "--target_modules",
            p["target_modules"],
            "--train_type",
            p["train_type"],
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
            "--torch_dtype",
            p["torch_dtype"],
            "--bf16",
            str(p["bf16"]).lower(),
            "--fp16",
            str(p["fp16"]).lower(),
            "--attn_impl",
            p["attn_impl"],
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

        user_mt = (p.get("model_type") or "").strip()
        mt = user_mt
        inferred_mt = False
        if not mt:
            hint = mscm.infer_ms_swift_model_type_from_hub_dir(model_arg)
            if hint:
                mt = hint
                inferred_mt = True
        if mt:
            cmd.extend(["--model_type", mt])
        tpl = (p.get("template") or "").strip()
        if not tpl and inferred_mt and mt in ("qwen3_vl", "qwen2_vl", "qwen2_5_vl"):
            tpl = mt
        if tpl:
            cmd.extend(["--template", tpl])
        sys_prompt = (p.get("system") or "").strip()
        if sys_prompt:
            cmd.extend(["--system", sys_prompt])

        qb = p.get("quant_bits")
        if qb is not None and int(qb) > 0:
            qm = (p.get("quant_method") or "").strip() or "bnb"
            cmd.extend(["--quant_method", qm, "--quant_bits", str(int(qb))])
            bnb_dt = (p.get("bnb_4bit_compute_dtype") or "").strip()
            if bnb_dt:
                cmd.extend(["--bnb_4bit_compute_dtype", bnb_dt])
            cmd.extend(["--bnb_4bit_quant_type", p.get("bnb_4bit_quant_type") or "nf4"])
            cmd.extend(
                ["--bnb_4bit_use_double_quant", str(bool(p.get("bnb_4bit_use_double_quant", True))).lower()]
            )

        if p.get("use_dora"):
            cmd.extend(["--use_dora", "true"])
        lr_ratio = p.get("lorap_lr_ratio")
        if lr_ratio is not None:
            cmd.extend(["--lorap_lr_ratio", str(lr_ratio)])
        ds = (p.get("deepspeed") or "").strip()
        if ds:
            cmd.extend(["--deepspeed", ds])
        tb = (p.get("tuner_backend") or "").strip()
        if tb:
            cmd.extend(["--tuner_backend", tb])
        if p.get("merge_lora"):
            cmd.extend(["--merge_lora", "true"])
        ad = (p.get("adapters") or "").strip()
        if ad:
            parts = [x.strip() for x in ad.split(",") if x.strip()]
            if parts:
                cmd.append("--adapters")
                cmd.extend(parts)

        # 子进程里 train.py 的补全可能因 import/异常被跳过；在发起任务时同步写入本地 hub 的 config.json
        if mt and Path(model_arg).is_dir():
            mscm.ensure_config_json_hf_model_type(model_arg, mt)

        return cmd


_training_manager_singleton: TrainingJobManager | None = None
_training_manager_workspace: Path | None = None


def get_training_manager(workspace: Path) -> TrainingJobManager:
    """与 `/api/training`、合并页等共用的 `TrainingJobManager` 单例（每个进程一个工作区）。"""
    global _training_manager_singleton, _training_manager_workspace
    root = workspace.resolve()
    if _training_manager_singleton is None or _training_manager_workspace != root:
        _training_manager_singleton = TrainingJobManager(root)
        _training_manager_workspace = root
    return _training_manager_singleton
