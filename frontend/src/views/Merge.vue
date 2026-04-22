<script setup lang="ts">
import { message } from "ant-design-vue";
import { ref } from "vue";
import { useRouter } from "vue-router";
import { http } from "../api/http";

const router = useRouter();
const base = ref("Qwen/Qwen3-VL-2B-Instruct");
const loras = ref("output/qwen3vl-2b-lora/adapter");
const output = ref("output/merged-workshop");
const log = ref("");
const jobId = ref<string | null>(null);
let poller: ReturnType<typeof setInterval> | null = null;

async function run() {
  const paths = loras.value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  const r = await http.post("/api/merge/jobs", {
    base_model_path: base.value,
    lora_paths: paths,
    output_path: output.value,
  });
  jobId.value = r.data.id;
  if (poller) clearInterval(poller);
  poller = setInterval(async () => {
    if (!jobId.value) return;
    const s = await http.get(`/api/merge/jobs/${jobId.value}`);
    const t = await http.get(`/api/merge/jobs/${jobId.value}/logs`);
    log.value = t.data.text;
    if (["succeeded", "failed", "cancelled"].includes(String(s.data.status))) {
      if (poller) clearInterval(poller);
      if (s.data.status === "succeeded") {
        message.success("合并完成");
        await http.post(`/api/merge/jobs/${jobId.value}/validate`);
      }
    }
  }, 1500);
}

function goPlay() {
  void router.push("/playground");
}
</script>

<template>
  <div>
    <a-typography-title :level="4">LoRA 合并</a-typography-title>
    <a-alert
      type="info"
      show-icon
      message="合并任务在子进程中执行。多路 LoRA 时，当前实现仅对第一个有效路径做 merge（与后端约定一致）。"
      style="margin-bottom: 12px"
    />
    <a-form layout="vertical" style="max-width: 600px">
      <a-form-item label="基座（ModelScope id 或本地/工作区相对路径）">
        <a-input v-model:value="base" />
      </a-form-item>
      <a-form-item label="LoRA 路径（可换行多个，仅第一个会参与）">
        <a-textarea v-model:value="loras" :rows="3" />
      </a-form-item>
      <a-form-item label="输出目录（工作区相对）">
        <a-input v-model:value="output" />
      </a-form-item>
      <a-button type="primary" @click="run">执行合并</a-button>
      <a-button type="link" @click="goPlay">去推理试跑</a-button>
    </a-form>
    <a-typography-title :level="5" style="margin-top: 16px">日志</a-typography-title>
    <pre
      style="
        max-height: 360px;
        overflow: auto;
        font-size: 12px;
        background: #0d1117;
        color: #e6edf3;
        padding: 8px;
        border-radius: 4px;
        white-space: pre-wrap;
      "
      >{{ log }}</pre
    >
  </div>
</template>
