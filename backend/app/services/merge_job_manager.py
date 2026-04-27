from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.db import (
    get_connection,
    json_dumps,
    merge_job_persist_upsert,
    merge_jobs_delete_all_for_training_id,
)
from backend.app.services.inference_models import _adapter_relpath_for_registered_training
from backend.app.services.paths import resolve_under_workspace

_merge_log = logging.getLogger("workshop.merge")


def _mw_rel_norm(rel: str) -> str:
    return (rel or "").strip().replace("\\", "/").lstrip("/")


def _is_merged_workshop_relpath(rel: str) -> bool:
    if not rel:
        return False
    p = rel.replace("\\", "/")
    if ".." in Path(p).parts:
        return False
    return p == "output/merged-workshop" or p.startswith("output/merged-workshop/")


def clear_merged_workshop_output_dir(workspace: Path, output_relpath: str) -> None:
    """合并子进程启动前：整目录删除，不保留多版本/历史（仅 `output/merged-workshop/...`）。"""
    rel = _mw_rel_norm(output_relpath)
    if not _is_merged_workshop_relpath(rel):
        return
    try:
        root = resolve_under_workspace(workspace, rel)
    except ValueError:
        return
    if root.is_dir():
        try:
            shutil.rmtree(root)
        except OSError as e:
            _merge_log.warning("合并前清理输出目录失败 %s: %s", root, e)
    root.parent.mkdir(parents=True, exist_ok=True)


def _remove_orphan_safetensor_shards(model_dir: Path) -> None:
    index_path = model_dir / "model.safetensors.index.json"
    if not index_path.is_file():
        return
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return
    wmap = data.get("weight_map")
    if not isinstance(wmap, dict):
        return
    expected: set[str] = set()
    for v in wmap.values():
        if isinstance(v, str) and v.strip():
            expected.add(v.strip().split("/")[-1])
    if not expected:
        return
    for p in model_dir.glob("*.safetensors"):
        if p.name in expected or p.name in ("model.safetensors", "adapter_model.safetensors"):
            continue
        if p.name.startswith("model-") and p.name.endswith(".safetensors"):
            try:
                p.unlink()
            except OSError as e:
                _merge_log.debug("删除孤立分片 %s: %s", p, e)


def _remove_history_subdirs_and_temp_files(model_dir: Path) -> None:
    for name in ("backup", "backup_merge", "old", ".trash", "__pycache__", "tmp", "cache"):
        p = model_dir / name
        if p.is_dir():
            try:
                shutil.rmtree(p)
            except OSError as e:
                _merge_log.debug("删除历史子目录 %s: %s", p, e)
    for pat in ("*.tmp", "*.bak", "*.old"):
        for f in model_dir.glob(pat):
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass


def cleanup_merged_workshop_dir_after_success(workspace: Path, output_relpath: str) -> None:
    """合并成功后：在 `output/merged-workshop/{tid}` 内清理分片/备份残留（不删 index 中仍引用的分片）。"""
    rel = _mw_rel_norm(output_relpath)
    if not _is_merged_workshop_relpath(rel):
        return
    try:
        model_dir = resolve_under_workspace(workspace, rel)
    except ValueError:
        return
    if not model_dir.is_dir():
        return
    _remove_orphan_safetensor_shards(model_dir)
    _remove_history_subdirs_and_temp_files(model_dir)


def _created_at_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()


def _finished_at_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return _created_at_iso(ts)


def read_merge_log_file(path: Path, *, max_bytes: int = 800_000) -> tuple[str, bool]:
    if not path.is_file():
        return "", False
    data = path.read_bytes()
    truncated = len(data) > max_bytes
    if truncated:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace"), truncated


def _merge_jobs_log_dir() -> Path:
    """合并任务日志仅服务轮询与排障；默认放系统 temp，避免与工作区 output/ 抢满盘（Docker 绑定挂载 errno 28）。"""
    custom = (os.environ.get("WORKSHOP_MERGE_JOBS_LOG_DIR") or "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return (Path(tempfile.gettempdir()) / "workshop-merge-jobs").resolve()


def _merge_output_relpath(body: MergeJobCreate, merge_job_id: str) -> tuple[str, str | None]:
    """返回 (工作区相对输出目录, 错误信息)。
    有 training_job_id 时为 output/merged-workshop/{该 id}；无 training_job_id 时为 output/merged-workshop/{merge_job_id}。"""
    tid0 = (body.training_job_id or "").strip() if body.training_job_id else ""
    if tid0:
        seg = tid0.replace("\\", "/").strip()
        if not seg or ".." in seg or "/" in seg:
            return "", "training_job_id 无效（不允许含路径分隔符或 ..）"
        rel = f"output/merged-workshop/{seg}".replace("\\", "/")
        return rel, None
    return f"output/merged-workshop/{merge_job_id}".replace("\\", "/"), None


class MergeJobCreate(BaseModel):
    base_model_path: str = Field(..., description="基座：ModelScope id 或工作区内相对路径或绝对本地目录")
    lora_paths: list[str] = Field(..., min_length=1)
    output_path: str = Field(
        "output/merged-workshop",
        min_length=1,
        description="忽略；合并产物目录为 output/merged-workshop/{training_job_id 或 merge_job_id}",
    )
    merge_lora_only: bool = Field(
        True,
        description="与 workshop_merge --merge_lora_only 一致：为 True 时将 LoRA 合并进基座并保存全量；为 False 时仅导出 PEFT 适配器目录",
    )
    training_job_id: str | None = Field(
        None,
        description="可选。对应训练任务 job_id；合并成功后将 zip 路径写入 merge_export_zips",
    )


def _lora_dir_has_adapter_files(p: Path) -> bool:
    return (
        p.is_dir()
        and (p / "adapter_config.json").is_file()
        and (p / "adapter_model.safetensors").is_file()
    )


def _training_request_json(workspace: Path, training_job_id: str) -> dict[str, Any] | None:
    tid = (training_job_id or "").strip()
    if not tid:
        return None
    conn = get_connection(workspace)
    row = conn.execute(
        "SELECT request_json FROM training_jobs_persist WHERE id = ?",
        (tid,),
    ).fetchone()
    if not row:
        return None
    raw = row["request_json"] or "{}"
    try:
        req = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        return None
    return req if isinstance(req, dict) else None


def _resolve_lora_paths_for_merge(
    workspace: Path, body: MergeJobCreate
) -> tuple[list[str], str | None]:
    """请求体中的路径可能滞后于磁盘；用 training_job_id 从库内 request 再解析一次。返回 (paths, error_msg)。"""
    ws = workspace.resolve()
    cleaned = [str(p).strip().replace("\\", "/") for p in body.lora_paths if str(p).strip()]
    if not cleaned:
        return [], "未提供 LoRA 路径"

    def abs_lora(rel: str) -> Path:
        q = Path(rel)
        if q.is_absolute():
            return q.resolve()
        return (ws / rel).resolve()

    first = cleaned[0]
    p0 = abs_lora(first)
    try:
        p0.relative_to(ws)
    except ValueError:
        return cleaned, f"LoRA 路径不允许超出工作区: {first}"

    if _lora_dir_has_adapter_files(p0):
        return cleaned, None

    tid = (body.training_job_id or "").strip() if body.training_job_id else ""
    if tid:
        req = _training_request_json(ws, tid)
        if req:
            fixed = _adapter_relpath_for_registered_training(ws, req)
            if fixed:
                relf = fixed.strip().replace("\\", "/")
                p1 = abs_lora(relf)
                try:
                    p1.relative_to(ws)
                except ValueError:
                    pass
                else:
                    if _lora_dir_has_adapter_files(p1):
                        rest = cleaned[1:] if len(cleaned) > 1 else []
                        return [relf, *rest], None

    return cleaned, (
        f"LoRA 目录不存在或缺少 adapter_config.json / adapter_model.safetensors: {first}。"
        "请确认训练输出仍在当前工作区内，或刷新合并页后重试。"
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
        self._repo_root = get_settings().repo_root.resolve()
        self._jobs: dict[str, MergeJob] = {}
        self._lock = threading.Lock()

    def _retire_merge_jobs_for_training(self, training_job_id: str) -> None:
        """新建合并前：同一训练下结束进程、清内存并删库内旧记录，只保留即将创建的新任务。"""
        tid = (training_job_id or "").strip()
        if not tid:
            return
        with self._lock:
            to_pop = [
                k
                for k, j in self._jobs.items()
                if str((j.request or {}).get("training_job_id") or "").strip() == tid
            ]
            for k in to_pop:
                j = self._jobs.pop(k, None)
                if j and j.process and j.process.poll() is None:
                    try:
                        j.process.terminate()
                    except Exception:
                        pass
        try:
            conn = get_connection(self._workspace)
            merge_jobs_delete_all_for_training_id(conn, tid)
            conn.commit()
        except Exception:
            logging.getLogger("workshop.merge").exception("合并：按训练清理 merge_jobs 失败")

    def _persist_merge_job(self, job: MergeJob) -> None:
        try:
            conn = get_connection(self._workspace)
            merge_job_persist_upsert(
                conn,
                job_id=job.id,
                status=job.status,
                created_at_s=_created_at_iso(job.created_at),
                finished_at_s=_finished_at_iso(job.finished_at),
                log_path_s=str(job.log_path) if job.log_path else None,
                error_message=job.error_message,
                request=dict(job.request) if job.request else {},
            )
        except Exception:
            logging.getLogger("workshop.merge").exception("合并任务写入 merge_jobs 失败")
            return
        tid = str((job.request or {}).get("training_job_id") or "").strip()
        if not tid:
            return
        with self._lock:
            to_remove = [
                k
                for k, j in self._jobs.items()
                if k != job.id and str((j.request or {}).get("training_job_id") or "").strip() == tid
            ]
            for k in to_remove:
                oj = self._jobs.pop(k, None)
                if oj and oj.process and oj.process.poll() is None:
                    try:
                        oj.process.terminate()
                    except Exception:
                        pass

    def get(self, job_id: str) -> MergeJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[MergeJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def create_job(self, body: MergeJobCreate) -> MergeJob:
        tid_in = (body.training_job_id or "").strip() if body.training_job_id else ""
        if tid_in:
            self._retire_merge_jobs_for_training(tid_in)
        job_id = str(uuid.uuid4())
        out_rel, out_err = _merge_output_relpath(body, job_id)
        if out_err:
            log_dir = _merge_jobs_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{job_id}.log"
            req_dict = body.model_dump()
            job = MergeJob(
                id=job_id,
                status="failed",
                created_at=time.time(),
                finished_at=time.time(),
                log_path=log_path,
                error_message=out_err,
                request=req_dict,
            )
            with self._lock:
                self._jobs[job_id] = job
            self._persist_merge_job(job)
            return job
        lora_paths_eff, lora_err = _resolve_lora_paths_for_merge(self._workspace, body)
        if lora_err:
            log_dir = _merge_jobs_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{job_id}.log"
            req_dict = body.model_dump()
            req_dict["output_path"] = out_rel
            req_dict["lora_paths"] = lora_paths_eff
            job = MergeJob(
                id=job_id,
                status="failed",
                created_at=time.time(),
                finished_at=time.time(),
                log_path=log_path,
                error_message=lora_err,
                request=req_dict,
            )
            with self._lock:
                self._jobs[job_id] = job
            self._persist_merge_job(job)
            return job
        try:
            resolve_under_workspace(self._workspace, out_rel)
        except ValueError as e:
            log_dir = _merge_jobs_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{job_id}.log"
            req_dict = body.model_dump()
            req_dict["output_path"] = out_rel
            req_dict["lora_paths"] = lora_paths_eff
            job = MergeJob(
                id=job_id,
                status="failed",
                created_at=time.time(),
                finished_at=time.time(),
                log_path=log_path,
                error_message=str(e),
                request=req_dict,
            )
            with self._lock:
                self._jobs[job_id] = job
            self._persist_merge_job(job)
            return job
        log_dir = _merge_jobs_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{job_id}.log"
        req_dict = body.model_dump()
        req_dict["output_path"] = out_rel
        req_dict["lora_paths"] = lora_paths_eff
        job = MergeJob(
            id=job_id,
            status="pending",
            created_at=time.time(),
            log_path=log_path,
            request=req_dict,
        )
        script = self._repo_root / "backend" / "scripts" / "workshop_merge.py"
        if not script.is_file():
            job.status = "failed"
            job.error_message = f"未找到合并脚本: {script}"
            job.finished_at = time.time()
            with self._lock:
                self._jobs[job_id] = job
            self._persist_merge_job(job)
            return job
        cmd = [sys.executable, "-u", str(script), "--base", body.base_model_path, "--output", out_rel]
        for p in lora_paths_eff:
            cmd.extend(["--lora", p.replace("\\", "/")])
        cmd.extend(["--merge_lora_only", "true" if body.merge_lora_only else "false"])
        cmd.append("--extra")
        cmd.append(json_dumps([x for x in lora_paths_eff[1:]]))
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = ""
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["WORKSHOP_MERGE_JOB_ID"] = job_id
        tid0 = (body.training_job_id or "").strip() if body.training_job_id else ""
        if tid0:
            env["WORKSHOP_TRAINING_JOB_ID"] = tid0
        with self._lock:
            self._jobs[job_id] = job
        if tid0:
            clear_merged_workshop_output_dir(self._workspace, out_rel)
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
            self._persist_merge_job(job)
            return job
        job.process = proc
        job.status = "running"
        self._persist_merge_job(job)

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
            post_zip_tid: str | None = None
            post_zip_out: str | None = None
            with self._lock:
                j = self._jobs.get(job_id)
                if not j:
                    return
                j.return_code = code
                j.finished_at = time.time()
                if j.status == "cancelled":
                    self._persist_merge_job(j)
                    return
                if code == 0:
                    j.status = "succeeded"
                    req = j.request or {}
                    tid = req.get("training_job_id")
                    post_zip_tid = str(tid).strip() if tid else None
                    op = req.get("output_path")
                    post_zip_out = str(op).strip() if op else None
                else:
                    j.status = "failed"
                    j.error_message = f"进程退出码 {code}"
                self._persist_merge_job(j)
            if code == 0 and post_zip_tid and post_zip_out:
                try:
                    from backend.app.services.merge_export_zip import create_and_record_merged_zip

                    create_and_record_merged_zip(self._workspace, post_zip_tid, post_zip_out)
                except Exception:
                    logging.getLogger("workshop.merge").exception("合并成功后打包 zip 失败")
            if code == 0 and post_zip_out:
                try:
                    cleanup_merged_workshop_dir_after_success(self._workspace, post_zip_out)
                except Exception:
                    _merge_log.exception("合并成功后清理 output/merged-workshop 失败")

        threading.Thread(target=_wait, daemon=True).start()
        return job

    def read_log(self, job_id: str, *, max_bytes: int = 800_000) -> tuple[str, bool]:
        j = self.get(job_id)
        if not j or not j.log_path:
            return "", False
        return read_merge_log_file(j.log_path, max_bytes=max_bytes)

    def latest_job_for_training_id(self, training_job_id: str) -> MergeJob | None:
        tid = (training_job_id or "").strip()
        if not tid:
            return None
        with self._lock:
            candidates: list[MergeJob] = []
            for j in self._jobs.values():
                req = j.request or {}
                if str(req.get("training_job_id") or "").strip() == tid:
                    candidates.append(j)
            if not candidates:
                return None
            return max(candidates, key=lambda x: x.created_at)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j or j.status not in ("pending", "running"):
                return False
            if j.process and j.process.poll() is None:
                j.process.terminate()
            j.status = "cancelled"
            j.finished_at = time.time()
            self._persist_merge_job(j)
            return True


_merge_manager_singleton: MergeJobManager | None = None


def get_merge_manager() -> MergeJobManager:
    """与 HTTP 层共用的合并任务管理器单例（内存态 + 与 /api/merge 一致）。"""
    global _merge_manager_singleton
    if _merge_manager_singleton is None:
        _merge_manager_singleton = MergeJobManager(get_settings().workspace_root.resolve())
    return _merge_manager_singleton
