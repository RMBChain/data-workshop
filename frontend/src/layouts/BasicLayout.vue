<script setup lang="ts">
import { useRoute, useRouter } from "vue-router";

const route = useRoute();
const router = useRouter();

function onMenuClick(info: { key: string | number }) {
  router.push(String(info.key));
}
</script>

<template>
  <a-layout style="min-height: 100vh">
    <a-layout-header style="color: #fff; font-size: 18px; line-height: 64px">数据工坊 · 图片多模态</a-layout-header>
    <a-layout>
      <a-layout-sider width="200" theme="light">
        <a-menu :selected-keys="[route.path]" mode="inline" @click="onMenuClick">
          <a-menu-item key="/data">数据导入</a-menu-item>
          <a-menu-item key="/training">训练</a-menu-item>
          <a-menu-item key="/inference">推理沙盒</a-menu-item>
        </a-menu>
      </a-layout-sider>
      <a-layout-content style="padding: 24px; background: #fff">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>
