# 数据工坊（Data Workshop）— 图片多模态 MVP

> **与代码同步**：本需求文档与当前仓库 `frontend/`、`backend/app/` 行为对齐；具体 HTTP 契约见 `Desiign_3_Api.md`，界面结构见 `Desiign_2_Ui.md`。

## 项目定位
面向**图片多模态大模型**（如 Qwen-VL、InternVL、LLaVA）的轻量级开发工具，将 ms-swift 从数据处理到模型合并的全流程封装为图形化界面。
**核心目标：**
- 开发者将精力集中在数据质量和模型策略上
- 无需在复杂的命令行操作和环境配置中耗费时间
- 真正实现“开箱即用”的大模型开发体验

**范围限定：**
- 专注于图片+文本的多模态任务。
- 多模态任务类型示例：**VQA**；**图像描述**；**OCR**（等）。
- **SFT 微调（LoRA）**；**推理**；**合并后试跑**；**评测**均在 **纯 CPU** 上执行（`torch` 使用 CPU 后端，不依赖 GPU/CUDA 跑主路径）。
- ms-swift 版本：**4.0.3**。
- CPU 基镜像见 **`devops/swift4.03-cpu.dockerfile`**。
- 训练与推理相关任务：**在容器内运行**。
- **仅支持本机运行**。

## 一、数据导入
### 1.1 数据源
| 项 | 说明 |
|----|------|
| Label Studio | API 拉取项目；原图在 LS 侧，本系统仅持导入后的元数据与可解析路径。 |

### 1.2 SFT 字段示例（与导入结果一致时）
训练用 JSONL **每行一条** JSON：`system`、`query`、`response`、**`images`**（单张图为单元素列表）。与 **qwen-vl** / **ms-swift** 的 query式多模态样本一致。生成数据集时，可选在 **query** 前自动加 **`<image>`**（与界面「为文本添加 image 标记」一致）。

```jsonl
{"system": "You are a helpful assistant.", "query": "请描述图片中的内容。", "response": "真正的图像描述", "images": ["/workspace/project/.../cable-003.jpg"]}
```

另可参考旧式 ShareGPT/LLaVA 的 `conversations` 形态（本 MVP 以 **上表 query/response 行** 为主）：

```json
{
  "images": ["/path/to/image.jpg"],
  "conversations": [
    {"from": "human", "value": "<image>这张图里有什么？"},
    {"from": "gpt", "value": "图中有一只猫坐在沙发上。"}
  ]
}
```

## 二、数据集（构建与划分）

**指令集构建**
- 界面模板将数据转为 SFT。
- 可选在 `query` 前补 `<image>`。
- 对话模板 **qwen-vl**（与 §3.1 模型一致）。

**划分**
- 按比例切分训练集、验证集、测试集。
- 可选固定随机种子。

**版本**
- 每次处理生成可备注快照。
- 可回滚、可导出。
- 版本快照根路径：`{workspace}/versions/`。

## 三、训练
### 3.1 基础模型
| 模型 | 参数量 | 约需内存（LoRA） |
|------|--------|------------------|
| Qwen3-VL-2B-Instruct | 2B | ~10GB |
| Qwen3-VL-2B-Thinking | 2B | ~10GB |
来源：ModelScope 拉取，或本机已下载路径。

### 3.2 任务与参数
- 任务类型：**SFT**。
- 表单+滑块可调，示例值可按项分别设置：
  - LoRA rank：`8` / `16` / `32`
  - alpha：`16`
  - target：`q_proj,v_proj`
  - 学习率 `lr`：`1e-4`
  - batch：`1–4`
  - grad accumulation：`4`
  - epochs：`3`
  - 图片分辨率：`448` / `512`
  - max pixels：`256×256`
- 导出生成 **YAML**。
- 可再导入 YAML、带示例项。

### 3.3 任务与监控
- **队列状态**：等待 → 运行 → 成功或失败。
- **队列操作**：可取消。
- **队列操作**：可删除。
- **队列操作**：可重试。
- **日志**：流式输出。
- **日志**：关键词高亮（ERROR / WARNING）。
- **日志**：支持复制。
- **曲线图**：训练 / 验证 **loss**。
- **曲线图**：**学习率**（echart 内嵌）。
- **资源监控**：CPU；内存。
- **资源监控**：定时刷新（如每 2s）。

## 四、推理沙盒
- **交互**：对话式 UI。
- **模型**：加载 **本机训练输出**的模型或适配器。
- **切换**：可切换模型且不重启进程。
- **图片来源**：仅 **已导入的 Label Studio 项目数据**。
- **图片来源**：或 **同链路上下文**（与 §1.1 一致）。
- **图片来源**：不包含本机随意上传路径；不包含 URL；不包含商城入图。

**输入与方式**
- **图**：与已导入 LS 项目可关联的 **条目**。
- **上下文**：与 §1.1 一致的 **同链路上下文**。

- **对话**：可保存。
- **对话**：可恢复。
- **对话**：可清空。
- **导出**：Markdown 或 JSON。

## 五、LoRA 合并
- **必须选择**：**基础模型路径**。
- **必须选择**：**一个或多个 LoRA 路径**。
- **必须选择**：**输出路径**。
- **命令**：执行 `swift merge-lora`（由界面代为组装参数）。
- **合并后**：校验加载。
- **合并后**：可跳转 §4 试跑。

## 六、导出
- **主产物**：**HF 兼容**的浮点或合并后权重。
- **验收口径**：可在 **ms-swift** 中加载。
- **后续能力**：在同一工具链视角下「可再训练」「可再推理」为准绳。
- **MVP 不采用**：依赖 **CUDA** 的 **bitsandbytes 4-bit** 等 GPU 专用路径。
- **原因**：易与「§范围限定」中的 **纯 CPU** 心智冲突（故单列说明）。

## 七、评测
- 数据源与 §1.1 同源：经 LS 进入工作区或同导入管线的 **JSONL/问答对**。
- 跑通指标与逐条结果。
- 指标：准确率；BLEU；ROUGE（均可开关）。
- 逐条结果列表。
- 简单图表。
- 可导出报告。
- 可留历史（存工作区侧）。

## 八、系统信息（界面展示）
| 项 | 显示 |
|----|------|
| Python、PyTorch 等 | 版本与能力。**若**展示本机 **GPU 信息**：仅表示环境探测，**不参与**本工具内训练/推理（易误解，故说明）。 |
| 路径 | 见下文「路径说明」。 |

**路径说明**（每项独立展示即可）：
- 模型缓存：`~/.cache/modelscope`（或挂载为容器内同路径）。
- 业务根目录：由 **`WORKSHOP_WORKSPACE_ROOT`** 决定（默认仓库下 **`working_data`**，内含 `versions`、`imports`、`output`、`state`（SQLite）等）。
- 界面可展示 **`GET /api/system/paths`** 的返回结果。

## 九、技术栈

**前端**
- Vue3
- Ant Design Vue
- Vite
- TypeScript
- echart

**后端**
- FastAPI
- **ms-swift 仅通过 CLI 子进程调用**（`swift` 或 `python -m swift`，与 4.0.3 文档一致）
- **`subprocess`** 执行 CLI
- **日志**：解析 stdout/stderr；推送到前端可用通道
- **配置**：YAML 生成与回读

**持久化**
- **SQLite**（单文件）
- **路径**：固定在 **`{workspace}/state/workshop.db`**，`{workspace}` 即 **`WORKSHOP_WORKSPACE_ROOT`**（默认仓库下 **`working_data`**）；无单独环境变量覆盖数据库路径。
- **表名**：业务表均以 **`dws_`** 为前缀（与 `backend/app/db.py` 一致），含 `dws_app_kv`、`dws_datasets`、`dws_trains`、`dws_merge_jobs`、`dws_merge_export_zips`、`dws_eval_jobs`、`dws_inference_sessions`。
- **Docker**：见 **`devops/docker-compose.yml`**（示例中设置 **`WORKSHOP_WORKSPACE_ROOT=/workspace/project/working_data`**，库与同目录产出一并落在宿主 **`working_data/`**）。

**运行**
- Docker（API）
- 本机 **Node ≥22**（前端 `npm run dev`）
- `devops/swift4.03-cpu.dockerfile`
- ms-swift 4.0.3

**数据流**
- 界面操作 → 生成 `swift` 命令 → 子进程执行 → 解析日志/输出 → 刷新界面

## 十、本机怎么跑
### 服务约定（开发）
#### **后端**
- API：http://127.0.0.1:8702/api
- health：http://127.0.0.1:8702/api/health
- 在**仓库根**中运行：
```powershell
# build基础镜像
docker build -f devops/swift4.03-cpu.dockerfile .

# 删除
docker compose -f devops/docker-compose.yml down
# 启动（MODELSCOPE_CACHE_HOST = 宿主机上 ModelScope 缓存根，即其中须含 `hub` 子目录；勿填到 `.../hub` 本身）
$env:MODELSCOPE_CACHE_HOST = "C:/_llm_model/modelscope"
docker compose -f devops/docker-compose.yml up -d --build backend label-studio
# label-studio: xwhoyeah@sohu.com/xwhoyeah/RUI_887Ytewr
# label-studio: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6ODA4NDA2NjMwNiwiaWF0IjoxNzc2ODY2MzA2LCJqdGkiOiI1ZDNiOGZiYTA3ZTI0MTBmODAwZTI2YzI1ZGFlZjU0OSIsInVzZXJfaWQiOiIxIn0.tLVtBtn9-8O7H0XvUh8M2DWDaPRPDSFWCRydI7VRb90

# 查看日志
docker logs -f backend


```

#### **前端**
- UI（Vite）：http://127.0.0.1:8701
- **仓库根**中运行：
```powershell
cd frontend
npm run dev
```

## 十一、使用约束
- 建议本机 **≥16GB 内存**；速度与体验受 **CPU/内存** 影响。
- 不修改 **ms-swift** 核心仓库；问题在其上游追踪。

## 十二、实现约束
- 仓库：`frontend/`、`backend/`。
- 产品名：**数据工坊**。
- 界面：**仅中文**。
- MVP 交付面：从 LS 的数据导入；训练派单；日志/曲线；§4 推理；依赖与 `swift4.03-cpu` 一致。
- **当前实现补充**（相对纯 MVP 清单，每项已落地或对齐实现）：
  - 全景管线树：`GET /api/overview/pipeline`
  - **模型管理**：魔搭 Hub 列表与下载任务
  - 训练：支持 **仅保存参数** → **`POST .../start`**；YAML 解析/导出；日志 **SSE**；指标解析；任务改名/删/重试
  - **LoRA 合并**：异步任务与校验
  - **评测**：任务与报告导出
  - 推理：支持 **`import_task_id`**、**`image_workspace_path`**；**multipart 本地上传**（沙盒上传落在工作区 `uploads/playground/`，与「仅 LS 路径」的长期目标并存）
- 从编码到可测路径由实现侧自行跑通（构建、compose、单测等按仓库约定）。

## 十三、编码
- 参考 `Desiign_2_Ui.md`
- 参考 `Desiign_3_Api.md`
