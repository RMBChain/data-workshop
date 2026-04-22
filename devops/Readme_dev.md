# modelscope 官网
- https://swift.readthedocs.io/zh-cn/latest/index.html
- 

# Model
- Qwen/Qwen3-VL-2B-Instruct
- Qwen/Qwen3-VL-2B-Thinking
- Qwen/Qwen3-VL-4B-Instruct
- Qwen/Qwen3-VL-4B-Thinking
- Qwen/Qwen3-VL-8B-Instruct
- Qwen/Qwen3-VL-8B-Thinking

# 创建 swift4.0.3 开发调试环境image（纯 CPU · Docker）
```bash
# 编译镜像（构建上下文为仓库根目录）
cmd
cd /d D:/_git/codeup-spooner/llm-train-learning/data-workshop/devops
docker pull python:3.11
docker build -f swift4.03-cpu.dockerfile -t swift4.03-cpu:latest .

```

# 创建 swift4.0.3 开发调试环境（纯 CPU · Docker）
```bash
# 运行：SSH 5622、SWIFT Web UI 7860。
# 重要：代码挂到 /workspace/project，不要挂到 /workspace 根目录。
# 若 -v 宿主: /workspace，会盖住镜像里 /workspace/.venv，容器里就只剩系统 python、无依赖。
cmd
cd /d D:/_git/codeup-spooner/llm-train-learning/data-workshop
docker rm -f swift403-cpu
# cmd 续行：行末 ^，且 ^ 后不能有空格
# --shm-size：默认 /dev/shm 仅约 64MB，训练时 DataLoader 多进程易报 bus error；与 backend/scripts/train.py 默认 dataloader_num_workers=0 配合更稳
docker run -it -d --name swift403-cpu                            ^
           --hostname swift403-cpu                               ^
           --shm-size=4g                                         ^
           -p 5622:5622 -p 7860:7860                             ^
           -v "%CD%":/workspace/project                          ^
           -v "C:\_llm_model\modelscope":/root/.cache/modelscope ^
           swift4.03-cpu:latest

```

# 远程调试登录（root 密码见 123456 ）
## 方式1
```bash
ssh -p 5622 root@127.0.0.1
cd /workspace/project
pip list
```

## 方式2
```bash
docker exec -it -w /workspace/project swift403-cpu bash
docker exec -it -w /workspace/project swift403-cpu swift web-ui --server_name 0.0.0.0 --server_port 7860

```

# 训练
## step1 数据清洗（在容器内、项目根目录）
```bash
cd /workspace/project
python backend/scripts/cleanData.py

```

## step2 准备数据
```bash
docker exec -it -w /workspace/project swift403-cpu python backend/scripts/prepare_data.py
```

## step3 使用ms-swift进行训练
```bash
docker exec -it -w /workspace/project swift403-cpu python backend/scripts/train.py --model Qwen/Qwen3-VL-2B-Instruct

```

## TensorBoard 查看结果
```bash
docker exec -it -w /workspace/project swift403-cpu tensorboard --logdir output/qwen3vl-2b-lora/v0-20260212-154649/runs --port 6006
curl 6006

```

## 查看 logging.jsonl
```bash
docker exec -it -w /workspace/project swift403-cpu python backend/scripts/view-train-logs.py

```

## 测试模型（使用未合并的模型）
```bash
docker exec -it -w /workspace/project swift403-cpu python backend/scripts/infer1.py

```

## 合并LoRA权重（用于部署）
```bash
docker exec -it -w /workspace/project swift403-cpu python backend/scripts/merge_lora.py

```

## 测试模型（使用合并后的模型）
```bash
docker exec -it -w /workspace/project swift403-cpu python backend/scripts/infer2.py

```
