<script setup lang="ts">
import { DeleteOutlined } from "@ant-design/icons-vue";
import { message } from "ant-design-vue";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
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
const IMAGE_ACCEPT =
  "image/png,image/jpeg,image/webp,image/gif,image/bmp,.png,.jpg,.jpeg,.webp,.gif,.bmp";
const fileInputRef = ref<HTMLInputElement | null>(null);
const imageFile = ref<File | null>(null);
const imageObjectUrl = ref<string | null>(null);
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

function revokeImageObjectUrl() {
  if (imageObjectUrl.value) {
    URL.revokeObjectURL(imageObjectUrl.value);
    imageObjectUrl.value = null;
  }
}

function isValidImageFile(f: File): boolean {
  const allowed = new Set([
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/bmp",
  ]);
  if (f.type && allowed.has(f.type)) return true;
  const ext = f.name.toLowerCase().match(/\.([^.]+)$/)?.[1];
  return ext != null && ["png", "jpg", "jpeg", "webp", "gif", "bmp"].includes(ext);
}

function setImageFile(f: File | null) {
  revokeImageObjectUrl();
  imageFile.value = f;
  if (f) imageObjectUrl.value = URL.createObjectURL(f);
}

function applyImageFromFileList(files: FileList | null) {
  if (!files?.length) return;
  const f = files[0];
  if (!isValidImageFile(f)) {
    message.error("请使用 png、jpg、webp、gif 或 bmp 图片");
    return;
  }
  setImageFile(f);
}

function triggerFileInput() {
  fileInputRef.value?.click();
}

function onImageChange(e: Event) {
  const inp = e.target as HTMLInputElement;
  applyImageFromFileList(inp.files);
  inp.value = "";
}

function onDropImage(e: DragEvent) {
  e.preventDefault();
  applyImageFromFileList(e.dataTransfer?.files ?? null);
}

function onDragOverImage(e: DragEvent) {
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
}

function clearImage() {
  setImageFile(null);
  if (fileInputRef.value) fileInputRef.value.value = "";
}

onMounted(() => {
  void loadModels();
});

onBeforeUnmount(() => {
  revokeImageObjectUrl();
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
    <a-typography-title :level="4">LoRA 验证</a-typography-title>
    <a-alert
      type="info"
      show-icon
      message="LoRA 在基座之上推理。终端里「Loading weights」是把已缓存的基座从磁盘读入内存（首次约数十秒），不是重新联网下模型；同一进程内再次推理会快很多。"
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
          <a-row :gutter="16" class="playground-image-token-row">
            <a-col :span="12">
              <a-form-item label="图片（单张，可拖拽或点击选择）">
                <input
                  ref="fileInputRef"
                  type="file"
                  class="playground-image-input"
                  :accept="IMAGE_ACCEPT"
                  @change="onImageChange"
                />
                <div
                  class="playground-image-drop"
                  :class="{ 'playground-image-drop--filled': imageFile }"
                  @dragover="onDragOverImage"
                  @drop="onDropImage"
                >
                  <template v-if="!imageFile">
                    <div class="playground-image-empty" @click="triggerFileInput">
                      <span class="playground-image-empty__hint">将图片拖到这里，或点击选择</span>
                      <span class="playground-image-empty__sub">仅 1 张，png / jpg / webp / gif / bmp，约 25MB 内</span>
                    </div>
                  </template>
                  <div v-else class="playground-image-filled">
                    <a-image
                      v-if="imageObjectUrl"
                      class="playground-image-thumb"
                      :src="imageObjectUrl"
                      :width="96"
                      :height="96"
                      alt=""
                      :preview="true"
                    />
                    <div class="playground-image-filled__meta">
                      <div class="playground-image-filled__name" :title="imageFile.name">
                        {{ imageFile.name }}
                      </div>
                      <a-space :size="4" wrap>
                        <a-button type="link" size="small" class="playground-image-filled__change" @click="triggerFileInput">
                          更换
                        </a-button>
                        <a-button
                          type="link"
                          danger
                          size="small"
                          class="playground-image-filled__remove"
                          @click="clearImage"
                        >
                          <template #icon><DeleteOutlined /></template>
                          删除
                        </a-button>
                      </a-space>
                    </div>
                  </div>
                </div>
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="max_new_tokens">
                <a-input-number v-model:value="maxNew" :min="8" :max="4096" style="width: 100%" />
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item label="提示词">
            <a-textarea v-model:value="prompt" :rows="4" />
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

<style scoped>
.playground-image-token-row {
  width: 100%;
}

.playground-image-input {
  position: absolute;
  width: 0;
  height: 0;
  opacity: 0;
  pointer-events: none;
}

.playground-image-drop {
  box-sizing: border-box;
  min-height: 120px;
  border: 1px dashed #d9d9d9;
  border-radius: 6px;
  background: #fafafa;
  transition: border-color 0.2s, background 0.2s;
}

.playground-image-drop:not(.playground-image-drop--filled) {
  cursor: pointer;
}

.playground-image-drop:not(.playground-image-drop--filled):hover {
  border-color: #1677ff;
  background: #f0f5ff;
}

.playground-image-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 120px;
  padding: 16px;
  user-select: none;
}

.playground-image-empty__hint {
  color: rgba(0, 0, 0, 0.88);
  font-size: 14px;
}

.playground-image-empty__sub {
  color: rgba(0, 0, 0, 0.45);
  font-size: 12px;
  text-align: center;
}

.playground-image-filled {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
}

.playground-image-thumb :deep(.ant-image-img) {
  object-fit: cover;
  border-radius: 4px;
}

.playground-image-filled__meta {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
  flex: 1;
}

.playground-image-filled__name {
  color: rgba(0, 0, 0, 0.65);
  font-size: 12px;
  line-height: 1.4;
  word-break: break-all;
}

.playground-image-filled__remove {
  padding: 0;
  height: auto;
  align-self: flex-start;
}
</style>
