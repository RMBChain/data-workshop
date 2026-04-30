<script setup lang="ts">
const open = defineModel<boolean>("open", { required: true });
const value = defineModel<string>("value", { required: true });

defineProps<{
  saving: boolean;
}>();

const emit = defineEmits<{ save: [] }>();
</script>

<template>
  <a-modal
    v-model:open="open"
    title="编辑任务名称"
    ok-text="保存"
    cancel-text="取消"
    :confirm-loading="saving"
    destroy-on-close
    @ok="emit('save')"
  >
    <a-input
      v-model:value="value"
      placeholder="训练任务显示名称"
      allow-clear
      @press-enter="emit('save')"
    />
  </a-modal>
</template>
