# 数据工坊（Data Workshop）— 后端 API 约定

> 依据 `Desiign_1_Requirement.md`、`Desiign_2_Ui.md` 整理；**与当前 `backend/app` 实现同步**。  
> 下文 **已实现** 表示路由已在 `main.py` 注册且可用；**规划** 表示需求或 UI 可能需要、但仓库中尚无对应路由（若已实现请勿改回规划——以代码为准）。

---

## 1. 总则

### 1.1 服务与路径（开发）

| 项 | 值 |
|----|-----|
| API 根 URL | `http://127.0.0.1:8702/api`（Vite **8701** 代理到 **8702**；容器内 `uvicorn` 与 `ports` 在 `devops/docker-compose.yml` 中固定为 **8702**） |
| 健康检查 | `GET /api/health` |
| OpenAPI | `GET /docs`、`/openapi.json` |

- 所有业务接口均挂在 **`/api` 前缀** 下（`include_router(..., prefix="/api")`）。
- 默认 **`Content-Type: application/json`**；推理 **`POST /api/inference/chat`** 同时支持 **`multipart/form-data`**（见 §9）。

### 1.2 命名与风格

**URL**
- 小写。
- 路径段：**kebab-case**。
- 资源名多为复数（例如 `jobs`、`imports`）。

**JSON**
- 字段名：**snake_case**（Pydantic 默认）。
- 与前端在边界按需映射。

**时间**
- 实现中训练任务等常用 **数值时间戳（秒）** 或 **ISO 字符串**。
- 以前端联调与 OpenAPI 为准。

### 1.3 统一响应与错误

**成功**：`200` / `201` + JSON 对象（无强制统一 `code` 包裹）。

**失败**：HTTP 语义化状态码；`detail` 多为**中文**可读字符串。

### 1.4 长任务与实时数据

**长任务类型**
- **训练**
- **合并**
- **数据集构建**
- **评测**
- **模型下载**

**约定**
- 创建接口返回 **`job_id`**（或同类 id）。
- 客户端：**轮询**详情 + 日志接口。
- 训练日志：另支持 **SSE**（见 §5）。

**资源**
- **`GET /api/system/resources`**：**短轮询**。

### 1.5 工作区与路径安全

- 用户传入的相对路径由 **`resolve_under_workspace`** 约束在 **`workspace_root`** 下；越界返回 **400**。
- 配置见 **`backend/app/config.py`**。
- 环境变量前缀：**`WORKSHOP_`**。
- 常见键示例：
  - `WORKSHOP_WORKSPACE_ROOT`
  - `WORKSHOP_LABEL_STUDIO_URL`
  - `WORKSHOP_DB_PATH`
  - `WORKSHOP_SWIFT_EXECUTABLE`

### 1.6 数据持久化（SQLite）

- 默认库路径：`{workspace_root}/state/workshop.db`；可被 **`WORKSHOP_DB_PATH`** 覆盖（Docker 推荐容器内路径 + 命名卷）。
- 大文件、JSONL、权重、日志全文仍落盘工作区；库内为批次、版本、任务状态与索引。

### 1.7 调用 ms-swift（CLI 子进程）

训练等通过子进程调用 **`swift`**（或配置覆盖），与工作区 `cwd` 一致；详见 `TrainingJobManager`、合并服务等实现。

---

## 2. 健康与环境

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/health` | `status`、`ms_swift_available`（能否 `import swift`） | **已实现** |

---

## 3. 全景管线

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/overview/pipeline` | 树：项目 → 导入批次 → 数据集版本 → 训练 → 合并（每训练优选一条合并） | **已实现** |

---

## 4. Label Studio 与连接、导入

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/label-studio/connection` | 界面持久化的 `base_url`、`token`（库 `app_kv`），基址空则回退配置默认 | **已实现** |
| PUT | `/api/label-studio/connection` | 写入连接 | **已实现** |
| GET | `/api/label-studio/status` | 探测配置默认 LS URL 是否可达 | **已实现** |
| POST | `/api/label-studio/test-connection` | Body：`base_url`、`token` | **已实现** |
| GET | `/api/label-studio/projects` | Query：`token` 必填；`base_url` 可选 | **已实现** |
| POST | `/api/label-studio/import` | Body：`project_id`、`token`、可选 `base_url` | **已实现** |

---

## 5. 导入批次与任务

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/imports` | 导入批次列表 | **已实现** |
| PATCH | `/api/imports/{import_batch_id}` | 更新批次展示字段等 | **已实现** |
| DELETE | `/api/imports/{import_batch_id}` | 删除批次 | **已实现** |
| GET | `/api/import-tasks` | 跨批次任务查询（参数见 OpenAPI） | **已实现** |
| GET | `/api/imports/{import_batch_id}/tasks` | 分页任务 | **已实现** |
| GET | `/api/imports/{import_batch_id}/tasks/{import_task_id}/raw` | 原始标注等 | **已实现** |
| GET | `/api/imports/{import_batch_id}/tasks/{import_task_id}/image` | 图片二进制 | **已实现** |

---

## 6. 数据集（构建、划分、版本）

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| POST | `/api/datasets/build` | Body：`import_batch_id`、`add_image_token`、`train_ratio`+`val_ratio`=100、`random_seed`、`note`；返回构建 `job_id` | **已实现** |
| GET | `/api/datasets/jobs/{job_id}` | 构建任务状态 | **已实现** |
| POST | `/api/datasets/jobs/{job_id}/cancel` | 取消构建 | **已实现** |
| GET | `/api/datasets/versions` | 版本列表 | **已实现** |
| POST | `/api/datasets/versions/{version_id}/rollback` | 回滚当前指针 | **已实现** |
| PATCH | `/api/datasets/versions/{version_id}` | 更新版本展示名等 | **已实现** |
| DELETE | `/api/datasets/versions/{version_id}` | 删除版本 | **已实现** |
| GET | `/api/datasets/versions/{version_id}/data` | 版本数据文件内容片段 | **已实现** |
| GET | `/api/datasets/versions/{version_id}/export` | 导出快照 | **已实现** |
| GET | `/api/datasets/preview` | 预览 | **已实现** |
| GET | `/api/datasets/file-text` | 按路径读取文本片段 | **已实现** |

---

## 7. 训练（SFT/LoRA、队列、日志、曲线）

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/training/jobs` | 任务列表（含展示用项目/批次/数据集名、条数等） | **已实现** |
| POST | `/api/training/jobs` | 创建并启动训练；Body：`TrainJobCreate`；**基座模型须已在 Hub 列表中** | **已实现** |
| POST | `/api/training/jobs/params-only` | 仅落库，`status=parameters_saved` | **已实现** |
| POST | `/api/training/jobs/{job_id}/start` | 从 `parameters_saved` 启动子进程 | **已实现** |
| PATCH | `/api/training/jobs/{job_id}` | 改名（`job_name`） | **已实现** |
| GET | `/api/training/jobs/{job_id}` | 详情 | **已实现** |
| GET | `/api/training/jobs/{job_id}/logs` | `text`、`truncated` | **已实现** |
| GET | `/api/training/jobs/{job_id}/logs/stream` | SSE，定时推送当前日志尾 | **已实现** |
| GET | `/api/training/jobs/{job_id}/metrics` | `series`（loss/lr 等）+ `progress` | **已实现** |
| POST | `/api/training/jobs/{job_id}/cancel` | 取消 | **已实现** |
| DELETE | `/api/training/jobs/{job_id}` | 删除（已结束任务等，见错误提示） | **已实现** |
| POST | `/api/training/jobs/{job_id}/retry` | 同输出目录从最新 checkpoint **继续训练** | **已实现** |
| POST | `/api/training/config/yaml/parse` | Body：`yaml` 文本 → 校验为 `TrainJobCreate` | **已实现** |
| GET | `/api/training/config/yaml/export` | Query：可选 `job_id`；下载 YAML | **已实现** |
| GET | `/api/training/form-params` | Query：`job_id` → 当前 `request` | **已实现** |
| POST | `/api/training/form-params` | Query：`job_id`；Body：JSON 更新 `request_json` | **已实现** |
| PUT | `/api/training/form-params` | 同上 | **已实现** |

**状态机（含扩展）**

- **`parameters_saved`** →（调用 **`start`**）→ **`pending`**
- **`pending`** → **`running`**
- **`running`** → **终态**之一：**`succeeded`**；**`failed`**；**`cancelled`**。

---

## 8. 模型管理（魔搭 Hub）

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/models/hub` | 本机已登记/已下载模型列表 | **已实现** |
| POST | `/api/models/hub/download` | 发起下载任务 | **已实现** |
| GET | `/api/models/hub/download/{job_id}` | 下载任务状态 | **已实现** |
| DELETE | `/api/models/hub` | 删除/清理（见实现） | **已实现** |

---

## 9. 推理沙盒

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/inference/models` | 已注册模型列表 | **已实现** |
| POST | `/api/inference/load` | 预加载基座+LoRA | **已实现** |
| POST | `/api/inference/unload` | 卸载缓存 | **已实现** |
| POST | `/api/inference/chat` | 见下文「`/api/inference/chat` 请求体」 | **已实现** |
| POST | `/api/inference/session` | 创建会话 | **已实现** |
| GET | `/api/inference/sessions/{session_id}` | 会话历史 | **已实现** |
| POST | `/api/inference/sessions/{session_id}/export` | 导出 Markdown/JSON | **已实现** |

**`/api/inference/chat` 请求体**

*`application/json` 时可用的字段（与实现 / OpenAPI 一致为准）*

- `prompt`
- `base_model`
- `adapter_path`
- `model_id`
- `import_task_id`
- `image_workspace_path`
- `messages`
- `max_new_tokens`

*`multipart/form-data`*

- 上述字段均可作为表单字段提交。
- 可选文件字段：**`image`** → 服务端写入 **`uploads/playground/`** 后再参与推理。

---

## 10. LoRA 合并

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| POST | `/api/merge/jobs` | 创建合并任务 | **已实现** |
| GET | `/api/merge/jobs/{job_id}` | 状态 | **已实现** |
| GET | `/api/merge/jobs/{job_id}/logs` | 日志 | **已实现** |
| POST | `/api/merge/jobs/{job_id}/cancel` | 取消 | **已实现** |
| POST | `/api/merge/jobs/{job_id}/validate` | 合并后校验 | **已实现** |
| GET | `/api/merge/training-candidates` | 训练产物候选 | **已实现** |
| GET | `/api/merge/training-status` | 训练状态聚合 | **已实现** |
| GET | `/api/merge/training-jobs/{training_job_id}/train-run-logs` | 训练 run 日志 | **已实现** |
| GET | `/api/merge/training-jobs/{training_job_id}/logs` | 训练日志 | **已实现** |
| GET | `/api/merge/training-jobs/{training_job_id}/export-zip` | 导出 zip | **已实现** |

---

## 11. 评测

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/eval/merged-models` | 可评测的合并模型与建议 val JSONL | **已实现** |
| POST | `/api/eval/jobs` | 创建评测任务 | **已实现** |
| GET | `/api/eval/jobs/{job_id}` | 状态与汇总 | **已实现** |
| GET | `/api/eval/jobs/{job_id}/items` | 分页逐条 | **已实现** |
| GET | `/api/eval/reports` | 报告列表 | **已实现** |
| GET | `/api/eval/reports/{report_id}/export` | 导出报告 | **已实现** |

---

## 12. 导出（汇总类）

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/exports/artifacts` | 汇总 HF 兼容权重路径（需求 §6） | **规划** |

当前导出由以下分散接口承担（非单一汇总路由）：
- **合并**：相关 **zip** 导出路由（见 §10）。
- **评测**：报告 **export** 路由（见 §11）。
- **训练**：**YAML export**、产物路径类接口（见 §7）。

---

## 13. 系统信息

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/system/resources` | CPU、内存等 | **已实现** |
| GET | `/api/system/info` | Python、PyTorch、CUDA 探测等 | **已实现** |
| GET | `/api/system/paths` | 缓存路径、工作区提示等 | **已实现** |

---

## 14. 与前端路由的映射（联调索引）

| 前端路由 | 主要 API 分组 |
|----------|----------------|
| `/importData` | §4、§5 |
| `/batchDataView` | §5 |
| `/datasets` | §6 |
| `/models` | §8 |
| `/train` | §7 |
| `/verify` | §9 |
| `/merge` | §10 |
| `/eval` | §11 |
| `/system-info` | §13 |
| `/` | §3（可选全景） |

---

## 15. 查看 API 容器日志

容器名以 `docker ps` 为准；Compose **服务名**与 **`container_name`** 均为 **`data-workshop-api`**。

```bash
docker logs -f data-workshop-api
```

（见 `devops/docker-compose.yml`：顶层项目名 **`name: data-workshop`**，服务 **`data-workshop-api`**。）
