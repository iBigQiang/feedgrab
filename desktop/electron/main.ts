import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { access, mkdir, stat, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

import type { FeedgrabWorkerEvent, FetchRequest } from "./ipc-types.js";
import { createMockPythonWorkerClient, createPythonWorkerClient } from "./python-worker.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..", "..");
const allowedOpenRoots = new Set<string>();
const allowedOpenPaths = new Set<string>();
const worker =
  process.env.FEEDGRAB_DESKTOP_MOCK === "true"
    ? createMockPythonWorkerClient()
    : createPythonWorkerClient({ cwd: projectRoot });

worker.onEvent((event: FeedgrabWorkerEvent) => {
  if (event.event === "artifact" && event.artifact?.path) {
    allowedOpenPaths.add(path.resolve(event.artifact.path));
  }
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) {
      window.webContents.send("feedgrab:workerEvent", event);
    }
  }
});

function createWindow(): void {
  const preload = path.join(__dirname, "preload.cjs");
  const window = new BrowserWindow({
    width: 1240,
    height: 820,
    minWidth: 960,
    minHeight: 680,
    title: "feedgrab Desktop",
    backgroundColor: "#f7f4ef",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload
    }
  });

  registerSmokeDiagnostics(window);

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });

  window.webContents.on("will-navigate", (event, url) => {
    const current = window.webContents.getURL();
    if (current && url !== current) {
      event.preventDefault();
    }
  });

  const devServerUrl = process.env.ELECTRON_RENDERER_URL;
  if (devServerUrl) {
    if (isAllowedDevServerUrl(devServerUrl)) {
      void window.loadURL(devServerUrl);
    } else {
      console.error(`Refusing ELECTRON_RENDERER_URL outside localhost: ${devServerUrl}`);
      void window.loadFile(path.join(__dirname, "../dist-renderer/index.html"));
    }
    registerSmokeScreenshot(window);
    return;
  }

  void window.loadFile(path.join(__dirname, "../dist-renderer/index.html"), screenshotViewLoadOptions());
  registerSmokeScreenshot(window);
}

function registerSmokeDiagnostics(window: BrowserWindow): void {
  if (process.env.FEEDGRAB_DESKTOP_SMOKE_LOG !== "true") {
    return;
  }

  window.webContents.on("console-message", (_event, level, message, line, sourceId) => {
    console.warn(`[renderer:${level}] ${message} (${sourceId}:${line})`);
  });
  window.webContents.on("preload-error", (_event, preloadPath, error) => {
    console.error(`[preload-error] ${preloadPath}: ${error.message}`);
  });
  window.webContents.on("did-fail-load", (_event, code, description, url) => {
    console.error(`[did-fail-load] ${code} ${description} ${url}`);
  });
  window.webContents.on("render-process-gone", (_event, details) => {
    console.error(`[render-process-gone] ${details.reason}`);
  });
}

function registerSmokeScreenshot(window: BrowserWindow): void {
  const screenshotPath = process.env.FEEDGRAB_DESKTOP_SCREENSHOT;
  if (!screenshotPath) {
    return;
  }

  window.webContents.once("did-finish-load", () => {
    const delayMs = Number(process.env.FEEDGRAB_DESKTOP_SCREENSHOT_DELAY_MS ?? "1200");
    setTimeout(() => {
      void captureSmokeScreenshot(window, screenshotPath);
    }, Number.isFinite(delayMs) ? delayMs : 1200);
  });
}

function screenshotViewLoadOptions(): { query: Record<string, string> } | undefined {
  const view = process.env.FEEDGRAB_DESKTOP_SCREENSHOT_VIEW;
  if (!view) {
    return undefined;
  }
  return { query: { view } };
}

async function captureSmokeScreenshot(window: BrowserWindow, screenshotPath: string): Promise<void> {
  await mkdir(path.dirname(screenshotPath), { recursive: true });
  let png: Uint8Array = Buffer.alloc(0);

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const image = await window.webContents.capturePage();
    png = image.toPNG();
    if (png.length > 0) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  await writeFile(screenshotPath, png);
  app.quit();
}

function registerIpc(): void {
  ipcMain.handle("feedgrab:ping", () => worker.ping());
  ipcMain.handle("feedgrab:detectPlatform", (_event, url: string) => worker.detectPlatform(url));
  ipcMain.handle("feedgrab:startFetch", (_event, request: FetchRequest) => {
    if (!isValidFetchRequest(request)) {
      throw new Error("无效抓取请求");
    }
    rememberOpenRoot(request.outputDirectory);
    return worker.startFetch(request);
  });
  ipcMain.handle("feedgrab:cancelJob", (_event, jobId: string) => worker.cancelJob(jobId));
  ipcMain.handle("feedgrab:doctor", () => worker.doctor());
  ipcMain.handle("feedgrab:settingsSnapshot", async () => {
    const snapshot = await worker.settingsSnapshot();
    rememberOpenRoot(snapshot.outputDirectory);
    return snapshot;
  });
  ipcMain.handle("feedgrab:loginStatus", () => worker.loginStatus());
  ipcMain.handle("feedgrab:outputList", () => worker.outputList());
  ipcMain.handle("feedgrab:chooseOutputDirectory", async () => {
    const result = await dialog.showOpenDialog({
      title: "选择 feedgrab 输出目录",
      properties: ["openDirectory", "createDirectory"]
    });
    if (result.canceled || result.filePaths.length === 0) {
      return { ok: false, cancelled: true };
    }
    rememberOpenRoot(result.filePaths[0]);
    return { ok: true, path: result.filePaths[0] };
  });
  ipcMain.handle("feedgrab:openPath", async (_event, targetPath: string) => {
    if (typeof targetPath !== "string" || targetPath.trim().length === 0) {
      return { ok: false, error: "路径为空" };
    }
    const resolved = path.resolve(targetPath);
    if (!(await canOpenPath(resolved))) {
      return { ok: false, error: "路径不在已授权输出目录或产物列表内" };
    }
    const error = await shell.openPath(resolved);
    return error ? { ok: false, error } : { ok: true };
  });
}

function isAllowedDevServerUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return (
      parsed.protocol === "http:" &&
      ["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname)
    );
  } catch {
    return false;
  }
}

function isValidFetchRequest(request: FetchRequest): boolean {
  return (
    Boolean(request) &&
    Array.isArray(request.urls) &&
    request.urls.length > 0 &&
    request.urls.every((url) => typeof url === "string" && /^https?:\/\//i.test(url)) &&
    typeof request.outputDirectory === "string" &&
    request.outputDirectory.trim().length > 0
  );
}

function rememberOpenRoot(rootPath: string): void {
  if (typeof rootPath === "string" && rootPath.trim().length > 0) {
    allowedOpenRoots.add(path.resolve(rootPath));
  }
}

async function canOpenPath(targetPath: string): Promise<boolean> {
  try {
    await access(targetPath);
    const info = await stat(targetPath);
    if (allowedOpenPaths.has(targetPath)) {
      return true;
    }
    if (![...allowedOpenRoots].some((root) => isPathInside(targetPath, root))) {
      return false;
    }
    if (info.isDirectory()) {
      return true;
    }
    return [".md", ".csv", ".srt", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp3", ".mp4"].includes(
      path.extname(targetPath).toLowerCase()
    );
  } catch {
    return false;
  }
}

function isPathInside(targetPath: string, rootPath: string): boolean {
  const relative = path.relative(rootPath, targetPath);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

registerIpc();

void app.whenReady().then(() => {
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
