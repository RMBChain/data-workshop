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
