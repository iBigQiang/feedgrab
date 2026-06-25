import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  root: ".",
  base: "./",
  plugins: [react()],
  build: {
    outDir: "dist-renderer",
    emptyOutDir: true
  },
  test: {
    environment: "jsdom",
    globals: true
  }
});
