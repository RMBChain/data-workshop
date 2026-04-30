<script setup lang="ts">
const open = defineModel<boolean>("open", { required: true });

defineProps<{
  loading: boolean;
  text: string;
}>();

const emptyHint =
  "（暂无该训练任务的合并日志。在卡片上执行「合并」后可见。）";
</script>

<template>
  <a-modal
    v-model:open="open"
    title="合并日志"
    width="min(1200px, 90vw)"
    :footer="null"
    destroy-on-close
  >
    <a-spin :spinning="loading" tip="加载中…">
      <pre class="train-merge-log-pre train-merge-log-pre--modal">{{ text || emptyHint }}</pre>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.train-merge-log-pre {
  max-height: 600px;
  overflow: auto;
  font-size: 12px;
  background: #0d1117;
  color: #e6edf3;
  padding: 8px;
  border-radius: 4px;
  white-space: pre-wrap;
  margin: 0;
}
.train-merge-log-pre--modal {
  height: 600px;
}
</style>
