# 数据工坊（Data Workshop）— 图片多模态 MVP

## 项目定位
面向**图片多模态大模型**（如 Qwen-VL、InternVL、LLaVA）的轻量级开发工具，将 ms-swift 从数据处理到模型合并的全流程封装为图形化界面。
**核心目标：**
- 开发者将精力集中在数据质量和模型策略上
- 无需在复杂的命令行操作和环境配置中耗费时间
- 真正实现“开箱即用”的大模型开发体验

**范围限定：**
- 专注于图片+文本的多模态任务（VQA、图像描述、OCR 等）
- **微调（SFT/LoRA）与推理、合并后试跑、评测**均在 **纯 CPU** 上执行（`torch` 使用 CPU 后端，不依赖 GPU/CUDA 跑主路径）
- ms-swift 版本为4.0.3；开发与生产镜像以 `devops/swift4.03-cpu.dockerfile` 为基
- 训练与推理相关任务在容器内运行
- 仅支持本机运行

## 一、数据导入
### 1.1 数据源
| 项 | 说明 |
|----|------|
| Label Studio | API 拉取项目；原图在 LS 侧，本系统仅持导入后的元数据与可解析路径。 |

### 1.2 SFT 字段示例（与导入结果一致时）
```json
{
  "images": ["/path/to/image.jpg"],
  "conversations": [
    {"from": "human", "value": "<image>这张图里有什么？"},
    {"from": "gpt", "value": "图中有一只猫坐在沙发上。"}
  ]
}
```
```jsonl
{"messages": [{"role": "user", "content": [{"type": "image", "image": "data/cable-010.png"}, {"type": "text", "text": "请描述图片中的内容。"}]}, {"role": "assistant", "content": [{"type": "text", "text": "这张图片展示了电缆相关的图像内容。"}]}]}
{"messages": [{"role": "user", "content": [{"type": "image", "image": "data/cable-024.jpg"}, {"type": "text", "text": "请描述图片中的内容。"}]}, {"role": "assistant", "content": [{"type": "text", "text": "这张图片展示了电缆相关的图像内容。"}]}]}
```

## 二、数据集（构建与划分）
- **指令集构建**：界面模板将数据转为 SFT；自动补 `<image>`；对话模板用 **qwen-vl**（与 §3.1 模型一致）。
- **划分**：按比例切分训练/验证/测试，可选固定随机种子。
- **版本**：每次处理生成可备注快照，可回滚、导出；`{workspace}/versions/`。

## 三、训练
### 3.1 基础模型
| 模型 | 参数量 | 约需内存（LoRA） |
|------|--------|------------------|
| Qwen3-VL-2B-Instruct | 2B | ~10GB |
| Qwen3-VL-2B-Thinking | 2B | ~10GB |
来源：ModelScope 拉取，或本机已下载路径。

### 3.2 任务与参数
- 任务类型：**SFT**。
- 表单+滑块可调（示例值）：LoRA rank 8/16/32、alpha 16、target `q_proj,v_proj`、lr 1e-4、batch 1–4、grad accumulation 4、epochs 3、图片分辨率 448/512、max pixels 256×256。
- 导出生成 **YAML**；可再导入、带示例项。

### 3.3 任务与监控
- 队列状态：等待 → 运行 → 成功/失败；可取消、删除、重试。
- 日志：流式、关键词高亮（ERROR/WARNING）、复制。
- 图：训练/验证 **loss**、**学习率**（echart 内嵌）。
- 资源：CPU、内存，定时刷新（如 2s）。

## 四、推理沙盒
- 对话式 UI；加载 **本机训练输出** 的模型/适配器；可切换不重启。
- 图片只引用 **已导入的 Label Studio 项目数据** 或同链路上下文，与 §1.1 一致；本机不上传、无 URL/商城入图。

| 输入 | 方式 |
|------|------|
| 图 | 与已导入 LS 项目可关联的条目/上下文 |

- 对话可保存、恢复、清；可导出为 Markdown/JSON。

## 五、LoRA 合并
- 选 **基础模型路径**、**一个或多个 LoRA 路径**、**输出路径**；执行 `swift merge-lora`；合并后校验加载并可跳转 §4 试跑。

## 六、导出
- 产物以 **HF 兼容的浮点/合并后权重** 为主，随 **ms-swift** 可加载、可再训练/可再推理 为准。  
- **MVP 不采用** 依赖 **CUDA 的 bitsandbytes 4-bit** 等 GPU 路径（易与 **§范围限定** 中纯 CPU 冲突，故单列）。

## 七、评测
- 与 §1.1 同源：经 LS 进入工作区或同导入管线的 **JSONL/问答对**；跑通指标与逐条结果。
- 指标：准确率、BLEU、ROUGE（可开关）；逐条结果列表。
- 简单图表、可导出报告、可留历史（存工作区侧）。

## 八、系统信息（界面展示）
| 项 | 显示 |
|----|------|
| Python、PyTorch 等 | 版本与能力；**若**显示本机 **GPU 信息**，仅表示环境探测，**不参与**本工具内训练/推理（易误解，故说明） |
| 路径 | 模型缓存 `~/.cache/modelscope`；数据 `./data`；输出 `./output` |

## 九、技术栈
| 层级 | 技术 |
|------|------|
| 前端 | Vue3、Ant Design Vue、Vite、TypeScript、echart |
| 后端 | FastAPI；**ms-swift 仅通过 CLI 子进程调用**（`swift` 或 `python -m swift`，与 4.0.3 文档一致），`subprocess` 执行、日志解析与推送、YAML 配置 |
| 持久化 | **SQLite**（MVP 暂用，单文件；任务/导入/版本等元数据；大文件与权重仍落工作区文件系统） |
| 运行 | Docker、nginx、Node 22.22.2（本机只用于开发构建）、`devops/swift4.03-cpu.dockerfile`、ms-swift 4.0.3 |
| 数据流 | 界面操作 → 生成 `swift` 命令 → 子进程执行 → 解析日志/输出 → 刷新界面 |

## 十、本机怎么跑
### Label Studio
单独用 Docker 起，供 §1.1 使用（cmd 勿混用 `^` 与 bash 的 `${PWD}`，详见根目录 `README.md`）。
**PowerShell：**
```powershell
docker pull heartexlabs/label-studio:20260421.012345-main-a5c6f37
docker rm -f label-studio
docker run -it -d --name label-studio -p 127.0.0.1:8080:8080 `
  -e LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true `
  -v "$((Get-Location).Path -replace '\\','/')/label-studio-data:/label-studio/data" `
  heartexlabs/label-studio:20260421.012345-main-a5c6f37

# xwhoyeah@sohu.com/xwhoyeah/RUI_887Ytewr
# eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6ODA4NDAzMjY2NiwiaWF0IjoxNzc2ODMyNjY2LCJqdGkiOiI5MzMzZDU2YzFjNWU0Yjg4YjYwMzA5YmQ0MWFiYzIyZCIsInVzZXJfaWQiOiIxIn0

```
**Bash：**
```bash
docker pull heartexlabs/label-studio:20260421.012345-main-a5c6f37
docker run -d --name label-studio -p 127.0.0.1:8080:8080 \
  -e LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true \
  -v "${PWD}/label-studio-data:/label-studio/data" \
  heartexlabs/label-studio:20260421.012345-main-a5c6f37
```
API 访问宿主机上 LS 的基址默认即为 `http://host.docker.internal:8080`（`backend/app/config.py`），**无需在容器启动时**设置 `WORKSHOP_LABEL_STUDIO_URL`；仅当 LS 基址与默认不同再覆盖（同根目录说明）。

### 服务约定
- 后端：开发与生产均 **Docker**；开发可用 **Compose** 叠加 `devops/docker-compose.dev.yml` 热重载，或 **等价 `docker run`**（见下「仅后端开发」），二者选其一即可。
- 前端：开发本机 `npm run dev`；生产由 `devops/Dockerfile.frontend` 打入 `web` 镜像。

**生产全栈**（`devops/build-compose.ps1` 或）：
```bash
docker build -f devops/swift4.03-cpu.dockerfile -t swift4.03-cpu:latest .
docker compose up -d --build
```
起 `web` + `api`；LS 不在此 Compose 内。

**仅后端开发**
```powershell
docker build -f devops/swift4.03-cpu.dockerfile -t swift4.03-cpu:latest .
docker build -f devops/Dockerfile.workshop -t data-workshop-api:latest .
docker rm -f workshop-api-dev
docker run -d --name workshop-api-dev  `
  --shm-size=4g  `
  -p 127.0.0.1:8702:8000  `
  -v "${PWD}:/workspace/project"  `
  -w /workspace/project  `
  -e WORKSHOP_WORKSPACE_ROOT=/workspace/project  `
  --add-host=host.docker.internal:host-gateway  `
  data-workshop-api:latest  `
  uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```
  PowerShell 下将卷挂载中的 `${PWD}` 换为当前目录的绝对路径（或将仓库根路径写死为 `-v "D:/path/to/data-workshop:/workspace/project"`）。停止/删除：`docker stop workshop-api-dev && docker rm workshop-api-dev`。

**本机前端：** `cd frontend` → `nvm use` → `npm run dev`（**8701**，代理到宿主机 **8702** 上的 API，与上表开发 API 端口一致）。

**端口**（`127.0.0.1`）

**生产**
- UI(nginx) : http://127.0.0.1:8601
- API       : http://127.0.0.1:8602/api，
- health    : http://127.0.0.1:8602/api/health

**开发**
- UI(Vite) : http://127.0.0.1:8701
- API      : http://127.0.0.1:8702/api，
- health   : http://127.0.0.1:8702/api/health

## 十一、使用约束
- 建议本机 **≥16GB 内存**；速度与体验受 **CPU/内存** 影响。
- 不修改 **ms-swift** 核心仓库；问题在其上游追踪。

## 十二、实现约束
- 仓库内：`frontend/`、`backend/`；产品名 **数据工坊**；界面 **仅中文**。
- MVP 交付面：**从 LS 的数据导入、训练派单、日志/曲线、§4 推理**；依赖与 `swift4.03-cpu` 一致。
- 从编码到可测路径由实现侧自行跑通（构建、compose、单测等按仓库约定）。

## 十二、编码
- 参考 Desiign_2_Ui.md
- 参考 Desiign_3_Api.md
