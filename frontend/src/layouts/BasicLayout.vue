<script setup lang="ts">
import {
  BarChartOutlined,
  BranchesOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  ExportOutlined,
  FileSearchOutlined,
  MessageOutlined,
  RocketOutlined,
  SettingOutlined,
} from "@ant-design/icons-vue";
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";

const isDev = computed(() => import.meta.env.DEV);
const route = useRoute();
const router = useRouter();

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
      <span>数据工坊</span>
      <span style="font-size: 14px; opacity: 0.9">
        <a-tag v-if="isDev" color="blue">环境：开发</a-tag>
        <a-tag v-else color="green">环境：生产</a-tag>
      </span>
    </a-layout-header>
    <a-layout>
      <a-layout-sider width="200" theme="light">
        <a-menu :selected-keys="[route.path === '/' ? '/' : route.path]" mode="inline" @click="onMenuClick">
          <a-menu-item key="/import">
            <template #icon><CloudUploadOutlined /></template>
            数据导入
          </a-menu-item>
          <a-menu-item key="/import-data">
            <template #icon><FileSearchOutlined /></template>
            数据查看
          </a-menu-item>
          <a-menu-item key="/datasets">
            <template #icon><DatabaseOutlined /></template>
            数据集
          </a-menu-item>
          <a-menu-item key="/train">
            <template #icon><RocketOutlined /></template>
            训练
          </a-menu-item>
          <a-menu-item key="/playground">
            <template #icon><MessageOutlined /></template>
            推理沙盒
          </a-menu-item>
          <a-menu-item key="/merge">
            <template #icon><BranchesOutlined /></template>
            合并
          </a-menu-item>
          <a-menu-item key="/eval">
            <template #icon><BarChartOutlined /></template>
            评测
          </a-menu-item>
          <a-menu-item key="/export">
            <template #icon><ExportOutlined /></template>
            导出
          </a-menu-item>
          <a-menu-item key="/">
            <template #icon><SettingOutlined /></template>
            设置
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
