import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 6006,
    allowedHosts: [
      'u871016-c7kl-88856538.bjb1.seetacloud.com',  // 您的AutoDL域名
      '.seetacloud.com',  // 允许所有 seetacloud.com 的子域名
      'localhost',
      '127.0.0.1'
    ]
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8702",
        changeOrigin: true,
      },
    },
  },
});
