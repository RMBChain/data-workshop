from __future__ import annotations

import gc
import threading
from pathlib import Path

from backend.app.services.paths import resolve_under_workspace

_lock = threading.Lock()
_bundle: tuple[str, str | None, object, object] | None = None


def _resolve_base_dir(workspace: Path, base_model: str) -> str:
    from modelscope import snapshot_download

    p = Path(base_model)
    if p.is_absolute():
        rp = p.resolve()
        if rp.is_dir():
            return str(rp)
        raise FileNotFoundError(f"本地模型目录不存在: {base_model}")
    try:
        rel = resolve_under_workspace(workspace, base_model)
        if rel.is_dir():
            return str(rel)
    except ValueError:
        pass
    return snapshot_download(base_model)


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


def infer_image(
    workspace: Path,
    image_path: Path,
    prompt: str,
    *,
    base_model: str = "Qwen/Qwen3-VL-2B-Instruct",
    adapter_rel: str | None = None,
    max_new_tokens: int = 256,
) -> str:
    import torch
    from qwen_vl_utils import process_vision_info

    model, processor = ensure_model(workspace, base_model=base_model, adapter_rel=adapter_rel)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": str(image_path.resolve())},
                {"type": "text", "text": prompt},
            ],
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
