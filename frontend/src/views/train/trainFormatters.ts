import type { MergeUiStatus, TrainJobMetaRow } from "./trainTypes";
import { MERGE_OUTPUT_ROOT } from "./trainTypes";

export function parseMergeUiStatus(v: string | undefined): MergeUiStatus {
  if (v === "merging" || v === "interrupted" || v === "failed" || v === "success" || v === "none") {
    return v;
  }
  return "none";
}

export function formatJobTime(t: unknown): string {
  if (t == null || t === "") return "—";
  const n = typeof t === "number" ? t : Number(t);
  if (Number.isNaN(n)) return "—";
  const d = new Date(n * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("zh-CN", { hour12: false });
}

export function formatJobEnd(finished: unknown, status: string | undefined): string {
  if (finished != null && finished !== "") {
    return formatJobTime(finished);
  }
  if (status === "running" || status === "pending") {
    return "未结束";
  }
  return "—";
}

/** 与后端 `TrainJob.status` 对齐，表格展示用中文 */
export function formatJobStatus(status: unknown): string {
  const s = typeof status === "string" ? status.trim() : String(status ?? "").trim();
  if (s === "parameters_saved") return "未训练";
  if (s === "running") return "训练中";
  if (s === "failed") return "训练失败";
  if (s === "cancelled") return "训练取消";
  if (s === "succeeded") return "训练成功";
  return s || "—";
}

/** 与数据集版本列表 `train_count` / `val_count` 一致；未知时「—」 */
export function formatJobSplitCount(v: unknown): string {
  if (v == null || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (Number.isNaN(n)) return "—";
  return String(n);
}

/** 工作区相对路径 → 后端工作区根下的绝对路径（用于 Tooltip）；无根信息时退回相对路径 */
export function workspaceAbsoluteDisplayPath(relOrDash: string, workspaceRootAbs: string | null): string {
  const raw = (relOrDash ?? "").trim();
  if (!raw || raw === "—") return raw || "—";
  const norm = raw.replace(/\\/g, "/");
  if (norm.startsWith("/") || /^[A-Za-z]:\//.test(norm)) {
    return norm;
  }
  const root = (workspaceRootAbs ?? "").trim();
  if (!root) return norm;
  const rn = root.replace(/\\/g, "/").replace(/\/+$/, "");
  const pn = norm.replace(/^\/+/, "");
  return `${rn}/${pn}`;
}

export function mergeStatusTagColor(s: MergeUiStatus): string {
  if (s === "none") return "default";
  if (s === "merging") return "processing";
  if (s === "interrupted") return "warning";
  if (s === "failed") return "error";
  return "success";
}

export function mergeOutputRelForTrainingJob(trainingJobId: string): string {
  const tid = trainingJobId.trim();
  if (!tid) return MERGE_OUTPUT_ROOT;
  return `${MERGE_OUTPUT_ROOT}/${tid}`.replace(/\\/g, "/");
}

export function mergeMergedOutputPathText(
  st: MergeUiStatus,
  apiPath: string | null | undefined,
): string {
  if (st !== "success") return "—";
  const p = apiPath;
  const s = typeof p === "string" ? p.trim() : "";
  return s || "—";
}

export function mergeMergedOutputPathTooltip(
  pathText: string,
  workspaceRootAbs: string | null,
): string | undefined {
  if (pathText === "—") return undefined;
  return workspaceAbsoluteDisplayPath(pathText, workspaceRootAbs);
}

export function trainingJobNameText(record: Record<string, unknown>): string {
  const j = record.job_name;
  if (j == null || j === "") return "—";
  const s = String(j);
  return s !== "" && s !== "—" ? s : "—";
}

export function trainingJobNameTitle(record: Record<string, unknown>): string | undefined {
  const t = trainingJobNameText(record);
  return t !== "—" ? t : undefined;
}

export function trainingRequest(record: Record<string, unknown>): Record<string, unknown> | undefined {
  const r = record.request;
  return r && typeof r === "object" ? (r as Record<string, unknown>) : undefined;
}

export function trainingOutputDirText(record: Record<string, unknown>): string {
  const top = record.output_dir;
  if (typeof top === "string" && top.trim() && top !== "—") return top.trim();
  const raw = trainingRequest(record)?.output_dir;
  const s = typeof raw === "string" ? raw.trim() : "";
  return s || "—";
}

export function trainingOutputDirVersionLevelText(record: Record<string, unknown>): string {
  const reqVid = trainingRequest(record)?.dataset_version_id;
  if (typeof reqVid === "string" && reqVid.trim()) {
    return `train/${reqVid.trim()}`;
  }
  const full = trainingOutputDirText(record);
  if (full === "—") return full;
  const parts = full
    .replace(/\\/g, "/")
    .replace(/\/+$/, "")
    .split("/")
    .filter(Boolean);
  if (parts.length >= 3 && parts[0] === "train") {
    return `train/${parts[1]}`;
  }
  return full;
}

export function trainingOutputDirRunText(record: Record<string, unknown>): string {
  const run = trainingRequest(record)?.swift_run_relpath;
  if (typeof run === "string" && run.trim()) return run.trim();
  return trainingOutputDirVersionLevelText(record);
}

export function trainingOutputDirRunTitle(record: Record<string, unknown>): string | undefined {
  const lines: string[] = [];
  const run = trainingRequest(record)?.swift_run_relpath;
  if (typeof run === "string" && run.trim()) {
    lines.push(`ms-swift 运行目录：${run.trim()}`);
  }
  const base = trainingOutputDirText(record);
  if (base !== "—") {
    lines.push(`任务 output_dir：${base}`);
  }
  return lines.length ? lines.join("\n") : undefined;
}

/** 合并前二次确认：对应本卡片训练任务 ID 与 LoRA 产出目录 */
export function mergeConfirmDescription(record: Record<string, unknown>): string {
  const id = String(record.id ?? "").trim();
  const name = trainingJobNameText(record);
  const lora = trainingOutputDirRunText(record);
  const outRel = mergeOutputRelForTrainingJob(id);
  const loraHint =
    lora !== "—"
      ? `LoRA 产出路径（合并使用该训练的 LoRA）：${lora}`
      : "LoRA 路径由服务端按该训练任务解析";
  return [
    "请确认：合并仅针对「本卡片」当前训练任务的结果，与其它训练任务无关。",
    `任务名称：${name}`,
    `训练任务 ID：${id}`,
    `${loraHint}。`,
    `合并产物写入：${outRel}`,
  ].join("\n");
}

/** 数据集列 tooltip：项目等展示 */
export function trainingDatasetTooltipField(v: unknown): string {
  if (v == null || v === "") return "—";
  const s = String(v).trim();
  return s || "—";
}

export function trainingJobCardMeta(
  record: Record<string, unknown>,
  mergedPathRow: { value: string; pathTooltip?: string },
): TrainJobMetaRow[] {
  const status = typeof record.status === "string" ? record.status : String(record.status ?? "");
  const outRun = trainingOutputDirRunText(record);
  const outTooltip = trainingOutputDirRunTitle(record);
  return [
    { label: "数据集", value: trainingDatasetTooltipField(record.dataset_name) },
    { label: "项目", value: trainingDatasetTooltipField(record.project_title) },
    { label: "训练集", value: formatJobSplitCount(record.train_count) },
    { label: "验证集", value: formatJobSplitCount(record.val_count) },
    {
      label: "LoRA 输出路径",
      value: outRun,
      pathTooltip: outRun !== "—" && outTooltip ? outTooltip : outRun !== "—" ? outRun : undefined,
    },
    { label: "训练开始时间", value: formatJobTime(record.created_at) },
    { label: "训练结束时间", value: formatJobEnd(record.finished_at, status) },
    {
      label: "合并后模型路径",
      value: mergedPathRow.value,
      pathTooltip: mergedPathRow.pathTooltip,
    },
  ];
}

export function trainingJobStatusTagColor(status: unknown): string {
  const s = (typeof status === "string" ? status : String(status ?? "")).trim();
  if (s === "succeeded") return "success";
  if (s === "failed") return "error";
  if (s === "cancelled") return "warning";
  if (s === "running" || s === "pending") return "processing";
  if (s === "parameters_saved") return "default";
  return "default";
}
