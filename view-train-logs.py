# 需要 pandas 环境
import json
import pandas as pd

filepath = "./output/qwen3vl-2b-lora/v0-20260212-211008/logging.jsonl"
records = []

with open(filepath, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except:
                pass  # 跳过坏行

if records:
    df = pd.DataFrame(records)
    print(df)
    
    # 常用操作示例：
    print("\nLoss 变化趋势：")
    print(df[['global_step/max_steps', 'loss', 'epoch']].to_string(index=False))
    
    print("\n最近5条：")
    print(df.tail(5))
else:
    print("文件中没有有效记录")
