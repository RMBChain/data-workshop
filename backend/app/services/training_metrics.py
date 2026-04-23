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


def _train_tqdm_from_tail(log: str, tail_lines: int = 400) -> dict[str, float | str] | None:
    """
    从日志尾部解析「Train: 88%|...| 7/8 [」类 tqdm 行（ms-swift / HF Trainer 等）。

    这类进度与终端里用户看到的一致。若仅依赖 [train_api] on_log 里的 epoch（如 0.12/1），
    会远低于当前迭代实际进度；当两者并存时，应优先使用本行。
    """
    for line in reversed(log.splitlines()[-tail_lines:]):
        clean = _strip_ansi(line)
        if "Train:" not in clean or "%|" not in clean:
            continue
        m = re.search(r"Train:\s*(\d{1,3})%\s*\|", clean)
        if not m:
            continue
        bar_pct = min(100, int(m.group(1)))
        m2 = re.search(r"(\d+)\s*/\s*(\d+)\s*\[", clean)
        if m2:
            a, b = int(m2.group(1)), int(m2.group(2))
            if b > 0:
                calc = min(100.0, round(100.0 * a / b, 1))
                return {
                    "percent": calc,
                    "label": f"训练 {a} / {b}（tqdm）",
                }
        return {
            "percent": float(bar_pct),
            "label": f"训练 {bar_pct}%（tqdm）",
        }
    return None


def _val_tqdm_from_tail(
    log: str,
    max_tail_lines: int = 8000,
) -> dict[str, float | str] | None:
    """
    解析 Val: 0%|…| 0/2 与 Val: 100%|…| 2/2 等行（ms-swift / evaluate 的验证 tqdm）。
    """
    for line in reversed(log.splitlines()[-max_tail_lines:]):
        clean = _strip_ansi(line)
        if "Val:" not in clean or "%|" not in clean:
            continue
        m = re.search(r"Val:\s*(\d{1,3})%\s*\|", clean)
        if not m:
            continue
        bar_pct = min(100, int(m.group(1)))
        m2 = re.search(r"(\d+)\s*/\s*(\d+)\s*\[", clean)
        if m2:
            a, b = int(m2.group(1)), int(m2.group(2))
            if b > 0:
                calc = min(100.0, round(100.0 * a / b, 1))
                return {
                    "percent": calc,
                    "label": f"验证 {a} / {b}（tqdm）",
                }
        return {
            "percent": float(bar_pct),
            "label": f"验证 {bar_pct}%（tqdm）",
        }
    return None


def _saw_val_tqdm_line(log: str) -> bool:
    return re.search(r"Val:\s*\d{1,3}\s*%\s*\|", log) is not None


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
        # 验证 tqdm 由 _val_tqdm_from_tail / 阶段行展示，勿作为「主进度」泛型匹配
        if re.search(r"Val:\s*\d{1,3}\s*%\s*\|", clean):
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
        if re.search(r"Val:\s*\d{1,3}\s*%\s*\|", clean):
            continue
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


def _saw_log_downloading(log: str) -> bool:
    return re.search(r"Downloading", log) is not None


def _last_tail_pretrain_tqdm(
    log: str,
) -> tuple[str, float, str] | None:
    """
    从日志尾部找到最近一次与「下载 / Map（数据集映射）」相关的 tqdm 行。
    返回 (kind, percent, label)，kind 为 "download" | "map" | "train"。
    """
    for line in reversed(log.splitlines()[-500:]):
        clean = _strip_ansi(line)
        if not clean.strip() or "%|" not in clean:
            continue
        percents = [int(m.group(1)) for m in re.finditer(r"(\d{1,3})%\s*\|", clean)]
        percents = [p for p in percents if 0 <= p <= 100]
        if not percents:
            continue
        p = float(max(percents))
        if "Downloading" in clean and "Map:" not in clean:
            return ("download", p, f"模型下载 {int(p)}%")
        m_map = re.search(r"Map:\s*(\d{1,3})%\s*\|", clean) or re.search(
            r"Map:\s*(\d{1,3})%", clean
        )
        if m_map:
            return ("map", float(int(m_map.group(1))), f"Map {int(m_map.group(1))}%")
        if "Map:" in clean or "Tokenizing" in clean or (
            "Processing" in clean and "Downloading" not in clean
        ):
            return ("map", p, f"Map / 处理 {int(p)}%")
        if re.search(r"(?:^|\s)Train:\s*\d{1,3}%", clean) or re.search(r"^\[train\]", clean, re.IGNORECASE):
            return ("train", p, f"训练 {int(p)}%")
    return None


def build_training_stages(
    log: str,
    _num_train_epochs: int | None,
    main: dict[str, float | int | str | None],
) -> list[dict[str, Any]]:
    """
    阶段：下载模型、Map、Train、Val（验证集 evaluate，来自 `Val: N%` tqdm）。
    与 parse_training_progress 的汇总进度一致，主进度体现在 Train 行。
    """
    has_end = bool(re.search(r"\[train_api\]\s+on_train_end", log))
    has_begin = bool(re.search(r"\[train_api\]\s+on_train_begin", log))
    m_logs = re.findall(r"\[train_api\]\s+on_log\s+\|\s+global_step=(\d+)", log)
    t_pct: float | None = None
    raw = main.get("percent")
    if isinstance(raw, (int, float)):
        t_pct = float(raw)
    t_lbl = str(main.get("label") or "")

    def _row(
        sid: str,
        name: str,
        percent: float | None,
        label: str,
    ) -> dict[str, Any]:
        return {"id": sid, "name": name, "percent": percent, "label": label}

    def _vrow() -> dict[str, Any]:
        vi = _val_tqdm_from_tail(log)
        if vi is not None:
            vp = vi.get("percent")
            p = float(vp) if isinstance(vp, (int, float)) else None
            return _row("val", "Val", p, str(vi.get("label", "")))
        if has_end:
            return _row(
                "val",
                "Val",
                None,
                "未运行验证" if not _saw_val_tqdm_line(log) else "—",
            )
        if _saw_val_tqdm_line(log):
            return _row("val", "Val", None, "—")
        return _row("val", "Val", None, "待开始" if not log.strip() else "等待中")

    if has_end:
        return [
            _row("download", "下载模型", 100.0, "已完成"),
            _row("map", "Map", 100.0, "已完成"),
            _row("train", "Train", 100.0, "训练已完成"),
            _vrow(),
        ]

    if m_logs:
        return [
            _row("download", "下载模型", 100.0, "已完成"),
            _row("map", "Map", 100.0, "已完成"),
            _row("train", "Train", t_pct, t_lbl if t_lbl else "—"),
            _vrow(),
        ]

    if has_begin:
        return [
            _row("download", "下载模型", 100.0, "已完成或跳过" if not _saw_log_downloading(log) else "已完成"),
            _row("map", "Map", 100.0, "已完成或跳过"),
            _row("train", "Train", t_pct, t_lbl or "准备训练…"),
            _vrow(),
        ]

    tail = _last_tail_pretrain_tqdm(log)
    if tail is not None:
        kind, p, short_lbl = tail
        if kind == "download":
            return [
                _row("download", "下载模型", p, short_lbl),
                _row("map", "Map", None, "等待模型就绪…"),
                _row("train", "Train", None, "待开始"),
                _vrow(),
            ]
        if kind == "map":
            dl_lbl = "已完成" if _saw_log_downloading(log) else "已跳过/无需下载"
            return [
                _row("download", "下载模型", 100.0, dl_lbl),
                _row("map", "Map", p, short_lbl),
                _row("train", "Train", None, "待开始"),
                _vrow(),
            ]
        if kind == "train":
            return [
                _row("download", "下载模型", 100.0, "已完成或跳过" if not _saw_log_downloading(log) else "已完成"),
                _row("map", "Map", 100.0, "已完成或跳过"),
                _row("train", "Train", p, short_lbl),
                _vrow(),
            ]

    tr_label = t_lbl if t_lbl else ("待开始" if not log.strip() else "—")
    return [
        _row("download", "下载模型", None, "待开始" if not log.strip() else "等待或已跳过"),
        _row("map", "Map", None, "待开始" if not log.strip() else "等待中"),
        _row("train", "Train", t_pct, tr_label),
        _vrow(),
    ]


def parse_training_progress(
    log: str,
    num_train_epochs: int | None = None,
) -> dict[str, float | int | str | None]:
    """
    从 [train_api]、按 epoch 的日志、以及下载/tqdm 行解析训练进度。
    """
    if re.search(r"\[train_api\]\s+on_train_end", log):
        return {"percent": 100.0, "label": "训练已完成"}

    # 有 Train: tqdm 时优先于 on_log 的步数/轮次，避免与终端进度不一致（on_log 常滞后或 epoch 小步时偏低）
    tt = _train_tqdm_from_tail(log)
    if tt is not None:
        return tt

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

    # 已有 on_train_begin 但尚未出现任何 on_log 时，仍用 tqdm 的 Train/Eval 行（首轮步进或崩溃前唯一进度）
    m_before_tqdm = re.findall(r"\[train_api\]\s+on_log\s+\|\s+global_step=(\d+)", log)
    if re.search(r"\[train_api\]\s+on_train_begin", log) and not m_before_tqdm:
        from_tqdm = _progress_from_tqdm_tail(log)
        if from_tqdm is not None:
            return from_tqdm

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
