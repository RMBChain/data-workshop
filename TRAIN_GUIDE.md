# Qwen3-VL-2B-Instruct 训练指南

本指南介绍如何使用提供的Python脚本对Qwen3-VL-2B-Instruct进行LoRA微调。

## 快速开始

### 1. 准备数据

首先运行数据准备脚本，将`data`文件夹中的图片转换为训练所需的JSONL格式：

```bash
python prepare_data.py
```

这将生成：
- `data/train.jsonl` - 训练集（默认80%）
- `data/val.jsonl` - 验证集（默认20%）

### 2. 开始训练

#### 方式一：使用简化脚本（推荐）

直接运行简化版训练脚本：

```bash
python train_simple.py
```

可以在`train_simple.py`中修改配置参数。

#### 方式二：使用完整脚本（支持命令行参数）

```bash
# 单卡训练
python train.py

# 多卡训练（2卡）
python train.py --num_gpus 2 --per_device_train_batch_size 1

# 自定义参数
python train.py \
    --lora_rank 32 \
    --learning_rate 2e-4 \
    --num_train_epochs 5 \
    --per_device_train_batch_size 1 \
    --gradient_accumulation_steps 8
```

#### 方式三：直接使用swift命令行

```bash
# 设置环境变量
set IMAGE_MAX_TOKEN_NUM=1024
set VIDEO_MAX_TOKEN_NUM=128
set CUDA_VISIBLE_DEVICES=0

# 运行训练
swift sft \
  --model Qwen/Qwen3-VL-2B-Instruct \
  --train_type lora \
  --dataset data/train.jsonl \
  --val_dataset data/val.jsonl \
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
  --output_dir output/qwen3vl-2b-lora \
  --logging_steps 5 \
  --save_steps 200 \
  --eval_steps 200 \
  --save_total_limit 2
```

## 配置说明

### 数据准备脚本 (prepare_data.py)

可以修改以下参数：

```python
prepare_dataset(
    data_dir="data",              # 图片文件夹路径
    output_train="data/train.jsonl",  # 训练集输出路径
    output_val="data/val.jsonl",      # 验证集输出路径
    val_ratio=0.2                     # 验证集比例（0.2表示20%）
)
```

### 训练脚本配置 (train_simple.py)

主要配置参数：

```python
CONFIG = {
    # 模型和数据
    'model': 'Qwen/Qwen3-VL-2B-Instruct',
    'train_dataset': 'data/train.jsonl',
    'val_dataset': 'data/val.jsonl',
    'output_dir': 'output/qwen3vl-2b-lora',
    
    # GPU配置
    'num_gpus': 1,  # 使用的GPU数量
    
    # LoRA配置
    'lora_rank': 16,           # LoRA秩，越大模型容量越大但显存占用更多
    'lora_alpha': 32,          # LoRA alpha，通常是rank的2倍
    'target_modules': 'all-linear',  # 目标模块
    'freeze_vit': True,        # 冻结视觉编码器
    
    # 训练配置
    'num_train_epochs': 3,
    'per_device_train_batch_size': 2,
    'gradient_accumulation_steps': 4,
    'learning_rate': 1e-4,
    
    # 其他配置
    'max_length': 2048,
    'gradient_checkpointing': True,  # 节省显存
}
```

## 显存优化建议

### 低显存（16GB）

```python
'lora_rank': 8,
'per_device_train_batch_size': 1,
'gradient_accumulation_steps': 8,
'gradient_checkpointing': True,
```

### 中等显存（24GB）

```python
'lora_rank': 16,
'per_device_train_batch_size': 2,
'gradient_accumulation_steps': 4,
'gradient_checkpointing': True,
```

### 高显存（40GB+）

```python
'lora_rank': 32,
'per_device_train_batch_size': 4,
'gradient_accumulation_steps': 2,
'gradient_checkpointing': False,
```

## 多卡训练

修改`train_simple.py`中的`num_gpus`参数：

```python
'num_gpus': 2,  # 使用2张GPU
```

或者使用命令行：

```bash
python train.py --num_gpus 2 --per_device_train_batch_size 1
```

## 训练输出

训练完成后，模型会保存在`output/qwen3vl-2b-lora`目录下：

```
output/qwen3vl-2b-lora/
├── checkpoint-200/
│   ├── adapter_config.json
│   ├── adapter_model.bin
│   └── ...
├── checkpoint-400/
│   └── ...
└── ...
```

## 推理

训练完成后，可以使用以下命令进行推理：

```bash
# 使用swift infer
set IMAGE_MAX_TOKEN_NUM=1024
swift infer \
  --model Qwen/Qwen3-VL-2B-Instruct \
  --adapters output/qwen3vl-2b-lora/checkpoint-xxx \
  --stream true
```

## 常见问题

1. **显存不足 (OOM)**
   - 减小`per_device_train_batch_size`
   - 增大`gradient_accumulation_steps`
   - 减小`lora_rank`
   - 启用`gradient_checkpointing`

2. **训练速度慢**
   - 确保安装了`flash-attn`
   - 使用多卡训练
   - 增大`per_device_train_batch_size`（如果显存允许）

3. **数据集格式错误**
   - 确保先运行`prepare_data.py`
   - 检查JSONL文件格式是否正确

## 注意事项

1. 确保已安装ms-swift和相关依赖（参考README.md）
2. 确保有足够的显存（建议至少16GB）
3. 训练过程中会定期保存检查点，可以随时中断和恢复
4. 建议先用小数据集测试，确认配置正确后再进行完整训练
