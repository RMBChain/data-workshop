<script setup lang="ts">
import {
  BarChartOutlined,
  BranchesOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  BlockOutlined,
  FileSearchOutlined,
  HomeOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  RocketOutlined,
  SettingOutlined,
} from "@ant-design/icons-vue";
import { computed, provide, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import ResourceInfoFloating from "../components/ResourceInfoFloating.vue";

const isDev = computed(() => import.meta.env.DEV);
const route = useRoute();
const router = useRouter();
const menuCollapsed = ref(false);
const resourceInfoOpen = ref(false);

provide("workshop:resourceInfoFloatingOpen", resourceInfoOpen);

function onMenuClick(info: { key: string | number }) {
  router.push(String(info.key));
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
      <div style="display: flex; align-items: center; gap: 4px; min-width: 0; flex: 1">
        <a-tooltip :title="menuCollapsed ? '展开侧栏' : '收起侧栏'">
          <a-button
            type="text"
            :aria-label="menuCollapsed ? '展开侧栏' : '收起侧栏'"
            style="color: #fff; width: 40px; height: 40px; flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center"
            @click="menuCollapsed = !menuCollapsed"
          >
            <MenuUnfoldOutlined v-if="menuCollapsed" :style="{ fontSize: '18px' }" />
            <MenuFoldOutlined v-else :style="{ fontSize: '18px' }" />
          </a-button>
        </a-tooltip>
        <span style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis">数据工坊</span>
      </div>
      <div style="display: flex; align-items: center; gap: 16px; flex-shrink: 0">
        <a
          href="#"
          style="font-size: 14px; color: rgba(255, 255, 255, 0.88); text-decoration: none; white-space: nowrap"
          @click.prevent="resourceInfoOpen = true"
          >资源信息</a
        >
        <span style="font-size: 14px; opacity: 0.9">
          <a-tag v-if="isDev" color="blue">环境：开发</a-tag>
          <a-tag v-else color="green">环境：生产</a-tag>
        </span>
      </div>
    </a-layout-header>
    <ResourceInfoFloating v-model:open="resourceInfoOpen" />
    <a-layout>
      <a-layout-sider
        v-model:collapsed="menuCollapsed"
        :width="200"
        :collapsed-width="0"
        theme="light"
        collapsible
        :trigger="null"
      >
        <a-menu :selected-keys="[route.path === '/' ? '/' : route.path]" mode="inline" @click="onMenuClick">
          <a-menu-item key="/">
            <template #icon><HomeOutlined /></template>
            HOME
          </a-menu-item>
          <a-menu-item key="/importData">
            <template #icon><CloudUploadOutlined /></template>
            数据导入
          </a-menu-item>
          <a-menu-item key="/batchDataView">
            <template #icon><FileSearchOutlined /></template>
            批次数据查看
          </a-menu-item>
          <a-menu-item key="/datasets">
            <template #icon><DatabaseOutlined /></template>
            数据集
          </a-menu-item>
          <a-menu-item key="/train">
            <template #icon><RocketOutlined /></template>
            LoRA 训练
          </a-menu-item>
          <a-menu-item key="/verify">
            <template #icon><MessageOutlined /></template>
            LoRA 验证
          </a-menu-item>
          <a-menu-item key="/merge">
            <template #icon><BranchesOutlined /></template>
            LoRA 合并
          </a-menu-item>
          <a-menu-item key="/eval">
            <template #icon><BarChartOutlined /></template>
            评测
          </a-menu-item>
          <a-menu-item key="/models">
            <template #icon><BlockOutlined /></template>
            模型管理
          </a-menu-item>
          <a-menu-item key="/system-info">
            <template #icon><SettingOutlined /></template>
            系统信息
          </a-menu-item>
        </a-menu>
      </a-layout-sider>
      <a-layout-content style="padding: 24px; background: #f5f5f5; min-height: 280px">
        <a-card :bordered="false">
          <router-view />
        </a-card>
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>
