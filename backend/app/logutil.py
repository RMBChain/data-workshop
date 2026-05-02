from __future__ import annotations

import logging
import os
import sys


def configure_logging() -> None:
    """
    应用日志：默认 INFO，环境变量 WORKSHOP_LOG_LEVEL=DEBUG 可开更细粒度。
    """
    name = (os.environ.get("WORKSHOP_LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, name, logging.INFO)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        stream=sys.stdout,
        force=True,
    )
    # 控制第三方库噪音；需要调试图片下载/LS 请求时设 WORKSHOP_HTTPX_LOG=1
    _hx = logging.DEBUG if os.environ.get("WORKSHOP_HTTPX_LOG") else logging.WARNING
    logging.getLogger("httpx").setLevel(_hx)
    logging.getLogger("httpcore").setLevel(_hx)
