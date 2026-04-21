# 数据工坊 (Data Workshop) - 图片多模态版

## 项目定位
面向**图片多模态大模型**（如 Qwen-VL、InternVL、LLaVA）的轻量级开发工具，将 ms-swift 从数据处理到模型合并的全流程封装为图形化界面。
**核心目标：**
- 开发者将精力集中在数据质量和模型策略上
- 无需在复杂的命令行操作和环境配置中耗费时间
- 真正实现“开箱即用”的大模型开发体验

**范围限定：**
- 专注于图片+文本的多模态任务（VQA、图像描述、OCR 等）
- 只支持 CPU 训练，用于快速验证初步想法
- ms-swift 版本为4.0.3
- 开发环境使用docker，参考 devops/swift4.03-cpu.dockerfile
- 训练任务在容器内运行。
- 仅支持本机运行
- labelStudio包含在docker compose中。

## 一、数据导入与连接

### 1.1 数据源支持（MVP 阶段）

| 数据源 | 支持状态 | 说明 |
|--------|----------|------|
| Label Studio | ✅ 首发支持 | 通过 API 拉取标注项目数据 |
| 本地文件jsonl | ✅ 首发支持 | 辅助入口 |

### 1.2 数据格式要求（图片 SFT）
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
## 二、数据集构建与管理

### 2.1 指令集构建器

提供可视化模板，将原始数据转换为标准 SFT 格式：

- **图片占位符自动处理**：在用户输入中自动插入 `<image>` 标记
- **对话模板选择**：支持 `qwen-vl`、`internvl`、`llava` 等模型格式

### 2.2 数据集划分

- 一键按比例划分（如 9:1、8:2）
- 支持训练集/验证集/测试集三划分
- 可选随机种子，保证可复现性

### 2.3 版本控制

- 每次处理后自动保存快照
- 支持版本备注、回滚和导出
- 存储位置：`{workspace}/versions/`

## 三、模型训练中心 (Model Training Hub)

### 3.1 模型选择与加载

**首发支持的模型（图片多模态）：**
| 模型 | 参数量 | 内存要求 (LoRA) |
|------|--------|-----------------|
| Qwen3-VL-2B-Instruct | 2B | ~10GB |
| Qwen3-VL-2B-Thinking | 2B | ~10GB |

**模型来源：**
- 自动从 ModelScope 下载
- 或加载本地已下载的模型路径

### 3.2 可视化训练配置

#### 训练任务类型
- ✅ SFT（监督微调）- 首发支持
- ⏳ 预训练 - 后续支持
- ⏳ DPO - 后续支持

#### 核心参数配置面板（表单+滑块）

| 参数类别 | 参数名 | 推荐值 | 说明 |
|----------|--------|--------|------|
| 微调策略 | LoRA rank | 8/16/32 | 数值越大效果越好，内存占用越高 |
| | LoRA alpha | 16 | 通常设为 rank 的 1-2 倍 |
| | Target modules | q_proj,v_proj | 默认全选 attention 层 |
| 训练超参 | Learning rate | 1e-4 | 典型范围 1e-5 ~ 5e-4 |
| | Batch size | 1/2/4 | 受内存限制，图片任务通常较小 |
| | Gradient accumulation | 4 | 有效 batch = batch × 积累步数 |
| | Epochs | 3 | 根据数据量调整 |
| 图片处理 | Image resolution | 448/512 | 越大越清晰，内存占用越高 |
| | Max pixels | 256x256 | 自动缩放宽高 |

#### LoRA 配置向导
```bash
Step 1: 选择基础模型
Step 2: 设置 LoRA rank
Step 3: 设置训练轮数
Step 4: 预览生成的 swift 命令
```

#### 配置文件管理
- 保存配置为 YAML 文件
- 导入已有 YAML 配置
- 提供示例配置文件库

### 3.3 训练任务监控

#### 任务队列
- 状态：等待中 → 运行中 → 已完成 / 失败
- 支持取消、删除、重试操作

#### 实时日志
- 滚动显示控制台输出
- 支持关键词高亮（ERROR、WARNING）
- 一键复制日志

#### 指标可视化
- **Loss 曲线**：实时绘制训练/验证 loss
- **学习率变化**：展示 scheduler 调整过程
- 使用 echart 直接嵌入 UI，无需额外打开 TensorBoard

#### 资源监控
- CPU：各核心利用率
- 内存：已用/总量
- 更新频率：2 秒/次

## 四、交互式推理沙盒

### 4.1 聊天界面

- 类似 ChatGPT 的对话界面
- 支持加载本地训练好的模型或适配器
- 模型切换无需重启

### 4.2 多模态输入

| 输入类型 | 支持方式 |
|----------|----------|
| 图片 | 拖拽上传、点击上传、粘贴截图 |
| 图片批处理 | 上传文件夹，批量生成描述 |
| 图片 URL | 输入 URL 自动下载 |

### 4.3 对话历史管理

- 保存/加载对话历史
- 导出为 Markdown/JSON
- 清除历史记录

## 五、模型合并工具 (Model Merger)

### 5.1 LoRA 适配器合并

- 选择基础模型路径
- 选择 LoRA 适配器路径（支持多个）
- 设置输出路径
- 一键执行 `swift merge-lora`

### 5.2 合并后操作

- 自动验证合并后的模型能否正常加载
- 一键跳转到推理沙盒测试

## 六、模型量化与导出（简化版）

### 6.1 4-bit 量化

- 使用 `bitsandbytes` 库
- 支持加载 4-bit 模型进行推理


### 6.2 导出格式

| 格式 | 支持状态 | 用途 |
|------|----------|------|
| 原始 HF 格式 | ✅ 首发 | 通用保存 |
| 4-bit (bnb) | ✅ 首发 | 低内存推理 |
| GPTQ / AWQ | ⏳ 后续 | 更高推理速度 |

## 七、模型评测模块（简化版）

### 7.1 自定义评测

- 支持上传 JSONL 格式的问答对
- 自动计算：准确率、BLEU、ROUGE（可选）
- 展示逐条预测结果

### 7.2 评测报告

- 可视化图表（柱状图、混淆矩阵）
- 导出 HTML/PDF 报告
- 保存评测结果历史

## 八、系统与环境管理

### 8.1 环境检测

| 检测项 | 显示内容 |
|--------|----------|
| Python 版本 | x.x.x (训练仅使用 CPU)|
| PyTorch 版本 | x.x.x + CUDA/cpu (训练仅使用 CPU)|
| CUDA 版本 | x.x（如有） (训练仅使用 CPU)|
| 可用 GPU | 型号、显存大小、数量 (训练仅使用 CPU) |

### 8.2 硬件识别
- NVIDIA GPU（CUDA）- 检测项保留，训练仅使用 CPU
- CPU（fallback）

### 8.3 路径配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| 模型缓存目录 | ~/.cache/modelscope | 模型下载位置 |
| 数据集目录 | ./data | 用户数据存放 |
| 输出目录 | ./output | 训练结果存放 |

## 九、技术架构

### 9.1 技术选型

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI |
| 前端框架 | antDesign |
| 训练调度 | subprocess |
| 日志解析 | 正则匹配 + 实时推送 |
| 图表绘制 | echart |
| 打包分发 | Docker |
| 配置管理 | YAML |

### 9.2 与 ms-swift 的交互方式

```
用户操作 → UI → 生成 swift 命令 → subprocess 执行 → 解析日志/输出 → UI 更新
```

所有底层能力来自 ms-swift，本工具只做图形化封装和流程编排。

## 十、开发语言及框架
- Vue3 + Ant Design Vue + Vite + TypeScript
- python
- nvm、node（22.22.2）、pnpm
- nginx
- FastAPI
- ms-swift4.0.3
- echart

## 十一、快速开始（Docker + 本机前端开发）

### 环境约定
- **后端**：开发与生产均在 **Docker**（`api` 容器挂载仓库目录；开发可叠加 `devops/docker-compose.dev.yml` 启用 `uvicorn --reload`）。
- **前端**：开发在 **本机**（Node 22 + `npm run dev`）；生产在 **Docker**（`devops/Dockerfile.frontend` 内构建，由 `web` 容器 nginx 提供）。

### 生产（全栈 Docker，宿主机无需 Node）
**Windows（推荐）：** 仓库根目录执行 `devops/build-compose.ps1`。

**命令行：**
```bash
docker build -f devops/swift4.03-cpu.dockerfile -t swift4.03-cpu:latest .
docker compose up -d --build
```
会构建并启动：`web`（多阶段前端镜像 + nginx:9000）、`api`、`label-studio`。

### 后端开发（仅 Docker：api + Label Studio，热重载）
**Windows（推荐）：** `devops/run-backend-dev.ps1`。

**命令行：**
```bash
docker build -f devops/swift4.03-cpu.dockerfile -t swift4.03-cpu:latest .
docker compose -f docker-compose.yml -f devops/docker-compose.dev.yml up -d --build label-studio api
```

### 前端开发（本机）
在仓库根目录：`cd frontend`，`nvm use`（见 `.nvmrc`），`npm install`，`npm run dev` → 默认 http://127.0.0.1:5173 ，通过 Vite 代理访问 `/api`（需本机 `8000` 已由后端容器映射，见上节先启动后端）。

### 服务说明（端口）
- UI(nginx): http://127.0.0.1:9000
- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health
- Label Studio：http://127.0.0.1:8080
- Label 探测：http://127.0.0.1:8000/api/label-studio/status

## 十二、局限性与免责声明
1. **仅支持图片多模态任务**，纯文本 SFT 不在首发范围内
2. **训练速度受限于本地硬件**，建议至少 16GB 内存
3. **本工具不修改 ms-swift 核心代码**，所有问题可追溯至 ms-swift 原项目

## 十三、代码生成要求
- 产品名与仓库策略：仍叫「数据工坊」、在当前 qwen3-vl-finetuning 里演进。
- 语言与地区：只支持中文即可。
- 只在win11下运行，docker需要使用linux容器。
- 前端VUE工程也放在当前目录下。
- MVP：数据导入 + 训练任务下发 + 日志 + 推理沙盒
- 4-bit 都放到镜像。
- 前端目录名frontend，后端目录名backend。
- AI要从编码开始，然后自动运行并进行测试。
- 后端开发和生产都在 Docker 环境中进行（开发可叠加 `devops/docker-compose.dev.yml` 热重载）。
- 前端开发在本机（Vite），生产环境在 Docker（`Dockerfile.frontend` + `web` 服务）。
