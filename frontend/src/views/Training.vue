<script setup lang="ts">
import { message } from "ant-design-vue";
import { ReloadOutlined } from "@ant-design/icons-vue";
import * as echarts from "echarts";
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { http } from "../api/http";

type HubModelRow = { model_id: string; path: string; size_bytes: number };

const router = useRouter();
const hubModels = ref<HubModelRow[]>([]);
const loadingHub = ref(false);

const form = reactive({
  model: "" as string,
  train_dataset: "",
  val_dataset: "",
  output_dir: "output/qwen3vl-2b-lora",
  lora_rank: 1,
  lora_alpha: 2,
  target_modules: "all-linear",
  num_train_epochs: 1,
  per_device_train_batch_size: 1,
  gradient_accumulation_steps: 1,
  learning_rate: 0.0001,
  max_length: 128,
  image_max_token_num: 64,
  video_max_token_num: 16,
  gradient_checkpointing: true,
  save_steps: 1000000,
  eval_steps: 1000000,
  save_total_limit: 1,
});

const activeDatasetHint = ref("");
const datasetPathModalOpen = ref(false);
const datasetPathModalTitle = ref("");
const datasetPathModalText = ref("");
const datasetPathModalLoading = ref(false);
const datasetPathModalError = ref("");
const loadingDatasetPaths = ref(false);
const submitting = ref(false);
const currentJobId = ref<string | null>(null);
const logText = ref("");
const logPre = ref<HTMLPreElement | null>(null);
const jobStatus = ref("");
const stick = ref(true);
const jobs = ref<Record<string, unknown>[]>([]);
let logPoll: ReturnType<typeof setInterval> | null = null;
let resPoll: ReturnType<typeof setInterval> | null = null;
const resInfo = ref("");
const chartRef = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;
const progressPercent = ref<number | null>(null);
const progressLabel = ref("");
const jobError = ref("");

const modelSelectOptions = computed(() =>
  hubModels.value.map((m) => ({
    value: m.model_id,
    label: `${m.model_id} · ${formatBytes(m.size_bytes)}`,
  })),
);

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function syncModelFromHub() {
  const ids = new Set(hubModels.value.map((m) => m.model_id));
  if (form.model && ids.has(form.model)) return;
  form.model = hubModels.value[0]?.model_id ?? "";
}

async function loadHubModels() {
  loadingHub.value = true;
  try {
    const r = await http.get<{ items: HubModelRow[] }>("/api/models/hub");
    hubModels.value = r.data.items ?? [];
    syncModelFromHub();
  } catch {
    hubModels.value = [];
    message.error("无法加载已下载模型列表，请检查网络或稍后重试");
  } finally {
    loadingHub.value = false;
  }
}

type DatasetVersionRow = {
  id: string;
  name?: string | null;
  note?: string | null;
  train_relpath?: string | null;
  val_relpath?: string | null;
  is_active?: boolean;
  train_count?: number;
  val_count?: number;
};

const datasetVersionItems = ref<DatasetVersionRow[]>([]);
const serverActiveVersionId = ref<string | null>(null);
const selectedDatasetVersionId = ref<string | null | undefined>(null);

const datasetSelectOptions = computed(() =>
  datasetVersionItems.value.map((v) => ({
    value: v.id,
    label: versionRowLabel(v),
  })),
);

function versionRowLabel(v: DatasetVersionRow) {
  const name = (v.name ?? v.id).trim() || v.id;
  const tr = v.train_count;
  const va = v.val_count;
  const cnt =
    typeof tr === "number" || typeof va === "number" ? ` · train ${tr ?? "—"}/val ${va ?? "—"}` : "";
  return `${name}${cnt}`;
}

function applyVersionToForm(row: DatasetVersionRow | null) {
  if (!row) {
    form.train_dataset = "";
    form.val_dataset = "";
    activeDatasetHint.value = "暂无可用数据集版本，请先在「数据集」中构建一版。";
    return;
  }
  const tr = (row.train_relpath ?? "").trim();
  const va = (row.val_relpath ?? "").trim();
  form.train_dataset = tr;
  form.val_dataset = va;
  const name = (row.name ?? row.id).trim() || row.id;
  const active = Boolean(row.is_active) || row.id === serverActiveVersionId.value;
  const badge = active ? "（与工作区「激活」一致）" : "（与「激活」不同，仅本次训练使用）";
  activeDatasetHint.value = `已选：${name} ${badge}`;
}

function filterDatasetOption(input: string, option: { label?: string; value?: string }) {
  const q = input.trim().toLowerCase();
  if (!q) return true;
  const label = String(option?.label ?? "").toLowerCase();
  const value = String(option?.value ?? "").toLowerCase();
  if (label.includes(q) || value.includes(q)) return true;
  const parts = q.split(/\s+/).filter(Boolean);
  if (parts.length > 1) {
    return parts.every((p) => !p || label.includes(p) || value.includes(p));
  }
  return false;
}

function onDatasetVersionSelect(versionId: string | null | undefined) {
  if (versionId == null || versionId === "") {
    applyVersionToForm(null);
    return;
  }
  const row = datasetVersionItems.value.find((x) => x.id === versionId) ?? null;
  applyVersionToForm(row);
}

/** 拉取版本列表，并根据选项更新选中行与表单项路径 */
async function loadDatasetVersions(opts?: { forceSelectActive?: boolean }) {
  loadingDatasetPaths.value = true;
  try {
    const r = await http.get<{
      active_version_id: string | null;
      items: DatasetVersionRow[];
    }>("/api/datasets/versions");
    serverActiveVersionId.value = r.data.active_version_id;
    const items = r.data.items ?? [];
    datasetVersionItems.value = items;
    const ids = new Set(items.map((x) => x.id));
    const act = r.data.active_version_id;

    let pick: string | null = null;
    if (opts?.forceSelectActive) {
      if (act && ids.has(act)) pick = act;
      else if (items[0]) pick = items[0].id;
    } else {
      const cur = selectedDatasetVersionId.value;
      if (cur && ids.has(cur)) pick = cur;
      else if (act && ids.has(act)) pick = act;
      else if (items[0]) pick = items[0].id;
    }

    selectedDatasetVersionId.value = pick;
    const row = pick ? (items.find((x) => x.id === pick) ?? null) : null;
    applyVersionToForm(row);
  } catch {
    datasetVersionItems.value = [];
    serverActiveVersionId.value = null;
    selectedDatasetVersionId.value = null;
    form.train_dataset = "";
    form.val_dataset = "";
    activeDatasetHint.value = "无法加载数据集版本列表。";
  } finally {
    loadingDatasetPaths.value = false;
  }
}

async function openDatasetPathModal(kind: "train" | "val") {
  const relpath = (kind === "train" ? form.train_dataset : form.val_dataset).trim();
  datasetPathModalTitle.value = kind === "train" ? "训练集 (jsonl)" : "验证集 (jsonl)";
  datasetPathModalText.value = "";
  datasetPathModalError.value = "";
  datasetPathModalOpen.value = true;
  datasetPathModalLoading.value = true;
  if (!relpath) {
    datasetPathModalError.value = "无文件路径";
    datasetPathModalLoading.value = false;
    return;
  }
  const maxBytes = 1_048_576;
  try {
    const r = await http.get<{
      text: string;
      truncated: boolean;
      size_bytes: number;
      max_bytes: number;
    }>("/api/datasets/file-text", {
      params: { relpath, max_bytes: maxBytes },
    });
    const body = r.data;
    let t = body.text ?? "";
    if (body.truncated) {
      t += `\n\n… 已截断：文件共 ${body.size_bytes} 字节，仅显示前 ${body.max_bytes} 字节。`;
    } else if (!t && body.size_bytes === 0) {
      t = "（文件为空）";
    }
    datasetPathModalText.value = t;
  } catch (e: unknown) {
    const ax = e as { response?: { data?: { detail?: unknown } } };
    const detail = ax.response?.data?.detail;
    datasetPathModalError.value =
      typeof detail === "string" ? detail : "无法读取文件内容，请确认路径可访问或稍后重试";
  } finally {
    datasetPathModalLoading.value = false;
  }
}

function goModelSettings() {
  void router.push({ path: "/", query: { tab: "models" } });
}

function goDatasets() {
  void router.push({ name: "datasets" });
}

const progressStatus = computed(() => {
  if (jobStatus.value === "succeeded") return "success" as const;
  if (jobStatus.value === "failed" || jobStatus.value === "cancelled") return "exception" as const;
  if (["running", "pending"].includes(jobStatus.value) && progressPercent.value == null) return "active" as const;
  return "normal" as const;
});

const jobColumns = [
  { title: "ID", dataIndex: "id", key: "id", ellipsis: true, width: 200 },
  { title: "开始时间", dataIndex: "created_at", key: "created_at", width: 170 },
  { title: "结束时间", dataIndex: "finished_at", key: "finished_at", width: 170 },
  { title: "状态", dataIndex: "status", key: "status", width: 100 },
  {
    title: "操作",
    key: "act",
    width: 100,
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

onMounted(() => {
  void loadHubModels();
  void loadDatasetVersions();
  void refreshJobs();
  resPoll = setInterval(async () => {
    try {
      const r = await http.get("/api/system/resources");
      resInfo.value = JSON.stringify(r.data);
    } catch {
      resInfo.value = "";
    }
  }, 2000);
});
onUnmounted(() => {
  stopLogPoll();
  if (resPoll) clearInterval(resPoll);
  if (chart) {
    chart.dispose();
    chart = null;
  }
});

function stopLogPoll() {
  if (logPoll) {
    clearInterval(logPoll);
    logPoll = null;
  }
}

function onLogScroll() {
  const el = logPre.value;
  if (!el) return;
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  stick.value = nearBottom;
}

function scrollToBottom() {
  const el = logPre.value;
  if (el) {
    el.scrollTop = el.scrollHeight;
    stick.value = true;
  }
}

async function applyYaml() {
  const raw = window.prompt("粘贴训练 YAML 到此处");
  if (!raw) return;
  try {
    const r = await http.post("/api/training/config/yaml/parse", { yaml: raw });
    Object.assign(form, r.data.params);
    await loadDatasetVersions({ forceSelectActive: true });
    await loadHubModels();
    const m = (form as { model?: string }).model;
    if (m && !hubModels.value.some((h) => h.model_id === m)) {
      message.warning("YAML 中的模型未在本机列表中，已改为列表中第一项。请先下载对应该模型或重新选择。");
      syncModelFromHub();
    }
    message.success("已应用 YAML 到表单");
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "解析失败");
  }
}

function downloadYaml() {
  const q = currentJobId.value ? `?job_id=${currentJobId.value}` : "";
  window.open(`/api/training/config/yaml/export${q}`, "_blank");
}

async function refreshJobs() {
  const r = await http.get("/api/training/jobs");
  jobs.value = r.data.items;
}

async function updateChart() {
  if (!currentJobId.value) return;
  const m = await http.get(`/api/training/jobs/${currentJobId.value}/metrics`);
  const pr = m.data.progress as { percent: number | null; label?: string } | undefined;
  if (pr) {
    progressPercent.value = typeof pr.percent === "number" ? pr.percent : null;
    progressLabel.value = typeof pr.label === "string" ? pr.label : "";
  }
  if (!chartRef.value) return;
  const s = m.data.series as {
    train_loss: { step: number; value: number }[];
    learning_rate: { value: number }[];
  };
  if (!chart) chart = echarts.init(chartRef.value);
  chart.setOption({
    title: { text: "训练指标（自日志解析）" },
    tooltip: { trigger: "axis" },
    xAxis: { type: "value" },
    yAxis: [{ type: "value", name: "loss" }, { type: "value", name: "lr" }],
    series: [
      { name: "train_loss", type: "line", data: s.train_loss?.map((x) => [x.step, x.value]) ?? [] },
      { name: "lr", type: "line", yAxisIndex: 1, data: s.learning_rate?.map((x, i) => [i, x.value]) ?? [] },
    ],
  });
}

async function refreshLogs() {
  if (!currentJobId.value) return;
  const st = await http.get<{
    status: string;
    error_message?: string | null;
  }>(`/api/training/jobs/${currentJobId.value}`);
  jobStatus.value = String(st.data.status);
  jobError.value = st.data.error_message != null && String(st.data.error_message).trim() ? String(st.data.error_message) : "";
  const logs = await http.get<{ text: string; truncated: boolean }>(
    `/api/training/jobs/${currentJobId.value}/logs`,
  );
  logText.value = logs.data.truncated ? `…（仅显示末尾）\n${logs.data.text}` : logs.data.text;
  if (stick.value) {
    requestAnimationFrame(() => {
      if (logPre.value) logPre.value.scrollTop = logPre.value.scrollHeight;
    });
  }
  try {
    await updateChart();
  } catch {
    /* ignore */
  }
  if (["succeeded", "failed", "cancelled"].includes(String(st.data.status))) {
    stopLogPoll();
  }
}

async function startTraining() {
  if (hubModels.value.length === 0) {
    message.warning("请先在「设置 → 模型管理」中成功下载至少一个模型");
    return;
  }
  if (!form.model || !hubModels.value.some((m) => m.model_id === form.model)) {
    message.warning("请从列表中选择已下载的模型");
    return;
  }
  if (!form.train_dataset.trim() || !form.val_dataset.trim()) {
    message.warning("训练/验证集路径来自当前激活的数据集版本。请先在「数据集」中构建并激活一版。");
    return;
  }
  submitting.value = true;
  try {
    const r = await http.post<{
      id: string;
      status: string;
      error_message?: string | null;
    }>("/api/training/jobs", { ...form });
    currentJobId.value = r.data.id;
    jobStatus.value = r.data.status;
    jobError.value =
      r.data.error_message != null && String(r.data.error_message).trim() ? String(r.data.error_message) : "";
    message.success(`任务已创建：${r.data.id}`);
    stopLogPoll();
    logPoll = setInterval(() => {
      void refreshLogs();
    }, 1500);
    await refreshLogs();
    await refreshJobs();
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? String(e));
  } finally {
    submitting.value = false;
  }
}

async function cancelJob() {
  if (!currentJobId.value) return;
  await http.post(`/api/training/jobs/${currentJobId.value}/cancel`);
  message.success("已请求取消");
  await refreshLogs();
  await refreshJobs();
}

async function delJob() {
  if (!currentJobId.value) return;
  await http.delete(`/api/training/jobs/${currentJobId.value}`);
  message.success("已删除");
  currentJobId.value = null;
  logText.value = "";
  progressPercent.value = null;
  progressLabel.value = "";
  jobError.value = "";
  await refreshJobs();
}

async function retryJob() {
  if (!currentJobId.value) return;
  const r = await http.post(`/api/training/jobs/${currentJobId.value}/retry`);
  currentJobId.value = r.data.id;
  logPoll = setInterval(() => void refreshLogs(), 1500);
  void refreshLogs();
  await refreshJobs();
}

function selectJob(id: string) {
  currentJobId.value = id;
  stopLogPoll();
  logPoll = setInterval(() => void refreshLogs(), 1500);
  void refreshLogs();
}

watch(logText, () => {
  if (stick.value) {
    requestAnimationFrame(() => {
      if (logPre.value) logPre.value.scrollTop = logPre.value.scrollHeight;
    });
  }
});
</script>

<template>
  <div>
    <a-typography-title :level="4">训练</a-typography-title>
    <a-alert
      type="info"
      show-icon
      message="默认已压到更省内存、尽快结束：1 epoch、LoRA r=1、max_length/视觉 token/视频 token 取可用下限、存盘与验证步频极大、日志很稀。再省内存可关 gradient_checkpointing（会更快但峰值内存升）。长图/长文任务请自行调大，否则易截断或效果差。"
      style="margin-bottom: 12px"
    />
    <a-form layout="horizontal">
      <a-row :gutter="[16, 16]">
        <a-col :span="10">
          <a-form-item label="数据集选择">
            <div style="display: flex; align-items: center; gap: 8px; width: 100%">
              <a-select
                v-model:value="selectedDatasetVersionId"
                :options="datasetSelectOptions"
                :loading="loadingDatasetPaths"
                :disabled="!datasetVersionItems.length"
                show-search
                :filter-option="filterDatasetOption"
                allow-clear
                placeholder="可搜索名称或版本 ID 片段"
                style="flex: 1; min-width: 0"
                :not-found-content="loadingDatasetPaths ? '加载中…' : '暂无版本，请先去「数据集」构建'"
                @change="onDatasetVersionSelect"
              />
              <a-tooltip title="刷新列表">
                <a-button
                  type="text"
                  size="small"
                  :loading="loadingDatasetPaths"
                  aria-label="刷新列表"
                  @click="loadDatasetVersions()"
                >
                  <template #icon>
                    <ReloadOutlined />
                  </template>
                </a-button>
              </a-tooltip>
            </div>
          </a-form-item>
        </a-col>
        <a-col :span="6">
          <a-form-item label="训练集 (jsonl)">
            <div style="min-width: 0">
              <a-typography-link
                v-if="form.train_dataset"
                style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap"
                @click.prevent="openDatasetPathModal('train')"
              >
                {{ form.train_dataset }}
              </a-typography-link>
              <a-typography-text v-else type="secondary">----</a-typography-text>
            </div>
          </a-form-item>
        </a-col>
        <a-col :span="6">
          <a-form-item label="验证集 (jsonl)">
            <div style="min-width: 0">
              <a-typography-link
                v-if="form.val_dataset"
                style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap"
                @click.prevent="openDatasetPathModal('val')"
              >
                {{ form.val_dataset }}
              </a-typography-link>
              <a-typography-text v-else type="secondary">----</a-typography-text>
            </div>
          </a-form-item>
        </a-col>
        <a-col :span="2">
          <a-button type="link" size="small" style="padding: 0" @click="goDatasets">去「数据集」管理</a-button>
        </a-col>
      </a-row>
    </a-form>
    <a-modal
      v-model:open="datasetPathModalOpen"
      :title="datasetPathModalTitle"
      width="min(896px, 90vw)"
      :footer="null"
      destroy-on-close
    >
      <a-spin :spinning="datasetPathModalLoading">
        <a-alert
          v-if="datasetPathModalError && !datasetPathModalLoading"
          type="error"
          :message="datasetPathModalError"
          show-icon
          style="margin-bottom: 0"
        />
        <pre
          v-else-if="!datasetPathModalLoading"
          style="
            margin: 0;
            white-space: pre-wrap;
            word-break: break-all;
            max-height: 60vh;
            overflow: auto;
            font-size: 13px;
            line-height: 1.5;
            font-family: ui-monospace, monospace;
          "
          >{{ datasetPathModalText || "（无内容）" }}</pre
        >
      </a-spin>
    </a-modal>
    <a-divider />
    <a-row :gutter="16">
      <a-col :span="8">
        <a-form layout="vertical">
          <a-form-item label="基础模型/路径（仅本机已下载）">
            <a-select
              v-model:value="form.model"
              :options="modelSelectOptions"
              :loading="loadingHub"
              :disabled="loadingHub"
              show-search
              option-filter-prop="label"
              placeholder="无可用模型时请先到设置中下载"
              style="width: 100%"
            />
            <div style="margin-top: 8px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center">
              <a-button type="link" size="small" style="padding: 0" @click="goModelSettings">去「设置 → 模型管理」下载</a-button>
              <a-button size="small" :loading="loadingHub" @click="loadHubModels">刷新模型列表</a-button>
            </div>
            <a-typography-text v-if="!loadingHub && hubModels.length === 0" type="secondary" style="display: block; margin-top: 6px"
              >当前没有检测到魔搭本机已缓存的模型。下载完成后点「刷新模型列表」。</a-typography-text
            >
          </a-form-item>

          <a-form-item label="输出目录">
            <a-input v-model:value="form.output_dir" />
          </a-form-item>
          <a-form-item label="LoRA rank">
            <a-input-number v-model:value="form.lora_rank" :min="1" :max="128" style="width: 100%" />
          </a-form-item>
          <a-form-item label="LoRA alpha">
            <a-input-number v-model:value="form.lora_alpha" :min="1" :max="256" style="width: 100%" />
          </a-form-item>
          <a-form-item label="目标模块">
            <a-input v-model:value="form.target_modules" />
          </a-form-item>
          <a-form-item label="Epochs">
            <a-input-number v-model:value="form.num_train_epochs" :min="1" :max="200" style="width: 100%" />
          </a-form-item>
          <a-form-item label="Batch size">
            <a-input-number
              v-model:value="form.per_device_train_batch_size"
              :min="1"
              :max="16"
              style="width: 100%"
            />
          </a-form-item>
          <a-form-item label="梯度累积">
            <a-input-number
              v-model:value="form.gradient_accumulation_steps"
              :min="1"
              :max="128"
              style="width: 100%"
            />
          </a-form-item>
          <a-form-item label="学习率">
            <a-input-number
              v-model:value="form.learning_rate"
              :min="1e-6"
              :max="1e-2"
              :step="0.00001"
              style="width: 100%"
            />
          </a-form-item>
          <a-form-item label="max_length（序列越长越吃内存）">
            <a-input-number v-model:value="form.max_length" :min="128" :max="8192" style="width: 100%" />
          </a-form-item>
          <a-form-item label="image_max_token_num（视觉 token 上限）">
            <a-input-number v-model:value="form.image_max_token_num" :min="64" :max="2048" style="width: 100%" />
          </a-form-item>
          <a-form-item label="video_max_token_num">
            <a-input-number v-model:value="form.video_max_token_num" :min="16" :max="512" style="width: 100%" />
          </a-form-item>
          <a-form-item label="gradient_checkpointing">
            <a-switch v-model:checked="form.gradient_checkpointing" />
          </a-form-item>
          <a-form-item label="save_steps / eval_steps">
            <a-space>
              <a-input-number v-model:value="form.save_steps" :min="10" :max="10000000" style="width: 120px" />
              <span>/</span>
              <a-input-number v-model:value="form.eval_steps" :min="10" :max="10000000" style="width: 120px" />
            </a-space>
          </a-form-item>
          <a-form-item label="save_total_limit">
            <a-input-number v-model:value="form.save_total_limit" :min="1" :max="10" style="width: 100%" />
          </a-form-item>
          <a-space wrap>
            <a-button
              type="primary"
              :loading="submitting"
              :disabled="loadingHub || !hubModels.length || !form.model"
              @click="startTraining"
              >提交训练</a-button
            >
            <a-button :disabled="!currentJobId" @click="cancelJob">取消</a-button>
            <a-button :disabled="!currentJobId" @click="delJob">删除记录</a-button>
            <a-button :disabled="!currentJobId" @click="retryJob">重试</a-button>
            <a-button @click="applyYaml">从 YAML 导入</a-button>
            <a-button @click="downloadYaml">导出 YAML</a-button>
          </a-space>
        </a-form>
      </a-col>
      <a-col :span="16">
        <a-typography-title :level="5">任务</a-typography-title>
        <a-table
          :columns="jobColumns"
          :data-source="(jobs as Record<string, unknown>[]) as any"
          :pagination="false"
          size="small"
          row-key="id"
        >
          <template #bodyCell="{ column, text, record }">
            <template v-if="column.key === 'act' && record && typeof record === 'object' && 'id' in record">
              <a @click="selectJob(String((record as { id: string }).id))">查看</a>
            </template>
            <span v-else-if="column.key === 'created_at' && record && typeof record === 'object'">{{
              formatJobTime((record as Record<string, unknown>).created_at)
            }}</span>
            <span v-else-if="column.key === 'finished_at' && record && typeof record === 'object'">{{
              formatJobEnd((record as Record<string, unknown>).finished_at, (record as { status?: string }).status)
            }}</span>
            <span v-else>{{ text }}</span>
          </template>
        </a-table>
        <a-typography-paragraph
          >当前：{{ currentJobId || "—" }} <a-tag v-if="jobStatus">{{ jobStatus }}</a-tag></a-typography-paragraph
        >
        <a-typography-title :level="5">资源（约 2s）</a-typography-title>
        <a-typography-paragraph style="word-break: break-all; font-size: 12px; color: #666">
          {{ resInfo || "—" }}
        </a-typography-paragraph>
        <a-typography-title :level="5">训练进度</a-typography-title>
        <div v-if="currentJobId" style="margin-bottom: 10px">
          <a-progress
            :percent="progressPercent === null ? 0 : progressPercent"
            :status="progressStatus"
            :show-info="progressPercent !== null"
          />
          <div style="font-size: 12px; color: #666; margin-top: 4px">{{ progressLabel || "—" }}</div>
          <a-typography-text
            v-if="jobError && (jobStatus === 'failed' || jobStatus === 'cancelled')"
            type="danger"
            style="display: block; margin-top: 8px; font-size: 12px"
            >原因（任务）: {{ jobError }}</a-typography-text
          >
        </div>
        <a-typography-title :level="5">日志</a-typography-title>
        <a-button v-if="!stick" type="dashed" size="small" style="margin-bottom: 8px" @click="scrollToBottom"
          >跟随最新 / 回到底部</a-button
        >
        <pre
          ref="logPre"
          style="
            font-family: ui-monospace, monospace;
            font-size: 12px;
            height: 200px;
            overflow: auto;
            background: #0d1117;
            color: #e6edf3;
            padding: 8px;
            border-radius: 4px;
            margin-top: 0;
            white-space: pre-wrap;
          "
          @scroll="onLogScroll"
          >{{ logText }}</pre
        >
        <a-typography-title :level="5" style="margin-top: 12px">曲线</a-typography-title>
        <div ref="chartRef" style="height: 280px" />
      </a-col>
    </a-row>
  </div>
</template>
