"""
数据工坊：在工作区内将单路 LoRA 合并进基座并保存为 HF 目录（纯 CPU，供推理加载）。
多路 LoRA 的依赖关系复杂，MVP 仅对第一个有效路径做 merge。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path


def _resolve_base(p: str) -> str:
    q = Path(p)
    if q.is_dir():
        return str(q.resolve())
    from modelscope import snapshot_download

    return snapshot_download(p)


def _parse_cli_bool(s: str) -> bool:
    t = (s or "").strip().lower()
    if t in ("1", "true", "t", "yes", "y"):
        return True
    if t in ("0", "false", "f", "no", "n"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {s!r}")


def _merge_torch_dtype():
    """合并子进程默认隐藏 GPU；float32 整模易在「Writing model shards」前 OOM，优先 bf16 降峰值内存。"""
    import torch

    override = (os.environ.get("WORKSHOP_MERGE_TORCH_DTYPE") or "").strip().lower()
    if override in ("fp32", "float32", "f32"):
        return torch.float32
    if override in ("fp16", "float16", "f16"):
        return torch.float16
    if override in ("bf16", "bfloat16"):
        return torch.bfloat16
    try:
        x = torch.ones(4, 4, dtype=torch.bfloat16)
        _ = x @ x.T
        return torch.bfloat16
    except Exception:
        return torch.float32


def _use_tmp_staging() -> bool:
    """在 Docker Desktop（Windows 等）下向绑定挂载区直接写大 safetensors 分片常触发 EIO；先写到容器可写层再搬回工作区可规避。"""
    v = (os.environ.get("WORKSHOP_MERGE_USE_TMP_STAGING") or "").strip().lower()
    return v in ("1", "true", "yes", "y")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="基座：ModelScope id 或本地目录")
    ap.add_argument("--lora", required=True, action="append", dest="loras", help="LoRA 目录（可多次）")
    ap.add_argument("--output", required=True, help="输出目录（工作区内或绝对路径）")
    ap.add_argument(
        "--merge_lora_only",
        type=_parse_cli_bool,
        default=True,
        help="为 True：merge_and_unload 后保存全量模型；为 False：仅保存 PEFT 适配器（未合并到基座）",
    )
    ap.add_argument("--extra", default="[]", help="JSON 列表：多路时忽略除第一个以外的说明（预留）")
    args = ap.parse_args()

    import torch
    from peft import PeftModel
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

    dtype = _merge_torch_dtype()
    loras: list[str] = list(args.loras)
    if len(loras) > 1:
        print(
            f"注意: 当前合并仅对第一个 LoRA 生效: {loras[0]}（共 {len(loras)} 个入参，其余忽略）",
            file=sys.stderr,
        )
    lora_p = Path(loras[0]).resolve()
    if not lora_p.is_dir():
        print(f"错误: LoRA 目录不存在: {lora_p}", file=sys.stderr)
        return 1
    for name in ("adapter_config.json", "adapter_model.safetensors"):
        if not (lora_p / name).is_file():
            print(f"错误: LoRA 缺少 {name}", file=sys.stderr)
            return 1

    out = Path(args.output).resolve()
    use_staging = _use_tmp_staging()
    if use_staging:
        work_dir = Path(
            tempfile.mkdtemp(prefix="workshop_merge_", dir=tempfile.gettempdir())
        )
        print(
            f"大文件先写入临时目录，再同步到工作区: {work_dir} → {out}",
            file=sys.stderr,
        )
    else:
        out.mkdir(parents=True, exist_ok=True)
        work_dir = out

    base = _resolve_base(args.base)
    print(f"加载基座: {base}（dtype={dtype}）")
    try:
        model = Qwen3VLForConditionalGeneration.from_pretrained(
            base,
            torch_dtype=dtype,
            trust_remote_code=True,
            low_cpu_mem_usage=True,
        )
        model = model.to("cpu")
        print(f"加载 LoRA: {lora_p}")
        model = PeftModel.from_pretrained(model, str(lora_p))
        if args.merge_lora_only:
            print("合并并卸载 LoRA 适配器层…")
            merged = model.merge_and_unload()
            # 分片降低单次写入峰值；与 HF 默认 tqdm「Writing model shards」一致
            merged.save_pretrained(
                str(work_dir),
                safe_serialization=True,
                max_shard_size="2GB",
            )
        else:
            print("未合并到基座：仅导出 PEFT 适配器目录（merge_lora_only=false）…")
            model.save_pretrained(
                str(work_dir),
                safe_serialization=True,
                max_shard_size="2GB",
            )
        processor = AutoProcessor.from_pretrained(base, trust_remote_code=True)
        processor.save_pretrained(str(work_dir))
        if use_staging:
            print(f"正在将合并结果复制到: {out}", file=sys.stderr)
            if out.exists():
                shutil.rmtree(out)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(work_dir), str(out))
    except Exception:
        if (
            use_staging
            and work_dir.resolve() != out.resolve()
            and work_dir.is_dir()
        ):
            print(
                f"合并写入临时目录未搬回工作区，可在此路径查找或手动复制: {work_dir}",
                file=sys.stderr,
            )
        print(
            "若出现 SafetensorError / I/O error：请检查磁盘空间；"
            "在 Docker 下可设置环境变量 WORKSHOP_MERGE_USE_TMP_STAGING=1。",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        return 1
    meta: dict = {
        "base": args.base,
        "lora_used": str(lora_p),
        "lora_ignored": [str(x) for x in loras[1:]],
        "extra_parsed": json.loads(args.extra or "[]"),
        "merge_lora_only": bool(args.merge_lora_only),
    }
    mj = (os.environ.get("WORKSHOP_MERGE_JOB_ID") or "").strip()
    if mj:
        meta["merge_job_id"] = mj
    (out / "workshop_merge_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"完成: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
