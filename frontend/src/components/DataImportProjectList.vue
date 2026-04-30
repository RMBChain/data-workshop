<script setup lang="ts">
import { message } from "ant-design-vue";
import { EditOutlined, ReloadOutlined } from "@ant-design/icons-vue";
import { reactive, ref } from "vue";
import { apiErrorDetail, http } from "../api/http";

type DatasetVersionRow = {
  id: string;
  label_studio_project_id?: number | null;
  name?: string | null;
  train_count?: number | null;
  val_count?: number | null;
  created_at?: string;
  project_title?: string | null;
};

type LsProject = { id: number; title: string; task_number: number };

const props = defineProps<{
  baseUrl: string;
  token: string;
  versions: DatasetVersionRow[];
  activeVersionId?: string | null;
}>();

const emit = defineEmits<{
  requestRefreshVersions: [];
  openVersionView: [versionId: string];
  openVersionNameEdit: [record: Record<string, unknown>];
  deleteVersion: [versionId: string];
}>();

const loadingProjects = ref(false);
const projects = ref<LsProject[]>([]);
const selectedProjectId = ref<number | null>(null);
const creatingDatasetIds = reactive(new Set<number>());
const selectedVersionId = ref<string | null>(null);

const POLL_MS = 2500;
const POLL_MAX_MS = 60 * 60 * 1000;

/** 与后端 `_default_dataset_version_name` 中的时间戳格式对齐（本地时间） */
function formatLocalTs(d = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

type CreateDatasetFormValues = {
  versionName: string | null;
  trainRatio: number;
  valRatio: number;
  randomSeed: number | null;
  note: string | null;
  addImageToken: boolean;
};

const createDatasetModalOpen = ref(false);
const createModalProject = ref<LsProject | null>(null);
const createVersionName = ref("");
const createTrainRatio = ref(80);
const createValRatio = ref(20);
const createRandomSeed = ref<number | null>(null);
const createNote = ref("");
const createAddImageToken = ref(true);

function projectVersions(projectId: number): DatasetVersionRow[] {
  return props.versions.filter(
    (v) => v.label_studio_project_id != null && Number(v.label_studio_project_id) === Number(projectId),
  );
}

const datasetColumns = [
  { title: "数据集名称", dataIndex: "name", key: "name", ellipsis: true, width: 160 },
  { title: "训练", dataIndex: "train_count", key: "train_count", width: 56 },
  { title: "验证", dataIndex: "val_count", key: "val_count", width: 56 },
  { title: "生成时间", dataIndex: "created_at", key: "created_at", width: 160 },
  { title: "操作", key: "action", width: 120 },
];

async function loadProjects() {
  if (!props.token.trim()) {
    message.warning("请填写 Token");
    return;
  }
  loadingProjects.value = true;
  try {
    const r = await http.get("/api/label-studio/projects", {
      params: { base_url: props.baseUrl, token: props.token },
    });
    const items: LsProject[] = r.data.items || [];
    projects.value = items;
    if (selectedProjectId.value != null && !items.some((p) => p.id === selectedProjectId.value)) {
      selectedProjectId.value = null;
    }
  } catch (e: unknown) {
    message.error(apiErrorDetail(e) ?? "获取项目失败");
  } finally {
    loadingProjects.value = false;
  }
}

function openCreateDatasetModal(project: LsProject) {
  createModalProject.value = project;
  const title =
    project.title != null && String(project.title).trim() ? String(project.title).trim() : "未命名项目";
  createVersionName.value = `${title}-${formatLocalTs()}`;
  createTrainRatio.value = 80;
  createValRatio.value = 20;
  createRandomSeed.value = null;
  createNote.value = "";
  createAddImageToken.value = true;
  createDatasetModalOpen.value = true;
}

function onTrainRatioChange(v: number | string | null) {
  const t = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(t)) return;
  const clamped = Math.min(100, Math.max(1, Math.round(t)));
  createTrainRatio.value = clamped;
  createValRatio.value = 100 - clamped;
}

function onValRatioChange(v: number | string | null) {
  const val = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(val)) return;
  const clamped = Math.min(99, Math.max(0, Math.round(val)));
  createValRatio.value = clamped;
  createTrainRatio.value = 100 - clamped;
  if (createTrainRatio.value < 1) {
    createTrainRatio.value = 1;
    createValRatio.value = 99;
  }
}

function handleCreateDatasetModalOk() {
  const pid = createModalProject.value?.id;
  if (pid == null) return Promise.reject(new Error("no project"));
  const tr = createTrainRatio.value;
  const vr = createValRatio.value;
  if (tr + vr !== 100) {
    message.warning("训练集与验证集比例之和须为 100");
    return Promise.reject(new Error("validation"));
  }
  if (tr < 1 || tr > 100 || vr < 0 || vr > 100) {
    message.warning("比例取值无效（训练集至少 1%）");
    return Promise.reject(new Error("validation"));
  }

  const rawName = createVersionName.value.trim();
  const params: CreateDatasetFormValues = {
    versionName: rawName.length ? rawName : null,
    trainRatio: tr,
    valRatio: vr,
    randomSeed: createRandomSeed.value,
    note: createNote.value.trim() ? createNote.value.trim() : null,
    addImageToken: createAddImageToken.value,
  };

  createDatasetModalOpen.value = false;
  createModalProject.value = null;

  void createDatasetFromLsProject(pid, params);
}

async function createDatasetFromLsProject(projectId: number, form: CreateDatasetFormValues) {
  if (creatingDatasetIds.has(projectId)) return;
  if (!props.token.trim()) {
    message.warning("请填写 Token");
    return;
  }
  creatingDatasetIds.add(projectId);
  selectedProjectId.value = projectId;
  try {
    const ir = await http.post("/api/label-studio/import", {
      project_id: projectId,
      base_url: props.baseUrl,
      token: props.token,
    });
    const lsImportId = ir.data.ls_import_id as string;
    const tc = Number(ir.data.task_count ?? 0);
    message.success(`已从 Label Studio 拉取 ${tc} 条标注，正在生成数据集…`);

    const br = await http.post("/api/datasets/build", {
      ls_import_id: lsImportId,
      add_image_token: form.addImageToken,
      train_ratio: form.trainRatio,
      val_ratio: form.valRatio,
      random_seed: form.randomSeed,
      note: form.note,
      version_name: form.versionName,
    });
    const jobId = br.data.job_id as string;

    const buildPollStart = Date.now();
    for (;;) {
      if (Date.now() - buildPollStart > POLL_MAX_MS) {
        message.warning("构建状态长时间未结束，请稍后在本页刷新「数据集」列表查看。");
        break;
      }
      const st = await http.get(`/api/datasets/jobs/${jobId}`);
      const status = String(st.data.status);
      if (["succeeded", "failed", "cancelled"].includes(status)) {
        if (status === "succeeded") {
          message.success("数据集已生成");
          emit("requestRefreshVersions");
        } else if (status === "failed") {
          const em = (st.data as { error_message?: string }).error_message;
          message.error(em && String(em).trim() ? em : "数据集构建失败");
        }
        break;
      }
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
  } catch (e: unknown) {
    const code = (e as { response?: { status?: number } }).response?.status;
    if (code === 404) {
      message.error("构建任务已不存在");
    } else {
      message.error(apiErrorDetail(e) ?? "操作失败");
    }
  } finally {
    creatingDatasetIds.delete(projectId);
  }
}

function selectVersion(versionId: string) {
  selectedVersionId.value = versionId;
}

function onDatasetTableRow(r: DatasetVersionRow) {
  return { onClick: () => r?.id != null && selectVersion(String(r.id)) };
}

function datasetRowClassName(record: { id?: string }) {
  const vid = props.activeVersionId;
  const sid = selectedVersionId.value;
  if (record?.id != null && vid != null && String(record.id) === String(vid)) {
    return "import-dataset-table__row--active";
  }
  if (sid == null || record?.id == null) return "";
  return String(record.id) === String(sid) ? "import-dataset-table__row--selected" : "";
}

async function refreshAfterConnectionLoaded() {
  emit("requestRefreshVersions");
  if (props.token.trim()) {
    await loadProjects();
  }
}

defineExpose({ refreshAfterConnectionLoaded });
</script>

<template>
  <div class="data-import-project-list">
    <a-typography-title :level="5" class="data-import-project-list__title">
      项目列表
      <a-tooltip title="刷新项目列表" placement="bottomRight" :auto-adjust-overflow="false">
        <a-button
          type="text"
          class="data-import-project-list__refresh-btn"
          :loading="loadingProjects"
          aria-label="刷新项目列表"
          @click="loadProjects"
        >
          <template #icon>
            <ReloadOutlined />
          </template>
        </a-button>
      </a-tooltip>
    </a-typography-title>
    <a-spin :spinning="loadingProjects">
      <div v-if="projects.length" class="import-project-card-grid">
        <a-card
          v-for="record in projects"
          :key="record.id"
          class="import-project-card"
          :class="{
            'import-project-card--selected':
              selectedProjectId != null && Number(record.id) === Number(selectedProjectId),
          }"
          hoverable
          @click="selectedProjectId = record.id"
        >
          <template #title>
            <span
              class="import-project-card-title"
              :title="record.title != null && String(record.title).trim() ? String(record.title) : undefined"
            >
              {{ record.title != null && String(record.title).trim() ? record.title : "—" }}
            </span>
            <div class="import-project-card-meta">
              约 <span class="import-project-card-meta-number">{{ record.task_number }}</span> 任务
            </div>
          </template>
          <template #extra>
            <a-button type="primary" :loading="creatingDatasetIds.has(record.id)" @click.stop="openCreateDatasetModal(record)">
              新建数据集
            </a-button>
          </template>

          <a-table
            v-if="projectVersions(record.id).length"
            class="import-dataset-table"
            :columns="datasetColumns"
            :data-source="projectVersions(record.id)"
            :pagination="false"
            size="small"
            row-key="id"
            :row-class-name="datasetRowClassName"
            :custom-row="onDatasetTableRow"
          >
            <template #bodyCell="{ column, record: vr }">
              <template v-if="column.key === 'name'">
                <span class="import-dataset-name-cell" @click.stop>
                  <span
                    class="import-dataset-name-text"
                    :title="vr?.name != null && String(vr.name) ? String(vr.name) : undefined"
                  >
                    {{ vr?.name != null && String(vr.name) ? vr.name : "—" }}
                  </span>
                  <EditOutlined
                    class="import-dataset-name-edit"
                    @click="emit('openVersionNameEdit', vr as Record<string, unknown>)"
                  />
                </span>
              </template>
              <template v-else-if="column.key === 'action'">
                <a-space size="small" @click.stop>
                  <a-button type="link" size="small" @click="emit('openVersionView', String(vr.id))">查看</a-button>
                  <a-button danger type="link" size="small" @click="emit('deleteVersion', String(vr.id))">
                    删除
                  </a-button>
                </a-space>
              </template>
              <span v-else>{{
                column.dataIndex == null
                  ? "—"
                  : (vr as Record<string, unknown>)?.[String(column.dataIndex)] ?? "—"
              }}</span>
            </template>
          </a-table>
          <a-typography-paragraph v-else type="secondary" style="margin-top: 8px"
            >尚无数据集，点击「新建数据集」从本项目生成。</a-typography-paragraph
          >
        </a-card>
      </div>
    </a-spin>

    <a-typography-paragraph v-if="!loadingProjects && !projects.length" type="secondary">
      请先在右上角打开连接设置，填写地址与 Token，再点击「刷新项目列表」加载 Label Studio 中的项目
    </a-typography-paragraph>

    <a-modal
      v-model:open="createDatasetModalOpen"
      title="新建数据集"
      ok-text="开始生成"
      cancel-text="取消"
      width="min(600px, 96vw)"
      :destroy-on-close="true"
      @ok="handleCreateDatasetModalOk"
    >
      <a-form layout="vertical" class="import-create-dataset-form">
        <a-form-item label="数据集名称">
          <a-input
            v-model:value="createVersionName"
            placeholder="留空则使用「项目名-时间戳」自动生成"
            allow-clear
            :maxlength="500"
            show-count
          />
        </a-form-item>
        <a-form-item label="划分比例（训练 / 验证）" extra="两项相加须为 100%；训练集至少 1%。">
          <a-space align="center" wrap>
            <span>训练</span>
            <a-input-number
              v-model:value="createTrainRatio"
              :min="1"
              :max="100"
              addon-after="%"
              @change="onTrainRatioChange"
            />
            <span>验证</span>
            <a-input-number
              v-model:value="createValRatio"
              :min="0"
              :max="99"
              addon-after="%"
              @change="onValRatioChange"
            />
          </a-space>
        </a-form-item>
        <a-form-item label="随机种子" extra="可选，固定后多次划分结果一致；留空则每次随机。">
          <a-input-number v-model:value="createRandomSeed" placeholder="留空为随机" style="width: 100%" allow-clear />
        </a-form-item>
        <a-form-item label="备注">
          <a-textarea v-model:value="createNote" placeholder="写入 meta / 版本备注（可选）" :rows="2" allow-clear />
        </a-form-item>
        <a-form-item label="在对话中加入图像占位符 &lt;image&gt;">
          <a-switch v-model:checked="createAddImageToken" checked-children="开" un-checked-children="关" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.data-import-project-list__title {
  margin: 24px 0 12px;
  display: flex;
  align-items: center;
  gap: 0;
  flex-wrap: wrap;
}
.data-import-project-list__title :deep(h5) {
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.data-import-project-list__refresh-btn {
  flex-shrink: 0;
  font-size: 18px;
  color: rgba(0, 0, 0, 0.45);
  margin-left: 4px;
}
.data-import-project-list__refresh-btn:hover {
  color: var(--ant-primary-color, #1677ff);
}
.data-import-project-list {
  width: 100%;
  max-width: none;
}
.import-project-card-grid {
  display: grid;
  gap: 16px;
  margin-top: 8px;
  grid-template-columns: 1fr;
}
@media (min-width: 520px) {
  .import-project-card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.import-project-card {
  min-width: 0;
  cursor: pointer;
  border-radius: 12px;
  border: 1px solid rgba(0, 0, 0, 0.14);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.1);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  background: color-mix(
    in srgb,
    var(--ant-color-fill-tertiary, #f0f0f0) 72%,
    var(--ant-color-fill-secondary, #e6e6e6) 28%
  );
}
.import-project-card :deep(.ant-card-head) {
  min-height: 56px;
  padding: 0 20px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--ant-color-fill-secondary, #e6e6e6) 55%, transparent) 0%,
    color-mix(in srgb, var(--ant-color-fill-tertiary, #f0f0f0) 35%, transparent) 100%
  );
}
.import-project-card :deep(.ant-card-head-title) {
  padding: 14px 0;
  min-width: 0;
}
.import-project-card :deep(.ant-card-extra) {
  padding: 14px 0;
}
.import-project-card :deep(.ant-card-body) {
  padding: 16px 20px 18px;
  background: color-mix(in srgb, var(--ant-color-fill-tertiary, #f0f0f0) 88%, var(--ant-color-bg-container, #fff) 12%);
}
.import-project-card-title {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  font-size: 16px;
  font-weight: 600;
  color: rgba(0, 0, 0, 0.88);
  letter-spacing: 0.01em;
}
.import-project-card--selected {
  border-color: var(--ant-primary-color, #1677ff);
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--ant-primary-color, #1677ff) 45%, transparent),
    0 8px 22px rgba(22, 119, 255, 0.2);
}
.import-project-card-meta {
  font-size: 14px;
  color: rgba(0, 0, 0, 0.55);
  line-height: 1.5;
}
.import-project-card-meta-number {
  font-weight: 700;
  font-size: 18px;
  font-variant-numeric: tabular-nums;
  color: rgba(0, 0, 0, 0.88);
}
.import-dataset-table :deep(.ant-table-tbody > tr) {
  cursor: pointer;
}
.import-dataset-table :deep(.import-dataset-table__row--selected > td) {
  background: color-mix(in srgb, var(--ant-primary-color, #1677ff) 12%, var(--ant-color-bg-container, #fff));
}
.import-dataset-table :deep(.import-dataset-table__row--selected:hover > td) {
  background: color-mix(in srgb, var(--ant-primary-color, #1677ff) 20%, var(--ant-color-bg-container, #fff));
}
.import-dataset-table :deep(.import-dataset-table__row--active > td) {
  background: color-mix(in srgb, var(--ant-color-success, #52c41a) 14%, var(--ant-color-bg-container, #fff));
}
.import-dataset-table :deep(.import-dataset-table__row--active:hover > td) {
  background: color-mix(in srgb, var(--ant-color-success, #52c41a) 22%, var(--ant-color-bg-container, #fff));
}
.import-dataset-name-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
}
.import-dataset-name-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.import-dataset-name-edit {
  flex-shrink: 0;
  color: rgba(0, 0, 0, 0.45);
  cursor: pointer;
  font-size: 14px;
}
.import-dataset-name-edit:hover {
  color: var(--ant-primary-color, #1677ff);
}
</style>
