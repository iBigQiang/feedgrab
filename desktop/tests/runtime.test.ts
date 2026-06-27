import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
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
      chromiumVersion: "142.0.7444.265",
      exists: (target) => target === workerExe || target === browsersPath
    });

    expect(runtime.source).toBe("bundled");
    expect(runtime.command).toBe(workerExe);
    expect(runtime.args).toEqual([]);
    expect(runtime.env.PLAYWRIGHT_BROWSERS_PATH).toBe(browsersPath);
    expect(runtime.env.PLAYWRIGHT_SKIP_BROWSER_GC).toBe("1");
    expect(runtime.env.FEEDGRAB_INSTALL_SESSIONS_DIR).toBe(
      path.join("C:\\Program Files\\feedgrab Desktop", "sessions")
    );
    expect(runtime.env.BROWSER_USER_AGENT).toContain("Chrome/142.0.7444.265");
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
    expect(runtime.env.FEEDGRAB_INSTALL_SESSIONS_DIR).toBe(
      path.join("D:\\AiCode\\feedgrab", "desktop", "sessions")
    );
  });

  it("keeps a user-provided browser user agent ahead of the runtime default", () => {
    const runtime = resolveFeedgrabRuntime({
      platform: "win32",
      projectRoot: "D:\\AiCode\\feedgrab",
      resourcesPath: "C:\\Program Files\\feedgrab Desktop\\resources",
      userDataPath: "C:\\Users\\Qiang\\AppData\\Roaming\\feedgrab Desktop",
      env: {
        BROWSER_USER_AGENT: "CustomAgent/1.0"
      },
      chromiumVersion: "142.0.7444.265",
      exists: () => false
    });

    expect(runtime.env.BROWSER_USER_AGENT).toBe("CustomAgent/1.0");
  });

  it("projects saved desktop proxy settings into the worker startup environment", () => {
    const userDataPath = mkdtempSync(path.join(tmpdir(), "feedgrab-runtime-"));
    writeFileSync(
      path.join(userDataPath, "settings.json"),
      JSON.stringify({
        values: {
          FEEDGRAB_PROXY_ENABLED: true,
          FEEDGRAB_PROXY_URL: "socks5://127.0.0.1:7890",
          FEEDGRAB_NO_PROXY: "127.0.0.1,localhost"
        }
      }),
      "utf8"
    );

    const runtime = resolveFeedgrabRuntime({
      platform: "win32",
      projectRoot: "D:\\AiCode\\feedgrab",
      resourcesPath: "C:\\Program Files\\feedgrab Desktop\\resources",
      userDataPath,
      env: {},
      exists: () => false
    });

    expect(runtime.env.HTTP_PROXY).toBe("socks5://127.0.0.1:7890");
    expect(runtime.env.HTTPS_PROXY).toBe("socks5://127.0.0.1:7890");
    expect(runtime.env.ALL_PROXY).toBe("socks5://127.0.0.1:7890");
    expect(runtime.env.NO_PROXY).toBe("127.0.0.1,localhost");
  });
});
