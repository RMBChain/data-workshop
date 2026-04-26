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
};

type MergeLogProgress = {
  percent: number | null;
  phase: string;
  fraction: string | null;
  phase_hint: string | null;
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
/** 与合并脚本 --merge_lora_only 一致，默认 true */
const mergeLoraOnly = ref(true);

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
const log = ref("");
const jobId = ref<string | null>(null);
const mergeJobStatus = ref<string | null>(null);
/** 来自 GET /api/merge/jobs/:id/logs 的 progress，由后端解析日志 */
const mergeProgress = ref<MergeLogProgress | null>(null);
const runSubmitting = ref(false);
let poller: ReturnType<typeof setInterval> | null = null;

function normalizeMergeProgress(raw: unknown): MergeLogProgress | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const percent = o.percent;
  return {
    percent: typeof percent === "number" && Number.isFinite(percent) ? percent : null,
    phase: typeof o.phase === "string" ? o.phase : "合并进行中",
    fraction: typeof o.fraction === "string" ? o.fraction : null,
    phase_hint: typeof o.phase_hint === "string" ? o.phase_hint : null,
  };
}

const successRows = ref<SuccessTrainingRow[]>([]);
const successLoading = ref(false);
const selectedJobIds = ref<string[]>([]);
const mergeStatusByJobId = ref<Record<string, MergeUiStatus>>({});
/** 仅合并成功时由 GET /api/merge/training-status 的 output_path_by_job_id 提供实际输出路径 */
const mergeOutputPathByJobId = ref<Record<string, string | null | undefined>>({});
/** 合并打包入库后由 zip_path_by_job_id 提供 zip 的工作区相对路径 */
const mergeZipPathByJobId = ref<Record<string, string | null | undefined>>({});

function parseMergeUiStatus(v: string | undefined): MergeUiStatus {
  if (v === "merging" || v === "interrupted" || v === "failed" || v === "success" || v === "none") {
    return v;
  }
  return "none";
}

type SuccessTableRow = SuccessTrainingRow & {
  merge_output_path: string;
  merge_status: MergeUiStatus;
  merge_zip_relpath: string;
};

const successTableRows = computed((): SuccessTableRow[] =>
  successRows.value.map((r) => {
    const merge_status = parseMergeUiStatus(mergeStatusByJobId.value[r.job_id]);
    const fromApi = mergeOutputPathByJobId.value[r.job_id];
    const p = fromApi == null || typeof fromApi !== "string" ? "" : fromApi.trim();
    const zraw = mergeZipPathByJobId.value[r.job_id];
    const z = zraw == null || typeof zraw !== "string" ? "" : zraw.trim();
    return {
      ...r,
      merge_output_path: merge_status === "success" ? p : "",
      merge_status,
      merge_zip_relpath: z,
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
    mergeZipPathByJobId.value = (r.data?.zip_path_by_job_id ?? {}) as Record<
      string,
      string | null | undefined
    >;
  } catch {
    mergeStatusByJobId.value = {};
    mergeOutputPathByJobId.value = {};
    mergeZipPathByJobId.value = {};
  }
}

async function loadSuccessList() {
  successLoading.value = true;
  try {
    const [r, , pathsR] = await Promise.all([
      http.get("/api/inference/models"),
      loadMergeStatus(),
      http.get("/api/system/paths").catch(() => ({ data: {} })),
    ]);
    const wr = (pathsR as { data?: { workspace_root?: unknown } }).data?.workspace_root;
    if (typeof wr === "string" && wr.trim()) {
      workspaceRootAbs.value = wr.trim();
    }
    const items = (r.data?.items ?? []) as SuccessTrainingRow[];
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
 * 使用当前表单中的 base / output 与给定的 `merge_lora_only` 发起合并（`base`/`output` 在选中行变化时由 watch 同步）。
 */
async function startMergeForRow(row: SuccessTrainingRow, loraOnly: boolean) {
  const paths = [row.path].map((s) => s.trim()).filter(Boolean);
  if (!paths.length) {
    message.error("该训练条目缺少有效 LoRA 路径");
    return;
  }
  runSubmitting.value = true;
  log.value = "";
  mergeProgress.value = null;
  mergeJobStatus.value = null;
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
    mergeJobStatus.value = String(r.data.status ?? "running");

    async function pollMergeOnce() {
      if (!jobId.value) return;
      try {
        const s = await http.get(`/api/merge/jobs/${jobId.value}`);
        mergeJobStatus.value = String(s.data.status ?? "");
        const t = await http.get(`/api/merge/jobs/${jobId.value}/logs`);
        log.value = t.data.text;
        mergeProgress.value = normalizeMergeProgress(t.data.progress);
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
  } catch (e: unknown) {
    message.error(String((e as { message?: string })?.message ?? e));
    jobId.value = null;
    mergeJobStatus.value = null;
    mergeProgress.value = null;
  } finally {
    runSubmitting.value = false;
  }
}

/** 与底部「执行合并」一致：使用复选框 `mergeLoraOnly` */
async function run() {
  const row = selectedRow.value;
  if (!row) {
    message.warning("请先在下方列表中选择一条已成功的训练任务");
    return;
  }
  await startMergeForRow(row, mergeLoraOnly.value);
}

/** 卡片上：先选中该训练，再合并；`merge_lora_only` 固定为 true */
async function runMergeForCard(row: SuccessTableRow) {
  selectSuccessRow(row.job_id);
  await startMergeForRow(row, true);
}

async function downloadMergeZip(row: SuccessTableRow) {
  const z = (row.merge_zip_relpath ?? "").trim();
  if (!z) {
    message.warning("暂无已打包的 zip，请先成功完成合并（打包需数秒，可稍后刷新列表）");
    return;
  }
  try {
    const res = await http.get(
      `/api/merge/training-jobs/${encodeURIComponent(row.job_id)}/export-zip`,
      { responseType: "blob" },
    );
    const blob = res.data as Blob;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `merged-model-${row.job_id}.zip`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e: unknown) {
    message.error(String((e as { message?: string })?.message ?? e));
  }
}

function goPlay() {
  void router.push("/playground");
}

onMounted(() => {
  void loadSuccessList();
});

onUnmounted(() => {
  if (poller) clearInterval(poller);
});
</script>

<template>
  <div>
    <a-typography-title :level="4">LoRA 合并</a-typography-title>
    <a-alert
      type="info"
      show-icon
      message="从已成功完成的训练任务中选择一条，再执行合并。合并子进程里多路 LoRA 时，当前实现仅对第一个有效路径做 merge（与后端约定一致）。"
      style="margin-bottom: 12px"
    />
    <div class="merge-section-title">
      <a-typography-title :level="5">训练成功列表（可合并列表）</a-typography-title>
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
                :disabled="runSubmitting || record.merge_status === 'merging'"
                :loading="runSubmitting"
                @click.stop="runMergeForCard(record)"
              >
                合并成模型
              </a-button>
              <a-button type="link">日志</a-button>
              <a-button
                type="link"
                :disabled="!record.merge_zip_relpath"
                @click.stop="downloadMergeZip(record)"
              >
                下载模型(zip)
              </a-button>
            </div>
          </div>
        </a-card>
      </div>
    </a-spin>
    <a-empty
      v-if="!successLoading && successRows.length === 0"
      description="暂无已成功的训练任务。"
      style="margin-bottom: 16px"
    >
      <a-button type="link" @click="() => router.push('/train')">去训练</a-button>
    </a-empty>
    <a-divider style="border-top: 2px solid rgba(0, 0, 0, 0.35)" />
    <a-typography-title :level="5">合并参数</a-typography-title>
    <a-form layout="vertical">
      <a-row :gutter="16">
        <a-col :span="7">
          <a-form-item label="基座（由所选训练决定，不可修改）">
            <a-input v-model:value="base" disabled />
          </a-form-item>
        </a-col>
        <a-col :span="7">
          <a-form-item label="将合并的 LoRA（由所选训练决定，不可手改）">
            <a-input :value="selectedRow?.path ?? '—（未选择训练）'" disabled />
          </a-form-item>
        </a-col>
        <a-col :span="7">
          <a-form-item label="输出目录（工作区相对）">
            <a-input v-model:value="output" />
          </a-form-item>
        </a-col>
      </a-row>
      <a-space direction="vertical" size="small" style="width: 100%; margin-top: 4px">
        <a-typography-text type="secondary" style="font-size: 12px; line-height: 1.5; display: block"
          ><code>--merge_lora_only</code>（合并脚本参数，默认 true）</a-typography-text
        >
        <a-checkbox
          v-model:checked="mergeLoraOnly"
          style="align-items: flex-start; line-height: 1.5"
        >
          将 LoRA 合并进基座并导出全量模型（关闭则仅导出 PEFT 适配器目录，默认开启）
        </a-checkbox>
      </a-space>
    </a-form>
    <a-button type="primary" :disabled="!selectedRow" :loading="runSubmitting" @click="run" >执行合并</a-button>

    <div
      v-if="
        jobId &&
        mergeJobStatus &&
        ['pending', 'running', 'succeeded', 'failed', 'cancelled'].includes(mergeJobStatus)
      "
      style="max-width: 600px; margin-top: 16px"
    >
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px">
        <a-typography-title :level="5" style="margin: 0">合并进度</a-typography-title>
        <a-tag v-if="mergeJobStatus === 'pending'" color="default">排队</a-tag>
        <a-tag v-else-if="mergeJobStatus === 'running'" color="processing">运行中</a-tag>
        <a-tag v-else-if="mergeJobStatus === 'succeeded'" color="success">已完成</a-tag>
        <a-tag v-else-if="mergeJobStatus === 'failed'" color="error">失败</a-tag>
        <a-tag v-else-if="mergeJobStatus === 'cancelled'" color="warning">已取消</a-tag>
      </div>
      <template v-if="mergeJobStatus === 'succeeded'">
        <a-progress :percent="100" status="success" />
        <div style="font-size: 12px; color: rgba(0, 0, 0, 0.65); margin-top: 4px">合并已完成，详见下方日志。</div>
      </template>
      <template v-else-if="mergeJobStatus === 'failed'">
        <a-progress :percent="mergeProgress?.percent ?? 0" status="exception" />
        <div style="font-size: 12px; color: rgba(0, 0, 0, 0.65); margin-top: 4px">
          {{ mergeProgress?.phase ? `${mergeProgress.phase} · ` : "" }}合并失败
        </div>
        <div
          v-if="mergeProgress?.phase_hint"
          style="font-size: 12px; color: rgba(0, 0, 0, 0.45); margin-top: 6px; line-height: 1.5"
        >
          {{ mergeProgress.phase_hint }}
        </div>
      </template>
      <template v-else-if="mergeJobStatus === 'cancelled'">
        <a-progress :percent="mergeProgress?.percent ?? 0" status="normal" />
        <div style="font-size: 12px; color: rgba(0, 0, 0, 0.65); margin-top: 4px">任务已取消。</div>
      </template>
      <template v-else>
        <a-progress
          :percent="mergeProgress?.percent ?? 0"
          :status="mergeProgress?.percent == null ? 'active' : 'normal'"
          :show-info="mergeProgress?.percent != null"
        />
        <div style="font-size: 12px; color: rgba(0, 0, 0, 0.65); margin-top: 4px">
          <template v-if="mergeProgress">
            {{ mergeProgress.phase }}
            <template v-if="mergeProgress.fraction"> · {{ mergeProgress.fraction }}</template>
            <template v-if="mergeProgress.percent == null">（等待详细百分比…）</template>
          </template>
          <template v-else>准备中…</template>
        </div>
        <div
          v-if="mergeProgress?.phase_hint"
          style="font-size: 12px; color: rgba(0, 0, 0, 0.45); margin-top: 6px; line-height: 1.5"
        >
          {{ mergeProgress.phase_hint }}
        </div>
      </template>
    </div>

    <a-typography-title :level="5" style="margin-top: 16px">日志</a-typography-title>
    <pre
      style="
        max-height: 600px;
        overflow: auto;
        font-size: 12px;
        background: #0d1117;
        color: #e6edf3;
        padding: 8px;
        border-radius: 4px;
        white-space: pre-wrap;
      "
      >{{ log }}</pre>
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
  flex-wrap: nowrap;
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
</style>
