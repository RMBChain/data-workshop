<script setup lang="ts">
import { message } from "ant-design-vue";
import { ref } from "vue";
import { http } from "../api/http";

const baseModel = ref("Qwen/Qwen3-VL-2B-Instruct");
const adapterPath = ref("");
const prompt = ref("请描述图片中的内容。");
const maxNewTokens = ref(256);
const file = ref<File | null>(null);
const loading = ref(false);
const result = ref("");

function onFile(e: Event) {
  const t = e.target as HTMLInputElement;
  file.value = t.files?.[0] ?? null;
}

async function run() {
  const f = file.value;
  if (!f) {
    message.warning("请先选择图片");
    return;
  }
  loading.value = true;
  result.value = "";
  const fd = new FormData();
  fd.append("prompt", prompt.value);
  fd.append("base_model", baseModel.value);
  fd.append("max_new_tokens", String(maxNewTokens.value));
  if (adapterPath.value.trim()) {
    fd.append("adapter_path", adapterPath.value.trim());
  }
  fd.append("image", f);
  try {
    const r = await http.post<{ text: string; image_path: string }>("/api/inference/chat", fd);
    result.value = r.data.text;
    message.success("完成");
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? String(e));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div>
    <a-typography-title :level="4">推理沙盒</a-typography-title>
    <a-typography-paragraph>首次加载模型可能较慢（CPU）。LoRA 目录为相对仓库根的路径，可留空使用基座模型。</a-typography-paragraph>

    <a-row :gutter="24">
      <a-col :xs="24" :lg="10">
        <a-form layout="vertical">
          <a-form-item label="基座模型">
            <a-input v-model:value="baseModel" />
          </a-form-item>
          <a-form-item label="LoRA 目录（可选）">
            <a-input v-model:value="adapterPath" placeholder="例如 output/qwen3vl-2b-lora/v0-xxx/checkpoint-3" />
          </a-form-item>
          <a-form-item label="提示词">
            <a-textarea v-model:value="prompt" :rows="4" />
          </a-form-item>
          <a-form-item label="max_new_tokens">
            <a-input-number v-model:value="maxNewTokens" :min="8" :max="4096" style="width: 100%" />
          </a-form-item>
          <a-form-item label="图片">
            <input type="file" accept="image/*" @change="onFile" />
          </a-form-item>
          <a-button type="primary" :loading="loading" @click="run">开始推理</a-button>
        </a-form>
      </a-col>
      <a-col :xs="24" :lg="14">
        <a-typography-title :level="5">输出</a-typography-title>
        <a-textarea :value="result" :rows="18" readonly placeholder="推理结果将显示在这里" />
      </a-col>
    </a-row>
  </div>
</template>
