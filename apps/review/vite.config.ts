import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "FBACC_");
  return {
  plugins: [react()],
  base: "./",
  server: { proxy: { "/__ae-fba": { target: process.env.FBACC_SERVICE_URL || env.FBACC_SERVICE_URL || "http://127.0.0.1:4318", changeOrigin: true,
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
  };
});
