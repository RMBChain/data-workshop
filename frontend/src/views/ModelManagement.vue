<script setup lang="ts">
import { message } from "ant-design-vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { http } from "../api/http";

type HubDownloadRecord = {
  /** 旧数据可能无此字段，有 completed_at 则按成功记录展示 */
  status?: "downloading" | "completed" | "failed";
  size_bytes?: number;
  files_total?: number;
  total_bytes_expected?: number;
  completed_at?: string;
  started_at?: string;
  failed_at?: string;
  error?: string;
};

type Row = {
  model_id: string;
  path: string;
  size_bytes: number;
  download_record?: HubDownloadRecord;
};

type JobState = {
  job_id: string;
  model_id: string;
  status: "running" | "completed" | "failed";
  error: string | null;
  local_path: string | null;
  current_file: string;
  current_file_total: number;
  current_file_done: number;
  current_file_percent: number | null;
  bytes_downloaded: number;
  files_completed: number;
  total_bytes_expected: number;
  files_total_expected: number;
  overall_percent: number | null;
};

type JobRow = {
  kind: "job";
  key: string;
  model_id: string;
  path: string;
  size_bytes: number;
  job: JobState;
};

type CachedRow = {
  kind: "cached";
  key: string;
  model_id: string;
  path: string;
  size_bytes: number;
  download_record?: HubDownloadRecord;
};

type TableRow = JobRow | CachedRow;

/** 魔搭 / HF 均为 Qwen/ 命名空间，与 snapshot_download 一致 */
const QWEN3_VL_PRESETS = [
  "Qwen/Qwen3-VL-2B-Instruct",
  "Qwen/Qwen3-VL-2B-Thinking",
  "Qwen/Qwen3-VL-4B-Instruct",
  "Qwen/Qwen3-VL-4B-Thinking",
  "Qwen/Qwen3-VL-8B-Instruct",
  "Qwen/Qwen3-VL-8B-Thinking",
  "Qwen/Qwen3-VL-30B-A3B-Instruct",
  "Qwen/Qwen3-VL-30B-A3B-Thinking",
  "Qwen/Qwen3-VL-32B-Instruct",
  "Qwen/Qwen3-VL-32B-Thinking",
  "Qwen/Qwen3-VL-235B-A22B-Instruct",
  "Qwen/Qwen3-VL-235B-A22B-Thinking",
  "Qwen/Qwen3-VL-235B-A22B-Instruct-FP8",
];

const modelPresetOptions = QWEN3_VL_PRESETS.map((value) => ({ value }));

const loading = ref(false);
const items = ref<Row[]>([]);
const hubRoot = ref("");
const downloadId = ref("Qwen/Qwen3-VL-2B-Instruct");
const downloading = ref(false);
const activeJobs = ref<JobState[]>([]);

let pollTimer: ReturnType<typeof setInterval> | null = null;
let pollBusy = false;

const tableData = computed<TableRow[]>(() => {
  const jobModelIds = new Set(activeJobs.value.map((j) => j.model_id));
  const jobs: JobRow[] = activeJobs.value.map((j) => ({
    kind: "job",
    key: `job:${j.job_id}`,
    model_id: j.model_id,
    path: "—",
    size_bytes: 0,
    job: j,
  }));
  const cached: CachedRow[] = items.value
    .filter((r) => !jobModelIds.has(r.model_id))
    .map((r) => ({
      kind: "cached" as const,
      key: r.model_id,
      ...r,
    }));
  return [...jobs, ...cached];
});

const columns = [
  { title: "模型 ID", dataIndex: "model_id", key: "model_id", ellipsis: true },
  { title: "相对路径", dataIndex: "path", key: "path", ellipsis: true },
  {
    title: "大小",
    dataIndex: "size_bytes",
    key: "size_bytes",
    width: 100,
  },
  { title: "下载 / 状态", key: "status", width: 400 },
  { title: "操作", key: "act", width: 90 },
];

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatRecordTime(iso: string) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function isDownloadRecordSuccess(rec: HubDownloadRecord | undefined): boolean {
  if (!rec) return false;
  if (rec.status === "failed" || rec.status === "downloading") return false;
  if (rec.status === "completed") return true;
  // 旧版持久化无 status，有 completed_at 即视为成功记录
  return Boolean(rec.completed_at);
}

function stopPoll() {
  if (pollTimer != null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function tickPoll() {
  if (pollBusy) return;
  const running = activeJobs.value.filter((j) => j.status === "running");
  if (running.length === 0) {
    stopPoll();
    return;
  }
  pollBusy = true;
  try {
    for (const j of running) {
      try {
        const r = await http.get<
          JobState & { current_file_percent?: number | null; overall_percent?: number | null }
        >(`/api/models/hub/download/${j.job_id}`);
        const u = r.data;
        const idx = activeJobs.value.findIndex((x) => x.job_id === j.job_id);
        if (idx < 0) continue;
        activeJobs.value[idx] = { ...activeJobs.value[idx], ...u };
        if (u.status === "completed") {
          message.success(`${u.model_id} 已下载完成`);
          activeJobs.value = activeJobs.value.filter((x) => x.job_id !== j.job_id);
          await refresh();
        } else if (u.status === "failed") {
          message.error(u.error ?? "下载失败");
          activeJobs.value = activeJobs.value.filter((x) => x.job_id !== j.job_id);
        }
      } catch {
        /* 轮询短暂失败时忽略 */
      }
    }
  } finally {
    pollBusy = false;
  }
}

function startPoll() {
  if (pollTimer != null) return;
  pollTimer = setInterval(() => {
    void tickPoll();
  }, 600);
  void tickPoll();
}

async function refresh() {
  loading.value = true;
  try {
    const r = await http.get<{
      items: Row[];
      hub_root: string;
      active_downloads?: JobState[];
    }>("/api/models/hub");
    items.value = r.data.items;
    hubRoot.value = r.data.hub_root;
    if (r.data.active_downloads?.length) {
      activeJobs.value = r.data.active_downloads;
      startPoll();
    } else {
      activeJobs.value = [];
      stopPoll();
    }
  } catch (e) {
    message.error("加载模型列表失败");
  } finally {
    loading.value = false;
  }
}

async function doDownload() {
  const id = downloadId.value.trim();
  if (!id) {
    message.warning("请填写 ModelScope 模型 id");
    return;
  }
  downloading.value = true;
  try {
    const r = await http.post<{
      job_id: string;
      model_id: string;
      total_bytes_expected: number;
      files_total_expected: number;
    }>("/api/models/hub/download", {
      model_id: id,
    });
    const tb = r.data.total_bytes_expected ?? 0;
    activeJobs.value.push({
      job_id: r.data.job_id,
      model_id: r.data.model_id,
      status: "running",
      error: null,
      local_path: null,
      current_file: "",
      current_file_total: 0,
      current_file_done: 0,
      current_file_percent: null,
      bytes_downloaded: 0,
      files_completed: 0,
      total_bytes_expected: tb,
      files_total_expected: r.data.files_total_expected ?? 0,
      overall_percent: tb > 0 ? 0 : null,
    });
    startPoll();
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "创建下载任务失败");
  } finally {
    downloading.value = false;
  }
}

async function doDelete(id: string) {
  try {
    await http.delete("/api/models/hub", { data: { model_id: id } });
    message.success("已删除本机缓存");
    await refresh();
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "删除失败");
  }
}

function jobProgressPercent(j: JobState): number {
  if (j.overall_percent != null && !Number.isNaN(j.overall_percent)) {
    return Math.min(100, Math.max(0, j.overall_percent));
  }
  if (j.current_file_percent != null) {
    return Math.min(99, Math.max(0, j.current_file_percent));
  }
  if (j.bytes_downloaded > 0) return 5;
  return 0;
}

onMounted(() => {
  void refresh();
});

onUnmounted(() => {
  stopPoll();
});
</script>

<template>
  <div>
    <a-typography-paragraph type="secondary">
      仅管理本机 ModelScope 缓存（hub）。点击下载后将出现在列表首行并显示进度；完成后自动刷新。
    </a-typography-paragraph>
    <a-typography-paragraph v-if="hubRoot" copyable type="secondary" style="font-size: 12px"
      >hub 根：{{ hubRoot }}</a-typography-paragraph
    >
    <a-space style="margin-bottom: 12px" wrap>
      <a-auto-complete
        v-model:value="downloadId"
        :options="modelPresetOptions"
        placeholder="选择或输入 ModelScope 模型 id（预设为 Qwen3-VL 系列）"
        allow-clear
        style="min-width: 360px"
        option-filter-prop="value"
      />
      <a-button type="primary" :loading="downloading" @click="doDownload">从 ModelScope 下载</a-button>
      <a-button :loading="loading" @click="refresh">刷新列表</a-button>
    </a-space>
    <a-table
      :loading="loading"
      :columns="columns"
      :data-source="tableData"
      :pagination="false"
      size="small"
      row-key="key"
    >
      <template #bodyCell="{ column, text, record }">
        <template
          v-if="column && (column as { key?: string }).key === 'act' && record && typeof record === 'object' && 'kind' in record && (record as TableRow).kind === 'cached'"
        >
          <a-popconfirm
            :title="`确定删除本机 ${String((record as CachedRow).model_id)} ？`"
            @confirm="doDelete(String((record as CachedRow).model_id))"
          >
            <a>删除</a>
          </a-popconfirm>
        </template>
        <span v-else-if="column && (column as { key?: string }).key === 'act'">—</span>
        <template v-else-if="column && (column as { key?: string }).key === 'status' && record && typeof record === 'object'">
          <template v-if="'kind' in record && (record as TableRow).kind === 'job'">
            <template v-if="(record as JobRow).job.status === 'running'">
              <div
                style="
                  font-size: 13px;
                  font-weight: 500;
                  margin-bottom: 6px;
                  color: rgba(0, 0, 0, 0.88);
                "
              >
                下载中
              </div>
              <a-progress
                :percent="jobProgressPercent((record as JobRow).job)"
                status="active"
                size="small"
              />
              <div style="margin-top: 4px; font-size: 12px; color: rgba(0, 0, 0, 0.45)">
                <span v-if="(record as JobRow).job.overall_percent != null"
                  >整体 {{ Number((record as JobRow).job.overall_percent).toFixed(1) }}%</span
                >
                <span v-else-if="(record as JobRow).job.total_bytes_expected > 0">整体 计算中…</span>
                <span v-if="(record as JobRow).job.current_file"> · 当前：{{ (record as JobRow).job.current_file }}</span>
                <span v-else> · 正在连接并开始下载…</span>
                <span> · 累计 {{ formatBytes((record as JobRow).job.bytes_downloaded || 0) }}</span>
                <span v-if="(record as JobRow).job.total_bytes_expected > 0">
                  / {{ formatBytes((record as JobRow).job.total_bytes_expected) }}</span
                >
                <span v-if="(record as JobRow).job.files_total_expected > 0">
                  · 文件 {{ (record as JobRow).job.files_completed || 0 }}/{{
                    (record as JobRow).job.files_total_expected
                  }}</span
                >
              </div>
            </template>
          </template>
          <div v-else>
            <template v-if="'kind' in record && (record as TableRow).kind === 'cached'">
              <template
                v-if="(record as CachedRow).download_record?.status === 'failed'"
              >
                <a-typography-text type="danger">上次下载失败</a-typography-text>
                <div
                  v-if="(record as CachedRow).download_record?.error"
                  style="margin-top: 4px; font-size: 12px; color: rgba(0, 0, 0, 0.45); line-height: 1.4; word-break: break-word"
                >
                  {{ (record as CachedRow).download_record?.error }}
                </div>
                <div
                  v-if="(record as CachedRow).download_record?.failed_at"
                  style="margin-top: 2px; font-size: 12px; color: rgba(0, 0, 0, 0.35)"
                >
                  {{ formatRecordTime(String((record as CachedRow).download_record?.failed_at)) }}
                </div>
              </template>
              <template
                v-else-if="isDownloadRecordSuccess((record as CachedRow).download_record)"
              >
                <a-typography-text type="secondary">已缓存</a-typography-text>
                <div
                  style="margin-top: 4px; font-size: 12px; color: rgba(0, 0, 0, 0.45); line-height: 1.4"
                >
                  本应用下载完成
                  <span v-if="(record as CachedRow).download_record?.completed_at">
                    ·
                    {{ formatRecordTime(String((record as CachedRow).download_record?.completed_at)) }}
                  </span>
                  <span
                    v-if="((record as CachedRow).download_record?.files_total ?? 0) > 0"
                  >
                    · 文件数 {{ (record as CachedRow).download_record?.files_total }}
                  </span>
                </div>
              </template>
              <template
                v-else-if="(record as CachedRow).download_record?.status === 'downloading'"
              >
                <a-typography-text type="secondary">已缓存</a-typography-text>
                <div
                  style="margin-top: 4px; font-size: 12px; color: rgba(0, 0, 0, 0.45)"
                >
                  状态：下载中（请点「刷新列表」同步进行中的任务）
                </div>
              </template>
              <a-typography-text v-else type="secondary">已缓存</a-typography-text>
            </template>
            <a-typography-text v-else type="secondary">已缓存</a-typography-text>
          </div>
        </template>
        <span v-else-if="column && (column as { key?: string }).key === 'size_bytes'">{{ formatBytes(Number(text) || 0) }}</span>
        <span v-else>{{ text }}</span>
      </template>
    </a-table>
  </div>
</template>
