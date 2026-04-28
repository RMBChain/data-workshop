import axios from "axios";

/** 由 Vite 将 /api 代理到后端（见 vite.config.ts） */
export const http = axios.create({
  baseURL: "",
  timeout: 0,
});

/** axios 错误上的 FastAPI `detail`（常见为字符串；422 时可能为数组） */
export function getApiErrorDetail(e: unknown): unknown {
  return (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
}

/** 仅当 `detail` 为字符串时返回，否则 `undefined`（需完整展示时用 `getApiErrorDetail` + `formatApiDetail`） */
export function apiErrorDetail(e: unknown): string | undefined {
  const d = getApiErrorDetail(e);
  return typeof d === "string" && d ? d : undefined;
}

export function formatApiDetail(detail: unknown): string {
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
