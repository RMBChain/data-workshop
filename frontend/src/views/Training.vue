<script setup lang="ts">
import { message } from "ant-design-vue";
import { onUnmounted, reactive, ref } from "vue";
import { http } from "../api/http";

const form = reactive({
  model: "Qwen/Qwen3-VL-2B-Instruct",
  train_dataset: "data/train.jsonl",
  val_dataset: "data/val.jsonl",
  output_dir: "output/qwen3vl-2b-lora",
  lora_rank: 8,
  lora_alpha: 16,
  num_train_epochs: 3,
  per_device_train_batch_size: 1,
  gradient_accumulation_steps: 8,
  learning_rate: 0.0001,
  max_length: 1024,
});

const submitting = ref(false);
const currentJobId = ref<string | null>(null);
const logText = ref("");
const jobStatus = ref<string>("");

let pollTimer: ReturnType<typeof setInterval> | null = null;

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function refreshLogs() {
  if (!currentJobId.value) return;
  try {
    const st = await http.get(`/api/training/jobs/${currentJobId.value}`);
    jobStatus.value = st.data.status;
    const logs = await http.get<{ text: string; truncated: boolean }>(
      `/api/training/jobs/${currentJobId.value}/logs`,
    );
    logText.value = logs.data.truncated ? `…（仅显示末尾）\n${logs.data.text}` : logs.data.text;
    if (["completed", "failed", "cancelled"].includes(st.data.status)) {
      stopPoll();
    }
  } catch {
    stopPoll();
  }
}

async function startTraining() {
  submitting.value = true;
  try {
    const r = await http.post<{ id: string; status: string }>("/api/training/jobs", { ...form });
    currentJobId.value = r.data.id;
    jobStatus.value = r.data.status;
    message.success(`任务已创建：${r.data.id}`);
    stopPoll();
    pollTimer = setInterval(refreshLogs, 2000);
    await refreshLogs();
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? String(e));
  } finally {
    submitting.value = false;
  }
}

async function cancelJob() {
  if (!currentJobId.value) return;
  try {
    await http.post(`/api/training/jobs/${currentJobId.value}/cancel`);
    message.success("已请求取消");
    await refreshLogs();
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? String(e));
  }
}

onUnmounted(() => stopPoll());
</script>

<template>
  <div>
    <a-typography-title :level="4">训练任务</a-typography-title>
    <a-typography-paragraph>在容器内通过 <code>backend/scripts/train.py</code> 调用 ms-swift（CPU）。日志见下方。</a-typography-paragraph>

    <a-row :gutter="24">
      <a-col :xs="24" :lg="10">
        <a-form layout="vertical">
          <a-form-item label="模型">
            <a-input v-model:value="form.model" />
          </a-form-item>
          <a-form-item label="训练集">
            <a-input v-model:value="form.train_dataset" />
          </a-form-item>
          <a-form-item label="验证集">
            <a-input v-model:value="form.val_dataset" />
          </a-form-item>
          <a-form-item label="输出目录">
            <a-input v-model:value="form.output_dir" />
          </a-form-item>
          <a-form-item label="LoRA rank">
            <a-input-number v-model:value="form.lora_rank" :min="1" :max="128" style="width: 100%" />
          </a-form-item>
          <a-form-item label="LoRA alpha">
            <a-input-number v-model:value="form.lora_alpha" :min="1" :max="256" style="width: 100%" />
          </a-form-item>
          <a-form-item label="Epochs">
            <a-input-number v-model:value="form.num_train_epochs" :min="1" :max="100" style="width: 100%" />
          </a-form-item>
          <a-form-item label="Batch size">
            <a-input-number v-model:value="form.per_device_train_batch_size" :min="1" :max="16" style="width: 100%" />
          </a-form-item>
          <a-form-item label="梯度累积">
            <a-input-number v-model:value="form.gradient_accumulation_steps" :min="1" :max="128" style="width: 100%" />
          </a-form-item>
          <a-form-item label="学习率">
            <a-input-number v-model:value="form.learning_rate" :min="1e-6" :max="1e-2" :step="0.00001" style="width: 100%" />
          </a-form-item>
          <a-form-item label="max_length">
            <a-input-number v-model:value="form.max_length" :min="128" :max="8192" style="width: 100%" />
          </a-form-item>
          <a-space>
            <a-button type="primary" :loading="submitting" @click="startTraining">启动训练</a-button>
            <a-button danger :disabled="!currentJobId" @click="cancelJob">取消当前任务</a-button>
          </a-space>
        </a-form>
      </a-col>
      <a-col :xs="24" :lg="14">
        <div style="margin-bottom: 8px">
          <strong>任务状态</strong>：
          <a-tag v-if="jobStatus">{{ jobStatus }}</a-tag>
          <span v-if="currentJobId" style="margin-left: 8px; color: #666">{{ currentJobId }}</span>
        </div>
        <a-textarea :value="logText" :rows="28" readonly style="font-family: ui-monospace, monospace; font-size: 12px" />
      </a-col>
    </a-row>
  </div>
</template>
