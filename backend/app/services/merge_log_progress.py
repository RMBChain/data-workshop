"""从合并任务日志文本解析 tqdm 进度，供 API 返回给前端展示。"""

from __future__ import annotations

import re
from typing import Any

_RE_TQDM_FULL = re.compile(r"^(.+?):\s*(\d+)%\|(?:.*?)\|\s*(\d+)/(\d+)")
_RE_TQDM_SIMPLE = re.compile(r"^(.+?):\s*(\d+)%\|")
_RE_LOADING_WEIGHTS = re.compile(r"(?i)^loading weights$")
_RE_WRITING_SHARDS = re.compile(r"(?i)^writing model shards$")


def _humanize_tqdm_label(raw: str) -> str:
    s = raw.strip()
    if _RE_LOADING_WEIGHTS.fullmatch(s):
        return "加载基座模型权重"
    if re.fullmatch(r"(?i)writing model shards", s):
        return "写入合并后的模型"
    return s


def _loading_weights_hint(fraction: str | None) -> str:
    if fraction:
        cleaned = re.sub(r"\s+", "", fraction)
        m = re.fullmatch(r"(\d+)/(\d+)", cleaned)
        if m:
            return f"与后端「Loading weights」一致：已载入 {m.group(1)} / {m.group(2)} 个权重分片。"
    return "与后端控制台 tqdm「Loading weights」一致：正在将基座权重载入内存。"


def _writing_shards_hint(percent: int, fraction: str | None) -> str:
    """单分片或首个 shard 极慢时，tqdm 可能长期停在 0%。"""
    parts = [
        "与后端「Writing model shards」一致：正在将合并后的全量模型写入输出目录。",
    ]
    if percent == 0 and fraction and "/1" in fraction.replace(" ", ""):
        parts.append("当前为 0/1 单分片时，进度条可能长时间停在 0%，通常仍在写盘，请耐心等待。")
    return "".join(parts)


def parse_merge_log_progress(text: str) -> dict[str, Any]:
    """解析合并子进程日志，返回与前端约定的 progress 结构（蛇形字段名）。"""
    lines = text.replace("\r\n", "\n").split("\n")
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if not line:
            continue
        m = _RE_TQDM_FULL.match(line)
        if m:
            raw_label = m.group(1).strip()
            pct = max(0, min(100, int(m.group(2))))
            fraction = f"{m.group(3)} / {m.group(4)}"
            phase = _humanize_tqdm_label(raw_label)
            rstrip = raw_label.strip()
            if _RE_LOADING_WEIGHTS.fullmatch(rstrip):
                phase_hint: str | None = _loading_weights_hint(fraction)
            elif _RE_WRITING_SHARDS.fullmatch(rstrip):
                phase_hint = _writing_shards_hint(pct, fraction)
            else:
                phase_hint = None
            return {
                "percent": pct,
                "phase": phase,
                "fraction": fraction,
                "phase_hint": phase_hint,
            }
        m_simple = _RE_TQDM_SIMPLE.match(line)
        if m_simple:
            raw_label = m_simple.group(1).strip()
            pct = max(0, min(100, int(m_simple.group(2))))
            rstrip = raw_label.strip()
            if _RE_LOADING_WEIGHTS.fullmatch(rstrip):
                ph = _loading_weights_hint(None)
            elif _RE_WRITING_SHARDS.fullmatch(rstrip):
                ph = _writing_shards_hint(pct, None)
            else:
                ph = None
            return {
                "percent": pct,
                "phase": _humanize_tqdm_label(raw_label),
                "fraction": None,
                "phase_hint": ph,
            }
    tail = "\n".join(lines[-40:])
    if re.search(r"(?i)writing model shards", tail):
        return {
            "percent": None,
            "phase": "写入合并后的模型",
            "fraction": None,
            "phase_hint": _writing_shards_hint(0, "0 / 1"),
        }
    if "合并并卸载" in tail:
        return {"percent": None, "phase": "合并并卸载 LoRA 适配器", "fraction": None, "phase_hint": None}
    if "加载 LoRA" in tail:
        return {"percent": None, "phase": "加载 LoRA 权重", "fraction": None, "phase_hint": None}
    if re.search(r"(?i)loading weights", tail):
        return {
            "percent": None,
            "phase": "加载基座模型权重",
            "fraction": None,
            "phase_hint": _loading_weights_hint(None),
        }
    if "加载基座" in tail:
        return {"percent": None, "phase": "加载基座模型", "fraction": None, "phase_hint": None}
    if "Downloading Model" in tail:
        return {"percent": None, "phase": "从 ModelScope 下载模型", "fraction": None, "phase_hint": None}
    return {"percent": None, "phase": "合并进行中", "fraction": None, "phase_hint": None}
