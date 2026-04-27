<script setup lang="ts">
import { ReloadOutlined } from "@ant-design/icons-vue";
import { message } from "ant-design-vue";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { http } from "../api/http";

type SuccessTrainingRow = {
  id: string;
  kind: string;
  path: string;
  train_base_model: string | null;
  job_id: string;
  job_name: string;
  label: string;
  project_title?: string | null;
  batch_name?: string | null;
  dataset_name?: string | null;
  /** 与训练表单的「数据集版本」ID 一致（数据 id） */
  dataset_version_id?: string | null;
  /** ms-swift add_version 子目录（如 v0-…），默认合并输出目录末段；无则回退 job_id */
  swift_train_version?: string | null;
  training_status: string;
  error_message?: string | null;
};

/** 与 GET /api/merge/training-status 一致 */
type MergeUiStatus = "none" | "merging" | "interrupted" | "failed" | "success";

const MERGE_STATUS_LABEL: Record<MergeUiStatus, string> = {
  none: "未合并",
  merging: "合并中",
  interrupted: "合并中断",
  failed: "合并失败",
  success: "合并成功",
};

function trainingStatusLabel(s: string | undefined): string {
  const x = (s ?? "").trim();
  if (x === "succeeded") return "成功";
  if (x === "failed") return "失败";
  if (x === "cancelled") return "已取消";
  if (x === "running") return "进行中";
  if (x === "pending") return "排队";
  if (x === "parameters_saved") return "仅参数";
  return x || "—";
}

function trainingStatusTagColor(s: string | undefined): string {
  if (s === "succeeded") return "success";
  if (s === "failed") return "error";
  if (s === "cancelled") return "warning";
  if (s === "running" || s === "pending") return "processing";
  return "default";
}

function mergeStatusTagColor(s: MergeUiStatus): string {
  if (s === "none") return "default";
  if (s === "merging") return "processing";
  if (s === "interrupted") return "warning";
  if (s === "failed") return "error";
  return "success";
}

function tableCellText(record: SuccessTableRow, dataIndex: string | undefined | unknown): string {
  if (!dataIndex || typeof dataIndex !== "string") return "—";
  const v = (record as Record<string, unknown>)[dataIndex];
  if (v == null) return "—";
  const s = String(v).trim();
  return s || "—";
}

/** 仅训练成功且存在可合并 LoRA 路径时可点「合并成全量模型」 */
function canMergeTrainingRecord(r: SuccessTableRow): boolean {
  return r.training_status === "succeeded" && !!(r.path || "").trim();
}

/** 工作区相对路径 → 后端工作区根下的绝对路径（用于 Tooltip）；无根信息时退回相对路径 */
const workspaceRootAbs = ref<string | null>(null);

function workspaceAbsoluteDisplayPath(relOrDash: string): string {
  const raw = (relOrDash ?? "").trim();
  if (!raw || raw === "—") return raw || "—";
  const norm = raw.replace(/\\/g, "/");
  if (norm.startsWith("/") || /^[A-Za-z]:\//.test(norm)) {
    return norm;
  }
  const root = (workspaceRootAbs.value ?? "").trim();
  if (!root) return norm;
  const rn = root.replace(/\\/g, "/").replace(/\/+$/, "");
  const pn = norm.replace(/^\/+/, "");
  return `${rn}/${pn}`;
}

const router = useRouter();
const base = ref("Qwen/Qwen3-VL-2B-Instruct");
const output = ref("output/merged-workshop");

/** 默认：output/merged-workshop/{数据 id}/{训练版本}，训练版本为 v0-…（无则 job_id）。 */
function defaultMergeOutputPath(row: SuccessTrainingRow): string {
  const dataId = (row.dataset_version_id ?? "").trim();
  const trainVer =
    (row.swift_train_version ?? "").trim() || (row.job_id ?? "").trim();
  const root = "output/merged-workshop";
  if (dataId && trainVer) {
    return `${root}/${dataId}/${trainVer}`.replace(/\/+/g, "/");
  }
  if (trainVer) {
    return `${root}/${trainVer}`.replace(/\/+/g, "/");
  }
  return root;
}
const jobId = ref<string | null>(null);
const runSubmitting = ref(false);
const mergeLogModalOpen = ref(false);
const mergeLogModalText = ref("");
const mergeLogModalTrainingJobId = ref<string | null>(null);
const mergeLogLoading = ref(false);
const trainRunLogModalOpen = ref(false);
const trainRunLogText = ref("");
const trainRunLogTid = ref<string | null>(null);
const trainRunLogLoading = ref(false);
let poller: ReturnType<typeof setInterval> | null = null;
let logModalPoller: ReturnType<typeof setInterval> | null = null;
let trainRunLogModalPoller: ReturnType<typeof setInterval> | null = null;
let trainRunPollCount = 0;

const successRows = ref<SuccessTrainingRow[]>([]);
const successLoading = ref(false);
const selectedJobIds = ref<string[]>([]);
const mergeStatusByJobId = ref<Record<string, MergeUiStatus>>({});
/** 仅合并成功时由 GET /api/merge/training-status 的 output_path_by_job_id 提供实际输出路径 */
const mergeOutputPathByJobId = ref<Record<string, string | null | undefined>>({});

function parseMergeUiStatus(v: string | undefined): MergeUiStatus {
  if (v === "merging" || v === "interrupted" || v === "failed" || v === "success" || v === "none") {
    return v;
  }
  return "none";
}

type SuccessTableRow = SuccessTrainingRow & {
  merge_output_path: string;
  merge_status: MergeUiStatus;
  training_status_label: string;
};

const successTableRows = computed((): SuccessTableRow[] =>
  successRows.value.map((r) => {
    const merge_status = parseMergeUiStatus(mergeStatusByJobId.value[r.job_id]);
    const fromApi = mergeOutputPathByJobId.value[r.job_id];
    const p = fromApi == null || typeof fromApi !== "string" ? "" : fromApi.trim();
    return {
      ...r,
      merge_output_path: merge_status === "success" ? p : "",
      merge_status,
      training_status_label: trainingStatusLabel(r.training_status),
    };
  }),
);

const selectedRow = computed((): SuccessTrainingRow | null => {
  const id = selectedJobIds.value[0];
  if (!id) return null;
  return successRows.value.find((r) => r.job_id === id) ?? null;
});

type MergeCardMetaItem = {
  title: string;
  dataIndex: keyof SuccessTableRow | string;
  /** 悬停时用 Tooltip 展示完整路径 */
  fullPathTooltip?: boolean;
};

/** 卡片正文展示的字段（训练名称在卡片标题，合并状态在 extra 标签） */
const mergeCardMetaItems: MergeCardMetaItem[] = [
  { title: "训练", dataIndex: "training_status_label" },
  { title: "训练备注", dataIndex: "error_message" },
  { title: "项目名称", dataIndex: "project_title" },
  { title: "批次名称", dataIndex: "batch_name" },
  { title: "数据集名称", dataIndex: "dataset_name" },
  { title: "基座", dataIndex: "train_base_model" },
  { title: "LoRA 路径", dataIndex: "path", fullPathTooltip: true },
  { title: "合并后模型路径", dataIndex: "merge_output_path", fullPathTooltip: true },
];

function selectSuccessRow(jobId: string) {
  selectedJobIds.value = [jobId];
}

async function loadMergeStatus() {
  try {
    const r = await http.get("/api/merge/training-status");
    const m = (r.data?.status_by_job_id ?? {}) as Record<string, string>;
    const next: Record<string, MergeUiStatus> = {};
    for (const [k, v] of Object.entries(m)) {
      next[k] = parseMergeUiStatus(v);
    }
    mergeStatusByJobId.value = next;
    mergeOutputPathByJobId.value = (r.data?.output_path_by_job_id ?? {}) as Record<
      string,
      string | null | undefined
    >;
  } catch {
    // 请求失败时保留上一帧状态，避免整表从「合并成功」落到「未合并」的闪烁/误判
  }
}

async function loadSuccessList() {
  successLoading.value = true;
  try {
    const [r, , pathsR] = await Promise.all([
      http.get("/api/merge/training-candidates"),
      loadMergeStatus(),
      http.get("/api/system/paths").catch(() => ({ data: {} })),
    ]);
    const wr = (pathsR as { data?: { workspace_root?: unknown } }).data?.workspace_root;
    if (typeof wr === "string" && wr.trim()) {
      workspaceRootAbs.value = wr.trim();
    }
    const raw = (r.data?.items ?? []) as unknown[];
    const items = raw.map((x: unknown) => {
      const o = (x && typeof x === "object" ? x : {}) as Record<string, unknown>;
      return {
        ...o,
        training_status: String(o.training_status ?? "succeeded"),
        path: String(o.path ?? ""),
      } as SuccessTrainingRow;
    });
    successRows.value = items;
    const cur = selectedJobIds.value[0];
    if (cur && !items.some((x) => x.job_id === cur)) {
      selectedJobIds.value = [];
    }
  } catch (e: unknown) {
    message.error(String((e as { message?: string })?.message ?? e));
  } finally {
    successLoading.value = false;
  }
}

watch(
  () => selectedRow.value,
  (row) => {
    const m = row?.train_base_model?.trim();
    if (m) {
      base.value = m;
    }
    if (row) {
      output.value = defaultMergeOutputPath(row);
    } else {
      output.value = "output/merged-workshop";
    }
  },
);

/**
 * 使用 watch 同步的 `base` / `output` 与 `merge_lora_only` 发起合并（选中卡片后由 `defaultMergeOutputPath` 等决定输出目录）。
 */
async function startMergeForRow(row: SuccessTrainingRow, loraOnly: boolean) {
  const paths = [row.path].map((s) => s.trim()).filter(Boolean);
  if (!paths.length) {
    message.error("该训练条目缺少有效 LoRA 路径");
    return;
  }
  runSubmitting.value = true;
  if (poller) clearInterval(poller);
  poller = null;
  try {
    const r = await http.post("/api/merge/jobs", {
      base_model_path: base.value,
      lora_paths: paths,
      output_path: output.value,
      merge_lora_only: loraOnly,
      training_job_id: row.job_id,
    });
    jobId.value = r.data.id;

    async function pollMergeOnce() {
      if (!jobId.value) return;
      try {
        const s = await http.get(`/api/merge/jobs/${jobId.value}`);
        void loadMergeStatus();
        if (["succeeded", "failed", "cancelled"].includes(String(s.data.status))) {
          if (poller) clearInterval(poller);
          poller = null;
          if (s.data.status === "succeeded") {
            message.success("合并完成");
            await http.post(`/api/merge/jobs/${jobId.value}/validate`);
          } else if (s.data.status === "failed") {
            message.error(String(s.data.error_message ?? "合并失败"));
          } else if (s.data.status === "cancelled") {
            message.warning("合并已取消");
          }
        }
      } catch (e: unknown) {
        message.error(String((e as { message?: string })?.message ?? e));
      }
    }

    void pollMergeOnce();
    poller = setInterval(() => void pollMergeOnce(), 1500);
    if (mergeLogModalOpen.value && mergeLogModalTrainingJobId.value === row.job_id) {
      scheduleLogModalPoll(row.job_id);
    }
  } catch (e: unknown) {
    message.error(String((e as { message?: string })?.message ?? e));
    jobId.value = null;
  } finally {
    runSubmitting.value = false;
  }
}

/** 卡片上：先选中该训练，再合并；`merge_lora_only` 固定为 true */
async function runMergeForCard(row: SuccessTableRow) {
  selectSuccessRow(row.job_id);
  await startMergeForRow(row, true);
}

function stopLogModalPoller() {
  if (logModalPoller) {
    clearInterval(logModalPoller);
    logModalPoller = null;
  }
}

async function loadMergeLogForModal(tid: string) {
  if (!tid) return;
  const r = await http.get(`/api/merge/training-jobs/${encodeURIComponent(tid)}/logs`);
  mergeLogModalText.value = String(r.data?.text ?? "");
}

/** 在合并进行中间隔拉取；合并结束后自动停止。 */
function scheduleLogModalPoll(tid: string) {
  stopLogModalPoller();
  logModalPoller = setInterval(() => {
    if (!mergeLogModalOpen.value || mergeLogModalTrainingJobId.value !== tid) {
      stopLogModalPoller();
      return;
    }
    void (async () => {
      try {
        await loadMergeLogForModal(tid);
        await loadMergeStatus();
        if (mergeStatusByJobId.value[tid] !== "merging") {
          stopLogModalPoller();
        }
      } catch {
        // 轮询失败时保留已显示内容
      }
    })();
  }, 1500);
}

async function openMergeLogModal(row: SuccessTableRow) {
  const tid = row.job_id;
  mergeLogModalTrainingJobId.value = tid;
  mergeLogLoading.value = true;
  mergeLogModalOpen.value = true;
  stopLogModalPoller();
  try {
    await loadMergeLogForModal(tid);
    await loadMergeStatus();
  } catch (e: unknown) {
    message.error(String((e as { message?: string })?.message ?? e));
    mergeLogModalText.value = "";
  } finally {
    mergeLogLoading.value = false;
  }
  if (mergeStatusByJobId.value[tid] === "merging") {
    scheduleLogModalPoll(tid);
  }
}

watch(mergeLogModalOpen, (open) => {
  if (!open) {
    stopLogModalPoller();
    mergeLogModalTrainingJobId.value = null;
  }
});

function stopTrainRunLogModalPoller() {
  if (trainRunLogModalPoller) {
    clearInterval(trainRunLogModalPoller);
    trainRunLogModalPoller = null;
  }
  trainRunPollCount = 0;
}

async function loadTrainRunLogForModal(tid: string) {
  if (!tid) return;
  const r = await http.get(
    `/api/merge/training-jobs/${encodeURIComponent(tid)}/train-run-logs`,
  );
  trainRunLogText.value = String(r.data?.text ?? "");
}

function scheduleTrainRunLogModalPoll(tid: string) {
  stopTrainRunLogModalPoller();
  trainRunLogModalPoller = setInterval(() => {
    if (!trainRunLogModalOpen.value || trainRunLogTid.value !== tid) {
      stopTrainRunLogModalPoller();
      return;
    }
    void (async () => {
      try {
        await loadTrainRunLogForModal(tid);
        trainRunPollCount += 1;
        if (trainRunPollCount % 2 === 0) {
          await loadSuccessList();
        }
        const tr = successRows.value.find((x) => x.job_id === tid);
        if (tr && tr.training_status !== "pending" && tr.training_status !== "running") {
          stopTrainRunLogModalPoller();
        }
      } catch {
        // 轮询失败时保留已显示内容
      }
    })();
  }, 1500);
}

async function openTrainRunLogModal(row: SuccessTableRow) {
  const tid = row.job_id;
  trainRunLogTid.value = tid;
  trainRunLogLoading.value = true;
  trainRunLogModalOpen.value = true;
  stopTrainRunLogModalPoller();
  try {
    await loadTrainRunLogForModal(tid);
  } catch (e: unknown) {
    message.error(String((e as { message?: string })?.message ?? e));
    trainRunLogText.value = "";
  } finally {
    trainRunLogLoading.value = false;
  }
  if (row.training_status === "pending" || row.training_status === "running") {
    scheduleTrainRunLogModalPoll(tid);
  }
}

watch(trainRunLogModalOpen, (open) => {
  if (!open) {
    stopTrainRunLogModalPoller();
    trainRunLogTid.value = null;
  }
});

onMounted(() => {
  void loadSuccessList();
});

onUnmounted(() => {
  if (poller) clearInterval(poller);
  stopLogModalPoller();
  stopTrainRunLogModalPoller();
});
</script>

<template>
  <div>
    <a-typography-title :level="4">LoRA 合并</a-typography-title>
    <a-alert
      type="info"
      show-icon
      message="本页展示已落库的训练任务（含成功与失败）。仅训练成功且存在有效 LoRA 路径时可执行「合并成全量模型」。「训练日志」走合并域接口，与训练页拉取的日志相独立；「合并日志」为 LoRA 合并子进程输出。"
      style="margin-bottom: 12px"
    />
    <div class="merge-section-title">
      <a-typography-title :level="5">训练任务</a-typography-title>
      <a-tooltip title="刷新列表" placement="bottomRight" :auto-adjust-overflow="false">
        <a-button
          type="text"
          size="small"
          :loading="successLoading"
          aria-label="刷新列表"
          @click="loadSuccessList"
        >
          <template #icon>
            <ReloadOutlined />
          </template>
        </a-button>
      </a-tooltip>
    </div>
    <a-spin :spinning="successLoading">
      <div v-if="successTableRows.length" class="merge-success-card-grid" >
        <a-card
          v-for="record in successTableRows"
          :key="record.job_id"
          class="merge-success-card"
          :class="{ 'merge-success-card--selected': selectedJobIds[0] === record.job_id }"
          size="small"
          hoverable
          @click="selectSuccessRow(record.job_id)"
        >
          <template #title>
            <div class="merge-success-card-title">
              <span
                class="merge-success-card-title-text"
                :title="tableCellText(record, 'job_name')"
              >
                {{ tableCellText(record, "job_name") }}
              </span>
            </div>
          </template>
          <template #extra>
            <a-tag :color="mergeStatusTagColor(record.merge_status)" @click.stop>
              {{ MERGE_STATUS_LABEL[record.merge_status] }}
            </a-tag>
          </template>
          <div class="merge-success-card-meta">
            <div
              v-for="item in mergeCardMetaItems"
              :key="String(item.dataIndex)"
              class="merge-success-card-meta-row"
            >
              <span class="merge-success-card-meta-label">{{ item.title }}</span>
              <div
                v-if="item.fullPathTooltip && tableCellText(record, item.dataIndex) !== '—'"
                class="merge-success-card-meta-value-wrap"
              >
                <a-tooltip
                  :title="workspaceAbsoluteDisplayPath(tableCellText(record, item.dataIndex))"
                  placement="topLeft"
                  :overlay-style="{ maxWidth: 'min(90vw, 560px)' }"
                >
                  <span class="merge-success-card-meta-value merge-success-card-meta-value--ellipsis">
                    {{ tableCellText(record, item.dataIndex) }}
                  </span>
                </a-tooltip>
              </div>
              <span
                v-else
                class="merge-success-card-meta-value"
                :title="tableCellText(record, item.dataIndex)"
              >
                {{ tableCellText(record, item.dataIndex) }}
              </span>
            </div>
            <div class="merge-success-card-meta-actions">
              <a-button
                type="link"
                :disabled="runSubmitting || record.merge_status === 'merging' || !canMergeTrainingRecord(record)"
                :loading="runSubmitting"
                @click.stop="runMergeForCard(record)"
              >
                合并成全量模型
              </a-button>
              <a-button type="link" @click.stop="openTrainRunLogModal(record)">训练日志</a-button>
              <a-button type="link" @click.stop="openMergeLogModal(record)">合并日志</a-button>
            </div>
          </div>
        </a-card>
      </div>
    </a-spin>
    <a-empty
      v-if="!successLoading && successRows.length === 0"
      description="暂无训练任务。"
      style="margin-bottom: 16px"
    >
      <a-button type="link" @click="() => router.push('/train')">去训练</a-button>
    </a-empty>

    <a-modal
      v-model:open="trainRunLogModalOpen"
      title="训练日志"
      width="min(1200px, 90vw)"
      :footer="null"
      destroy-on-close
    >
      <a-spin :spinning="trainRunLogLoading" tip="加载中…">
        <pre class="merge-log-pre merge-log-pre--modal">{{
          trainRunLogText || "（暂无训练子进程输出。未开训、仅保存参数、或日志文件已清理时可能为空。）"
        }}</pre>
      </a-spin>
    </a-modal>
    <a-modal
      v-model:open="mergeLogModalOpen"
      title="合并日志"
      width="min(1200px, 90vw)"
      :footer="null"
      destroy-on-close
    >
      <a-spin :spinning="mergeLogLoading" tip="加载中…">
        <pre class="merge-log-pre merge-log-pre--modal">{{
          mergeLogModalText || "（暂无该训练任务的合并日志。在卡片上执行「合并成全量模型」后可见。）"
        }}</pre>
      </a-spin>
    </a-modal>
  </div>
</template>

<style scoped>
.merge-section-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 8px;
  margin: 0 0 0.5em 0;
}
.merge-section-title :deep(.ant-typography) {
  margin-bottom: 0;
}
.merge-success-card-grid {
  margin-top: 4px;
  margin-bottom: 16px;
  width: 100%;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 350px), 1fr));
  gap: 16px;
}
.merge-success-card {
  cursor: pointer;
  border-radius: 10px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.merge-success-card :deep(.ant-card-head) {
  min-height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.merge-success-card :deep(.ant-card-head-title) {
  padding: 10px 0;
  min-width: 0;
}
.merge-success-card :deep(.ant-card-extra) {
  padding: 10px 0;
}
.merge-success-card :deep(.ant-card-body) {
  padding: 12px 16px 14px;
}
.merge-success-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.merge-success-card-title-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  font-size: 14px;
  color: rgba(0, 0, 0, 0.88);
}
.merge-success-card--selected {
  border-color: var(--ant-primary-color, #1677ff);
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--ant-primary-color, #1677ff) 40%, transparent),
    0 6px 16px rgba(22, 119, 255, 0.16);
}
.merge-success-card-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12px;
}
.merge-success-card-meta-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  min-width: 0;
}
.merge-success-card-meta-actions {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
.merge-success-card-meta-actions :deep(.ant-btn) {
  padding-inline: 4px;
}
.merge-success-card-meta-label {
  flex-shrink: 0;
  width: 96px;
  color: rgba(0, 0, 0, 0.45);
  line-height: 1.5;
}
.merge-success-card-meta-value {
  flex: 1;
  min-width: 0;
  line-height: 1.5;
  word-break: break-all;
  color: rgba(0, 0, 0, 0.85);
}
.merge-success-card-meta-value-wrap {
  flex: 1;
  min-width: 0;
}
.merge-success-card-meta-value-wrap :deep(.ant-tooltip-disabled-compatible-wrapper) {
  display: block;
  max-width: 100%;
}
.merge-success-card-meta-value--ellipsis {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  word-break: normal;
}
.merge-log-pre {
  max-height: 600px;
  overflow: auto;
  font-size: 12px;
  background: #0d1117;
  color: #e6edf3;
  padding: 8px;
  border-radius: 4px;
  white-space: pre-wrap;
  margin: 0;
}
.merge-log-pre--modal {
  height: 600px;
}
</style>
