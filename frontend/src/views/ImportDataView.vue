<script setup lang="ts">
import { message } from "ant-design-vue";
import { onMounted, reactive, ref } from "vue";
import { http } from "../api/http";

const filters = reactive({ project_title: "", batch_name: "" });
const tasks = ref<Record<string, unknown>[]>([]);
const page = ref(1);
const total = ref(0);
const form = reactive({ page_size: 20 });
const loadingTasks = ref(false);
const rawModalOpen = ref(false);
const rawModalTitle = ref("");
const rawModalText = ref("");
const taskImageBroken = ref<Record<string, boolean>>({});

onMounted(() => {
  void search();
});

async function fetchTasks() {
  loadingTasks.value = true;
  try {
    const params: Record<string, string | number> = {
      page: page.value,
      page_size: form.page_size,
    };
    if (filters.project_title.trim()) params.project_title = filters.project_title.trim();
    if (filters.batch_name.trim()) params.batch_name = filters.batch_name.trim();
    const r = await http.get("/api/import-tasks", { params });
    tasks.value = ((r.data.items as Record<string, unknown>[]) || []).map((row) => ({
      ...row,
      resolved_label: (row as { resolved?: number }).resolved ? "是" : "否",
    }));
    total.value = r.data.total ?? 0;
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "搜索任务失败");
    tasks.value = [];
    total.value = 0;
  } finally {
    loadingTasks.value = false;
  }
}

async function search() {
  page.value = 1;
  taskImageBroken.value = {};
  await fetchTasks();
}

function resetFilters() {
  filters.project_title = "";
  filters.batch_name = "";
  void search();
}

function onTaskTableChange(p: number, ps?: number) {
  page.value = p;
  if (ps != null) form.page_size = ps;
  taskImageBroken.value = {};
  void fetchTasks();
}

async function openTaskRaw(record: { id: string; batch_id?: string; ls_task_id?: number }) {
  const bid = record.batch_id != null ? String(record.batch_id) : "";
  if (!bid) {
    message.warning("缺少批次信息");
    return;
  }
  try {
    const r = await http.get(
      `/api/imports/${encodeURIComponent(bid)}/tasks/${encodeURIComponent(record.id)}/raw`,
    );
    const payload = r.data as { raw?: unknown; raw_text?: string };
    let text: string;
    if (payload.raw !== undefined && payload.raw !== null) {
      text = JSON.stringify(payload.raw, null, 2);
    } else if (typeof payload.raw_text === "string") {
      text = payload.raw_text;
    } else {
      text = "（无原始数据）";
    }
    rawModalTitle.value = `LS 任务 ${record.ls_task_id ?? "?"} · 原始 JSON`;
    rawModalText.value = text;
    rawModalOpen.value = true;
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "获取原始数据失败");
  }
}

function taskImageUrl(batchId: string, importTaskId: string) {
  return `/api/imports/${encodeURIComponent(batchId)}/tasks/${encodeURIComponent(importTaskId)}/image`;
}

function onTaskImageError(importTaskId: string) {
  taskImageBroken.value = { ...taskImageBroken.value, [importTaskId]: true };
}

function ellipsisText(v: unknown, maxChars: number): string {
  if (v == null || v === "") return "-";
  const s = String(v);
  if (s.length <= maxChars) return s;
  return `${s.slice(0, maxChars)}…`;
}

function taskImagePathTooltip(record: { image_rel?: unknown }): string {
  const ir = record?.image_rel;
  if (ir == null || String(ir).trim() === "") return "暂无图片路径";
  return String(ir);
}

const taskColumns = [
  { title: "项目名称", dataIndex: "project_title", key: "project_title", ellipsis: true, width: 120, align: "center" as const },
  { title: "批次名称", dataIndex: "batch_name", key: "batch_name", ellipsis: true, width: 120, align: "center" as const },
  { title: "任务 ID", dataIndex: "ls_task_id", key: "ls_task_id", width: 90, align: "center" as const },
  { title: "图片", key: "task_image", width: 80, align: "center" as const },
  { title: "图片路径", key: "image_rel", width: 100, align: "center" as const },
  { title: "标注数", dataIndex: "annotation_count", key: "annotation_count", width: 80, align: "center" as const },
  { title: "已解析", dataIndex: "resolved_label", key: "resolved_label", width: 80, align: "center" as const },
  { title: "原始数据", key: "raw_action", width: 100, align: "center" as const },
];
</script>

<template>
  <div>
    <a-typography-title :level="4">数据查看</a-typography-title>
    <a-typography-paragraph type="secondary" style="margin-bottom: 16px">
      按项目名称、批次名称在已导入任务中模糊搜索；留空则列出全部任务（分页）。
    </a-typography-paragraph>

    <a-form layout="inline" style="margin-bottom: 16px; gap: 8px; flex-wrap: wrap">
      <a-form-item label="项目名称">
        <a-input
          v-model:value="filters.project_title"
          allow-clear
          placeholder="模糊匹配"
          style="width: 220px"
          @press-enter="search"
        />
      </a-form-item>
      <a-form-item label="批次名称">
        <a-input
          v-model:value="filters.batch_name"
          allow-clear
          placeholder="模糊匹配"
          style="width: 220px"
          @press-enter="search"
        />
      </a-form-item>
      <a-form-item>
        <a-space>
          <a-button type="primary" :loading="loadingTasks" @click="search">搜索</a-button>
          <a-button @click="resetFilters">重置</a-button>
        </a-space>
      </a-form-item>
    </a-form>

    <a-typography-title :level="5" style="margin: 0 0 12px">导入任务</a-typography-title>
    <a-table
      :columns="taskColumns"
      :data-source="tasks"
      :loading="loadingTasks"
      :pagination="{
        current: page,
        pageSize: form.page_size,
        total: total,
        showSizeChanger: true,
        pageSizeOptions: ['10', '20', '50', '100'],
        onChange: onTaskTableChange,
      }"
      row-key="id"
      size="small"
      :scroll="{ x: true }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'task_image'">
          <div class="import-task-image-cell">
            <template
              v-if="(record as any).has_task_image && !taskImageBroken[String((record as any).id)]"
            >
              <a-image
                class="import-task-thumb"
                :src="taskImageUrl(String((record as any).batch_id), String((record as any).id))"
                :width="48"
                :height="48"
                alt=""
                :preview="true"
                @error="onTaskImageError(String((record as any).id))"
              />
            </template>
            <span v-else class="import-task-image-placeholder">—</span>
          </div>
        </template>
        <template v-else-if="column.key === 'image_rel'">
          <span
            class="import-task-image-rel"
            :title="
              (record as any).image_rel != null && String((record as any).image_rel) !== ''
                ? String((record as any).image_rel)
                : undefined
            "
          >
            {{ (record as any).image_rel, 14 }}
          </span>
        </template>
        <template v-else-if="column.key === 'annotation_count'">
          {{ (record as any).annotation_count ?? 0 }}
        </template>
        <template v-else-if="column.key === 'raw_action'">
          <a-tooltip :title="taskImagePathTooltip(record as any)">
            <a @click.stop="openTaskRaw(record as any)">查看 JSON</a>
          </a-tooltip>
        </template>
        <span v-else>{{ (record as any)?.[column.dataIndex as string] ?? "-" }}</span>
      </template>
    </a-table>
    <a-typography-paragraph v-if="!loadingTasks && !tasks.length" type="secondary" style="margin-top: 8px">
      无匹配任务。可调整筛选条件或先到「数据导入」完成导入。
    </a-typography-paragraph>

    <a-modal
      v-model:open="rawModalOpen"
      :title="rawModalTitle"
      width="min(920px, 96vw)"
      :footer="null"
      destroy-on-close
    >
      <pre
        style="
          margin: 0;
          max-height: 72vh;
          overflow: auto;
          font-size: 12px;
          white-space: pre-wrap;
          word-break: break-word;
        "
      >{{ rawModalText }}</pre>
    </a-modal>
  </div>
</template>

<style scoped>
.import-task-image-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
}
.import-task-thumb {
  flex-shrink: 0;
}
.import-task-thumb :deep(.ant-image) {
  border-radius: 4px;
  overflow: hidden;
  border: 1px solid rgba(0, 0, 0, 0.06);
}
.import-task-thumb :deep(.ant-image-img) {
  object-fit: cover;
  height: 48px !important;
}
.import-task-image-placeholder {
  color: rgba(0, 0, 0, 0.25);
  font-size: 12px;
}

.import-task-image-rel {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  vertical-align: bottom;
  cursor: default;
}
</style>
