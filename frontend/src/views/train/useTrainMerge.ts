import { message } from "ant-design-vue";
import { onUnmounted, ref, watch } from "vue";
import { apiErrorDetail, http } from "../../api/http";
import type { MergeUiStatus } from "./trainTypes";
import { MERGE_DEFAULT_BASE } from "./trainTypes";
import { mergeOutputRelForTrainingJob, parseMergeUiStatus } from "./trainFormatters";

export function useTrainMerge() {
  const mergeStatusByJobId = ref<Record<string, MergeUiStatus>>({});
  /** 仅合并成功时由 GET /api/merge/training-status 的 output_path_by_job_id 提供实际输出路径 */
  const mergeOutputPathByJobId = ref<Record<string, string | null | undefined>>({});

  async function loadMergeStatus() {
    try {
      const r = await http.get("/api/merge/training-status");
      const m = (r.data?.status_by_job_id ?? {}) as Record<string, string>;
      const next: Record<string, MergeUiStatus> = {};
      for (const [k, v] of Object.entries(m)) {
        next[k] = parseMergeUiStatus(v);
      }
      mergeStatusByJobId.value = next;
      mergeOutputPathByJobId.value = (r.data?.output_path_by_job_id ?? {}) as Record<
        string,
        string | null | undefined
      >;
    } catch {
      // 保留上一帧，避免闪烁
    }
  }

  const mergeSubmitting = ref(false);
  let trainPageMergePoller: ReturnType<typeof setInterval> | null = null;
  let trainPageMergePollId: string | null = null;
  /** 当前 `trainPageMergePoller` 对应的训练任务 id（用于避免与日志轮询重复请求） */
  const mergePollTrainingJobId = ref<string | null>(null);

  /** 合并日志弹窗（与 Merge.vue「日志」同源接口） */
  const mergeLogModalOpen = ref(false);
  const mergeLogModalText = ref("");
  const mergeLogModalTrainingJobId = ref<string | null>(null);
  const mergeLogLoading = ref(false);

  async function loadMergeLogForModal(tid: string) {
    if (!tid) return;
    const r = await http.get(`/api/merge/training-jobs/${encodeURIComponent(tid)}/logs`);
    mergeLogModalText.value = String(r.data?.text ?? "");
  }

  /** 合并已在别处/当前会话进行中时，仅刷新日志弹窗（与 Merge.vue scheduleLogModalPoll 一致） */
  let mergeLogRefreshPoller: ReturnType<typeof setInterval> | null = null;

  function stopMergeLogRefreshPoller() {
    if (mergeLogRefreshPoller) {
      clearInterval(mergeLogRefreshPoller);
      mergeLogRefreshPoller = null;
    }
  }

  function scheduleMergeLogRefreshUntilDone(tid: string) {
    stopMergeLogRefreshPoller();
    mergeLogRefreshPoller = setInterval(() => {
      if (!mergeLogModalOpen.value || mergeLogModalTrainingJobId.value !== tid) {
        stopMergeLogRefreshPoller();
        return;
      }
      void (async () => {
        try {
          await loadMergeLogForModal(tid);
          await loadMergeStatus();
          if (mergeStatusByJobId.value[tid] !== "merging") {
            stopMergeLogRefreshPoller();
          }
        } catch {
          // 保留已显示内容
        }
      })();
    }, 1500);
  }

  /** 当前训练任务已有合并在进行：打开日志并轮询至结束 */
  async function openMergeLogForOngoingMerge(tid: string) {
    mergeLogModalTrainingJobId.value = tid;
    mergeLogLoading.value = true;
    mergeLogModalOpen.value = true;
    stopMergeLogRefreshPoller();
    try {
      await loadMergeLogForModal(tid);
      await loadMergeStatus();
    } catch (e: unknown) {
      message.error(apiErrorDetail(e) ?? String(e));
      mergeLogModalText.value = "";
    } finally {
      mergeLogLoading.value = false;
    }
    if (mergeStatusByJobId.value[tid] === "merging") {
      if (!(trainPageMergePoller && mergePollTrainingJobId.value === tid)) {
        scheduleMergeLogRefreshUntilDone(tid);
      }
    }
  }

  watch(mergeLogModalOpen, (open) => {
    if (!open) {
      mergeLogModalTrainingJobId.value = null;
      stopMergeLogRefreshPoller();
    }
  });

  function stopTrainPageMergePoll() {
    if (trainPageMergePoller) {
      clearInterval(trainPageMergePoller);
      trainPageMergePoller = null;
    }
    trainPageMergePollId = null;
    mergePollTrainingJobId.value = null;
  }

  /** 与 Merge.vue 卡片「合并成全量模型」一致：`merge_lora_only: true` + 轮询合并任务 */
  async function runMergeFromTrainCard(record: Record<string, unknown>) {
    const tid = String(record.id ?? "").trim();
    if (!tid) return;

    mergeSubmitting.value = true;
    try {
      const [candidatesRes, statusRes] = await Promise.all([
        http.get("/api/merge/training-candidates"),
        http.get("/api/merge/training-status"),
      ]);
      const items = (candidatesRes.data?.items ?? []) as Record<string, unknown>[];
      const row = items.find((x) => String(x.job_id ?? "").trim() === tid);
      if (!row) {
        message.error("未找到该任务的合并候选（仅保存参数或未开训的任务不会在合并列表中）");
        return;
      }
      const path = String(row.path ?? "").trim();
      if (!path) {
        message.error("该训练条目缺少有效 LoRA 路径");
        return;
      }
      const trainingStatus = String(row.training_status ?? "").trim().toLowerCase();
      if (trainingStatus === "pending" || trainingStatus === "running") {
        message.warning("训练未完成，无法合并");
        return;
      }
      const statusByJobId = (statusRes.data?.status_by_job_id ?? {}) as Record<string, string>;
      const mergeSt = parseMergeUiStatus(statusByJobId[tid]);
      if (mergeSt === "merging") {
        await openMergeLogForOngoingMerge(tid);
        return;
      }
      if (mergeSt !== "none" && trainingStatus !== "succeeded") {
        message.warning("需训练成功后才可再次合并");
        return;
      }

      stopTrainPageMergePoll();

      const trainBase =
        typeof row.train_base_model === "string" && row.train_base_model.trim()
          ? row.train_base_model.trim()
          : MERGE_DEFAULT_BASE;

      const createRes = await http.post("/api/merge/jobs", {
        base_model_path: trainBase,
        lora_paths: [path],
        output_path: mergeOutputRelForTrainingJob(tid),
        merge_lora_only: true,
        training_job_id: tid,
      });
      const mergeJobId = String(createRes.data?.id ?? "").trim();
      if (!mergeJobId) {
        message.error("合并任务创建失败");
        return;
      }

      trainPageMergePollId = mergeJobId;
      mergePollTrainingJobId.value = tid;

      mergeLogModalTrainingJobId.value = tid;
      mergeLogLoading.value = true;
      mergeLogModalOpen.value = true;
      try {
        await loadMergeLogForModal(tid);
        await loadMergeStatus();
      } catch (e: unknown) {
        message.error(apiErrorDetail(e) ?? String(e));
        mergeLogModalText.value = "";
      } finally {
        mergeLogLoading.value = false;
      }

      async function pollMergeOnce() {
        const mid = trainPageMergePollId;
        if (!mid) return;
        try {
          const s = await http.get(`/api/merge/jobs/${mid}`);
          void loadMergeStatus();
          if (mergeLogModalOpen.value && mergeLogModalTrainingJobId.value === tid) {
            void loadMergeLogForModal(tid);
          }
          const st = String(s.data?.status ?? "");
          if (["succeeded", "failed", "cancelled"].includes(st)) {
            stopTrainPageMergePoll();
            void loadMergeLogForModal(tid);
            if (st === "succeeded") {
              message.success("合并完成");
              await http.post(`/api/merge/jobs/${mid}/validate`);
            } else if (st === "failed") {
              message.error(String(s.data?.error_message ?? "合并失败"));
            } else if (st === "cancelled") {
              message.warning("合并已取消");
            }
          }
        } catch (e: unknown) {
          message.error(String((e as { message?: string })?.message ?? e));
        }
      }

      void pollMergeOnce();
      trainPageMergePoller = setInterval(() => void pollMergeOnce(), 1500);
    } catch (e: unknown) {
      message.error(apiErrorDetail(e) ?? String(e));
    } finally {
      mergeSubmitting.value = false;
    }
  }

  onUnmounted(() => {
    stopTrainPageMergePoll();
    stopMergeLogRefreshPoller();
  });

  return {
    mergeStatusByJobId,
    mergeOutputPathByJobId,
    loadMergeStatus,
    mergeSubmitting,
    mergeLogModalOpen,
    mergeLogModalText,
    mergeLogModalTrainingJobId,
    mergeLogLoading,
    runMergeFromTrainCard,
  };
}
