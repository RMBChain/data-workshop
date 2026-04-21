# 数据工坊（图片多模态）

面向图片多模态（如 Qwen3-VL）的轻量工具：在 **Docker** 中运行 **ms-swift 4.0.3（CPU）** 与 **FastAPI**，浏览器中完成数据导入、训练任务与推理沙盒。详细产品设计见 `devops/Readme_design.md`。

## 环境要求

- **Windows 11**，**Docker Desktop**（使用 **Linux 容器**）
- **生产 / 后端**：无需在宿主机安装 Python、Node；依赖均在镜像内
- **前端开发（本机）**：**Node 22.22.2**（建议用 [nvm-windows](https://github.com/coreybutler/nvm-windows)，`frontend/.nvmrc` 已指定版本）

## 首次构建 ms-swift CPU 基础镜像

所有 Compose 服务都依赖该镜像，**首次或 Dockerfile 变更后**需要先构建：

```powershell
cd <仓库根目录>
docker build -f devops/swift4.03-cpu.dockerfile -t swift4.03-cpu:latest .
```

## 使用方式概览

| 场景 | 说明 | 推荐命令 |
|------|------|----------|
| **生产全栈**（前端镜像 + API + Label Studio） | 前端在镜像内 `npm ci && build`，宿主机无需 Node | `.\devops\build-compose.ps1` |
| **后端开发**（API 热重载 + Label Studio，不构建生产 Web 镜像） | 改 `backend/` 代码后自动重载 | `.\devops\run-backend-dev.ps1` |
| **前端开发**（本机 Vite） | 需先启动后端容器，再开前端 | 见下文「前端开发」 |

### 生产全栈（Docker）

```powershell
.\devops\build-compose.ps1
```

或：

```bash
docker build -f devops/swift4.03-cpu.dockerfile -t swift4.03-cpu:latest .
docker compose up -d --build
```

### 后端开发（仅 Docker）

```powershell
.\devops\run-backend-dev.ps1
```

或手动（不启动 `web` 服务，节省构建时间）：

```bash
docker compose -f docker-compose.yml -f devops/docker-compose.dev.yml up -d --build label-studio api
```

`api` 使用 `uvicorn --reload`，挂载整个仓库到容器内 `/workspace/project`。

### 前端开发（本机）

1. 先按上一节启动 **后端容器**（映射 `127.0.0.1:8000`）。
2. 新开终端：

```powershell
cd frontend
nvm use
npm install
npm run dev
```

浏览器访问 **http://127.0.0.1:5173**；Vite 已将 `/api` 代理到 **http://127.0.0.1:8000**。

修改前端后无需重启 API；发布静态资源仍由 **`devops/Dockerfile.frontend`** 在 CI/生产构建中完成。

## 服务与端口（默认仅本机）

| 服务 | 地址 | 说明 |
|------|------|------|
| 生产 Web（nginx） | http://127.0.0.1:9000 | `docker compose` 含 `web` 时 |
| API / Swagger | http://127.0.0.1:8000/docs | |
| 健康检查 | http://127.0.0.1:8000/api/health | 含 `ms_swift_available` 等字段 |
| Label Studio | http://127.0.0.1:8080 | |
| 本机前端开发 | http://127.0.0.1:5173 | 仅 `npm run dev` 时 |

## 配置（可选）

复制 `.env.example` 为 `.env` 并按需修改。Compose 中常见变量：

- `WORKSHOP_LABEL_STUDIO_URL`：API 容器内访问 Label Studio 的地址（默认 `http://label-studio:8080`）
- `WORKSHOP_WORKSPACE_ROOT`：容器内项目根（默认 `/workspace/project`）
- `WORKSHOP_CORS_ORIGINS`：允许的前端源（含 `5173`、`9000` 等）

## 仓库结构（与使用相关）

- `backend/app/`：FastAPI 应用
- `backend/requirements.txt`：API 额外依赖列表（本机 `pip install` 也可用）
- `devops/workshop-requirements.txt`：与上一文件**内容一致**，供 `Dockerfile.workshop` 的 `COPY` 使用（避免构建上下文路径问题）
- `backend/scripts/`：训练与数据脚本（例如 **`backend/scripts/train.py`** 由任务调度在仓库根工作目录下执行）
- `frontend/`：Vue3 + Vite + Ant Design Vue
- `devops/`：Dockerfile、Compose 叠加文件、设计文档

## 命令行脚本（容器内、工作目录为仓库根）

示例（与 `devops/Readme_dev.md` 一致，路径已迁至 `backend/scripts/`）：

```bash
python backend/scripts/cleanData.py
python backend/scripts/prepare_data.py
python backend/scripts/train.py --model Qwen/Qwen3-VL-2B-Instruct
```

## 更多文档

- 产品与技术规格：`devops/Readme_design.md`
- 单机 swift 调试容器与训练示例：`devops/Readme_dev.md`
