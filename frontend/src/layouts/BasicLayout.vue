<script setup lang="ts">
import { ref, computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { http } from "../api/http";

const isDev = computed(() => import.meta.env.DEV);

const route = useRoute();
const router = useRouter();
const systemOpen = ref(false);
const systemLoading = ref(false);
const systemData = ref<Record<string, unknown>>({});
const pathsData = ref<Record<string, unknown>>({});
const resData = ref<Record<string, unknown>>({});
let resTimer: ReturnType<typeof setInterval> | null = null;

function onMenuClick(info: { key: string | number }) {
  router.push(String(info.key));
}

async function openSystem() {
  systemOpen.value = true;
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
  if (resTimer) clearInterval(resTimer);
  resTimer = setInterval(async () => {
    if (!systemOpen.value) return;
    try {
      const r = await http.get("/api/system/resources");
      resData.value = r.data;
    } catch {
      /* ignore */
    }
  }, 2000);
}

function onCloseSystem() {
  if (resTimer) {
    clearInterval(resTimer);
    resTimer = null;
  }
}
</script>

<template>
  <a-layout style="min-height: 100vh">
    <a-layout-header
      style="
        color: #fff;
        font-size: 18px;
        line-height: 64px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0 24px;
        background: #001529;
      "
    >
      <span>数据工坊</span>
      <span style="font-size: 14px; opacity: 0.9">
        <a-tag v-if="isDev" color="blue">环境：开发</a-tag>
        <a-tag v-else color="green">环境：生产</a-tag>
        <a-button type="link" style="color: #fff" @click="openSystem">系统信息</a-button>
      </span>
    </a-layout-header>
    <a-layout>
      <a-layout-sider width="200" theme="light">
        <a-menu :selected-keys="[route.path]" mode="inline" @click="onMenuClick">
          <a-menu-item key="/import">数据导入</a-menu-item>
          <a-menu-item key="/datasets">数据集</a-menu-item>
          <a-menu-item key="/train">训练</a-menu-item>
          <a-menu-item key="/playground">推理沙盒</a-menu-item>
          <a-menu-item key="/merge">合并</a-menu-item>
          <a-menu-item key="/eval">评测</a-menu-item>
          <a-menu-item key="/export">导出</a-menu-item>
        </a-menu>
      </a-layout-sider>
      <a-layout-content style="padding: 24px; background: #f5f5f5; min-height: 280px">
        <a-card :bordered="false">
          <router-view />
        </a-card>
      </a-layout-content>
    </a-layout>
  </a-layout>
  <a-drawer
    v-model:open="systemOpen"
    title="系统信息"
    placement="right"
    width="420"
    @close="onCloseSystem"
  >
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
      <pre v-else style="font-size: 12px; white-space: pre-wrap">{{
        JSON.stringify(resData, null, 2)
      }}</pre>
    </a-spin>
  </a-drawer>
</template>
