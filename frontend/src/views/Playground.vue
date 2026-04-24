<script setup lang="ts">
import { message } from "ant-design-vue";
import { onMounted, ref, watch } from "vue";
import { http } from "../api/http";

const DEFAULT_BASE = "Qwen/Qwen3-VL-2B-Instruct";

const models = ref<
  {
    id: string;
    label: string;
    path: string;
    kind: string;
    job_id?: string;
    job_name?: string;
    train_base_model?: string | null;
  }[]
>([]);
const modelId = ref<string | null>(null);
const base = ref(DEFAULT_BASE);
const adapter = ref("");
const imageFile = ref<File | null>(null);
const prompt = ref("你好");
const maxNew = ref(256);
const loading = ref(false);
const loadModelLoading = ref(false);
const unloadModelLoading = ref(false);
const reply = ref("");

function filterTrainingOption(input: string, option: { value?: string | null }) {
  const q = input.trim().toLowerCase();
  if (!q) return true;
  const m = models.value.find((x) => x.id === option.value);
  if (!m) return false;
  const blob = `${m.label} ${m.path} ${m.job_id ?? ""}`.toLowerCase();
  return blob.includes(q);
}

onMounted(() => {
  void loadModels();
});

watch(modelId, () => {
  const id = modelId.value;
  if (!id) {
    base.value = DEFAULT_BASE;
    adapter.value = "";
    return;
  }
  const row = models.value.find((m) => m.id === id);
  if (row?.kind === "lora") {
    base.value = (row.train_base_model || "").trim() || DEFAULT_BASE;
    adapter.value = row.path;
    return;
  }
  if (row?.kind === "merge") {
    base.value = row.path;
    adapter.value = "";
    return;
  }
  if (id.startsWith("lora:")) {
    const rel = id.slice(5);
    const byPath = models.value.find((m) => m.path === rel);
    base.value =
      (byPath?.train_base_model || "").trim() || DEFAULT_BASE;
    adapter.value = rel;
  } else if (id.startsWith("merged:")) {
    base.value = id.slice(7);
    adapter.value = "";
  }
});

async function loadModels() {
  const r = await http.get("/api/inference/models");
  models.value = r.data.items;
  if (models.value.length) modelId.value = models.value[0].id;
  else {
    modelId.value = null;
    base.value = DEFAULT_BASE;
    adapter.value = "";
  }
}

function onImageChange(e: Event) {
  const inp = e.target as HTMLInputElement;
  imageFile.value = inp.files?.[0] ?? null;
}

async function loadSelectedModel() {
  if (!modelId.value) {
    message.warning("请先在「已登记训练任务」中选择一个模型");
    return;
  }
  loadModelLoading.value = true;
  try {
    await http.post("/api/inference/load", {
      base_model: base.value,
      adapter_path: adapter.value || null,
      model_id: modelId.value,
    });
    message.success("模型已预加载，发送时将复用此缓存");
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "预加载失败");
  } finally {
    loadModelLoading.value = false;
  }
}

async function unloadSelectedModel() {
  unloadModelLoading.value = true;
  try {
    await http.post("/api/inference/unload");
    message.success("已卸载内存中的模型缓存");
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "卸载失败");
  } finally {
    unloadModelLoading.value = false;
  }
}

async function send() {
  loading.value = true;
  reply.value = "";
  try {
    const form = new FormData();
    form.append("base_model", base.value);
    if (adapter.value) form.append("adapter_path", adapter.value);
    if (modelId.value) form.append("model_id", modelId.value);
    form.append("prompt", prompt.value);
    form.append("max_new_tokens", String(maxNew.value));
    if (imageFile.value) form.append("image", imageFile.value);
    const r = await http.post("/api/inference/chat", form);
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
      type="info"
      show-icon
      message="LoRA 在基座之上推理：基座与训练时「模型管理」的 model id 一致。终端里「Loading weights」是把已缓存的基座从磁盘读入内存（首次约数十秒），不是重新联网下模型；同一进程内再次推理会快很多。图片：uploads/playground/，png/jpg/webp/gif/bmp，约 25MB 内。"
      style="margin-bottom: 12px"
    />
    <a-row :gutter="16">
      <a-col :span="10">
        <a-form layout="vertical">
          <a-form-item label="训练成功的任务（LoRA）">
            <a-select
              v-model:value="modelId"
              :options="models.map((m) => ({ value: m.id, label: m.label }))"
              :filter-option="filterTrainingOption"
              show-search
              allow-clear
              style="width: 100%"
            />
          </a-form-item>
          <a-form-item label="基座模型/合并目录（随上项自动填充）">
            <a-input v-model:value="base" disabled />
          </a-form-item>
          <a-form-item label="LoRA 相对路径（随上项自动填充，合并模型为空）">
            <a-input v-model:value="adapter" disabled placeholder="—" />
          </a-form-item>
          <a-space>
            <a-button type="primary" :loading="loadModelLoading" @click="loadSelectedModel">加载模型</a-button>
            <a-button :loading="unloadModelLoading" @click="unloadSelectedModel">卸载模型</a-button>
          </a-space>
          <a-divider />
          <a-form-item label="图片">
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif,image/bmp,.png,.jpg,.jpeg,.webp,.gif,.bmp"
              style="width: 100%"
              @change="onImageChange"
            />
            <div v-if="imageFile" style="margin-top: 6px; color: rgba(0, 0, 0, 0.45); font-size: 12px">
              已选：{{ imageFile.name }}
            </div>
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
