#!/usr/bin/env python3
"""
合并LoRA权重到基础模型
"""
from pathlib import Path
from modelscope import snapshot_download
from transformers import Qwen3VLForConditionalGeneration, AutoProcessor
from peft import PeftModel

def merge_lora_weights():
    """合并LoRA权重到基础模型"""
    print("开始合并LoRA权重...")

    # 设置路径
    base_model_id = 'Qwen/Qwen3-VL-2B-Instruct'
    adapter_path = './output/qwen3vl-2b-lora/v0-20260212-211008/checkpoint-15'
    output_dir = './output/merged-model'

    # 检查adapter路径是否存在
    if not Path(adapter_path).exists():
        raise FileNotFoundError(f"LoRA适配器路径不存在: {adapter_path}")

    # 检查必要的LoRA文件
    required_files = ["adapter_config.json", "adapter_model.safetensors"]
    missing_files = [f for f in required_files if not Path(adapter_path, f).exists()]
    if missing_files:
        raise FileNotFoundError(f"LoRA适配器缺少必要文件: {missing_files}")

    print(f"加载基础模型: {base_model_id}")
    # 下载并加载基础模型
    base_model_path = snapshot_download(base_model_id)
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        base_model_path, dtype="auto", device_map="auto"
    )

    print(f"加载LoRA适配器: {adapter_path}")
    # 加载LoRA适配器
    model = PeftModel.from_pretrained(model, adapter_path)

    print("合并LoRA权重...")
    # 合并LoRA权重到基础模型
    merged_model = model.merge_and_unload()

    print(f"保存合并后的模型到: {output_dir}")
    # 创建输出目录
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 保存合并后的模型
    merged_model.save_pretrained(output_dir, safe_serialization=True)

    # 保存processor
    processor = AutoProcessor.from_pretrained(base_model_path)
    processor.save_pretrained(output_dir)

    print("LoRA权重合并完成！")
    print(f"合并后的模型保存在: {output_dir}")

if __name__ == "__main__":
    try:
        merge_lora_weights()
    except Exception as e:
        print(f"合并过程中出现错误: {e}")
        import traceback
        traceback.print_exc()