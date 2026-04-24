from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Type


def modelscope_cache_dir() -> Path:
    """与 ModelScope `snapshot_download` 使用的缓存根一致：`$MODELSCOPE_CACHE` 或 `~/.cache/modelscope`。"""
    env = (os.environ.get("MODELSCOPE_CACHE") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".cache" / "modelscope").resolve()


def modelscope_hub_root() -> Path:
    return (modelscope_cache_dir() / "hub").resolve()


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


def list_hub_models() -> list[dict[str, str | int]]:
    """
    列出 ModelScope 本机 hub 中已缓存的 `models/作者/名称` 目录（与 ModelScope 下载布局一致）。
    """
    mdir = modelscope_hub_root() / "models"
    if not mdir.is_dir():
        return []
    out: list[dict[str, str | int]] = []
    for a in sorted(mdir.iterdir(), key=lambda x: x.name.lower()):
        if not a.is_dir() or a.name.startswith("."):
            continue
        for b in sorted(a.iterdir(), key=lambda x: x.name.lower()):
            if b.is_dir() and not b.name.startswith("."):
                mid = f"{a.name}/{b.name}"
                out.append(
                    {
                        "model_id": mid,
                        "path": f"models/{mid}",
                        "size_bytes": _dir_size(b),
                    }
                )
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
