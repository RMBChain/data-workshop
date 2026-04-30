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


@router.get("/overview/pipeline")
async def get_pipeline_tree(root: WorkspaceRoot) -> dict[str, Any]:
    """四层（展示）：项目 → 数据集（版本）→ 训练 → 合并（每训练仅一条合并，优先成功再按时间）。"""
    conn = get_connection(root.resolve())

    imp_rows = conn.execute(
        "SELECT id, project_title, import_label, created_at FROM ls_imports ORDER BY created_at"
    ).fetchall()
    v_rows = conn.execute(
        "SELECT id, ls_import_id, name, note, train_relpath, created_at FROM dataset_versions ORDER BY created_at"
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
            train_relpath_to_vid[tr] = str(r["id"])

    ls_import_by_id = {str(r["id"]): r for r in (imp_rows or [])}

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
    orphan_ls: dict[str, list[dict[str, Any]]] = defaultdict(list)
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

        liid = str(reqt.get("ls_import_id") or "").strip()
        iid: str | None = None
        if liid and liid in ls_import_by_id:
            iid = liid
        else:
            pt = str(reqt.get("project_title") or "").strip()
            ilab = str(reqt.get("import_label") or "").strip()
            if ilab:
                for iid0, brow in ls_import_by_id.items():
                    p0 = str(brow["project_title"] or "").strip()
                    l0 = str(brow["import_label"] or "").strip()
                    if p0 == pt and l0 == ilab:
                        iid = iid0
                        break
            elif pt:
                same_proj = [
                    iid0
                    for iid0, brow in ls_import_by_id.items()
                    if str(brow["project_title"] or "").strip() == pt
                ]
                iid = same_proj[0] if len(same_proj) == 1 else None
        if iid:
            orphan_ls[iid].append(job)
        else:
            orphan_ls[""].append(job)
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
        orphan_ls[""].append(job)

    v_by_ls_import: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in v_rows or []:
        iid0 = str(r["ls_import_id"] or "").strip()
        if not iid0:
            continue
        d = {
            "id": str(r["id"]),
            "label": str(r["name"] or "").strip() or str(r["id"])[:8],
            "ls_import_id": iid0,
            "trainings": v_to_train.get(str(r["id"]), []),
        }
        v_by_ls_import[iid0].append(d)

    for iid0, tlist in orphan_ls.items():
        if not tlist or not iid0:
            continue
        v_by_ls_import[iid0].append(
            {
                "id": f"__virtual__:{iid0}:no_version",
                "label": "（未建数据集版本）",
                "ls_import_id": iid0,
                "virtual": True,
                "trainings": tlist,
            }
        )

    projects_map: dict[str, dict[str, Any]] = {}
    project_order: list[str] = []

    def _project_key(project_title: str) -> str:
        return project_title if project_title else "未命名项目"

    def _ensure_project(pkey: str) -> None:
        if pkey not in projects_map:
            projects_map[pkey] = {
                "key": f"project:{pkey}",
                "label": pkey,
                "datasets": [],
            }
            project_order.append(pkey)

    def _merge_no_version_virtuals(datasets: list[dict[str, Any]], pkey: str) -> list[dict[str, Any]]:
        nv_label = "（未建数据集版本）"
        buckets: list[dict[str, Any]] = []
        rest = []
        for d in datasets:
            if d.get("virtual") and str(d.get("label") or "") == nv_label:
                buckets.append(d)
            else:
                rest.append(d)
        if not buckets:
            return datasets
        trainings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for d in buckets:
            for tj in d.get("trainings") or []:
                tid = str(tj.get("id") or "")
                if tid and tid not in seen:
                    seen.add(tid)
                    trainings.append(tj)
        trainings.sort(key=lambda t: str(t.get("id") or ""))
        safe_pk = pkey.replace(":", "_")[:120]
        rest.append(
            {
                "id": f"__virtual__:{safe_pk}:no_version",
                "label": nv_label,
                "ls_import_id": "",
                "virtual": True,
                "trainings": trainings,
            }
        )
        return rest

    for r in imp_rows or []:
        iid0 = str(r["id"])
        pkey = _project_key(str(r["project_title"] or "").strip())
        _ensure_project(pkey)
        projects_map[pkey]["datasets"].extend(v_by_ls_import.get(iid0, []))

    for r in v_rows or []:
        iid0 = str(r["ls_import_id"] or "").strip()
        if iid0:
            continue
        pkey = "未归属"
        _ensure_project(pkey)
        projects_map[pkey]["datasets"].append(
            {
                "id": str(r["id"]),
                "label": str(r["name"] or "").strip() or str(r["id"])[:8],
                "ls_import_id": "",
                "trainings": v_to_train.get(str(r["id"]), []),
            }
        )

    if orphan_ls.get(""):
        pkey = "未归属"
        _ensure_project(pkey)
        projects_map[pkey]["datasets"].append(
            {
                "id": "__orphan_data__",
                "label": "（无匹配项目/数据集）",
                "ls_import_id": "",
                "virtual": True,
                "trainings": orphan_ls[""],
            }
        )

    projects = [projects_map[k] for k in project_order if k in projects_map]
    for p in projects:
        pkey = str(p.get("label") or "")
        p["datasets"] = _merge_no_version_virtuals(list(p["datasets"]), pkey)
        p["datasets"].sort(key=lambda d: (1 if d.get("virtual") else 0, str(d.get("id") or "")))
        for d in p["datasets"]:
            d["trainings"].sort(key=lambda t: str(t.get("id") or ""))
            for tj in d["trainings"]:
                tj.get("merges", []).sort(key=lambda m: str(m.get("id") or ""))

    return {"projects": projects}
