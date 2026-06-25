import { existsSync } from "node:fs";
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
};

export function resolveFeedgrabRuntime(options: FeedgrabRuntimeOptions): FeedgrabRuntimeResolution {
  const env = options.env ?? process.env;
  const exists = options.exists ?? existsSync;
  const platform = options.platform ?? process.platform;
  const runtimeRoot = env.FEEDGRAB_DESKTOP_RUNTIME_DIR || path.join(options.resourcesPath, "feedgrab-runtime");
  const workerName = platform === "win32" ? "feedgrab-worker.exe" : "feedgrab-worker";
  const workerPath = path.join(runtimeRoot, "feedgrab-worker", workerName);
  const bundledBrowserPath = path.join(runtimeRoot, "ms-playwright");
  const managedBrowserPath = path.join(options.userDataPath, "runtime", "ms-playwright");
  const bundledWorkerAvailable = exists(workerPath);
  const bundledBrowserAvailable = exists(bundledBrowserPath);
  const browserPath = bundledBrowserAvailable ? bundledBrowserPath : managedBrowserPath;
  const baseEnv: NodeJS.ProcessEnv = {
    ...env,
    FEEDGRAB_DESKTOP_RUNTIME_ROOT: runtimeRoot,
    PLAYWRIGHT_BROWSERS_PATH: browserPath,
    PLAYWRIGHT_SKIP_BROWSER_GC: "1",
    PYTHONIOENCODING: "utf-8"
  };

  if (bundledWorkerAvailable) {
    return {
      source: "bundled",
      command: workerPath,
      args: [],
      cwd: options.userDataPath,
      env: {
        ...baseEnv,
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
    env: baseEnv,
    runtimeRoot,
    workerPath,
    browserPath,
    bundledWorkerAvailable,
    bundledBrowserAvailable
  };
}
