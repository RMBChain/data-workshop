# Qwen3-VL LoRA 微调工程模板

这是一个使用 **ms-swift** 对 **Qwen3-VL** (2B/4B/8B) 进行 LoRA 微调的完整工程模板。

## 目录

- [环境安装](#环境安装)
- [快速开始](#快速开始)
- [数据集格式](#数据集格式)
- [训练](#训练)
- [推理](#推理)
- [环境变量](#环境变量)
- [超参数推荐](#超参数推荐)
- [常见问题](#常见问题)

---

## 环境安装

### 1. 安装依赖

```bash
# 创建 conda 环境（推荐）
conda create -n qwen3vl python=3.11.14 -y
conda activate qwen3vl

# 安装 PyTorch（根据你的 CUDA 版本选择）
# CUDA 12.1:
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# CUDA 11.8:
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# 安装 ms-swift（最新版本）
pip install "ms-swift>=4.0" -U

# 安装 transformers 和其他依赖
pip install "transformers>=4.57" "qwen_vl_utils>=0.0.14" -U

# 安装 flash-attention（强烈推荐，用于加速和节省显存）
pip install flash-attn --no-build-isolation

# 可选：安装 deepspeed 用于多卡训练
pip install deepspeed

# 可选：安装 vllm 用于推理加速
pip install "vllm>=0.11.0"

# 可选：安装 liger-kernel 用于节省显存
pip install liger-kernel
```

### 2. 验证安装

```bash
python -c "import swift; print('ms-swift:', swift.__version__)"
python -c "import transformers; print('transformers:', transformers.__version__)"
python -c "import torch; print('torch:', torch.__version__); print('CUDA:', torch.cuda.is_available())"
```

---

## 快速开始

### 单卡训练

```bash
CUDA_VISIBLE_DEVICES=0 \
IMAGE_MAX_TOKEN_NUM=1024 \
VIDEO_MAX_TOKEN_NUM=128 \
swift sft \
  --model Qwen/Qwen3-VL-4B-Instruct \
  --train_type lora \
  --dataset data/example_train.jsonl \
  --val_dataset data/example_val.jsonl \
  --torch_dtype bfloat16 \
  --attn_impl flash_attn \
  --num_train_epochs 3 \
  --per_device_train_batch_size 2 \
  --per_device_eval_batch_size 1 \
  --gradient_accumulation_steps 4 \
  --learning_rate 1e-4 \
  --lora_rank 16 \
  --lora_alpha 32 \
  --target_modules all-linear \
  --freeze_vit true \
  --max_length 2048 \
  --output_dir output/qwen3vl-lora \
  --logging_steps 5 \
  --save_steps 200 \
  --eval_steps 200 \
  --save_total_limit 2
```

### 多卡训练（双卡）

```bash
NPROC_PER_NODE=2 \
CUDA_VISIBLE_DEVICES=0,1 \
IMAGE_MAX_TOKEN_NUM=1024 \
VIDEO_MAX_TOKEN_NUM=128 \
swift sft \
  --model Qwen/Qwen3-VL-4B-Instruct \
  --train_type lora \
  --dataset data/example_train.jsonl \
  --val_dataset data/example_val.jsonl \
  --torch_dtype bfloat16 \
  --attn_impl flash_attn \
  --num_train_epochs 3 \
  --per_device_train_batch_size 1 \
  --gradient_accumulation_steps 4 \
  --learning_rate 1e-4 \
  --lora_rank 16 \
  --lora_alpha 32 \
  --target_modules all-linear \
  --freeze_vit true \
  --max_length 2048 \
  --output_dir output/qwen3vl-lora \
  --logging_steps 5 \
  --save_steps 200 \
  --eval_steps 200
```

### 使用配置文件

```bash
CUDA_VISIBLE_DEVICES=0,1 \
NPROC_PER_NODE=2 \
IMAGE_MAX_TOKEN_NUM=1024 \
swift sft --config config/lora_qwen3vl.yaml
```

---

## 数据集格式

ms-swift 使用 JSONL 格式，包含 `messages` 和 `images` 字段。

### 基础格式

```json
{
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful vision-language assistant."
    },
    {
      "role": "user",
      "content": [
        {"type": "image", "image": "path/to/image.jpg"},
        {"type": "text", "text": "这张图片里有什么？"}
      ]
    },
    {
      "role": "assistant",
      "content": "图片展示了一幅美丽的山景日落。"
    }
  ]
}
```

### 单图示例

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "image", "image": "images/cat.jpg"},
        {"type": "text", "text": "描述这只动物。"}
      ]
    },
    {
      "role": "assistant",
      "content": "这是一只可爱的橘白相间的猫咪，正坐在沙发上。"
    }
  ]
}
```

### 多图示例

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "image", "image": "images/before.jpg"},
        {"type": "image", "image": "images/after.jpg"},
        {"type": "text", "text": "这两张图片有什么不同？"}
      ]
    },
    {
      "role": "assistant",
      "content": "主要区别是第二张图片的停车场里多了一辆红色轿车，而第一张图片里没有。"
    }
  ]
}
```

### OCR / 文档理解示例

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "image", "image": "documents/invoice.png"},
        {"type": "text", "text": "从这张发票中提取总金额。"}
      ]
    },
    {
      "role": "assistant",
      "content": "总金额：¥8,500.00"
    }
  ]
}
```

### 带系统提示词

```json
{
  "messages": [
    {
      "role": "system",
      "content": "你是一位医学影像专家，请仔细分析提供的医学影像。"
    },
    {
      "role": "user",
      "content": [
        {"type": "image", "image": "medical/xray.jpg"},
        {"type": "text", "text": "这张X光片有什么异常？"}
      ]
    },
    {
      "role": "assistant",
      "content": "X光片显示远端桡骨有轻微骨折..."
    }
  ]
}
```

### 多轮对话

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "image", "image": "images/product.jpg"},
        {"type": "text", "text": "这是什么产品？"}
      ]
    },
    {
      "role": "assistant",
      "content": "这是一款无线蓝牙耳机。"
    },
    {
      "role": "user",
      "content": "它是什么颜色的？通常多少钱？"
    },
    {
      "role": "assistant",
      "content": "这款耳机是黑银配色的。类似型号通常售价在300-1000元之间，具体取决于品牌和功能。"
    }
  ]
}
```

**注意：** 图片路径可以是：
- 相对路径（相对于 JSONL 文件位置）
- 绝对路径
- URL 链接（http/https）

---

## 训练

### 1. 命令行参数说明

| 参数 | 说明 | 推荐值 |
|------|------|--------|
| `--model` | 模型名称或路径 | `Qwen/Qwen3-VL-4B-Instruct` |
| `--train_type` | 训练类型 | `lora` |
| `--dataset` | 训练数据集路径 | `data/train.jsonl` |
| `--val_dataset` | 验证数据集路径 | `data/val.jsonl` |
| `--torch_dtype` | 数据类型 | `bfloat16` |
| `--attn_impl` | Attention 实现方式 | `flash_attn` |
| `--num_train_epochs` | 训练轮数 | `3` |
| `--per_device_train_batch_size` | 每卡 batch size | `1-4` |
| `--gradient_accumulation_steps` | 梯度累积步数 | `4-8` |
| `--learning_rate` | 学习率 | `1e-4` ~ `2e-4` |
| `--lora_rank` | LoRA 秩 | `8-32` |
| `--lora_alpha` | LoRA alpha | `16-64`（通常是 rank 的 2 倍） |
| `--target_modules` | LoRA 目标模块 | `all-linear` |
| `--freeze_vit` | 冻结视觉编码器 | `true` |
| `--max_length` | 最大序列长度 | `2048-4096` |
| `--output_dir` | 输出目录 | `output/qwen3vl-lora` |
| `--logging_steps` | 每 N 步记录日志 | `5-10` |
| `--save_steps` | 每 N 步保存检查点 | `200-500` |
| `--eval_steps` | 每 N 步评估 | `200-500` |
| `--save_total_limit` | 最多保留检查点数 | `2-5` |
| `--warmup_ratio` | 预热比例 | `0.03-0.1` |
| `--lr_scheduler_type` | 学习率调度器 | `cosine` |
| `--gradient_checkpointing` | 梯度检查点 | `true`（节省显存） |
| `--packing` | 序列打包 | `false`（设为 true 加速） |

### 2. LoRA 配置

根据 GPU 显存推荐的配置：

**低显存（16GB）：**
```bash
--lora_rank 8 \
--lora_alpha 16 \
--target_modules all-linear \
--per_device_train_batch_size 1 \
--gradient_accumulation_steps 8 \
--gradient_checkpointing true
```

**中等显存（24GB）：**
```bash
--lora_rank 16 \
--lora_alpha 32 \
--target_modules all-linear \
--per_device_train_batch_size 2 \
--gradient_accumulation_steps 4 \
--gradient_checkpointing true
```

**高显存（40GB+）：**
```bash
--lora_rank 32 \
--lora_alpha 64 \
--target_modules all-linear \
--per_device_train_batch_size 4 \
--gradient_accumulation_steps 2
```

### 3. 高级：DeepSpeed 集成

创建 `ds_config_zero2.json`：

```json
{
  "bf16": {
    "enabled": true
  },
  "zero_optimization": {
    "stage": 2,
    "offload_optimizer": {
      "device": "cpu",
      "pin_memory": true
    },
    "allgather_partitions": true,
    "allgather_bucket_size": 2e8,
    "overlap_comm": true,
    "reduce_scatter": true,
    "reduce_bucket_size": 2e8,
    "contiguous_gradients": true
  },
  "train_batch_size": "auto",
  "train_micro_batch_size_per_gpu": "auto",
  "gradient_accumulation_steps": "auto",
  "wall_clock_breakdown": false
}
```

使用 DeepSpeed 训练：

```bash
NPROC_PER_NODE=4 \
CUDA_VISIBLE_DEVICES=0,1,2,3 \
IMAGE_MAX_TOKEN_NUM=1024 \
swift sft \
  --model Qwen/Qwen3-VL-4B-Instruct \
  --train_type lora \
  --dataset data/example_train.jsonl \
  --deepspeed config/ds_config_zero2.json \
  ...
```

**重要：** LoRA 训练只能使用 ZeRO-2，ZeRO-3 与 LoRA 不兼容。

---

## 推理

### 1. 使用 swift infer（推荐）

训练完成后进行推理：

```bash
CUDA_VISIBLE_DEVICES=0 \
IMAGE_MAX_TOKEN_NUM=1024 \
swift infer \
  --model output/qwen3vl-lora/checkpoint-xxx \
  --stream true
```

使用基础模型 + LoRA 适配器：

```bash
CUDA_VISIBLE_DEVICES=0 \
IMAGE_MAX_TOKEN_NUM=1024 \
swift infer \
  --model Qwen/Qwen3-VL-4B-Instruct \
  --adapters output/qwen3vl-lora/checkpoint-xxx \
  --stream true
```

### 2. 使用 vLLM 加速

```bash
CUDA_VISIBLE_DEVICES=0 \
IMAGE_MAX_TOKEN_NUM=1024 \
swift infer \
  --model output/qwen3vl-lora/checkpoint-xxx \
  --infer_backend vllm \
  --stream true \
  --max_new_tokens 512
```

### 3. 使用 Python 脚本（infer.py）

项目中的 `infer.py` 提供了程序化推理示例：

```bash
python infer.py \
  --model_path output/qwen3vl-lora/checkpoint-xxx \
  --image_path images/test.jpg \
  --prompt "详细描述这张图片。"
```

### 4. 合并 LoRA 权重

将 LoRA 权重合并到基础模型以便于部署：

```bash
swift export \
  --adapters output/qwen3vl-lora/checkpoint-xxx \
  --merge_lora true \
  --output_dir output/qwen3vl-merged
```

---

## 环境变量

### Qwen3-VL 关键变量

| 变量名 | 默认值 | 说明 | 推荐值 |
|--------|--------|------|--------|
| `IMAGE_MAX_TOKEN_NUM` | - | 图片最大 token 数 | `1024`（训练），`2048`（高分辨率） |
| `VIDEO_MAX_TOKEN_NUM` | - | 视频最大 token 数 | `128`（视频理解） |
| `FPS_MAX_FRAMES` | - | 视频最大帧数 | `16`（视频理解） |
| `MAX_PIXELS` | - | 图片最大像素数 | `1003520`（预处理） |
| `PYTORCH_CUDA_ALLOC_CONF` | - | CUDA 内存配置 | `expandable_segments:True`（防 OOM） |

### 其他重要变量

| 变量名 | 说明 |
|--------|------|
| `CUDA_VISIBLE_DEVICES` | 指定使用的 GPU（如 `0,1,2,3`） |
| `NPROC_PER_NODE` | 每节点进程数（多卡训练） |
| `MASTER_ADDR` | 主节点地址（分布式训练） |
| `MASTER_PORT` | 主节点端口（分布式训练） |
| `SWIFT_PATCH_CONV3D` | 设为 `1` 修复 PyTorch 2.9 训练慢的问题 |
| `TRANSFORMERS_OFFLINE` | 设为 `1` 启用离线模式 |
| `HF_DATASETS_OFFLINE` | 设为 `1` 启用离线模式 |

### 配置示例

```bash
export IMAGE_MAX_TOKEN_NUM=1024
export VIDEO_MAX_TOKEN_NUM=128
export FPS_MAX_FRAMES=16
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"
export CUDA_VISIBLE_DEVICES=0,1
export NPROC_PER_NODE=2
```

---

## 超参数推荐

### Qwen3-VL 推荐配置

| 场景 | LoRA Rank | LoRA Alpha | 学习率 | Batch Size | 轮数 |
|------|-----------|------------|--------|------------|------|
| 快速实验 | 8 | 16 | 2e-4 | 1-2 | 1-2 |
| 标准微调 | 16 | 32 | 1e-4 | 2 | 3 |
| 高质量 | 32 | 64 | 5e-5 | 2-4 | 3-5 |
| 领域适应 | 64 | 128 | 1e-4 | 1 | 5 |

### 学习率指南

- **LoRA (rank 8-16)：** `1e-4` ~ `2e-4`
- **LoRA (rank 32-64)：** `5e-5` ~ `1e-4`
- **全参数微调：** `1e-5` ~ `5e-5`

### Batch Size 指南

- **单卡 A10 (24GB)：** `per_device_train_batch_size=1`，`gradient_accumulation_steps=8`
- **单卡 A100 (40GB)：** `per_device_train_batch_size=2`，`gradient_accumulation_steps=4`
- **单卡 A100 (80GB)：** `per_device_train_batch_size=4`，`gradient_accumulation_steps=2`
- **多卡训练：** 相应调整 batch size

---

## 常见问题

### 1. 显存不足 (OOM)

**解决方案：**
- 启用梯度检查点：`--gradient_checkpointing true`
- 减小 batch size：`--per_device_train_batch_size 1`
- 减小 LoRA rank：`--lora_rank 8`
- 减小最大长度：`--max_length 1024`
- 启用 DeepSpeed ZeRO-2
- 设置 `PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"`

### 2. 训练速度慢

**解决方案：**
- 安装 flash-attention：`--attn_impl flash_attn`
- 启用打包：`--packing true`（可能影响收敛）
- PyTorch 2.9 设置 `SWIFT_PATCH_CONV3D=1`
- 使用多卡训练

### 3. 模型不收敛

**解决方案：**
- 增大学习率（尝试 `2e-4`）
- 减小 LoRA rank（尝试 `8` 或 `16`）
- 检查数据集格式
- 确保标签正确

### 4. LoRA 输出与基础模型相同

**问题：** 微调后的模型输出与基础模型相同。

**解决方案：**
- 确保推理时正确加载了 LoRA 权重
- 尝试合并 LoRA 权重：`swift export --merge_lora true`
- 检查 `target_modules` 是否覆盖了正确的层
- 增大 LoRA rank

### 5. 视频训练卡住

**解决方案：**
- 使用 torchcodec 替代 decord 进行视频处理
- 减小 `VIDEO_MAX_TOKEN_NUM` 和 `FPS_MAX_FRAMES`

### 6. 导入错误

```bash
# 如果出现 "No module named 'qwen_vl_utils'"
pip install "qwen_vl_utils>=0.0.14"

# 如果出现 flash-attention 错误
pip uninstall flash-attn
pip install flash-attn --no-build-isolation
```

---

## 项目结构

```
.
├── README.md                     # 本文件
├── config/
│   ├── lora_qwen3vl.yaml        # 训练配置文件
│   └── ds_config_zero2.json     # DeepSpeed 配置
├── data/
│   ├── example_train.jsonl      # 训练数据示例
│   └── example_val.jsonl        # 验证数据示例
├── infer.py                      # 推理脚本
├── output/                       # 训练输出（训练时自动创建）
└── scripts/
    ├── train_single_gpu.sh      # 单卡训练脚本
    └── train_multi_gpu.sh       # 多卡训练脚本
```

---

## 参考资料

- [ms-swift 官方文档](https://swift.readthedocs.io/)
- [Qwen3-VL 最佳实践](https://swift.readthedocs.io/en/latest/BestPractices/Qwen3-VL-Best-Practice.html)
- [Qwen3-VL GitHub](https://github.com/QwenLM/Qwen3-VL)
- [Qwen3-VL HuggingFace](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct)

---

## 许可证

本模板仅供教育和研究目的使用。请遵守 ms-swift、Qwen3-VL 及其他依赖库的许可证。

---

## 引用

如果你在研究中使用了本模板，请引用：

```bibtex
@software{ms-swift,
  title = {ms-swift: A Scalable lightWeight Infrastructure for Fine-Tuning},
  author = {ModelScope},
  year = {2025}
}

@article{qwen3vl,
  title = {Qwen3-VL: A Vision-Language Model Series},
  author = {Qwen Team},
  year = {2025}
}
```
