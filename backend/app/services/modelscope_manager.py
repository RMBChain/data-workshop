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

# 与 [HF-Mirror](https://hf-mirror.com) 说明一致，供前端选择「从镜像下载」时写入
HF_MIRROR_ENDPOINT = "https://hf-mirror.com"


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


def _hub_model_dir_looks_complete(model_dir: Path) -> bool:
    """
    判断是否像已就绪的 Transformers/GGUF 本机缓存（用于修复「目录已有完整文件但持久化仍为失败/中断」）。
    保守：略过体积极小的目录，且需存在常见入口文件之一。
    """
    try:
        base = model_dir.resolve()
    except OSError:
        return False
    if not base.is_dir():
        return False
    sz = _dir_size(base)
    if sz < 256 * 1024:
        return False
    marker_names = (
        "config.json",
        "configuration.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "model_index.json",
    )
    for name in marker_names:
        p = base / name
        if p.is_file():
            return True
    for dirpath, _dirnames, filenames in os.walk(str(base), followlinks=True):
        for fn in filenames:
            lower = fn.lower()
            if lower.endswith(".safetensors"):
                return True
            if lower.endswith(".gguf"):
                return True
            if lower.endswith(".bin") and "model" in lower:
                return True
            if fn == "pytorch_model.bin":
                return True
    return False


def heal_hub_records_if_cached_model_complete(active_model_ids: set[str]) -> None:
    """
    本机 ``hub/models`` 目录已可用，但 JSON 仍为 failed/interrupted 时，
    补写为 completed（避免训练页 ``readyHubModels`` 为空而误拦）。
    """
    mdir = modelscope_hub_root() / "models"
    if not mdir.is_dir():
        return
    records = _load_hub_records()
    to_heal: list[tuple[str, Path]] = []
    for a in sorted(mdir.iterdir(), key=lambda x: x.name.lower()):
        if not a.is_dir() or a.name.startswith("."):
            continue
        for b in sorted(a.iterdir(), key=lambda x: x.name.lower()):
            if not (b.is_dir() and not b.name.startswith(".")):
                continue
            mid = f"{a.name}/{b.name}"
            rec = records.get(mid)
            if rec is None:
                continue
            if mid in active_model_ids:
                continue
            st = rec.get("status")
            if st == "completed":
                continue
            if st not in ("failed", "interrupted", "downloading"):
                continue
            if _hub_model_dir_looks_complete(b):
                try:
                    to_heal.append((mid, b.resolve()))
                except OSError:
                    pass
    for mid, path in to_heal:
        try:
            record_hub_download_success(
                mid,
                str(path),
                files_completed=int((records.get(mid) or {}).get("files_total") or 0),
                total_bytes_expected=int((records.get(mid) or {}).get("total_bytes_expected") or 0),
            )
        except Exception:
            pass


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


def swift_model_arg_if_hub_cached(model: str) -> str:
    """
    将「数据工坊-模型管理」中已缓存在本机 hub 的 ``作者/名称`` 解析为**目录绝对路径**。

    ms-swift 在收到 id 形参时会先走 ModelScope Hub；从 Hugging Face 下载的模型仅本地存在、魔搭上可能无此仓库。
    传本地路径可走 ``from_pretrained(本地)``，避免误连 ModelScope。
    """
    m = (model or "").strip()
    if not m or ".." in m or "://" in m:
        return m
    p = Path(m)
    if p.is_dir():
        return str(p.resolve())
    if m.count("/") != 1:
        return m
    if m.startswith(("/", ".", "\\")):
        return m
    if len(m) >= 2 and m[1] == ":":
        return m
    cached = hub_model_dir_if_cached(m)
    if cached is not None:
        return str(cached.resolve())
    return m


def _infer_ms_swift_model_type_from_dirname(dirname: str) -> str | None:
    s = (dirname or "").lower()
    if "qwen3" in s and "vl" in s:
        return "qwen3_vl"
    if "qwen2.5" in s and "vl" in s:
        return "qwen2_5_vl"
    if "qwen2" in s and "vl" in s:
        return "qwen2_vl"
    return None


def infer_ms_swift_model_type_from_hub_dir(model_arg: str) -> str | None:
    """
    本机模型目录下，为 ms-swift 推断 ``--model_type``（与 swift.model.models 中注册名一致）。

    仅传本地路径时 swift 常无法从 id 反查，会报 *Multiple possible types*，需显式指定；
    这里读取 ``config.json`` 的 ``architectures``，若无则根据目录名作弱启发。
    """
    p = Path(model_arg)
    if not p.is_dir():
        return None
    cfg = p / "config.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return _infer_ms_swift_model_type_from_dirname(p.name)
        archs = data.get("architectures")
        if not isinstance(archs, list):
            mt = data.get("model_type")
            if isinstance(mt, str) and mt.strip():
                return mt.strip()
            return _infer_ms_swift_model_type_from_dirname(p.name)
        a = ""
        for x in archs:
            if isinstance(x, str) and x:
                a = x
                break
        if "Qwen3VL" in a or "Qwen3_VL" in a:
            return "qwen3_vl"
        if "Qwen2_5_VL" in a or "Qwen2.5" in a:
            return "qwen2_5_vl"
        if "Qwen2VL" in a or "Qwen2_VL" in a:
            return "qwen2_vl"
    return _infer_ms_swift_model_type_from_dirname(p.name)


# ms-swift 的 --model_type 与 Transformers 根 config.json 的 model_type 一致（见官方 Qwen3-VL config）
_SWIFT_TO_HF_CONFIG_MODEL_TYPE: dict[str, str] = {
    "qwen3_vl": "qwen3_vl",
    "qwen2_vl": "qwen2_vl",
    "qwen2_5_vl": "qwen2_5_vl",
}


def ensure_config_json_hf_model_type(model_dir: str | Path, swift_model_type: str) -> bool:
    """
    Transformers ``AutoConfig.from_pretrained(本地目录)`` 要求根 ``config.json`` 含可识别的顶层 ``model_type``；
    部分魔搭/衍生权重只有 ``architectures``、或 ``model_type`` 为衍生串（如 CPRT 变体），会报 *Unrecognized model*。

    - 对 ``_SWIFT_TO_HF_CONFIG_MODEL_TYPE`` 中列出的架构：若缺失、为空或与标准 HF 名不一致，则**对齐**为对应值
      （因 Transformers 只认 Qwen3-VL 等标准 ``model_type`` 字符串）。
    - 其他 ``swift_model_type``：仅在缺失或空白时补写，避免覆盖真·自定义类。
    """
    p = Path(model_dir)
    if not p.is_dir():
        return False
    st = (swift_model_type or "").strip()
    if not st:
        return False
    cfg_path = p / "config.json"
    if not cfg_path.is_file():
        return False
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    hf_type = _SWIFT_TO_HF_CONFIG_MODEL_TYPE.get(st, st)
    raw = data.get("model_type")
    existing = raw.strip() if isinstance(raw, str) else ""
    if st in _SWIFT_TO_HF_CONFIG_MODEL_TYPE:
        if existing == hf_type:
            return False
    else:
        if existing:
            return False
    data["model_type"] = hf_type
    try:
        _atomic_write_json(cfg_path, data)
    except OSError:
        return False
    print(f"提示: 已写入 {cfg_path.name} 顶层 model_type={hf_type!r}（Transformers 加载所需）")
    return True


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


def hf_hub_endpoint_effective() -> str | None:
    """
    Hugging Face 下载的 Hub 根 URL 仅由环境变量 ``HF_ENDPOINT`` 提供（与 huggingface_hub 一致）；
    未设置时为本函数返回 None，请求走官方默认端点；不设其它配置项作为替代。
    """
    v = os.environ.get("HF_ENDPOINT")
    if v and str(v).strip():
        return str(v).strip().rstrip("/")
    return None


def hf_hub_endpoint_host_for_display() -> str | None:
    """供前端展示当前使用的 Hub 主机名；未配置自定义端点时返回 None。"""
    ep = hf_hub_endpoint_effective()
    if not ep:
        return None
    from urllib.parse import urlparse

    u = urlparse(ep)
    if u.netloc:
        return u.netloc
    return ep


def _hf_api_for(hf_endpoint: str | None):
    from huggingface_hub import HfApi

    if hf_endpoint:
        return HfApi(endpoint=hf_endpoint)
    return HfApi()


def estimate_hf_download_totals(
    model_id: str, *, hf_endpoint: str | None
) -> tuple[int, int]:
    """
    通过 Hugging Face API 统计默认 revision 下可下载文件体积与文件数，供进度分母。
    """
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        return 0, 0
    mid = model_id.strip()
    try:
        api = _hf_api_for(hf_endpoint)
        info = api.model_info(mid, files_metadata=True)
    except Exception:
        return 0, 0
    total = 0
    n = 0
    for s in info.siblings or []:
        rfn = s.rfilename
        if not rfn or rfn.endswith("/"):
            continue
        n += 1
        if s.size is not None:
            try:
                total += int(s.size)
            except (TypeError, ValueError):
                pass
    return total, n


def download_hf_snapshot(
    model_id: str,
    *,
    progress_callbacks: list[Type[Any]] | None = None,
    hf_endpoint: str | None = None,
) -> str:
    """
    从 Hugging Face 拉取到与 ModelScope 相同的本机相对布局：
    ``hub/models/作者/名称``，便于与现有「模型管理」列表与基座 id 一致。
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as e:
        raise RuntimeError("未安装 huggingface_hub，无法从 Hugging Face 下载") from e

    hf_ep = hf_endpoint

    mid = model_id.strip().replace("\\", "/")
    if ".." in mid or mid.startswith(("/", ".")):
        raise ValueError("非法 model_id")
    parts = mid.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("HF 模型 id 须为 作者/名称，例如 Qwen/Qwen3-VL-2B-Instruct")
    out_dir = (modelscope_hub_root() / "models" / parts[0] / parts[1]).resolve()
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    api = _hf_api_for(hf_ep)
    info = api.model_info(mid, files_metadata=True)
    progress_cls: Type[Any] | None = None
    if progress_callbacks and len(progress_callbacks) > 0:
        progress_cls = progress_callbacks[0]
    for s in info.siblings or []:
        rfn = s.rfilename
        if not rfn or rfn.endswith("/"):
            continue
        size = int(s.size or 0)
        cb = None
        if progress_cls is not None:
            cb = progress_cls(rfn, size)
        dl_kw: dict[str, Any] = {
            "repo_id": mid,
            "filename": rfn,
            "local_dir": out_dir,
            "resume_download": True,
        }
        if hf_ep:
            dl_kw["endpoint"] = hf_ep
        hf_hub_download(**dl_kw)
        if cb is not None:
            if size:
                cb.update(size)
            cb.end()
    return str(out_dir)
