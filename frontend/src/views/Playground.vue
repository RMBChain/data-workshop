<script setup lang="ts">
import { message } from "ant-design-vue";
import { onMounted, ref } from "vue";
import { http } from "../api/http";

const models = ref<{ id: string; label: string; path: string; kind: string }[]>([]);
const modelId = ref<string | null>(null);
const base = ref("Qwen/Qwen3-VL-2B-Instruct");
const adapter = ref("");
const importTaskId = ref("");
const imagePath = ref("");
const prompt = ref("请描述图片中的内容。");
const maxNew = ref(256);
const loading = ref(false);
const reply = ref("");

onMounted(() => {
  void loadModels();
});

async function loadModels() {
  const r = await http.get("/api/inference/models");
  models.value = r.data.items;
  if (models.value.length) modelId.value = models.value[0].id;
}

function applyModel() {
  if (!modelId.value) return;
  const m = modelId.value;
  if (m.startsWith("lora:")) {
    base.value = "Qwen/Qwen3-VL-2B-Instruct";
    adapter.value = m.slice(5);
  } else if (m.startsWith("merged:")) {
    base.value = m.slice(7);
    adapter.value = "";
  }
  message.info("已根据选项填写基座/适配器，请再确认后发送。");
}

async function send() {
  loading.value = true;
  reply.value = "";
  try {
    const r = await http.post("/api/inference/chat", {
      base_model: base.value,
      adapter_path: adapter.value || null,
      model_id: modelId.value,
      prompt: prompt.value,
      max_new_tokens: maxNew.value,
      image_workspace_path: imagePath.value || null,
      import_task_id: importTaskId.value || null,
    });
    reply.value = r.data.text;
    message.success("完成");
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "失败");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div>
    <a-typography-title :level="4">推理沙盒</a-typography-title>
    <a-alert
      type="warning"
      show-icon
      message="图片来源仅限工作区内已登记路径，或与导入任务关联的本地路径。不支持本机随意上传、外部 URL 贴图。"
      style="margin-bottom: 12px"
    />
    <a-row :gutter="16">
      <a-col :span="10">
        <a-form layout="vertical">
          <a-form-item label="已注册模型/适配器">
            <a-select
              v-model:value="modelId"
              :options="models.map((m) => ({ value: m.id, label: m.label }))"
              show-search
              allow-clear
              style="width: 100%"
            />
            <a-button type="link" @click="applyModel">将选项写入下方字段</a-button>
          </a-form-item>
          <a-form-item label="基座模型/合并目录">
            <a-input v-model:value="base" />
          </a-form-item>
          <a-form-item label="LoRA 相对路径（可空）">
            <a-input v-model:value="adapter" />
          </a-form-item>
          <a-form-item label="导入任务行 ID（与导入页 tasks 的 id 一致，二选一）">
            <a-input v-model:value="importTaskId" />
          </a-form-item>
          <a-form-item label="或：图片工作区相对路径">
            <a-input v-model:value="imagePath" placeholder="例如 data/cable-010.png" />
          </a-form-item>
          <a-form-item label="提示词">
            <a-textarea v-model:value="prompt" :rows="4" />
          </a-form-item>
          <a-form-item label="max_new_tokens">
            <a-input-number v-model:value="maxNew" :min="8" :max="4096" style="width: 100%" />
          </a-form-item>
          <a-button type="primary" :loading="loading" @click="send">发送</a-button>
        </a-form>
      </a-col>
      <a-col :span="14">
        <a-typography-title :level="5">回答</a-typography-title>
        <a-textarea v-model:value="reply" :rows="18" readonly placeholder="多模态输出将显示在这里" />
      </a-col>
    </a-row>
  </div>
</template>
