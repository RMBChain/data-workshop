<script setup lang="ts">
import { RouterLink } from "vue-router";

const pipelineSteps = [
  { key: "import", label: "导入", to: "/import" as const },
  { key: "datasets", label: "数据集", to: "/datasets" as const },
  { key: "train", label: "LoRA 训练", to: "/train" as const },
  { key: "playground", label: "LoRA 验证", to: "/playground" as const },
  { key: "merge", label: "LoRA 合并", to: "/merge" as const },
  { key: "eval", label: "评测", to: "/eval" as const },  
  { key: "export", label: "导出", to: "/export" as const },
] as const;

const features: { path: string; title: string; desc: string }[] = [
  { path: "/import", title: "数据导入", desc: "上传或接入原始数据，进入后续处理流程。" },
  { path: "/import-data", title: "数据查看", desc: "浏览、检索已导入的数据与字段。" },
  { path: "/datasets", title: "数据集", desc: "管理训练/评测用数据集与划分。" },
  { path: "/train", title: "LoRA 训练", desc: "配置并启动微调或全量训练任务。" },
  { path: "/playground", title: "LoRA 验证", desc: "在线试跑模型输出，快速验证效果。" },
  { path: "/merge", title: "LoRA 合并", desc: "将 LoRA 权重与基座模型合并导出。" },
  { path: "/eval", title: "评测", desc: "在固定集上评估模型指标与样例。" },
  { path: "/export", title: "导出", desc: "打包模型与相关产物以便部署或分发。" },
];
</script>

<template>
  <div>
    <a-typography-title :level="3" style="margin-top: 0">欢迎使用数据工坊</a-typography-title>
    <a-typography-paragraph>
      数据工坊面向大模型数据与训练流程，提供一站式 Web 能力。请确保后端服务已启动，以便各页面正常调用接口。
    </a-typography-paragraph>
    <div class="home-flow" role="region" aria-label="典型数据处理链路">
      <div class="home-flow__track">
        <template v-for="(step, i) in pipelineSteps" :key="step.key">
          <span v-if="i > 0" class="home-flow__arrow" aria-hidden="true">→</span>
          <div class="home-flow__node">
            <RouterLink class="home-flow__link-main" :to="step.to">{{ step.label }}</RouterLink>
          </div>
        </template>
      </div>
    </div>

    <a-typography-title :level="4">功能一览</a-typography-title>
    <a-row :gutter="[16, 16]" style="margin-bottom: 24px">
      <a-col v-for="item in features" :key="item.path" :xs="24" :sm="12" :md="6">
        <a-card size="small" hoverable>
          <template #title>
            <RouterLink :to="item.path">{{ item.title }}</RouterLink>
          </template>
          <a-typography-paragraph type="secondary" style="margin-bottom: 0">{{ item.desc }}</a-typography-paragraph>
        </a-card>
      </a-col>
    </a-row>

    <a-typography-title :level="4">推荐流程</a-typography-title>
    <a-typography-paragraph type="secondary" style="margin-bottom: 8px">
      可按实际需求跳过某些步骤；合并与导出通常在得到满意 checkpoint 后进行。
    </a-typography-paragraph>
    <a-typography-paragraph>
      <ol style="margin: 0; padding-left: 20px">
        <li>
          <RouterLink to="/import">数据导入</RouterLink>
          与
          <RouterLink to="/import-data">数据查看</RouterLink>
          —— 准备与检查数据
        </li>
        <li><RouterLink to="/datasets">数据集</RouterLink> —— 构建训练/评测数据</li>
        <li><RouterLink to="/train">训练</RouterLink> —— 启动训练并关注日志与产物</li>
        <li><RouterLink to="/playground">推理沙盒</RouterLink> 与 <RouterLink to="/eval">评测</RouterLink> —— 验证效果</li>
        <li>
          若使用 LoRA：<RouterLink to="/merge">LoRA 合并</RouterLink>，再通过
          <RouterLink to="/export">导出</RouterLink>
          交付
        </li>
        <li><RouterLink to="/models">模型管理</RouterLink> —— 统一管理模型与路径</li>
      </ol>
    </a-typography-paragraph>
  </div>
</template>

<style scoped>
.home-flow {
  margin-bottom: 24px;
  padding: 16px 20px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
}
.home-flow__track {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 4px 0;
}
.home-flow__arrow {
  flex-shrink: 0;
  color: rgba(0, 0, 0, 0.25);
  font-size: 18px;
  line-height: 1;
  padding: 0 6px;
  user-select: none;
}
.home-flow__node {
  flex: 1 1 100px;
  min-width: 88px;
  max-width: 220px;
  padding: 12px 14px;
  background: #fff;
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  text-align: center;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.home-flow__link-main {
  font-weight: 600;
  font-size: 14px;
  color: #1677ff;
}
.home-flow__link-main:hover {
  color: #4096ff;
}
</style>
