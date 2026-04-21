<script setup lang="ts">
import { message } from "ant-design-vue";
import { ref } from "vue";
import { http } from "../api/http";

const target = ref("data/uploaded.jsonl");
const uploading = ref(false);

async function beforeUpload(file: File) {
  uploading.value = true;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("target", target.value);
  try {
    const r = await http.post<{ path: string; bytes: number }>("/api/data/upload-jsonl", fd);
    message.success(`已保存到 ${r.data.path}（${r.data.bytes} 字节）`);
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? String(e));
  } finally {
    uploading.value = false;
  }
  return false;
}
</script>

<template>
  <div>
    <a-typography-title :level="4">数据导入</a-typography-title>
    <a-typography-paragraph>上传 JSONL 到工作区，供训练使用（相对仓库根路径）。</a-typography-paragraph>
    <a-space direction="vertical" style="width: 100%; max-width: 560px" size="large">
      <a-form layout="vertical">
        <a-form-item label="保存为（相对路径）">
          <a-input v-model:value="target" placeholder="例如 data/train.jsonl" />
        </a-form-item>
        <a-form-item label="选择文件">
          <a-upload :before-upload="beforeUpload" :show-upload-list="false" accept=".jsonl">
            <a-button type="primary" :loading="uploading">上传 JSONL</a-button>
          </a-upload>
        </a-form-item>
      </a-form>
    </a-space>
  </div>
</template>
