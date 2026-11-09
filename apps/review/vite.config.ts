import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { proxy: { "/__ae-fba": { target: process.env.FBACC_SERVICE_URL || "http://127.0.0.1:8787", changeOrigin: true,
    rewrite: path => path.replace(/^\/__ae-fba/, "") } } },
  build: {
    target: "es2020",
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
