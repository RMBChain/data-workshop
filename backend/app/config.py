from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> 仓库根目录
_DEFAULT_WORKSPACE = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKSHOP_", env_file=".env", extra="ignore")

    # 默认本机为仓库根；Docker Compose 中通过 WORKSHOP_WORKSPACE_ROOT=/workspace/project 覆盖
    workspace_root: Path = _DEFAULT_WORKSPACE
    # 覆盖 <workspace>/state/workshop.db；工作区在 NFS/不兼容卷且 SQLite 报 I/O 时，设为容器内本地路径如 /var/lib/workshop/workshop.db
    db_path: Path | None = None
    # 单独部署的 Label Studio；Docker 中 API 通过 host.docker.internal 访问宿主机端口（见 README）
    label_studio_url: str = "http://host.docker.internal:8080"
    # 本地开发前端（Vite 等）
    cors_origins: str = (
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:8601,http://localhost:8601,"
        "http://127.0.0.1:8701,http://localhost:8701"
    )
    # ms-swift 可执行文件（或 `python -m swift` 由实现侧切分），Docker 中通常为 PATH 内 `swift`
    swift_executable: str = "swift"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def parse_cors_origins(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]
