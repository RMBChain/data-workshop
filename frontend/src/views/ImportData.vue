<script setup lang="ts">
import { message, Modal } from "ant-design-vue";
import { QuestionCircleOutlined, ReloadOutlined, SettingOutlined } from "@ant-design/icons-vue";
import { computed, nextTick, onMounted, ref, unref, type Ref } from "vue";
import DataImportProjectList from "../components/DataImportProjectList.vue";
import { apiErrorDetail, formatApiDetail, getApiErrorDetail, http } from "../api/http";

type LsStatus = { url?: string; reachable?: boolean };

type DataImportProjectListExposed = {
  refreshAfterConnectionLoaded: () => Promise<void>;
  loadProjects: () => Promise<void>;
  loadingProjects: Ref<boolean>;
};

type DatasetVersionRow = {
  id: string;
  label_studio_project_id?: number | null;
  name?: string | null;
  status?: string | null;
  train_count?: number | null;
  val_count?: number | null;
  created_at?: string;
  note?: string | null;
  project_title?: string | null;
};

type VersionDataPayload = {
  version_id: string;
  meta: unknown;
  train_jsonl_preview: string;
  val_jsonl_preview: string;
};

function jsonlPreviewLineCount(text: string): number {
  const t = (text ?? "").trim();
  if (!t) return 0;
  return t.split(/\r?\n/).filter((line) => line.trim()).length;
}

function formatJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

const baseUrl = ref("http://host.docker.internal:8080");
const token = ref("");
const testing = ref(false);
const savingConnection = ref(false);
const status = ref<LsStatus>({});
const settingsModalOpen = ref(false);
const projectListRef = ref<DataImportProjectListExposed | null>(null);

const versions = ref<DatasetVersionRow[]>([]);
const activeVersionId = ref<string | null>(null);

const lsUnreachable = computed(() => status.value.reachable === false);

const loadingLsProjects = computed(() => unref(projectListRef.value?.loadingProjects) ?? false);

function refreshProjectList() {
  void projectListRef.value?.loadProjects();
}

const versionNameEditOpen = ref(false);
const versionNameEditId = ref<string | null>(null);
const versionNameEditValue = ref("");
const versionNameSaving = ref(false);

const versionViewOpen = ref(false);
const versionViewLoading = ref(false);
const versionViewTitle = ref("");
const versionViewPayload = ref<VersionDataPayload | null>(null);

function openVersionNameEditor(record: Record<string, unknown>) {
  if (!record?.id) return;
  versionNameEditId.value = String(record.id);
  versionNameEditValue.value = record.name != null ? String(record.name) : "";
  versionNameEditOpen.value = true;
}

async function saveVersionName() {
  const id = versionNameEditId.value;
  if (!id) return;
  const name = (versionNameEditValue.value ?? "").trim();
  if (!name) {
    message.warning("名称不能为空");
    return;
  }
  versionNameSaving.value = true;
  try {
    await http.patch(`/api/datasets/versions/${encodeURIComponent(id)}`, { name });
    message.success("名称已保存");
    versionNameEditOpen.value = false;
    await refreshVersions();
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "保存失败");
  } finally {
    versionNameSaving.value = false;
  }
}

async function openVersionDataView(versionId: string) {
  versionViewTitle.value = `数据集 · ${versionId}`;
  versionViewOpen.value = true;
  versionViewLoading.value = true;
  versionViewPayload.value = null;
  try {
    const r = await http.get(`/api/datasets/versions/${encodeURIComponent(versionId)}/data`, {
      params: { per_split: 8 },
    });
    versionViewPayload.value = r.data as VersionDataPayload;
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "加载失败");
    versionViewOpen.value = false;
  } finally {
    versionViewLoading.value = false;
  }
}

async function promptDeleteVersion(versionId: string) {
  Modal.confirm({
    title: "删除数据集？",
    content:
      "将永久删除该版本对应的 versions/ 目录（含 train/val.jsonl 和 meta.json）及数据库记录。若为当前活跃版本，激活标记将被清除。此操作不可恢复。",
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    async onOk() {
      try {
        const r = await http.delete(`/api/datasets/versions/${encodeURIComponent(versionId)}`);
        if (r.data?.warning) {
          message.warning(String(r.data.warning));
        } else {
          message.success("版本已删除");
        }
        await refreshVersions();
      } catch (e: unknown) {
        message.error(apiErrorDetail(e) ?? "删除失败");
        throw e;
      }
    },
  });
}

async function loadLabelStudioConnection() {
  try {
    const r = await http.get("/api/label-studio/connection");
    const d = r.data as { base_url?: string; token?: string };
    if (typeof d.base_url === "string" && d.base_url.trim()) {
      baseUrl.value = d.base_url.trim().replace(/\/$/, "");
    }
    if (typeof d.token === "string") {
      token.value = d.token;
    }
  } catch {
    /* 保持默认或上次内存中的值 */
  }
}

async function saveLabelStudioConnection() {
  savingConnection.value = true;
  try {
    await http.put("/api/label-studio/connection", {
      base_url: baseUrl.value.trim(),
      token: token.value,
    });
    message.success("连接设置已保存到数据库");
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "保存失败");
  } finally {
    savingConnection.value = false;
  }
}

async function refreshVersions() {
  try {
    const r = await http.get("/api/datasets/versions");
    versions.value = r.data.items as DatasetVersionRow[];
    activeVersionId.value = (r.data.active_version_id as string | null) ?? null;
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "获取数据集列表失败");
  }
}

async function fetchInitialViewData() {
  await loadLabelStudioConnection();
  await refreshConfigStatus();
  await refreshVersions();
  await nextTick();
  await projectListRef.value?.refreshAfterConnectionLoaded();
}

onMounted(() => {
  void fetchInitialViewData();
});

async function refreshConfigStatus() {
  try {
    const r = await http.get("/api/label-studio/status");
    status.value = r.data as LsStatus;
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
  try {
    await http.post("/api/label-studio/test-connection", { base_url: baseUrl.value.trim(), token: token.value.trim() });
    message.success("已连接，Token 有效。");
  } catch (e: unknown) {
    const d = formatApiDetail(getApiErrorDetail(e) ?? "连接失败");
    message.error(d);
  } finally {
    testing.value = false;
  }
}
</script>

<template>
  <div>
    <div class="data-import__page-header">
      <div class="data-import__title-row">
        <div class="data-import__title-refresh">
          <a-typography-title :level="4" class="data-import__title">数据集</a-typography-title>
        </div>
        <a-tooltip title="刷新项目列表" placement="bottomRight" :auto-adjust-overflow="false">
          <a-button
            type="text"
            class="data-import__refresh-btn"
            :loading="loadingLsProjects"
            aria-label="刷新项目列表"
            @click="refreshProjectList"
          >
            <template #icon>
              <ReloadOutlined />
            </template>
          </a-button>
        </a-tooltip>
        <a-tooltip title="Label Studio 连接设置" placement="bottomRight" :auto-adjust-overflow="false">
          <a-button
            type="text"
            class="data-import__settings-btn"
            aria-label="Label Studio 连接设置"
            @click="settingsModalOpen = true"
          >
            <template #icon>
              <SettingOutlined />
            </template>
          </a-button>
        </a-tooltip>
      </div>
    </div>
    <a-divider />
    <a-typography-paragraph v-if="lsUnreachable" type="secondary" style="margin-bottom: 16px">
      未探测到可访问的默认 LS 地址。请用 Docker 在本地启动 Label Studio（见仓库说明），并确认
      <code>WORKSHOP_LABEL_STUDIO_URL</code> 与网络可达性。
    </a-typography-paragraph>

    <DataImportProjectList
      ref="projectListRef"
      :base-url="baseUrl"
      :token="token"
      :versions="versions"
      :active-version-id="activeVersionId"
      @request-refresh-versions="refreshVersions"
      @open-version-view="openVersionDataView"
      @open-version-name-edit="openVersionNameEditor"
      @delete-version="promptDeleteVersion"
    />

    <a-modal
      v-model:open="versionNameEditOpen"
      title="编辑数据集名称"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="versionNameSaving"
      destroy-on-close
      @ok="saveVersionName"
    >
      <a-input
        v-model:value="versionNameEditValue"
        placeholder="版本显示名称"
        allow-clear
        @press-enter="saveVersionName"
      />
    </a-modal>
    <a-modal
      v-model:open="versionViewOpen"
      :title="versionViewTitle"
      width="min(1200px, 96vw)"
      :footer="null"
      destroy-on-close
    >
      <a-spin :spinning="versionViewLoading">
        <a-tabs v-if="versionViewPayload">
          <a-tab-pane key="meta" tab="元数据">
            <pre class="dataset-version-view-pre">{{
              versionViewPayload.meta != null ? formatJson(versionViewPayload.meta) : "（无 meta.json 或无法解析）"
            }}</pre>
          </a-tab-pane>
          <a-tab-pane key="train" :tab="`训练样本 (${jsonlPreviewLineCount(versionViewPayload.train_jsonl_preview)})`">
            <pre class="dataset-version-view-pre">{{
              versionViewPayload.train_jsonl_preview.trim()
                ? versionViewPayload.train_jsonl_preview
                : "（无样本或 train.jsonl 不存在）"
            }}</pre>
          </a-tab-pane>
          <a-tab-pane key="val" :tab="`验证样本 (${jsonlPreviewLineCount(versionViewPayload.val_jsonl_preview)})`">
            <pre class="dataset-version-view-pre">{{
              versionViewPayload.val_jsonl_preview.trim()
                ? versionViewPayload.val_jsonl_preview
                : "（无样本或 val.jsonl 不存在）"
            }}</pre>
          </a-tab-pane>
        </a-tabs>
      </a-spin>
    </a-modal>

    <a-modal v-model:open="settingsModalOpen" title="Label Studio 连接设置" width="min(880px, 96vw)" :footer="null">
      <a-form layout="vertical">
        <a-form-item>
          <template #label>
            <span>
              Label Studio 地址
              <a-tooltip>
                <template #title>
                  后端在 Docker 内时请用 host.docker.internal（或与本机 WORKSHOP_LABEL_STUDIO_URL
                  一致）；仅当 API 与本机进程同机直连 LS 时用 127.0.0.1。
                </template>
                <QuestionCircleOutlined style="margin-left: 6px; color: rgba(0, 0, 0, 0.45); cursor: help" />
              </a-tooltip>
            </span>
          </template>
          <a-textarea
            v-model:value="baseUrl"
            placeholder="http://host.docker.internal:8080"
            :auto-size="{ minRows: 2, maxRows: 4 }"
          />
        </a-form-item>
        <a-form-item>
          <template #label>
            <span>
              API Token
              <a-tooltip>
                <template #title>在 Label Studio 账户/设置中创建。</template>
                <QuestionCircleOutlined style="margin-left: 6px; color: rgba(0, 0, 0, 0.45); cursor: help" />
              </a-tooltip>
            </span>
          </template>
          <a-textarea v-model:value="token" :rows="4" />
        </a-form-item>
        <a-form-item style="margin-top: 24px">
          <a-space>
            <a-button type="primary" :loading="savingConnection" @click="saveLabelStudioConnection">保存</a-button>
            <a-button :loading="testing" @click="testConnection">测试连接</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.data-import__page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
}
.data-import__page-header :deep(.ant-tooltip) {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
}
.data-import__title {
  margin: 0;
}
.data-import__title-refresh {
  display: inline-flex;
  align-items: center;
  column-gap: 0;
  min-width: 0;
  flex: 0 1 auto;
}
.data-import__title-refresh :deep(h4) {
  margin: 0;
  line-height: 1.3;
}
.data-import__title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}
.data-import__refresh-btn,
.data-import__settings-btn {
  flex-shrink: 0;
  font-size: 18px;
  color: rgba(0, 0, 0, 0.45);
}
.data-import__refresh-btn:hover,
.data-import__settings-btn:hover {
  color: var(--ant-primary-color, #1677ff);
}

.dataset-version-view-pre {
  margin: 0;
  max-height: 70vh;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
