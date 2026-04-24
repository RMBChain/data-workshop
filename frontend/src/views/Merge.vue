<script setup lang="ts">
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

const selectedRow = computed((): SuccessTrainingRow | null => {
  const id = selectedJobIds.value[0];
  if (!id) return null;
  return successRows.value.find((r) => r.job_id === id) ?? null;
});

const columns = [
  { title: "项目名称", dataIndex: "project_title", key: "project_title", ellipsis: true },
  { title: "批次名称", dataIndex: "batch_name", key: "batch_name", ellipsis: true },
  { title: "数据集名称", dataIndex: "dataset_name", key: "dataset_name", ellipsis: true },
  { title: "训练名称", dataIndex: "job_name", key: "job_name", ellipsis: true },
  { title: "基座", dataIndex: "train_base_model", key: "train_base_model", ellipsis: true },
  { title: "LoRA 路径", dataIndex: "path", key: "path", ellipsis: true },
];

const rowSelection = computed(() => ({
  type: "radio" as const,
  selectedRowKeys: selectedJobIds.value,
  onChange: (keys: (string | number)[]) => {
    selectedJobIds.value = keys.map((k) => String(k));
  },
}));

async function loadSuccessList() {
  successLoading.value = true;
  try {
    const r = await http.get("/api/inference/models");
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

async function run() {
  const row = selectedRow.value;
  if (!row) {
    message.warning("请先在下方列表中选择一条已成功的训练任务");
    return;
  }
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
    <a-divider style="border-top: 2px solid rgba(0, 0, 0, 0.35)" />
    <a-alert
      type="info"
      show-icon
      message="从已成功完成的训练任务中选择一条，再执行合并。合并子进程里多路 LoRA 时，当前实现仅对第一个有效路径做 merge（与后端约定一致）。"
      style="margin-bottom: 12px"
    />
    <a-typography-title :level="5">训练成功（可合并）</a-typography-title>
    <a-space style="margin-bottom: 8px">
      <a-button size="small" :loading="successLoading" @click="loadSuccessList">刷新列表</a-button>
    </a-space>
    <a-table
      :columns="columns"
      :data-source="successRows"
      :loading="successLoading"
      :pagination="false"
      :row-selection="rowSelection"
      row-key="job_id"
      size="small"
      :scroll="{ x: 'max-content' }"
      style="width: 100%; margin-bottom: 16px"
    />
    <a-empty
      v-if="!successLoading && successRows.length === 0"
      description="暂无已成功的训练任务（或磁盘上已找不到 LoRA/adapter）"
      style="margin-bottom: 16px"
    >
      <a-button type="link" @click="() => router.push('/training')">去训练</a-button>
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
      >{{ log }}</pre
    >
  </div>
</template>
