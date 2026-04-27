<script setup lang="ts">
import { message } from "ant-design-vue";
import { QuestionCircleOutlined, SettingOutlined } from "@ant-design/icons-vue";
import { computed, nextTick, onMounted, ref } from "vue";
import DataImportGlobalPreview from "../components/DataImportGlobalPreview.vue";
import DataImportProjectList from "../components/DataImportProjectList.vue";
import { apiErrorDetail, formatApiDetail, getApiErrorDetail, http } from "../api/http";

type LsStatus = { url?: string; reachable?: boolean };

type DataImportProjectListExposed = {
  refreshAfterConnectionLoaded: () => Promise<void>;
};

const baseUrl = ref("http://host.docker.internal:8080");
const token = ref("");
const testing = ref(false);
const savingConnection = ref(false);
const status = ref<LsStatus>({});
const settingsModalOpen = ref(false);
const projectListRef = ref<DataImportProjectListExposed | null>(null);

const lsUnreachable = computed(() => status.value.reachable === false);

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

async function fetchInitialViewData() {
  await loadLabelStudioConnection();
  await refreshConfigStatus();
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
          <a-typography-title :level="4" class="data-import__title">数据导入</a-typography-title>
        </div>
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

    <DataImportProjectList ref="projectListRef" :base-url="baseUrl" :token="token" />
    <DataImportGlobalPreview />

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
.data-import__settings-btn {
  flex-shrink: 0;
  font-size: 18px;
  color: rgba(0, 0, 0, 0.45);
}
.data-import__settings-btn:hover {
  color: var(--ant-primary-color, #1677ff);
}
</style>
