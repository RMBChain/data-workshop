<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { http } from "../api/http";

const systemLoading = ref(false);
const systemData = ref<Record<string, unknown>>({});
const pathsData = ref<Record<string, unknown>>({});
const resData = ref<Record<string, unknown>>({});
let resTimer: ReturnType<typeof setInterval> | null = null;

async function load() {
  systemLoading.value = true;
  try {
    const [a, b, c] = await Promise.all([
      http.get("/api/system/info"),
      http.get("/api/system/paths"),
      http.get("/api/system/resources"),
    ]);
    systemData.value = a.data;
    pathsData.value = b.data;
    resData.value = c.data;
  } catch {
    systemData.value = { error: "无法加载" };
  } finally {
    systemLoading.value = false;
  }
  if (resTimer) {
    clearInterval(resTimer);
    resTimer = null;
  }
  resTimer = setInterval(async () => {
    try {
      const r = await http.get("/api/system/resources");
      resData.value = r.data;
    } catch {
      /* ignore */
    }
  }, 2000);
}

onMounted(() => {
  void load();
});
onUnmounted(() => {
  if (resTimer) {
    clearInterval(resTimer);
    resTimer = null;
  }
});
</script>

<template>
  <a-spin :spinning="systemLoading">
    <a-typography-paragraph v-if="(systemData as { gpu_note?: string }).gpu_note" type="secondary">
      {{ (systemData as { gpu_note?: string }).gpu_note }}
    </a-typography-paragraph>
    <a-descriptions bordered size="small" :column="1">
      <a-descriptions-item label="Python">
        {{ (systemData as { python?: string }).python }}
      </a-descriptions-item>
      <a-descriptions-item label="平台">
        {{ (systemData as { platform?: string }).platform }}
      </a-descriptions-item>
      <a-descriptions-item label="PyTorch">
        {{ (systemData as { torch?: string }).torch }} · CUDA 可见：{{
          (systemData as { torch_cuda_available?: boolean }).torch_cuda_available ? "是" : "否"
        }}
      </a-descriptions-item>
    </a-descriptions>
    <a-typography-title :level="5" style="margin-top: 16px">约定路径</a-typography-title>
    <a-typography-paragraph
      v-for="(v, k) in pathsData as Record<string, string>"
      :key="k"
      copyable
      >{{ k }}：{{ v }}</a-typography-paragraph
    >
    <a-typography-title :level="5" style="margin-top: 16px">资源（约 2s 刷新）</a-typography-title>
    <pre v-if="(resData as { note?: string }).note" style="color: #888; font-size: 12px">{{
      (resData as { note?: string }).note
    }}</pre>
    <pre v-else style="font-size: 12px; white-space: pre-wrap">{{ JSON.stringify(resData, null, 2) }}</pre>
  </a-spin>
</template>
