<script setup lang="ts">
import { message } from "ant-design-vue";
import { computed, ref, watch } from "vue";
import { http } from "../../api/http";
import {
  mergeConfirmDescription,
  mergeOutputRelForTrainingJob,
  parseMergeUiStatus,
} from "../../views/train/trainFormatters";
import { MERGE_DEFAULT_BASE } from "../../views/train/trainTypes";

const props = defineProps<{
  /** v-model:open */
  open: boolean;
  record: Record<string, unknown> | null;
}>();

const emit = defineEmits<{
  "update:open": [open: boolean];
  start: [payload: { base_model_path: string; merge_lora_only: boolean }];
}>();

const loading = ref(false);
const candidatePath = ref("");
const candidateTrainBase = ref("");
const trainingStatus = ref("");
const mergeStatus = ref<ReturnType<typeof parseMergeUiStatus>>("none");

const baseModelPath = ref(MERGE_DEFAULT_BASE);
const mergeLoraOnly = ref(true);

const jobId = computed(() => String(props.record?.id ?? "").trim());

const outputRel = computed(() => (jobId.value ? mergeOutputRelForTrainingJob(jobId.value) : ""));

const loadError = ref("");

const canStart = computed(() => {
  if (loadError.value) return false;
  if (!jobId.value || loading.value) return false;
  if (!candidatePath.value.trim()) return false;
  const ts = trainingStatus.value.trim().toLowerCase();
  if (ts === "pending" || ts === "running") return false;
  if (mergeStatus.value === "merging") return false;
  if (mergeStatus.value !== "none" && ts !== "succeeded") return false;
  return !!(baseModelPath.value ?? "").trim();
});

async function loadCandidate() {
  const tid = jobId.value;
  if (!tid || !props.open) return;
  loading.value = true;
  loadError.value = "";
  candidatePath.value = "";
  candidateTrainBase.value = "";
  trainingStatus.value = "";
  mergeStatus.value = "none";
  try {
    const [candidatesRes, statusRes] = await Promise.all([
      http.get("/api/merge/training-candidates"),
      http.get("/api/merge/training-status"),
    ]);
    const items = (candidatesRes.data?.items ?? []) as Record<string, unknown>[];
    const row = items.find((x) => String(x.job_id ?? "").trim() === tid);
    if (!row) {
      loadError.value = "未找到该任务的合并候选（仅保存参数或未开训的任务不会在合并列表中）";
      return;
    }
    candidatePath.value = String(row.path ?? "").trim();
    candidateTrainBase.value =
      typeof row.train_base_model === "string" ? row.train_base_model.trim() : "";
    trainingStatus.value = String(row.training_status ?? "").trim();
    const statusByJobId = (statusRes.data?.status_by_job_id ?? {}) as Record<string, string>;
    mergeStatus.value = parseMergeUiStatus(statusByJobId[tid]);

    const m = candidateTrainBase.value || MERGE_DEFAULT_BASE;
    baseModelPath.value = m;
    mergeLoraOnly.value = true;

    if (!candidatePath.value) {
      loadError.value = "该训练条目缺少有效 LoRA 路径";
    }
  } catch (e: unknown) {
    loadError.value = String((e as { message?: string })?.message ?? e);
    message.error(loadError.value);
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.open, props.record?.id] as const,
  ([isOpen]) => {
    if (isOpen && props.record) {
      void loadCandidate();
    }
  },
  { immediate: true },
);

function onCancel() {
  emit("update:open", false);
}

function onStart() {
  const bp = (baseModelPath.value ?? "").trim();
  if (!bp) {
    message.warning("请填写基座模型路径");
    return;
  }
  if (!canStart.value) return;
  emit("start", { base_model_path: bp, merge_lora_only: mergeLoraOnly.value });
  emit("update:open", false);
}
</script>

<template>
  <a-modal
    :open="open"
    title="合并 LoRA"
    width="min(800px, 92vw)"
    ok-text="开始"
    cancel-text="取消"
    :confirm-loading="loading"
    :ok-button-props="{ disabled: !canStart }"
    destroy-on-close
    @ok="onStart"
    @cancel="onCancel"
  >
    <a-spin :spinning="loading" tip="加载合并参数…">
      <div v-if="record && jobId" class="train-merge-start-modal">
        <a-alert type="info" show-icon style="margin-bottom: 12px">
          <template #message>
            <div class="train-merge-start-modal__hint">{{ mergeConfirmDescription(record) }}</div>
          </template>
        </a-alert>
        <a-alert v-if="loadError" type="error" show-icon style="margin-bottom: 12px" :message="loadError" />
        <a-form layout="vertical" class="train-merge-start-modal__form">
          <a-form-item label="LoRA 路径（服务端解析）">
            <a-input :value="candidatePath || '—'" disabled />
          </a-form-item>
          <a-form-item label="合并产物写入（相对工作区）">
            <a-input :value="outputRel || '—'" disabled />
          </a-form-item>
          <a-form-item label="基座模型路径（base_model_path）" required>
            <a-input
              v-model:value="baseModelPath"
              disabled
            />
          </a-form-item>
          <a-form-item label="合并方式">
            <a-radio-group v-model:value="mergeLoraOnly">
              <a-radio :value="true">将 LoRA 合并进基座并保存全量模型</a-radio>
              <a-radio :value="false">仅导出 PEFT 适配器目录</a-radio>
            </a-radio-group>
          </a-form-item>
        </a-form>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.train-merge-start-modal__hint {
  white-space: pre-line;
  font-size: 12px;
  line-height: 1.55;
}
.train-merge-start-modal__form :deep(.ant-form-item) {
  margin-bottom: 12px;
}
.train-merge-start-modal__form :deep(.ant-form-item:last-child) {
  margin-bottom: 0;
}
</style>
