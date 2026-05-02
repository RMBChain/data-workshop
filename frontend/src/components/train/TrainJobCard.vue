<script setup lang="ts">
import { DeleteOutlined, EditOutlined, LoadingOutlined } from "@ant-design/icons-vue";
import type { MergeUiStatus } from "../../views/train/trainTypes";
import { MERGE_STATUS_LABEL } from "../../views/train/trainTypes";
import {
  formatJobStatus,
  mergeStatusTagColor,
  trainingJobCardMeta,
  trainingJobNameTitle,
  trainingJobNameText,
  trainingJobStatusTagColor,
} from "../../views/train/trainFormatters";

const props = defineProps<{
  record: Record<string, unknown>;
  mergeUiStatus: MergeUiStatus;
  mergedPathRow: { value: string; pathTooltip?: string };
  mergeSubmitting: boolean;
}>();

defineEmits<{
  editName: [record: Record<string, unknown>];
  delete: [jobId: string];
  train: [jobId: string];
  verify: [jobId: string];
  eval: [jobId: string];
  merge: [record: Record<string, unknown>];
}>();

function jobId(): string {
  return String(props.record.id ?? "");
}
</script>

<template>
  <a-card class="train-job-card" size="middle" hoverable>
    <template #title>
      <div class="train-job-card-title">
        <span class="train-job-card-title-text" :title="trainingJobNameTitle(record)">
          {{ trainingJobNameText(record) }}
        </span>
      </div>
    </template>
    <template #extra>
      <span class="train-job-card-extra" @click.stop>
        <EditOutlined class="training-job-name-edit" @click="$emit('editName', record)" />
        <a-popconfirm
          title="确定删除？将移除任务记录与日志；仅当无其它任务共用同一 train/ 子目录时，才删除该目录下文件（如 checkpoint/LoRA）。"
          ok-text="确定"
          cancel-text="取消"
          @confirm="$emit('delete', jobId())"
        >
          <a-button type="link" danger aria-label="删除">
            <template #icon>
              <DeleteOutlined />
            </template>
          </a-button>
        </a-popconfirm>
        <a-tag :color="trainingJobStatusTagColor(record.status)">{{ formatJobStatus(record.status) }}</a-tag>
        <a-tooltip v-if="mergeUiStatus === 'merging'" title="合并任务进行中，请稍候">
          <a-tag
            :color="mergeStatusTagColor(mergeUiStatus)"
            class="train-merge-status-tag train-merge-status-tag--merging"
            @click.stop
          >
            <LoadingOutlined spin class="train-merge-status-tag__spin" />
            <span>{{ MERGE_STATUS_LABEL[mergeUiStatus] }}</span>
          </a-tag>
        </a-tooltip>

        <a-tag v-else :color="mergeStatusTagColor(mergeUiStatus)" @click.stop>
          {{ MERGE_STATUS_LABEL[mergeUiStatus] }}
        </a-tag>
      </span>
    </template>
    <div class="train-job-card-meta">
      <div
        v-for="row in trainingJobCardMeta(record, mergedPathRow)"
        :key="row.label"
        class="train-job-card-meta-row"
      >
        <span class="train-job-card-meta-label">{{ row.label }}</span>
        <div v-if="row.pathTooltip && row.value !== '—'" class="train-job-card-meta-value-wrap">
          <a-tooltip
            :title="row.pathTooltip"
            placement="topLeft"
            :overlay-style="{ maxWidth: 'min(90vw, 560px)' }"
          >
            <span class="train-job-card-meta-value train-job-card-meta-value--ellipsis">
              {{ row.value }}
            </span>
          </a-tooltip>
        </div>
        <span v-else class="train-job-card-meta-value" :title="row.value">
          {{ row.value }}
        </span>
      </div>
      <div class="train-job-card-meta-actions">
        <a-button type="link" @click="$emit('train', jobId())">训练</a-button>
        →
        <a-button type="link" @click="$emit('verify', jobId())">验证</a-button>
        →
        <a-button type="link" :loading="mergeSubmitting" @click.stop="$emit('merge', record)">
          合并
        </a-button>
        →
        <a-button type="link" @click="$emit('eval', jobId())">评测</a-button>
      </div>
    </div>
  </a-card>
</template>

<style scoped>
.train-job-card {
  border-radius: 10px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.train-job-card :deep(.ant-card-head) {
  min-height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.train-job-card :deep(.ant-card-head-title) {
  padding: 10px 0;
  min-width: 0;
}
.train-job-card :deep(.ant-card-extra) {
  padding: 10px 0;
}
.train-job-card :deep(.ant-card-body) {
  padding: 12px 16px 14px;
}
.train-job-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.train-job-card-title-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  font-size: 14px;
  color: rgba(0, 0, 0, 0.88);
}
.train-job-card-extra {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.train-job-card-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12px;
}
.train-job-card-meta-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  min-width: 0;
}
.train-job-card-meta-actions {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  font-size: 24px;
}
.train-job-card-meta-actions :deep(.ant-btn) {
  padding-inline: 4px;
  font-size: 20px;
}
.train-job-card-meta-actions :deep(.ant-btn.ant-btn-link) {
  padding-block: 0;
  min-height: 0;
  height: auto;
  line-height: inherit;
}
.train-job-card-meta-label {
  flex-shrink: 0;
  width: 96px;
  color: rgba(0, 0, 0, 0.45);
  line-height: 1.5;
}
.train-job-card-meta-value {
  flex: 1;
  min-width: 0;
  line-height: 1.5;
  word-break: break-all;
  color: rgba(0, 0, 0, 0.85);
}
.train-job-card-meta-value-wrap {
  flex: 1;
  min-width: 0;
}
.train-job-card-meta-value-wrap :deep(.ant-tooltip-disabled-compatible-wrapper) {
  display: block;
  max-width: 100%;
}
.train-job-card-meta-value--ellipsis {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  word-break: normal;
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
.train-merge-status-tag--merging {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  animation: train-merge-status-tag-pulse 1.6s ease-in-out infinite;
  cursor: default;
}
.train-merge-status-tag__spin {
  font-size: 12px;
}
@keyframes train-merge-status-tag-pulse {
  0%,
  100% {
    opacity: 1;
    filter: brightness(1);
  }
  50% {
    opacity: 0.88;
    filter: brightness(1.06);
  }
}
</style>
