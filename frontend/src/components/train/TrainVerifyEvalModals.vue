<script setup lang="ts">
import Eval from "../../views/Eval.vue";
import Verify from "../../views/Verify.vue";

const verifyOpen = defineModel<boolean>("verifyOpen", { required: true });
const evalOpen = defineModel<boolean>("evalOpen", { required: true });

defineProps<{
  verifyPrefillJobId: string | null;
  evalPrefillJobId: string | null;
}>();

const emit = defineEmits<{
  verifyClosed: [];
  evalClosed: [];
}>();

const modalBody = { maxHeight: "calc(100vh - 140px)", overflow: "auto", paddingTop: "12px" };
</script>

<template>
  <a-modal
    v-model:open="verifyOpen"
    title="LoRA 验证"
    width="min(1200px, 96vw)"
    :footer="null"
    destroy-on-close
    :body-style="modalBody"
    @cancel="emit('verifyClosed')"
  >
    <Verify embedded :prefill-job-id="verifyPrefillJobId" />
  </a-modal>

  <a-modal
    v-model:open="evalOpen"
    title="评测"
    width="min(1200px, 96vw)"
    :footer="null"
    destroy-on-close
    :body-style="modalBody"
    @cancel="emit('evalClosed')"
  >
    <Eval embedded :prefill-job-id="evalPrefillJobId" />
  </a-modal>
</template>
