import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

export type FeedgrabRuntimeSource = "bundled" | "system-python";

export type FeedgrabRuntimeResolution = {
  source: FeedgrabRuntimeSource;
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
  runtimeRoot: string;
  workerPath: string;
  browserPath: string;
  bundledWorkerAvailable: boolean;
  bundledBrowserAvailable: boolean;
};

export type FeedgrabRuntimeOptions = {
  platform?: NodeJS.Platform;
  projectRoot: string;
  resourcesPath: string;
  userDataPath: string;
  env?: NodeJS.ProcessEnv;
  exists?: (target: string) => boolean;
  chromiumVersion?: string;
};

function defaultUserAgentForPlatform(platform: NodeJS.Platform, chromiumVersion?: string): string {
  if (!chromiumVersion) {
    return "";
  }
  const osToken =
    platform === "win32"
      ? "Windows NT 10.0; Win64; x64"
      : platform === "darwin"
        ? "Macintosh; Intel Mac OS X 10_15_7"
        : "X11; Linux x86_64";
  return `Mozilla/5.0 (${osToken}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${chromiumVersion} Safari/537.36`;
}

export function resolveFeedgrabRuntime(options: FeedgrabRuntimeOptions): FeedgrabRuntimeResolution {
  const env = options.env ?? process.env;
  const exists = options.exists ?? existsSync;
  const platform = options.platform ?? process.platform;
  const defaultBrowserUserAgent =
    env.BROWSER_USER_AGENT ||
    defaultUserAgentForPlatform(platform, options.chromiumVersion ?? process.versions.chrome);
  const runtimeRoot = env.FEEDGRAB_DESKTOP_RUNTIME_DIR || path.join(options.resourcesPath, "feedgrab-runtime");
  const workerName = platform === "win32" ? "feedgrab-worker.exe" : "feedgrab-worker";
  const workerPath = path.join(runtimeRoot, "feedgrab-worker", workerName);
  const bundledBrowserPath = path.join(runtimeRoot, "ms-playwright");
  const settingsPath = path.join(options.userDataPath, "settings.json");
  const managedBrowserPath = path.join(options.userDataPath, "runtime", "ms-playwright");
  const bundledWorkerAvailable = exists(workerPath);
  const bundledBrowserAvailable = exists(bundledBrowserPath);
  const packagedInstallRoot = path.dirname(options.resourcesPath);
  const installSessionsPath = bundledWorkerAvailable
    ? path.join(packagedInstallRoot, "sessions")
    : path.join(options.projectRoot, "desktop", "sessions");
  const browserPath = bundledBrowserAvailable ? bundledBrowserPath : managedBrowserPath;
  const baseEnv: NodeJS.ProcessEnv = {
    ...env,
    FEEDGRAB_DESKTOP_RUNTIME_ROOT: runtimeRoot,
    FEEDGRAB_INSTALL_SESSIONS_DIR: env.FEEDGRAB_INSTALL_SESSIONS_DIR || installSessionsPath,
    FEEDGRAB_PROJECT_SESSIONS_DIR: env.FEEDGRAB_PROJECT_SESSIONS_DIR || path.join(options.projectRoot, "sessions"),
    FEEDGRAB_SETTINGS_PATH: env.FEEDGRAB_SETTINGS_PATH || settingsPath,
    PLAYWRIGHT_BROWSERS_PATH: browserPath,
    PLAYWRIGHT_SKIP_BROWSER_GC: "1",
    PYTHONIOENCODING: "utf-8",
    ...(defaultBrowserUserAgent ? { BROWSER_USER_AGENT: defaultBrowserUserAgent } : {})
  };
  const envWithProxy = projectProxyEnvironment(baseEnv, env.FEEDGRAB_SETTINGS_PATH || settingsPath);

  if (bundledWorkerAvailable) {
    return {
      source: "bundled",
      command: workerPath,
      args: [],
      cwd: options.userDataPath,
      env: {
        ...envWithProxy,
        FEEDGRAB_DATA_DIR: env.FEEDGRAB_DATA_DIR || path.join(options.userDataPath, "sessions"),
        OUTPUT_DIR: env.OUTPUT_DIR || path.join(options.userDataPath, "output")
      },
      runtimeRoot,
      workerPath,
      browserPath,
      bundledWorkerAvailable,
      bundledBrowserAvailable
    };
  }

  return {
    source: "system-python",
    command: env.FEEDGRAB_DESKTOP_PYTHON || "python",
    args: ["-m", "feedgrab.worker"],
    cwd: options.projectRoot,
    env: envWithProxy,
    runtimeRoot,
    workerPath,
    browserPath,
    bundledWorkerAvailable,
    bundledBrowserAvailable
  };
}

function projectProxyEnvironment(env: NodeJS.ProcessEnv, settingsPath: string): NodeJS.ProcessEnv {
  const values = readSettingsValues(settingsPath);
  const enabled = normalizeBoolean(values.FEEDGRAB_PROXY_ENABLED ?? env.FEEDGRAB_PROXY_ENABLED);
  const proxyUrl = stringValue(values.FEEDGRAB_PROXY_URL ?? env.FEEDGRAB_PROXY_URL);
  const noProxy = stringValue(values.FEEDGRAB_NO_PROXY ?? env.FEEDGRAB_NO_PROXY) || "127.0.0.1,localhost";
  if (!enabled || !proxyUrl) {
    return env;
  }
  return {
    ...env,
    FEEDGRAB_PROXY_ENABLED: "true",
    FEEDGRAB_PROXY_URL: proxyUrl,
    FEEDGRAB_NO_PROXY: noProxy,
    HTTP_PROXY: proxyUrl,
    HTTPS_PROXY: proxyUrl,
    ALL_PROXY: proxyUrl,
    NO_PROXY: noProxy,
    http_proxy: proxyUrl,
    https_proxy: proxyUrl,
    all_proxy: proxyUrl,
    no_proxy: noProxy
  };
}

function readSettingsValues(settingsPath: string): Record<string, unknown> {
  try {
    const payload = JSON.parse(readFileSync(settingsPath, "utf8")) as Record<string, unknown>;
    const values = payload.values && typeof payload.values === "object" ? payload.values : payload;
    return values && typeof values === "object" ? (values as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function normalizeBoolean(value: unknown): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  return String(value ?? "").trim().toLowerCase() === "true" || String(value ?? "").trim() === "1";
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
