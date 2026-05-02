from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


def _swift_available() -> bool:
    try:
        import swift  # noqa: F401
    except ImportError:
        return False
    return True


@router.get("/health")
async def health() -> dict[str, str | bool]:
    """存活探测；`ms_swift_available` 为 False 时训练接口仍会启动子进程但会失败（请使用 Docker CPU 镜像）。"""
    return {"status": "ok", "ms_swift_available": _swift_available()}
