<script setup lang="ts">
import { message } from "ant-design-vue";
import { onMounted, reactive, ref } from "vue";
import { http } from "../api/http";

/** 与 backend Settings.label_studio_url 默认一致；容器内访问宿主机 LS 需 host.docker.internal */
const baseUrl = ref("http://host.docker.internal:8080");
const token = ref("");
const testMsg = ref("");
const testMsgOk = ref(true);
const testing = ref(false);
const projects = ref<{ id: number; title: string; task_number: number }[]>([]);
const projectId = ref<number | null>(null);
const loadingProjects = ref(false);
const importing = ref(false);
const lastBatch = ref<string | null>(null);
const status = ref<Record<string, unknown>>({});
const tasks = ref<unknown[]>([]);
const page = ref(1);
const total = ref(0);
const form = reactive({ page_size: 20 });

onMounted(() => {
  void refreshConfigStatus();
});

function formatApiDetail(detail: unknown): string {
  if (detail == null) return "连接失败";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item: { msg?: string; loc?: unknown[] }) => {
        const loc = Array.isArray(item.loc) ? item.loc.filter((x) => x !== "body").join(".") : "";
        return loc ? `${loc}: ${item.msg ?? ""}` : (item.msg ?? JSON.stringify(item));
      })
      .join("；");
  }
  return String(detail);
}

async function refreshConfigStatus() {
  try {
    const r = await http.get("/api/label-studio/status");
    status.value = r.data;
    const u = r.data?.url;
    if (typeof u === "string" && u.trim()) {
      baseUrl.value = u.trim().replace(/\/$/, "");
    }
  } catch {
    /* ignore */
  }
}

async function testConnection() {
  if (!baseUrl.value.trim()) {
    message.warning("请填写 Label Studio 基址");
    return;
  }
  if (!token.value.trim()) {
    message.warning("请填写 API Token 后再测试");
    return;
  }
  testing.value = true;
  testMsg.value = "";
  testMsgOk.value = true;
  try {
    await http.post("/api/label-studio/test-connection", { base_url: baseUrl.value.trim(), token: token.value.trim() });
    testMsg.value = "连接成功，Token 有效。";
    testMsgOk.value = true;
    message.success("已连接");
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: unknown } } };
    const d = formatApiDetail(err.response?.data?.detail ?? "连接失败");
    testMsg.value = d;
    testMsgOk.value = false;
    message.error(d);
  } finally {
    testing.value = false;
  }
}

async function loadProjects() {
  if (!token.value.trim()) {
    message.warning("请填写 Token");
    return;
  }
  loadingProjects.value = true;
  try {
    const r = await http.get("/api/label-studio/projects", {
      params: { base_url: baseUrl.value, token: token.value },
    });
    projects.value = r.data.items;
    if (projects.value.length && projectId.value == null) {
      projectId.value = projects.value[0].id;
    }
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "获取项目失败");
  } finally {
    loadingProjects.value = false;
  }
}

async function doImport() {
  if (projectId.value == null) {
    message.warning("请选择项目");
    return;
  }
  importing.value = true;
  try {
    const r = await http.post("/api/label-studio/import", {
      project_id: projectId.value,
      base_url: baseUrl.value,
      token: token.value,
    });
    lastBatch.value = r.data.import_batch_id;
    message.success(`已导入 ${r.data.task_count} 条任务`);
    page.value = 1;
    await loadTasks();
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } };
    message.error(err.response?.data?.detail ?? "导入失败");
  } finally {
    importing.value = false;
  }
}

async function loadTasks() {
  if (!lastBatch.value) return;
  const r = await http.get(`/api/imports/${lastBatch.value}/tasks`, {
    params: { page: page.value, page_size: form.page_size },
  });
  tasks.value = (r.data.items as Record<string, unknown>[]).map((row) => ({
    ...row,
    resolved_label: (row as { resolved?: number }).resolved ? "是" : "否",
  }));
  total.value = r.data.total;
}

function onTableChange(p: number) {
  page.value = p;
  void loadTasks();
}

const taskColumns = [
  { title: "任务 ID", dataIndex: "ls_task_id", key: "ls_task_id", width: 100 },
  { title: "图片路径", dataIndex: "image_rel", key: "image_rel", ellipsis: true },
  { title: "已解析", dataIndex: "resolved_label", key: "resolved_label", width: 100 },
];

const sftJsonExample = `{
  "images": ["/path/to/image.jpg"],
  "conversations": [
    {"from": "human", "value": "<image>这张图里有什么？"},
    {"from": "gpt", "value": "图中有一只猫坐在沙发上。"}
  ]
}`;
const messageJsonExample = `{"messages": [{"role": "user", "content": [{"type": "image", "image": "data/cable-010.png"}, {"type": "text", "text": "请描述。"}]}]}`;
</script>

<template>
  <div>
    <a-typography-title :level="4">数据导入</a-typography-title>
    <a-alert
      type="info"
      show-icon
      message="通过 Label Studio API 拉取项目；原图在 LS 侧，本处保存元数据与工作区内可解析路径。"
      style="margin-bottom: 16px"
    />
    <a-typography-paragraph
      v-if="!(status as { reachable?: boolean }).reachable"
      type="secondary"
    >
      未探测到可访问的默认 LS 地址。请用 Docker
      在本地启动 Label Studio（见仓库说明），并确认
      <code>WORKSHOP_LABEL_STUDIO_URL</code> 与网络可达性。
    </a-typography-paragraph>
    <a-form layout="vertical" style="max-width: 720px">
      <a-form-item
        label="Label Studio 基址"
        extra="后端在 Docker 内时请用 host.docker.internal（或与本机 WORKSHOP_LABEL_STUDIO_URL 一致）；仅当 API 与本机进程同机直连 LS 时用 127.0.0.1。"
      >
        <a-input v-model:value="baseUrl" placeholder="http://host.docker.internal:8080" />
      </a-form-item>
      <a-form-item label="API Token" extra="在 Label Studio 账户/设置中创建。">
        <a-input-password v-model:value="token" />
      </a-form-item>
      <a-form-item>
        <a-space>
          <a-button :loading="testing" @click="testConnection">测试连接</a-button>
          <a-button :loading="loadingProjects" @click="loadProjects">刷新项目列表</a-button>
        </a-space>
        <a-alert
          v-if="testMsg"
          :message="testMsg"
          :type="testMsgOk ? 'success' : 'error'"
          show-icon
          style="margin-top: 8px"
        />
      </a-form-item>
      <a-form-item label="项目">
        <a-select
          v-model:value="projectId"
          :options="projects.map((p) => ({ value: p.id, label: `${p.id} — ${p.title}（约 ${p.task_number} 任务）` }))"
          style="width: 100%"
          placeholder="先刷新项目列表"
        />
      </a-form-item>
      <a-form-item>
        <a-button type="primary" :loading="importing" :disabled="projectId == null" @click="doImport"
          >拉取并导入</a-button
        >
      </a-form-item>
    </a-form>

    <a-collapse v-if="lastBatch" style="margin-top: 16px">
      <a-collapse-panel key="1" :header="`最近批次：${lastBatch} — 任务预览`">
        <a-table
          :columns="taskColumns"
          :data-source="tasks as Record<string, unknown>[]"
          :pagination="{
            current: page,
            pageSize: form.page_size,
            total: total,
            onChange: onTableChange,
          }"
          row-key="id"
          size="small"
          :scroll="{ x: 900 }"
        />
      </a-collapse-panel>
    </a-collapse>
    <a-collapse style="margin-top: 16px">
      <a-collapse-panel key="2" header="SFT 字段示例（只读，与需求文档一致）">
        <a-typography-paragraph>
          <pre style="font-size: 12px; overflow: auto; white-space: pre-wrap">{{ sftJsonExample }}</pre>
        </a-typography-paragraph>
        <a-typography-paragraph>messages 多模态样例</a-typography-paragraph>
        <a-typography-paragraph>
          <pre style="font-size: 12px; overflow: auto; white-space: pre-wrap">{{ messageJsonExample }}</pre>
        </a-typography-paragraph>
      </a-collapse-panel>
    </a-collapse>
  </div>
</template>
