from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.app.deps import WorkspaceRoot
from backend.app.services.inference_models import list_registered_training_models
from backend.app.services.merge_job_manager import get_merge_manager
from backend.app.services.merge_training_status import training_merge_status_by_job_id

router = APIRouter(tags=["exports"])


@router.get("/exports/artifacts")
async def list_export_artifacts(root: WorkspaceRoot) -> dict[str, Any]:
    """仅列出与「LoRA 合并」页一致的「合并成功」项：数据来自已登记训练 + 合并状态（同 GET /api/merge/training-status 逻辑，非目录通配扫描）。"""
    rows = list_registered_training_models(root)
    by_jid, output_by_jid = training_merge_status_by_job_id(root, rows, get_merge_manager())
    out: list[dict[str, Any]] = []
    for row in rows:
        jid = str(row.get("job_id") or "").strip()
        st = by_jid.get(jid, "none")
        raw_out = output_by_jid.get(jid)
        rel_out = (str(raw_out).strip() if raw_out is not None else "").replace("\\", "/")
        if st != "success" or not rel_out:
            continue
        lora = str(row.get("path") or "").strip().replace("\\", "/")
        raw_label = row.get("label") if row.get("label") is not None else row.get("job_name")
        label = str(raw_label).strip() if raw_label is not None else jid
        out.append(
            {
                "kind": "LoRA 合并",
                "path": rel_out,
                "lora_path": lora,
                "label": label or jid,
                "job_id": jid,
            }
        )
    return {
        "items": out[:200],
        "note": "仅显示工坊内「LoRA 合并」中状态为「合并成功」的合并后模型目录，与合并页数据一致。HF 浮点/合并后权重以 ms-swift 可加载为准。MVP 不引导需要 CUDA 的 bitsandbytes 4-bit 量化。",
    }
