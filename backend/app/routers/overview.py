from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from fastapi import APIRouter

from backend.app.db import get_connection
from backend.app.deps import WorkspaceRoot

router = APIRouter(tags=["overview"])


def _req_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            o = json.loads(raw)
            return o if isinstance(o, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _train_label(jid: str, req: dict[str, Any]) -> str:
    n = str(req.get("job_name") or "").strip()
    return n if n else f"训练 {jid[:8]}"


def _merge_label(mid: str, req: dict[str, Any]) -> str:
    out = str(req.get("output_path") or "").strip()
    if out:
        parts = [p for p in out.replace("\\", "/").split("/") if p]
        if parts:
            return f"合并 → {parts[-1]}"
    return f"合并 {mid[:8]}"


@router.get("/overview/pipeline")
async def get_pipeline_tree(root: WorkspaceRoot) -> dict[str, Any]:
    """五层：项目 → 批次 → 数据集（版本）→ 训练 → 合并。用于全局预览页横向树。"""
    conn = get_connection(root.resolve())

    b_rows = conn.execute(
        "SELECT id, project_title, batch_name, created_at FROM import_batches ORDER BY created_at"
    ).fetchall()
    v_rows = conn.execute(
        "SELECT id, import_batch_id, name, note, train_relpath, created_at FROM dataset_versions ORDER BY created_at"
    ).fetchall()
    t_rows = conn.execute(
        "SELECT id, status, request_json, created_at FROM training_jobs_persist ORDER BY created_at"
    ).fetchall()
    m_rows = conn.execute("SELECT id, status, request_json, created_at FROM merge_jobs ORDER BY created_at").fetchall()

    versions_by_id = {str(r["id"]): r for r in (v_rows or [])}
    train_relpath_to_vid: dict[str, str] = {}
    for r in v_rows or []:
        tr = str(r["train_relpath"] or "").strip()
        if tr:
            # 同一 relpath 多版本时保留最后遍历的一条
            train_relpath_to_vid[tr] = str(r["id"])

    batch_by_id = {str(r["id"]): r for r in (b_rows or [])}

    merges_by_tid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in m_rows or []:
        mid = str(r["id"])
        reqm = _req_dict(r["request_json"])
        tid = str(reqm.get("training_job_id") or "").strip()
        if not tid:
            continue
        merges_by_tid[tid].append(
            {
                "id": mid,
                "label": _merge_label(mid, reqm),
                "status": str(r["status"] or "").strip() or "unknown",
            }
        )

    for lst in merges_by_tid.values():
        lst.sort(key=lambda x: str(x.get("id") or ""))

    v_to_train: dict[str, list[dict[str, Any]]] = defaultdict(list)
    orphan_batch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    assigned_tid: set[str] = set()

    for r in t_rows or []:
        tid = str(r["id"])
        st = str(r["status"] or "").strip() or "unknown"
        reqt = _req_dict(r["request_json"])
        job = {
            "id": tid,
            "label": _train_label(tid, reqt),
            "status": st,
            "merges": merges_by_tid.get(tid, []),
        }

        vid = str(reqt.get("dataset_version_id") or "").strip()
        if vid and vid in versions_by_id:
            v_to_train[vid].append(job)
            assigned_tid.add(tid)
            continue

        tr = str(reqt.get("train_dataset") or "").strip()
        if tr and tr in train_relpath_to_vid:
            v_to_train[train_relpath_to_vid[tr]].append(job)
            assigned_tid.add(tid)
            continue

        pt = str(reqt.get("project_title") or "").strip()
        bn = str(reqt.get("batch_name") or "").strip()
        bid: str | None = None
        for bid0, brow in batch_by_id.items():
            p0 = str(brow["project_title"] or "").strip()
            b0 = str(brow["batch_name"] or "").strip()
            if p0 == pt and b0 == bn:
                bid = bid0
                break
        if bid:
            orphan_batch[bid].append(job)
        else:
            orphan_batch[""].append(job)
        assigned_tid.add(tid)

    for tid in merges_by_tid:
        if tid in assigned_tid:
            continue
        rows = merges_by_tid[tid]
        job = {
            "id": tid,
            "label": f"训练 {tid[:8]}（无训练记录）",
            "status": "unknown",
            "merges": rows,
        }
        orphan_batch[""].append(job)

    v_by_batch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in v_rows or []:
        bid0 = str(r["import_batch_id"] or "").strip()
        if not bid0:
            continue
        d = {
            "id": str(r["id"]),
            "label": str(r["name"] or "").strip() or str(r["id"])[:8],
            "import_batch_id": bid0,
            "trainings": v_to_train.get(str(r["id"]), []),
        }
        v_by_batch[bid0].append(d)

    for bid0, tlist in orphan_batch.items():
        if not tlist or not bid0:
            continue
        v_by_batch[bid0].append(
            {
                "id": f"__virtual__:{bid0}:no_version",
                "label": "（未建数据集版本）",
                "import_batch_id": bid0,
                "virtual": True,
                "trainings": tlist,
            }
        )

    projects_map: dict[str, dict[str, Any]] = {}
    project_order: list[str] = []

    def _project_key(project_title: str) -> str:
        return project_title if project_title else "未命名项目"

    for r in b_rows or []:
        bid0 = str(r["id"])
        pkey = _project_key(str(r["project_title"] or "").strip())
        if pkey not in projects_map:
            projects_map[pkey] = {
                "key": f"project:{pkey}",
                "label": pkey,
                "batches": [],
            }
            project_order.append(pkey)
        dsets = v_by_batch.get(bid0, [])
        b_label = str(r["batch_name"] or "").strip() or "未命名批次"
        projects_map[pkey]["batches"].append(
            {
                "id": bid0,
                "label": b_label,
                "datasets": dsets,
            }
        )

    if orphan_batch.get(""):
        pkey = "未归属"
        if pkey not in projects_map:
            projects_map[pkey] = {
                "key": f"project:{pkey}",
                "label": pkey,
                "batches": [
                    {
                        "id": "__orphan__",
                        "label": "—",
                        "datasets": [
                            {
                                "id": "__orphan_data__",
                                "label": "（无匹配批次/数据集）",
                                "import_batch_id": "",
                                "virtual": True,
                                "trainings": orphan_batch[""],
                            }
                        ],
                    }
                ],
            }
            project_order.append(pkey)

    projects = [projects_map[k] for k in project_order if k in projects_map]
    for p in projects:
        p["batches"].sort(key=lambda b: str(b.get("id") or ""))
        for b in p["batches"]:
            b["datasets"].sort(key=lambda d: (1 if d.get("virtual") else 0, str(d.get("id") or "")))
            for d in b["datasets"]:
                d["trainings"].sort(key=lambda t: str(t.get("id") or ""))
                for tj in d["trainings"]:
                    tj.get("merges", []).sort(key=lambda m: str(m.get("id") or ""))

    return {"projects": projects}
