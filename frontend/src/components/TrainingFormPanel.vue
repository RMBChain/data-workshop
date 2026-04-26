<script setup lang="ts">
import { message } from "ant-design-vue";
import { InfoCircleOutlined, QuestionCircleOutlined, ReloadOutlined } from "@ant-design/icons-vue";
import * as echarts from "echarts";
import { computed, inject, nextTick, onMounted, onUnmounted, reactive, ref, toRaw, watch, type Ref } from "vue";
import { useRouter } from "vue-router";
import { apiErrorDetail, getApiErrorDetail, http } from "../api/http";

type HubDownloadRecord = {
  status?: "downloading" | "completed" | "failed" | "interrupted";
  completed_at?: string;
};

type HubModelRow = {
  model_id: string;
  path: string;
  size_bytes: number;
  download_record?: HubDownloadRecord;
};

const props = withDefaults(
  defineProps<{
    jobs: Record<string, unknown>[];
    /** 自训练列表「查看」带入，写入表单「训练名称」；新建训练为清空 */
    jobNamePrefill?: string;
    /** 父级「新建训练」每次打开递增，用于在无预填时重新生成默认名称 */
    newTrainOpenSeq?: number;
  }>(),
  { jobNamePrefill: "", newTrainOpenSeq: 0 },
);

/** 全屏训练参数 Modal；为 false 时不拉取/合并已保存参数，避免未展示时写坏表单 */
const panelOpen = defineModel<boolean>("open", { default: false });
const currentJobId = defineModel<string | null>("currentJobId", { default: null });

/** 与 BasicLayout 中「资源信息」共用同一悬浮面板 */
const resourceInfoFloatingOpen = inject<Ref<boolean> | undefined>("workshop:resourceInfoFloatingOpen", undefined);
function openResourceInfoModal() {
  if (resourceInfoFloatingOpen) resourceInfoFloatingOpen.value = true;
}
const emit = defineEmits<{
  (e: "refresh-jobs"): void;
}>();

const router = useRouter();
const hubModels = ref<HubModelRow[]>([]);
const loadingHub = ref(false);

const form = reactive({
  model: "" as string,
  job_name: "",
  train_dataset: "",
  val_dataset: "",
  output_dir: "output/",
  lora_rank: 4,
  lora_alpha: 8,
  lora_dropout: 0.05,
  target_modules: "all-linear",
  model_type: "",
  template: "",
  torch_dtype: "float32",
  bf16: false,
  attn_impl: "eager",
  max_length: 1024,
  system: "",
  num_train_epochs: 3,
  per_device_train_batch_size: 1,
  per_device_eval_batch_size: 1,
  gradient_accumulation_steps: 4,
  learning_rate: 0.0001,
  lr_scheduler_type: "cosine",
  warmup_ratio: 0.1,
  logging_steps: 10,
  save_steps: 500,
  eval_steps: 100,
  save_total_limit: 3,
  quant_method: "bnb",
  quant_bits: 4,
  bnb_4bit_compute_dtype: "bfloat16",
  bnb_4bit_quant_type: "nf4",
  bnb_4bit_use_double_quant: true,
  use_dora: false,
  lorap_lr_ratio: null as number | null,
  gradient_checkpointing: true,
  deepspeed: "",
  resume_from_checkpoint: "",
  merge_lora: false,
  adapters: "",
  train_type: "lora",
  tuner_backend: "",
  freeze_vit: true,
  packing: false,
  image_max_token_num: 64,
  video_max_token_num: 16,
  dataloader_num_workers: 4,
});

const activeDatasetHint = ref("");
const datasetPathModalOpen = ref(false);
const datasetPathModalTitle = ref("");
const datasetPathModalText = ref("");
const datasetPathModalLoading = ref(false);
const datasetPathModalError = ref("");
const loadingDatasetPaths = ref(false);
const submitting = ref(false);
const savingFormParams = ref(false);
const logText = ref("");
const logPre = ref<HTMLPreElement | null>(null);
const jobStatus = ref("");
const stick = ref(true);
const advancedTrainParamsActiveKeys = ref<string[]>([]);

const torchDtypeOptions = [
  { value: "float32", label: "float32" },
  { value: "bfloat16", label: "bfloat16" },
  { value: "float16", label: "float16" },
];

const attnImplOptions = [
  { value: "eager", label: "eager（CPU 安全）" },
  { value: "sdpa", label: "sdpa" },
  { value: "flash_attn", label: "flash_attn" },
];

const lrSchedulerOptions = [
  { value: "cosine", label: "cosine" },
  { value: "linear", label: "linear" },
  { value: "constant", label: "constant" },
];

const trainTypeOptions = [
  { value: "lora", label: "lora" },
  { value: "full", label: "full（全参）" },
];

const tunerBackendOptions = [
  { value: "", label: "默认（peft）" },
  { value: "peft", label: "peft" },
  { value: "unsloth", label: "unsloth" },
];

const bnbQuantTypeOptions = [
  { value: "nf4", label: "nf4（推荐）" },
  { value: "fp4", label: "fp4" },
];

const bnbComputeDtypeOptions = [
  { value: "bfloat16", label: "bfloat16" },
  { value: "float16", label: "float16" },
  { value: "float32", label: "float32" },
];

let logPoll: ReturnType<typeof setInterval> | null = null;
const workspaceRootAbs = ref("");
const chartRef = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;
const progressPercent = ref<number | null>(null);
const progressLabel = ref("");
type ProgressStage = { id: string; name: string; percent: number | null; label: string };
const progressStages = ref<ProgressStage[]>([]);
const jobError = ref("");

function defaultTrainJobName(datasetLabel: string) {
  const base = (datasetLabel || "").trim() || "训练";
  return `${base}#${Date.now()}`;
}

function syncOutputDirWithTaskOrDataset(versionId?: string | null) {
  if (currentJobId.value) {
    const row = props.jobs.find((j) => (j as { id?: string }).id === currentJobId.value) as
      | { request?: { output_dir?: unknown; dataset_version_id?: unknown } }
      | undefined;
    const req = row?.request;
    const od0 = req?.output_dir;
    if (typeof od0 === "string" && od0.trim()) {
      form.output_dir = od0.trim();
      return;
    }
    const dvid0 = req?.dataset_version_id;
    if (typeof dvid0 === "string" && dvid0.trim()) {
      form.output_dir = `output/${dvid0.trim()}`;
      return;
    }
  }
  const vid = versionId !== undefined ? versionId : selectedDatasetVersionId.value;
  if (vid != null && vid !== "") {
    form.output_dir = `output/${vid}`;
    return;
  }
  form.output_dir = "output/";
}

const currentJobNameDisplay = computed(() => {
  const id = currentJobId.value;
  if (!id) return "";
  const row = props.jobs.find((j) => (j as { id?: string }).id === id) as { job_name?: string } | undefined;
  const n = row?.job_name;
  if (typeof n === "string" && n.length > 0 && n !== "—") return n;
  return "";
});

function isHubDownloadSuccess(rec: HubDownloadRecord | undefined): boolean {
  if (!rec) return false;
  if (rec.status === "failed" || rec.status === "downloading" || rec.status === "interrupted") {
    return false;
  }
  if (rec.status === "completed") return true;
  return Boolean(rec.completed_at);
}

const readyHubModels = computed(() => hubModels.value.filter((m) => isHubDownloadSuccess(m.download_record)));

const modelSelectOptions = computed(() =>
  readyHubModels.value.map((m) => ({
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

function workspaceDatasetAbsDisplay(relRaw: string): string {
  const rel = (relRaw ?? "").trim();
  if (!rel) return "";
  const root = workspaceRootAbs.value.trim().replace(/[\\/]+$/, "");
  if (!root) return rel.replace(/\\/g, "/");
  if (/^[a-zA-Z]:[\\/]/.test(rel)) return rel;
  if (rel.startsWith("\\\\")) return rel;
  const rootIsWin = /^[a-zA-Z]:/.test(root);
  if (rel.startsWith("/") && !rootIsWin) return rel;
  const sep = rootIsWin ? "\\" : "/";
  const relNorm = rel
    .replace(/^[\\/]+/, "")
    .split(/[/\\]+/)
    .filter(Boolean)
    .join(sep);
  return `${root}${sep}${relNorm}`;
}

async function loadWorkspacePaths() {
  try {
    const r = await http.get<{ workspace_root?: string }>("/api/system/paths");
    workspaceRootAbs.value = (r.data.workspace_root ?? "").trim();
  } catch {
    workspaceRootAbs.value = "";
  }
}

function syncModelFromHub() {
  const list = readyHubModels.value;
  const ids = new Set(list.map((m) => m.model_id));
  if (form.model && ids.has(form.model)) return;
  form.model = list[0]?.model_id ?? "";
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
  import_batch_id?: string | null;
  project_title?: string | null;
  batch_name?: string | null;
  train_relpath?: string | null;
  val_relpath?: string | null;
  is_active?: boolean;
  train_count?: number;
  val_count?: number;
};

const datasetVersionItems = ref<DatasetVersionRow[]>([]);
const serverActiveVersionId = ref<string | null>(null);
const selectedDatasetVersionId = ref<string | null | undefined>(null);
/** 与 Datasets 页一致：用稳定键区分项目展示名 */
function projectKeyOf(v: { project_title?: string | null }): string {
  return `ptitle:${(v.project_title ?? "").trim() || "—"}`;
}
/** 有 import_batch 用批次 id；无则每版本独键，避免多 orphan 混在一个选项里 */
function batchKeyOf(v: DatasetVersionRow): string {
  const bid = (v.import_batch_id ?? "").trim();
  if (bid) return `bid:${bid}`;
  return `orphan:${v.id}`;
}
const selectedProjectKey = ref<string | null>(null);
const selectedBatchKey = ref<string | null>(null);

const projectSelectOptions = computed(() => {
  const m = new Map<string, string>();
  for (const v of datasetVersionItems.value) {
    const k = projectKeyOf(v);
    if (!m.has(k)) m.set(k, (v.project_title ?? "").trim() || "—");
  }
  return [...m.entries()]
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));
});

const batchSelectOptions = computed(() => {
  if (!selectedProjectKey.value) return [];
  const m = new Map<string, { label: string }>();
  for (const v of datasetVersionItems.value) {
    if (projectKeyOf(v) !== selectedProjectKey.value) continue;
    const bk = batchKeyOf(v);
    if (!m.has(bk)) {
      const rawBn = (v.batch_name ?? "").trim();
      const hasBid = Boolean((v.import_batch_id ?? "").trim());
      const label = rawBn || (hasBid ? "—" : `未关联批次 · ${v.id.slice(0, 8)}${v.id.length > 8 ? "…" : ""}`);
      m.set(bk, { label });
    }
  }
  return [...m.entries()]
    .map(([value, o]) => ({ value, label: o.label }))
    .sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));
});

const versionSelectOptions = computed(() => {
  if (!selectedProjectKey.value || !selectedBatchKey.value) return [];
  return datasetVersionItems.value
    .filter((v) => projectKeyOf(v) === selectedProjectKey.value && batchKeyOf(v) === selectedBatchKey.value)
    .map((v) => ({ value: v.id, label: versionRowLabel(v) }));
});

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

function syncCascadeFromVersionRow(row: DatasetVersionRow | null) {
  if (!row) {
    selectedProjectKey.value = null;
    selectedBatchKey.value = null;
  } else {
    selectedProjectKey.value = projectKeyOf(row);
    selectedBatchKey.value = batchKeyOf(row);
  }
}

function onDatasetVersionSelect(versionId: string | null | undefined) {
  const vid = versionId == null || versionId === "" ? null : versionId;
  selectedDatasetVersionId.value = vid;
  if (vid == null) {
    applyVersionToForm(null);
    syncCascadeFromVersionRow(null);
  } else {
    const row = datasetVersionItems.value.find((x) => x.id === vid) ?? null;
    syncCascadeFromVersionRow(row);
    applyVersionToForm(row);
  }
  syncOutputDirWithTaskOrDataset(vid);
}

function onProjectKeyChange(v: string | null | undefined) {
  const pk = v ?? null;
  selectedProjectKey.value = pk;
  if (pk == null) {
    selectedBatchKey.value = null;
    onDatasetVersionSelect(null);
    return;
  }
  const inProj = datasetVersionItems.value.filter((x) => projectKeyOf(x) === pk);
  if (!inProj.length) {
    selectedBatchKey.value = null;
    onDatasetVersionSelect(null);
    return;
  }
  const batchKeys = [...new Set(inProj.map(batchKeyOf))].sort();
  const bk0 = batchKeys[0]!;
  selectedBatchKey.value = bk0;
  const v0 = inProj.find((x) => batchKeyOf(x) === bk0) ?? inProj[0]!;
  onDatasetVersionSelect(v0.id);
}

function onBatchKeyChange(bk: string | null | undefined) {
  const key = bk ?? null;
  selectedBatchKey.value = key;
  if (key == null) {
    onDatasetVersionSelect(null);
    return;
  }
  const pk = selectedProjectKey.value;
  if (!pk) return;
  const list = datasetVersionItems.value.filter(
    (x) => projectKeyOf(x) === pk && batchKeyOf(x) === key,
  );
  if (!list.length) {
    onDatasetVersionSelect(null);
    return;
  }
  onDatasetVersionSelect(list[0]!.id);
}

async function loadDatasetVersions(opts?: {
  forceSelectActive?: boolean;
  /** 若存在且仍在本机版本列表中，优先生效（与合并已保存训练参数时一致，避免先被默认/激活版覆盖） */
  preferVersionId?: string | null;
}) {
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

    const pref = (opts?.preferVersionId ?? "").trim();
    let pick: string | null = null;
    if (pref && ids.has(pref)) {
      pick = pref;
    } else if (opts?.forceSelectActive) {
      if (act && ids.has(act)) pick = act;
      else if (items[0]) pick = items[0].id;
    } else {
      const cur = selectedDatasetVersionId.value;
      if (cur && ids.has(cur)) pick = cur;
      else if (act && ids.has(act)) pick = act;
      else if (items[0]) pick = items[0].id;
    }

    onDatasetVersionSelect(pick);
  } catch {
    datasetVersionItems.value = [];
    serverActiveVersionId.value = null;
    onDatasetVersionSelect(null);
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
    const detail = getApiErrorDetail(e);
    datasetPathModalError.value =
      typeof detail === "string" && detail
        ? detail
        : "无法读取文件内容，请确认路径可访问或稍后重试";
  } finally {
    datasetPathModalLoading.value = false;
  }
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

function stageRowStatus(percent: number | null) {
  if (jobStatus.value === "succeeded") return "success" as const;
  if (jobStatus.value === "failed" || jobStatus.value === "cancelled") return "exception" as const;
  if (["running", "pending"].includes(jobStatus.value) && percent == null) return "active" as const;
  return "normal" as const;
}

const overallJobStatusLabel = computed(() => {
  if (!currentJobId.value) return "";
  const s = jobStatus.value;
  if (s === "succeeded") return "成功";
  if (s === "failed") return "失败";
  if (s === "cancelled") return "已取消";
  if (s === "running" || s === "pending") return "进行中";
  return s ? String(s) : "";
});

const overallJobStatusTagColor = computed(() => {
  const s = jobStatus.value;
  if (s === "succeeded") return "success" as const;
  if (s === "failed") return "error" as const;
  if (s === "cancelled") return "default" as const;
  if (s === "running" || s === "pending") return "processing" as const;
  return "default" as const;
});

function onChartsResize() {
  requestAnimationFrame(() => {
    chart?.resize();
  });
}

onMounted(() => {
  void loadWorkspacePaths();
  window.addEventListener("resize", onChartsResize);
});

onUnmounted(() => {
  window.removeEventListener("resize", onChartsResize);
  stopLogPoll();
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
    if (m && !readyHubModels.value.some((h) => h.model_id === m)) {
      message.warning(
        "YAML 中的模型未在本机「下载完成」列表中，已改为列表中第一项。请先在模型管理中成功下载该模型或重新选择。",
      );
      syncModelFromHub();
    }
    message.success("已应用 YAML 到表单");
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "解析失败");
  }
}

function downloadYaml() {
  const q = currentJobId.value ? `?job_id=${currentJobId.value}` : "";
  window.open(`/api/training/config/yaml/export${q}`, "_blank");
}

async function updateChart() {
  if (!currentJobId.value) return;
  const m = await http.get(`/api/training/jobs/${currentJobId.value}/metrics`);
  const pr = m.data.progress as
    | { percent: number | null; label?: string; stages?: ProgressStage[] }
    | undefined;
  if (pr) {
    progressPercent.value = typeof pr.percent === "number" ? pr.percent : null;
    progressLabel.value = typeof pr.label === "string" ? pr.label : "";
    if (Array.isArray(pr.stages) && pr.stages.length) {
      progressStages.value = pr.stages.map((s) => ({
        id: String(s.id ?? ""),
        name: String(s.name ?? ""),
        percent: typeof s.percent === "number" ? s.percent : null,
        label: typeof s.label === "string" ? s.label : "",
      }));
    } else {
      progressStages.value = [];
    }
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

/** 与「提交训练」POST /api/training/jobs 请求体一致（含 output_dir 占位、job_name 解析、扩展字段） */
function buildTrainJobRequestBody(): Record<string, unknown> {
  const ver =
    selectedDatasetVersionId.value != null && selectedDatasetVersionId.value !== ""
      ? datasetVersionItems.value.find((x) => x.id === selectedDatasetVersionId.value) ?? null
      : null;
  const dataLabel = (ver?.name ?? "").trim() || (ver ? String(ver.id) : "");
  const dvid =
    selectedDatasetVersionId.value != null && selectedDatasetVersionId.value !== ""
      ? String(selectedDatasetVersionId.value)
      : "";
  const resolvedJobName = (form.job_name || "").trim() || defaultTrainJobName(dataLabel);
  const plain = JSON.parse(JSON.stringify(toRaw(form))) as Record<string, unknown>;
  return {
    ...plain,
    output_dir: "output/",
    dataset_version_id: dvid,
    job_name: resolvedJobName,
    project_title: (ver?.project_title ?? "").trim(),
    batch_name: (ver?.batch_name ?? "").trim(),
    dataset_name: dataLabel,
  };
}

async function saveTrainingFormParams() {
  savingFormParams.value = true;
  try {
    const jid = (currentJobId.value ?? "").trim();
    if (jid) {
      await http.post("/api/training/form-params", buildTrainJobRequestBody(), {
        params: { job_id: jid },
      });
      message.success("已更新该任务的训练参数记录");
    } else {
      const bodyPayload = buildTrainJobRequestBody();
      const r = await http.post<{
        id: string;
        status: string;
        output_dir?: string | null;
        error_message?: string | null;
      }>("/api/training/jobs/params-only", bodyPayload);
      const ro = (r.data.output_dir ?? "").trim();
      const dvid =
        typeof bodyPayload.dataset_version_id === "string" ? bodyPayload.dataset_version_id.trim() : "";
      form.output_dir = ro || (dvid ? `output/${dvid}` : "output/");
      jobStatus.value = r.data.status;
      jobError.value =
        r.data.error_message != null && String(r.data.error_message).trim()
          ? String(r.data.error_message)
          : "";
      currentJobId.value = r.data.id;
      message.success("已新建任务并保存训练参数，可在列表中查看；点击「开始训练」将启动该任务。");
    }
    emit("refresh-jobs");
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? String(e));
  } finally {
    savingFormParams.value = false;
  }
}

function applySavedFormParamsToForm(d: Record<string, unknown>) {
  for (const key of Object.keys(form) as (keyof typeof form)[]) {
    if (!Object.prototype.hasOwnProperty.call(d, key)) continue;
    const v = d[key as string];
    if (v === undefined) continue;
    if (key === "lorap_lr_ratio") {
      form.lorap_lr_ratio = v === null || v === "" ? null : Number(v);
      continue;
    }
    if (key === "resume_from_checkpoint") {
      form.resume_from_checkpoint = v == null ? "" : String(v);
      continue;
    }
    if (key === "quant_bits") {
      form.quant_bits =
        typeof v === "number" && !Number.isNaN(v) ? v : v === null || v === "" ? 4 : Number(v);
      continue;
    }
    const cur = form[key];
    if (typeof cur === "number") {
      const n = typeof v === "number" ? v : Number(v);
      if (!Number.isNaN(n)) (form as unknown as Record<string, unknown>)[key] = n;
      continue;
    }
    if (typeof cur === "boolean") {
      (form as unknown as Record<string, unknown>)[key] = Boolean(v);
      continue;
    }
    (form as unknown as Record<string, unknown>)[key] = v as unknown;
  }
  if (!currentJobId.value) {
    syncOutputDirWithTaskOrDataset();
  }
}

/** 当点击表格「训练」加载已有任务时，初始化数据集级联选择器（使项目/批次/数据集名称可编辑） */
async function initCascadeFromExistingJob(jobId: string) {
  const row = props.jobs.find((j) => (j as { id?: string }).id === jobId) as
    | {
        request?: { dataset_version_id?: unknown; dataset_name?: unknown };
        dataset_version_id?: unknown;
        dataset_name?: unknown;
      }
    | undefined;
  if (!row) return;

  const req = row.request || {};
  let vid = req.dataset_version_id || row.dataset_version_id;
  if (typeof vid === "string" && vid.trim()) {
    vid = vid.trim();
    if (datasetVersionItems.value.some((x) => x.id === vid)) {
      onDatasetVersionSelect(vid as string);
      return;
    }
  }

  // Fallback: try to match by dataset_name/project_title/batch_name
  const dsName =
    (typeof req.dataset_name === "string" ? req.dataset_name : String(req.dataset_name ?? "")).trim() ||
    (row.dataset_name != null ? String(row.dataset_name) : "").trim();
  if (dsName) {
    const match = datasetVersionItems.value.find(
      (v) => (v.name || v.id) === dsName || v.train_relpath?.includes(dsName),
    );
    if (match) {
      onDatasetVersionSelect(match.id);
    }
  }
}

watch(
  () => [panelOpen.value, currentJobId.value] as const,
  async ([visible, jid]) => {
    if (!visible) return;
    try {
      if (jid) {
        await loadDatasetVersions();
        await loadHubModels();
        await initCascadeFromExistingJob(jid);
        const row = props.jobs.find((j) => (j as { id?: string }).id === jid) as
          | { request?: Record<string, unknown> }
          | undefined;
        const req = row?.request;
        if (req && typeof req === "object" && !Array.isArray(req)) {
          applySavedFormParamsToForm(req);
          await nextTick();
        }
        syncModelFromHub();
      } else {
        await loadDatasetVersions();
        await loadHubModels();
        syncModelFromHub();
      }
    } catch (e) {
      console.warn("Failed to initialize training form:", e);
    }
  },
  { immediate: true },
);

async function startTraining() {
  if (readyHubModels.value.length === 0) {
    message.warning("请先在「设置 → 模型管理」中成功下载至少一个模型");
    return;
  }
  if (!form.model || !readyHubModels.value.some((m) => m.model_id === form.model)) {
    message.warning("请从列表中选择已下载完成的模型");
    return;
  }
  if (!form.train_dataset.trim() || !form.val_dataset.trim()) {
    message.warning("训练/验证集路径来自当前激活的数据集版本。请先在「数据集」中构建并激活一版。");
    return;
  }
  const cid = (currentJobId.value ?? "").trim();
  const listRow = cid
    ? (props.jobs.find((j) => (j as { id?: string }).id === cid) as { status?: string } | undefined)
    : undefined;
  const useStartFromSaved =
    Boolean(cid) &&
    (listRow?.status === "parameters_saved" ||
      (!listRow && jobStatus.value === "parameters_saved"));

  submitting.value = true;
  try {
    if (useStartFromSaved) {
      const r = await http.post<{
        id: string;
        status: string;
        error_message?: string | null;
        output_dir?: string | null;
      }>(`/api/training/jobs/${encodeURIComponent(cid)}/start`);
      const ro = (r.data.output_dir ?? "").trim();
      const dvid = String(
        (buildTrainJobRequestBody() as { dataset_version_id?: string }).dataset_version_id ?? "",
      ).trim();
      form.output_dir = ro || (dvid ? `output/${dvid}` : "output/");
      jobStatus.value = r.data.status;
      jobError.value =
        r.data.error_message != null && String(r.data.error_message).trim()
          ? String(r.data.error_message)
          : "";
      const jn = (form.job_name || "").trim();
      message.success(ro ? `已开始训练：${jn || "任务"}（${ro}）` : `已开始训练：${jn || "任务"}`);
      void refreshLogs();
    } else {
      const requestBody = buildTrainJobRequestBody();
      const resolvedJobName = String((requestBody.job_name as string) ?? form.job_name ?? "");
      const dvid =
        typeof requestBody.dataset_version_id === "string" ? requestBody.dataset_version_id.trim() : "";
      const r = await http.post<{
        id: string;
        status: string;
        error_message?: string | null;
        output_dir?: string | null;
      }>("/api/training/jobs", requestBody);
      const ro = (r.data.output_dir ?? "").trim();
      form.output_dir = ro || (dvid ? `output/${dvid}` : "output/");
      jobStatus.value = r.data.status;
      jobError.value =
        r.data.error_message != null && String(r.data.error_message).trim() ? String(r.data.error_message) : "";
      form.job_name = defaultNewTrainJobName();
      message.success(ro ? `任务已创建：${resolvedJobName}（${ro}）` : `任务已创建：${resolvedJobName}`);
      currentJobId.value = r.data.id;
    }
    emit("refresh-jobs");
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? String(e));
  } finally {
    submitting.value = false;
  }
}

async function cancelJob() {
  if (!currentJobId.value) return;
  await http.post(`/api/training/jobs/${currentJobId.value}/cancel`);
  message.success("已请求取消");
  await refreshLogs();
  emit("refresh-jobs");
}

async function continueTraining() {
  if (!currentJobId.value) return;
  try {
    const r = await http.post<{ id: string }>(`/api/training/jobs/${currentJobId.value}/retry`);
    currentJobId.value = r.data.id;
    message.success("已从断点继续训练（新任务）");
    emit("refresh-jobs");
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? String(e));
  }
}

watch(
  currentJobId,
  (id) => {
    if (id == null || id === "") {
      stopLogPoll();
      logText.value = "";
      progressPercent.value = null;
      progressLabel.value = "";
      progressStages.value = [];
      jobError.value = "";
      jobStatus.value = "";
      if (chart) {
        chart.clear();
      }
      syncOutputDirWithTaskOrDataset();
      return;
    }
    const row = props.jobs.find((j) => (j as { id?: string }).id === id) as
      | { output_dir?: unknown; request?: { output_dir?: unknown; dataset_version_id?: unknown } }
      | undefined;
    if (row) {
      const req = row?.request;
      const topOd = row?.output_dir;
      const raw = typeof topOd === "string" && topOd.trim() && topOd !== "—" ? topOd : req?.output_dir;
      const od = typeof raw === "string" ? raw.trim() : "";
      const dvid = typeof req?.dataset_version_id === "string" ? req.dataset_version_id.trim() : "";
      form.output_dir = od || (dvid ? `output/${dvid}` : "output/");
    }
    stopLogPoll();
    logPoll = setInterval(() => {
      void refreshLogs();
    }, 1500);
    void refreshLogs();
  },
);

watch(logText, () => {
  if (stick.value) {
    requestAnimationFrame(() => {
      if (logPre.value) logPre.value.scrollTop = logPre.value.scrollHeight;
    });
  }
});

/** 新增默认：YYYYMMDD-HHmmss + 5 位 UUID 十六进制片段 */
function defaultNewTrainJobName(): string {
  const d = new Date();
  const p2 = (n: number) => String(n).padStart(2, "0");
  const ts = `${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-${p2(d.getHours())}${p2(d.getMinutes())}${p2(d.getSeconds())}`;
  const u5 = crypto.randomUUID().replace(/-/g, "").slice(0, 5);
  return `${ts}-${u5}`;
}

watch(
  () => [props.jobNamePrefill, props.newTrainOpenSeq] as const,
  () => {
    const pre = (props.jobNamePrefill ?? "").trim();
    if (pre) {
      form.job_name = pre;
    } else {
      form.job_name = defaultNewTrainJobName();
    }
  },
  { immediate: true },
);
</script>

<template>
  <a-modal
    v-model:open="panelOpen"
    :footer="null"
    width="100%"
    :centered="false"
    :destroy-on-close="false"
    :mask-closable="true"
    wrap-class-name="training-fullscreen-modal-wrap"
    :style="{ top: 0, paddingBottom: 0, maxWidth: '100vw' }"
  >
    <template #title>
      <div class="training-modal-title-bar">
        <span class="training-modal-title-text">训练</span>
        <a-space wrap :size="8" class="training-modal-title-actions">
          <a-button
            type="primary"
            size="small"
            :loading="savingFormParams"
            :disabled="savingFormParams"
            @click="saveTrainingFormParams"
            >仅保存</a-button
          >
          <a-button
            type="primary"
            size="small"
            :loading="submitting"
            :disabled="loadingHub || !readyHubModels.length || !form.model"
            @click="startTraining"
            >开始训练</a-button
          >
          <a-button size="small" :disabled="!currentJobId" @click="cancelJob">停止</a-button>
          <a-button size="small" :disabled="!currentJobId" @click="continueTraining">继续训练</a-button>
          <a-button size="small" @click="applyYaml">从 YAML 导入配置</a-button>
          <a-button size="small" @click="downloadYaml">导出 YAML 配置</a-button>
          <a-button size="small" @click="openResourceInfoModal">资源信息</a-button>
        </a-space>
      </div>
    </template>
    <div class="training-form-panel">
    <a-alert type="info" show-icon style="margin-bottom: 12px">
        <template #message>
          默认按常用 LoRA 配置：rank=8、alpha=32、dropout=0.05、max_length=2048、3 epoch、梯度累积 4、cosine+10% warmup、日志/评估/保存步数 10/100/500。<br />
          显存紧张时请降低 max_length、batch、视觉 token，或开启量化（QLoRA）。lora_alpha/lora_rank 比值建议在 2～8。
        </template>
      </a-alert>
    <a-form layout="horizontal">
      <a-row :gutter="16">
        <a-col :span="24">
          <a-form-item label="训练名称">
            <a-input
              v-model:value="form.job_name"
              placeholder="默认：YYYYMMDD-HHmmss + 5位短码；可清空后按数据集规则生成"
              allow-clear
              style="width: 100%"
              autocomplete="off"
            />
          </a-form-item>
        </a-col>
        <a-col :span="8">
          <a-form-item>
            <template #label>
              <span style="display: inline-flex; align-items: center; gap: 4px">
                基于模型
                <a-tooltip title="仅列出在模型管理中标记为下载完成的模型">
                  <InfoCircleOutlined
                    style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                    aria-label="仅下载完成的模型"
                    role="img"
                  />
                </a-tooltip>
              </span>
            </template>
            <div style="display: flex; align-items: center; gap: 8px">
              <a-select
                v-model:value="form.model"
                :options="modelSelectOptions"
                :loading="loadingHub"
                :disabled="loadingHub"
                show-search
                option-filter-prop="label"
                placeholder="无下载完成模型时请先到设置中下载"
                style="flex: 1; min-width: 0"
              />
              <a-tooltip title="刷新模型列表">
                <a-button
                  type="text"
                  size="small"
                  :loading="loadingHub"
                  aria-label="刷新模型列表"
                  @click="loadHubModels"
                >
                  <template #icon>
                    <ReloadOutlined />
                  </template>
                </a-button>
              </a-tooltip>
            </div>
            <a-typography-text
              v-if="!loadingHub && readyHubModels.length === 0 && hubModels.length === 0"
              type="secondary"
              style="display: block; margin-top: 6px"
              >当前没有检测到魔搭本机模型目录。下载完成后点右侧刷新。</a-typography-text
            >
            <a-typography-text
              v-else-if="!loadingHub && readyHubModels.length === 0 && hubModels.length > 0"
              type="secondary"
              style="display: block; margin-top: 6px"
              >本机有模型目录，但尚无下载完成记录（可能仍在下载或上次失败/中断）。请到「设置 → 模型管理」确认状态，完成后点右侧刷新。</a-typography-text
            >
          </a-form-item>
        </a-col>
        <a-col :span="4">
          <a-form-item label="项目名称">
            <a-select
              v-model:value="selectedProjectKey"
              :options="projectSelectOptions"
              :loading="loadingDatasetPaths"
              :disabled="loadingDatasetPaths || !datasetVersionItems.length"
              show-search
              :filter-option="filterDatasetOption"
              allow-clear
              placeholder="选择项目"
              :not-found-content="loadingDatasetPaths ? '加载中…' : '暂无项目，请去「数据集」构建'"
              @update:value="onProjectKeyChange"
            />
          </a-form-item>
        </a-col>
        <a-col :span="4">
          <a-form-item label="批次名称">
            <a-select
              v-model:value="selectedBatchKey"
              :options="batchSelectOptions"
              :loading="loadingDatasetPaths"
              :disabled="loadingDatasetPaths || !selectedProjectKey"
              show-search
              :filter-option="filterDatasetOption"
              allow-clear
              placeholder="选择批次"
              :not-found-content="loadingDatasetPaths ? '加载中…' : '当前项目下暂无批次'"
              @update:value="onBatchKeyChange"
            />
          </a-form-item>
        </a-col>
        <a-col :span="8">
          <a-form-item label="数据集名称">
            <div style="display: flex; align-items: center; gap: 8px; width: 100%">
              <a-select
                v-model:value="selectedDatasetVersionId"
                :options="versionSelectOptions"
                :loading="loadingDatasetPaths"
                :disabled="loadingDatasetPaths || !selectedBatchKey"
                show-search
                :filter-option="filterDatasetOption"
                allow-clear
                placeholder="选择数据版本"
                style="flex: 1; min-width: 0"
                :not-found-content="loadingDatasetPaths ? '加载中…' : '当前批次下暂无数据版本，请去「数据集」构建'"
                @update:value="onDatasetVersionSelect"
              />
            </div>
          </a-form-item>
        </a-col>

        <a-col :span="6">
          <a-form-item label="输出目录">
            <a-typography-text type="secondary" style="font-size: 13px; word-break: break-all">
              {{
                form.output_dir && workspaceRootAbs
                  ? workspaceDatasetAbsDisplay(form.output_dir)
                  : "—"
              }}
            </a-typography-text>
          </a-form-item>
        </a-col>
        
        <a-col :span="6">
          <a-form-item label="训练集">
            <div style="min-width: 0">
              <a-tooltip v-if="form.train_dataset" :title="workspaceDatasetAbsDisplay(form.train_dataset)">
                <a-typography-link
                  style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap"
                  @click.prevent="openDatasetPathModal('train')"
                >
                  {{ form.train_dataset }}
                </a-typography-link>
              </a-tooltip>
              <a-typography-text v-else type="secondary">----</a-typography-text>
            </div>
          </a-form-item>
        </a-col>
        <a-col :span="6">
          <a-form-item label="验证集">
            <div style="min-width: 0">
              <a-tooltip v-if="form.val_dataset" :title="workspaceDatasetAbsDisplay(form.val_dataset)">
                <a-typography-link
                  style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap"
                  @click.prevent="openDatasetPathModal('val')"
                >
                  {{ form.val_dataset }}
                </a-typography-link>
              </a-tooltip>
              <a-typography-text v-else type="secondary">----</a-typography-text>
            </div>
          </a-form-item>
        </a-col>
      </a-row>

      <a-collapse
        v-model:activeKey="advancedTrainParamsActiveKeys"
        :bordered="false"
        style="margin-bottom: 0; background: transparent"
      >
        <a-collapse-panel key="lora_quad" header="最常用 LoRA（四连）">
          <a-typography-text type="secondary" style="display: block; margin-bottom: 12px">
            核心关系：<code>lora_alpha / lora_rank</code> 影响实际缩放，建议比值约 2～8（如 rank=8、alpha=32 → 比值为 4）。
          </a-typography-text>
          <a-row :gutter="16">
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    lora_rank
                    <a-tooltip title="低秩矩阵的秩，控制额外参数量。通用 8；任务复杂可 16～32。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="lora_rank 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.lora_rank" :min="1" :max="128" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    lora_alpha
                    <a-tooltip title="缩放系数，控制 LoRA 影响强度。rank=8 时常用 32。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="lora_alpha 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.lora_alpha" :min="1" :max="256" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    lora_dropout
                    <a-tooltip title="正则化丢弃概率，减轻过拟合。常用 0.05。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="lora_dropout 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.lora_dropout" :min="0" :max="0.9" :step="0.01" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    target_modules
                    <a-tooltip title="施加 LoRA 的模块，如 q_proj,v_proj；常用 all-linear。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="target_modules 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input v-model:value="form.target_modules" placeholder="all-linear" />
              </a-form-item>
            </a-col>
          </a-row>
        </a-collapse-panel>

        <a-collapse-panel key="core_model" header="核心与模型参数">
          <a-typography-text type="secondary" style="display: block; margin-bottom: 12px">
            模型 ID / 路径请在上方「基于模型」中选择；此处为架构、模板、精度与序列等 ms-swift 参数。
          </a-typography-text>
          <a-row :gutter="16" class="training-form-grid-row">
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    model_type
                    <a-tooltip title="模型架构族（与 config 中 model_type 概念不同）。留空则 swift 自动推断。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="model_type 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input v-model:value="form.model_type" placeholder="留空自动" allow-clear />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    template
                    <a-tooltip title="对话模板类型。留空则按模型自动匹配。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="template 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input v-model:value="form.template" placeholder="如 qwen，留空自动" allow-clear />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="torch_dtype">
                <a-select v-model:value="form.torch_dtype" :options="torchDtypeOptions" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    bf16
                    <a-tooltip title="与 torch_dtype 配合使用；GPU 上常用 bfloat16 + bf16 true。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="bf16 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-switch v-model:checked="form.bf16" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="attn_impl">
                <a-select v-model:value="form.attn_impl" :options="attnImplOptions" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    max_length
                    <a-tooltip title="最大 token 长度，越长越占显存 / 内存。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="max_length 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.max_length" :min="128" :max="8192" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :span="24">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    system
                    <a-tooltip title="系统提示词，或工作区内 .txt 相对路径。数据集中 system 字段优先。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="system 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-textarea v-model:value="form.system" :rows="2" placeholder="可选，用于角色设定等" allow-clear />
              </a-form-item>
            </a-col>
          </a-row>
        </a-collapse-panel>

        <a-collapse-panel key="data_train" header="数据与训练参数">
          <a-typography-text type="secondary" style="display: block; margin-bottom: 12px">
            训练 / 验证集路径见上方「训练集 / 验证集」（随「数据集选择」联动）。保存目录见「输出目录」。
          </a-typography-text>
          <a-row :gutter="16" class="training-form-grid-row">
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="num_train_epochs">
                <a-input-number v-model:value="form.num_train_epochs" :min="1" :max="200" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    per_device_train_batch_size
                    <a-tooltip title="单卡 batch，视显存 1～4。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="batch 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.per_device_train_batch_size" :min="1" :max="128" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="per_device_eval_batch_size">
                <a-input-number v-model:value="form.per_device_eval_batch_size" :min="1" :max="128" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    gradient_accumulation_steps
                    <a-tooltip title="梯度累积，模拟更大 batch。常用 4～8。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="梯度累积说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.gradient_accumulation_steps" :min="1" :max="128" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    learning_rate
                    <a-tooltip title="LoRA 常用 1e-4；全参可更小如 1e-5。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="learning_rate 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.learning_rate" :min="1e-6" :max="1e-2" :step="0.00001" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="lr_scheduler_type">
                <a-select v-model:value="form.lr_scheduler_type" :options="lrSchedulerOptions" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    warmup_ratio
                    <a-tooltip title="预热占总步数比例，如 0.1 表示约 10%。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="warmup_ratio 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.warmup_ratio" :min="0" :max="1" :step="0.01" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="logging_steps">
                <a-input-number v-model:value="form.logging_steps" :min="1" :max="10000000" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="eval_steps">
                <a-input-number v-model:value="form.eval_steps" :min="10" :max="10000000" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="save_steps">
                <a-input-number v-model:value="form.save_steps" :min="10" :max="10000000" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    save_total_limit
                    <a-tooltip title="最多保留 checkpoint 数量。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="save_total_limit 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.save_total_limit" :min="1" :max="20" style="width: 100%" />
              </a-form-item>
            </a-col>
          </a-row>
        </a-collapse-panel>

        <a-collapse-panel key="qlora" header="QLoRA（量化 LoRA）">
          <a-typography-text type="secondary" style="display: block; margin-bottom: 12px">
            对应 ms-swift 的 <code>quant_method</code> / <code>quant_bits</code>（常见文档里的 4bit QLoRA）。不设位数则关闭量化加载。
          </a-typography-text>
          <a-row :gutter="16" class="training-form-grid-row training-form-grid-row--qlora">
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    quant_bits
                    <a-tooltip title="如 4 表示 4bit QLoRA；留空表示不量化加载。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="quant_bits 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.quant_bits" :min="0" :max="8" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    quant_method
                    <a-tooltip title="QLoRA 常用 bnb；留空则在启用 quant_bits 时默认 bnb。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="quant_method 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input v-model:value="form.quant_method" placeholder="bnb" allow-clear />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="bnb_4bit_compute_dtype">
                <a-select v-model:value="form.bnb_4bit_compute_dtype" :options="bnbComputeDtypeOptions" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="bnb_4bit_quant_type">
                <a-select v-model:value="form.bnb_4bit_quant_type" :options="bnbQuantTypeOptions" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="bnb_4bit_use_double_quant">
                <a-switch v-model:checked="form.bnb_4bit_use_double_quant" />
              </a-form-item>
            </a-col>
          </a-row>
        </a-collapse-panel>

        <a-collapse-panel key="advanced_more" header="进阶与其他">
          <a-row :gutter="16" class="training-form-grid-row">
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="dataloader_num_workers">
                <a-input-number v-model:value="form.dataloader_num_workers" :min="0" :max="128" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="train_type">
                <a-select v-model:value="form.train_type" :options="trainTypeOptions" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="tuner_backend">
                <a-select v-model:value="form.tuner_backend" :options="tunerBackendOptions" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="use_dora">
                <a-switch v-model:checked="form.use_dora" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    lorap_lr_ratio
                    <a-tooltip title="LoRA+：B 矩阵学习率倍率，常用约 10～16。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="lorap_lr_ratio 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.lorap_lr_ratio" :min="0" :step="1" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="gradient_checkpointing">
                <a-switch v-model:checked="form.gradient_checkpointing" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    deepspeed
                    <a-tooltip title="如 zero2、zero3 或自定义 json 路径。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="deepspeed 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input v-model:value="form.deepspeed" placeholder="zero2 / 配置文件路径…" allow-clear />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    merge_lora
                    <a-tooltip title="多用于 swift export；训练任务通常保持关闭。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="merge_lora 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-switch v-model:checked="form.merge_lora" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="freeze_vit">
                <a-switch v-model:checked="form.freeze_vit" />
              </a-form-item>
            </a-col>

            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    packing
                    <a-tooltip title="序列打包；需 flash_attn 等，CPU/eager 下请关闭。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="packing 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-switch v-model:checked="form.packing" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    image_max_token_num
                    <a-tooltip title="视觉 token 上限（多模态）。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="image_max_token_num 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input-number v-model:value="form.image_max_token_num" :min="64" :max="2048" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="6">
              <a-form-item label="video_max_token_num">
                <a-input-number v-model:value="form.video_max_token_num" :min="16" :max="512" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :md="18">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    adapters
                    <a-tooltip title="逗号分隔的 adapter 路径；仅加载权重时常用，与 resume_from_checkpoint 不同。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="adapters 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input v-model:value="form.adapters" placeholder="path1,path2" allow-clear />
              </a-form-item>
            </a-col>
            <a-col :span="18">
              <a-form-item>
                <template #label>
                  <span style="display: inline-flex; align-items: center; gap: 4px">
                    resume_from_checkpoint
                    <a-tooltip title="断点目录（工作区相对路径）。「继续训练」会自动填入最新 checkpoint。">
                      <QuestionCircleOutlined
                        style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                        aria-label="resume 说明"
                        role="img"
                      />
                    </a-tooltip>
                  </span>
                </template>
                <a-input v-model:value="form.resume_from_checkpoint" placeholder="可选" allow-clear />
              </a-form-item>
            </a-col>
          </a-row>
        </a-collapse-panel>
      </a-collapse>
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
      <a-col :span="24">
        <a-typography-paragraph>
          <span><b>进度</b>：</span>
          <a-tag
            v-if="currentJobId && overallJobStatusLabel"
            :color="overallJobStatusTagColor"
            style="margin: 0; line-height: 1.5; margin-right: 50px;""
            >
            <span>整体：{{ overallJobStatusLabel }}</span>
          </a-tag>
          <span style="margin-right: 50px;">
            <b>任务名称：</b>
            <template v-if="currentJobNameDisplay">{{ currentJobNameDisplay }}</template>
          </span>
          <span><b>任务ID</b>：{{ currentJobId || "—" }}</span>
        </a-typography-paragraph>
        <div v-if="currentJobId" style="margin-bottom: 10px">
          <div v-for="s in progressStages" :key="s.id" style="margin-bottom: 12px">
            <div style="font-size: 12px; color: rgba(0, 0, 0, 0.65); margin-bottom: 4px"># {{ s.name }}</div>
            <a-progress
              :percent="s.percent == null ? 0 : s.percent"
              :status="stageRowStatus(s.percent)"
              :show-info="s.percent != null"
            />
            <div style="font-size: 12px; color: #666; margin-top: 2px">{{ s.label || "—" }}</div>
          </div>
          <template v-if="!progressStages.length">
            <a-progress
              :percent="progressPercent === null ? 0 : progressPercent"
              :status="progressStatus"
              :show-info="progressPercent !== null"
            />
            <div style="font-size: 12px; color: #666; margin-top: 4px">{{ progressLabel || "—" }}</div>
          </template>
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
            height: 400px;
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
  </a-modal>

</template>

<style scoped>
.training-form-grid-row :deep(.ant-form-item) {
  width: 100%;
}
.training-form-grid-row :deep(.ant-form-item-row) {
  align-items: flex-start;
}
.training-form-grid-row :deep(.ant-form-item-label) {
  flex: 0 0 200px;
  max-width: 45%;
}
.training-form-grid-row :deep(.ant-form-item-control) {
  flex: 1 1 0;
  min-width: 0;
}
.training-form-grid-row.training-form-grid-row--qlora :deep(.ant-form-item-label) {
  flex: 0 0 260px;
  max-width: 55%;
}
</style>
<style>
/* 与 Training.vue 中全屏 Modal 全局样式配套；标题栏放操作按钮 */
.training-fullscreen-modal-wrap .training-modal-title-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  width: 100%;
  padding-right: 36px;
}
.training-fullscreen-modal-wrap .training-modal-title-text {
  font-size: 16px;
  font-weight: 600;
  flex-shrink: 0;
}
.training-fullscreen-modal-wrap .training-modal-title-actions {
  flex: 1;
  min-width: 0;
  justify-content: flex-end;
}
.training-fullscreen-modal-wrap .ant-modal-title {
  width: 100%;
}
</style>
