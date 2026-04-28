<script setup lang="ts">
import { message } from "ant-design-vue";
import { EditOutlined, PlusOutlined } from "@ant-design/icons-vue";
import { computed, onMounted, ref } from "vue";
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

function trainingJobNameText(record: Record<string, unknown>): string {
  const j = record.job_name;
  if (j == null || j === "") return "—";
  const s = String(j);
  return s !== "" && s !== "—" ? s : "—";
}

function trainingJobNameTitle(record: Record<string, unknown>): string | undefined {
  const t = trainingJobNameText(record);
  return t !== "—" ? t : undefined;
}

function trainingRequest(record: Record<string, unknown>): Record<string, unknown> | undefined {
  const r = record.request;
  return r && typeof r === "object" ? (r as Record<string, unknown>) : undefined;
}

function trainingOutputDirText(record: Record<string, unknown>): string {
  const top = record.output_dir;
  if (typeof top === "string" && top.trim() && top !== "—") return top.trim();
  const raw = trainingRequest(record)?.output_dir;
  const s = typeof raw === "string" ? raw.trim() : "";
  return s || "—";
}

function trainingOutputDirVersionLevelText(record: Record<string, unknown>): string {
  const reqVid = trainingRequest(record)?.dataset_version_id;
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

function trainingOutputDirRunText(record: Record<string, unknown>): string {
  const run = trainingRequest(record)?.swift_run_relpath;
  if (typeof run === "string" && run.trim()) return run.trim();
  return trainingOutputDirVersionLevelText(record);
}

function trainingOutputDirRunTitle(record: Record<string, unknown>): string | undefined {
  const lines: string[] = [];
  const run = trainingRequest(record)?.swift_run_relpath;
  if (typeof run === "string" && run.trim()) {
    lines.push(`ms-swift 运行目录：${run.trim()}`);
  }
  const base = trainingOutputDirText(record);
  if (base !== "—") {
    lines.push(`任务 output_dir：${base}`);
  }
  return lines.length ? lines.join("\n") : undefined;
}

type TrainJobMetaRow = {
  label: string;
  value: string;
  /** 路径类：ellipsis + Tooltip 展示完整 tooltip */
  pathTooltip?: string;
};

function trainingJobCardMeta(record: Record<string, unknown>): TrainJobMetaRow[] {
  const status = typeof record.status === "string" ? record.status : String(record.status ?? "");
  const outRun = trainingOutputDirRunText(record);
  const outTooltip = trainingOutputDirRunTitle(record);
  return [
    { label: "数据集", value: trainingDatasetTooltipField(record.dataset_name) },
    { label: "项目", value: trainingDatasetTooltipField(record.project_title) },
    { label: "批次", value: trainingDatasetTooltipField(record.batch_name) },
    { label: "训练", value: formatJobSplitCount(record.train_count) },
    { label: "验证", value: formatJobSplitCount(record.val_count) },
    {
      label: "输出 LoRA 路径",
      value: outRun,
      pathTooltip: outRun !== "—" && outTooltip ? outTooltip : outRun !== "—" ? outRun : undefined,
    },
    { label: "开始时间", value: formatJobTime(record.created_at) },
    { label: "结束时间", value: formatJobEnd(record.finished_at, status) },
  ];
}

function trainingJobStatusTagColor(status: unknown): string {
  const s = (typeof status === "string" ? status : String(status ?? "")).trim();
  if (s === "succeeded") return "success";
  if (s === "failed") return "error";
  if (s === "cancelled") return "warning";
  if (s === "running" || s === "pending") return "processing";
  if (s === "parameters_saved") return "default";
  return "default";
}

/** 数据集列 tooltip：项目 / 批次展示 */
function trainingDatasetTooltipField(v: unknown): string {
  if (v == null || v === "") return "—";
  const s = String(v).trim();
  return s || "—";
}

function openTrainingJobNameEditor(record: Record<string, unknown>) {
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

function openNewTrainModal() {
  currentJobId.value = null;
  jobNamePrefillForForm.value = "";
  newTrainOpenSeq.value += 1;
  trainParamsModalOpen.value = true;
}

function selectJob(id: string) {
  currentJobId.value = id;
  const row = jobs.value.find((j) => (j as Record<string, unknown>).id === id) as
    | Record<string, unknown>
    | undefined;
  const t = row ? trainingJobNameText(row) : "—";
  jobNamePrefillForForm.value = t !== "—" ? t : "";
  trainParamsModalOpen.value = true;
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

const jobRows = computed(() => jobs.value as Record<string, unknown>[]);
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

    <div v-if="jobRows.length" class="train-job-card-grid">
      <a-card
        v-for="record in jobRows"
        :key="String(record.id)"
        class="train-job-card"
        size="small"
        hoverable
      >
        <template #title>
          <div class="train-job-card-title">
            <span class="train-job-card-title-text" :title="trainingJobNameTitle(record)">
              {{ trainingJobNameText(record) }}
            </span>
          </div>
        </template>
        <template #extra>
          <span class="train-job-card-extra" @click.stop>
            <EditOutlined class="training-job-name-edit" @click="openTrainingJobNameEditor(record)" />
            <a-tag :color="trainingJobStatusTagColor(record.status)">{{ formatJobStatus(record.status) }}</a-tag>
          </span>
        </template>
        <div class="train-job-card-meta">
          <div
            v-for="row in trainingJobCardMeta(record)"
            :key="row.label"
            class="train-job-card-meta-row"
          >
            <span class="train-job-card-meta-label">{{ row.label }}</span>
            <div
              v-if="row.pathTooltip && row.value !== '—'"
              class="train-job-card-meta-value-wrap"
            >
              <a-tooltip
                :title="row.pathTooltip"
                placement="topLeft"
                :overlay-style="{ maxWidth: 'min(90vw, 560px)' }"
              >
                <span class="train-job-card-meta-value train-job-card-meta-value--ellipsis">
                  {{ row.value }}
                </span>
              </a-tooltip>
            </div>
            <span
              v-else
              class="train-job-card-meta-value"
              :title="row.value"
            >
              {{ row.value }}
            </span>
          </div>
          <div class="train-job-card-meta-actions">
            <a-button type="link" @click="selectJob(String(record.id))">训练</a-button>
            <a-popconfirm
              title="确定删除？将移除任务记录与日志；仅当无其它任务共用同一 output 目录时，才删除该目录下文件（如 checkpoint/LoRA）。"
              ok-text="确定"
              cancel-text="取消"
              @confirm="deleteJobById(String(record.id))"
            >
              <a-button type="link" danger>删除</a-button>
            </a-popconfirm>
          </div>
        </div>
      </a-card>
    </div>
    <a-empty v-else description="暂无训练任务。请点击右上角「+」新建训练。" style="margin-bottom: 16px" />

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
.train-job-card-grid {
  margin-top: 4px;
  margin-bottom: 16px;
  width: 100%;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 350px), 1fr));
  gap: 16px;
}
.train-job-card {
  border-radius: 10px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.train-job-card :deep(.ant-card-head) {
  min-height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.train-job-card :deep(.ant-card-head-title) {
  padding: 10px 0;
  min-width: 0;
}
.train-job-card :deep(.ant-card-extra) {
  padding: 10px 0;
}
.train-job-card :deep(.ant-card-body) {
  padding: 12px 16px 14px;
}
.train-job-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.train-job-card-title-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  font-size: 14px;
  color: rgba(0, 0, 0, 0.88);
}
.train-job-card-extra {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.train-job-card-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12px;
}
.train-job-card-meta-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  min-width: 0;
}
.train-job-card-meta-actions {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
.train-job-card-meta-actions :deep(.ant-btn) {
  padding-inline: 4px;
}
.train-job-card-meta-label {
  flex-shrink: 0;
  width: 96px;
  color: rgba(0, 0, 0, 0.45);
  line-height: 1.5;
}
.train-job-card-meta-value {
  flex: 1;
  min-width: 0;
  line-height: 1.5;
  word-break: break-all;
  color: rgba(0, 0, 0, 0.85);
}
.train-job-card-meta-value-wrap {
  flex: 1;
  min-width: 0;
}
.train-job-card-meta-value-wrap :deep(.ant-tooltip-disabled-compatible-wrapper) {
  display: block;
  max-width: 100%;
}
.train-job-card-meta-value--ellipsis {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  word-break: normal;
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
