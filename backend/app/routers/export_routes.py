from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.app.deps import WorkspaceRoot
from backend.app.services.inference_models import list_workspace_models

router = APIRouter(tags=["exports"])


@router.get("/exports/artifacts")
async def list_export_artifacts(root: WorkspaceRoot) -> dict[str, Any]:
    models = list_workspace_models(root)
    out: list[dict[str, Any]] = [
        {
            "kind": m["kind"],
            "path": m["path"],
            "label": m.get("label"),
        }
        for m in models
    ]
    merged_root = root / "output"
    for p in merged_root.glob("**/workshop_merge_meta.json"):
        out.append(
            {
                "kind": "workshop_merged",
                "path": str(p.parent.relative_to(root)).replace("\\", "/"),
                "label": f"工作区合并产物（含元数据）{p.parent.name}",
            }
        )
    return {
        "items": out[:200],
        "note": "HF 浮点/合并后权重以 ms-swift 可加载为准。MVP 不引导需要 CUDA 的 bitsandbytes 4-bit 量化。",
    }
