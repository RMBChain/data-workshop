<script setup lang="ts">
import { message, Modal } from "ant-design-vue";
import { EditOutlined, PlusOutlined } from "@ant-design/icons-vue";
import { onMounted, onUnmounted, ref, computed } from "vue";
import { useRouter } from "vue-router";
import { apiErrorDetail, http } from "../api/http";

const router = useRouter();
type ImportBatchRow = {
  id: string;
  project_id?: number | null;
  project_title?: string | null;
  batch_name?: string | null;
  task_count: number;
  created_at: string;
};

const imports = ref<ImportBatchRow[]>([]);
const selectedProjectKey = ref<string | null>(null);
const selectedBatch = ref<string | null>(null);
const buildNote = ref("");
const trainRatio = ref(80);
const valRatio = ref(20);
const seed = ref<number | null>(null);
const addImageToken = ref(true);
const buildJob = ref<Record<string, unknown> | null>(null);
const pollT = ref<ReturnType<typeof setInterval> | null>(null);
const versions = ref<Record<string, unknown>[]>([]);
const activeVersion = ref<string | null>(null);
const versionNameEditOpen = ref(false);
const versionNameEditId = ref<string | null>(null);
const versionNameEditValue = ref("");
const versionNameSaving = ref(false);
const preview = ref<unknown>(null);
const autoImageLabel = computed(() => "自动补全 <image> 提示");

type VersionDataPayload = {
  version_id: string;
  meta: unknown;
  train_samples: unknown[];
  val_samples: unknown[];
};
const versionViewOpen = ref(false);
const versionViewLoading = ref(false);
const versionViewTitle = ref("");
const versionViewPayload = ref<VersionDataPayload | null>(null);
const createDatasetOpen = ref(false);
/** 从点击「生成数据集」到任务终态或轮询结束 */
const datasetBuildLoading = ref(false);

function openCreateDatasetModal() {
  createDatasetOpen.value = true;
}

const createDatasetFormLabelCol = { flex: "0 0 300px" as const, style: { maxWidth: "200px" } };
const createDatasetFormWrapperCol = { flex: "1 1 0", style: { minWidth: 0, maxWidth: "100%" } as const };

/** 与后端 import_batches 一致：有 project_id 时按 id 分组合并；无 id 时按项目标题分组合并 */
function importProjectKey(i: { project_id?: number | null; project_title?: string | null }): string {
  if (i.project_id != null) return `pid:${i.project_id}`;
  return `ptitle:${(i.project_title?.trim() || "—")}`;
}

function buildProjectOptions(items: ImportBatchRow[]): { value: string; label: string }[] {
  const map = new Map<string, string>();
  for (const row of items) {
    const k = importProjectKey(row);
    if (!map.has(k)) map.set(k, row.project_title?.trim() || "—");
  }
  return Array.from(map.entries()).map(([value, label]) => ({ value, label }));
}

const importProjectOptions = computed(() => buildProjectOptions(imports.value));

const createDatasetBatchOptions = computed(() => {
  const pk = selectedProjectKey.value;
  if (pk == null) return [];
  return imports.value
    .filter((i) => importProjectKey(i) === pk)
    .map((i) => {
      const batch = (i.batch_name != null && String(i.batch_name).trim()) || "—";
      return { value: i.id, label: `${batch} · ${i.task_count} 条` };
    });
});

function onCreateDatasetProjectChange(val: string | null | undefined) {
  if (val == null) {
    selectedBatch.value = null;
    return;
  }
  const list = imports.value.filter((i) => importProjectKey(i) === val);
  selectedBatch.value = list[0]?.id ?? null;
}

function formatJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

async function openVersionDataView(versionId: string) {
  versionViewTitle.value = `数据集 · ${versionId}`;
  versionViewOpen.value = true;
  versionViewLoading.value = true;
  versionViewPayload.value = null;
  try {
    const r = await http.get(`/api/datasets/versions/${encodeURIComponent(versionId)}/data`, {
      params: { per_split: 8 },
    });
    versionViewPayload.value = r.data as VersionDataPayload;
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "加载失败");
    versionViewOpen.value = false;
  } finally {
    versionViewLoading.value = false;
  }
}

/** 轮询间隔（毫秒）；1s 会使 uvicorn 访问日志很密 */
const DATASET_JOB_POLL_MS = 2500;
/** 超过该时长仍无终态则停止轮询（正常构建一般远小于此；避免异常卡死一直请求） */
const DATASET_JOB_POLL_MAX_MS = 60 * 60 * 1000;

onMounted(() => {
  void refreshImports();
  void refreshVersions();
});

onUnmounted(() => {
  if (pollT.value) {
    clearInterval(pollT.value);
    pollT.value = null;
  }
  datasetBuildLoading.value = false;
});

async function refreshImports() {
  const r = await http.get("/api/imports");
  imports.value = r.data.items;
  const items = imports.value;
  const opts = buildProjectOptions(items);
  if (opts.length === 0) {
    selectedProjectKey.value = null;
    selectedBatch.value = null;
    return;
  }
  if (!selectedProjectKey.value || !opts.some((o) => o.value === selectedProjectKey.value)) {
    selectedProjectKey.value = opts[0].value;
  }
  const list = items.filter((i) => importProjectKey(i) === selectedProjectKey.value);
  if (!list.length) {
    selectedBatch.value = null;
  } else if (!selectedBatch.value || !list.some((b) => b.id === selectedBatch.value)) {
    selectedBatch.value = list[0].id;
  }
}

function deleteCurrentBatch() {
  if (!selectedBatch.value) {
    message.warning("请选择要删除的批次");
    return;
  }
  const id = selectedBatch.value;
  Modal.confirm({
    title: "删除该源导入批次？",
    content:
      "将删除该批次在工作区中的原始导入数据与任务记录。已由此批次生成的「数据集版本」会保留在列表中，仅解除与批次的关联。此操作不可恢复。",
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    async onOk() {
      const r = await http.delete(`/api/imports/${encodeURIComponent(id)}`);
      if (r.data?.warning) {
        message.warning(String(r.data.warning));
      } else {
        message.success("已删除");
      }
      await refreshImports();
    },
  });
}

async function refreshVersions() {
  const r = await http.get("/api/datasets/versions");
  versions.value = r.data.items;
  activeVersion.value = r.data.active_version_id;
}

function datasetVersionNameText(record: { name?: string | null }): string {
  if (record.name == null) return "-";
  const s = String(record.name);
  return s !== "" ? s : "-";
}

function datasetVersionNameTitle(record: { name?: string | null }): string | undefined {
  if (record.name == null) return undefined;
  const s = String(record.name);
  return s !== "" ? s : undefined;
}

function openVersionNameEditor(record: { id?: string; name?: string | null }) {
  if (!record?.id) return;
  versionNameEditId.value = String(record.id);
  versionNameEditValue.value = record.name != null ? String(record.name) : "";
  versionNameEditOpen.value = true;
}

async function saveVersionName() {
  const id = versionNameEditId.value;
  if (!id) return;
  const name = (versionNameEditValue.value ?? "").trim();
  if (!name) {
    message.warning("名称不能为空");
    return;
  }
  versionNameSaving.value = true;
  try {
    await http.patch(`/api/datasets/versions/${encodeURIComponent(id)}`, { name });
    message.success("名称已保存");
    versionNameEditOpen.value = false;
    await refreshVersions();
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "保存失败");
  } finally {
    versionNameSaving.value = false;
  }
}

async function activateVersion(versionId: string) {
  try {
    await http.post(`/api/datasets/versions/${encodeURIComponent(versionId)}/rollback`);
    message.success("已激活该版本");
    await refreshVersions();
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "激活失败");
  }
}

function deleteVersion(versionId: string) {
  Modal.confirm({
    title: "删除数据集版本？",
    content:
      "将永久删除该版本对应的 versions/ 目录（含 train/val.jsonl 和 meta.json）及数据库记录。若为当前活跃版本，激活标记将被清除。此操作不可恢复。",
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    async onOk() {
      try {
        const r = await http.delete(`/api/datasets/versions/${encodeURIComponent(versionId)}`);
        if (r.data?.warning) {
          message.warning(String(r.data.warning));
        } else {
          message.success("版本已删除");
        }
        await refreshVersions();
      } catch (e: unknown) {
        message.error(apiErrorDetail(e) ?? "删除失败");
      }
    },
  });
}

async function startBuild() {
  if (!selectedBatch.value) {
    message.warning("请选择导入批次");
    return;
  }
  datasetBuildLoading.value = true;
  try {
    const r = await http.post("/api/datasets/build", {
      import_batch_id: selectedBatch.value,
      add_image_token: addImageToken.value,
      train_ratio: trainRatio.value,
      val_ratio: valRatio.value,
      random_seed: seed.value,
      note: buildNote.value || null,
    });
    const jid = r.data.job_id as string;
    buildJob.value = { id: jid, status: "pending" };
    if (pollT.value) clearInterval(pollT.value);
    const buildPollStart = Date.now();
    pollT.value = setInterval(() => {
      void (async () => {
        if (Date.now() - buildPollStart > DATASET_JOB_POLL_MAX_MS) {
          if (pollT.value) clearInterval(pollT.value);
          pollT.value = null;
          datasetBuildLoading.value = false;
          message.warning("构建状态长时间未结束，已停止轮询。请查看「版本」或刷新后重试。");
          return;
        }
        try {
          const st = await http.get(`/api/datasets/jobs/${jid}`);
          buildJob.value = st.data;
          if (["succeeded", "failed", "cancelled"].includes(String(st.data.status))) {
            if (pollT.value) clearInterval(pollT.value);
            pollT.value = null;
            datasetBuildLoading.value = false;
            if (st.data.status === "succeeded") {
              message.success("数据集已生成");
              createDatasetOpen.value = false;
              await refreshVersions();
            } else if (st.data.status === "failed") {
              const em = (st.data as { error_message?: string }).error_message;
              message.error(em && String(em).trim() ? em : "数据集构建失败");
            }
          }
        } catch (e: unknown) {
          if (pollT.value) clearInterval(pollT.value);
          pollT.value = null;
          datasetBuildLoading.value = false;
          const st = (e as { response?: { status?: number } }).response?.status;
          if (st === 404) {
            message.error("构建任务已不存在，已停止轮询。");
          } else {
            message.error(apiErrorDetail(e) ?? "获取构建状态失败，已停止轮询。");
          }
        }
      })();
    }, DATASET_JOB_POLL_MS);
  } catch (e: unknown) {
    datasetBuildLoading.value = false;
    message.error(apiErrorDetail(e) ?? "失败");
  }
}

async function loadPreview() {
  try {
    const r = await http.get("/api/datasets/preview", { params: {} });
    preview.value = r.data.sample;
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "无预览");
  }
}

function goTrain() {
  void router.push("/train");
}
</script>

<template>
  <div>
    <div class="datasets-page-header">
      <div class="datasets-page-header__title-row">
        <a-typography-title :level="4">数据集</a-typography-title>
        <a-tooltip title="新增数据集" placement="bottom">
          <a-button
            type="text"
            class="datasets-header-add-btn"
            aria-label="新增数据集"
            @click="openCreateDatasetModal"
          >
            <template #icon>
              <PlusOutlined />
            </template>
          </a-button>
        </a-tooltip>
      </div>
    </div>

    <a-modal
      v-model:open="createDatasetOpen"
      title="新建数据集"
      width="min(960px, 96vw)"
      :footer="null"
      destroy-on-close
    >
    <a-alert
      type="info"
      show-icon
      message="将导入数据转为 SFT 对话格式（qwen-vl 模板族），并划分训练集与验证集。产物位于工作区 versions/。"
      style="margin-bottom: 12px"
    />
      <a-form
        class="create-dataset-form"
        layout="horizontal"
        :label-col="createDatasetFormLabelCol"
        :wrapper-col="createDatasetFormWrapperCol"
      >
        <a-form-item label="项目">
          <a-select
            v-model:value="selectedProjectKey"
            :options="importProjectOptions"
            style="width: 100%"
            :disabled="!importProjectOptions.length"
            placeholder="无导入时请到「数据导入」"
            @change="onCreateDatasetProjectChange"
          />
        </a-form-item>
        <a-form-item label="数据批次">
          <a-select
            v-model:value="selectedBatch"
            :options="createDatasetBatchOptions"
            style="width: 100%"
            :disabled="!createDatasetBatchOptions.length"
            placeholder="请先选择项目，或到「数据导入」创建批次"
          />
        </a-form-item>
  
        <a-form-item :label="autoImageLabel">
          <a-switch v-model:checked="addImageToken" />
        </a-form-item>
  
        <a-form-item label="随机种子（可空）">
          <a-input-number v-model:value="seed" style="width: 100%" />
        </a-form-item>

        <a-form-item label="划分比例">
          <a-input-number v-model:value="trainRatio" :min="0" :max="100" /> :
          <a-input-number v-model:value="valRatio" :min="0" :max="100" />
          <span style="margin-left: 8px; color: #666; font-size: 12px">两数之和须为 100。（训练 : 验证，默认 80:20）</span>
        </a-form-item>

        <a-form-item label="备注">
          <a-input v-model:value="buildNote" />
        </a-form-item>

        <a-form-item :colon="false" label=" " class="create-dataset-form__actions">
          <a-space>
            <a-button @click="createDatasetOpen = false">取消</a-button>
            <a-button type="primary" :loading="datasetBuildLoading" @click="startBuild">生成数据集</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-modal>
    <a-typography-paragraph v-if="buildJob"
      >当前构建任务：{{ String((buildJob as { id?: string }).id) }} ·
      {{ String((buildJob as { status?: string }).status) }}</a-typography-paragraph
    >
    <a-divider />
    <a-table
      :columns="[
        { title: '数据集名称', dataIndex: 'name', key: 'name', ellipsis: true, width: 280 },
        { title: '项目名称', dataIndex: 'project_title', key: 'project_title', ellipsis: true },
        { title: '批次名称', dataIndex: 'batch_name', key: 'batch_name', ellipsis: true },
        { title: '训练', dataIndex: 'train_count', key: 'train_count', width: 72 },
        { title: '验证', dataIndex: 'val_count', key: 'val_count', width: 72 },
        { title: '备注', dataIndex: 'note', key: 'note' },
        { title: '时间', dataIndex: 'created_at', key: 'created_at' },
        {
          title: '操作',
          key: 'action',
          width: 200,
        },
      ]"
      :data-source="versions as Record<string, unknown>[]"
      :pagination="false"
      size="small"
      row-key="id"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'is_active'">
          <a-tag v-if="record && typeof record === 'object' && (record as any).is_active" color="success">活跃</a-tag>
          <a v-else-if="record && typeof record === 'object'" @click="activateVersion((record as any).id)">设为活跃</a>
        </template>
        <template v-else-if="column.key === 'name' && record && typeof record === 'object'">
          <span class="dataset-version-name-cell" @click.stop>
            <span class="dataset-version-name-text" :title="datasetVersionNameTitle(record as { name?: string | null })">
              {{ datasetVersionNameText(record as { name?: string | null }) }}
            </span>
            <EditOutlined
              class="dataset-version-name-edit"
              @click="openVersionNameEditor(record as { id?: string; name?: string | null })"
            />
          </span>
        </template>
        <template v-else-if="column.key === 'action' && record && typeof record === 'object'">
          <a-space>
            <a @click="openVersionDataView(String((record as any).id))">查看</a>
            <a @click="deleteVersion((record as any).id)" style="color: #ff4d4f">删除</a>
          </a-space>
        </template>
        <span v-else>{{ record?.[column.dataIndex as string] ?? '-' }}</span>
      </template>
    </a-table>

    <a-modal
      v-model:open="versionNameEditOpen"
      title="编辑数据集名称"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="versionNameSaving"
      destroy-on-close
      @ok="saveVersionName"
    >
      <a-input
        v-model:value="versionNameEditValue"
        placeholder="版本显示名称"
        allow-clear
        @press-enter="saveVersionName"
      />
    </a-modal>
    <a-modal
      v-model:open="versionViewOpen"
      :title="versionViewTitle"
      width="min(960px, 96vw)"
      :footer="null"
      destroy-on-close
    >
      <a-spin :spinning="versionViewLoading">
        <a-tabs v-if="versionViewPayload">
          <a-tab-pane key="meta" tab="元数据">
            <pre
              class="dataset-version-view-pre"
            >{{ versionViewPayload.meta != null ? formatJson(versionViewPayload.meta) : '（无 meta.json 或无法解析）' }}</pre>
          </a-tab-pane>
          <a-tab-pane key="train" :tab="`训练样本 (${versionViewPayload.train_samples.length})`">
            <pre class="dataset-version-view-pre">{{
              versionViewPayload.train_samples.length
                ? formatJson(versionViewPayload.train_samples)
                : '（无样本或 train.jsonl 不存在）'
            }}</pre>
          </a-tab-pane>
          <a-tab-pane key="val" :tab="`验证样本 (${versionViewPayload.val_samples.length})`">
            <pre class="dataset-version-view-pre">{{
              versionViewPayload.val_samples.length
                ? formatJson(versionViewPayload.val_samples)
                : '（无样本或 val.jsonl 不存在）'
            }}</pre>
          </a-tab-pane>
        </a-tabs>
      </a-spin>
    </a-modal>
  </div>
</template>

<style scoped>
.datasets-page-header {
  margin-bottom: 12px;
}
.datasets-page-header__title-row {
  display: inline-flex;
  align-items: center;
  gap: 0;
  max-width: 100%;
  min-width: 0;
}
.datasets-page-header__title-row :deep(h4) {
  margin: 0;
  padding: 0;
  line-height: 1.35;
}
.datasets-header-add-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  font-size: 18px;
  color: rgba(0, 0, 0, 0.45);
}
.datasets-header-add-btn:hover {
  color: var(--ant-primary-color, #1677ff);
}
.dataset-version-name-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
}
.dataset-version-name-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dataset-version-name-edit {
  flex-shrink: 0;
  color: rgba(0, 0, 0, 0.45);
  cursor: pointer;
  font-size: 14px;
}
.dataset-version-name-edit:hover {
  color: var(--ant-primary-color, #1677ff);
}

.dataset-version-view-pre {
  margin: 0;
  max-height: 70vh;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
