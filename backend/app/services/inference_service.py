from __future__ import annotations

import gc
import logging
import threading
from pathlib import Path

from backend.app.services import modelscope_manager as mscm
from backend.app.services.paths import resolve_under_workspace

_lock = threading.Lock()
_bundle: tuple[str, str | None, object, object] | None = None
_log = logging.getLogger("workshop")


def _resolve_base_dir(workspace: Path, base_model: str) -> str:
    from modelscope import snapshot_download

    p = Path(base_model)
    if p.is_absolute():
        rp = p.resolve()
        if rp.is_dir():
            _log.info("推理基座：本地绝对路径（未使用魔搭下载）%s", rp)
            return str(rp)
        raise FileNotFoundError(f"本地模型目录不存在: {base_model}")
    try:
        rel = resolve_under_workspace(workspace, base_model)
        if rel.is_dir():
            _log.info("推理基座：工作区相对路径（未使用魔搭下载）%s", rel)
            return str(rel)
    except ValueError:
        pass
    hub = mscm.hub_model_dir_if_cached(base_model)
    if hub is not None:
        _log.info(
            "推理基座：使用本机魔搭缓存（不会为此再跑网络下载；随后「Loading weights」为读盘进内存）%s",
            hub,
        )
        return str(hub)
    _log.warning(
        "推理基座：未在 %s 下找到已缓存模型，将调用 snapshot_download（可能联网）：%s",
        mscm.modelscope_cache_dir(),
        base_model,
    )
    out = snapshot_download(base_model)
    _log.info("snapshot_download 完成，目录：%s", out)
    return out


def ensure_model(
    workspace: Path,
    *,
    base_model: str = "Qwen/Qwen3-VL-2B-Instruct",
    adapter_rel: str | None = None,
) -> tuple[object, object]:
    """在 CPU 上懒加载模型（单例，按 base+adapter 维度缓存）。依赖 torch/transformers 等，仅在调用时加载。"""
    import torch
    from peft import PeftModel
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

    global _bundle
    key = (base_model, adapter_rel)
    with _lock:
        if _bundle is not None and (_bundle[0], _bundle[1]) == key:
            return _bundle[2], _bundle[3]

        if _bundle is not None:
            del _bundle
            gc.collect()
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass

        base_path = _resolve_base_dir(workspace, base_model)
        _log.info(
            "正在将基座权重从磁盘载入内存（进度条「Loading weights」属此步骤，非重新下载；体量大约数 GB 时首次需数十秒）"
        )
        model = Qwen3VLForConditionalGeneration.from_pretrained(
            base_path,
            torch_dtype=torch.float32,
            trust_remote_code=True,
        )
        model = model.to("cpu")
        if adapter_rel:
            adapter_path = resolve_under_workspace(workspace, adapter_rel)
            if not adapter_path.is_dir():
                raise FileNotFoundError(f"LoRA 目录不存在: {adapter_rel}")
            model = PeftModel.from_pretrained(model, str(adapter_path))
        processor = AutoProcessor.from_pretrained(base_path, trust_remote_code=True)
        model.eval()
        _bundle = (base_model, adapter_rel, model, processor)
        return model, processor


def preload_model(
    workspace: Path,
    *,
    base_model: str = "Qwen/Qwen3-VL-2B-Instruct",
    adapter_rel: str | None = None,
) -> None:
    """将 base+LoRA 载入进程内单例缓存，供后续 infer_image 复用（首次等同完整「Loading weights」）。"""
    ensure_model(workspace, base_model=base_model, adapter_rel=adapter_rel)


def unload_cached_model() -> None:
    """释放进程内已缓存的模型与 processor。"""
    import torch

    global _bundle
    with _lock:
        if _bundle is not None:
            del _bundle
            _bundle = None
        gc.collect()
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass


def infer_image(
    workspace: Path,
    image_path: Path | None,
    prompt: str,
    *,
    base_model: str = "Qwen/Qwen3-VL-2B-Instruct",
    adapter_rel: str | None = None,
    max_new_tokens: int = 256,
) -> str:
    import torch
    from qwen_vl_utils import process_vision_info

    model, processor = ensure_model(workspace, base_model=base_model, adapter_rel=adapter_rel)
    if image_path is not None:
        user_content: list[dict[str, str]] = [
            {"type": "image", "image": str(image_path.resolve())},
            {"type": "text", "text": prompt},
        ]
    else:
        user_content = [{"type": "text", "text": prompt}]
    messages = [
        {
            "role": "user",
            "content": user_content,
        }
    ]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info([messages])
    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        return_tensors="pt",
    )
    inputs = inputs.to(model.device)

    with torch.no_grad():
        generated_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            temperature=None,
            top_p=None,
        )

    generated_ids_trimmed = [
        out_ids[len(in_ids) :] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
    ]
    output_text = processor.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )
    return output_text[0]
