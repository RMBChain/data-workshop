<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import SystemInfoPanel from "../components/SystemInfoPanel.vue";
import ModelManagement from "./ModelManagement.vue";

const activeKey = ref("system");
const route = useRoute();
const router = useRouter();

const validTabs = new Set(["system", "models"]);

function syncFromRoute() {
  const raw = route.query.tab;
  const q = raw == null || Array.isArray(raw) ? "system" : String(raw);
  if (validTabs.has(q)) {
    activeKey.value = q;
  } else {
    activeKey.value = "system";
  }
}

onMounted(() => {
  syncFromRoute();
});

watch(
  () => route.query.tab,
  () => {
    if (route.name === "home" || route.path === "/") {
      syncFromRoute();
    }
  },
);

watch(activeKey, (k) => {
  if (route.path !== "/") {
    return;
  }
  const cur = route.query.tab;
  const curS = cur == null || Array.isArray(cur) ? "" : String(cur);
  if (curS === k) {
    return;
  }
  router.replace({ path: "/", query: { ...route.query, tab: k } });
});
</script>

<template>
  <a-tabs v-model:activeKey="activeKey" type="line" style="margin-top: 0">
    <a-tab-pane key="system" tab="系统信息">
      <SystemInfoPanel v-if="activeKey === 'system'" />
    </a-tab-pane>
    <a-tab-pane key="models" tab="模型管理">
      <ModelManagement v-if="activeKey === 'models'" />
    </a-tab-pane>
  </a-tabs>
</template>
