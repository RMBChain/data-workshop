<script setup lang="ts">
import { message } from "ant-design-vue";
import * as echarts from "echarts";
import { onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { http } from "../api/http";

const form = reactive({
  model: "Qwen/Qwen3-VL-2B-Instruct",
  train_dataset: "data/train.jsonl",
  val_dataset: "data/val.jsonl",
  output_dir: "output/qwen3vl-2b-lora",
  lora_rank: 8,
  lora_alpha: 16,
  target_modules: "all-linear",
  num_train_epochs: 3,
  per_device_train_batch_size: 1,
  gradient_accumulation_steps: 8,
  learning_rate: 0.0001,
  max_length: 1024,
});

const submitting = ref(false);
const currentJobId = ref<string | null>(null);
const logText = ref("");
const logPre = ref<HTMLPreElement | null>(null);
const jobStatus = ref("");
const stick = ref(true);
const jobs = ref<Record<string, unknown>[]>([]);
let logPoll: ReturnType<typeof setInterval> | null = null;
let resPoll: ReturnType<typeof setInterval> | null = null;
const resInfo = ref("");
const chartRef = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;

const jobColumns = [
  { title: "ID", dataIndex: "id", key: "id", ellipsis: true, width: 200 },
  { title: "状态", dataIndex: "status", key: "status", width: 100 },
  {
    title: "操作",
    key: "act",
    width: 100,
  },
];

onMounted(() => {
  void refreshJobs();
  resPoll = setInterval(async () => {
    try {
      const r = await http.get("/api/system/resources");
      resInfo.value = JSON.stringify(r.data);
    } catch {
      resInfo.value = "";
    }
  }, 2000);
});
onUnmounted(() => {
  stopLogPoll();
  if (resPoll) clearInterval(resPoll);
  if (chart) {
    chart.dispose();
    chart = null;
  }
});

function stopLogPoll() {
  if (logPoll) {
    clearInterval(logPoll);
    logPoll = null;
  }
}

function onLogScroll() {
  const el = logPre.value;
  if (!el) return;
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  stick.value = nearBottom;
}

function scrollToBottom() {
  const el = logPre.value;
  if (el) {
    el.scrollTop = el.scrollHeight;
    stick.value = true;
  }
}

async function applyYaml() {
  const raw = window.prompt("粘贴训练 YAML 到此处");
  if (!raw) return;
  try {
    const r = await http.post("/api/training/config/yaml/parse", { yaml: raw });
    Object.assign(form, r.data.params);
    message.success("已应用 YAML 到表单");
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "解析失败");
  }
}

function downloadYaml() {
  const q = currentJobId.value ? `?job_id=${currentJobId.value}` : "";
  window.open(`/api/training/config/yaml/export${q}`, "_blank");
}

async function refreshJobs() {
  const r = await http.get("/api/training/jobs");
  jobs.value = r.data.items;
}

async function updateChart() {
  if (!currentJobId.value || !chartRef.value) return;
  const m = await http.get(`/api/training/jobs/${currentJobId.value}/metrics`);
  const s = m.data.series as {
    train_loss: { step: number; value: number }[];
    learning_rate: { value: number }[];
  };
  if (!chart) chart = echarts.init(chartRef.value);
  chart.setOption({
    title: { text: "训练指标（自日志解析）" },
    tooltip: { trigger: "axis" },
    xAxis: { type: "value" },
    yAxis: [{ type: "value", name: "loss" }, { type: "value", name: "lr" }],
    series: [
      { name: "train_loss", type: "line", data: s.train_loss?.map((x) => [x.step, x.value]) ?? [] },
      { name: "lr", type: "line", yAxisIndex: 1, data: s.learning_rate?.map((x, i) => [i, x.value]) ?? [] },
    ],
  });
}

async function refreshLogs() {
  if (!currentJobId.value) return;
  const st = await http.get(`/api/training/jobs/${currentJobId.value}`);
  jobStatus.value = String(st.data.status);
  const logs = await http.get<{ text: string; truncated: boolean }>(
    `/api/training/jobs/${currentJobId.value}/logs`,
  );
  logText.value = logs.data.truncated ? `…（仅显示末尾）\n${logs.data.text}` : logs.data.text;
  if (stick.value) {
    requestAnimationFrame(() => {
      if (logPre.value) logPre.value.scrollTop = logPre.value.scrollHeight;
    });
  }
  try {
    await updateChart();
  } catch {
    /* ignore */
  }
  if (["succeeded", "failed", "cancelled"].includes(String(st.data.status))) {
    stopLogPoll();
  }
}

async function startTraining() {
  submitting.value = true;
  try {
    const r = await http.post<{ id: string; status: string }>("/api/training/jobs", { ...form });
    currentJobId.value = r.data.id;
    jobStatus.value = r.data.status;
    message.success(`任务已创建：${r.data.id}`);
    stopLogPoll();
    logPoll = setInterval(() => {
      void refreshLogs();
    }, 1500);
    await refreshLogs();
    await refreshJobs();
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? String(e));
  } finally {
    submitting.value = false;
  }
}

async function cancelJob() {
  if (!currentJobId.value) return;
  await http.post(`/api/training/jobs/${currentJobId.value}/cancel`);
  message.success("已请求取消");
  await refreshLogs();
  await refreshJobs();
}

async function delJob() {
  if (!currentJobId.value) return;
  await http.delete(`/api/training/jobs/${currentJobId.value}`);
  message.success("已删除");
  currentJobId.value = null;
  logText.value = "";
  await refreshJobs();
}

async function retryJob() {
  if (!currentJobId.value) return;
  const r = await http.post(`/api/training/jobs/${currentJobId.value}/retry`);
  currentJobId.value = r.data.id;
  logPoll = setInterval(() => void refreshLogs(), 1500);
  void refreshLogs();
  await refreshJobs();
}

function selectJob(id: string) {
  currentJobId.value = id;
  stopLogPoll();
  logPoll = setInterval(() => void refreshLogs(), 1500);
  void refreshLogs();
}

watch(logText, () => {
  if (stick.value) {
    requestAnimationFrame(() => {
      if (logPre.value) logPre.value.scrollTop = logPre.value.scrollHeight;
    });
  }
});
</script>

<template>
  <div>
    <a-typography-title :level="4">训练</a-typography-title>
    <a-alert
      type="info"
      show-icon
      message="训练与日志解析均在纯 CPU 上执行，速度受本机资源影响。"
      style="margin-bottom: 12px"
    />
    <a-row :gutter="16">
      <a-col :span="8">
        <a-form layout="vertical">
          <a-form-item label="基础模型/路径">
            <a-input v-model:value="form.model" />
          </a-form-item>
          <a-form-item label="训练集 (jsonl)">
            <a-input v-model:value="form.train_dataset" />
          </a-form-item>
          <a-form-item label="验证集 (jsonl)">
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
          <a-form-item label="目标模块">
            <a-input v-model:value="form.target_modules" />
          </a-form-item>
          <a-form-item label="Epochs">
            <a-input-number v-model:value="form.num_train_epochs" :min="1" :max="200" style="width: 100%" />
          </a-form-item>
          <a-form-item label="Batch size">
            <a-input-number
              v-model:value="form.per_device_train_batch_size"
              :min="1"
              :max="16"
              style="width: 100%"
            />
          </a-form-item>
          <a-form-item label="梯度累积">
            <a-input-number
              v-model:value="form.gradient_accumulation_steps"
              :min="1"
              :max="128"
              style="width: 100%"
            />
          </a-form-item>
          <a-form-item label="学习率">
            <a-input-number
              v-model:value="form.learning_rate"
              :min="1e-6"
              :max="1e-2"
              :step="0.00001"
              style="width: 100%"
            />
          </a-form-item>
          <a-form-item label="max_length">
            <a-input-number v-model:value="form.max_length" :min="128" :max="8192" style="width: 100%" />
          </a-form-item>
          <a-space wrap>
            <a-button type="primary" :loading="submitting" @click="startTraining">提交训练</a-button>
            <a-button :disabled="!currentJobId" @click="cancelJob">取消</a-button>
            <a-button :disabled="!currentJobId" @click="delJob">删除记录</a-button>
            <a-button :disabled="!currentJobId" @click="retryJob">重试</a-button>
            <a-button @click="applyYaml">从 YAML 导入</a-button>
            <a-button @click="downloadYaml">导出 YAML</a-button>
          </a-space>
        </a-form>
      </a-col>
      <a-col :span="16">
        <a-typography-title :level="5">任务</a-typography-title>
        <a-table
          :columns="jobColumns"
          :data-source="(jobs as Record<string, unknown>[]) as any"
          :pagination="false"
          size="small"
          row-key="id"
        >
          <template #bodyCell="{ column, text, record }">
            <template v-if="column.key === 'act' && record && typeof record === 'object' && 'id' in record">
              <a @click="selectJob(String((record as { id: string }).id))">查看</a>
            </template>
            <span v-else>{{ text }}</span>
          </template>
        </a-table>
        <a-typography-paragraph
          >当前：{{ currentJobId || "—" }} <a-tag v-if="jobStatus">{{ jobStatus }}</a-tag></a-typography-paragraph
        >
        <a-typography-title :level="5">资源（约 2s）</a-typography-title>
        <a-typography-paragraph style="word-break: break-all; font-size: 12px; color: #666">
          {{ resInfo || "—" }}
        </a-typography-paragraph>
        <a-typography-title :level="5">日志</a-typography-title>
        <a-button v-if="!stick" type="dashed" size="small" style="margin-bottom: 8px" @click="scrollToBottom"
          >跟随最新 / 回到底部</a-button
        >
        <pre
          ref="logPre"
          style="
            font-family: ui-monospace, monospace;
            font-size: 12px;
            height: 200px;
            overflow: auto;
            background: #0d1117;
            color: #e6edf3;
            padding: 8px;
            border-radius: 4px;
            margin-top: 0;
            white-space: pre-wrap;
          "
          @scroll="onLogScroll"
          >{{ logText }}</pre
        >
        <a-typography-title :level="5" style="margin-top: 12px">曲线</a-typography-title>
        <div ref="chartRef" style="height: 280px" />
      </a-col>
    </a-row>
  </div>
</template>
