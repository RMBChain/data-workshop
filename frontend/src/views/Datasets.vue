<script setup lang="ts">
import { message } from "ant-design-vue";
import { onMounted, ref, computed } from "vue";
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

onMounted(() => {
  void refreshImports();
  void refreshVersions();
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
    pollT.value = setInterval(async () => {
      const st = await http.get(`/api/datasets/jobs/${jid}`);
      buildJob.value = st.data;
      if (["succeeded", "failed", "cancelled"].includes(String(st.data.status))) {
        if (pollT.value) clearInterval(pollT.value);
        pollT.value = null;
        if (st.data.status === "succeeded") {
          message.success("数据集已生成");
          await refreshVersions();
        }
      }
    }, 1000);
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
