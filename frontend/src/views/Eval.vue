<script setup lang="ts">
import { message } from "ant-design-vue";
import { ref } from "vue";
import { http } from "../api/http";

const dataPath = ref("data/val.jsonl");
const acc = ref(true);
const bleu = ref(true);
const rouge = ref(true);
const job = ref<Record<string, unknown> | null>(null);
const items = ref<Record<string, unknown>[]>([]);
let t: ReturnType<typeof setInterval> | null = null;

async function start() {
  const r = await http.post("/api/eval/jobs", {
    data_jsonl: dataPath.value,
    enable_accuracy: acc.value,
    enable_bleu: bleu.value,
    enable_rouge: rouge.value,
  });
  const jid = r.data.job_id as string;
  job.value = { id: jid, status: "pending" };
  if (t) clearInterval(t);
  t = setInterval(async () => {
    const s = await http.get(`/api/eval/jobs/${jid}`);
    job.value = s.data;
    if (s.data.status === "succeeded") {
      if (t) clearInterval(t);
      const it = await http.get(`/api/eval/jobs/${jid}/items`, { params: { page: 1, page_size: 50 } });
      items.value = it.data.items;
      message.success("评测完成");
    }
  }, 1000);
}
</script>

<template>
  <div>
    <a-typography-title :level="4">评测</a-typography-title>
    <a-alert
      type="info"
      show-icon
      message="MVP 仅生成逐条结果占位与指标壳；可后续接入与标注对齐的计分逻辑。"
      style="margin-bottom: 12px"
    />
    <a-form layout="vertical" style="max-width: 480px">
      <a-form-item label="数据 JSONL（工作区相对）">
        <a-input v-model:value="dataPath" />
      </a-form-item>
      <a-form-item label="指标">
        <a-checkbox v-model:checked="acc">准确率</a-checkbox>
        <a-checkbox v-model:checked="bleu">BLEU</a-checkbox>
        <a-checkbox v-model:checked="rouge">ROUGE</a-checkbox>
      </a-form-item>
      <a-button type="primary" @click="start">开始评测</a-button>
    </a-form>
    <a-typography-paragraph v-if="job" style="margin-top: 12px"
      >状态：{{ String((job as { status?: string }).status) }} 概览：{{
        JSON.stringify((job as { summary?: unknown }).summary)
      }}</a-typography-paragraph
    >
    <a-table
      v-if="items.length"
      :columns="[
        { title: '序号', dataIndex: 'index', key: 'i', width: 60 },
        { title: '说明', dataIndex: 'message', key: 'm' },
      ]"
      :data-source="items as Record<string, unknown>[]"
      :pagination="false"
      size="small"
      row-key="index"
    />
  </div>
</template>
