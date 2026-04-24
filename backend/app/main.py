from __future__ import annotations

import warnings

# 纯 CPU 也会在首次 import torch 时加载 torch.cuda → 可能 import pynvml（nvidia-smi 相关）。
# 依赖里应装 nvidia-ml-py（见 backend/requirements.txt）；勿再单独装已弃用的 PyPI 包 pynvml。
# 若环境仍带旧 pynvml 触发 FutureWarning，按模块忽略（通常仅此一条与 pynvml 相关）。
warnings.filterwarnings("ignore", category=FutureWarning, module=r"torch\.cuda$")

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.app.config import get_settings, parse_cors_origins
from backend.app.logutil import configure_logging
from backend.app.services.dataset_build import mark_stale_build_jobs_failed_on_restart
from backend.app.routers import (
    datasets,
    eval_routes,
    export_routes,
    health,
    imports,
    inference_routes,
    label_studio,
    merge_routes,
    models_routes,
    system_routes,
    training,
)

_log = logging.getLogger("workshop")


class _RequestTimingMiddleware(BaseHTTPMiddleware):
    """为每条 HTTP 记一条带耗时的业务日志。"""

    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000.0
        _log.info(
            "HTTP %s %s -> %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            ms,
        )
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    root = settings.workspace_root.resolve()
    n = mark_stale_build_jobs_failed_on_restart(root)
    if n:
        _log.warning(
            "启动时已将 %d 条未结束的数据集构建任务标为 failed（进程重启后无内存状态）",
            n,
        )
    _log.info("Data Workshop API 已启动, workspace=%s", root)
    yield
    _log.info("Data Workshop API 正在关闭")


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(
        title="Data Workshop API",
        version="0.1.0",
        description="ms-swift 数据工坊后端（CPU / Docker）",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_cors_origins(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(_RequestTimingMiddleware)
    app.include_router(health.router, prefix="/api")
    app.include_router(label_studio.router, prefix="/api")
    app.include_router(imports.router, prefix="/api")
    app.include_router(datasets.router, prefix="/api")
    app.include_router(training.router, prefix="/api")
    app.include_router(inference_routes.router, prefix="/api")
    app.include_router(merge_routes.router, prefix="/api")
    app.include_router(eval_routes.router, prefix="/api")
    app.include_router(export_routes.router, prefix="/api")
    app.include_router(system_routes.router, prefix="/api")
    app.include_router(models_routes.router, prefix="/api")
    return app


app = create_app()
