from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from backend.app.config import get_settings
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
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "torch": torch_ver,
        "torch_cuda_available": bool(cuda),
        "gpu_note": "若检测到 CUDA 设备，仅作环境探测；本工具内训练/推理主路径在纯 CPU 上执行（与需求一致）。",
    }


@router.get("/system/paths")
async def system_paths() -> dict[str, Any]:
    ws = get_settings().workspace_root.resolve()
    mroot = mscm.modelscope_cache_dir()
    return {
        "workspace_root": str(ws),
        "modelscope_cache": str(mroot),
        "modelscope_hub": str(mscm.modelscope_hub_root()),
        "data_dir": str((ws / "data").resolve()),
        "output_dir": str((ws / "output").resolve()),
    }
