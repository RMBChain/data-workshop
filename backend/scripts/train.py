"""
使用 ms-swift 在纯 CPU 上对 Qwen3-VL-2B-Instruct 做 LoRA 微调。
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import torch
from contextlib import contextmanager
from pathlib import Path


def _parse_cli_bool(value: str | bool) -> bool:
    """
    argparse 的 type=bool 会执行 bool(字符串) —— 而 bool("false") 在 Python 中为 True。
    对 `--packing false` 等参数必须使用本解析器。
    """
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "t", "yes", "y", "on"):
        return True
    if s in ("0", "false", "f", "no", "n", "off"):
        return False
    raise ValueError(f"invalid boolean: {value!r}")


def _parse_torch_version() -> tuple[int, int, int] | None:
    s = torch.__version__.split("+", 1)[0]
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _ensure_torch_compatible_with_swift() -> None:
    """当前依赖栈里会出现 `from torch.distributed.fsdp import FSDPModule`；该符号仅在 PyTorch>=2.6 导出。"""
    v = _parse_torch_version()
    if v is None:
        return
    if v < (2, 6, 0):
        print(
            f"错误: 当前 PyTorch 为 {torch.__version__}，需 torch>=2.6（否则无法提供 torch.distributed.fsdp.FSDPModule）。\n"
            "CPU 示例（版本需一致）:\n"
            "  uv pip install torch==2.6.0+cpu torchvision==0.21.0+cpu torchaudio==2.6.0+cpu "
            "-f https://download.pytorch.org/whl/cpu"
        )
        sys.exit(1)


def _uniq_callback_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _swift_callback_names(
    *,
    enable_phase_log: bool,
    extra: list[str] | None,
) -> tuple[list[str], Path | None]:
    extra = extra or []
    plugin = Path(__file__).resolve().parent / "swift_phase_hooks.py"
    names: list[str] = []
    if plugin.is_file():
        names.append("train_api_events")
        if enable_phase_log:
            names.append("phase_log")
    else:
        print(f"警告: 未找到 {plugin}，无法注册 train_api_events / phase_log。")
    names = _uniq_callback_names(names + list(extra))
    return names, plugin if plugin.is_file() else None


def _load_swift_phase_hooks_plugin(plugin_path: Path) -> None:
    if not plugin_path.is_file():
        return
    spec = importlib.util.spec_from_file_location("_qwen_train_swift_phase_hooks", plugin_path)
    if spec is None or spec.loader is None:
        print(f"警告: 无法加载插件 {plugin_path}")
        return
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)


@contextmanager
def _swift_train_env(env: dict[str, str]):
    old = os.environ.copy()
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


def _run_sft_main_api(argv: list[str], *, plugin_path: Path, callback_names: list[str]) -> None:
    if any(n in ("phase_log", "train_api_events") for n in callback_names):
        _load_swift_phase_hooks_plugin(plugin_path)
    from swift.pipelines import sft_main

    sft_main(argv)


def train_with_swift(
    model_name="Qwen/Qwen3-VL-2B-Instruct",
    train_dataset="data/train.jsonl",
    val_dataset="data/val.jsonl",
    output_dir="output/qwen3vl-2b-lora",
    swift_callbacks=None,
    no_swift_phase_hooks: bool = False,
    **kwargs,
):
    """纯 CPU：固定 float32、eager attention，并隐藏 GPU。"""
    kwargs["torch_dtype"] = "float32"
    kwargs["attn_impl"] = "eager"
    kwargs.setdefault("image_max_token_num", 64)
    kwargs.setdefault("max_length", 128)
    kwargs.setdefault("per_device_train_batch_size", 1)
    kwargs.setdefault("gradient_accumulation_steps", 1)
    kwargs.setdefault("lora_rank", 1)
    kwargs.setdefault("lora_alpha", 2)
    # ms-swift 在 Linux 上默认 dataloader_num_workers=1；Docker 默认 /dev/shm 很小，易触发 bus error
    kwargs.setdefault("dataloader_num_workers", 0)
    # 必须显式传入；若省略，swift 可能对部分模板默认开启 packing，而 packing 依赖 flash_attn（与 CPU/eager 冲突）
    kwargs.setdefault("packing", False)
    if kwargs.get("attn_impl") == "eager":
        kwargs["packing"] = False

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = ""
    env.pop("NPROC_PER_NODE", None)
    env["IMAGE_MAX_TOKEN_NUM"] = str(kwargs.get("image_max_token_num", 64))
    env["VIDEO_MAX_TOKEN_NUM"] = str(kwargs.get("video_max_token_num", 16))

    argv: list[str] = [
        "--model",
        model_name,
        "--train_type",
        "lora",
        "--dataset",
        train_dataset,
        "--val_dataset",
        val_dataset,
        "--output_dir",
        output_dir,
        "--lora_rank",
        str(kwargs.get("lora_rank", 1)),
        "--lora_alpha",
        str(kwargs.get("lora_alpha", 2)),
        "--target_modules",
        kwargs.get("target_modules", "all-linear"),
        "--freeze_vit",
        str(kwargs.get("freeze_vit", "true")).lower(),
        "--num_train_epochs",
        str(kwargs.get("num_train_epochs", 1)),
        "--per_device_train_batch_size",
        str(kwargs.get("per_device_train_batch_size", 1)),
        "--per_device_eval_batch_size",
        str(kwargs.get("per_device_eval_batch_size", 1)),
        "--gradient_accumulation_steps",
        str(kwargs.get("gradient_accumulation_steps", 1)),
        "--learning_rate",
        str(kwargs.get("learning_rate", 1e-4)),
        "--torch_dtype",
        kwargs.get("torch_dtype", "float32"),
        "--attn_impl",
        kwargs.get("attn_impl", "eager"),
        "--fp16",
        "false",
        "--bf16",
        "false",
        "--max_length",
        str(kwargs.get("max_length", 128)),
        "--logging_steps",
        str(kwargs.get("logging_steps", 1000)),
        "--save_steps",
        str(kwargs.get("save_steps", 1_000_000)),
        "--eval_steps",
        str(kwargs.get("eval_steps", 1_000_000)),
        "--save_total_limit",
        str(kwargs.get("save_total_limit", 1)),
        "--dataloader_num_workers",
        str(kwargs.get("dataloader_num_workers", 0)),
    ]

    cb_names, plugin_path = _swift_callback_names(
        enable_phase_log=not no_swift_phase_hooks,
        extra=list(swift_callbacks or []),
    )
    if cb_names:
        argv.extend(["--callbacks"] + cb_names)

    if kwargs.get("warmup_ratio"):
        argv.extend(["--warmup_ratio", str(kwargs["warmup_ratio"])])
    if kwargs.get("lr_scheduler_type"):
        argv.extend(["--lr_scheduler_type", kwargs["lr_scheduler_type"]])
    if kwargs.get("gradient_checkpointing"):
        argv.extend(["--gradient_checkpointing", str(kwargs["gradient_checkpointing"]).lower()])
    argv.extend(["--packing", str(bool(kwargs.get("packing", False))).lower()])

    print("=" * 80)
    print("训练配置 (CPU)")
    print(f"  模型: {model_name}")
    print(f"  dtype / attn: {kwargs.get('torch_dtype')} / {kwargs.get('attn_impl')}")
    print(f"  max_length: {kwargs.get('max_length', 128)}")
    print(f"  packing: {kwargs.get('packing', False)} (关闭时勿依赖 flash_attn)")
    print(f"  dataloader_num_workers: {kwargs.get('dataloader_num_workers', 0)}")
    print("=" * 80)
    print("环境变量:")
    print(f"  IMAGE_MAX_TOKEN_NUM={env['IMAGE_MAX_TOKEN_NUM']}")
    print(f"  VIDEO_MAX_TOKEN_NUM={env['VIDEO_MAX_TOKEN_NUM']}")
    print(f"  CUDA_VISIBLE_DEVICES=(空，强制 CPU)")
    print(f"  callbacks: {cb_names or '(无)'}")
    print("=" * 80)
    print()

    plugin = plugin_path or Path(__file__).resolve().parent / "swift_phase_hooks.py"
    try:
        with _swift_train_env(env):
            _run_sft_main_api(argv, plugin_path=plugin, callback_names=cb_names)
        print("\n训练完成！")
        print(f"模型保存在: {output_dir}")
    except KeyboardInterrupt:
        print("\n训练被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n训练失败: {e}")
        sys.exit(1)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="ms-swift CPU 微调 Qwen3-VL-2B-Instruct")
    parser.add_argument("--model", type=str, default="Qwen/Qwen3-VL-2B-Instruct", help="模型名称或路径")
    parser.add_argument("--train_dataset", type=str, default="data/train.jsonl", help="训练集")
    parser.add_argument("--val_dataset", type=str, default="data/val.jsonl", help="验证集")
    parser.add_argument("--output_dir", type=str, default="output/qwen3vl-2b-lora", help="输出目录")

    parser.add_argument("--lora_rank", type=int, default=1, help="LoRA rank（默认可选最小以省内存）")
    parser.add_argument("--lora_alpha", type=int, default=2, help="LoRA alpha")
    parser.add_argument("--target_modules", type=str, default="all-linear", help="LoRA 目标模块")
    parser.add_argument(
        "--freeze_vit",
        type=_parse_cli_bool,
        default=True,
        help="是否冻结 ViT（可传 true/false）",
    )

    parser.add_argument("--num_train_epochs", type=int, default=1, help="训练轮数（默认 1 以尽快完成）")
    parser.add_argument("--per_device_train_batch_size", type=int, default=1, help="训练 batch size")
    parser.add_argument("--per_device_eval_batch_size", type=int, default=1, help="验证 batch size")
    parser.add_argument("--gradient_accumulation_steps", type=int, default=1, help="梯度累积（默认 1 减少重复前向、加快结束）")
    parser.add_argument("--learning_rate", type=float, default=1e-4, help="学习率")
    parser.add_argument(
        "--dataloader_num_workers",
        type=int,
        default=0,
        help="DataLoader 子进程数；Docker 默认 shm 很小，建议 0（主进程加载）",
    )

    parser.add_argument("--max_length", type=int, default=128, help="最大序列长度（过大易 OOM）")
    parser.add_argument("--logging_steps", type=int, default=1000, help="日志步频")
    parser.add_argument("--save_steps", type=int, default=1_000_000, help="保存步频（大可减少中途存盘、加快小任务）")
    parser.add_argument("--eval_steps", type=int, default=1_000_000, help="评估步频（大可减少验证开销）")
    parser.add_argument("--save_total_limit", type=int, default=1, help="最多保留 checkpoint 数")
    parser.add_argument("--warmup_ratio", type=float, default=0.005, help="预热比例")
    parser.add_argument("--lr_scheduler_type", type=str, default="cosine", help="学习率调度")
    parser.add_argument(
        "--gradient_checkpointing",
        type=_parse_cli_bool,
        default=True,
        help="梯度检查点（可传 true/false）",
    )
    parser.add_argument(
        "--packing",
        type=_parse_cli_bool,
        default=False,
        help="序列打包；CPU/eager 下须为 false，否则需 flash_attn",
    )
    parser.add_argument("--image_max_token_num", type=int, default=64, help="图片最大 token 数（VL 主内存占用之一）")
    parser.add_argument("--video_max_token_num", type=int, default=16, help="视频最大 token 数")
    parser.add_argument(
        "--swift_callbacks",
        nargs="*",
        default=None,
        metavar="NAME",
        help="附加 ms-swift 回调名，见 swift/callbacks/mapping.py",
    )
    parser.add_argument(
        "--no_swift_phase_hooks",
        action="store_true",
        help="关闭 phase_log；保留 train_api_events",
    )

    args = parser.parse_args()
    _ensure_torch_compatible_with_swift()

    train_dataset = args.train_dataset
    val_dataset = args.val_dataset
    if not Path(train_dataset).exists():
        print(f"错误: 训练数据集不存在: {train_dataset}")
        sys.exit(1)
    if not Path(val_dataset).exists():
        print(f"错误: 验证数据集不存在: {val_dataset}")
        sys.exit(1)

    train_kwargs = vars(args).copy()
    swift_callbacks = train_kwargs.pop("swift_callbacks", None) or []
    no_swift_phase_hooks = bool(train_kwargs.pop("no_swift_phase_hooks", False))
    model_name = train_kwargs.pop("model")
    output_dir = train_kwargs.pop("output_dir")
    train_kwargs.pop("train_dataset", None)
    train_kwargs.pop("val_dataset", None)

    train_with_swift(
        model_name=model_name,
        train_dataset=train_dataset,
        val_dataset=val_dataset,
        output_dir=output_dir,
        swift_callbacks=swift_callbacks,
        no_swift_phase_hooks=no_swift_phase_hooks,
        **train_kwargs,
    )


if __name__ == "__main__":
    main()
