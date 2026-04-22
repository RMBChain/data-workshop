<script setup lang="ts">
import { message } from "ant-design-vue";
import { onMounted, onUnmounted, ref, computed } from "vue";
import { useRouter } from "vue-router";
import { http } from "../api/http";

const router = useRouter();
const imports = ref<{ id: string; project_title: string; task_count: number; created_at: string }[]>([]);
const selectedBatch = ref<string | null>(null);
const buildNote = ref("");
const trainRatio = ref(80);
const valRatio = ref(10);
const testRatio = ref(10);
const seed = ref<number | null>(null);
const addImageToken = ref(true);
const buildJob = ref<Record<string, unknown> | null>(null);
const pollT = ref<ReturnType<typeof setInterval> | null>(null);
const versions = ref<Record<string, unknown>[]>([]);
const activeVersion = ref<string | null>(null);
const preview = ref<unknown>(null);
const autoImageLabel = computed(() => "自动在文本中补全 <image> 提示");

/** 轮询间隔（毫秒）；1s 会使 uvicorn 访问日志很密 */
const DATASET_JOB_POLL_MS = 2500;
/** 超过该时长仍无终态则停止轮询（正常构建一般远小于此；避免异常卡死一直请求） */
const DATASET_JOB_POLL_MAX_MS = 60 * 60 * 1000;

onMounted(() => {
  void refreshImports();
  void refreshVersions();
});

onUnmounted(() => {
  if (pollT.value) {
    clearInterval(pollT.value);
    pollT.value = null;
  }
});

async function refreshImports() {
  const r = await http.get("/api/imports");
  imports.value = r.data.items;
  if (imports.value.length && !selectedBatch.value) {
    selectedBatch.value = imports.value[0].id;
  }
}

async function refreshVersions() {
  const r = await http.get("/api/datasets/versions");
  versions.value = r.data.items;
  activeVersion.value = r.data.active_version_id;
}

async function startBuild() {
  if (!selectedBatch.value) {
    message.warning("请选择导入批次");
    return;
  }
  try {
    const r = await http.post("/api/datasets/build", {
      import_batch_id: selectedBatch.value,
      add_image_token: addImageToken.value,
      train_ratio: trainRatio.value,
      val_ratio: valRatio.value,
      test_ratio: testRatio.value,
      random_seed: seed.value,
      note: buildNote.value || null,
    });
    const jid = r.data.job_id as string;
    buildJob.value = { id: jid, status: "pending" };
    if (pollT.value) clearInterval(pollT.value);
    const buildPollStart = Date.now();
    pollT.value = setInterval(() => {
      void (async () => {
        if (Date.now() - buildPollStart > DATASET_JOB_POLL_MAX_MS) {
          if (pollT.value) clearInterval(pollT.value);
          pollT.value = null;
          message.warning("构建状态长时间未结束，已停止轮询。请查看「版本」或刷新后重试。");
          return;
        }
        try {
          const st = await http.get(`/api/datasets/jobs/${jid}`);
          buildJob.value = st.data;
          if (["succeeded", "failed", "cancelled"].includes(String(st.data.status))) {
            if (pollT.value) clearInterval(pollT.value);
            pollT.value = null;
            if (st.data.status === "succeeded") {
              message.success("数据集已生成");
              await refreshVersions();
            } else if (st.data.status === "failed") {
              const em = (st.data as { error_message?: string }).error_message;
              message.error(em && String(em).trim() ? em : "数据集构建失败");
            }
          }
        } catch (e: unknown) {
          if (pollT.value) clearInterval(pollT.value);
          pollT.value = null;
          const err = e as { response?: { status?: number } };
          if (err.response?.status === 404) {
            message.error("构建任务已不存在，已停止轮询。");
          } else {
            message.error("获取构建状态失败，已停止轮询。");
          }
        }
      })();
    }, DATASET_JOB_POLL_MS);
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "失败");
  }
}

async function loadPreview() {
  try {
    const r = await http.get("/api/datasets/preview", { params: {} });
    preview.value = r.data.sample;
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "无预览");
  }
}

function goTrain() {
  void router.push("/train");
}
</script>

<template>
  <div>
    <a-typography-title :level="4">数据集</a-typography-title>
    <a-alert
      type="info"
      show-icon
      message="将导入数据转为 SFT 对话格式（qwen-vl 模板族），并划分训练/验证/测试。产物位于工作区 versions/。"
      style="margin-bottom: 12px"
    />
    <a-form layout="vertical" style="max-width: 640px">
      <a-form-item label="源导入批次">
        <a-select
          v-model:value="selectedBatch"
          :options="imports.map((i) => ({ value: i.id, label: `${i.id} — ${i.project_title}（${i.task_count} 条）` }))"
          style="width: 100%"
          placeholder="无批次时请先到「数据导入」"
        />
      </a-form-item>
      <a-form-item :label="autoImageLabel">
        <a-switch v-model:checked="addImageToken" />
      </a-form-item>
      <a-form-item label="划分比例（训练/验证/测试）">
        <a-input-number v-model:value="trainRatio" :min="0" :max="100" /> /
        <a-input-number v-model:value="valRatio" :min="0" :max="100" /> /
        <a-input-number v-model:value="testRatio" :min="0" :max="100" />
      </a-form-item>
      <a-form-item label="随机种子（可空）">
        <a-input-number v-model:value="seed" style="width: 100%" />
      </a-form-item>
      <a-form-item label="备注">
        <a-input v-model:value="buildNote" />
      </a-form-item>
      <a-form-item>
        <a-button type="primary" @click="startBuild">生成数据集</a-button>
        <a-button style="margin-left: 8px" @click="loadPreview">预览一条样本</a-button>
        <a-button type="link" @click="goTrain">去训练</a-button>
      </a-form-item>
    </a-form>
    <a-typography-paragraph v-if="buildJob"
      >当前构建任务：{{ String((buildJob as { id?: string }).id) }} ·
      {{ String((buildJob as { status?: string }).status) }}</a-typography-paragraph
    >
    <a-typography-title :level="5">版本</a-typography-title>
    <a-table
      :columns="[
        { title: 'ID', dataIndex: 'id', key: 'id' },
        { title: '时间', dataIndex: 'created_at', key: 'created_at' },
        { title: '备注', dataIndex: 'note', key: 'note' },
        { title: '激活', dataIndex: 'is_active', key: 'act', width: 80 },
      ]"
      :data-source="versions as Record<string, unknown>[]"
      :pagination="false"
      size="small"
      row-key="id"
    />
    <a-typography-title :level="5" style="margin-top: 16px">样本预览</a-typography-title>
    <pre v-if="preview" style="max-height: 240px; overflow: auto; font-size: 12px">{{
      JSON.stringify(preview, null, 2)
    }}</pre>
    <a-typography-paragraph v-else type="secondary">未加载</a-typography-paragraph>
  </div>
</template>
