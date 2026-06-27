import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import type { PluginOption } from "vite";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export default defineConfig(({ command }) => ({
  root: ".",
  base: "./",
  plugins: [react(), command === "serve" ? devServerCspForVite() : null],
  server: {
    fs: {
      allow: [repoRoot]
    }
  },
  build: {
    outDir: "dist-renderer",
    emptyOutDir: true
  },
  test: {
    environment: "jsdom",
    globals: true
  }
}));

function devServerCspForVite(): PluginOption {
  return {
    name: "feedgrab-dev-server-csp",
    transformIndexHtml(html) {
      return html.replace(
        "script-src 'self'; style-src 'self';",
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"
      );
    }
  };
}
