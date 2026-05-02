# 数据工坊（Data Workshop）— 后端 API 约定

> 依据 `Desiign_1_Requirement.md`、`Desiign_2_Ui.md` 整理；与前端联调时以本文为契约基准。  
> 实现可分期：文中标注 **已实现** 的与当前 `backend/app` 一致；**规划** 为 MVP 完整面所需、待开发接口。

---

## 1. 总则

### 1.1 服务与路径

| 项 | 生产 | 开发（`devops/compose.env.dev`） |
|----|------|----------------------------------|
| API 根 URL | `http://127.0.0.1:8602/api` | `http://127.0.0.1:8702/api` |
| 健康检查 | `GET /api/health` | 同上 |

- 所有业务接口均挂在 **`/api` 前缀** 下（与 FastAPI `include_router(..., prefix="/api")` 一致）。
- 默认 **`Content-Type: application/json`**；文件上传场景使用 `multipart/form-data`。
- API 文档：开发环境可访问 FastAPI 自动文档（如 `/docs`），生产可按需关闭。

### 1.2 命名与风格

- URL：小写、**kebab-case** 路径段（例：`/label-studio/projects`），资源名用复数（`jobs`、`datasets`）除非业界固定单数（如 `health`）。
- JSON 字段：**camelCase** 或 **snake_case** 二选一全局统一；**建议与现有后端一致用 snake_case**（Python/Pydantic 惯例），前端在边界层映射。
- 时间：ISO 8601 字符串，UTC 或带显式偏移（实现侧统一一种并文档化）。

### 1.3 统一响应与错误

**成功**  
- 简单操作：`200` + JSON 对象（无强制 `code` 包裹，与当前实现一致即可）。
- 创建资源：`201` + 新建体或 `{ "id": "..." }`（实现任选，但需全局一致）。

**失败**  
- 使用 HTTP 语义化状态码：`400` 参数/业务校验、`401/403` 鉴权（MVP 可无鉴权）、`404` 不存在、`409` 冲突、`500` 未捕获错误。
- 响应体建议形状（FastAPI `HTTPException` 的 `detail` 可扩展）：

```json
{
  "detail": "人类可读说明（界面可直出，中文）",
  "code": "OPTIONAL_MACHINE_CODE",
  "trace_id": "可选，便于排障"
}
```

- 与 `Desiign_2_Ui.md` §4 一致：**用户可见错误以中文为主**；技术细节可进日志，不必全部暴露给前端。

### 1.4 长任务与实时数据

- **训练 / 合并 / 评测 / 数据集构建** 等长任务：创建任务返回 `job_id`，客户端轮询 `GET .../jobs/{id}` 或通过 **SSE**（推荐路径如 `GET .../jobs/{id}/logs/stream`）推送日志行；与 UI「流式日志、自动滚底」一致。
- **资源监控**（CPU、内存）：`GET` 短轮询（如 2s）或 SSE；数据形状见 §6。

### 1.5 工作区与路径安全

- 所有「相对路径」均解析在 **`workspace_root`** 下，禁止 `..` 越界（与 `resolve_under_workspace` 行为一致）。
- 环境变量：`WORKSHOP_LABEL_STUDIO_URL`、工作区根路径等由 `backend/app/config.py` 统一读取；文档与部署说明保持同步。

### 1.6 数据持久化（SQLite，MVP 暂用）

- **数据库**：MVP 阶段结构化数据使用 **SQLite 3**（单文件、无需单独数据库进程），与本机/单容器 **单实例** 部署一致；后续若多实例或强并发写，可再迁 PostgreSQL 等，**API 契约不因存储后端而变**（通过数据访问层隔离）。
- **库文件位置**：由配置指定，建议落在工作区内便于与卷挂载一并备份，例如 `{workspace_root}/state/workshop.db`（具体键名实现时在 `config` 中定义）；开发/生产路径需在 Compose 与文档中写清。
- **建议落库内容**：Label Studio 导入批次与任务元数据索引、数据集版本记录、训练/合并/评测 **任务状态与摘要**（含 `job_id`、时间、错误摘要）、推理会话列表与导出记录索引、评测报告历史索引等。
- **仍走文件系统**：大规模 **JSONL**、训练 **日志全文**、ms-swift 产出 **权重与 YAML**、需求中的 **`{workspace}/versions/`** 快照目录等；SQLite 仅存路径引用与查询所需字段，不存大 BLOB。
- **并发**：默认单 API 进程写库；启用 SQLite **WAL**、短事务；避免长时间锁定事务与大批量同步写阻塞请求。
- **迁移与版本**： schema 变更用迁移脚本（如 Alembic）或受控 `CREATE TABLE IF NOT EXISTS` 策略，并在修订记录或单独迁移说明中记录版本。

### 1.7 调用 ms-swift（CLI 子进程）

与 `Desiign_1_Requirement.md` §九「界面 → 生成 swift 命令 → 子进程执行」一致：**训练、合并、以及将来采用命令行的推理/评测**，均以 **调用 ms-swift 命令行（CLI）** 为主，**不在 FastAPI 进程内直接执行 ms-swift 的训练主循环**（避免与 Web 进程共享内存/GIL、环境变量冲突、异常拖垮 API、以及 CPU 策略不统一等问题）。

**调用方式**

- 使用 Python **`subprocess.Popen`** 或 **`asyncio.create_subprocess_exec`**，在 **`workspace_root`** 下启动子进程（`cwd=str(workspace_root)`），与工作区内相对路径、JSONL、输出目录约定一致。
- **可执行入口**：以安装 ms-swift **4.0.3**（与 `devops/swift4.03-cpu.dockerfile` 一致）后暴露在 PATH 中的 **`swift`** 为准；若环境使用模块方式，可为 `python -m swift ...`（实现侧二选一并写死版本镜像）。可选配置项如 `WORKSHOP_SWIFT_EXECUTABLE` 覆盖默认命令名。
- **子命令与参数**：严格以 **ms-swift 4.0.3 官方 CLI 文档**为准（子命令名、长短参数可能随版本迭代）。约定能力映射如下，具体 flag 在实现/运维手册中列出或与界面表单一一对应即可：
  - **SFT / LoRA 微调**：对应官方 **SFT** 类子命令及参数（数据集路径、输出目录、LoRA rank/alpha、学习率、epoch、分辨率相关选项等）。
  - **LoRA 合并**：**`swift merge-lora`**（或该版本等价子命令），传入基础模型路径、一个或多个 LoRA 路径、输出路径（与需求 §5 一致）。
  - **推理 / 评测**：若改为 CLI 驱动，使用同一套 `swift` 子进程约定；参数与 ms-swift 该版本的 infer/eval 文档对齐。

**环境与输出**

- **CPU 路径**：子进程环境中设置 **`CUDA_VISIBLE_DEVICES=""`**（或等价方式），与需求「纯 CPU」一致；另设 **`PYTHONUNBUFFERED=1`** 便于日志实时落盘。
- **标准输出**：将 **stdout + stderr 合并** 重定向到任务日志文件（与当前训练 job 日志行为一致），供接口轮询或 SSE 推送；界面 **loss / 学习率曲线** 由后端 **解析日志行** 或 ms-swift 产出指标文件得到（实现可选）。
- **取消任务**：对仍存活的子进程发送 **SIGTERM**，必要时超时后 **SIGKILL**；更新 job 状态为 `cancelled`。

**与当前仓库的过渡说明**

- 现状：`TrainingJobManager` 通过 **`python -u backend/scripts/train.py ...`** 起子进程，脚本内部再调用 **`swift.pipelines.sft_main`**，已具备「子进程隔离、日志泵送」形态。
- **目标**：将 `train.py` 的职责收敛为 **拼装并 `exec` 官方 `swift sft ...` 一行命令**（或薄包装仅注入 `external_plugins`/回调路径），使 **命令行可复现** 与 **API 下发** 使用同一套 CLI 参数，便于排障与文档化。

---

## 2. 健康与环境

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 存活探测；可返回 `ms_swift_available` 等 **已实现** |

**响应示例（已实现）**  
`{ "status": "ok", "ms_swift_available": true }`

---

## 3. Label Studio 与数据导入

对应 UI `Desiign_2_Ui.md` §3.1、需求 §1。

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/label-studio/status` | 探测配置的 LS 基址是否可达（可不校验 Token） | **已实现** |
| POST | `/api/label-studio/test-connection` | Body：`base_url`、`token`；验证 Token 并返回账号/权限摘要（可选） | **规划** |
| GET | `/api/label-studio/projects` | 列出项目（id、title、任务数等） | **规划** |
| POST | `/api/label-studio/import` | Body：project_id、可选 filter；拉取任务元数据写入工作区，返回 `import_batch_id`、条数 | **规划** |
| GET | `/api/imports` | 导入批次列表 | **规划** |
| GET | `/api/imports/{import_batch_id}/tasks` | 分页：任务 id、图片路径/解析状态、缩略图 URL 说明 | **规划** |

**约定**  
- 原图在 LS 侧；本系统持久化 **元数据 + 可解析路径**（需求 §1.1）。  
- MVP 中若暂用 JSONL 上传过渡，须在界面标注与正式「LS 导入」的差异；当前 **`POST /api/data/upload-jsonl`**、**`POST /api/data/upload-image`** 为 **已实现** 的过渡能力，**与需求「推理沙盒无本地上传」目标不一致**，后续推理与正式导入应对齐「仅工作区内路径 / LS 关联条目」。

---

## 4. 数据集（构建、划分、版本）

对应 UI §3.2、需求 §2。

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| POST | `/api/datasets/build` | Body：源 import_batch_id、是否自动补 `<image>`、划分比例、随机种子、备注；异步任务返回 `job_id` | **规划** |
| GET | `/api/datasets/jobs/{job_id}` | 构建任务状态、进度、错误 | **规划** |
| POST | `/api/datasets/jobs/{job_id}/cancel` | 取消构建 | **规划** |
| GET | `/api/datasets/versions` | 版本列表：时间、备注、`versions/` 下标识 | **规划** |
| POST | `/api/datasets/versions/{version_id}/rollback` | 回滚当前工作区指针 | **规划** |
| GET | `/api/datasets/versions/{version_id}/export` | 导出快照（zip 或清单路径） | **规划** |
| GET | `/api/datasets/preview` | Query：`version_id` 或路径；返回 1 条 JSON 样例（与需求 §1.2 形态一致） | **规划** |

**约定**  
- 对话模板固定 **qwen-vl**，与训练默认 Qwen3-VL 族一致（需求 §2）。  
- 产物路径：`{workspace}/versions/`（需求 §2）。

---

## 5. 训练（SFT/LoRA、队列、日志、曲线）

对应 UI §3.3、需求 §3。

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/training/jobs` | 任务列表 | **已实现** |
| POST | `/api/training/jobs` | 创建训练任务；Body 对齐 `TrainJobCreate`（YAML/超参/数据路径等） | **已实现** |
| GET | `/api/training/jobs/{job_id}` | 单任务详情 | **已实现** |
| GET | `/api/training/jobs/{job_id}/logs` | 日志全文或尾部（`truncated`） | **已实现** |
| POST | `/api/training/jobs/{job_id}/cancel` | 取消 | **已实现** |
| DELETE | `/api/training/jobs/{job_id}` | 删除记录（可选，软删） | **规划** |
| POST | `/api/training/jobs/{job_id}/retry` | 重试（复制参数新建或重启） | **规划** |
| GET | `/api/training/jobs/{job_id}/metrics` | 解析 loss / lr 等序列供 ECharts | **规划** |
| GET | `/api/training/jobs/{job_id}/logs/stream` | SSE：增量日志 | **规划** |
| GET | `/api/system/resources` | 当前 CPU/内存（训练页 2s 轮询） | **规划**（也可并入 §10） |
| POST | `/api/training/config/yaml/parse` | 上传或粘贴 YAML 校验并返回结构化参数 | **规划** |
| GET | `/api/training/config/yaml/export` | Query：job_id 或表单快照；返回 YAML 文件 | **规划** |

**状态机（契约）**  
`pending` → `running` → `succeeded` | `failed` | `cancelled`（与需求「等待 → 运行 → 成功/失败」一致；`cancelled` 单独枚举）。

**训练日志与 UI 滚动（MVP）**

- 界面需展示 **训练过程中持续增长的日志**，并支持 **纵向滚动阅读**；前端在用户位于 **日志底部附近** 时于每次拉取后 **自动滚到底部**，用户上滚查看历史时 **不抢滚**，并提供 **「跟随最新」** 恢复（与 `Desiign_2_Ui.md` §3.3「训练日志区」一致）。
- **MVP 推荐**：`GET /api/training/jobs/{job_id}/logs` **短周期轮询**（建议 **1～2s**）；响应体已有 `text`、`truncated`；超长日志仅返回尾部时，界面提示「仅显示末尾」即可。
- **后续**：`GET /api/training/jobs/{job_id}/logs/stream`（SSE）推送增量行，前端同一套 **跟随尾部 / 暂停跟随** 逻辑；若增加查询参数（如 `since_byte` / `offset`），在实现 OpenAPI 中补充，避免重复传输整段尾部。

---

## 6. 推理沙盒

对应 UI §3.4、需求 §4。

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| POST | `/api/inference/chat` | 多模态对话；**当前实现** 为 `multipart` + **本地上传图片** | **已实现（待对齐需求）** |
| POST | `/api/inference/chat` | **目标契约**：Body JSON：`model_id` 或 `base_model`+`adapter_path`，`messages[]`，**图片**为 `workspace` 内相对路径或 `task_id`（LS 关联），**禁止**随意 URL/本机未登记上传 | **规划（替换/并存后废弃上传）** |
| GET | `/api/inference/models` | 可选：已注册 checkpoint / 合并结果列表，供下拉 | **规划** |
| POST | `/api/inference/session` | 创建/恢复对话会话；返回 `session_id` | **规划** |
| GET | `/api/inference/sessions/{session_id}` | 拉取历史 | **规划** |
| POST | `/api/inference/sessions/{session_id}/export` | Query：`format=markdown|json` | **规划** |

**约定**  
- **切换模型不重启进程**：后端可缓存已加载权重键（`base+adapter`），切换时换键（实现细节不限，行为与 UI 一致即可）。

---

## 7. LoRA 合并

对应 UI §3.5、需求 §5。

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| POST | `/api/merge/jobs` | Body：base_model_path、lora_paths[]、output_path；异步，返回 `job_id` | **规划** |
| GET | `/api/merge/jobs/{job_id}` | 状态、日志路径 | **规划** |
| POST | `/api/merge/jobs/{job_id}/cancel` | 取消 | **规划** |
| POST | `/api/merge/jobs/{job_id}/validate` | 合并后试加载（轻量校验） | **规划** |

内部等价调用 `swift merge-lora`（需求 §5）。

---

## 8. 评测

对应 UI §3.6、需求 §7。

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| POST | `/api/eval/jobs` | Body：数据版本/JSONL 路径、指标开关（准确率/BLEU/ROUGE） | **规划** |
| GET | `/api/eval/jobs/{job_id}` | 状态、汇总指标、逐条结果引用 | **规划** |
| GET | `/api/eval/jobs/{job_id}/items` | 分页逐条结果 | **规划** |
| GET | `/api/eval/reports` | 历史报告列表 | **规划** |
| GET | `/api/eval/reports/{report_id}/export` | 导出 | **规划** |

---

## 9. 导出

对应 UI §3.7、需求 §6。

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/exports/artifacts` | 汇总 HF 兼容权重路径、说明文案 | **规划** |

以 **ms-swift 可加载/可再训** 为准；MVP 不引导 CUDA bitsandbytes 4-bit（需求 §6）。

---

## 10. 系统信息

对应 UI §3.8、需求 §8。

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/system/info` | Python、PyTorch、CPU/CUDA 探测；**GPU 旁注字段**说明「仅探测，不参与本工具训练/推理」 | **规划** |
| GET | `/api/system/paths` | 模型缓存、`data`、`output` 等展示用路径 | **规划** |

可与 §5 的 `GET /api/system/resources` 合并为同一资源的不同字段，避免碎片化。

---

## 11. 与界面路由的映射（联调索引）

| 前端路由（`Desiign_2_Ui.md` §6） | 主要 API 分组 |
|----------------------------------|---------------|
| `/import` | §3 |
| `/datasets` | §4 |
| `/train`、`/train/jobs/:id` | §5 |
| `/playground` | §6 |
| `/merge` | §7 |
| `/eval` | §8 |
| `/export` | §9 |
| `/system` 或抽屉 | §10 |

---

## 13. 查看日志
```bash
docker logs -f workshop-api-dev
```
