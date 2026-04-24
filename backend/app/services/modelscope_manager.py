from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Type

_hub_records_lock = threading.Lock()
_HUB_RECORDS_NAME = "data_workshop_hub_records.json"


def modelscope_cache_dir() -> Path:
    """与 ModelScope `snapshot_download` 使用的缓存根一致：`$MODELSCOPE_CACHE` 或 `~/.cache/modelscope`。"""
    env = (os.environ.get("MODELSCOPE_CACHE") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".cache" / "modelscope").resolve()


def modelscope_hub_root() -> Path:
    return (modelscope_cache_dir() / "hub").resolve()


def _hub_records_path() -> Path:
    return (modelscope_cache_dir() / _HUB_RECORDS_NAME).resolve()


def _load_hub_records() -> dict[str, dict[str, Any]]:
    p = _hub_records_path()
    if not p.is_file():
        return {}
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = v
    return out


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=path.name + "."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_hub_download_success(
    model_id: str,
    local_path: str,
    *,
    files_completed: int,
    total_bytes_expected: int,
) -> None:
    """本应用通过 hub 下载任务成功完成后写入，供列表展示持久化状态与当时体积。"""
    mid = model_id.strip()
    try:
        sz = _dir_size(Path(local_path).resolve())
    except OSError:
        sz = 0
    rec: dict[str, Any] = {
        "status": "completed",
        "size_bytes": int(sz),
        "files_total": int(files_completed),
        "total_bytes_expected": int(total_bytes_expected),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    with _hub_records_lock:
        data = _load_hub_records()
        data[mid] = rec
        _atomic_write_json(_hub_records_path(), data)


def remove_hub_record(model_id: str) -> None:
    mid = model_id.strip()
    with _hub_records_lock:
        data = _load_hub_records()
        if mid not in data:
            return
        del data[mid]
        _atomic_write_json(_hub_records_path(), data)


def record_hub_download_start(model_id: str) -> None:
    """创建下载任务时调用，将持久化状态标为「下载中」。"""
    mid = model_id.strip()
    rec: dict[str, Any] = {
        "status": "downloading",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    with _hub_records_lock:
        data = _load_hub_records()
        data[mid] = rec
        _atomic_write_json(_hub_records_path(), data)


def record_hub_download_failed(model_id: str, err: str) -> None:
    """下载任务失败时写入，便于列表展示「上次下载失败」。"""
    mid = model_id.strip()
    msg = (err or "").strip()[:1000]
    rec: dict[str, Any] = {
        "status": "failed",
        "error": msg,
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }
    with _hub_records_lock:
        data = _load_hub_records()
        data[mid] = rec
        _atomic_write_json(_hub_records_path(), data)


def prune_stale_downloading_records(active_model_ids: set[str]) -> None:
    """
    若持久化中仍为 ``downloading`` 但当前无运行中的任务（例如进程结束、未收到失败回调），
    将状态改为 **interrupted**，避免列表误显示为「无记录的已缓存」。
    """
    with _hub_records_lock:
        data = _load_hub_records()
        changed = False
        for mid, rec in list(data.items()):
            if rec.get("status") == "downloading" and mid not in active_model_ids:
                started = rec.get("started_at")
                out: dict[str, Any] = {
                    "status": "interrupted",
                    "interrupted_at": datetime.now(timezone.utc).isoformat(),
                }
                if isinstance(started, str) and started.strip():
                    out["started_at"] = started.strip()
                data[mid] = out
                changed = True
        if changed:
            _atomic_write_json(_hub_records_path(), data)


def _dir_size(p: Path) -> int:
    """
    统计目录下普通文件总字节数。
    ModelScope 可能将 ``hub/models/作者/名称`` 设为指向实际缓存目录的符号链接；
    ``Path.rglob`` 默认不进入此类目录链接，会导致体积严重偏小（例如仅数 MB）。
    """
    if not p.exists():
        return 0
    try:
        base = p.resolve()
    except OSError:
        base = p
    if not base.is_dir():
        return 0
    n = 0
    for dirpath, _dirnames, filenames in os.walk(base, followlinks=True):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                n += os.path.getsize(fp)
            except OSError:
                pass
    return n


def list_hub_models() -> list[dict[str, Any]]:
    """
    列出 ModelScope 本机 hub 中已缓存的 `models/作者/名称` 目录（与 ModelScope 下载布局一致）。
    若曾下载**成功**（``status==completed``），**size_bytes** 用记录中的完成时体积，避免整目录统计；
    其他状态（含 **interrupted**、**下载中**、**失败** 或无记录）则对目录做 ``_dir_size`` 实时统计。
    """
    mdir = modelscope_hub_root() / "models"
    if not mdir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    with _hub_records_lock:
        records = _load_hub_records()
    for a in sorted(mdir.iterdir(), key=lambda x: x.name.lower()):
        if not a.is_dir() or a.name.startswith("."):
            continue
        for b in sorted(a.iterdir(), key=lambda x: x.name.lower()):
            if b.is_dir() and not b.name.startswith("."):
                mid = f"{a.name}/{b.name}"
                rec = records.get(mid)
                st = (rec or {}).get("status")
                if st == "completed" and rec and "size_bytes" in rec:
                    try:
                        pz = int(rec["size_bytes"])
                        size_val = pz if pz >= 0 else _dir_size(b)
                    except (TypeError, ValueError):
                        size_val = _dir_size(b)
                else:
                    # 下载中 / 失败 / 无记录：使用当前目录实时体积
                    size_val = _dir_size(b)
                row: dict[str, str | int | dict[str, Any]] = {
                    "model_id": mid,
                    "path": f"models/{mid}",
                    "size_bytes": size_val,
                }
                if rec is not None:
                    row["download_record"] = rec
                out.append(row)
    return out


def _resolve_model_dir(model_id: str) -> Path:
    mid = model_id.strip().replace("\\", "/").lstrip("/")
    if ".." in mid or mid.startswith(("/", ".")):
        raise ValueError("非法 model_id")
    parts = mid.split("/")
    if len(parts) == 2:
        d = modelscope_hub_root() / "models" / parts[0] / parts[1]
    elif len(parts) == 3 and parts[0] == "models":
        d = modelscope_hub_root() / parts[0] / parts[1] / parts[2]
    else:
        raise ValueError("model_id 须为 作者/名称，例如 Qwen/Qwen3-VL-2B-Instruct")
    d = d.resolve()
    root = modelscope_hub_root()
    d.relative_to(root)
    if not d.is_dir():
        raise FileNotFoundError(str(d))
    return d


def hub_model_dir_if_cached(model_id: str) -> Path | None:
    """若 `model_id` 在魔搭本机已有完整目录则返回绝对路径，否则 None（不触发下载）。"""
    try:
        return _resolve_model_dir(model_id)
    except (ValueError, FileNotFoundError, OSError):
        pass
    mid = model_id.strip().replace("\\", "/").lstrip("/")
    parts = mid.split("/")
    if len(parts) != 2 or ".." in parts or not parts[0] or not parts[1]:
        return None
    root = modelscope_cache_dir().resolve()
    legacy = (root / "models" / parts[0] / parts[1]).resolve()
    try:
        legacy.relative_to(root)
    except ValueError:
        return None
    if legacy.is_dir():
        return legacy
    return None


def delete_hub_model(model_id: str) -> None:
    d = _resolve_model_dir(model_id)
    shutil.rmtree(d, ignore_errors=False)
    remove_hub_record(model_id)


def estimate_hub_model_download_totals(model_id: str) -> tuple[int, int]:
    """
    调用魔搭 API 列出当前默认 revision 下全部文件，汇总声明的 Size 与文件数，
    用作下载整体进度分母（与 snapshot_download 拉取的文件集合一致）。
    失败时返回 (0, 0)，由上层改用按文件数估算或降级显示。
    """
    try:
        from modelscope.hub.api import HubApi
        from modelscope.utils.constant import REPO_TYPE_MODEL
    except ImportError:
        return 0, 0

    mid = model_id.strip()
    try:
        api = HubApi(token=None)
        endpoint = api.get_endpoint_for_read(
            repo_id=mid, repo_type=REPO_TYPE_MODEL, token=None
        )
        cookies = api.get_cookies()
        rd = api.get_valid_revision_detail(
            mid, revision=None, cookies=cookies, endpoint=endpoint
        )
        revision = rd["Revision"]
        files = api.get_model_files(
            model_id=mid,
            revision=revision,
            root=None,
            recursive=True,
            use_cookies=cookies,
            headers=None,
            endpoint=endpoint,
        )
    except Exception:
        return 0, 0

    total = 0
    for f in files:
        sz = f.get("Size")
        if sz is None:
            continue
        try:
            total += int(sz)
        except (TypeError, ValueError):
            pass
    return total, len(files)


def download_snapshot(
    model_id: str,
    *,
    progress_callbacks: list[Type[Any]] | None = None,
) -> str:
    from modelscope import snapshot_download

    kwargs: dict[str, Any] = {}
    if progress_callbacks:
        kwargs["progress_callbacks"] = progress_callbacks
    p = snapshot_download(model_id, **kwargs)
    return str(p)
