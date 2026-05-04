from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import APIRouter

from backend.app.deps import WorkspaceRoot
from backend.app.services import modelscope_manager as mscm
from backend.app.services.gpu_detection import collect_gpu_devices, get_torch

router = APIRouter(tags=["system"])


def _gpu_memory_usage_via_nvml() -> list[dict[str, Any]]:
    try:
        import pynvml
    except ImportError:
        return []
    try:
        pynvml.nvmlInit()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    try:
        n = int(pynvml.nvmlDeviceGetCount())
        for i in range(n):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            raw = pynvml.nvmlDeviceGetName(h)
            name = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            out.append(
                {
                    "index": i,
                    "name": name.strip(),
                    "used_bytes": int(mem.used),
                    "total_bytes": int(mem.total),
                }
            )
    except Exception:
        return []
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
    return out


def _gpu_memory_usage_via_torch() -> list[dict[str, Any]]:
    t = get_torch()
    if t is None:
        return []
    try:
        if not bool(t.cuda.is_available()):  # type: ignore[union-attr]
            return []
        n = int(t.cuda.device_count())  # type: ignore[union-attr]
    except Exception:
        return []
    mem_get = getattr(t.cuda, "mem_get_info", None)
    if not callable(mem_get):
        return []
    out: list[dict[str, Any]] = []
    for i in range(n):
        try:
            free_b, total_b = mem_get(i)  # type: ignore[misc]
            total_i = int(total_b)
            used_i = max(0, total_i - int(free_b))
            p = t.cuda.get_device_properties(i)  # type: ignore[union-attr]
            name = str(getattr(p, "name", f"cuda:{i}")).strip()
            out.append({"index": i, "name": name, "used_bytes": used_i, "total_bytes": total_i})
        except Exception:
            continue
    return out


def _gpu_memory_usage_snapshot() -> list[dict[str, Any]]:
    gpus = _gpu_memory_usage_via_nvml()
    if gpus:
        return gpus
    return _gpu_memory_usage_via_torch()


def _collect_static_hardware() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        import psutil
    except ImportError:
        out["hardware_note"] = "psutil 未安装，无法读取 CPU / 内存硬件信息。"
        gpus, note = collect_gpu_devices()
        out["gpus"] = gpus
        if note:
            out["gpu_list_note"] = note
        return out

    phys = psutil.cpu_count(logical=False)
    logical = psutil.cpu_count(logical=True)
    out["cpu_physical_cores"] = phys
    out["cpu_logical_threads"] = logical
    try:
        cf = psutil.cpu_freq()
    except Exception:
        cf = None
    if cf is not None:
        cur = getattr(cf, "current", None)
        mn = getattr(cf, "min", None)
        mx = getattr(cf, "max", None)
        out["cpu_freq_mhz"] = {
            "current": float(cur) if cur is not None else None,
            "min": float(mn) if mn is not None else None,
            "max": float(mx) if mx is not None else None,
        }
    else:
        out["cpu_freq_mhz"] = None

    try:
        out["memory_total_bytes"] = int(psutil.virtual_memory().total)
    except Exception:
        out["memory_total_bytes"] = None

    gpus, note = collect_gpu_devices()
    out["gpus"] = gpus
    if note:
        out["gpu_list_note"] = note
    return out


@router.get("/system/resources")
async def system_resources() -> dict[str, Any]:
    try:
        import psutil
    except ImportError:
        return {
            "cpu_percent": 0.0,
            "memory": {"used_bytes": 0, "total_bytes": 0, "percent": 0.0},
            "note": "psutil 未安装，请在后端环境安装 `psutil` 以显示资源占用。",
        }
    m = psutil.virtual_memory()
    out: dict[str, Any] = {
        "cpu_percent": float(psutil.cpu_percent(interval=0.1)),
        "memory": {
            "used_bytes": int(m.used),
            "total_bytes": int(m.total),
            "percent": float(m.percent),
        },
    }
    gpu_devs = _gpu_memory_usage_snapshot()
    if gpu_devs:
        used_sum = sum(int(d.get("used_bytes") or 0) for d in gpu_devs)
        total_sum = sum(int(d.get("total_bytes") or 0) for d in gpu_devs)
        pct = float(100.0 * used_sum / total_sum) if total_sum > 0 else 0.0
        out["gpu_memory"] = {
            "used_bytes": used_sum,
            "total_bytes": total_sum,
            "percent": pct,
            "devices": gpu_devs,
        }
    return out


@router.get("/system/info")
async def system_info() -> dict[str, Any]:
    torch_ver = "未安装"
    cuda = False
    t = get_torch()
    if t is not None:
        torch_ver = str(getattr(t, "__version__", ""))
        try:
            cuda = bool(t.cuda.is_available())  # type: ignore[union-attr]
        except Exception:
            cuda = False
    base: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "torch": torch_ver,
        "torch_cuda_available": bool(cuda),
        "gpu_note": "CUDA / GPU 信息来自当前运行环境的 PyTorch 与驱动探测，供部署与健康检查参考。",
    }
    base.update(_collect_static_hardware())
    return base


@router.get("/system/paths")
async def system_paths(ws: WorkspaceRoot) -> dict[str, Any]:
    mroot = mscm.modelscope_cache_dir()
    return {
        "workspace_root": str(ws),
        "modelscope_cache": str(mroot),
        "modelscope_hub": str(mscm.modelscope_hub_root()),
        "data_dir": str((ws / "data").resolve()),
        "output_dir": str((ws / "output").resolve()),
    }
