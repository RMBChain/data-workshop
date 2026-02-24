"""
数据准备脚本：将data文件夹中的图片转换为ms-swift所需的JSONL格式
"""
import os
import json
from pathlib import Path
import random

def prepare_dataset(data_dir="data", output_train="data/train.jsonl", output_val="data/val.jsonl", val_ratio=0.2):
    """
    将data文件夹中的图片转换为训练和验证数据集
    
    Args:
        data_dir: 图片文件夹路径
        output_train: 训练集输出路径
        output_val: 验证集输出路径
        val_ratio: 验证集比例
    """
    data_path = Path(data_dir)
    
    # 获取所有图片文件
    image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif'}
    image_files = [f for f in data_path.iterdir() 
                   if f.suffix.lower() in image_extensions]
    
    # 按文件名排序
    image_files.sort()
    
    print(f"找到 {len(image_files)} 张图片")
    
    # 随机打乱并分割训练集和验证集
    random.seed(42)
    random.shuffle(image_files)
    
    split_idx = int(len(image_files) * (1 - val_ratio))
    train_files = image_files[:split_idx]
    val_files = image_files[split_idx:]
    
    print(f"训练集: {len(train_files)} 张")
    print(f"验证集: {len(val_files)} 张")
    
    def create_sample(image_file, prompt="请详细描述这张图片中的内容。", response_prefix="这张图片展示了"):
        """创建单个训练样本"""
        image_path = str(image_file.resolve()).replace('\\', '/')

        return {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image_path},
                        {"type": "text", "text": prompt}
                    ]
                },
                {
                    "role": "assistant",
                    # 为了兼容PyArrow的类型检查，将content也转换为数组格式
                    # 虽然标准格式是字符串，但PyArrow需要类型一致
                    "content": [
                        {"type": "text", "text": f"{response_prefix}电缆相关的图像内容。"}
                    ]
                }
            ]
        }
    
    # 生成训练集
    with open(output_train, 'w', encoding='utf-8') as f:
        for img_file in train_files:
            sample = create_sample(img_file)
            f.write(json.dumps(sample, ensure_ascii=False) + '\n')
    
    # 生成验证集
    with open(output_val, 'w', encoding='utf-8') as f:
        for img_file in val_files:
            sample = create_sample(img_file)
            f.write(json.dumps(sample, ensure_ascii=False) + '\n')
    
    print(f"\n数据集已生成:")
    print(f"  训练集: {output_train}")
    print(f"  验证集: {output_val}")
    
    # 显示示例
    print("\n示例数据格式:")
    example = create_sample(train_files[0] if train_files else image_files[0])
    print(json.dumps(example, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    prepare_dataset()
