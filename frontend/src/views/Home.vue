<script setup lang="ts">
import { RouterLink } from "vue-router";
import DataImportGlobalPreview from "../components/DataImportGlobalPreview.vue";

type HomeFlowTo = "/importData" | "/train" | "/playground" | "/merge" | "/eval" | "/export";

type HomeFlowStep = {
  key: string;
  label: string;
  to: HomeFlowTo;
  /** 流程节点下方可选说明 */
  desc?: string;
};

const pipelineSteps: HomeFlowStep[] = [
  { key: "import-data",  label: "数据集", to: "/importData", desc: "拉取标注并生成数据集" },
  { key: "train", label: "微调", to: "/train", desc: "配置并启动微调或全量训练任务" },
  { key: "verify", label: "验证", to: "/train", desc: "在线试跑模型输出，快速验证效果" },
  { key: "merge", label: "合并", to: "/train", desc: "将 LoRA 权重与基座模型合并" },
  { key: "eval", label: "评测", to: "/train", desc: "在固定集上评估模型指标与样例" },
  { key: "export", label: "导出", to: "/train", desc: "打包模型以便部署或分发" },
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
            <p v-if="step.desc" class="home-flow__desc">{{ step.desc }}</p>
          </div>
        </template>
      </div>
    </div>

    <DataImportGlobalPreview />
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
.home-flow__desc {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.45;
  color: rgba(0, 0, 0, 0.45);
  text-align: center;
}
</style>
