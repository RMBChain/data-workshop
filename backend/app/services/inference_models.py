from __future__ import annotations

from pathlib import Path
from typing import Any


def list_workspace_models(workspace: Path) -> list[dict[str, Any]]:
    """粗扫工作区下可能的 LoRA/合并结果目录，供 UI 下拉里展示。"""
    root = workspace.resolve()
    out: list[dict[str, Any]] = []
    out_dir = root / "output"
    if not out_dir.is_dir():
        return []
    for adapter in out_dir.rglob("adapter_config.json"):
        rel = adapter.parent.relative_to(root)
        out.append(
            {
                "id": f"lora:{rel.as_posix()}",
                "kind": "lora",
                "path": rel.as_posix(),
                "label": f"LoRA · {rel.as_posix()}",
            }
        )
    for cfg in out_dir.rglob("config.json"):
        if "checkpoint" in str(cfg) or "v0-" in str(cfg) or "merged" in str(cfg).lower():
            d = cfg.parent
            if (d / "model.safetensors").is_file() or (d / "adapter_config.json").is_file():
                continue
        rel = cfg.parent.relative_to(root)
        if any(x["path"] == rel.as_posix() for x in out):
            continue
        if rel.parts and rel.parts[0] == "merge-jobs":
            continue
        out.append(
            {
                "id": f"merged:{rel.as_posix()}",
                "kind": "merge",
                "path": rel.as_posix(),
                "label": f"合并/权重 · {rel.as_posix()}",
            }
        )
    return out[:200]
