"""
ms-swift external_plugins 入口：向 callbacks_map 注册 phase_log，在 Trainer 各生命周期打印阶段名。

环境变量：
  SWIFT_PHASE_HOOK_VERBOSE=1  — 同时打印高频事件（每步的 step / optimizer / substep / on_log）
"""
from __future__ import annotations

import os
from typing import Any, Optional

from swift.callbacks.base import TrainerCallback
from swift.callbacks.mapping import callbacks_map
from transformers import TrainerControl, TrainerState

_VERBOSE = os.environ.get("SWIFT_PHASE_HOOK_VERBOSE", "").lower() in ("1", "true", "yes")

# 步级/优化器级事件默认关闭，避免刷屏；设为 VERBOSE 后开启
_STEP_LIKE = frozenset(
    {
        "on_step_begin",
        "on_step_end",
        "on_substep_end",
        "on_pre_optimizer_step",
        "on_optimizer_step",
        "on_log",
    }
)


def _print_phase(name: str, state: Optional[TrainerState] = None, **extra: Any) -> None:
    if name in _STEP_LIKE and not _VERBOSE:
        return
    parts = [f"[swift phase] {name}"]
    if state is not None:
        parts.append(f"global_step={getattr(state, 'global_step', None)}")
        ep = getattr(state, "epoch", None)
        parts.append(f"epoch={ep:.4f}" if isinstance(ep, (int, float)) else "epoch=?")
    if extra:
        parts.append(repr(extra))
    print(" | ".join(parts), flush=True)


class PhaseLogCallback(TrainerCallback):
    """为 transformers TrainerCallback 的每个事件打印阶段名称（见类方法名）。"""

    def on_init_end(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_init_end", state)
        return super().on_init_end(args, state, control, **kwargs)

    def on_train_begin(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_train_begin", state)
        return super().on_train_begin(args, state, control, **kwargs)

    def on_train_end(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_train_end", state)
        return super().on_train_end(args, state, control, **kwargs)

    def on_epoch_begin(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_epoch_begin", state)
        return super().on_epoch_begin(args, state, control, **kwargs)

    def on_epoch_end(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_epoch_end", state)
        return super().on_epoch_end(args, state, control, **kwargs)

    def on_step_begin(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_step_begin", state)
        return super().on_step_begin(args, state, control, **kwargs)

    def on_pre_optimizer_step(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_pre_optimizer_step", state)
        return super().on_pre_optimizer_step(args, state, control, **kwargs)

    def on_optimizer_step(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_optimizer_step", state)
        return super().on_optimizer_step(args, state, control, **kwargs)

    def on_substep_end(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_substep_end", state)
        return super().on_substep_end(args, state, control, **kwargs)

    def on_step_end(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_step_end", state)
        return super().on_step_end(args, state, control, **kwargs)

    def on_evaluate(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_evaluate", state, metrics=kwargs.get("metrics"))
        return super().on_evaluate(args, state, control, **kwargs)

    def on_predict(self, args, state: TrainerState, control: TrainerControl, metrics, **kwargs):
        _print_phase("on_predict", state, metrics=metrics)
        return super().on_predict(args, state, control, metrics, **kwargs)

    def on_save(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_save", state)
        return super().on_save(args, state, control, **kwargs)

    def on_log(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        logs = kwargs.get("logs")
        _print_phase("on_log", state, logs=logs)
        return super().on_log(args, state, control, **kwargs)

    def on_prediction_step(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_prediction_step", state)
        return super().on_prediction_step(args, state, control, **kwargs)

    def on_push_begin(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        _print_phase("on_push_begin", state)
        return super().on_push_begin(args, state, control, **kwargs)


callbacks_map["phase_log"] = PhaseLogCallback


class TrainApiEventCallback(TrainerCallback):
    """
    供 backend/scripts/train.py 直接调用 sft_main 时使用：打印关键训练事件与 metrics / logs（避免与 phase_log 完全重复）。
    步级事件默认关闭，与 phase_log 一致，可用 SWIFT_PHASE_HOOK_VERBOSE=1 打开。
    """

    def on_train_begin(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        max_steps = getattr(args, "max_steps", None)
        out = getattr(args, "output_dir", None)
        print(
            f"[train_api] on_train_begin | output_dir={out!r} | max_steps={max_steps} | "
            f"num_train_epochs={getattr(args, 'num_train_epochs', None)}",
            flush=True,
        )
        return super().on_train_begin(args, state, control, **kwargs)

    def on_epoch_begin(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        print(f"[train_api] on_epoch_begin | epoch={getattr(state, 'epoch', None)}", flush=True)
        return super().on_epoch_begin(args, state, control, **kwargs)

    def on_epoch_end(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        print(f"[train_api] on_epoch_end | epoch={getattr(state, 'epoch', None)}", flush=True)
        return super().on_epoch_end(args, state, control, **kwargs)

    def on_step_begin(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        if _VERBOSE:
            print(f"[train_api] on_step_begin | global_step={state.global_step}", flush=True)
        return super().on_step_begin(args, state, control, **kwargs)

    def on_step_end(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        if _VERBOSE:
            print(f"[train_api] on_step_end | global_step={state.global_step}", flush=True)
        return super().on_step_end(args, state, control, **kwargs)

    def on_log(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        logs = kwargs.get("logs")
        print(f"[train_api] on_log | global_step={state.global_step} | logs={logs!r}", flush=True)
        return super().on_log(args, state, control, **kwargs)

    def on_evaluate(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        metrics = kwargs.get("metrics")
        print(f"[train_api] on_evaluate | global_step={state.global_step} | metrics={metrics!r}", flush=True)
        return super().on_evaluate(args, state, control, **kwargs)

    def on_save(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        print(
            f"[train_api] on_save | global_step={state.global_step} | kwargs_keys={sorted(kwargs.keys())!r}",
            flush=True,
        )
        return super().on_save(args, state, control, **kwargs)

    def on_train_end(self, args, state: TrainerState, control: TrainerControl, **kwargs):
        print(
            f"[train_api] on_train_end | global_step={state.global_step} | "
            f"best_metric={getattr(state, 'best_metric', None)}",
            flush=True,
        )
        return super().on_train_end(args, state, control, **kwargs)


callbacks_map["train_api_events"] = TrainApiEventCallback
