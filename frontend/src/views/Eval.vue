<script setup lang="ts">
import { message } from "ant-design-vue";
import { computed, onMounted, ref, watch } from "vue";
import { http } from "../api/http";

type MergedModelRow = {
  id: string;
  path: string;
  label: string;
  /** 来自 dataset_versions.val_relpath（与合并所用 LoRA 对应训练的数据集版本一致） */
  val_jsonl: string;
  dataset_version_id?: string | null;
  /** 写入 workshop_merge_meta.json 的 Web 合并任务 id（仅新合并有） */
  merge_job_id?: string | null;
};

const DEFAULT_VAL = "data/val.jsonl";
const dataPath = ref(DEFAULT_VAL);
const mergedModels = ref<MergedModelRow[]>([]);
const mergedModelsLoading = ref(false);
const selectedMergedPath = ref<string | null>(null);
const acc = ref(true);
const bleu = ref(true);
const rouge = ref(true);
const job = ref<Record<string, unknown> | null>(null);
const items = ref<Record<string, unknown>[]>([]);
let t: ReturnType<typeof setInterval> | null = null;

function filterMergedOption(input: string, option: { value?: string | null }) {
  const q = input.trim().toLowerCase();
  if (!q) return true;
  const m = mergedModels.value.find((x) => x.path === option.value);
  if (!m) return false;
  const extra = [m.merge_job_id, m.dataset_version_id].filter(Boolean).join(" ");
  return `${m.label} ${m.path} ${extra}`.toLowerCase().includes(q);
}

async function loadMergedModels() {
  mergedModelsLoading.value = true;
  try {
    const r = await http.get("/api/eval/merged-models");
    mergedModels.value = (r.data.items ?? []) as MergedModelRow[];
  } catch {
    mergedModels.value = [];
  } finally {
    mergedModelsLoading.value = false;
  }
}

/** 与后端 workspace_root 一致，仅用于展示完整路径 */
const workspaceRootAbs = ref("");

/** 将工作区内相对路径显示为自工作区根起的绝对路径（与 Training.vue 一致，仅用于展示） */
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

const dataPathFull = computed(() => workspaceDatasetAbsDisplay(dataPath.value));

const selectedMergedModel = computed((): MergedModelRow | null => {
  const p = selectedMergedPath.value;
  if (!p) return null;
  return mergedModels.value.find((x) => x.path === p) ?? null;
});

async function loadWorkspacePaths() {
  try {
    const r = await http.get<{ workspace_root?: string }>("/api/system/paths");
    workspaceRootAbs.value = (r.data.workspace_root ?? "").trim();
  } catch {
    workspaceRootAbs.value = "";
  }
}

onMounted(() => {
  void loadWorkspacePaths();
  void loadMergedModels();
});

watch(selectedMergedPath, (p) => {
  if (!p) {
    dataPath.value = DEFAULT_VAL;
    return;
  }
  const row = mergedModels.value.find((x) => x.path === p);
  dataPath.value = row?.val_jsonl?.trim() || DEFAULT_VAL;
});

async function start() {
  const r = await http.post("/api/eval/jobs", {
    data_jsonl: dataPath.value,
    enable_accuracy: acc.value,
    enable_bleu: bleu.value,
    enable_rouge: rouge.value,
  });
  const jid = r.data.job_id as string;
  job.value = { id: jid, status: "pending" };
  if (t) clearInterval(t);
  t = setInterval(async () => {
    const s = await http.get(`/api/eval/jobs/${jid}`);
    job.value = s.data;
    if (s.data.status === "succeeded") {
      if (t) clearInterval(t);
      const it = await http.get(`/api/eval/jobs/${jid}/items`, { params: { page: 1, page_size: 50 } });
      items.value = it.data.items;
      message.success("评测完成");
    }
  }, 1000);
}
</script>

<template>
  <div>
    <a-typography-title :level="4">评测</a-typography-title>
    <a-alert
      type="info"
      show-icon
      message="评测合并后的LoRA模型。"
      style="margin-bottom: 12px"
    />
    <a-form layout="vertical" style="max-width: 480px">
      <a-form-item label="通过 LoRA 合并的模型">
        <a-select
          v-model:value="selectedMergedPath"
          allow-clear
          show-search
          :filter-option="filterMergedOption"
          :loading="mergedModelsLoading"
          :options="
            mergedModels.map((m) => ({
              value: m.path,
              label: m.label,
            }))
          "
          placeholder="选择合并产物后自动填充下方验证集路径"
        />
      </a-form-item>
      <a-form-item label="验证集 JSONL（工作区相对）">
        <a-input
          v-model:value="dataPath"
          placeholder="选择上方合并模型后，按该次合并所用训练的数据集版本自动填入 val"
        />
        <a-typography-text
          v-if="selectedMergedModel?.dataset_version_id || selectedMergedModel?.merge_job_id"
          type="secondary"
          style="display: block; margin-top: 6px; font-size: 12px"
        >
          <template v-if="selectedMergedModel?.merge_job_id"
            >合并任务 id：{{ selectedMergedModel.merge_job_id }} ·
          </template>
          <template v-if="selectedMergedModel?.dataset_version_id"
            >数据集版本 id：{{ selectedMergedModel.dataset_version_id }}（val 来自该版本在库中的 <code>val</code> 路径）</template
          >
        </a-typography-text>
        <a-typography-text v-if="dataPathFull" type="secondary" style="display: block; margin-top: 6px"
          >完整路径：{{ dataPathFull }}</a-typography-text
        >
      </a-form-item>
      <a-form-item label="指标">
        <a-checkbox v-model:checked="acc">准确率</a-checkbox>
        <a-checkbox v-model:checked="bleu">BLEU</a-checkbox>
        <a-checkbox v-model:checked="rouge">ROUGE</a-checkbox>
      </a-form-item>
      <a-button type="primary" @click="start">开始评测</a-button>
    </a-form>
    <a-typography-paragraph v-if="job" style="margin-top: 12px"
      >状态：{{ String((job as { status?: string }).status) }} 概览：{{
        JSON.stringify((job as { summary?: unknown }).summary)
      }}</a-typography-paragraph
    >
    <a-table
      v-if="items.length"
      :columns="[
        { title: '序号', dataIndex: 'index', key: 'i', width: 60 },
        { title: '说明', dataIndex: 'message', key: 'm' },
      ]"
      :data-source="items as Record<string, unknown>[]"
      :pagination="false"
      size="small"
      row-key="index"
    />
  </div>
</template>
