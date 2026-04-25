<script setup lang="ts">
import { message, Modal } from "ant-design-vue";
import {
  EditOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  SettingOutlined,
} from "@ant-design/icons-vue";
import { computed, onMounted, reactive, ref } from "vue";
import { http } from "../api/http";

type LsStatus = { url?: string; reachable?: boolean };
type ImportBatch = { id: string; project_id?: number; batch_name?: string | null; task_count?: number; created_at?: string };
type LsProject = { id: number; title: string; task_number: number };

const baseUrl = ref("http://host.docker.internal:8080");
const token = ref("");
const testing = ref(false);
const savingConnection = ref(false);
const projects = ref<LsProject[]>([]);
const selectedProjectId = ref<number | null>(null);
const loadingProjects = ref(false);
const importingIds = reactive(new Set<number>());
const lastBatch = ref<string | null>(null);
const batches = ref<ImportBatch[]>([]);
const selectedBatchId = ref<string | null>(null);
const loadingBatches = ref(false);
const status = ref<LsStatus>({});
const settingsModalOpen = ref(false);

const batchNameEditOpen = ref(false);
const batchNameEditId = ref<string | null>(null);
const batchNameEditValue = ref("");
const batchNameSaving = ref(false);

const lsUnreachable = computed(() => status.value.reachable === false);

const batchesByProjectId = computed(() => {
  const m = new Map<number, ImportBatch[]>();
  for (const b of batches.value) {
    const pid = Number(b.project_id);
    if (Number.isNaN(pid)) continue;
    const list = m.get(pid);
    if (list) list.push(b);
    else m.set(pid, [b]);
  }
  return m;
});

function projectBatches(projectId: number): ImportBatch[] {
  return batchesByProjectId.value.get(projectId) ?? [];
}

function openBatchNameEditor(record: ImportBatch) {
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
    message.error(apiDetail(e) ?? "保存失败");
  } finally {
    batchNameSaving.value = false;
  }
}

async function loadLabelStudioConnection() {
  try {
    const r = await http.get("/api/label-studio/connection");
    const d = r.data as { base_url?: string; token?: string };
    if (typeof d.base_url === "string" && d.base_url.trim()) {
      baseUrl.value = d.base_url.trim().replace(/\/$/, "");
    }
    if (typeof d.token === "string") {
      token.value = d.token;
    }
  } catch {
    /* 保持默认或上次内存中的值 */
  }
}

async function saveLabelStudioConnection() {
  savingConnection.value = true;
  try {
    await http.put("/api/label-studio/connection", {
      base_url: baseUrl.value.trim(),
      token: token.value,
    });
    message.success("连接设置已保存到数据库");
  } catch (e: unknown) {
    message.error(apiDetail(e) ?? "保存失败");
  } finally {
    savingConnection.value = false;
  }
}

async function fetchInitialViewData() {
  await loadLabelStudioConnection();
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

function apiDetail(e: unknown): string | undefined {
  return (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
}

async function refreshConfigStatus() {
  try {
    const r = await http.get("/api/label-studio/status");
    status.value = r.data as LsStatus;
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
  try {
    await http.post("/api/label-studio/test-connection", { base_url: baseUrl.value.trim(), token: token.value.trim() });
    message.success("已连接，Token 有效。");
  } catch (e: unknown) {
    const d = formatApiDetail((e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ?? "连接失败");
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
    const items: LsProject[] = r.data.items || [];
    projects.value = items;
    if (selectedProjectId.value != null && !items.some((p) => p.id === selectedProjectId.value)) {
      selectedProjectId.value = null;
    }
  } catch (e: unknown) {
    message.error(apiDetail(e) ?? "获取项目失败");
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
    await loadBatches();
  } catch (e: unknown) {
    message.error(apiDetail(e) ?? "导入失败");
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
    message.error(apiDetail(e) ?? "获取批次列表失败");
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
        }
        await loadBatches();
      } catch (e: unknown) {
        message.error(apiDetail(e) ?? "删除失败");
        return Promise.reject(e);
      }
    },
  });
}

function selectBatch(batchId: string) {
  selectedBatchId.value = batchId;
  lastBatch.value = batchId;
}

function onBatchTableRow(r: ImportBatch) {
  return { onClick: () => r?.id != null && selectBatch(String(r.id)) };
}

function batchRowClassName(record: { id?: string }) {
  const sid = selectedBatchId.value;
  if (sid == null || record?.id == null) return "";
  return String(record.id) === String(sid) ? "import-batch-table__row--selected" : "";
}

const batchColumns = [
  { title: "批次名称", dataIndex: "batch_name", key: "batch_name", ellipsis: true, width: 180 },
  { title: "任务数", dataIndex: "task_count", key: "task_count", width: 60 },
  { title: "建立时间", dataIndex: "created_at", key: "created_at", width: 160 },
  { title: "操作", key: "action", width: 60 },
];
</script>

<template>
  <div>
    <div class="data-import__page-header">
      <div class="data-import__title-row">
        <a-typography-title :level="4" class="data-import__title">数据导入</a-typography-title>
        <a-tooltip title="刷新项目列表" placement="bottomRight" :auto-adjust-overflow="false">
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
        <a-tooltip title="Label Studio 连接设置" placement="bottomRight" :auto-adjust-overflow="false">
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
    </div>
    <a-divider />
    <a-typography-paragraph v-if="lsUnreachable" type="secondary" style="margin-bottom: 16px">
      未探测到可访问的默认 LS 地址。请用 Docker 在本地启动 Label Studio（见仓库说明），并确认
      <code>WORKSHOP_LABEL_STUDIO_URL</code> 与网络可达性。
    </a-typography-paragraph>

    <a-typography-title :level="5" style="margin: 24px 0 12px">项目列表</a-typography-title>
    <a-spin :spinning="loadingProjects">
      <div v-if="projects.length" class="import-project-card-grid">
        <a-card
          v-for="record in projects"
          :key="record.id"
          class="import-project-card"
          :class="{
            'import-project-card--selected':
              selectedProjectId != null && Number(record.id) === Number(selectedProjectId),
          }"
          hoverable
          @click="selectedProjectId = record.id"
        >
            <template #title>
              <span
                class="import-project-card-title"
                :title="record.title != null && String(record.title).trim() ? String(record.title) : undefined"
              >
                {{ record.title != null && String(record.title).trim() ? record.title : "—" }}
              </span>
              <div class="import-project-card-meta">
                约 <span class="import-project-card-meta-number">{{ record.task_number }}</span> 任务
              </div>
            </template>
            <template #extra>
              <a-button type="primary" :loading="importingIds.has(record.id)" @click.stop="importProject(record.id)">
                新建数据批次
              </a-button>
            </template>

            <a-table
              v-if="projectBatches(record.id).length"
              class="import-batch-table"
              :columns="batchColumns"
              :data-source="projectBatches(record.id)"
              :loading="loadingBatches"
              :pagination="false"
              size="small"
              row-key="id"
              :row-class-name="batchRowClassName"
              :custom-row="onBatchTableRow"
            >
              <template #bodyCell="{ column, record: br }">
                <template v-if="column.key === 'batch_name'">
                  <span class="import-batch-name-cell" @click.stop>
                    <span
                      class="import-batch-name-text"
                      :title="br?.batch_name != null && String(br.batch_name) ? String(br.batch_name) : undefined"
                    >
                      {{ br?.batch_name != null && String(br.batch_name) ? br.batch_name : "—" }}
                    </span>
                    <EditOutlined class="import-batch-name-edit" @click="openBatchNameEditor(br)" />
                  </span>
                </template>
                <template v-else-if="column.key === 'action'">
                  <a-space size="small" @click.stop>
                    <a style="color: #ff4d4f" @click="confirmDeleteBatch(String(br.id))">删除</a>
                  </a-space>
                </template>
                <span v-else>{{
                  column.dataIndex == null
                    ? "—"
                    : (br as Record<string, unknown>)?.[String(column.dataIndex)] ?? "—"
                }}</span>
              </template>
            </a-table>
            <a-typography-paragraph v-else type="secondary" style="margin-top: 8px">暂无批次。请先导入数据。</a-typography-paragraph>
        </a-card>
      </div>
    </a-spin>
    <a-typography-paragraph v-if="!loadingProjects && !projects.length" type="secondary">
      请先在右上角打开连接设置，填写地址与 Token，再点击「刷新项目列表」加载 Label Studio 中的项目
    </a-typography-paragraph>

    <a-modal v-model:open="settingsModalOpen" title="Label Studio 连接设置" width="min(880px, 96vw)" :footer="null">
      <a-form layout="vertical">
        <a-form-item>
          <template #label>
            <span>
              Label Studio 地址
              <a-tooltip>
                <template #title>
                  后端在 Docker 内时请用 host.docker.internal（或与本机 WORKSHOP_LABEL_STUDIO_URL
                  一致）；仅当 API 与本机进程同机直连 LS 时用 127.0.0.1。
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
                <template #title>在 Label Studio 账户/设置中创建。</template>
                <QuestionCircleOutlined style="margin-left: 6px; color: rgba(0, 0, 0, 0.45); cursor: help" />
              </a-tooltip>
            </span>
          </template>
          <a-textarea v-model:value="token" :rows="4" />
        </a-form-item>
        <a-form-item style="margin-top: 24px">
          <a-space>
            <a-button type="primary" :loading="savingConnection" @click="saveLabelStudioConnection">保存</a-button>
            <a-button :loading="testing" @click="testConnection">测试连接</a-button>
          </a-space>
        </a-form-item>
      </a-form>
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
/* 与原先 a-col xs24 / sm&md12 / lg&xl8 一致：小屏 1 列、≥576px 2 列、≥992px 3 列 */
.import-project-card-grid {
  display: grid;
  gap: 16px;
  margin-top: 8px;
  grid-template-columns: 1fr;
}
@media (min-width: 576px) {
  .import-project-card-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
@media (min-width: 992px) {
  .import-project-card-grid {
    grid-template-columns: repeat(3, 1fr);
  }
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
