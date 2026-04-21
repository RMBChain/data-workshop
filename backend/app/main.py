from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings, parse_cors_origins
from backend.app.routers import data_import, health, inference, label_studio, training


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Data Workshop API",
        version="0.1.0",
        description="ms-swift 数据工坊后端（CPU / Docker）",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_cors_origins(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(label_studio.router, prefix="/api")
    app.include_router(training.router, prefix="/api")
    app.include_router(data_import.router, prefix="/api")
    app.include_router(inference.router, prefix="/api")
    return app


app = create_app()
