# Prompt
- 进入python环境请先运行： conda activate qwen3vl-swift-py3.11

# Model
- Qwen/Qwen3-VL-2B-Instruct
- Qwen/Qwen3-VL-2B-Thinking
- Qwen/Qwen3-VL-4B-Instruct
- Qwen/Qwen3-VL-4B-Thinking
- Qwen/Qwen3-VL-8B-Instruct
- Qwen/Qwen3-VL-8B-Thinking

# swift
## swift - python
```bash
d: && cd D:\_git\codeup-spooner\qwen3-vl-finetuning
conda deactivate && conda remove --name qwen3vl-swift-py3.11 --all -y
conda create -n qwen3vl-swift-py3.11 python=3.11.14 -y
conda activate qwen3vl-swift-py3.11
pip install transformers>=4.57     -i http://localhost:3141/root/pypi/+simple/ --trusted-host=localhost
pip install ms-swift[all]>=4.0 -U  -i http://localhost:3141/root/pypi/+simple/ --trusted-host=localhost
pip install qwen_vl_utils>=0.0.14  -i http://localhost:3141/root/pypi/+simple/ --trusted-host=localhost
pip install tensorboard       -i http://localhost:3141/root/pypi/+simple/ --trusted-host=localhost
pip install "decord" -U       -i http://localhost:3141/root/pypi/+simple/ --trusted-host=localhost

# for cleanvision
pip install cleanvision       -i http://localhost:3141/root/pypi/+simple/ --trusted-host=localhost
pip install pandas jinja2 matplotlib    -i http://localhost:3141/root/pypi/+simple/ --trusted-host=localhost

```

## 数据清洗（??????）
```bash
d: && cd D:\_git\codeup-spooner\llm-train-learning\qwen3-vl-finetuning
conda activate qwen3vl-swift-py3.11
python cleanData.py

```

## 准备数据
```bash
d: && cd D:\_git\codeup-spooner\llm-train-learning\qwen3-vl-finetuning
conda activate qwen3vl-swift-py3.11
python prepare_data.py
```

## TensorBoard 查看结果
```bash
d: && cd D:\_git\codeup-spooner\llm-train-learning\qwen3-vl-finetuning
conda activate qwen3vl-swift-py3.11
tensorboard --logdir output/qwen3vl-2b-lora/v0-20260212-154649/runs --port 6006
curl 6006

```

## 查看 logging.jsonl
```bash
d: && cd D:\_git\codeup-spooner\llm-train-learning\qwen3-vl-finetuning
conda activate qwen3vl-swift-py3.11
python view-train-logs.py

```

## 测试模型（使用未合并的模型）
```bash
d: && cd D:\_git\codeup-spooner\llm-train-learning\qwen3-vl-finetuning
conda activate qwen3vl-swift-py3.11
python infer1.py --image_path "data/cable-001.jpeg" --prompt "请描述这张图片中的电缆状况，包括是否有损坏、弯曲或异常情况。"

```

## 合并LoRA权重（用于部署）
```bash
d: && cd D:\_git\codeup-spooner\llm-train-learning\qwen3-vl-finetuning
conda activate qwen3vl-swift-py3.11
python merge_lora.py

```

## 测试模型（使用合并后的模型）
```bash
d: && cd D:\_git\codeup-spooner\llm-train-learning\qwen3-vl-finetuning
conda activate qwen3vl-swift-py3.11
python infer2.py --image_path "data/cable-001.jpeg" --prompt "请描述这张图片中的电缆状况，包括是否有损坏、弯曲或异常情况。"

```

# swift web-ui
```bash
set SSL_CERT_FILE=
set REQUESTS_CA_BUNDLE=
swift web-ui --server_name 0.0.0.0 --server_port 9860
curl http://localhos:9860

```

