import axios from "axios";

/** 开发环境由 Vite 代理 /api；生产构建由 nginx 反代同域 /api */
export const http = axios.create({
  baseURL: "",
  timeout: 0,
});
