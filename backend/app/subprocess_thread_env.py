from __future__ import annotations

from collections.abc import MutableMapping

# 若宿主机 / 编排传入空串或非正整数，libgomp 会报 Invalid value for OMP_NUM_THREADS，子进程可能卡住或无有效日志。
_STRICT_THREAD_ENV_KEYS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS")
_OPTIONAL_THREAD_ENV_KEYS = ("OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS")


def _thread_count_env_ok(text: str) -> bool:
    if not text:
        return False
    try:
        return int(text) >= 1
    except ValueError:
        return False


def sanitize_thread_limit_env(env: MutableMapping[str, str], *, fallback: str = "1") -> None:
    """
    规范化与 OpenMP / BLAS 线程数相关的环境变量；避免非法值导致 libgomp 报错或异常行为。
    OMP_NUM_THREADS、MKL_NUM_THREADS：缺省或非法时设为 fallback。
    OPENBLAS_NUM_THREADS、NUMEXPR_NUM_THREADS：仅当已设置且非法时覆盖（未设置不注入）。
    """

    for key in _STRICT_THREAD_ENV_KEYS:
        raw = env.get(key)
        val = "" if raw is None else str(raw).strip()
        if not _thread_count_env_ok(val):
            env[key] = fallback

    for key in _OPTIONAL_THREAD_ENV_KEYS:
        if key not in env:
            continue
        raw = env.get(key)
        val = "" if raw is None else str(raw).strip()
        if not _thread_count_env_ok(val):
            env[key] = fallback
