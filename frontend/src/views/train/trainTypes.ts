export type MergeUiStatus = "none" | "merging" | "interrupted" | "failed" | "success";

export type TrainJobMetaRow = {
  label: string;
  value: string;
  /** 路径类：ellipsis + Tooltip 展示完整 tooltip */
  pathTooltip?: string;
};

export const MERGE_STATUS_LABEL: Record<MergeUiStatus, string> = {
  none: "未合并",
  merging: "合并中",
  interrupted: "合并中断",
  failed: "合并失败",
  success: "合并成功",
};

/** 与 Merge.vue `mergeOutputRelForTrainingJob` / 默认基座一致 */
export const MERGE_OUTPUT_ROOT = "output/merged-workshop";
export const MERGE_DEFAULT_BASE = "Qwen/Qwen3-VL-2B-Instruct";
