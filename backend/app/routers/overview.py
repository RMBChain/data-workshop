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


def _created_at_ts(raw: Any) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _pick_single_merge_node(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每个训练在全景树中只展示一个合并：优先成功态，否则取创建时间最新的一条（忽略内部排序字段）。"""
    if not candidates:
        return []
    succ = [m for m in candidates if str(m.get("status") or "").strip().lower() == "succeeded"]
    pool = succ if succ else candidates
    best = max(pool, key=lambda m: float(m.get("_ts", 0) or 0.0))
    return [{"id": best["id"], "label": best["label"], "status": best["status"]}]


def _project_key(project_title: str) -> str:
    return project_title if project_title else "未命名项目"


@router.get("/overview/pipeline")
async def get_pipeline_tree(root: WorkspaceRoot) -> dict[str, Any]:
    """四层（展示）：项目 → 数据集（版本）→ 训练 → 合并（每训练仅一条合并，优先成功再按时间）。"""
    conn = get_connection(root.resolve())

    v_rows = conn.execute(
        "SELECT id, label_studio_project_title, name, train_relpath, created_at "
        "FROM dws_datasets WHERE status = 'succeeded' ORDER BY created_at"
    ).fetchall()
    t_rows = conn.execute(
        "SELECT id, status, request_json, created_at FROM dws_training_jobs_persist ORDER BY created_at"
    ).fetchall()
    m_rows = conn.execute("SELECT id, status, request_json, created_at FROM dws_merge_jobs ORDER BY created_at").fetchall()

    versions_by_id = {str(r["id"]): r for r in (v_rows or [])}
    train_relpath_to_vid: dict[str, str] = {}
    for r in v_rows or []:
        tr = str(r["train_relpath"] or "").strip()
        if tr:
            train_relpath_to_vid[tr] = str(r["id"])

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
                "_ts": _created_at_ts(r["created_at"]),
            }
        )

    merges_by_tid = {tid: _pick_single_merge_node(lst) for tid, lst in merges_by_tid.items()}

    v_to_train: dict[str, list[dict[str, Any]]] = defaultdict(list)
    orphan_trainings: list[dict[str, Any]] = []
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

        orphan_trainings.append(job)
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
        orphan_trainings.append(job)

    projects_map: dict[str, dict[str, Any]] = {}
    project_order: list[str] = []

    def _ensure_project(pkey: str) -> None:
        if pkey not in projects_map:
            projects_map[pkey] = {
                "key": f"project:{pkey}",
                "label": pkey,
                "datasets": [],
            }
            project_order.append(pkey)

    for r in v_rows or []:
        title = str(r["label_studio_project_title"] or "").strip()
        pkey = _project_key(title) if title else "未归属"
        _ensure_project(pkey)
        vid = str(r["id"])
        projects_map[pkey]["datasets"].append(
            {
                "id": vid,
                "label": str(r["name"] or "").strip() or vid[:8],
                "trainings": v_to_train.get(vid, []),
            }
        )

    if orphan_trainings:
        pkey = "未归属"
        _ensure_project(pkey)
        projects_map[pkey]["datasets"].append(
            {
                "id": "__orphan_data__",
                "label": "（无匹配数据集版本）",
                "virtual": True,
                "trainings": orphan_trainings,
            }
        )

    projects = [projects_map[k] for k in project_order if k in projects_map]
    for p in projects:
        p["datasets"].sort(key=lambda d: (1 if d.get("virtual") else 0, str(d.get("id") or "")))
        for d in p["datasets"]:
            d["trainings"].sort(key=lambda t: str(t.get("id") or ""))
            for tj in d["trainings"]:
                tj.get("merges", []).sort(key=lambda m: str(m.get("id") or ""))

    return {"projects": projects}
