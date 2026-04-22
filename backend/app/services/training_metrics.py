from __future__ import annotations

import re
from typing import Any


def parse_training_log_metrics(log: str) -> dict[str, Any]:
    """
    从训练日志中尽力抽取 loss / lr 序列，供 ECharts 使用。
    不同 ms-swift 版本输出格式可能不同，采用宽松模式匹配。
    """
    train_loss: list[dict[str, float | int]] = []
    eval_loss: list[dict[str, float | int]] = []
    lr: list[dict[str, float | int]] = []
    idx = 0
    for i, line in enumerate(log.splitlines()):
        m = re.search(
            r"(?:train|training)?\D*(?:loss)[:\s=]+([0-9eE+.\-]+)",
            line,
            re.IGNORECASE,
        )
        if m:
            try:
                train_loss.append({"step": idx, "value": float(m.group(1))})
                idx += 1
            except ValueError:
                pass
        m2 = re.search(r"(?:eval|val|validation)\D*(?:loss)[:\s=]+([0-9eE+.\-]+)", line, re.IGNORECASE)
        if m2:
            try:
                eval_loss.append({"step": i, "value": float(m2.group(1))})
            except ValueError:
                pass
        m3 = re.search(
            r"(?:lr|learning[_\s]?rate)[:\s=]+([0-9eE+.\-]+)"
            r"|"
            r"learning rate:\s*([0-9eE+.\-]+)",
            line,
            re.IGNORECASE,
        )
        if m3:
            g = m3.group(1) or m3.group(2)
            if g:
                try:
                    lr.append({"step": i, "value": float(g)})
                except ValueError:
                    pass
    return {
        "train_loss": train_loss[:500],
        "eval_loss": eval_loss[:500],
        "learning_rate": lr[:500],
    }


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)


def _parse_train_begin_meta(log: str) -> tuple[int | None, float | None]:
    """自 [train_api] on_train_begin 行解析 max_steps 与 num_train_epochs。"""
    max_steps: int | None = None
    num_ep: float | None = None
    for line in log.splitlines():
        if "[train_api] on_train_begin" in line and "max_steps=" in line:
            m = re.search(r"max_steps=(-?\d+)", line)
            if m:
                max_steps = int(m.group(1))
            m2 = re.search(r"num_train_epochs=([0-9.]+|None)", line)
            if m2 and m2.group(1) != "None":
                try:
                    num_ep = float(m2.group(1))
                except ValueError:
                    pass
    return max_steps, num_ep


def _last_on_log_epoch(log: str) -> float | None:
    """从 [train_api] on_log 的 logs= 字典中解析最后一次的 epoch（按 epoch 训练时 max_steps 常为 -1）。"""
    last: float | None = None
    for line in log.splitlines():
        if "[train_api] on_log" in line and "global_step=" in line:
            # 宽松匹配 'epoch': 0.12 与 "epoch": 0.12
            m = re.search(r"['\"]epoch['\"]\s*:\s*([0-9.eE+\-]+)", line)
            if m:
                try:
                    last = float(m.group(1))
                except ValueError:
                    pass
    return last


def _progress_from_tqdm_tail(log: str) -> dict[str, float | str] | None:
    """
    在尚未出现 [train_api] 训练指标前，从 ModelScope 下载、HF tqdm 等行解析进度。
    例：Downloading:  41%|...| 1.61G/3.96G ；Processing: 0.00/1.00
    """
    lines = log.splitlines()[-500:]
    for line in reversed(lines):
        clean = _strip_ansi(line)
        if not clean.strip():
            continue
        # 优先：同一行中 tqdm 的 N%|（多段时取最大，如同时有子进度条）
        if "%|" in clean or "it/s" in clean or "Downloading" in clean or "Processing" in clean:
            percents = [int(m.group(1)) for m in re.finditer(r"(\d{1,3})%\s*\|", clean)]
            percents = [p for p in percents if 0 <= p <= 100]
            if percents:
                p = max(percents)
                if "Downloading" in clean:
                    label = f"模型下载 {p}%"
                elif "Processing" in clean:
                    label = f"处理中 {p}%"
                else:
                    label = f"进行中 {p}%"
                return {"percent": float(p), "label": label}
        # 0.00/1.00 [ 类（处理条目数等）
        m = re.search(r"(\d+\.\d+)\s*/\s*(\d+\.\d+)\s*\[", clean)
        if m:
            try:
                a, b = float(m.group(1)), float(m.group(2))
            except ValueError:
                continue
            if b > 0:
                p = min(100.0, 100.0 * a / b)
                return {"percent": round(p, 1), "label": f"进度 {a:.2f} / {b:.2f}"}
        # 1.61G/3.96G 字节量（同单位）
        m2 = re.search(
            r"(\d+\.?\d*)\s*([KMGTkgt]?)\s*B?\s*/\s*(\d+\.?\d*)\s*([KMGTkgt]?)\s*B?(?:\s*\[)?",
            clean,
        )
        if m2 and ("Downloading" in clean or "model" in clean.lower() or "safetensors" in clean):
            def _to_bytes(val: str, unit: str) -> float:
                mult = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "": 1.0}
                uu = (unit or "").upper()[:1]
                try:
                    return float(val) * mult.get(uu, 1.0)
                except ValueError:
                    return 0.0
            a = _to_bytes(m2.group(1), m2.group(2) or "")
            b = _to_bytes(m2.group(3), m2.group(4) or "")
            if b > 0 and a >= 0:
                p = min(100.0, 100.0 * a / b)
                return {"percent": round(p, 1), "label": f"下载量 {a / 1e9:.2f}G / {b / 1e9:.2f}G"}

    for line in reversed(lines):
        clean = _strip_ansi(line)
        m2 = re.search(
            r"(?:\|\s*)?(\d+)\s*/\s*(\d+)(?=\s*(?:\[|\|))",
            clean,
        )
        if m2:
            a, b = int(m2.group(1)), int(m2.group(2))
            if b > 0 and 0 <= a <= b * 2:
                return {
                    "percent": min(100.0, round(100.0 * a / b, 1)),
                    "label": f"步 {a} / {b}（自日志行）",
                }
    return None


def parse_training_progress(
    log: str,
    num_train_epochs: int | None = None,
) -> dict[str, float | int | str | None]:
    """
    从 [train_api]、按 epoch 的日志、以及下载/tqdm 行解析训练进度。
    """
    if re.search(r"\[train_api\]\s+on_train_end", log):
        return {"percent": 100.0, "label": "训练已完成"}

    begin_max, begin_ep = _parse_train_begin_meta(log)
    n_epochs = float(num_train_epochs) if (num_train_epochs and num_train_epochs > 0) else None
    if n_epochs is None and begin_ep is not None and begin_ep > 0:
        n_epochs = begin_ep

    if begin_max is not None and begin_max > 0:
        m_logs = re.findall(r"\[train_api\]\s+on_log\s+\|\s+global_step=(\d+)", log)
        if m_logs:
            gs = int(m_logs[-1])
            return {
                "percent": min(100.0, round(100.0 * gs / begin_max, 1)),
                "label": f"步 {gs} / {begin_max}",
            }

    if n_epochs and n_epochs > 0:
        ep_log = _last_on_log_epoch(log)
        if ep_log is not None and ep_log >= 0:
            p = min(100.0, 100.0 * ep_log / n_epochs)
            return {
                "percent": round(p, 1),
                "label": f"轮次 {ep_log:.2f} / {n_epochs:g}（来自 on_log）",
            }

    if n_epochs and n_epochs > 0:
        epochs: list[float] = []
        for m in re.finditer(
            r"\[train_api\]\s+on_epoch_end\s+\|\s+epoch=([0-9.]+)", log
        ):
            try:
                epochs.append(float(m.group(1)))
            except ValueError:
                pass
        if epochs:
            e = max(epochs)
            if e >= 0:
                p = min(100.0, 100.0 * e / n_epochs)
                return {
                    "percent": round(p, 1),
                    "label": f"轮次约 {e:.2f} / {n_epochs:g}（on_epoch_end）",
                }

    if not re.search(r"\[train_api\]\s+on_train_begin", log):
        from_tqdm = _progress_from_tqdm_tail(log)
        if from_tqdm is not None:
            return from_tqdm

    m_logs = re.findall(r"\[train_api\]\s+on_log\s+\|\s+global_step=(\d+)", log)
    if m_logs:
        gs = int(m_logs[-1])
        if begin_max in (-1, 0) or begin_max is None:
            return {
                "percent": None,
                "label": f"训练中，已 {gs} 步（总步数由 epoch/日志解析）",
            }

    return {"percent": None, "label": "等待训练输出…" if not log.strip() else "进度解析中…"}
