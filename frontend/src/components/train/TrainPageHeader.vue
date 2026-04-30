<script setup lang="ts">
import { PlusOutlined } from "@ant-design/icons-vue";

const emit = defineEmits<{ newTrain: [] }>();
</script>

<template>
  <div class="datasets-page-header">
    <div class="datasets-page-header__title-row">
      <a-typography-title :level="4">微调</a-typography-title>
      <a-tooltip title="新建训练" placement="bottom">
        <a-button
          type="text"
          class="datasets-header-add-btn"
          aria-label="新建训练"
          @click="emit('newTrain')"
        >
          <template #icon>
            <PlusOutlined />
          </template>
        </a-button>
      </a-tooltip>
    </div>
  </div>
</template>

<style scoped>
.datasets-header-add-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  font-size: 18px;
  color: rgba(0, 0, 0, 0.45);
}
.datasets-header-add-btn:hover {
  color: var(--ant-primary-color, #1677ff);
}
.datasets-page-header__title-row {
  display: inline-flex;
  align-items: center;
  gap: 0;
  max-width: 100%;
  min-width: 0;
}
.datasets-page-header__title-row :deep(h4) {
  margin: 0;
  padding: 0;
  line-height: 1.35;
}
.datasets-page-header {
  margin-bottom: 12px;
}
</style>
