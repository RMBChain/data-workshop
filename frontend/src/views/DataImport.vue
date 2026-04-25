<script setup lang="ts">
import { message, Modal } from "ant-design-vue";
import {
  EditOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  SettingOutlined,
} from "@ant-design/icons-vue";
import { onMounted, reactive, ref } from "vue";
import { http } from "../api/http";

/** 与 backend Settings.label_studio_url 默认一致；容器内访问宿主机 LS 需 host.docker.internal */
const baseUrl = ref("http://host.docker.internal:8080");
const token = ref("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6ODA4NDA2NjMwNiwiaWF0IjoxNzc2ODY2MzA2LCJqdGkiOiI1ZDNiOGZiYTA3ZTI0MTBmODAwZTI2YzI1ZGFlZjU0OSIsInVzZXJfaWQiOiIxIn0.tLVtBtn9-8O7H0XvUh8M2DWDaPRPDSFWCRydI7VRb90");
const testMsg = ref("");
const testMsgOk = ref(true);
const testing = ref(false);
const projects = ref<{ id: number; title: string; task_number: number }[]>([]);
const selectedProjectId = ref<number | null>(null);
const loadingProjects = ref(false);
const importingIds = reactive(new Set<number>());
const lastBatch = ref<string | null>(null);
const batches = ref<any[]>([]);
const selectedBatchId = ref<string | null>(null);
const loadingBatches = ref(false);
const status = ref<Record<string, unknown>>({});
const tasks = ref<unknown[]>([]);
const page = ref(1);
const total = ref(0);
const form = reactive({ page_size: 20 });
/** 选中批次后展开「任务预览」折叠面板 */
const batchPreviewCollapseKeys = ref<string[]>(["1"]);
const settingsModalOpen = ref(false);
const rawModalOpen = ref(false);
const rawModalTitle = ref("");
const rawModalText = ref("");

const batchNameEditOpen = ref(false);
const batchNameEditId = ref<string | null>(null);
const batchNameEditValue = ref("");
const batchNameSaving = ref(false);

function openBatchNameEditor(record: { id?: string; batch_name?: string | null }) {
  if (!record?.id) return;
  batchNameEditId.value = String(record.id);
  batchNameEditValue.value = record.batch_name != null ? String(record.batch_name) : "";
  batchNameEditOpen.value = true;
}

async function saveBatchName() {
  const id = batchNameEditId.value;
  if (!id) return;
  batchNameSaving.value = true;
  try {
    await http.patch(`/api/imports/${encodeURIComponent(id)}`, {
      batch_name: batchNameEditValue.value ?? "",
    });
    message.success("批次名称已保存");
    batchNameEditOpen.value = false;
    await loadBatches();
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "保存失败");
  } finally {
    batchNameSaving.value = false;
  }
}

/** 进入页面时刷新：LS 状态、批次表；若已填 Token 则同步项目列表 */
async function fetchInitialViewData() {
  await refreshConfigStatus();
  await loadBatches();
  if (token.value.trim()) {
    await loadProjects();
  }
}

onMounted(() => {
  void fetchInitialViewData();
});

function formatApiDetail(detail: unknown): string {
  if (detail == null) return "连接失败";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item: { msg?: string; loc?: unknown[] }) => {
        const loc = Array.isArray(item.loc) ? item.loc.filter((x) => x !== "body").join(".") : "";
        return loc ? `${loc}: ${item.msg ?? ""}` : (item.msg ?? JSON.stringify(item));
      })
      .join("；");
  }
  return String(detail);
}

async function refreshConfigStatus() {
  try {
    const r = await http.get("/api/label-studio/status");
    status.value = r.data;
    const u = r.data?.url;
    if (typeof u === "string" && u.trim()) {
      baseUrl.value = u.trim().replace(/\/$/, "");
    }
  } catch {
    /* ignore */
  }
}

async function testConnection() {
  if (!baseUrl.value.trim()) {
    message.warning("请填写 Label Studio 基址");
    return;
  }
  if (!token.value.trim()) {
    message.warning("请填写 API Token 后再测试");
    return;
  }
  testing.value = true;
  testMsg.value = "";
  testMsgOk.value = true;
  try {
    await http.post("/api/label-studio/test-connection", { base_url: baseUrl.value.trim(), token: token.value.trim() });
    testMsg.value = "连接成功，Token 有效。";
    testMsgOk.value = true;
    message.success("已连接");
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: unknown } } };
    const d = formatApiDetail(err.response?.data?.detail ?? "连接失败");
    testMsg.value = d;
    testMsgOk.value = false;
    message.error(d);
  } finally {
    testing.value = false;
  }
}

async function loadProjects() {
  if (!token.value.trim()) {
    message.warning("请填写 Token");
    return;
  }
  loadingProjects.value = true;
  try {
    const r = await http.get("/api/label-studio/projects", {
      params: { base_url: baseUrl.value, token: token.value },
    });
    const items: { id: number; title: string; task_number: number }[] = r.data.items || [];
    projects.value = items;
    if (selectedProjectId.value != null && !items.some((p) => p.id === selectedProjectId.value)) {
      selectedProjectId.value = null;
    }
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "获取项目失败");
  } finally {
    loadingProjects.value = false;
  }
}

async function importProject(projectId: number) {
  if (importingIds.has(projectId)) return;
  if (!token.value.trim()) {
    message.warning("请填写 Token");
    return;
  }
  importingIds.add(projectId);
  try {
    const r = await http.post("/api/label-studio/import", {
      project_id: projectId,
      base_url: baseUrl.value,
      token: token.value,
    });
    lastBatch.value = r.data.import_batch_id;
    selectedBatchId.value = r.data.import_batch_id;
    selectedProjectId.value = projectId;
    message.success(`已导入 ${r.data.task_count} 条任务 (项目 ${projectId})`);
    page.value = 1;
    await loadBatches();
    await loadTasks(r.data.import_batch_id);
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "导入失败");
  } finally {
    importingIds.delete(projectId);
  }
}

async function loadBatches() {
  loadingBatches.value = true;
  try {
    const r = await http.get("/api/imports");
    batches.value = r.data.items || [];
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "获取批次列表失败");
  } finally {
    loadingBatches.value = false;
  }
}

function confirmDeleteBatch(batchId: string) {
  Modal.confirm({
    title: "删除导入批次？",
    content:
      "将删除该批次的记录与本地导入目录。已生成的数据集版本会保留，仅解除与此批次的关联。此操作不可恢复。",
    okText: "删除",
    cancelText: "取消",
    okType: "danger",
    async onOk() {
      try {
        await http.delete(`/api/imports/${encodeURIComponent(batchId)}`);
        message.success("已删除批次");
        if (selectedBatchId.value === batchId) {
          selectedBatchId.value = null;
          lastBatch.value = null;
          tasks.value = [];
          total.value = 0;
        }
        await loadBatches();
      } catch (e: unknown) {
        const err = e as { response?: { data?: { detail?: string } } };
        message.error(err.response?.data?.detail ?? "删除失败");
        return Promise.reject(e);
      }
    },
  });
}

async function loadTasks(batchId?: string) {
  const bid = batchId || selectedBatchId.value || lastBatch.value;
  if (!bid) return;
  const r = await http.get(`/api/imports/${bid}/tasks`, {
    params: { page: page.value, page_size: form.page_size },
  });
  tasks.value = (r.data.items as Record<string, unknown>[]).map((row) => ({
    ...row,
    resolved_label: (row as { resolved?: number }).resolved ? "是" : "否",
  }));
  taskImageBroken.value = {};
  total.value = r.data.total;
  selectedBatchId.value = bid;
  lastBatch.value = bid;
}

async function selectBatch(batchId: string) {
  selectedBatchId.value = batchId;
  page.value = 1;
  batchPreviewCollapseKeys.value = ["1"];
  await loadTasks(batchId);
}

async function openTaskRaw(record: { id: string; ls_task_id?: number }) {
  const bid = selectedBatchId.value;
  if (!bid) {
    message.warning("请先选择批次");
    return;
  }
  try {
    const r = await http.get(
      `/api/imports/${encodeURIComponent(bid)}/tasks/${encodeURIComponent(record.id)}/raw`,
    );
    const payload = r.data as { raw?: unknown; raw_text?: string };
    let text: string;
    if (payload.raw !== undefined && payload.raw !== null) {
      text = JSON.stringify(payload.raw, null, 2);
    } else if (typeof payload.raw_text === "string") {
      text = payload.raw_text;
    } else {
      text = "（无原始数据）";
    }
    rawModalTitle.value = `LS 任务 ${record.ls_task_id ?? "?"} · 导入的原始 JSON`;
    rawModalText.value = text;
    rawModalOpen.value = true;
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "获取原始数据失败");
  }
}

function onTableChange(p: number) {
  page.value = p;
  void loadTasks();
}

function onTaskRowClick(record: any) {
  if (record?.batch_id) {
    void selectBatch(record.batch_id);
  }
}

function taskImageUrl(batchId: string, importTaskId: string) {
  return `/api/imports/${encodeURIComponent(batchId)}/tasks/${encodeURIComponent(importTaskId)}/image`;
}

const taskImageBroken = ref<Record<string, boolean>>({});

function onTaskImageError(importTaskId: string) {
  taskImageBroken.value = { ...taskImageBroken.value, [importTaskId]: true };
}

/** 省略展示：最多显示 maxChars 个字符，超出追加 …（悬停可看完整路径 title） */
function ellipsisText(v: unknown, maxChars: number): string {
  if (v == null || v === "") return "-";
  const s = String(v);
  if (s.length <= maxChars) return s;
  return `${s.slice(0, maxChars)}…`;
}

function taskImagePathTooltip(record: { image_rel?: unknown }): string {
  const ir = record?.image_rel;
  if (ir == null || String(ir).trim() === "") return "暂无图片路径";
  return String(ir);
}

const taskColumns = [
  { title: "任务 ID", dataIndex: "ls_task_id", key: "ls_task_id", width: 30 },
  { title: "图片", key: "task_image", width: 70 },
  { title: "图片路径", key: "image_rel", width: 60 },
  { title: "标注数", dataIndex: "annotation_count", key: "annotation_count", width: 20 },
  { title: "已解析", dataIndex: "resolved_label", key: "resolved_label", width: 20 },
  { title: "原始数据", key: "raw_action", width: 30 },
];

const batchColumns = [
  { title: "批次名称", dataIndex: "batch_name", key: "batch_name", ellipsis: true, width: 180 },
  { title: "项目", dataIndex: "project_title", key: "project_title", ellipsis: true },
  { title: "任务数", dataIndex: "task_count", key: "task_count", width: 90 },
  { title: "时间", dataIndex: "created_at", key: "created_at", width: 160 },
  { title: "操作", key: "action", width: 120 },
];

const sftJsonExample = `{
  "images": ["/path/to/image.jpg"],
  "conversations": [
    {"from": "human", "value": "<image>这张图里有什么？"},
    {"from": "gpt", "value": "图中有一只猫坐在沙发上。"}
  ]
}`;
const messageJsonExample = `{"messages": [{"role": "user", "content": [{"type": "image", "image": "data/cable-010.png"}, {"type": "text", "text": "请描述。"}]}]}`;

function selectProject(projectId: number) {
  selectedProjectId.value = projectId;
}

/** 必须在脚本中读 .value：模板里内联箭头函数不会稳定解包 ref，会导致高亮 class 永远不生效 */
function batchRowClassName(record: { id?: string }) {
  const sid = selectedBatchId.value;
  if (sid == null || record?.id == null) return "";
  return String(record.id) === String(sid) ? "import-batch-table__row--selected" : "";
}
</script>

<template>
  <div>
    <div class="data-import__page-header">
      <div class="data-import__title-row">
        <a-typography-title :level="4" class="data-import__title">数据导入</a-typography-title>
        <a-tooltip
          title="刷新项目列表"
          placement="bottomRight"
          :auto-adjust-overflow="false"
        >
          <a-button
            type="text"
            class="data-import__refresh-btn"
            :loading="loadingProjects"
            aria-label="刷新项目列表"
            @click="loadProjects"
          >
            <template #icon>
              <ReloadOutlined />
            </template>
          </a-button>
        </a-tooltip>
      </div>
      <a-tooltip
        title="Label Studio 连接设置"
        placement="bottomRight"
        :auto-adjust-overflow="false"
      >
        <a-button
          type="text"
          class="data-import__settings-btn"
          aria-label="Label Studio 连接设置"
          @click="settingsModalOpen = true"
        >
          <template #icon>
            <SettingOutlined />
          </template>
        </a-button>
      </a-tooltip>
    </div>
    <a-divider />
    <a-typography-paragraph
      v-if="!(status as { reachable?: boolean }).reachable"
      type="secondary"
      style="margin-bottom: 16px"
    >
      未探测到可访问的默认 LS 地址。请用 Docker 在本地启动 Label Studio（见仓库说明），并确认
      <code>WORKSHOP_LABEL_STUDIO_URL</code> 与网络可达性。
    </a-typography-paragraph>
    <a-divider style="border-top: 2px solid rgba(0, 0, 0, 0.35)" />

        <a-typography-title :level="5" style="margin: 24px 0 12px">项目列表</a-typography-title>
        <a-spin :spinning="loadingProjects">
          <a-row v-if="projects.length" class="import-project-card-grid" :gutter="[16, 16]">
            <a-col
              v-for="record in projects"
              :key="record.id"
              :xs="24"
              :sm="12"
              :md="12"
              :lg="8"
              :xl="6"
            >
              <a-card
                class="import-project-card"
                :class="{
                  'import-project-card--selected':
                    selectedProjectId != null && Number(record.id) === Number(selectedProjectId),
                }"
                hoverable
                @click="selectProject(record.id)"
              >
                <template #title>
                  <span
                    class="import-project-card-title"
                    :title="record.title != null && String(record.title).trim() !== '' ? String(record.title) : undefined"
                  >
                    {{ record.title != null && String(record.title).trim() !== "" ? record.title : "-" }}
                  </span>
                </template>
                <template #extra>
                  <a-button
                    type="primary"
                    :loading="importingIds.has(record.id)"
                    @click.stop="importProject(record.id)"
                  >
                    导入数据
                  </a-button>
                </template>
                <div class="import-project-card-meta">
                  约 <span class="import-project-card-meta-number">{{ record.task_number }}</span> 任务
                </div>
              </a-card>
            </a-col>
          </a-row>
        </a-spin>
        <a-typography-paragraph v-if="!loadingProjects && !projects.length" type="secondary">
          请先在右上角打开连接设置，填写地址与 Token，再点击「刷新项目列表」加载 Label Studio 中的项目
        </a-typography-paragraph>
        <a-divider style="border-top: 2px solid rgba(0, 0, 0, 0.35)" />
        <a-typography-title :level="5" style="margin: 24px 0 12px">导入的批次列表</a-typography-title>
        <a-table
          class="import-batch-table"
          :columns="batchColumns"
          :data-source="batches"
          :loading="loadingBatches"
          :pagination="false"
          size="small"
          row-key="id"
          :row-class-name="batchRowClassName"
          :custom-row="(record: any) => ({
            onClick: () => record?.id != null && selectBatch(String(record.id)),
          })"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'batch_name'">
              <span class="import-batch-name-cell" @click.stop>
                <span
                  class="import-batch-name-text"
                  :title="
                    record?.batch_name != null && String(record.batch_name) !== ''
                      ? String(record.batch_name)
                      : undefined
                  "
                >
                  {{
                    record?.batch_name != null && String(record.batch_name) !== "" ? record.batch_name : "-"
                  }}
                </span>
                <EditOutlined class="import-batch-name-edit" @click="openBatchNameEditor(record as any)" />
              </span>
            </template>
            <template v-else-if="column.key === 'action'">
              <a-space size="small" @click.stop>
                <a style="color: #ff4d4f" @click="confirmDeleteBatch(record.id as string)">删除</a>
              </a-space>
            </template>
            <span v-else>{{ record?.[column.dataIndex as string] ?? "-" }}</span>
          </template>
        </a-table>
        <a-typography-paragraph v-if="!batches.length" type="secondary" style="margin-top: 8px">
          暂无批次。请先导入数据。
        </a-typography-paragraph>
       
    <a-modal
      v-model:open="settingsModalOpen"
      title="Label Studio 连接设置"
      width="min(880px, 96vw)"
      :footer="null"
    >
      <!-- 24 栅格 4 列（每列 span=6）；基址/Token 各占两列，操作区通栏 -->
      <a-form layout="vertical">
  
            <a-form-item>
              <template #label>
                <span>
                  Label Studio 地址
                  <a-tooltip>
                    <template #title>
                      后端在 Docker 内时请用 host.docker.internal（或与本机 WORKSHOP_LABEL_STUDIO_URL 一致）；仅当 API 与本机进程同机直连 LS 时用 127.0.0.1。
                    </template>
                    <QuestionCircleOutlined style="margin-left: 6px; color: rgba(0, 0, 0, 0.45); cursor: help" />
                  </a-tooltip>
                </span>
              </template>
              <a-textarea
                v-model:value="baseUrl"
                placeholder="http://host.docker.internal:8080"
                :auto-size="{ minRows: 2, maxRows: 4 }"
              />
            </a-form-item>
   
            <a-form-item>
              <template #label>
                <span>
                  API Token
                  <a-tooltip>
                    <template #title>
                      在 Label Studio 账户/设置中创建。
                    </template>
                    <QuestionCircleOutlined style="margin-left: 6px; color: rgba(0, 0, 0, 0.45); cursor: help" />
                  </a-tooltip>
                </span>
              </template>
              <a-textarea v-model:value="token" :rows="2" />
            </a-form-item>
 
            <a-form-item style="margin-top: 24px">
              <a-space>
                <a-button :loading="testing" @click="testConnection">测试连接</a-button>
              </a-space>
            </a-form-item>

      </a-form>
    </a-modal>
    <a-modal
      v-model:open="rawModalOpen"
      :title="rawModalTitle"
      width="min(920px, 96vw)"
      :footer="null"
      destroy-on-close
    >
      <pre style="margin: 0; max-height: 72vh; overflow: auto; font-size: 12px; white-space: pre-wrap; word-break: break-word">{{ rawModalText }}</pre>
    </a-modal>
    <a-modal
      v-model:open="batchNameEditOpen"
      title="编辑批次名称"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="batchNameSaving"
      destroy-on-close
      @ok="saveBatchName"
    >
      <a-input
        v-model:value="batchNameEditValue"
        placeholder="批次显示名称"
        allow-clear
        @press-enter="saveBatchName"
      />
    </a-modal>
  </div>
</template>

<style scoped>
.data-import__page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
}
.data-import__page-header :deep(.ant-tooltip) {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
}
.data-import__title {
  margin: 0;
}
.data-import__title-row {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  flex: 1;
}
.data-import__refresh-btn {
  flex-shrink: 0;
  font-size: 18px;
  color: rgba(0, 0, 0, 0.45);
}
.data-import__refresh-btn:hover {
  color: var(--ant-primary-color, #1677ff);
}
.data-import__settings-btn {
  flex-shrink: 0;
  font-size: 18px;
  color: rgba(0, 0, 0, 0.45);
}
.data-import__settings-btn:hover {
  color: var(--ant-primary-color, #1677ff);
}
.import-project-card-grid {
  margin-top: 8px;
}
.import-project-card {
  cursor: pointer;
  border-radius: 12px;
  border: 1px solid rgba(0, 0, 0, 0.14);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.1);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  background: color-mix(
    in srgb,
    var(--ant-color-fill-tertiary, #f0f0f0) 72%,
    var(--ant-color-fill-secondary, #e6e6e6) 28%
  );
}
.import-project-card :deep(.ant-card-head) {
  min-height: 56px;
  padding: 0 20px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--ant-color-fill-secondary, #e6e6e6) 55%, transparent) 0%,
    color-mix(in srgb, var(--ant-color-fill-tertiary, #f0f0f0) 35%, transparent) 100%
  );
}
.import-project-card :deep(.ant-card-head-title) {
  padding: 14px 0;
  min-width: 0;
}
.import-project-card :deep(.ant-card-extra) {
  padding: 14px 0;
}
.import-project-card :deep(.ant-card-body) {
  padding: 16px 20px 18px;
  background: color-mix(in srgb, var(--ant-color-fill-tertiary, #f0f0f0) 88%, var(--ant-color-bg-container, #fff) 12%);
}
.import-project-card-title {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  font-size: 16px;
  font-weight: 600;
  color: rgba(0, 0, 0, 0.88);
  letter-spacing: 0.01em;
}
.import-project-card--selected {
  border-color: var(--ant-primary-color, #1677ff);
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--ant-primary-color, #1677ff) 45%, transparent),
    0 8px 22px rgba(22, 119, 255, 0.2);
}
.import-project-card-meta {
  font-size: 14px;
  color: rgba(0, 0, 0, 0.55);
  line-height: 1.5;
}
.import-project-card-meta-number {
  font-weight: 700;
  font-size: 18px;
  font-variant-numeric: tabular-nums;
  color: rgba(0, 0, 0, 0.88);
}

.import-batch-table :deep(.ant-table-tbody > tr) {
  cursor: pointer;
}
.import-batch-table :deep(.import-batch-table__row--selected > td) {
  background: color-mix(in srgb, var(--ant-primary-color, #1677ff) 12%, var(--ant-color-bg-container, #fff));
}
.import-batch-table :deep(.import-batch-table__row--selected:hover > td) {
  background: color-mix(in srgb, var(--ant-primary-color, #1677ff) 20%, var(--ant-color-bg-container, #fff));
}

.import-task-image-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
}
.import-task-thumb {
  flex-shrink: 0;
}
.import-task-thumb :deep(.ant-image) {
  border-radius: 4px;
  overflow: hidden;
  border: 1px solid rgba(0, 0, 0, 0.06);
}
.import-task-thumb :deep(.ant-image-img) {
  object-fit: cover;
  height: 48px !important;
}
.import-task-image-placeholder {
  color: rgba(0, 0, 0, 0.25);
  font-size: 12px;
}

.import-task-image-rel {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  vertical-align: bottom;
  cursor: default;
}

.import-batch-name-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
}
.import-batch-name-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.import-batch-name-edit {
  flex-shrink: 0;
  color: rgba(0, 0, 0, 0.45);
  cursor: pointer;
  font-size: 14px;
}
.import-batch-name-edit:hover {
  color: var(--ant-primary-color, #1677ff);
}
</style>
