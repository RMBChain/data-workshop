<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { http } from "../api/http";

type CpuFreq = { current?: number | null; min?: number | null; max?: number | null };
type GpuRow = { index: number; name: string; memory_total_bytes: number };

type SystemInfoPayload = {
  error?: string;
  python?: string;
  platform?: string;
  torch?: string;
  torch_cuda_available?: boolean;
  gpu_note?: string;
  gpu_list_note?: string;
  hardware_note?: string;
  cpu_physical_cores?: number | null;
  cpu_logical_threads?: number | null;
  cpu_freq_mhz?: CpuFreq | null;
  memory_total_bytes?: number | null;
  gpus?: GpuRow[];
};

type ResourcesPayload = {
  cpu_percent?: number;
  memory?: { used_bytes: number; total_bytes: number; percent: number };
  note?: string;
};

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatMhz(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toFixed(0)} MHz`;
}

const systemLoading = ref(false);
const systemData = ref<SystemInfoPayload>({});
const pathsData = ref<Record<string, unknown>>({});
const resData = ref<ResourcesPayload>({});
let resTimer: ReturnType<typeof setInterval> | null = null;

const cpuDisplay = computed(() => {
  const d = systemData.value;
  if (d.error) return "—";
  const phys = d.cpu_physical_cores;
  const log = d.cpu_logical_threads;
  const parts: string[] = [];
  if (phys != null) parts.push(`物理核心：${phys}`);
  if (log != null) parts.push(`逻辑线程：${log}`);
  const f = d.cpu_freq_mhz;
  if (f && (f.current != null || f.max != null || f.min != null)) {
    const cur = f.current != null ? formatMhz(f.current) : "—";
    const hi = f.max != null ? `，最高 ${formatMhz(f.max)}` : "";
    parts.push(`频率：当前 ${cur}${hi}`);
  } else {
    parts.push("频率：—（部分环境无法读取）");
  }
  return parts.length ? parts.join(" · ") : "—";
});

const memoryDisplay = computed(() => {
  const d = systemData.value;
  if (d.error) return "—";
  const total = d.memory_total_bytes;
  const m = resData.value.memory;
  const lines: string[] = [];
  if (total != null) lines.push(`安装内存：${formatBytes(total)}`);
  if (m && m.total_bytes) {
    lines.push(
      `实时占用：${formatBytes(m.used_bytes)} / ${formatBytes(m.total_bytes)}（${Number(m.percent).toFixed(1)}%）`,
    );
  }
  return lines.length ? lines.join("；") : "—";
});

const gpuDisplayLines = computed(() => {
  const gpus = systemData.value.gpus ?? [];
  return gpus.map(
    (g) => `GPU ${g.index}：${g.name} · 显存 ${formatBytes(g.memory_total_bytes)}`,
  );
});

async function load() {
  systemLoading.value = true;
  try {
    const [a, b, c] = await Promise.all([
      http.get<SystemInfoPayload>("/api/system/info"),
      http.get("/api/system/paths"),
      http.get<ResourcesPayload>("/api/system/resources"),
    ]);
    systemData.value = a.data;
    pathsData.value = b.data;
    resData.value = c.data;
  } catch {
    systemData.value = { error: "无法加载" };
  } finally {
    systemLoading.value = false;
  }
  if (resTimer) {
    clearInterval(resTimer);
    resTimer = null;
  }
  resTimer = setInterval(async () => {
    try {
      const r = await http.get<ResourcesPayload>("/api/system/resources");
      resData.value = r.data;
    } catch {
      /* ignore */
    }
  }, 2000);
}

onMounted(() => {
  void load();
});
onUnmounted(() => {
  if (resTimer) {
    clearInterval(resTimer);
    resTimer = null;
  }
});
</script>

<template>
  <a-spin :spinning="systemLoading">
    <a-alert
      v-if="systemData.error"
      type="error"
      :message="systemData.error"
      show-icon
      style="margin-bottom: 16px"
    />
    <a-typography-paragraph v-if="systemData.hardware_note" type="secondary">
      {{ systemData.hardware_note }}
    </a-typography-paragraph>
    <a-typography-paragraph v-if="systemData.gpu_note" type="secondary">
      {{ systemData.gpu_note }}
    </a-typography-paragraph>
    <a-descriptions bordered size="small" :column="1">
      <a-descriptions-item label="Python">
        {{ systemData.python }}
      </a-descriptions-item>
      <a-descriptions-item label="平台">
        {{ systemData.platform }}
      </a-descriptions-item>
      <a-descriptions-item label="PyTorch">
        {{ systemData.torch }} · CUDA 可见：{{
          systemData.torch_cuda_available ? "是" : "否"
        }}
      </a-descriptions-item>
      <a-descriptions-item label="CPU">
        {{ cpuDisplay }}
      </a-descriptions-item>
      <a-descriptions-item label="内存">
        {{ memoryDisplay }}
        <a-typography-text v-if="resData.note" type="secondary" style="display: block; margin-top: 6px">
          {{ resData.note }}
        </a-typography-text>
      </a-descriptions-item>
      <a-descriptions-item v-if="gpuDisplayLines.length" label="GPU">
        <div v-for="line in gpuDisplayLines" :key="line">{{ line }}</div>
      </a-descriptions-item>
    </a-descriptions>
    <a-typography-paragraph v-if="systemData.gpu_list_note && !gpuDisplayLines.length" type="secondary">
      {{ systemData.gpu_list_note }}
    </a-typography-paragraph>
    <a-typography-title :level="5" style="margin-top: 16px">约定路径</a-typography-title>
    <a-typography-paragraph
      v-for="(v, k) in pathsData as Record<string, string>"
      :key="k"
      copyable
      >{{ k }}：{{ v }}</a-typography-paragraph
    >
  </a-spin>
</template>
