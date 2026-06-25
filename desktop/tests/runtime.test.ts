import path from "node:path";

import { describe, expect, it } from "vitest";

import { resolveFeedgrabRuntime } from "../electron/runtime";

describe("resolveFeedgrabRuntime", () => {
  it("uses the bundled PyInstaller worker and bundled Chromium when available", () => {
    const resourcesPath = "C:\\Program Files\\feedgrab Desktop\\resources";
    const runtimeRoot = path.join(resourcesPath, "feedgrab-runtime");
    const workerExe = path.join(runtimeRoot, "feedgrab-worker", "feedgrab-worker.exe");
    const browsersPath = path.join(runtimeRoot, "ms-playwright");

    const runtime = resolveFeedgrabRuntime({
      platform: "win32",
      projectRoot: "D:\\AiCode\\feedgrab",
      resourcesPath,
      userDataPath: "C:\\Users\\Qiang\\AppData\\Roaming\\feedgrab Desktop",
      env: {},
      exists: (target) => target === workerExe || target === browsersPath
    });

    expect(runtime.source).toBe("bundled");
    expect(runtime.command).toBe(workerExe);
    expect(runtime.args).toEqual([]);
    expect(runtime.env.PLAYWRIGHT_BROWSERS_PATH).toBe(browsersPath);
    expect(runtime.env.PLAYWRIGHT_SKIP_BROWSER_GC).toBe("1");
  });

  it("falls back to system Python and managed browser path when bundled runtime is absent", () => {
    const runtime = resolveFeedgrabRuntime({
      platform: "win32",
      projectRoot: "D:\\AiCode\\feedgrab",
      resourcesPath: "C:\\Program Files\\feedgrab Desktop\\resources",
      userDataPath: "C:\\Users\\Qiang\\AppData\\Roaming\\feedgrab Desktop",
      env: { FEEDGRAB_DESKTOP_PYTHON: "D:\\Python312\\python.exe" },
      exists: () => false
    });

    expect(runtime.source).toBe("system-python");
    expect(runtime.command).toBe("D:\\Python312\\python.exe");
    expect(runtime.args).toEqual(["-m", "feedgrab.worker"]);
    expect(runtime.cwd).toBe("D:\\AiCode\\feedgrab");
    expect(runtime.env.PLAYWRIGHT_BROWSERS_PATH).toContain("ms-playwright");
  });
});
