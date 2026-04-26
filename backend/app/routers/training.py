from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from backend.app.deps import WorkspaceRoot, get_workspace_root
from backend.app.db import get_connection
from backend.app.services import modelscope_manager as mscm
from backend.app.services.job_manager import TrainJobCreate, TrainingJobManager, _latest_checkpoint_relpath
from backend.app.services.training_metrics import (
    build_training_stages,
    parse_training_log_metrics,
    parse_training_progress,
)

router = APIRouter(tags=["training"])

_MAX_SAVED_FORM_PARAMS_BYTES = 400_000

_manager: TrainingJobManager | None = None


def _manager_singleton() -> TrainingJobManager:
    global _manager
    if _manager is None:
        _manager = TrainingJobManager(get_workspace_root())
    return _manager


def _display_names_for_job_request(workspace: Path, req: dict[str, Any]) -> tuple[str, str, str]:
    """合并请求中携带的展示字段与按 train_relpath 在库中的回退（旧任务、或无展示字段时）。"""

    def _pick(stored: str, fallback: str) -> str:
        s = (stored or "").strip()
        if s:
            return s
        f = (fallback or "").strip()
        return f or "—"

    s_pt = str(req.get("project_title") or "")
    s_bn = str(req.get("batch_name") or "")
    s_dn = str(req.get("dataset_name") or "")
    tr = str(req.get("train_dataset") or "").strip()
    d_pt, d_bn, d_dn = "", "", ""
    if tr:
        try:
            conn = get_connection(workspace)
            row = conn.execute(
                "SELECT v.name, b.project_title, b.batch_name "
                "FROM dataset_versions v "
                "LEFT JOIN import_batches b ON b.id = v.import_batch_id "
                "WHERE v.train_relpath = ? "
                "ORDER BY v.created_at DESC "
                "LIMIT 1",
                (tr,),
            ).fetchone()
            if row:
                d_dn, d_pt, d_bn = (row[0] or ""), (row[1] or ""), (row[2] or "")
        except Exception:
            pass
    return _pick(s_pt, d_pt), _pick(s_bn, d_bn), _pick(s_dn, d_dn)


def _job_name_from_request(req: dict[str, Any]) -> str:
    n = str(req.get("job_name") or "").strip()
    return n if n else "—"


def _output_dir_from_request(req: dict[str, Any]) -> str:
    """训练产物目录（相对工作区根），创建任务时由后端解析并写入 request_json。"""
    od = req.get("output_dir")
    if isinstance(od, str) and od.strip():
        return od.strip().replace("\\", "/")
    return "—"


def _require_model_downloaded_in_hub(model: str) -> None:
    """基础模型仅允许在魔搭本机 hub 中已存在的缓存（与「模型管理」列表一致）。"""
    mid = (model or "").strip()
    if not mid:
        raise HTTPException(status_code=400, detail="未指定基础模型")
    known = {str(m.get("model_id", "")) for m in mscm.list_hub_models()}
    if mid not in known:
        raise HTTPException(
            status_code=400,
            detail="请先在「模型管理」中成功下载该模型；基础模型仅可选择本机已缓存的 model_id。",
        )


@router.get("/training/jobs")
async def list_training_jobs(root: WorkspaceRoot) -> dict:
    jobs = _manager_singleton().list_jobs()
    result = []
    for j in jobs:
        req = j.request or {}
        pt, bn, dn = _display_names_for_job_request(root, req)
        result.append(
            {
                "id": j.id,
                "status": j.status,
                "created_at": j.created_at,
                "finished_at": j.finished_at,
                "return_code": j.return_code,
                "error_message": j.error_message,
                "job_name": _job_name_from_request(req),
                "project_title": pt,
                "batch_name": bn,
                "dataset_name": dn,
                "output_dir": _output_dir_from_request(req),
                "request": req,
            }
        )
    return {"items": result}


@router.post("/training/jobs")
async def create_training_job(body: TrainJobCreate) -> dict:
    _require_model_downloaded_in_hub(body.model)
    job = _manager_singleton().create_job(body)
    req = job.request or {}
    out = req.get("output_dir") if isinstance(req.get("output_dir"), str) else None
    return {
        "id": job.id,
        "status": job.status,
        "log_path": str(job.log_path) if job.log_path else None,
        "error_message": job.error_message,
        "output_dir": out,
    }


@router.post("/training/jobs/params-only")
async def create_params_only_training_job(request: Request) -> dict:
    """仅落库为训练任务（status=parameters_saved），不启子进程。与「仅保存参数」在新建时一致。"""
    try:
        raw_body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="请求体须为合法 JSON") from e
    if not isinstance(raw_body, dict):
        raise HTTPException(status_code=400, detail="JSON 根节点须为对象")
    try:
        job = _manager_singleton().create_params_only_job(raw_body)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    req = job.request or {}
    out = req.get("output_dir") if isinstance(req.get("output_dir"), str) else None
    return {
        "id": job.id,
        "status": job.status,
        "log_path": None,
        "error_message": job.error_message,
        "output_dir": out,
    }


@router.post("/training/jobs/{job_id}/start")
async def start_training_from_params_job(job_id: str) -> dict:
    """将 parameters_saved 任务按当前 request 启训练，与开始训练不重复建 id。"""
    m = _manager_singleton()
    j0 = m.get_job(job_id)
    if not j0:
        raise HTTPException(status_code=404, detail="任务不存在")
    if j0.status != "parameters_saved":
        raise HTTPException(
            status_code=400,
            detail="该任务不是「仅保存参数」待启动状态。请直接用「开始训练」从当前表单创建新任务。",
        )
    req0 = j0.request or {}
    mod = req0.get("model")
    mid = (str(mod) if mod is not None else "").strip()
    if not mid:
        raise HTTPException(
            status_code=400,
            detail="请先在「设置 → 模型管理」中成功下载至少一个模型，并在表单中选择该模型。",
        )
    _require_model_downloaded_in_hub(mid)
    job = m.start_params_saved_job(job_id)
    if not job:
        raise HTTPException(status_code=400, detail="无法启动训练，请检查任务参数后重试。")
    req = job.request or {}
    out = req.get("output_dir") if isinstance(req.get("output_dir"), str) else None
    return {
        "id": job.id,
        "status": job.status,
        "log_path": str(job.log_path) if job.log_path else None,
        "error_message": job.error_message,
        "output_dir": out,
    }


@router.patch("/training/jobs/{job_id}")
async def patch_training_job(job_id: str, body: TrainJobRenameBody) -> dict:
    j = _manager_singleton().update_job_name(job_id, body.job_name)
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在或名称为空")
    req = j.request or {}
    return {"ok": True, "job_name": _job_name_from_request(req)}


@router.get("/training/jobs/{job_id}")
async def get_training_job(root: WorkspaceRoot, job_id: str) -> dict:
    job = _manager_singleton().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    req = job.request or {}
    pt, bn, dn = _display_names_for_job_request(root, req)
    return {
        "id": job.id,
        "status": job.status,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "return_code": job.return_code,
        "error_message": job.error_message,
        "job_name": _job_name_from_request(req),
        "project_title": pt,
        "batch_name": bn,
        "dataset_name": dn,
        "output_dir": _output_dir_from_request(req),
        "request": req,
    }


@router.get("/training/jobs/{job_id}/logs")
async def get_training_logs(job_id: str) -> dict:
    job = _manager_singleton().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    text, truncated = _manager_singleton().read_log(job_id)
    return {"text": text, "truncated": truncated}


@router.post("/training/jobs/{job_id}/cancel")
async def cancel_training_job(job_id: str) -> dict:
    ok = _manager_singleton().cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="无法取消该任务")
    return {"ok": True}


@router.delete("/training/jobs/{job_id}")
async def delete_training_job(job_id: str) -> dict:
    ok = _manager_singleton().delete_job(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="仅可删除已结束且非运行中任务，或请先用取消。")
    return {"ok": True}


@router.post("/training/jobs/{job_id}/retry")
async def retry_training_job(root: WorkspaceRoot, job_id: str) -> dict:
    """与 UI「继续训练」一致：在相同 output_dir 上从最新 checkpoint 恢复，而非清空目录重训。"""
    m = _manager_singleton()
    old = m.get_job(job_id)
    if not old or not old.request:
        raise HTTPException(status_code=404, detail="原任务不存在或参数缺失")
    if old.status not in ("failed", "cancelled"):
        raise HTTPException(status_code=400, detail="仅失败或已取消的任务可继续训练")
    mod = old.request.get("model")
    if isinstance(mod, str):
        _require_model_downloaded_in_hub(mod)
    body = TrainJobCreate.model_validate(old.request)
    if not _latest_checkpoint_relpath(root, body.output_dir):
        raise HTTPException(
            status_code=400,
            detail="输出目录中未找到可恢复的 checkpoint（如 checkpoint-8）。若尚未产生断点，请重新发起训练。",
        )
    j = m.retry_job(job_id)
    if not j:
        raise HTTPException(status_code=400, detail="无法继续训练（请重试或检查输出目录与 checkpoint）")
    return {
        "id": j.id,
        "status": j.status,
        "log_path": str(j.log_path) if j.log_path else None,
        "error_message": j.error_message,
    }


@router.get("/training/jobs/{job_id}/metrics")
async def get_training_metrics(job_id: str) -> dict:
    j = _manager_singleton().get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在")
    text, _t = _manager_singleton().read_log(job_id, max_bytes=2_000_000)
    ne: int | None = None
    if j.request and isinstance(j.request, dict):
        raw = j.request.get("num_train_epochs")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            ne = int(raw)
    prog = parse_training_progress(text, num_train_epochs=ne)
    prog["stages"] = build_training_stages(text, ne, prog)
    st = j.status
    if st == "succeeded" and isinstance(prog.get("percent"), (int, float)) and float(prog["percent"]) < 100:
        prog = {**prog, "percent": 100.0, "label": "任务成功"}
        for stg in prog.get("stages") or []:
            if isinstance(stg, dict) and stg.get("id") == "train":
                stg["percent"] = 100.0
                stg["label"] = "任务成功"
    elif st in ("failed", "cancelled"):
        pl = prog.get("label")
        if isinstance(pl, str) and pl and prog.get("percent") is not None:
            base = f"已结束（{st}） · 最近进度: {pl}"
        elif prog.get("percent") is None:
            base = f"已结束（{st}）"
        else:
            base = f"已结束（{st}）"
        em = (j.error_message or "").strip()
        if em:
            base = f"{base} — {em}"
        elif j.return_code is not None and j.return_code != 0:
            base = f"{base}（子进程退出码 {j.return_code}）"
        prog = {**prog, "label": base}
        for stg in prog.get("stages") or []:
            if isinstance(stg, dict) and stg.get("id") == "train":
                stg["label"] = base
    return {"job_id": job_id, "series": parse_training_log_metrics(text), "progress": prog}


@router.get("/training/jobs/{job_id}/logs/stream")
async def stream_training_logs(
    job_id: str,
    interval: float = Query(1.0, ge=0.2, le=5.0),
) -> Any:
    """简易 SSE：定期推送当前日志尾（与轮询等效，前端可二选一）。"""

    async def _gen() -> Any:
        last = ""
        while True:
            j = _manager_singleton().get_job(job_id)
            if not j:
                yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                return
            text, truncated = _manager_singleton().read_log(job_id)
            if text != last:
                last = text
                yield f"data: {json.dumps({'text': text, 'truncated': truncated, 'status': j.status}, ensure_ascii=False)}\n\n"
            if j.status in ("succeeded", "failed", "cancelled"):
                yield f"data: {json.dumps({'status': j.status, 'end': True}, ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(interval)

    return StreamingResponse(_gen(), media_type="text/event-stream")


class TrainJobRenameBody(BaseModel):
    job_name: str = Field(..., min_length=1, description="训练任务显示名称")


class YamlBody(BaseModel):
    yaml: str = Field(..., min_length=1, description="训练 YAML 文本")


_NO_CACHE_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


@router.post("/training/config/yaml/parse")
async def parse_training_yaml(body: YamlBody) -> dict[str, Any]:
    try:
        data = yaml.safe_load(body.yaml)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"YAML 无法解析: {e}") from e
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="根节点须为对象")
    try:
        t = TrainJobCreate.model_validate(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"与训练参数字段不完全匹配: {e}") from e
    return {"ok": True, "params": t.model_dump()}


@router.get("/training/config/yaml/export")
async def export_training_yaml(job_id: str | None = Query(None)) -> Response:
    if not job_id:
        t = TrainJobCreate()
    else:
        j = _manager_singleton().get_job(job_id)
        if not j or not j.request:
            raise HTTPException(status_code=404, detail="任务不存在或缺少参数")
        t = TrainJobCreate.model_validate(j.request)
    text = f"# 数据工坊 训练配置（可粘贴回「从 YAML 导入」）\n{yaml.safe_dump(t.model_dump(), allow_unicode=True, sort_keys=False)}"
    return Response(
        content=text,
        media_type="text/yaml; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="train-config.yaml"'},
    )


@router.get("/training/form-params")
async def get_training_form_params(
    job_id: str = Query(..., min_length=1, description="与 GET /api/training/jobs/{id} 中 request 为同一条数据"),
) -> JSONResponse:
    jid = job_id.strip()
    job = _manager_singleton().get_job(jid)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    req = job.request
    p = dict(req) if isinstance(req, dict) else None
    return JSONResponse(content={"params": p}, headers=_NO_CACHE_HEADERS)


async def _persist_training_form_params(request: Request, job_id: str) -> JSONResponse:
    """将表单写入该任务在库中的 request_json，经 TrainJobCreate 校验，不启训练子进程（与仅查看的 request 同一条）。"""
    try:
        raw_body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="请求体须为合法 JSON") from e
    if not isinstance(raw_body, dict):
        raise HTTPException(status_code=400, detail="JSON 根节点须为对象")
    data: dict[str, Any] = raw_body
    payload = json.dumps(data, ensure_ascii=False)
    if len(payload.encode("utf-8")) > _MAX_SAVED_FORM_PARAMS_BYTES:
        raise HTTPException(status_code=400, detail="训练参数数据过大，请删减后再试")
    jid = job_id.strip()
    if not jid:
        raise HTTPException(status_code=400, detail="需要 job_id")
    try:
        u = _manager_singleton().update_job_request_params(jid, data)
        if not u:
            raise HTTPException(status_code=404, detail="任务不存在")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(content={"ok": True}, headers=_NO_CACHE_HEADERS)


@router.post("/training/form-params")
async def post_training_form_params(
    request: Request,
    job_id: str = Query(..., min_length=1, description="要更新的训练任务 id"),
) -> JSONResponse:
    return await _persist_training_form_params(request, job_id)


@router.put("/training/form-params")
async def put_training_form_params(
    request: Request,
    job_id: str = Query(..., min_length=1, description="要更新的训练任务 id"),
) -> JSONResponse:
    return await _persist_training_form_params(request, job_id)
