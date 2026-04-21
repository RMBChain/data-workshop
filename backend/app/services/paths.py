from __future__ import annotations

from pathlib import Path


def resolve_under_workspace(workspace: Path, relative: str) -> Path:
    """将相对路径解析为 workspace 下的绝对路径，并防止越界。"""
    root = workspace.resolve()
    rel = relative.strip().replace("\\", "/").lstrip("/")
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise ValueError(f"路径不允许超出工作区: {relative}") from e
    return target
