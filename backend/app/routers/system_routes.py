from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from backend.app.config import get_settings

router = APIRouter(tags=["system"])

try:
    import torch
except Exception:  # 某些最小环境
    torch = None  # type: ignore[assignment]


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
    if torch is not None:
        torch_ver = str(torch.__version__)
        try:
            cuda = bool(torch.cuda.is_available())
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
    home = Path.home()
    ws = get_settings().workspace_root.resolve()
    return {
        "workspace_root": str(ws),
        "modelscope_cache": str((home / ".cache" / "modelscope").resolve()),
        "data_dir": str((ws / "data").resolve()),
        "output_dir": str((ws / "output").resolve()),
    }
