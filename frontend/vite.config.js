import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the OpenKB REST API on :8000; production serves
// the built bundle from the same origin via FastAPI StaticFiles.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    // Build into the Python package so the bundle ships inside the wheel
    // (as openkb/web) instead of a top-level dir that would pollute
    // site-packages. Kept out of git via .gitignore; hatchling picks it up
    // through `artifacts` at build time.
    outDir: "../openkb/web",
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
