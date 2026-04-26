<script setup lang="ts">
import { onMounted, ref } from "vue";
import { http } from "../api/http";

/** 与 GET /api/exports/artifacts 一致：仅合并成功的 LoRA 合并产物（与合并页数据源一致） */
type ExportItem = {
  kind: string;
  /** 工作区相对路径：合并后的模型目录 */
  path: string;
  label: string;
  lora_path: string;
  job_id: string;
};

const items = ref<ExportItem[]>([]);
const note = ref("");
const loading = ref(false);

onMounted(() => {
  void load();
});

async function load() {
  loading.value = true;
  try {
    const r = await http.get("/api/exports/artifacts");
    items.value = (r.data?.items ?? []) as ExportItem[];
    note.value = String(r.data?.note ?? "");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div>
    <a-typography-title :level="4">导出 / 产物</a-typography-title>
    <a-alert v-if="note" type="info" :message="note" show-icon style="margin-bottom: 12px" />
    <a-spin :spinning="loading">
      <div v-if="items.length" class="export-artifact-card-grid">
        <a-card
          v-for="it in items"
          :key="it.job_id"
          class="export-artifact-card"
          size="middle"
          hoverable
        >
          <template #title>
            <span class="export-artifact-card-title" :title="it.label">{{ it.label }}</span>
          </template>
          <template #extra>
            <a-tag color="success">合并成功</a-tag>
          </template>
          <div class="export-artifact-card-meta">
            <div class="export-artifact-card-meta-row">
              <span class="export-artifact-card-meta-label">类型</span>
              <span class="export-artifact-card-meta-value" :title="it.kind">{{ it.kind }}</span>
            </div>
            <div class="export-artifact-card-meta-row">
              <span class="export-artifact-card-meta-label">合并后</span>
              <code class="export-artifact-card-path" :title="it.path">{{ it.path }}</code>
            </div>
            <div v-if="it.lora_path" class="export-artifact-card-meta-row">
              <span class="export-artifact-card-meta-label">原 LoRA</span>
              <code class="export-artifact-card-path" :title="it.lora_path">{{ it.lora_path }}</code>
            </div>
            <div class="export-artifact-card-meta-row">
              <span class="export-artifact-card-meta-label">任务 ID</span>
              <span class="export-artifact-card-meta-value" :title="it.job_id">{{ it.job_id }}</span>
            </div>
          </div>
        </a-card>
      </div>
      <a-empty
        v-else-if="!loading"
        description="暂无合并成功的「LoRA 合并」记录，请先在「LoRA 合并」页完成合并。"
        style="margin: 16px 0 8px"
      />
    </a-spin>
  </div>
</template>

<style scoped>
.export-artifact-card-grid {
  margin-top: 4px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 350px), 1fr));
  gap: 16px;
}
.export-artifact-card {
  border-radius: 10px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.export-artifact-card :deep(.ant-card-head) {
  min-height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.export-artifact-card :deep(.ant-card-head-title) {
  padding: 10px 0;
  min-width: 0;
}
.export-artifact-card :deep(.ant-card-extra) {
  padding: 10px 0;
}
.export-artifact-card :deep(.ant-card-body) {
  padding: 12px 16px 14px;
}
.export-artifact-card-title {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  font-size: 14px;
  color: rgba(0, 0, 0, 0.88);
}
.export-artifact-card-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12px;
}
.export-artifact-card-meta-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  min-width: 0;
}
.export-artifact-card-meta-label {
  flex-shrink: 0;
  width: 64px;
  color: rgba(0, 0, 0, 0.45);
  line-height: 1.5;
}
.export-artifact-card-meta-value {
  flex: 1;
  min-width: 0;
  line-height: 1.5;
  word-break: break-all;
  color: rgba(0, 0, 0, 0.85);
}
.export-artifact-card-path {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
  color: rgba(0, 0, 0, 0.75);
  background: rgba(0, 0, 0, 0.04);
  padding: 2px 6px;
  border-radius: 4px;
}
</style>
