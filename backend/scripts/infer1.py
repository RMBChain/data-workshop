#!/usr/bin/env python3
"""
推理脚本 - 测试微调后的Qwen3-VL模型
支持图片和文本输入的推理测试
"""
from pathlib import Path
import torch
from modelscope import snapshot_download
from qwen_vl_utils import process_vision_info
from transformers import Qwen3VLForConditionalGeneration, AutoProcessor
from peft import PeftModel

def load_model():
    """加载checkpoint-6 LoRA适配器模型"""
    print("正在加载模型...")

    checkpoint_path = "./output/qwen3vl-2b-lora/v0-20260212-211008/checkpoint-15"

    if not Path(checkpoint_path).exists():
        raise FileNotFoundError(f"错误: checkpoint-6目录不存在: {checkpoint_path}")

    # 检查是否包含必要的LoRA文件
    required_files = ["adapter_config.json", "adapter_model.safetensors"]
    missing_files = [f for f in required_files if not Path(checkpoint_path, f).exists()]

    if missing_files:
        raise FileNotFoundError(f"错误: checkpoint-6缺少必要文件: {missing_files}")

    # 使用基础模型 + checkpoint-6 LoRA适配器
    base_model_path = snapshot_download('Qwen/Qwen3-VL-2B-Instruct')
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        base_model_path, dtype="auto", device_map="auto"
    )
    model = PeftModel.from_pretrained(model, checkpoint_path)
    processor = AutoProcessor.from_pretrained(base_model_path)

    print("模型加载完成！")
    return model, processor

def inference_image(model, processor, image_path, prompt, max_new_tokens=256):
    """图片推理"""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image_path},
                {"type": "text", "text": prompt}
            ]
        }
    ]

    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info([messages])

    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        return_tensors="pt"
    ).to(model.device)

    with torch.no_grad():
        generated_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            temperature=None,
            top_p=None
        )

    generated_ids_trimmed = [
        out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
    ]
    output_text = processor.batch_decode(
        generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
    )

    return output_text[0]

def main():
    # 配置参数（直接在代码中设置）
    image_path = "data/cable-001.jpeg"  # 测试图片路径
    prompt = "请描述这张图片中的电缆状况，包括是否有损坏、弯曲或异常情况。"  # 推理提示词
    max_new_tokens = 256  # 最大生成token数

    # 检查图片文件是否存在
    if not Path(image_path).exists():
        print(f"错误: 图片文件不存在: {image_path}")
        return

    try:
        # 加载checkpoint-6模型
        model, processor = load_model()

        # 进行推理
        print(f"正在分析图片: {image_path}")
        print(f"提示词: {prompt}")
        print("-" * 50)

        result = inference_image(model, processor, image_path, prompt, max_new_tokens)

        print("推理结果:")
        print(result)
        print("-" * 50)

    except Exception as e:
        print(f"推理过程中出现错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()