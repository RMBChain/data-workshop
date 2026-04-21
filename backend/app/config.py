from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> 仓库根目录
_DEFAULT_WORKSPACE = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKSHOP_", env_file=".env", extra="ignore")

    # 默认本机为仓库根；Docker Compose 中通过 WORKSHOP_WORKSPACE_ROOT=/workspace/project 覆盖
    workspace_root: Path = _DEFAULT_WORKSPACE
    # Label Studio 在 compose 中的服务地址（浏览器访问仍用 127.0.0.1:8080）
    label_studio_url: str = "http://label-studio:8080"
    # 本地开发前端（Vite 等）
    cors_origins: str = (
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:9000,http://localhost:9000"
    )


def get_settings() -> Settings:
    return Settings()


def parse_cors_origins(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]
