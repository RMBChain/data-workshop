<script setup lang="ts">
import { CloseOutlined } from "@ant-design/icons-vue";
import * as echarts from "echarts";
import { nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { http } from "../api/http";

type SystemResourcesPayload = {
  cpu_percent: number;
  memory: { used_bytes: number; total_bytes: number; percent: number };
  note?: string;
};

const open = defineModel<boolean>("open", { default: false });

const pos = ref({ x: 0, y: 0 });
const dragging = ref(false);
let dragStart = { clientX: 0, clientY: 0, posX: 0, posY: 0 };

const PANEL_MIN_TOP = 72;
const MIN_PANEL_W = 320;
const MIN_PANEL_H = 220;
const DEFAULT_PANEL_W = 948;
const DEFAULT_PANEL_H = 340;

const panelW = ref(DEFAULT_PANEL_W);
const panelH = ref(DEFAULT_PANEL_H);
const resizing = ref(false);
let resizeStart = { clientX: 0, clientY: 0, w: 0, h: 0 };

function placePanelInitial() {
  const w = typeof window !== "undefined" ? window.innerWidth : 1200;
  const h = typeof window !== "undefined" ? window.innerHeight : 800;
  pos.value = {
    x: Math.max(16, w - panelW.value - 24),
    y: Math.min(PANEL_MIN_TOP + 16, Math.max(PANEL_MIN_TOP, h * 0.12)),
  };
  clampPanelToViewport();
}

function onHeaderMouseDown(e: MouseEvent) {
  const t = e.target as HTMLElement;
  if (t.closest("button")) return;
  dragging.value = true;
  dragStart = { clientX: e.clientX, clientY: e.clientY, posX: pos.value.x, posY: pos.value.y };
  window.addEventListener("mousemove", onWindowMouseMove);
  window.addEventListener("mouseup", onWindowMouseUp);
  e.preventDefault();
}

function onWindowMouseMove(e: MouseEvent) {
  if (!dragging.value) return;
  const nx = dragStart.posX + e.clientX - dragStart.clientX;
  const ny = dragStart.posY + e.clientY - dragStart.clientY;
  const maxX = Math.max(0, window.innerWidth - panelW.value - 8);
  const maxY = Math.max(0, window.innerHeight - panelH.value - 8);
  pos.value = {
    x: Math.min(maxX, Math.max(8, nx)),
    y: Math.min(maxY, Math.max(PANEL_MIN_TOP, ny)),
  };
}

function onWindowMouseUp() {
  dragging.value = false;
  window.removeEventListener("mousemove", onWindowMouseMove);
  window.removeEventListener("mouseup", onWindowMouseUp);
}

function onResizeHandleMouseDown(e: MouseEvent) {
  e.preventDefault();
  e.stopPropagation();
  resizing.value = true;
  resizeStart = {
    clientX: e.clientX,
    clientY: e.clientY,
    w: panelW.value,
    h: panelH.value,
  };
  window.addEventListener("mousemove", onResizeMouseMove);
  window.addEventListener("mouseup", onResizeMouseUp);
}

function onResizeMouseMove(e: MouseEvent) {
  if (!resizing.value) return;
  let nw = resizeStart.w + (e.clientX - resizeStart.clientX);
  let nh = resizeStart.h + (e.clientY - resizeStart.clientY);
  nw = Math.max(MIN_PANEL_W, nw);
  nh = Math.max(MIN_PANEL_H, nh);
  const maxW = window.innerWidth - pos.value.x - 8;
  const maxH = window.innerHeight - pos.value.y - 8;
  nw = Math.min(nw, Math.max(MIN_PANEL_W, maxW));
  nh = Math.min(nh, Math.max(MIN_PANEL_H, maxH));
  panelW.value = nw;
  panelH.value = nh;
  onChartsResize();
}

function onResizeMouseUp() {
  window.removeEventListener("mousemove", onResizeMouseMove);
  window.removeEventListener("mouseup", onResizeMouseUp);
  resizing.value = false;
}

function clampPanelToViewport() {
  const maxW = Math.max(MIN_PANEL_W, window.innerWidth - pos.value.x - 8);
  const maxH = Math.max(MIN_PANEL_H, window.innerHeight - pos.value.y - 8);
  if (panelW.value > maxW) panelW.value = maxW;
  if (panelH.value > maxH) panelH.value = maxH;
  const maxX = Math.max(8, window.innerWidth - panelW.value - 8);
  const maxY = Math.max(PANEL_MIN_TOP, window.innerHeight - panelH.value - 8);
  pos.value = {
    x: Math.min(Math.max(8, pos.value.x), maxX),
    y: Math.min(Math.max(PANEL_MIN_TOP, pos.value.y), maxY),
  };
}

function onWindowBoundsChange() {
  clampPanelToViewport();
  onChartsResize();
}

function close() {
  open.value = false;
}

let resPoll: ReturnType<typeof setInterval> | null = null;
const resourceSnapshot = ref<SystemResourcesPayload | null>(null);
const resCpuChartRef = ref<HTMLDivElement | null>(null);
const resMemChartRef = ref<HTMLDivElement | null>(null);
let resCpuChart: echarts.ECharts | null = null;
let resMemChart: echarts.ECharts | null = null;

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatBytesAxis(n: number) {
  const v = Number(n);
  if (!Number.isFinite(v) || v < 0) return "0";
  if (v < 1024) return `${Math.round(v)}`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(0)}K`;
  if (v < 1024 * 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)}M`;
  return `${(v / 1024 / 1024 / 1024).toFixed(1)}G`;
}

function clamp01to100(n: number) {
  if (!Number.isFinite(n)) return 0;
  return Math.min(100, Math.max(0, n));
}

const MAX_RESOURCE_LINE_POINTS = 150;
const resourceTimeSeries: { t: number; cpu: number; memBytes: number }[] = [];

function emptyCpuLineOption() {
  return {
    xAxis: { type: "time" as const },
    yAxis: { type: "value" as const, name: "占用率 (%)", min: 0, max: 100 },
    series: [],
  };
}

function emptyMemLineOption() {
  return {
    xAxis: { type: "time" as const },
    yAxis: { type: "value" as const, name: "已用内存", min: 0 },
    series: [],
  };
}

function buildSingleLineOption(color: string, name: string, points: [number, number][]) {
  const n = points.length;
  const showSymbol = n <= 3;
  return {
    color: [color],
    animationDurationUpdate: 300,
    tooltip: {
      trigger: "axis" as const,
      axisPointer: { type: "cross" as const },
      valueFormatter: (v: string | number) => `${Number(v).toFixed(1)}%`,
    },
    grid: { left: 50, right: 20, top: 16, bottom: 24, containLabel: true },
    xAxis: { type: "time" as const },
    yAxis: {
      type: "value" as const,
      name: "占用率 (%)",
      min: 0,
      max: 100,
      splitLine: { show: true, lineStyle: { type: "dashed" } },
    },
    series: [
      {
        name,
        type: "line" as const,
        smooth: 0.2,
        showSymbol,
        symbolSize: 6,
        lineStyle: { width: 2 },
        data: points,
      },
    ],
  };
}

function buildMemBytesLineOption(
  color: string,
  name: string,
  points: [number, number][],
  totalBytes?: number,
) {
  const n = points.length;
  const showSymbol = n <= 3;
  const dataMax = n ? Math.max(...points.map((p) => p[1]), 0) : 0;
  const cap =
    typeof totalBytes === "number" && totalBytes > 0 && totalBytes >= dataMax ? totalBytes : undefined;
  return {
    color: [color],
    animationDurationUpdate: 300,
    tooltip: {
      trigger: "axis" as const,
      axisPointer: { type: "cross" as const },
      valueFormatter: (v: string | number) => formatBytes(Number(v)),
    },
    grid: { left: 56, right: 20, top: 16, bottom: 24, containLabel: true },
    xAxis: { type: "time" as const },
    yAxis: {
      type: "value" as const,
      name: "已用内存",
      min: 0,
      max: cap ?? (dataMax > 0 ? Math.ceil(dataMax * 1.08) : undefined),
      splitLine: { show: true, lineStyle: { type: "dashed" } },
      axisLabel: {
        formatter: (val: string | number) => formatBytesAxis(Number(val)),
      },
    },
    series: [
      {
        name,
        type: "line" as const,
        smooth: 0.2,
        showSymbol,
        symbolSize: 6,
        lineStyle: { width: 2 },
        data: points,
      },
    ],
  };
}

function updateResourceLineCharts() {
  const elCpu = resCpuChartRef.value;
  const elMem = resMemChartRef.value;
  if (!elCpu || !elMem) return;
  if (!resCpuChart) resCpuChart = echarts.init(elCpu);
  if (!resMemChart) resMemChart = echarts.init(elMem);

  if (resourceTimeSeries.length === 0) {
    resCpuChart.setOption({ ...emptyCpuLineOption(), color: ["#5470c6"] }, true);
    resMemChart.setOption({ ...emptyMemLineOption(), color: ["#91cc75"] }, true);
    return;
  }

  const cpuPts = resourceTimeSeries.map((p) => [p.t, p.cpu] as [number, number]);
  const memPts = resourceTimeSeries.map((p) => [p.t, p.memBytes] as [number, number]);
  const totalB = resourceSnapshot.value?.memory?.total_bytes;
  resCpuChart.setOption(buildSingleLineOption("#5470c6", "CPU (%)", cpuPts), true);
  resMemChart.setOption(
    buildMemBytesLineOption("#91cc75", "已用内存", memPts, typeof totalB === "number" ? totalB : undefined),
    true,
  );
}

async function loadSystemResources() {
  try {
    const r = await http.get<SystemResourcesPayload>("/api/system/resources");
    resourceSnapshot.value = r.data;
    const t = Date.now();
    const cpu = clamp01to100(r.data.cpu_percent);
    const memBytes = Math.max(0, Math.floor(Number(r.data.memory?.used_bytes ?? 0)));
    resourceTimeSeries.push({ t, cpu, memBytes });
    while (resourceTimeSeries.length > MAX_RESOURCE_LINE_POINTS) {
      resourceTimeSeries.shift();
    }
    updateResourceLineCharts();
  } catch {
    resourceSnapshot.value = null;
    resourceTimeSeries.length = 0;
    resCpuChart?.clear();
    resMemChart?.clear();
  }
}

function onChartsResize() {
  requestAnimationFrame(() => {
    resCpuChart?.resize();
    resMemChart?.resize();
  });
}

function startPolling() {
  void loadSystemResources();
  if (resPoll) clearInterval(resPoll);
  resPoll = setInterval(() => void loadSystemResources(), 2000);
}

function stopPolling() {
  if (resPoll) {
    clearInterval(resPoll);
    resPoll = null;
  }
}

function disposeCharts() {
  resourceTimeSeries.length = 0;
  if (resCpuChart) {
    resCpuChart.dispose();
    resCpuChart = null;
  }
  if (resMemChart) {
    resMemChart.dispose();
    resMemChart = null;
  }
}

watch(
  open,
  async (v) => {
    if (v) {
      placePanelInitial();
      await nextTick();
      startPolling();
      requestAnimationFrame(() => onChartsResize());
    } else {
      stopPolling();
      disposeCharts();
      resourceSnapshot.value = null;
    }
  },
  { immediate: true },
);

onMounted(() => {
  window.addEventListener("resize", onWindowBoundsChange);
});

onUnmounted(() => {
  window.removeEventListener("resize", onWindowBoundsChange);
  onWindowMouseUp();
  onResizeMouseUp();
  stopPolling();
  disposeCharts();
});
</script>

<template>
  <Teleport to="body">
    <div
      v-show="open"
      class="resource-float"
      :class="{ 'resource-float--dragging': dragging, 'resource-float--resizing': resizing }"
      :style="{ left: `${pos.x}px`, top: `${pos.y}px`, width: `${panelW}px`, height: `${panelH}px` }"
      role="dialog"
      aria-label="资源信息"
    >
      <div class="resource-float__header" @mousedown="onHeaderMouseDown">
        <span class="resource-float__title">资源信息</span>
        <a-button type="text" size="small" aria-label="关闭" class="resource-float__close" @click="close">
          <template #icon><CloseOutlined /></template>
        </a-button>
      </div>
      <div class="resource-float__body">
        <a-row :gutter="[16, 16]">
          <a-col :span="24">
            <div class="res-charts-pair">
              <div class="res-charts-pair__panel">
                <div
                  style="
                    display: flex;
                    align-items: baseline;
                    justify-content: space-between;
                    gap: 8px;
                    flex-wrap: wrap;
                    margin-bottom: 4px;
                  "
                >
                  <span style="font-size: 12px; color: rgba(0, 0, 0, 0.45)">CPU 使用率</span>
                  <span
                    v-if="resourceSnapshot"
                    style="font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; color: rgba(0, 0, 0, 0.88)"
                    >{{ resourceSnapshot.cpu_percent.toFixed(1) }}%</span
                  >
                  <span v-else style="font-size: 12px; color: rgba(0, 0, 0, 0.25)">—</span>
                </div>
                <div ref="resCpuChartRef" class="res-charts-pair__chart" />
              </div>
              <div class="res-charts-pair__vbar" aria-hidden="true" />
              <div class="res-charts-pair__panel">
                <div
                  style="
                    display: flex;
                    align-items: baseline;
                    justify-content: space-between;
                    gap: 8px;
                    flex-wrap: wrap;
                    margin-bottom: 4px;
                  "
                >
                  <span style="font-size: 12px; color: rgba(0, 0, 0, 0.45)"
                    >内存（若在虚拟环境中，可能会和宿主机不同）</span
                  >
                  <span
                    v-if="resourceSnapshot"
                    style="font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; color: rgba(0, 0, 0, 0.88); text-align: right"
                  >
                    {{ formatBytes(resourceSnapshot.memory.used_bytes) }}
                    <template v-if="resourceSnapshot.memory.total_bytes > 0">
                      &nbsp;/ {{ formatBytes(resourceSnapshot.memory.total_bytes) }}
                    </template>
                    &nbsp;（{{ resourceSnapshot.memory.percent.toFixed(1) }}%）
                  </span>
                  <span v-else style="font-size: 12px; color: rgba(0, 0, 0, 0.25)">—</span>
                </div>
                <div ref="resMemChartRef" class="res-charts-pair__chart" />
              </div>
            </div>
          </a-col>
        </a-row>
        <a-typography-text v-if="resourceSnapshot?.note" type="warning" style="display: block; margin-top: 4px; font-size: 12px">
          {{ resourceSnapshot.note }}
        </a-typography-text>
        <a-typography-text v-else-if="!resourceSnapshot" type="secondary" style="display: block; margin-top: 4px; font-size: 12px">
          暂无资源数据
        </a-typography-text>
      </div>
      <div
        class="resource-float__resize"
        title="拖拽调整大小"
        aria-label="拖拽调整窗口大小"
        @mousedown="onResizeHandleMouseDown"
      />
    </div>
  </Teleport>
</template>

<style scoped>
.resource-float {
  position: fixed;
  z-index: 1100;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: #fff;
  border-radius: 8px;
  box-shadow:
    0 6px 16px 0 rgba(0, 0, 0, 0.08),
    0 3px 6px -4px rgba(0, 0, 0, 0.12),
    0 9px 28px 8px rgba(0, 0, 0, 0.05);
  border: 1px solid rgba(0, 0, 0, 0.06);
  overflow: hidden;
  box-sizing: border-box;
}
.resource-float--dragging {
  cursor: grabbing;
  user-select: none;
}
.resource-float--resizing {
  user-select: none;
}
.resource-float__header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  background: #fafafa;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  cursor: grab;
}
.resource-float__title {
  font-size: 14px;
  font-weight: 600;
  color: rgba(0, 0, 0, 0.88);
}
.resource-float__close {
  flex-shrink: 0;
  color: rgba(0, 0, 0, 0.45);
}
.resource-float__body {
  flex: 1;
  min-height: 0;
  padding: 12px 12px 14px;
  overflow: auto;
}
.resource-float__resize {
  flex-shrink: 0;
  align-self: flex-end;
  width: 16px;
  height: 16px;
  margin: 2px 4px 4px 0;
  cursor: nwse-resize;
  touch-action: none;
  background: linear-gradient(
    135deg,
    transparent 0%,
    transparent 45%,
    rgba(0, 0, 0, 0.12) 45%,
    rgba(0, 0, 0, 0.12) 48%,
    transparent 48%,
    transparent 52%,
    rgba(0, 0, 0, 0.12) 52%,
    rgba(0, 0, 0, 0.12) 55%,
    transparent 55%
  );
}
.resource-float__resize:hover {
  background: linear-gradient(
    135deg,
    transparent 0%,
    transparent 42%,
    rgba(22, 119, 255, 0.45) 42%,
    rgba(22, 119, 255, 0.45) 58%,
    transparent 58%
  );
}
.res-charts-pair {
  display: flex;
  flex-direction: row;
  align-items: stretch;
  width: 100%;
  box-sizing: border-box;
}
.res-charts-pair__panel {
  flex: 1 1 0;
  min-width: 0;
}
.res-charts-pair__chart {
  height: 200px;
  width: 100%;
  min-width: 0;
}
.res-charts-pair__vbar {
  flex: 0 0 1px;
  width: 1px;
  align-self: stretch;
  min-height: 0;
  background: rgba(0, 0, 0, 0.08);
  margin: 0 8px;
  box-sizing: content-box;
}
@media (max-width: 991px) {
  .res-charts-pair {
    flex-direction: column;
  }
  .res-charts-pair__vbar {
    display: none;
  }
  .res-charts-pair__panel:last-of-type {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid rgba(0, 0, 0, 0.08);
  }
}
</style>
