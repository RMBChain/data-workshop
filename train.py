"""
使用ms-swift对Qwen3-VL-2B-Instruct进行LoRA微调
支持CPU和GPU训练，自动检测并调整参数
"""
import os
import sys
import subprocess
import torch
from pathlib import Path

def train_with_swift(
    model_name="Qwen/Qwen3-VL-2B-Instruct",
    train_dataset="data/train.jsonl",
    val_dataset="data/val.jsonl",
    output_dir="output/qwen3vl-2b-lora",
    num_gpus=None,  # None表示自动检测
    **kwargs
):
    """
    使用ms-swift进行训练
    
    Args:
        model_name: 模型名称
        train_dataset: 训练数据集路径
        val_dataset: 验证数据集路径
        output_dir: 输出目录
        num_gpus: GPU数量，None表示自动检测
        **kwargs: 其他训练参数
    """
    
    # 检测GPU可用性
    has_gpu = torch.cuda.is_available()
    if num_gpus is None:
        num_gpus = torch.cuda.device_count() if has_gpu else 0
    use_cpu = not has_gpu or num_gpus == 0
    
    # 根据是否有GPU自动调整参数
    if use_cpu:
        kwargs.setdefault('torch_dtype', 'float32')
        kwargs.setdefault('attn_impl', 'eager')
        kwargs.setdefault('image_max_token_num', 512)
        kwargs.setdefault('max_length', 1024)
        kwargs.setdefault('per_device_train_batch_size', 1)
        kwargs.setdefault('gradient_accumulation_steps', 8)
        kwargs.setdefault('lora_rank', 8)
        kwargs.setdefault('lora_alpha', 16)
    else:
        kwargs.setdefault('torch_dtype', 'bfloat16')
        kwargs.setdefault('attn_impl', 'flash_attn')
        kwargs.setdefault('image_max_token_num', 1024)
        kwargs.setdefault('max_length', 2048)
        kwargs.setdefault('per_device_train_batch_size', 2)
        kwargs.setdefault('gradient_accumulation_steps', 4)
        kwargs.setdefault('lora_rank', 16)
        kwargs.setdefault('lora_alpha', 32)
    
    # 设置环境变量
    env = os.environ.copy()
    env['IMAGE_MAX_TOKEN_NUM'] = str(kwargs.get('image_max_token_num', 1024))
    env['VIDEO_MAX_TOKEN_NUM'] = str(kwargs.get('video_max_token_num', 128))
    
    # 设置CUDA设备（仅在有GPU时设置）
    if use_cpu:
        # CPU训练：不设置CUDA相关环境变量
        env.pop('CUDA_VISIBLE_DEVICES', None)
        env.pop('NPROC_PER_NODE', None)
    else:
        # GPU训练：设置CUDA设备
        if num_gpus > 1:
            cuda_devices = ','.join([str(i) for i in range(num_gpus)])
            env['CUDA_VISIBLE_DEVICES'] = cuda_devices
            env['NPROC_PER_NODE'] = str(num_gpus)
        else:
            env['CUDA_VISIBLE_DEVICES'] = '0'
    
    # 构建swift sft命令
    cmd = ['swift', 'sft']
    
    # 基本参数
    cmd.extend(['--model', model_name])
    cmd.extend(['--train_type', 'lora'])
    cmd.extend(['--dataset', train_dataset])
    cmd.extend(['--val_dataset', val_dataset])
    cmd.extend(['--output_dir', output_dir])
    
    # LoRA参数
    cmd.extend(['--lora_rank', str(kwargs.get('lora_rank', 16))])
    cmd.extend(['--lora_alpha', str(kwargs.get('lora_alpha', 32))])
    cmd.extend(['--target_modules', kwargs.get('target_modules', 'all-linear')])
    cmd.extend(['--freeze_vit', str(kwargs.get('freeze_vit', 'true')).lower()])
    
    # 训练参数
    cmd.extend(['--num_train_epochs', str(kwargs.get('num_train_epochs', 3))])
    cmd.extend(['--per_device_train_batch_size', str(kwargs.get('per_device_train_batch_size', 2))])
    cmd.extend(['--per_device_eval_batch_size', str(kwargs.get('per_device_eval_batch_size', 1))])
    cmd.extend(['--gradient_accumulation_steps', str(kwargs.get('gradient_accumulation_steps', 4))])
    cmd.extend(['--learning_rate', str(kwargs.get('learning_rate', 1e-4))])
    
    # 其他参数（已根据CPU/GPU自动调整）
    cmd.extend(['--torch_dtype', kwargs.get('torch_dtype', 'float32' if use_cpu else 'bfloat16')])
    cmd.extend(['--attn_impl', kwargs.get('attn_impl', 'eager' if use_cpu else 'flash_attn')])
    cmd.extend(['--max_length', str(kwargs.get('max_length', 1024 if use_cpu else 2048))])
    cmd.extend(['--logging_steps', str(kwargs.get('logging_steps', 5))])
    cmd.extend(['--save_steps', str(kwargs.get('save_steps', 200))])
    cmd.extend(['--eval_steps', str(kwargs.get('eval_steps', 200))])
    cmd.extend(['--save_total_limit', str(kwargs.get('save_total_limit', 2))])
    
    # 可选参数
    if kwargs.get('warmup_ratio'):
        cmd.extend(['--warmup_ratio', str(kwargs['warmup_ratio'])])
    
    if kwargs.get('lr_scheduler_type'):
        cmd.extend(['--lr_scheduler_type', kwargs['lr_scheduler_type']])
    
    if kwargs.get('gradient_checkpointing'):
        cmd.extend(['--gradient_checkpointing', str(kwargs['gradient_checkpointing']).lower()])
    
    if kwargs.get('packing'):
        cmd.extend(['--packing', str(kwargs['packing']).lower()])
    
    # 打印命令
    print("=" * 80)
    print("训练配置:")
    print(f"  设备: {'CPU' if use_cpu else f'GPU ({num_gpus}张)'}")
    print(f"  模型: {model_name}")
    print(f"  数据类型: {kwargs.get('torch_dtype', 'float32' if use_cpu else 'bfloat16')}")
    print(f"  Attention实现: {kwargs.get('attn_impl', 'eager' if use_cpu else 'flash_attn')}")
    print(f"  最大长度: {kwargs.get('max_length', 1024 if use_cpu else 2048)}")
    if use_cpu:
        print(f"\n  注意: CPU训练速度较慢，建议使用GPU以获得更好的性能")
    print("=" * 80)
    print(f"\n环境变量:")
    print(f"  IMAGE_MAX_TOKEN_NUM={env['IMAGE_MAX_TOKEN_NUM']}")
    print(f"  VIDEO_MAX_TOKEN_NUM={env['VIDEO_MAX_TOKEN_NUM']}")
    if not use_cpu:
        print(f"  CUDA_VISIBLE_DEVICES={env.get('CUDA_VISIBLE_DEVICES', '0')}")
        if num_gpus > 1:
            print(f"  NPROC_PER_NODE={env['NPROC_PER_NODE']}")
    print("=" * 80)
    print()
    
    # 执行训练
    try:
        subprocess.run(cmd, env=env, check=True)
        print("\n训练完成！")
        print(f"模型保存在: {output_dir}")
    except subprocess.CalledProcessError as e:
        print(f"\n训练失败: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n训练被用户中断")
        sys.exit(1)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='使用ms-swift训练Qwen3-VL-2B-Instruct')
    
    # 基本参数
    parser.add_argument('--model', type=str, default='Qwen/Qwen3-VL-2B-Instruct',
                       help='模型名称或路径')
    parser.add_argument('--train_dataset', type=str, default='data/train.jsonl',
                       help='训练数据集路径')
    parser.add_argument('--val_dataset', type=str, default='data/val.jsonl',
                       help='验证数据集路径')
    parser.add_argument('--output_dir', type=str, default='output/qwen3vl-2b-lora',
                       help='输出目录')
    parser.add_argument('--num_gpus', type=int, default=None,
                       help='使用的GPU数量，None表示自动检测（默认：自动检测）')
    
    # LoRA参数
    parser.add_argument('--lora_rank', type=int, default=16,
                       help='LoRA rank')
    parser.add_argument('--lora_alpha', type=int, default=32,
                       help='LoRA alpha')
    parser.add_argument('--target_modules', type=str, default='all-linear',
                       help='LoRA目标模块')
    parser.add_argument('--freeze_vit', type=bool, default=True,
                       help='是否冻结视觉编码器')
    
    # 训练参数
    parser.add_argument('--num_train_epochs', type=int, default=3,
                       help='训练轮数')
    parser.add_argument('--per_device_train_batch_size', type=int, default=2,
                       help='每卡训练batch size')
    parser.add_argument('--per_device_eval_batch_size', type=int, default=1,
                       help='每卡验证batch size')
    parser.add_argument('--gradient_accumulation_steps', type=int, default=4,
                       help='梯度累积步数')
    parser.add_argument('--learning_rate', type=float, default=1e-4,
                       help='学习率')
    
    # 其他参数
    parser.add_argument('--torch_dtype', type=str, default='bfloat16',
                       choices=['float32', 'float16', 'bfloat16'],
                       help='数据类型')
    parser.add_argument('--attn_impl', type=str, default='flash_attn',
                       choices=['flash_attn', 'sdpa', 'eager'],
                       help='Attention实现方式')
    parser.add_argument('--max_length', type=int, default=2048,
                       help='最大序列长度')
    parser.add_argument('--logging_steps', type=int, default=5,
                       help='日志记录步数')
    parser.add_argument('--save_steps', type=int, default=200,
                       help='保存检查点步数')
    parser.add_argument('--eval_steps', type=int, default=200,
                       help='评估步数')
    parser.add_argument('--save_total_limit', type=int, default=2,
                       help='最多保留的检查点数')
    parser.add_argument('--warmup_ratio', type=float, default=0.03,
                       help='预热比例')
    parser.add_argument('--lr_scheduler_type', type=str, default='cosine',
                       help='学习率调度器类型')
    parser.add_argument('--gradient_checkpointing', type=bool, default=True,
                       help='是否启用梯度检查点')
    parser.add_argument('--packing', type=bool, default=False,
                       help='是否启用序列打包')
    parser.add_argument('--image_max_token_num', type=int, default=1024,
                       help='图片最大token数')
    parser.add_argument('--video_max_token_num', type=int, default=128,
                       help='视频最大token数')
    
    args = parser.parse_args()
    
    # 检查数据集是否存在
    if not Path(args.train_dataset).exists():
        print(f"错误: 训练数据集不存在: {args.train_dataset}")
        print("请先运行 prepare_data.py 准备数据")
        sys.exit(1)
    
    if not Path(args.val_dataset).exists():
        print(f"错误: 验证数据集不存在: {args.val_dataset}")
        print("请先运行 prepare_data.py 准备数据")
        sys.exit(1)
    
    # 转换为字典
    train_kwargs = vars(args)
    train_kwargs.pop('model')
    train_kwargs.pop('train_dataset')
    train_kwargs.pop('val_dataset')
    train_kwargs.pop('output_dir')
    train_kwargs.pop('num_gpus')
    
    # 开始训练
    train_with_swift(
        model_name=args.model,
        train_dataset=args.train_dataset,
        val_dataset=args.val_dataset,
        output_dir=args.output_dir,
        num_gpus=args.num_gpus,
        **train_kwargs
    )


if __name__ == "__main__":
    main()
