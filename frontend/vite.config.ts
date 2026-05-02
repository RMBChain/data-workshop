import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 8701,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8702",
        changeOrigin: true,
      },
    },
  },
});
