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
      env: {
        FEEDGRAB_SETTINGS_PATH: "E:\\feedgrab-dev\\settings.json",
        OBSIDIAN_VAULT: "E:\\Obsidian\\Qiang_Obsidian\\inbox",
        OUTPUT_DIR: "E:\\Obsidian\\Qiang_Obsidian\\inbox"
      },
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
    expect(runtime.env.FEEDGRAB_DATA_DIR).toBe(
      path.join("C:\\Program Files\\feedgrab Desktop", "sessions")
    );
    expect(runtime.env.OUTPUT_DIR).toBe(path.join("C:\\Program Files\\feedgrab Desktop", "output"));
    expect(runtime.env.OBSIDIAN_VAULT).toBe("");
    expect(runtime.env.FEEDGRAB_SETTINGS_PATH).toBe(
      path.join("C:\\Users\\Qiang\\AppData\\Roaming\\feedgrab Desktop", "settings.json")
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
    expect(runtime.env.FEEDGRAB_DATA_DIR).toBe(path.join("D:\\AiCode\\feedgrab", "desktop", "sessions"));
  });

  it("uses the desktop sessions directory as the system Python default data dir", () => {
    const runtime = resolveFeedgrabRuntime({
      platform: "win32",
      projectRoot: "D:\\AiCode\\feedgrab",
      resourcesPath: "C:\\Program Files\\feedgrab Desktop\\resources",
      userDataPath: "C:\\Users\\Qiang\\AppData\\Roaming\\feedgrab Desktop",
      env: {
        FEEDGRAB_DATA_DIR: "D:\\AiCode\\feedgrab\\sessions"
      },
      exists: () => false
    });

    expect(runtime.source).toBe("system-python");
    expect(runtime.env.FEEDGRAB_INSTALL_SESSIONS_DIR).toBe(
      path.join("D:\\AiCode\\feedgrab", "desktop", "sessions")
    );
    expect(runtime.env.FEEDGRAB_DATA_DIR).toBe(path.join("D:\\AiCode\\feedgrab", "desktop", "sessions"));
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

  it("ignores inherited data directory for bundled install defaults", () => {
    const resourcesPath = "D:\\feedgrab Desktop\\resources";
    const runtimeRoot = path.join(resourcesPath, "feedgrab-runtime");
    const workerExe = path.join(runtimeRoot, "feedgrab-worker", "feedgrab-worker.exe");

    const runtime = resolveFeedgrabRuntime({
      platform: "win32",
      projectRoot: "D:\\AiCode\\feedgrab",
      resourcesPath,
      userDataPath: "C:\\Users\\Qiang\\AppData\\Roaming\\feedgrab Desktop",
      env: {
        FEEDGRAB_DATA_DIR: "E:\\feedgrab-sessions"
      },
      exists: (target) => target === workerExe
    });

    expect(runtime.source).toBe("bundled");
    expect(runtime.env.FEEDGRAB_INSTALL_SESSIONS_DIR).toBe(path.join("D:\\feedgrab Desktop", "sessions"));
    expect(runtime.env.FEEDGRAB_DATA_DIR).toBe(path.join("D:\\feedgrab Desktop", "sessions"));
  });

  it("ignores inherited output directory for bundled install defaults", () => {
    const resourcesPath = "D:\\feedgrab Desktop\\resources";
    const runtimeRoot = path.join(resourcesPath, "feedgrab-runtime");
    const workerExe = path.join(runtimeRoot, "feedgrab-worker", "feedgrab-worker.exe");

    const runtime = resolveFeedgrabRuntime({
      platform: "win32",
      projectRoot: "D:\\AiCode\\feedgrab",
      resourcesPath,
      userDataPath: "C:\\Users\\Qiang\\AppData\\Roaming\\feedgrab Desktop",
      env: {
        OUTPUT_DIR: "E:\\Obsidian\\Qiang_Obsidian\\inbox"
      },
      exists: (target) => target === workerExe
    });

    expect(runtime.source).toBe("bundled");
    expect(runtime.env.OUTPUT_DIR).toBe(path.join("D:\\feedgrab Desktop", "output"));
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
