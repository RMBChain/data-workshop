# 介绍

**Data Workshop（数据工坊）** 是一套围绕 **[ms-swift](https://github.com/modelscope/ms-swift)** 的 **Web 操作台**：用浏览器完成数据集准备与管理、训练、模型合并、推理与评测等流程，由 **FastAPI** 后端调度脚本与任务，产出与任务状态落在可配置的**工作区目录**下（默认仓库内 `working_data`，数据库为 `<workspace>/state/workshop.db`）；路径等可通过环境变量 **`WORKSHOP_` 前缀**覆盖（如 `WORKSHOP_WORKSPACE_ROOT`、`WORKSHOP_REPO_ROOT`，详见 `backend/app/config.py`）。

- **前端**：`frontend/` — Vite、Vue 3、TypeScript、Ant Design Vue、Vue Router。
- **后端**：`backend/app/` — FastAPI，`/api` 下提供数据集、训练、合并、评测、模型与系统等相关接口。
- **设计稿**：`design/ui/` 下列出主要界面示意图（下文「画面」）。

更详细的本地/云主机环境、依赖与启动命令见本文 **Quick start** 章节。

# 画面
- 首页
![首页](design/ui/1.Home.bmp)
- 
- 数据集
![数据集](design/ui/2.datasets.bmp)
- 
- 训练中心
![训练中心](design/ui/3.train.bmp)

- 训练中心 · 训练
![训练流程 · 训练](design/ui/3.train1train.bmp)

- 训练中心 · 验证
![训练流程 · 验证](design/ui/3.train2verfy.bmp)

- 训练中心 · 合并
![训练流程 · 合并](design/ui/3.train3merge.bmp)

- 训练中心 · 评测
![训练流程 · 评测](design/ui/3_train4eval.bmp)

- 模型管理
![模型管理](design/ui/4.modelManagement.bmp)

# Quick start
## 环境(autodl.com)
- Miniconda3
- python 3.10.8
- ubuntu22.04
- cuda 11.8

## 主机信息
- CPU ：12 核心
- 内存：62 GB
- GPU ：NVIDIA GeForce RTX 4080 SUPER, 1
- 系 统 盘/               ：1% 53M/30G
- 数 据 盘/root/autodl-tmp：1% 12K/50G

## 登录信息（免密登录）
- ssh -p 17531 root@connect.bjb1.seetacloud.com

## 下载代码
```bash
cd /
# git clone git@git-spooner:68a7e1d75ca26351a77c73b9/
git clone git@github.com:RMBChain/data-workshop.git

```

## label studio
```bash
docker pull heartexlabs/label-studio:20260421.012345-main-a5c6f37
docker rm -f label-studio
docker run -it -d --restart always --name label-studio -p 8080:8080 \
  -e LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true \
  heartexlabs/label-studio:20260421.012345-main-a5c6f37
docker logs -f label-studio

curl http://39.101.168.205:8080

# label-studio: http://39.101.168.205:8080/
# label-studio: xwhoyeah@sohu.com/RUI_887Ytewr
# label-studio: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6ODA4NDg1MDYzMCwiaWF0IjoxNzc3NjUwNjMwLCJqdGkiOiJlYmNhMzNhNzJlZWI0YzYzYTBjZmE0MGUxZTVhYTQ3ZSIsInVzZXJfaWQiOiIxIn0.w09mb8nPF1BdBAo2OYVqSEC7nqkULRx70Au8Rf2mXok

```

## backend
- Python 依赖清单：仓库根目录 `requirements.txt`（标准 pip 要求格式；安装见下）。
- 创建环境
```bash
cd /data-workshop
conda deactivate 
conda env list
conda remove --name data-workshop-py3.10 --all -y
conda create -n data-workshop-py3.10 python=3.10.8 -y
source /root/miniconda3/etc/profile.d/conda.sh
conda activate data-workshop-py3.10

export PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple/

pip install --no-cache-dir uv -i  ${PYPI_INDEX}
# uv cache clean
uv venv
uv pip install pip setuptools wheel -i ${PYPI_INDEX}
uv pip install -r requirements.txt -i ${PYPI_INDEX}

```

- 运行
```bash
cd /data-workshop
conda activate data-workshop-py3.10
uv run uvicorn backend.app.main:app --app-dir /data-workshop --host "0.0.0.0" --port "8702" --reload

```

## frontend
- 安装nvm
```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
# 新开终端，或在本终端执行：
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm install 24.15.0

```

- 开发
```bash
cd /data-workshop/frontend
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm use 24.15.0
npm install
npm run dev -- --host 0.0.0.0 --port 6006

curl http://localhost:6006
curl https://u871016-gxz9-10f52e8a.bjb1.seetacloud.com:8443

```
