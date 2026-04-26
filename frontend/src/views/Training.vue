<script setup lang="ts">
import { message } from "ant-design-vue";
import { EditOutlined, PlusOutlined } from "@ant-design/icons-vue";
import { onMounted, ref } from "vue";
import { apiErrorDetail, http } from "../api/http";
import TrainingFormPanel from "../components/TrainingFormPanel.vue";

const jobs = ref<Record<string, unknown>[]>([]);
const currentJobId = ref<string | null>(null);

const jobNameEditOpen = ref(false);
const jobNameEditId = ref<string | null>(null);
const jobNameEditValue = ref("");
const jobNameSaving = ref(false);

/** 全屏：训练参数（含表单、进度、日志） */
const trainParamsModalOpen = ref(false);
/** 从列表「查看」带入表单的训练名称；新建训练时清空 */
const jobNamePrefillForForm = ref("");
/** 每次点击「新建训练」递增，用于子组件重新生成默认训练名称 */
const newTrainOpenSeq = ref(0);

const jobColumns = [
  { title: "训练名称", dataIndex: "job_name", key: "job_name", ellipsis: true, width: 200 },
  { title: "数据集", dataIndex: "dataset_name", key: "dataset_name", ellipsis: true, width: 140 },
  { title: "训练", dataIndex: "train_count", key: "train_count", width: 72 },
  { title: "验证", dataIndex: "val_count", key: "val_count", width: 72 },
  { title: "输出LoRA 路径", dataIndex: "output_dir", key: "output_dir", ellipsis: true, width: 220 },
  { title: "开始时间", dataIndex: "created_at", key: "created_at", width: 100 },
  { title: "结束时间", dataIndex: "finished_at", key: "finished_at", width: 100 },
  { title: "状态", dataIndex: "status", key: "status", width: 60 },
  {
    title: "操作",
    key: "act",
    width: 60,
  },
];

function formatJobTime(t: unknown): string {
  if (t == null || t === "") return "—";
  const n = typeof t === "number" ? t : Number(t);
  if (Number.isNaN(n)) return "—";
  const d = new Date(n * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("zh-CN", { hour12: false });
}

function formatJobEnd(finished: unknown, status: string | undefined): string {
  if (finished != null && finished !== "") {
    return formatJobTime(finished);
  }
  if (status === "running" || status === "pending") {
    return "未结束";
  }
  return "—";
}

/** 与后端 `TrainJob.status` 对齐，表格展示用中文 */
function formatJobStatus(status: unknown): string {
  const s = typeof status === "string" ? status.trim() : String(status ?? "").trim();
  if (s === "parameters_saved") return "未训练";
  return s || "—";
}

/** 与数据集版本列表 `train_count` / `val_count` 一致；未知时「—」 */
function formatJobSplitCount(v: unknown): string {
  if (v == null || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (Number.isNaN(n)) return "—";
  return String(n);
}

async function refreshJobs() {
  const r = await http.get("/api/training/jobs");
  jobs.value = r.data.items;
}

function trainingJobNameText(record: { job_name?: string | null }): string {
  const j = record.job_name;
  if (j == null || j === "") return "—";
  const s = String(j);
  return s !== "" && s !== "—" ? s : "—";
}

function trainingJobNameTitle(record: { job_name?: string | null }): string | undefined {
  const t = trainingJobNameText(record);
  return t !== "—" ? t : undefined;
}

function trainingOutputDirText(record: { output_dir?: string | null; request?: { output_dir?: unknown } }): string {
  const top = record.output_dir;
  if (typeof top === "string" && top.trim() && top !== "—") return top.trim();
  const raw = record.request?.output_dir;
  const s = typeof raw === "string" ? raw.trim() : "";
  return s || "—";
}

function trainingOutputDirVersionLevelText(record: {
  output_dir?: string | null;
  request?: { output_dir?: unknown; dataset_version_id?: unknown };
}): string {
  const reqVid = record.request?.dataset_version_id;
  if (typeof reqVid === "string" && reqVid.trim()) {
    return `output/${reqVid.trim()}`;
  }
  const full = trainingOutputDirText(record);
  if (full === "—") return full;
  const parts = full
    .replace(/\\/g, "/")
    .replace(/\/+$/, "")
    .split("/")
    .filter(Boolean);
  if (parts.length >= 3 && parts[0] === "output") {
    return `output/${parts[1]}`;
  }
  return full;
}

function trainingOutputDirRunText(record: {
  output_dir?: string | null;
  request?: { output_dir?: unknown; swift_run_relpath?: unknown; dataset_version_id?: unknown };
}): string {
  const run = record.request?.swift_run_relpath;
  if (typeof run === "string" && run.trim()) return run.trim();
  return trainingOutputDirVersionLevelText(record);
}

function trainingOutputDirRunTitle(record: {
  output_dir?: string | null;
  request?: { output_dir?: unknown; swift_run_relpath?: unknown };
}): string | undefined {
  const lines: string[] = [];
  const run = record.request?.swift_run_relpath;
  if (typeof run === "string" && run.trim()) {
    lines.push(`ms-swift 运行目录：${run.trim()}`);
  }
  const base = trainingOutputDirText(record);
  if (base !== "—") {
    lines.push(`任务 output_dir：${base}`);
  }
  return lines.length ? lines.join("\n") : undefined;
}

/** 数据集列 tooltip：同列展示项目、批次全名 */
function trainingDatasetTooltipProjectText(record: { project_title?: string | null }): string {
  const t = record.project_title;
  if (t == null || t === "") return "—";
  const s = String(t).trim();
  return s || "—";
}

function trainingDatasetTooltipBatchText(record: { batch_name?: string | null }): string {
  const t = record.batch_name;
  if (t == null || t === "") return "—";
  const s = String(t).trim();
  return s || "—";
}

function openTrainingJobNameEditor(record: { id?: string; job_name?: string | null }) {
  if (!record?.id) return;
  jobNameEditId.value = String(record.id);
  const j = record.job_name != null ? String(record.job_name) : "";
  jobNameEditValue.value = j !== "" && j !== "—" ? j : "";
  jobNameEditOpen.value = true;
}

async function saveTrainingJobName() {
  const id = jobNameEditId.value;
  if (!id) return;
  const name = (jobNameEditValue.value ?? "").trim();
  if (!name) {
    message.warning("名称不能为空");
    return;
  }
  jobNameSaving.value = true;
  try {
    await http.patch(`/api/training/jobs/${encodeURIComponent(id)}`, { job_name: name });
    message.success("名称已保存");
    jobNameEditOpen.value = false;
    await refreshJobs();
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "保存失败");
  } finally {
    jobNameSaving.value = false;
  }
}

function openTrainParamsModal() {
  trainParamsModalOpen.value = true;
}

function openNewTrainModal() {
  currentJobId.value = null;
  jobNamePrefillForForm.value = "";
  newTrainOpenSeq.value += 1;
  openTrainParamsModal();
}

function selectJob(id: string) {
  currentJobId.value = id;
  const row = jobs.value.find((j) => (j as { id?: string }).id === id) as { job_name?: string | null } | undefined;
  const t = row ? trainingJobNameText(row) : "—";
  jobNamePrefillForForm.value = t !== "—" ? t : "";
  openTrainParamsModal();
}

async function deleteJobById(jobId: string) {
  if (!jobId) return;
  await http.delete(`/api/training/jobs/${jobId}`);
  message.success("已删除");
  if (currentJobId.value === jobId) {
    currentJobId.value = null;
  }
  await refreshJobs();
}

onMounted(() => {
  void refreshJobs();
});
</script>

<template>
  <div>
    <div class="datasets-page-header">
      <div class="datasets-page-header__title-row">
        <a-typography-title :level="4">LoRA 训练</a-typography-title>
        <a-tooltip title="新建训练" placement="bottom">
          <a-button
            type="text"
            class="datasets-header-add-btn"
            aria-label="新建训练"
            @click="openNewTrainModal"
          >
            <template #icon>
              <PlusOutlined />
            </template>
          </a-button>
        </a-tooltip>
      </div>
    </div>

    <a-table
      :columns="jobColumns"
      :data-source="(jobs as Record<string, unknown>[]) as any"
      :pagination="false"
      size="small"
      row-key="id"
    >
      <template #bodyCell="{ column, text, record }">
        <template v-if="column.key === 'act' && record && typeof record === 'object' && 'id' in record">
          <a-space :size="8" align="center">
            <a @click="selectJob(String((record as { id: string }).id))">训练</a>
            <a-popconfirm
              title="确定删除？将移除任务记录与日志；仅当无其它任务共用同一 output 目录时，才删除该目录下文件（如 checkpoint/LoRA）。"
              ok-text="确定"
              cancel-text="取消"
              @confirm="deleteJobById(String((record as { id: string }).id))"
            >
              <a-button type="link" danger size="small" style="padding: 0; height: auto">删除</a-button>
            </a-popconfirm>
          </a-space>
        </template>
        <span v-else-if="column.key === 'created_at' && record && typeof record === 'object'">{{
          formatJobTime((record as Record<string, unknown>).created_at)
        }}</span>
        <span v-else-if="column.key === 'finished_at' && record && typeof record === 'object'">{{
          formatJobEnd((record as Record<string, unknown>).finished_at, (record as { status?: string }).status)
        }}</span>
        <span v-else-if="column.key === 'status' && record && typeof record === 'object'">{{
          formatJobStatus((record as Record<string, unknown>).status)
        }}</span>
        <span v-else-if="column.key === 'train_count' && record && typeof record === 'object'">{{
          formatJobSplitCount((record as Record<string, unknown>).train_count)
        }}</span>
        <span v-else-if="column.key === 'val_count' && record && typeof record === 'object'">{{
          formatJobSplitCount((record as Record<string, unknown>).val_count)
        }}</span>
        <span v-else-if="column.key === 'job_name' && record && typeof record === 'object'" class="training-job-name-cell" @click.stop>
          <span class="training-job-name-text" :title="trainingJobNameTitle(record as { job_name?: string | null })">
            {{ trainingJobNameText(record as { job_name?: string | null }) }}
          </span>
          <EditOutlined class="training-job-name-edit" @click="openTrainingJobNameEditor(record as { id?: string; job_name?: string | null })" />
        </span>
        <span
          v-else-if="column.key === 'output_dir' && record && typeof record === 'object'"
          :title="
            trainingOutputDirRunTitle(
              record as { output_dir?: string | null; request?: { output_dir?: unknown; swift_run_relpath?: unknown } },
            )
          "
        >
          {{
            trainingOutputDirRunText(
              record as {
                output_dir?: string | null;
                request?: { output_dir?: unknown; swift_run_relpath?: unknown; dataset_version_id?: unknown };
              },
            )
          }}
        </span>
        <a-tooltip
          v-else-if="column.key === 'dataset_name' && record && typeof record === 'object'"
          placement="topLeft"
        >
          <template #title>
            <div>项目：{{ trainingDatasetTooltipProjectText(record as { project_title?: string | null }) }}</div>
            <div>批次：{{ trainingDatasetTooltipBatchText(record as { batch_name?: string | null }) }}</div>
          </template>
          <span class="training-dataset-name-cell">{{ text }}</span>
        </a-tooltip>
        <span v-else>{{ text }}</span>
      </template>
    </a-table>

    <a-modal
      v-model:open="jobNameEditOpen"
      title="编辑任务名称"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="jobNameSaving"
      destroy-on-close
      @ok="saveTrainingJobName"
    >
      <a-input
        v-model:value="jobNameEditValue"
        placeholder="训练任务显示名称"
        allow-clear
        @press-enter="saveTrainingJobName"
      />
    </a-modal>

    <TrainingFormPanel
      v-model:open="trainParamsModalOpen"
      v-model:current-job-id="currentJobId"
      :jobs="(jobs as Record<string, unknown>[])"
      :job-name-prefill="jobNamePrefillForForm"
      :new-train-open-seq="newTrainOpenSeq"
      @refresh-jobs="refreshJobs"
    />
  </div>
</template>

<style scoped>
.training-job-name-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
}
.training-job-name-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.training-job-name-edit {
  flex-shrink: 0;
  color: rgba(0, 0, 0, 0.45);
  cursor: pointer;
  font-size: 14px;
}
.training-job-name-edit:hover {
  color: var(--ant-primary-color, #1677ff);
}
.training-dataset-name-cell {
  display: inline-block;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
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
.datasets-page-header {
  margin-bottom: 12px;
}
</style>
<style>
/* 全屏训练参数：Modal 渲染到 body，需非 scoped */
.training-fullscreen-modal-wrap .ant-modal {
  top: 0;
  max-width: 100vw;
  width: 100% !important;
  margin: 0;
  padding: 0;
  padding-bottom: 0;
}
.training-fullscreen-modal-wrap .ant-modal-content {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  max-height: 100vh;
  border-radius: 0;
  padding: 0;
}
.training-fullscreen-modal-wrap .ant-modal-header {
  flex-shrink: 0;
  margin: 0;
  border-radius: 0;
  padding: 12px 16px;
}
.training-fullscreen-modal-wrap .ant-modal-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 16px 16px;
}
</style>
