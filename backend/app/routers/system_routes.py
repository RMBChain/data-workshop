from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import APIRouter

from backend.app.deps import WorkspaceRoot
from backend.app.services import modelscope_manager as mscm

router = APIRouter(tags=["system"])

# 延迟到首次需要时再 import torch，使 main 里对 torch.cuda 的 filterwarnings 已生效
_torch_mod: object | None = None
_torch_load_failed: bool = False


def _get_torch() -> object | None:
    global _torch_mod, _torch_load_failed
    if _torch_load_failed:
        return None
    if _torch_mod is not None:
        return _torch_mod
    try:
        import torch as _t

        _torch_mod = _t
    except Exception:
        _torch_load_failed = True
        _torch_mod = None
    return _torch_mod


def _gpu_devices_via_nvml() -> list[dict[str, Any]]:
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
            out.append({"index": i, "name": name.strip(), "memory_total_bytes": int(mem.total)})
    except Exception:
        return []
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
    return out


def _gpu_devices_via_torch() -> list[dict[str, Any]]:
    t = _get_torch()
    if t is None:
        return []
    try:
        if not bool(t.cuda.is_available()):  # type: ignore[union-attr]
            return []
        n = int(t.cuda.device_count())  # type: ignore[union-attr]
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for i in range(n):
        try:
            p = t.cuda.get_device_properties(i)  # type: ignore[union-attr]
            out.append(
                {
                    "index": i,
                    "name": str(getattr(p, "name", f"cuda:{i}")).strip(),
                    "memory_total_bytes": int(getattr(p, "total_memory", 0)),
                }
            )
        except Exception:
            continue
    return out


def _collect_gpu_devices() -> tuple[list[dict[str, Any]], str | None]:
    gpus = _gpu_devices_via_nvml()
    if gpus:
        return gpus, None
    gpus = _gpu_devices_via_torch()
    if gpus:
        return gpus, None
    return [], "未检测到 NVIDIA GPU，或驱动 / NVML 不可用（非 NVIDIA 显卡本接口暂不枚举）。"


def _collect_static_hardware() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        import psutil
    except ImportError:
        out["hardware_note"] = "psutil 未安装，无法读取 CPU / 内存硬件信息。"
        gpus, note = _collect_gpu_devices()
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

    gpus, note = _collect_gpu_devices()
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
    return {
        "cpu_percent": float(psutil.cpu_percent(interval=0.1)),
        "memory": {
            "used_bytes": int(m.used),
            "total_bytes": int(m.total),
            "percent": float(m.percent),
        },
    }


@router.get("/system/info")
async def system_info() -> dict[str, Any]:
    torch_ver = "未安装"
    cuda = False
    t = _get_torch()
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
        "gpu_note": "若检测到 CUDA 设备，仅作环境探测；本工具内训练/推理主路径在纯 CPU 上执行（与需求一致）。",
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
