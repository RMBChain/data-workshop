"""FastAPI 依赖：工作区根路径与数据库连接（与单例 get_connection 一致）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from backend.app.config import get_settings
from backend.app.db import get_connection


def get_workspace_root() -> Path:
    return get_settings().workspace_root.resolve()


WorkspaceRoot = Annotated[Path, Depends(get_workspace_root)]


def get_db(workspace: WorkspaceRoot) -> sqlite3.Connection:
    return get_connection(workspace)


DbConn = Annotated[sqlite3.Connection, Depends(get_db)]
