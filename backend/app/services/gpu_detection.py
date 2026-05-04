"""NVIDIA GPU 枚举（NVML 优先，回退 PyTorch）。供 system 接口与训练启动等复用。"""

from __future__ import annotations

from typing import Any

# 延迟到首次需要时再 import torch，使 main 里对 torch.cuda 的 filterwarnings 已生效
_torch_mod: object | None = None
_torch_load_failed: bool = False


def get_torch() -> object | None:
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
    t = get_torch()
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


def collect_gpu_devices() -> tuple[list[dict[str, Any]], str | None]:
    """返回 GPU 列表；若为空则附带说明字符串（仅用于展示）。"""
    gpus = _gpu_devices_via_nvml()
    if gpus:
        return gpus, None
    gpus = _gpu_devices_via_torch()
    if gpus:
        return gpus, None
    return [], "未检测到 NVIDIA GPU，或驱动 / NVML 不可用（非 NVIDIA 显卡本接口暂不枚举）。"


def resolve_training_cuda_visible_devices(raw: str | None) -> str:
    """Web/CLI 约定：空串或 auto → 有 GPU 用 0 号卡，否则纯 CPU；cpu/none/- 强制 CPU；其它原样（如 0,1）。"""
    s = (raw or "").strip()
    if not s:
        gpus, _ = collect_gpu_devices()
        return "0" if gpus else ""
    low = s.lower()
    if low in ("cpu", "none", "-"):
        return ""
    if low == "auto":
        gpus, _ = collect_gpu_devices()
        return "0" if gpus else ""
    return s
