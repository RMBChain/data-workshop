<script setup lang="ts">
import { MinusOutlined, PlusOutlined, ReloadOutlined, UndoOutlined } from "@ant-design/icons-vue";
import * as echarts from "echarts";
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { http } from "../api/http";

type PipeMergeN = { id: string; label: string; status: string };
type PipeTrainN = { id: string; label: string; status: string; merges: PipeMergeN[] };
type PipeDatasetN = {
  id: string;
  label: string;
  ls_import_id: string;
  virtual?: boolean;
  trainings: PipeTrainN[];
};
type PipeProjectN = { key: string; label: string; datasets: PipeDatasetN[] };

const pipelineLoading = ref(false);
const pipelineErrorText = ref<string | null>(null);
const pipelineProjects = ref<PipeProjectN[]>([]);
const pipelineChartRef = ref<HTMLDivElement | null>(null);
const pipelineChartMinHeight = ref(480);
const pipelineChartZoom = ref(1);
const PIPE_ZOOM_MIN = 0.5;
const PIPE_ZOOM_MAX = 2.5;
const PIPE_ZOOM_STEP = 0.1;
let pipelineChart: echarts.ECharts | null = null;

const pipelineChartBoxStyle = computed(() => {
  const z = pipelineChartZoom.value;
  const h = Math.max(200, Math.round(pipelineChartMinHeight.value * z));
  const w = `${(100 * z).toFixed(2)}%`;
  return {
    width: w,
    maxWidth: "none",
    height: `${h}px`,
    boxSizing: "border-box" as const,
  };
});

const pipelineZoomPercentLabel = computed(() => `${Math.round(pipelineChartZoom.value * 100)}%`);

function pipeMergeNode(m: PipeMergeN) {
  return {
    name: `合并 · ${m.label} [${m.status}]`,
  };
}

function pipeTrainNode(t: PipeTrainN) {
  const trName = `训练 · ${t.label} [${t.status}]`;
  if (!t.merges?.length) {
    return { name: trName };
  }
  return {
    name: trName,
    children: t.merges.map(pipeMergeNode),
  };
}

function pipeDatasetNode(d: PipeDatasetN) {
  const dName = `数据集 · ${d.label}`;
  if (!d.trainings.length) {
    return { name: dName };
  }
  return {
    name: dName,
    children: d.trainings.map(pipeTrainNode),
  };
}

function pipeBuildTreeData(list: PipeProjectN[]) {
  return list.map((p) => {
    if (!p.datasets.length) {
      return { name: `项目 · ${p.label}` };
    }
    return {
      name: `项目 · ${p.label}`,
      children: p.datasets.map(pipeDatasetNode),
    };
  });
}

function pipeBuildSeriesData(list: PipeProjectN[]) {
  const projectNodes = pipeBuildTreeData(list);
  if (!projectNodes.length) {
    return [];
  }
  return [
    {
      name: "全部项目",
      children: projectNodes,
    },
  ];
}

function setPipelineChartOption() {
  if (!pipelineChart) return;
  const list = pipelineProjects.value;
  if (!list.length) {
    pipelineChartMinHeight.value = 480;
    pipelineChart.setOption(
      {
        title: { text: "暂无项目/数据集", left: "center", top: "middle", textStyle: { color: "#999", fontSize: 16 } },
        series: [],
      },
      true
    );
    return;
  }
  const n = list.length;
  pipelineChartMinHeight.value = Math.min(3600, Math.max(480, 160 + n * 140));
  const data = pipeBuildSeriesData(list);
  pipelineChart.setOption(
    {
      title: { show: false },
      tooltip: {
        trigger: "item",
        confine: true,
        enterable: true,
      },
      series: [
        {
          type: "tree",
          data,
          top: "6%",
          right: "12%",
          bottom: "6%",
          left: "7%",
          symbol: "roundRect",
          symbolSize: 12,
          orient: "LR",
          layout: "orthogonal",
          edgeShape: "polyline",
          label: {
            position: "left",
            verticalAlign: "middle",
            align: "right",
            fontSize: 12,
            lineHeight: 16,
            distance: 8,
            overflow: "truncate",
            width: 160,
            padding: [2, 4, 2, 4],
          },
          leaves: {
            label: {
              position: "right",
              verticalAlign: "middle",
              align: "left",
              padding: [2, 4, 2, 4],
            },
            symbolSize: 8,
          },
          emphasis: { focus: "descendant" },
          expandAndCollapse: true,
          initialTreeDepth: 5,
          animationDuration: 200,
          animationDurationUpdate: 200,
          lineStyle: { width: 1.5, color: "#b5b5b5" },
        },
      ],
    } as echarts.EChartsOption,
    true
  );
  void nextTick(() => {
    pipelineChart?.resize();
  });
}

function resizePipelineChart() {
  pipelineChart?.resize();
}

function pipelineZoomIn() {
  pipelineChartZoom.value = Math.min(PIPE_ZOOM_MAX, Math.round((pipelineChartZoom.value + PIPE_ZOOM_STEP) * 10) / 10);
  void nextTick(() => {
    pipelineChart?.resize();
  });
}

function pipelineZoomOut() {
  pipelineChartZoom.value = Math.max(PIPE_ZOOM_MIN, Math.round((pipelineChartZoom.value - PIPE_ZOOM_STEP) * 10) / 10);
  void nextTick(() => {
    pipelineChart?.resize();
  });
}

function pipelineZoomReset() {
  pipelineChartZoom.value = 1;
  void nextTick(() => {
    pipelineChart?.resize();
  });
}

async function loadPipelineOverview() {
  pipelineLoading.value = true;
  pipelineErrorText.value = null;
  try {
    const res = await http.get<{ projects: PipeProjectN[] }>("/api/overview/pipeline");
    pipelineProjects.value = res.data?.projects ?? [];
    await nextTick();
    if (!pipelineChart && pipelineChartRef.value) {
      pipelineChart = echarts.init(pipelineChartRef.value);
    }
    setPipelineChartOption();
  } catch (e: unknown) {
    const msg = e && typeof e === "object" && "message" in e ? String((e as { message?: string }).message) : "加载失败";
    pipelineErrorText.value = msg;
    pipelineProjects.value = [];
  } finally {
    pipelineLoading.value = false;
  }
}

onMounted(() => {
  void loadPipelineOverview();
  window.addEventListener("resize", resizePipelineChart);
});

onUnmounted(() => {
  window.removeEventListener("resize", resizePipelineChart);
  pipelineChart?.dispose();
  pipelineChart = null;
});
</script>

<template>
  <div class="data-import-global-preview">
    <div class="data-import-global-preview__header">
      <a-typography-title :level="5" class="data-import-global-preview__title">全局预览</a-typography-title>
      <a-tooltip title="刷新" placement="bottomRight" :auto-adjust-overflow="false">
        <a-button
          type="text"
          class="data-import-global-preview__refresh-btn"
          :loading="pipelineLoading"
          aria-label="刷新全局预览"
          @click="loadPipelineOverview"
        >
          <template #icon>
            <ReloadOutlined />
          </template>
        </a-button>
      </a-tooltip>
    </div>
    <a-typography-paragraph type="secondary" style="margin-bottom: 16px">
      自左至右为：总览根「全部项目」、各 Label Studio 项目、数据集版本、训练、LoRA 合并。
    </a-typography-paragraph>
    <a-alert v-if="pipelineErrorText" type="error" :message="pipelineErrorText" show-icon style="margin-bottom: 12px" />
    <a-spin :spinning="pipelineLoading" tip="加载中…">
      <div class="data-import-global-preview__panel">
        <div class="data-import-global-preview__toolbar">
          <a-space :size="10" class="data-import-global-preview__toolbar__actions" align="center">
            <a-tooltip title="缩小">
              <a-button
                class="data-import-global-preview__zoom-btn"
                :disabled="pipelineChartZoom <= PIPE_ZOOM_MIN"
                aria-label="缩小关系图"
                @click="pipelineZoomOut"
              >
                <template #icon><MinusOutlined /></template>
              </a-button>
            </a-tooltip>
            <span class="data-import-global-preview__zoom-pct" aria-live="polite">{{ pipelineZoomPercentLabel }}</span>
            <a-tooltip title="放大">
              <a-button
                class="data-import-global-preview__zoom-btn"
                :disabled="pipelineChartZoom >= PIPE_ZOOM_MAX"
                aria-label="放大关系图"
                @click="pipelineZoomIn"
              >
                <template #icon><PlusOutlined /></template>
              </a-button>
            </a-tooltip>
            <a-tooltip title="恢复 100% 显示">
              <a-button
                class="data-import-global-preview__zoom-reset"
                type="text"
                :disabled="pipelineChartZoom === 1"
                aria-label="缩放置为百分之百"
                @click="pipelineZoomReset"
              >
                <template #icon><UndoOutlined /></template>
                100%
              </a-button>
            </a-tooltip>
          </a-space>
        </div>
        <div class="data-import-global-preview__scroll">
          <div
            ref="pipelineChartRef"
            class="data-import-global-preview__chart"
            :style="pipelineChartBoxStyle"
            role="img"
            aria-label="全部项目至合并 关系树"
          />
        </div>
      </div>
    </a-spin>
  </div>
</template>

<style scoped>
.data-import-global-preview {
  margin-top: 28px;
}
.data-import-global-preview__header {
  display: flex;
  align-items: center;
  width: 100%;
  box-sizing: border-box;
  margin: 0 0 8px;
  flex-wrap: wrap;
  gap: 8px 12px;
}
.data-import-global-preview__title {
  margin: 0 !important;
  min-width: 0;
  flex: 0 1 auto;
}
.data-import-global-preview__refresh-btn {
  flex-shrink: 0;
  font-size: 18px;
  color: rgba(0, 0, 0, 0.45);
}
.data-import-global-preview__refresh-btn:hover {
  color: var(--ant-primary-color, #1677ff);
}
.data-import-global-preview__panel {
  background: linear-gradient(180deg, #fbfcff 0%, #ffffff 48%);
  border: 1px solid #e6e8eb;
  border-radius: 10px;
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 4px 16px rgba(15, 23, 42, 0.06);
  overflow: hidden;
}
.data-import-global-preview__toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px 16px 10px;
  border-bottom: 1px solid #eef0f3;
  background: linear-gradient(180deg, #ffffff 0%, #f8f9fc 100%);
}
.data-import-global-preview__toolbar__actions {
  flex-shrink: 0;
}
.data-import-global-preview__zoom-btn {
  min-width: 32px;
  height: 32px;
  padding: 0 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.data-import-global-preview__zoom-pct {
  min-width: 3.25em;
  text-align: center;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: #434343;
  font-weight: 500;
}
.data-import-global-preview__zoom-reset {
  padding: 0 6px 0 2px;
  height: 32px;
  color: #595959;
}
.data-import-global-preview__scroll {
  padding: 12px 14px 18px;
  max-height: min(78vh, 1400px);
  overflow: auto;
  -webkit-overflow-scrolling: touch;
}
.data-import-global-preview__chart {
  min-height: 200px;
  display: block;
  border-radius: 6px;
  background: #fff;
  border: 1px solid #f0f0f0;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.02);
  overflow: visible;
}
</style>
