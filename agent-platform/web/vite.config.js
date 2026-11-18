import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

/**
 * 构建产物落到 web/dist，由 FastAPI 的 `/` 与 `/assets` 托管。
 * 开发时用 `npm run dev`，/api 代理到平台（8090），避免前端另起一个源导致 CORS。
 */
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // ECharts 单独成一个 chunk（≈1MB），单独告警没有意义
    chunkSizeWarningLimit: 1400,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ["echarts"],
          markdown: ["markdown-it", "dompurify"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8090", changeOrigin: true },
      "/healthz": { target: "http://127.0.0.1:8090", changeOrigin: true },
    },
  },
});
