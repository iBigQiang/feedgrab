import { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, net, session, shell } from "electron";
import { spawn } from "node:child_process";
import { appendFileSync, copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, statSync } from "node:fs";
import { access, mkdir, stat, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

import type {
  ChromeCdpEnsureResult,
  DirectorySelectionOptions,
  FeedgrabWorkerEvent,
  FetchRequest,
  LoginStatusRequest,
  SettingsFieldValue,
  SupportedPlatform
} from "./ipc-types.js";
import { createMockPythonWorkerClient, createPythonWorkerClient } from "./python-worker.js";
import type { PythonWorkerClient } from "./python-worker.js";
import { resolveFeedgrabRuntime } from "./runtime.js";
import type { FeedgrabRuntimeResolution } from "./runtime.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..", "..");
const appIconPath = app.isPackaged
  ? path.join(process.resourcesPath, "app.ico")
  : path.join(projectRoot, "docs", "feedgrab-icons", "windows", "app.ico");
const appWindowIconPath = app.isPackaged
  ? path.join(process.resourcesPath, "Square44x44Logo.png")
  : path.join(projectRoot, "docs", "feedgrab-icons", "windows", "Square44x44Logo.png");
const allowedOpenRoots = new Set<string>();
const allowedOpenPaths = new Set<string>();
let worker: PythonWorkerClient | undefined;
const chromeCdpProbeTimeoutMs = 1000;
const chromeCdpStartupPollMs = 500;
const chromeCdpStartupAttempts = 20;
const allowedRemoteMarkdownUrls = new Set([
  "https://edgeone.gh-proxy.com/https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/sponsor.md",
  "https://edgeone.gh-proxy.com/https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/group.md",
  "https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/sponsor.md",
  "https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/group.md"
]);
let currentProxyCredentials: { username: string; password: string } | undefined;

if (process.platform === "win32") {
  app.setAppUserModelId("com.feedgrab.desktop");
}

if (process.env.FEEDGRAB_DESKTOP_DISABLE_GPU === "true" || process.env.FEEDGRAB_DESKTOP_SMOKE_LOG === "true") {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("disable-gpu-compositing");
  app.commandLine.appendSwitch("disable-gpu-sandbox");
  app.commandLine.appendSwitch("in-process-gpu");
}

if (process.env.FEEDGRAB_DESKTOP_USER_DATA_DIR) {
  app.setPath("userData", process.env.FEEDGRAB_DESKTOP_USER_DATA_DIR);
}

app.on("login", (event, _webContents, _request, authInfo, callback) => {
  if (authInfo.isProxy && currentProxyCredentials) {
    event.preventDefault();
    callback(currentProxyCredentials.username, currentProxyCredentials.password);
  }
});

function initializeWorker(): PythonWorkerClient {
  if (worker) {
    return worker;
  }
  smokeLog("initializing worker");
  if (process.env.FEEDGRAB_DESKTOP_MOCK === "true") {
    worker = createMockPythonWorkerClient();
    smokeLog("mock worker selected");
  } else {
    const runtime = resolveFeedgrabRuntime({
      projectRoot,
      resourcesPath: process.resourcesPath,
      userDataPath: app.getPath("userData"),
      env: process.env
    });
    smokeLog(
      `runtime source=${runtime.source} worker=${runtime.workerPath} browsers=${runtime.browserPath} cwd=${runtime.cwd}`
    );
    ensureRuntimeDirectories(runtime);
    smokeLog("runtime directories ready");
    worker = createPythonWorkerClient({
      command: runtime.command,
      args: runtime.args,
      cwd: runtime.cwd,
      env: runtime.env
    });
    console.info(
      `[feedgrab runtime] source=${runtime.source} worker=${runtime.workerPath} browsers=${runtime.browserPath}`
    );
  }
  worker.onEvent(forwardWorkerEvent);
  return worker;
}

function currentWorker(): PythonWorkerClient {
  return worker ?? initializeWorker();
}

function ensureRuntimeDirectories(runtime: FeedgrabRuntimeResolution): void {
  const paths = [
    runtime.cwd,
    runtime.env.FEEDGRAB_DATA_DIR,
    runtime.env.OUTPUT_DIR
  ];
  if (!runtime.bundledBrowserAvailable) {
    paths.push(runtime.browserPath);
  }
  for (const targetPath of paths) {
    if (targetPath) {
      mkdirSync(targetPath, { recursive: true });
    }
  }
  synchronizeSessionTemplates(runtime.env.FEEDGRAB_DATA_DIR);
}

function synchronizeSessionTemplates(dataDirectory: string | undefined): void {
  if (!dataDirectory) {
    return;
  }

  const sourceDirectory = resolveSessionTemplateSourceDirectory();
  if (!sourceDirectory) {
    smokeLog("session template source directory not found");
    return;
  }

  try {
    copySessionTemplates(sourceDirectory, dataDirectory);
  } catch (error) {
    smokeLog(`session template sync failed: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function resolveSessionTemplateSourceDirectory(): string | undefined {
  const templateSourceCandidates = [
    path.join(process.resourcesPath, "session-templates"),
    path.join(projectRoot, "desktop", "session-templates")
  ];

  return templateSourceCandidates.find((candidate) => {
    if (!existsSync(candidate)) {
      return false;
    }
    try {
      return statSync(candidate).isDirectory();
    } catch {
      return false;
    }
  });
}

function copySessionTemplates(sourceDirectory: string, targetDirectory: string): void {
  mkdirSync(targetDirectory, { recursive: true });
  const entries = readdirSync(sourceDirectory, { withFileTypes: true });

  for (const entry of entries) {
    const sourcePath = path.join(sourceDirectory, entry.name);
    const targetPath = path.join(targetDirectory, entry.name);

    if (entry.isDirectory()) {
      copySessionTemplates(sourcePath, targetPath);
      continue;
    }
    if (!entry.isFile() || existsSync(targetPath)) {
      continue;
    }
    copyFileSync(sourcePath, targetPath);
  }
}

function smokeLog(message: string): void {
  const logPath = process.env.FEEDGRAB_DESKTOP_SMOKE_LOG_FILE;
  if (!logPath) {
    return;
  }
  try {
    mkdirSync(path.dirname(logPath), { recursive: true });
    appendFileSync(logPath, `[${new Date().toISOString()}] ${message}\n`, "utf8");
  } catch {
    // Smoke logging must never affect normal app startup.
  }
}

function loadAppWindowIcon(): Electron.NativeImage | string {
  const iconPath = existsSync(appWindowIconPath) ? appWindowIconPath : appIconPath;
  const icon = nativeImage.createFromPath(iconPath);
  return icon.isEmpty() ? appIconPath : icon;
}

function forwardWorkerEvent(event: FeedgrabWorkerEvent): void {
  if (event.event === "artifact" && event.artifact?.path) {
    allowedOpenPaths.add(path.resolve(event.artifact.path));
  }
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) {
      window.webContents.send("feedgrab:workerEvent", event);
    }
  }
}

function createWindow(): void {
  smokeLog("creating browser window");
  const preload = path.join(__dirname, "preload.cjs");
  const window = new BrowserWindow({
    width: 1240,
    height: 820,
    minWidth: 960,
    minHeight: 680,
    title: "feedgrab 桌面版",
    icon: loadAppWindowIcon(),
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

  smokeLog(`loading renderer from ${path.join(__dirname, "../dist-renderer/index.html")}`);
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
    smokeLog("renderer did-finish-load");
    const delayMs = Number(process.env.FEEDGRAB_DESKTOP_SCREENSHOT_DELAY_MS ?? "1200");
    setTimeout(() => {
      void captureSmokeScreenshot(window, screenshotPath).catch((error: unknown) => {
        smokeLog(`screenshot failed: ${error instanceof Error ? error.message : String(error)}`);
      });
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
  smokeLog(`capturing screenshot to ${screenshotPath}`);
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
  smokeLog(`screenshot written (${png.length} bytes)`);
  app.quit();
}

function registerIpc(): void {
  ipcMain.handle("feedgrab:ping", () => currentWorker().ping());
  ipcMain.handle("feedgrab:detectPlatform", (_event, url: string) => currentWorker().detectPlatform(url));
  ipcMain.handle("feedgrab:startFetch", (_event, request: FetchRequest) => {
    if (!isValidFetchRequest(request)) {
      throw new Error("无效抓取请求");
    }
    rememberOpenRoot(request.outputDirectory);
    return currentWorker().startFetch(request);
  });
  ipcMain.handle("feedgrab:cancelJob", (_event, jobId: string) => currentWorker().cancelJob(jobId));
  ipcMain.handle("feedgrab:doctor", () => currentWorker().doctor());
  ipcMain.handle("feedgrab:repairDoctor", (_event, checkName: string) => {
    if (typeof checkName !== "string" || checkName.trim().length === 0) {
      throw new Error("无效诊断修复项");
    }
    return currentWorker().repairDoctor(checkName);
  });
  ipcMain.handle("feedgrab:settingsSnapshot", async () => {
    const snapshot = await currentWorker().settingsSnapshot();
    rememberOpenRoot(snapshot.outputDirectory);
    return snapshot;
  });
  ipcMain.handle("feedgrab:settingsSchema", () => currentWorker().settingsSchema());
  ipcMain.handle("feedgrab:settingsUpdate", async (_event, values: Record<string, SettingsFieldValue>) => {
    if (!isValidSettingsUpdate(values)) {
      throw new Error("无效设置更新");
    }
    const result = await currentWorker().settingsUpdate(values);
    if (result.ok) {
      await applyElectronProxySettings();
    }
    return result;
  });
  ipcMain.handle("feedgrab:ensureChromeCdp", (_event, port?: number) => ensureChromeCdp(port));
  ipcMain.handle("feedgrab:loginStatus", (_event, request?: LoginStatusRequest) => currentWorker().loginStatus(request));
  ipcMain.handle("feedgrab:importLoginSessions", (_event, sourceDirectory?: string, platform?: SupportedPlatform) => {
    if (sourceDirectory !== undefined && typeof sourceDirectory !== "string") {
      throw new Error("无效 sessions 来源目录");
    }
    if (platform !== undefined && !isSupportedPlatform(platform)) {
      throw new Error("无效平台");
    }
    return currentWorker().importLoginSessions(sourceDirectory, platform);
  });
  ipcMain.handle("feedgrab:loginPlatform", async (_event, platform: SupportedPlatform) => {
    if (!isSupportedPlatform(platform)) {
      throw new Error("无效平台");
    }
    await ensureChromeCdpForLogin();
    return currentWorker().loginPlatform(platform);
  });
  ipcMain.handle("feedgrab:outputList", () => currentWorker().outputList());
  ipcMain.handle("feedgrab:chooseOutputDirectory", async (_event, options?: DirectorySelectionOptions) => {
    const title = typeof options?.title === "string" && options.title.trim() ? options.title.trim() : "选择目录";
    const result = await dialog.showOpenDialog({
      title,
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
  ipcMain.handle("feedgrab:fetchRemoteMarkdown", async (_event, url: string) => fetchRemoteMarkdown(url));
}

async function fetchRemoteMarkdown(url: string): Promise<{ ok: boolean; markdown?: string; error?: string }> {
  if (!allowedRemoteMarkdownUrls.has(url)) {
    return { ok: false, error: "remote markdown url is not allowed" };
  }
  try {
    await applyElectronProxySettings();
    const response = await net.fetch(url, {
      cache: "no-store"
    });
    if (!response.ok) {
      return { ok: false, error: `HTTP ${response.status}` };
    }
    return { ok: true, markdown: await response.text() };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

async function applyElectronProxySettings(): Promise<void> {
  const settings = readDesktopSettings();
  const enabled = normalizeBoolean(settings.FEEDGRAB_PROXY_ENABLED ?? process.env.FEEDGRAB_PROXY_ENABLED);
  const proxyUrl = stringValue(settings.FEEDGRAB_PROXY_URL ?? process.env.FEEDGRAB_PROXY_URL);
  const noProxy = stringValue(settings.FEEDGRAB_NO_PROXY ?? process.env.FEEDGRAB_NO_PROXY) || "127.0.0.1,localhost";
  if (!enabled || !proxyUrl) {
    currentProxyCredentials = undefined;
    await session.defaultSession.setProxy({ mode: "system" });
    return;
  }

  const parsed = parseProxyUrl(proxyUrl);
  if (!parsed) {
    currentProxyCredentials = undefined;
    await session.defaultSession.setProxy({ mode: "system" });
    return;
  }
  currentProxyCredentials = parsed.credentials;
  await session.defaultSession.setProxy({
    mode: "fixed_servers",
    proxyRules: parsed.proxyRules,
    proxyBypassRules: noProxy
  });
}

function readDesktopSettings(): Record<string, unknown> {
  const settingsPath = process.env.FEEDGRAB_SETTINGS_PATH || path.join(app.getPath("userData"), "settings.json");
  try {
    const payload = JSON.parse(readFileSync(settingsPath, "utf8")) as Record<string, unknown>;
    const values = payload.values && typeof payload.values === "object" ? payload.values : payload;
    return values && typeof values === "object" ? (values as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function parseProxyUrl(proxyUrl: string): {
  proxyRules: string;
  credentials?: { username: string; password: string };
} | undefined {
  try {
    const parsed = new URL(proxyUrl);
    if (!["http:", "https:", "socks5:"].includes(parsed.protocol) || !parsed.hostname || !parsed.port) {
      return undefined;
    }
    const host = parsed.hostname.includes(":") && !parsed.hostname.startsWith("[") ? `[${parsed.hostname}]` : parsed.hostname;
    const proxyRules = `${parsed.protocol}//${host}:${parsed.port}`;
    const username = decodeURIComponent(parsed.username || "");
    const password = decodeURIComponent(parsed.password || "");
    return {
      proxyRules,
      credentials: username || password ? { username, password } : undefined
    };
  } catch {
    return undefined;
  }
}

async function ensureChromeCdp(requestedPort?: number): Promise<ChromeCdpEnsureResult> {
  const port = normalizeCdpPort(requestedPort);
  const existing = await probeChromeCdp(port);
  if (existing.ok) {
    return {
      ok: true,
      port,
      started: false,
      message: "Chrome CDP 已连接",
      url: existing.url
    };
  }

  const chromePath = findChromeExecutable();
  if (!chromePath) {
    return {
      ok: false,
      port,
      started: false,
      message: "未找到 Chrome/Chromium/Edge 可执行文件",
      error: "请安装 Chrome，或通过 CHROME_PATH 指定浏览器路径。"
    };
  }

  const profileDir = path.join(app.getPath("userData"), "chrome-cdp-profile");
  try {
    mkdirSync(profileDir, { recursive: true });
    const child = spawn(
      chromePath,
      [
        `--remote-debugging-port=${port}`,
        `--user-data-dir=${profileDir}`,
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank"
      ],
      {
        detached: true,
        stdio: "ignore",
        windowsHide: true
      }
    );
    child.unref();
  } catch (error) {
    return {
      ok: false,
      port,
      started: false,
      chromePath,
      message: "Chrome CDP 启动失败",
      error: error instanceof Error ? error.message : String(error)
    };
  }

  for (let attempt = 0; attempt < chromeCdpStartupAttempts; attempt += 1) {
    await delay(chromeCdpStartupPollMs);
    const started = await probeChromeCdp(port);
    if (started.ok) {
      return {
        ok: true,
        port,
        started: true,
        chromePath,
        message: "已启动 Chrome CDP 并连接成功",
        url: started.url
      };
    }
  }

  return {
    ok: false,
    port,
    started: false,
    chromePath,
    message: "Chrome 已启动，但 CDP 端口未在预期时间内响应",
    error: `请检查端口 ${port} 是否被占用，或浏览器是否被安全软件拦截。`
  };
}

async function ensureChromeCdpForLogin(): Promise<void> {
  const settings = readDesktopSettings();
  const enabled = normalizeBoolean(settings.CHROME_CDP_LOGIN ?? process.env.CHROME_CDP_LOGIN);
  if (!enabled) {
    return;
  }

  const port = normalizeCdpPort(numberValue(settings.CHROME_CDP_PORT ?? process.env.CHROME_CDP_PORT));
  const result = await ensureChromeCdp(port);
  if (!result.ok) {
    smokeLog(`Chrome CDP login ensure failed: ${result.error ?? result.message}`);
  }
}

async function probeChromeCdp(port: number): Promise<{ ok: boolean; url?: string; error?: string }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), chromeCdpProbeTimeoutMs);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/version`, {
      signal: controller.signal
    });
    if (!response.ok) {
      return { ok: false, error: `HTTP ${response.status}` };
    }
    const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    const debuggerUrl = typeof payload.webSocketDebuggerUrl === "string" ? payload.webSocketDebuggerUrl : undefined;
    return { ok: true, url: debuggerUrl };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  } finally {
    clearTimeout(timeout);
  }
}

function normalizeCdpPort(port?: number): number {
  if (Number.isFinite(port) && port && port >= 1 && port <= 65535) {
    return Math.floor(port);
  }
  return 9222;
}

function numberValue(value: unknown): number | undefined {
  if (typeof value === "number") {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function findChromeExecutable(): string | undefined {
  const programFiles = envPath("PROGRAMFILES");
  const programFilesX86 = envPath("PROGRAMFILES(X86)");
  const localAppData = envPath("LOCALAPPDATA");
  const candidates = [
    envPath("CHROME_PATH"),
    programFiles ? path.join(programFiles, "Google", "Chrome", "Application", "chrome.exe") : undefined,
    programFilesX86 ? path.join(programFilesX86, "Google", "Chrome", "Application", "chrome.exe") : undefined,
    localAppData ? path.join(localAppData, "Google", "Chrome", "Application", "chrome.exe") : undefined,
    programFiles ? path.join(programFiles, "Chromium", "Application", "chrome.exe") : undefined,
    programFiles ? path.join(programFiles, "Microsoft", "Edge", "Application", "msedge.exe") : undefined,
    programFilesX86 ? path.join(programFilesX86, "Microsoft", "Edge", "Application", "msedge.exe") : undefined,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser"
  ];
  return candidates.find((candidate): candidate is string => typeof candidate === "string" && existsSync(candidate));
}

function envPath(name: string): string | undefined {
  const value = process.env[name];
  return value && value.trim().length > 0 ? value : undefined;
}

function normalizeBoolean(value: unknown): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  const text = String(value ?? "").trim().toLowerCase();
  return text === "true" || text === "1" || text === "yes" || text === "on";
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
  if (
    !request ||
    !Array.isArray(request.urls) ||
    typeof request.outputDirectory !== "string" ||
    request.outputDirectory.trim().length === 0
  ) {
    return false;
  }
  const hasUrls = request.urls.length > 0;
  const hasTargets = Array.isArray(request.targets) && request.targets.length > 0;
  if (hasUrls) {
    return request.urls.every((url) => typeof url === "string" && /^https?:\/\//i.test(url));
  }
  return Boolean(
    hasTargets &&
      request.targets?.every((target) => typeof target === "string" && target.trim().length > 0) &&
      isSupportedPlatform(request.platform) &&
      (request.mode === "search" || request.mode === "account")
  );
}

function isValidSettingsUpdate(values: unknown): values is Record<string, SettingsFieldValue> {
  if (!values || typeof values !== "object" || Array.isArray(values)) {
    return false;
  }
  return Object.entries(values as Record<string, unknown>).every(
    ([name, value]) =>
      typeof name === "string" &&
      name.length > 0 &&
      (typeof value === "string" || typeof value === "number" || typeof value === "boolean")
  );
}

function isSupportedPlatform(value: unknown): value is SupportedPlatform {
  return (
    typeof value === "string" &&
    [
      "twitter",
      "xhs",
      "youtube",
      "bilibili",
      "wechat",
      "github",
      "linuxdo",
      "idcflare",
      "feishu",
      "kdocs",
      "flowus",
      "zhihu",
      "zsxq",
      "web",
      "unknown"
    ].includes(value)
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

void app.whenReady().then(async () => {
  smokeLog("app ready");
  if (process.platform !== "darwin") {
    Menu.setApplicationMenu(null);
  }
  await applyElectronProxySettings();
  initializeWorker();
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
}).catch((error: unknown) => {
  smokeLog(`app startup failed: ${error instanceof Error ? error.stack ?? error.message : String(error)}`);
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
