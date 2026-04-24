<script setup lang="ts">
import { message } from "ant-design-vue";
import {
  EditOutlined,
  InfoCircleOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
} from "@ant-design/icons-vue";
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
  job_name: "",
  train_dataset: "",
  val_dataset: "",
  output_dir: "output/",
  lora_rank: 8,
  lora_alpha: 32,
  lora_dropout: 0.05,
  target_modules: "all-linear",
  model_type: "",
  template: "",
  torch_dtype: "float32",
  bf16: false,
  attn_impl: "eager",
  max_length: 2048,
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
  quant_method: "",
  quant_bits: null as number | null,
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
  dataloader_num_workers: 2,
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
const jobsTableActiveKeys = ref<string[]>(["jobs"]);
/** LoRA / 训练超参：多面板默认全部收起，展开后编辑 */
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
const jobNameEditOpen = ref(false);
const jobNameEditId = ref<string | null>(null);
const jobNameEditValue = ref("");
const jobNameSaving = ref(false);
let logPoll: ReturnType<typeof setInterval> | null = null;
/** 工作区根目录绝对路径（与后端 workspace_root 一致），用于展示 train/val 完整路径 */
const workspaceRootAbs = ref("");
const chartRef = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;
const progressPercent = ref<number | null>(null);
const progressLabel = ref("");
type ProgressStage = { id: string; name: string; percent: number | null; label: string };
const progressStages = ref<ProgressStage[]>([]);
const jobError = ref("");

/** 留空提交时使用：数据集展示名 + # + 毫秒时间戳 */
function defaultTrainJobName(datasetLabel: string) {
  const base = (datasetLabel || "").trim() || "训练";
  return `${base}#${Date.now()}`;
}

/**
 * 查看某任务时：以任务记录中的 output_dir 为准；否则展示用 output/<versionId>（同版本多次训练共用该目录，ms-swift 用 v0-/v1-… 分子目录）。
 * @param versionId 来自 Select 的 @update:value 时传入，避免 v-model 尚未提交导致读到旧选中项。
 */
function syncOutputDirWithTaskOrDataset(versionId?: string | null) {
  if (currentJobId.value) {
    const row = jobs.value.find((j) => (j as { id?: string }).id === currentJobId.value) as
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
  const row = jobs.value.find((j) => (j as { id?: string }).id === id) as { job_name?: string } | undefined;
  const n = row?.job_name;
  if (typeof n === "string" && n.length > 0 && n !== "—") return n;
  return "";
});

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

/** 将工作区内相对路径显示为自工作区根起的绝对路径（与后端解析一致，仅用于展示） */
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
  } else {
    const row = datasetVersionItems.value.find((x) => x.id === versionId) ?? null;
    applyVersionToForm(row);
  }
  syncOutputDirWithTaskOrDataset(versionId);
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
    syncOutputDirWithTaskOrDataset(pick);
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
  void router.push({ path: "/models" });
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

/** 训练进度区标题旁：整体任务状态 */
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

const jobColumns = [
  { title: "训练名称", dataIndex: "job_name", key: "job_name", ellipsis: true, width: 200 },
  { title: "项目", dataIndex: "project_title", key: "project_title", ellipsis: true, width: 120 },
  { title: "批次", dataIndex: "batch_name", key: "batch_name", ellipsis: true, width: 120 },
  { title: "数据集", dataIndex: "dataset_name", key: "dataset_name", ellipsis: true, width: 140 },
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

function onChartsResize() {
  requestAnimationFrame(() => {
    chart?.resize();
  });
}

onMounted(() => {
  void loadWorkspacePaths();
  void loadHubModels();
  void loadDatasetVersions();
  void refreshJobs();
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

/** 列表格展示：统一到数据集版本根目录 output/<versionId>（隐藏任务子目录等）。 */
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

function trainingOutputDirTitle(record: { output_dir?: string | null; request?: { output_dir?: unknown } }): string | undefined {
  const t = trainingOutputDirText(record);
  return t !== "—" ? t : undefined;
}

/** 列表优先展示 ms-swift 本次运行目录（日志解析的 swift_run_relpath，含 v0-/v1-）；尚无则退回版本级路径。 */
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
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "保存失败");
  } finally {
    jobNameSaving.value = false;
  }
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
    const ver =
      selectedDatasetVersionId.value != null && selectedDatasetVersionId.value !== ""
        ? datasetVersionItems.value.find((x) => x.id === selectedDatasetVersionId.value) ?? null
        : null;
    const dataLabel = (ver?.name ?? "").trim() || (ver ? String(ver.id) : "");
    const resolvedJobName = (form.job_name || "").trim() || defaultTrainJobName(dataLabel);
    const dvid = selectedDatasetVersionId.value != null && selectedDatasetVersionId.value !== "" ? String(selectedDatasetVersionId.value) : "";
    const r = await http.post<{
      id: string;
      status: string;
      error_message?: string | null;
      output_dir?: string | null;
    }>("/api/training/jobs", {
      ...form,
      output_dir: "output/",
      dataset_version_id: dvid,
      job_name: resolvedJobName,
      project_title: (ver?.project_title ?? "").trim(),
      batch_name: (ver?.batch_name ?? "").trim(),
      dataset_name: dataLabel,
    });
    currentJobId.value = r.data.id;
    const ro = (r.data.output_dir ?? "").trim();
    form.output_dir = ro || (dvid ? `output/${dvid}` : "output/");
    jobStatus.value = r.data.status;
    jobError.value =
      r.data.error_message != null && String(r.data.error_message).trim() ? String(r.data.error_message) : "";
    form.job_name = "";
    message.success(ro ? `任务已创建：${resolvedJobName}（${ro}）` : `任务已创建：${resolvedJobName}`);
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

async function deleteJobById(jobId: string) {
  if (!jobId) return;
  await http.delete(`/api/training/jobs/${jobId}`);
  message.success("已删除");
  if (currentJobId.value === jobId) {
    currentJobId.value = null;
    syncOutputDirWithTaskOrDataset();
    logText.value = "";
    progressPercent.value = null;
    progressLabel.value = "";
    progressStages.value = [];
    jobError.value = "";
    stopLogPoll();
  }
  await refreshJobs();
}

async function continueTraining() {
  if (!currentJobId.value) return;
  try {
    const r = await http.post(`/api/training/jobs/${currentJobId.value}/retry`);
    currentJobId.value = r.data.id;
    message.success("已从断点继续训练（新任务）");
    logPoll = setInterval(() => void refreshLogs(), 1500);
    void refreshLogs();
    await refreshJobs();
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? String(e));
  }
}

function selectJob(id: string) {
  currentJobId.value = id;
  const row = jobs.value.find((j) => (j as { id?: string }).id === id) as
    | { output_dir?: unknown; request?: { output_dir?: unknown; dataset_version_id?: unknown } }
    | undefined;
  const req = row?.request;
  const topOd = row?.output_dir;
  const raw = typeof topOd === "string" && topOd.trim() && topOd !== "—" ? topOd : req?.output_dir;
  const od = typeof raw === "string" ? raw.trim() : "";
  const dvid = typeof req?.dataset_version_id === "string" ? req.dataset_version_id.trim() : "";
  form.output_dir = od || (dvid ? `output/${dvid}` : "output/");
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
    <a-typography-title :level="4">LoRA 训练</a-typography-title>
    <a-divider style="border-top: 2px solid rgba(0, 0, 0, 0.35)" />
    <a-collapse v-model:activeKey="jobsTableActiveKeys" :bordered="false" style="margin-bottom: 8px; background: transparent">
      <a-collapse-panel key="jobs" header="训练任务列表">
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
                <a @click="selectJob(String((record as { id: string }).id))">查看</a>
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
            <span v-else>{{ text }}</span>
          </template>
        </a-table>
      </a-collapse-panel>
    </a-collapse>
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
    <a-divider style="border-top: 2px solid rgba(0, 0, 0, 0.35)" />
    <a-alert
      type="info"
      show-icon
      message="默认按常用 LoRA 配置：rank=8、alpha=32、dropout=0.05、max_length=2048、3 epoch、梯度累积 4、cosine+10% warmup、日志/评估/保存步数 10/100/500。显存紧张时请降低 max_length、batch、视觉 token，或开启量化（QLoRA）。lora_alpha/lora_rank 比值建议在 2～8。"
      style="margin-bottom: 12px"
    />
    <a-form layout="horizontal">
      <a-row :gutter="16">
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
                @update:value="onDatasetVersionSelect"
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
        <a-col :span="2">
          <a-button type="link" size="small" style="padding: 0" @click="goDatasets">去「数据集」管理</a-button>
        </a-col>

        <a-col :span="10">
          <a-form-item>
            <template #label>
              <span style="display: inline-flex; align-items: center; gap: 4px">
                基于模型
                <a-tooltip title="仅本机已下载">
                  <InfoCircleOutlined
                    style="color: rgba(0, 0, 0, 0.45); cursor: help; font-size: 14px; vertical-align: -0.125em"
                    aria-label="仅本机已下载"
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
                placeholder="无可用模型时请先到设置中下载"
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
            <a-typography-text v-if="!loadingHub && hubModels.length === 0" type="secondary" style="display: block; margin-top: 6px"
              >当前没有检测到魔搭本机已缓存的模型。下载完成后点右侧刷新。</a-typography-text
            >
          </a-form-item>
        </a-col>
        <a-col :span="8">
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

      <a-row :gutter="16">
        <a-col :span="24" style="margin-top: 4px; margin-bottom: 4px">
          <a-space wrap>
            <a-button
              type="primary"
              :loading="submitting"
              :disabled="loadingHub || !hubModels.length || !form.model"
              @click="startTraining"
              >提交训练</a-button
            >
            <a-button :disabled="!currentJobId" @click="cancelJob">取消</a-button>
            <a-button :disabled="!currentJobId" @click="continueTraining">继续训练</a-button>
            <a-button @click="applyYaml">从 YAML 导入配置</a-button>
            <a-button @click="downloadYaml">导出 YAML 配置</a-button>
          </a-space>
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
      <a-col :span="24">
        <a-typography-title :level="5">训练任务</a-typography-title>
        <a-typography-paragraph>
          <span style="margin-right: 10px;"><B>当前训练任务：</B><template v-if="currentJobNameDisplay">{{ currentJobNameDisplay }}</template></span>
          <span><B>任务ID</B>：{{ currentJobId || "—" }}</span>
          <a-tag v-if="jobStatus">{{ jobStatus }}</a-tag>
        </a-typography-paragraph>
        <div
          style="display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; row-gap: 4px"
        >
          <a-typography-title :level="5" style="margin: 0">训练进度</a-typography-title>
          <a-tag
            v-if="currentJobId && overallJobStatusLabel"
            :color="overallJobStatusTagColor"
            style="margin: 0; line-height: 1.5"
            >整体：{{ overallJobStatusLabel }}</a-tag
          >
        </div>
        <div v-if="currentJobId" style="margin-bottom: 10px">
          <div v-for="s in progressStages" :key="s.id" style="margin-bottom: 12px">
            <div style="font-size: 12px; color: rgba(0, 0, 0, 0.65); margin-bottom: 4px">{{ s.name }}</div>
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

/* 折叠面板内栅格表单：每项占满列宽，标签区宽度一致，控件对齐 */
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
/* QLoRA：bnb_* 等 label 更长，加宽标签列 */
.training-form-grid-row.training-form-grid-row--qlora :deep(.ant-form-item-label) {
  flex: 0 0 260px;
  max-width: 55%;
}
</style>
