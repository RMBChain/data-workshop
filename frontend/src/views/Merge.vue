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
};

const router = useRouter();
const base = ref("Qwen/Qwen3-VL-2B-Instruct");
const output = ref("output/merged-workshop");
const log = ref("");
const jobId = ref<string | null>(null);
let poller: ReturnType<typeof setInterval> | null = null;

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
  const r = await http.post("/api/merge/jobs", {
    base_model_path: base.value,
    lora_paths: paths,
    output_path: output.value,
  });
  jobId.value = r.data.id;
  if (poller) clearInterval(poller);
  poller = setInterval(async () => {
    if (!jobId.value) return;
    const s = await http.get(`/api/merge/jobs/${jobId.value}`);
    const t = await http.get(`/api/merge/jobs/${jobId.value}/logs`);
    log.value = t.data.text;
    if (["succeeded", "failed", "cancelled"].includes(String(s.data.status))) {
      if (poller) clearInterval(poller);
      if (s.data.status === "succeeded") {
        message.success("合并完成");
        await http.post(`/api/merge/jobs/${jobId.value}/validate`);
      }
    }
  }, 1500);
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

    <a-form layout="vertical" style="max-width: 600px">
      <a-form-item label="基座（由所选训练决定，不可修改）">
        <a-input v-model:value="base" disabled />
      </a-form-item>
      <a-form-item label="将合并的 LoRA（由所选训练决定，不可手改）">
        <a-input :value="selectedRow?.path ?? '—（未选择训练）'" disabled />
      </a-form-item>
      <a-form-item label="输出目录（工作区相对）">
        <a-input v-model:value="output" />
      </a-form-item>
      <a-button type="primary" :disabled="!selectedRow" @click="run">执行合并</a-button>
      <a-button type="link" @click="goPlay">去推理试跑</a-button>
    </a-form>
    <a-typography-title :level="5" style="margin-top: 16px">日志</a-typography-title>
    <pre
      style="
        max-height: 360px;
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
