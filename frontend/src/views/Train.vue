<script setup lang="ts">
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons-vue";
import { message } from "ant-design-vue";
import { computed, onMounted, ref } from "vue";
import { apiErrorDetail, http } from "../api/http";
import TrainJobCard from "../components/train/TrainJobCard.vue";
import TrainJobNameModal from "../components/train/TrainJobNameModal.vue";
import TrainMergeLogModal from "../components/train/TrainMergeLogModal.vue";
import TrainVerifyEvalModals from "../components/train/TrainVerifyEvalModals.vue";
import TrainingFormPanel from "../components/TrainingFormPanel.vue";
import {
  mergeMergedOutputPathText,
  mergeMergedOutputPathTooltip,
  trainingJobNameText,
} from "./train/trainFormatters";
import type { MergeUiStatus } from "./train/trainTypes";
import { useTrainMerge } from "./train/useTrainMerge";

const verifyModalOpen = ref(false);
const verifyPrefillJobId = ref<string | null>(null);

function openVerifyModal(jobId: string) {
  verifyPrefillJobId.value = jobId;
  verifyModalOpen.value = true;
}

function onVerifyModalClosed() {
  verifyPrefillJobId.value = null;
}

const evalModalOpen = ref(false);
const evalPrefillJobId = ref<string | null>(null);

function openEvalModal(jobId: string) {
  evalPrefillJobId.value = jobId;
  evalModalOpen.value = true;
}

function onEvalModalClosed() {
  evalPrefillJobId.value = null;
}

const jobs = ref<Record<string, unknown>[]>([]);
const jobsLoading = ref(true);
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

const workspaceRootAbs = ref<string | null>(null);

const {
  mergeStatusByJobId,
  mergeOutputPathByJobId,
  loadMergeStatus,
  mergeSubmittingJobId,
  mergeLogModalOpen,
  mergeLogModalText,
  mergeLogLoading,
  runMergeFromTrainCard,
} = useTrainMerge();

function mergedPathRowFor(record: Record<string, unknown>): {
  value: string;
  pathTooltip?: string;
} {
  const id = String(record.id ?? "").trim();
  const st = (mergeStatusByJobId.value[id] ?? "none") as MergeUiStatus;
  const value = mergeMergedOutputPathText(st, mergeOutputPathByJobId.value[id]);
  const pathTooltip = mergeMergedOutputPathTooltip(value, workspaceRootAbs.value);
  return { value, pathTooltip };
}

let refreshJobsSeq = 0;

async function refreshJobs() {
  const seq = ++refreshJobsSeq;
  jobsLoading.value = true;
  try {
    const [r, pathsR] = await Promise.all([
      http.get("/api/training/jobs"),
      http.get("/api/system/paths").catch(() => ({ data: {} })),
    ]);
    jobs.value = r.data.items;
    const wr = (pathsR as { data?: { workspace_root?: unknown } }).data?.workspace_root;
    if (typeof wr === "string" && wr.trim()) {
      workspaceRootAbs.value = wr.trim();
    }
    await loadMergeStatus();
  } finally {
    if (seq === refreshJobsSeq) {
      jobsLoading.value = false;
    }
  }
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

function mergeUiStatusForRecord(record: Record<string, unknown>): MergeUiStatus {
  const id = String(record.id ?? "").trim();
  return mergeStatusByJobId.value[id] ?? "none";
}
</script>

<template>
  <div>
    <div class="train-page-header">
      <div class="train-page-header__title-row">
        <a-typography-title :level="4">微调</a-typography-title>
        <a-tooltip title="刷新" placement="bottomRight" :auto-adjust-overflow="false">
          <a-button
            type="text"
            class="train-page-header__icon-btn"
            aria-label="刷新"
            @click="refreshJobs"
          >
            <template #icon>
              <ReloadOutlined />
            </template>
          </a-button>
        </a-tooltip>
        <a-tooltip title="新建训练" placement="bottom">
          <a-button
            type="text"
            class="train-page-header__icon-btn"
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

    <a-spin v-if="jobsLoading" :spinning="true" tip="正在加载训练任务…">
      <div class="train-jobs-loading-placeholder" />
    </a-spin>
    <div v-else-if="jobRows.length" class="train-job-card-grid">
      <TrainJobCard
        v-for="record in jobRows"
        :key="String(record.id)"
        :record="record"
        :merge-ui-status="mergeUiStatusForRecord(record)"
        :merged-path-row="mergedPathRowFor(record)"
        :merge-submitting="mergeSubmittingJobId === String(record.id ?? '').trim()"
        @edit-name="openTrainingJobNameEditor"
        @delete="deleteJobById"
        @train="selectJob"
        @verify="openVerifyModal"
        @eval="openEvalModal"
        @merge="runMergeFromTrainCard"
      />
    </div>
    <a-empty v-else description="暂无训练任务。请点击右上角「+」新建训练。" style="margin-bottom: 16px" />

    <TrainJobNameModal
      v-model:open="jobNameEditOpen"
      v-model:value="jobNameEditValue"
      :saving="jobNameSaving"
      @save="saveTrainingJobName"
    />

    <TrainVerifyEvalModals
      v-model:verify-open="verifyModalOpen"
      v-model:eval-open="evalModalOpen"
      :verify-prefill-job-id="verifyPrefillJobId"
      :eval-prefill-job-id="evalPrefillJobId"
      @verify-closed="onVerifyModalClosed"
      @eval-closed="onEvalModalClosed"
    />

    <TrainMergeLogModal v-model:open="mergeLogModalOpen" :loading="mergeLogLoading" :text="mergeLogModalText" />

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
.train-jobs-loading-placeholder {
  min-height: 160px;
}
.train-page-header__icon-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  font-size: 18px;
  color: rgba(0, 0, 0, 0.45);
}
.train-page-header__icon-btn:hover {
  color: var(--ant-primary-color, #1677ff);
}
.train-page-header__title-row {
  display: inline-flex;
  align-items: center;
  gap: 0;
  max-width: 100%;
  min-width: 0;
}
.train-page-header__title-row :deep(h4) {
  margin: 0;
  padding: 0;
  line-height: 1.35;
}
.train-page-header {
  margin-bottom: 12px;
}
.train-job-card-grid {
  margin-top: 4px;
  margin-bottom: 16px;
  width: 100%;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 437.5px), 1fr));
  gap: 16px;
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
