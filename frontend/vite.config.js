import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the OpenKB REST API on :8000; production serves
// the built bundle from the same origin via FastAPI StaticFiles.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../web",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
