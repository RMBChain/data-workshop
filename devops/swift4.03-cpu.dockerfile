# 使用轻量级 Python 镜像
FROM python:3.11

# 1. 安装系统依赖 (包括 SSH 和 sudo)
# 设置非交互模式，避免安装 SSH 时卡住
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openssh-server \
        sudo \
        vim \
        iproute2 \
    && rm -rf /var/lib/apt/lists/*

# 2. 配置 SSH 服务
# 创建运行 SSHD 所需的目录
RUN mkdir -p /var/run/sshd

# 允许 root 登录 (为了简单，默认用 root)，并允许密码登录；远程调试 SSH 监听 5622
RUN sed -i 's/^#Port 22/Port 5622/' /etc/ssh/sshd_config 2>/dev/null || true; \
    sed -i 's/^Port 22$/Port 5622/' /etc/ssh/sshd_config 2>/dev/null || true; \
    grep -qE '^Port[[:space:]]+5622' /etc/ssh/sshd_config || echo 'Port 5622' >> /etc/ssh/sshd_config && \
    echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config && \
    echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config && \
    # 修改 root 密码为 123456 (请在生产环境中修改为强密码)
    echo 'root:123456' | chpasswd

# 3. 安装 SWIFT 及其 Python 依赖
# 创建工作目录
WORKDIR /workspace

# 安装 SWIFT 及依赖（与 Readme_dev.md 本地 CPU 步骤一致：uv + 固定版本）
# 1. uv；（可选）清理缓存
# 2. PyTorch CPU 三件套（需 >=2.6：transformers 5.x 会 import torch.distributed.fsdp.FSDPModule，2.4/2.5 无此符号）
# 3. ms-swift 4.0.3
# 4. 其余依赖（纯 CPU，与文档 pip 快照一致）
# 5. 清理 uv 缓存
# PyPI 国内镜像（阿里云）；可 --build-arg PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple/
ARG PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir uv -i ${PYPI_INDEX}
# RUN uv cache clean
# 虚拟环境在 /workspace/.venv。运行容器时请把代码挂到 /workspace/project（见 Readme），勿 -v 覆盖整个 /workspace，否则 .venv 会被宿主目录遮住。
RUN mkdir -p /workspace/project
RUN uv venv
# uv 默认可能不在 .venv/bin 下生成 pip 可执行文件（依赖用 uv pip 装），此处显式安装，便于 pip 命令与 /usr/local 软链
RUN uv pip install pip setuptools wheel -i ${PYPI_INDEX}
ENV VIRTUAL_ENV=/workspace/.venv
# SSH 默认 PATH 常不含 /usr/local/bin，须写全；venv 放最前
ENV PATH="/workspace/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin"
# PyTorch 官方 whl/cpu 是 PEP503 索引，可用 -i；阿里云 pytorch-wheels/cpu 是目录页，须用 --find-links（-f），不能用 -i
# 官方备用：--build-arg PYTORCH_CPU_FIND_LINKS=https://download.pytorch.org/whl/cpu
ARG PYTORCH_CPU_FIND_LINKS=https://mirrors.aliyun.com/pytorch-wheels/cpu
RUN uv pip install torch==2.6.0+cpu torchvision==0.21.0+cpu torchaudio==2.6.0+cpu \
  -f ${PYTORCH_CPU_FIND_LINKS} \
  -i ${PYPI_INDEX}
RUN uv pip install "ms-swift[all]==4.0.3"            -i ${PYPI_INDEX}
RUN uv pip install opencv-python-headless==4.13.0.92 -i ${PYPI_INDEX}
RUN uv pip install transformers==5.3.0               -i ${PYPI_INDEX}
RUN uv pip install qwen_vl_utils==0.0.14             -i ${PYPI_INDEX}
RUN uv pip install tensorboard==2.20.0               -i ${PYPI_INDEX}
RUN uv pip install decord==0.6.0                     -i ${PYPI_INDEX}
RUN uv pip install cleanvision==0.3.7                -i ${PYPI_INDEX}
RUN uv pip install jinja2==3.1.6                     -i ${PYPI_INDEX}
# 与 data-workshop workshop-requirements 一致；即使纯 CPU 也会 import torch.cuda，用官方 nvidia-ml-py 替代弃用的 pynvml 包，避免 FutureWarning
RUN uv pip install "nvidia-ml-py>=12.560.0"         -i ${PYPI_INDEX}
# RUN uv cache clean

RUN printf '%s\n' \
  'export VIRTUAL_ENV=/workspace/.venv' \
  'export PATH="/workspace/.venv/bin:/usr/local/bin:/usr/local/sbin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"' \
  > /etc/profile.d/swift-venv.sh && chmod 644 /etc/profile.d/swift-venv.sh

RUN printf '%s\n' \
  '' \
  '# /workspace/.venv（与 profile.d 一致）' \
  'export VIRTUAL_ENV=/workspace/.venv' \
  'export PATH="/workspace/.venv/bin:/usr/local/bin:/usr/local/sbin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"' \
  >> /root/.bashrc

# SSH 常不加载 profile / .bashrc，仍会命中 /usr/local/bin 里的 pip；链到 venv 后默认即虚拟环境
# 须解析到真实解释器路径：venv 里 python 常指向 /usr/local/bin/python3，若直接 ln -s venv/python
# 到 /usr/local/bin/python3 会形成两节点软链环，pip  shebang 报 Too many levels of symbolic links
RUN REAL_PY="$(readlink -f /workspace/.venv/bin/python)" && \
    ln -sf "$REAL_PY" /usr/local/bin/python && \
    ln -sf "$REAL_PY" /usr/local/bin/python3 && \
    { [ "$REAL_PY" = "/usr/local/bin/python3.11" ] || ln -sf "$REAL_PY" /usr/local/bin/python3.11; } && \
    ln -sf /workspace/.venv/bin/pip /usr/local/bin/pip && \
    ln -sf /workspace/.venv/bin/pip /usr/local/bin/pip3

# 4. 暴露端口
# 远程调试 SSH 5622，SWIFT Web UI 常用 7860
EXPOSE 5622 7860

# 5. 启动脚本 (关键：同时启动 SSH 和保持容器运行)
RUN echo '#!/bin/bash\n\
# 启动 SSH 后台服务\n\
/usr/sbin/sshd -D &\n\
# 保持容器运行 (防止容器退出)\n\
wait\n\
' > /start.sh && chmod +x /start.sh

# 设置默认命令
CMD ["/start.sh"]
