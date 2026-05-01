# quicek start
- label studio
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


- backend
```bash
cd /data-workshop
conda activate data-workshop-py3.10
uv run uvicorn backend.app.main:app --app-dir /data-workshop --host "0.0.0.0" --port "8702" --reload

```

- frontend
```bash
cd /data-workshop/frontend
npm run dev -- --host 0.0.0.0 --port 6006

curl https://u871016-c7kl-88856538.bjb1.seetacloud.com:8443/
```

# 环境(autodl.com)
- Miniconda3
- python 3.10.8
- ubuntu22.04
- cuda 11.8

# 主机信息
- CPU ：12 核心
- 内存：62 GB
- GPU ：NVIDIA GeForce RTX 4080 SUPER, 1
- 系 统 盘/               ：1% 53M/30G
- 数 据 盘/root/autodl-tmp：1% 12K/50G

# 登录信息（已设置免密登录）
- ssh -p 17531 root@connect.bjb1.seetacloud.com


# 代码
- 下载代码
```bash
cd /
git clone git@git-spooner:68a7e1d75ca26351a77c73b9/llm-train-learning/data-workshop.git
```

# 配置软件依赖
- backend
```bash
cd /data-workshop
conda deactivate 
conda env list
conda remove --name data-workshop-py3.10 --all -y
conda create -n data-workshop-py3.10 python=3.10.8 -y
source /root/miniconda3/etc/profile.d/conda.sh
conda activate data-workshop-py3.10

export PYTORCH_CPU_FIND_LINKS=https://mirrors.aliyun.com/pytorch-wheels/cpu
export PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple/

pip install --no-cache-dir uv -i  ${PYPI_INDEX}
# uv cache clean
uv venv
uv pip install pip setuptools wheel -i ${PYPI_INDEX}
uv pip install torch==2.6.0+cpu torchvision==0.21.0+cpu torchaudio==2.6.0+cpu -f ${PYTORCH_CPU_FIND_LINKS} -i ${PYPI_INDEX}
uv pip install "ms-swift[all]==4.0.3"   -i ${PYPI_INDEX}
uv pip install opencv-python-headless==4.13.0.92   -i ${PYPI_INDEX}
uv pip install transformers==5.3.0   -i ${PYPI_INDEX}
uv pip install "huggingface_hub>=0.20"   -i ${PYPI_INDEX}
uv pip install qwen_vl_utils==0.0.14   -i ${PYPI_INDEX}
uv pip install tensorboard==2.20.0   -i ${PYPI_INDEX}
uv pip install decord==0.6.0   -i ${PYPI_INDEX}
uv pip install cleanvision==0.3.7   -i ${PYPI_INDEX}
uv pip install jinja2==3.1.6   -i ${PYPI_INDEX}
uv pip install "bitsandbytes>=0.43.0"   -i ${PYPI_INDEX}
uv pip install "nvidia-ml-py>=12.560.0"   -i ${PYPI_INDEX}
uv pip install "fastapi>=0.115"   -i ${PYPI_INDEX}
uv pip install "uvicorn[standard]>=0.32"   -i ${PYPI_INDEX}
uv pip install python-multipart   -i ${PYPI_INDEX}
uv pip install pyyaml   -i ${PYPI_INDEX}
uv pip install httpx   -i ${PYPI_INDEX}
uv pip install "pydantic-settings>=2.0"   -i ${PYPI_INDEX}
uv pip install "psutil>=5.9"   -i ${PYPI_INDEX}
```

- frontend
```bash
rm -rf ~/.nvm
git clone https://gitclone.com/github.com/nvm-sh/nvm.git ~/.nvm && cd ~/.nvm && git checkout `git describe --abbrev=0 --tags`
echo 'export NVM_DIR="$HOME/.nvm"' >> ~/.bashrc
echo '[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"' >> ~/.bashrc
echo '[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"' >> ~/.bashrc

source ~/.bashrc
nvm ls-remote
nvm install 24.15.0
nvm alias default 24.15.0
nvm use 24.15.0

cd /data-workshop/frontend
npm install

```





