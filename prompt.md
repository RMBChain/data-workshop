你现在是一个专业的 ms-swift + Qwen-VL 微调工程师。

任务：为我创建一个用于微调 Qwen3-VL 的工程模板（目标模型：Qwen/Qwen3-VL-2B，如果没有就用最新的 2B/4B 视觉指令模型）。

要求：
1. 使用 LoRA 微调（sft_type: lora），lora_rank 建议 8~32
2. 冻结 vision tower（freeze_vit: true），只训 LLM 部分和 projector（如果需要）
3. 支持图片输入，数据集格式为 ms-swift 推荐的 messages + images 格式
4. 包含一个非常详细的 README.md，里面有：
   - 环境安装命令（包括 flash-attn, ms-swift）
   - 单卡 & 双卡启动示例（使用 CUDA_VISIBLE_DEVICES 和 NPROC_PER_NODE）
   - 重要环境变量说明（MAX_PIXELS, IMAGE_MAX_TOKEN_NUM, VIDEO_MAX_TOKEN_NUM 等）
   - 推荐的超参（学习率 5e-5 ~ 2e-4, batch size, gradient accumulation 等）
   - 数据集格式详细说明（带例子）
   - 推理命令示例（swift infer 或 vllm）
5. 在 config/ 目录下生成一个 yaml 配置文件，文件名 lora_qwen3vl.yaml，内容要包含：
   - model_type: qwen3_vl （或实际支持的 type）
   - model_id_or_path: Qwen/Qwen3-VL-4B-Instruct
   - train_type: lora
   - sft_type: lora （或 full 如果要全参）
   - freeze_vit: true
   - lora_rank: 16
   - lora_alpha: 32
   - target_modules: all-linear （或更精细的模块选择）
   - dataset: data/example_train.jsonl
   - val_dataset: data/example_val.jsonl
   - output_dir: output/qwen3vl-lora
   - learning_rate: 1e-4
   - per_device_train_batch_size: 2
   - gradient_accumulation_steps: 4
   - max_length: 2048
   - num_train_epochs: 3
   - logging_steps: 5
   - save_steps: 200
   - eval_steps: 200
   - system: "You are a helpful vision-language assistant." （可选）
   - 其他推荐加速参数（deepspeed, flash_attn, bf16 等）

6. 在 data/ 目录下生成两个示例 jsonl 文件，展示如何放图片路径、对话格式、可能的多图情况。

7. 写一个简单的 infer.py，支持加载 LoRA 权重后进行图片+文本推理。

请按照这个结构完整生成所有文件内容，并用 markdown 代码块展示每个文件。