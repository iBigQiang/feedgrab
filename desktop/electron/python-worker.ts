import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";

import type {
  DoctorCheck,
  DoctorRepairResult,
  DoctorSnapshot,
  FeedgrabWorkerEvent,
  FetchJobSnapshot,
  FetchRequest,
  LoginPlatformResult,
  LoginSessionImportResult,
  LoginStatus,
  LoginStatusRequest,
  OutputArtifact,
  SettingsFieldSchema,
  SettingsFieldValue,
  SettingsPlatformSchema,
  SettingsSchema,
  SettingsSnapshot,
  SettingsUpdateResult,
  SupportedPlatform,
  WorkerPing
} from "./ipc-types.js";

export type PythonWorkerClient = {
  ping: () => Promise<WorkerPing>;
  onEvent: (callback: (event: FeedgrabWorkerEvent) => void) => () => void;
  detectPlatform: (url: string) => Promise<SupportedPlatform>;
  startFetch: (request: FetchRequest) => Promise<FetchJobSnapshot[]>;
  cancelJob: (jobId: string) => Promise<FetchJobSnapshot>;
  doctor: () => Promise<DoctorSnapshot>;
  repairDoctor: (checkName: string) => Promise<DoctorRepairResult>;
  settingsSnapshot: () => Promise<SettingsSnapshot>;
  settingsSchema: () => Promise<SettingsSchema>;
  settingsUpdate: (values: Record<string, SettingsFieldValue>) => Promise<SettingsUpdateResult>;
  loginStatus: (request?: LoginStatusRequest) => Promise<LoginStatus[]>;
  importLoginSessions: (sourceDirectory?: string, platform?: SupportedPlatform) => Promise<LoginSessionImportResult>;
  loginPlatform: (platform: SupportedPlatform) => Promise<LoginPlatformResult>;
  outputList: () => Promise<OutputArtifact[]>;
};

type PendingRequest = {
  resolve: (event: FeedgrabWorkerEvent) => void;
  reject: (error: Error) => void;
};

export type PythonWorkerClientOptions = {
  command?: string;
  args?: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
};

const platformMatchers: Array<[SupportedPlatform, RegExp]> = [
  ["twitter", /(?:^|\.)x\.com|(?:^|\.)twitter\.com/i],
  ["xhs", /xiaohongshu\.com|xhslink\.com/i],
  ["youtube", /youtube\.com|youtu\.be/i],
  ["bilibili", /bilibili\.com|b23\.tv/i],
  ["wechat", /mp\.weixin\.qq\.com/i],
  ["github", /github\.com/i],
  ["linuxdo", /linux\.do/i],
  ["idcflare", /idcflare\.com/i],
  ["feishu", /feishu\.cn|larksuite\.com|larkoffice\.com/i],
  ["kdocs", /kdocs\.cn|wps\.cn/i],
  ["flowus", /flowus\.cn/i],
  ["zhihu", /zhihu\.com/i],
  ["zsxq", /zsxq\.com/i]
];

const loginPlatforms: SupportedPlatform[] = [
  "twitter",
  "xhs",
  "wechat",
  "feishu",
  "kdocs",
  "flowus",
  "zhihu",
  "linuxdo",
  "idcflare",
  "zsxq",
  "github",
  "youtube",
  "bilibili",
  "web"
];

const legacyDesktopDefaultPaths = new Set([
  "e:\\obsidian\\qiang_obsidian\\inbox"
]);

export function detectPlatformFromUrl(url: string): SupportedPlatform {
  const trimmed = url.trim();
  if (trimmed.length === 0) {
    return "unknown";
  }

  const match = platformMatchers.find(([, pattern]) => pattern.test(trimmed));
  return match?.[0] ?? "web";
}

function titleFromUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const tail = parsed.pathname.split("/").filter(Boolean).at(-1);
    return tail ? decodeURIComponent(tail).slice(0, 80) : parsed.hostname;
  } catch {
    return "未命名内容";
  }
}

function platformFolder(platform: SupportedPlatform): string {
  const folders: Record<SupportedPlatform, string> = {
    twitter: "X",
    xhs: "XHS",
    youtube: "YouTube",
    bilibili: "Bilibili",
    wechat: "mpweixin",
    github: "GitHub",
    linuxdo: "LinuxDo",
    idcflare: "IDCFlare",
    feishu: "Feishu",
    kdocs: "KDocs",
    flowus: "FlowUs",
    zhihu: "Zhihu",
    zsxq: "Zsxq",
    web: "Web",
    unknown: "Manual"
  };
  return folders[platform];
}

function safeFileStem(url: string): string {
  const title = titleFromUrl(url).toLowerCase();
  return title.replace(/[^a-z0-9\u4e00-\u9fa5]+/gi, "-").replace(/^-|-$/g, "") || "content";
}

export function createMockPythonWorkerClient(): PythonWorkerClient {
  const jobs = new Map<string, FetchJobSnapshot>();
  const listeners = new Set<(event: FeedgrabWorkerEvent) => void>();
  const outputs: OutputArtifact[] = [];
  const emit = (event: FeedgrabWorkerEvent): void => {
    for (const listener of listeners) {
      listener(event);
    }
  };

  return {
    onEvent(callback) {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    ping() {
      return Promise.resolve({ ok: true, worker: "mock" });
    },
    detectPlatform(url) {
      return Promise.resolve(detectPlatformFromUrl(url));
    },
    startFetch(request) {
      const targets = (request.targets ?? []).map((target) => target.trim()).filter(Boolean);
      if (request.urls.length === 0 && targets.length > 0) {
        const id = `mock-job-${jobs.size + 1}`;
        const platform = isSupportedPlatform(request.platform) ? request.platform : "unknown";
        const target = targets.join(", ");
        const folder = platformFolder(platform);
        const markdownPath = `${request.outputDirectory}\\${folder}\\search\\${safeFileStem(target)}.md`;
        const job: FetchJobSnapshot = {
          id,
          url: request.commandPreview ?? target,
          target,
          targets,
          platform,
          mode: request.mode,
          commandPreview: request.commandPreview,
          status: "running",
          outputDirectory: request.outputDirectory,
          markdownPath,
          attachments: [],
          createdAt: new Date().toISOString()
        };
        jobs.set(job.id, job);
        outputs.unshift({
          id: `${job.id}-artifact`,
          title: target,
          platform: folder,
          markdownPath,
          attachments: [],
          createdAt: job.createdAt
        });
        setTimeout(() => {
          emit({ id: job.id, event: "job_started", method: "fetch", result: { total: 1 } });
          emit({ id: job.id, event: "log", method: "fetch", level: "info", message: "mock structured fetch job started" });
          emit({ id: job.id, event: "done", method: "fetch", result: { fetched: 1, errors: 0, command: request.commandPreview } });
        }, 0);
        return Promise.resolve([job]);
      }

      const createdJobs = request.urls.map((url, index) => {
        const platform = detectPlatformFromUrl(url);
        const folder = platformFolder(platform);
        const id = `mock-job-${jobs.size + index + 1}`;
        const markdownPath = `${request.outputDirectory}\\${folder}\\${safeFileStem(url)}.md`;
        const job: FetchJobSnapshot = {
          id,
          url,
          platform,
          status: "running",
          outputDirectory: request.outputDirectory,
          markdownPath,
          attachments: [`${request.outputDirectory}\\${folder}\\attachments\\${safeFileStem(url)}-image.png`],
          createdAt: new Date().toISOString()
        };
        return job;
      });

      for (const job of createdJobs) {
        jobs.set(job.id, job);
        outputs.unshift({
          id: `${job.id}-artifact`,
          title: titleFromUrl(job.url),
          platform: platformFolder(job.platform),
          markdownPath: job.markdownPath ?? "",
          attachments: job.attachments ?? [],
          createdAt: job.createdAt
        });
      }
      setTimeout(() => {
        for (const job of createdJobs) {
          emit({ id: job.id, event: "job_started", method: "fetch", result: { total: 1 } });
          emit({ id: job.id, event: "log", method: "fetch", level: "info", message: "mock fetch job started" });
          emit({ id: job.id, event: "progress", method: "fetch", stage: "fetch", message: "fetching", url: job.url });
          emit({
            id: job.id,
            event: "artifact",
            method: "fetch",
            url: job.url,
            artifact: { kind: "markdown", path: job.markdownPath }
          });
          emit({ id: job.id, event: "done", method: "fetch", result: { fetched: 1, errors: 0 } });
        }
      }, 0);

      return Promise.resolve(createdJobs);
    },
    cancelJob(jobId) {
      const job = jobs.get(jobId);
      if (!job) {
        return Promise.resolve({
          id: jobId,
          url: "",
          platform: "unknown",
          status: "cancelled",
          outputDirectory: "",
          createdAt: new Date().toISOString()
        });
      }
      const cancelled = { ...job, status: "cancelled" as const };
      jobs.set(jobId, cancelled);
      emit({ id: jobId, event: "cancelled", method: "fetch", result: { cancelled: true } });
      return Promise.resolve(cancelled);
    },
    doctor() {
      const checks: DoctorCheck[] = [
        { name: "python", label: "Python", status: "ok", message: "mock 3.12-compatible" },
        { name: "node", label: "Node.js", status: "ok", message: process.version },
        { name: "chromium", label: "Chromium", status: "ok", message: process.versions.chrome ?? "mock" },
        {
          name: "playwright_chromium",
          label: "Playwright Chromium",
          status: "warning",
          message: "mock missing",
          repair: { id: "playwright-browsers", label: "安装/更新", available: true }
        }
      ];
      return Promise.resolve({
        python: "mock 3.12-compatible",
        browser: "mock",
        network: "disabled",
        writableOutput: true,
        notes: [],
        checks
      });
    },
    repairDoctor(checkName) {
      return Promise.resolve({
        ok: true,
        action: checkName,
        message: checkName === "all" ? "所有依赖已检查更新" : "依赖已更新"
      });
    },
    settingsSnapshot() {
      return Promise.resolve({
        outputDirectory: "",
        obsidianVault: "",
        effectiveOutputDirectory: "",
        concurrency: 2,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      });
    },
    settingsSchema() {
      return Promise.resolve({
        basic: [
          {
            name: "OUTPUT_DIR",
            label: "输出目录",
            type: "path",
            value: "",
            description: "Markdown 和附件的默认输出目录"
          },
          { name: "DOWNLOAD_IMAGES", label: "下载图片", type: "boolean", value: true }
        ],
        platforms: [
          {
            id: "x",
            label: "X / Twitter",
            fields: [
              { name: "X_SEARCH_DAYS", label: "搜索天数", type: "number", value: 7 },
              { name: "TWITTERAPI_IO_KEY", label: "TwitterAPI.io Key", type: "secret", value: "[redacted]", secret: true }
            ]
          },
          {
            id: "feishu",
            label: "文档平台",
            fields: [
              { name: "FEISHU_APP_ID", label: "App ID", type: "string", value: "" },
              { name: "FEISHU_APP_SECRET", label: "App Secret", type: "secret", value: "[redacted]", secret: true },
              { name: "FLOWUS_DOWNLOAD_IMAGES", label: "FlowUs 图片下载到本地", type: "boolean", value: false },
              { name: "GITHUB_TOKEN", label: "GitHub Token", type: "secret", value: "[redacted]", secret: true }
            ]
          },
          {
            id: "discourse",
            label: "Discourse论坛",
            fields: [
              {
                name: "LINUXDO_REPLY_MODE",
                label: "回复模式",
                type: "select",
                value: "author",
                options: [
                  { label: "只看楼主", value: "author" },
                  { label: "全部楼层", value: "all" },
                  { label: "仅主贴", value: "none" }
                ]
              }
            ]
          },
          {
            id: "video_podcast",
            label: "视频播客",
            fields: [
              { name: "YOUTUBE_API_KEY", label: "YouTube Data API Key", type: "secret", value: "[redacted]", secret: true },
              { name: "BILIBILI_SUBTITLE_ENABLED", label: "B 站字幕抓取", type: "boolean", value: true },
              { name: "XIAOYUZHOU_WHISPER", label: "小宇宙 Whisper 转录", type: "boolean", value: true },
              { name: "XIMALAYA_WHISPER", label: "喜马拉雅 Whisper 转录", type: "boolean", value: true }
            ]
          },
          {
            id: "zhihu",
            label: "知乎",
            fields: [
              { name: "ZHIHU_CDP_ENABLED", label: "知乎复用 Chrome CDP", type: "boolean", value: true },
              { name: "ZHIHU_SEARCH_LIMIT", label: "知乎搜索结果数", type: "number", value: 20 }
            ]
          }
        ]
      });
    },
    settingsUpdate(values) {
      return Promise.resolve({
        ok: true,
        updated: Object.entries(values).map(([name, value]) => ({ name, value: String(value) }))
      });
    },
    loginStatus(request) {
      const now = new Date().toISOString();
      const statuses: LoginStatus[] = [
        { platform: "twitter", label: "X / Twitter", status: "missing", lastChecked: now },
        { platform: "xhs", label: "小红书", status: "expired", lastChecked: now },
        { platform: "wechat", label: "微信公众号", status: "connected", lastChecked: now },
        { platform: "feishu", label: "飞书", status: "missing", lastChecked: now },
        { platform: "kdocs", label: "金山文档", status: "missing", lastChecked: now },
        { platform: "flowus", label: "FlowUs", status: "missing", lastChecked: now },
        { platform: "zhihu", label: "知乎", status: "missing", lastChecked: now },
        { platform: "linuxdo", label: "LinuxDo", status: "connected", lastChecked: now },
        { platform: "idcflare", label: "IDCFlare", status: "missing", lastChecked: now },
        { platform: "zsxq", label: "知识星球", status: "missing", lastChecked: now },
        { platform: "github", label: "GitHub", status: "notRequired", lastChecked: now },
        { platform: "youtube", label: "YouTube", status: "notRequired", lastChecked: now },
        { platform: "bilibili", label: "Bilibili", status: "notRequired", lastChecked: now },
        { platform: "web", label: "网页", status: "notRequired", lastChecked: now }
      ];
      const platforms = request?.platforms;
      return Promise.resolve(platforms ? statuses.filter((item) => platforms.includes(item.platform)) : statuses);
    },
    importLoginSessions(sourceDirectory, platform) {
      const sourceRoot = sourceDirectory || "D:\\AiCode\\feedgrab\\desktop\\sessions";
      const candidates = platform ? [platform] : ["twitter", "xhs", "wechat", "linuxdo"];
      return Promise.resolve({
        ok: true,
        sourceDirectory: sourceRoot,
        targetDirectory: "D:\\feedgrab Desktop\\sessions",
        imported: candidates.map((item) => ({
          source: `${sourceRoot}\\${item}.json`,
          target: `D:\\feedgrab Desktop\\sessions\\${item}.json`
        })),
        skipped: [],
        disabled: [],
        ignored: []
      });
    },
    loginPlatform(platform) {
      return Promise.resolve({
        ok: true,
        platform,
        status: "connected",
        message: `${platformLabel(platform)} 登录流程已启动`
      });
    },
    outputList() {
      return Promise.resolve(outputs);
    }
  };
}

export function createPythonWorkerClient(options: PythonWorkerClientOptions = {}): PythonWorkerClient {
  return new JsonLinePythonWorkerClient(options);
}

class JsonLinePythonWorkerClient implements PythonWorkerClient {
  private child: ChildProcessWithoutNullStreams | undefined;
  private buffer = "";
  private seq = 1;
  private readonly pending = new Map<string, PendingRequest>();
  private readonly artifacts = new Map<string, string>();
  private readonly activeFetchJobs = new Map<string, FetchJobSnapshot>();
  private readonly listeners = new Set<(event: FeedgrabWorkerEvent) => void>();
  private readonly command: string;
  private readonly args: string[];
  private readonly cwd: string | undefined;
  private readonly env: NodeJS.ProcessEnv | undefined;

  constructor(options: PythonWorkerClientOptions) {
    this.command = options.command ?? "python";
    this.args = options.args ?? ["-m", "feedgrab.worker"];
    this.cwd = options.cwd;
    this.env = options.env;
  }

  onEvent(callback: (event: FeedgrabWorkerEvent) => void): () => void {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  ping(): Promise<WorkerPing> {
    return this.request("ping", {}).then(() => ({ ok: true, worker: "python" }));
  }

  detectPlatform(url: string): Promise<SupportedPlatform> {
    return this.request("detect_platform", { url }).then((result) => {
      const platform = asRecord(result).platform;
      return isSupportedPlatform(platform) ? platform : "unknown";
    });
  }

  startFetch(request: FetchRequest): Promise<FetchJobSnapshot[]> {
    const urls = request.urls.map((url) => url.trim()).filter(Boolean);
    const targets = (request.targets ?? []).map((target) => target.trim()).filter(Boolean);
    if (urls.length === 0 && targets.length === 0) {
      return Promise.resolve([]);
    }
    this.ensureProcess();
    const child = this.child;
    if (!child) {
      return Promise.reject(new Error("Python worker 进程不可用"));
    }

    if (urls.length === 0 && targets.length > 0) {
      const platform = isSupportedPlatform(request.platform) ? request.platform : "unknown";
      const job: FetchJobSnapshot = {
        id: this.nextId("fetch"),
        url: request.commandPreview ?? targets.join(", "),
        target: targets.join(", "),
        targets,
        platform,
        mode: request.mode,
        commandPreview: request.commandPreview,
        status: "running",
        outputDirectory: request.outputDirectory,
        attachments: [],
        createdAt: new Date().toISOString()
      };
      const payload = {
        id: job.id,
        method: "fetch",
        params: {
          urls: [],
          targets,
          platform,
          mode: request.mode,
          command_preview: request.commandPreview,
          output_dir: request.outputDirectory
        }
      };
      this.activeFetchJobs.set(job.id, job);
      return new Promise((resolve, reject) => {
        const accepted = this.waitForJobAccepted(job.id, () => resolve([job]));
        child.stdin.write(`${JSON.stringify(payload)}\n`, (error?: Error | null) => {
          if (error) {
            accepted();
            this.activeFetchJobs.delete(job.id);
            reject(error);
          }
        });
      });
    }

    const jobs = urls.map<FetchJobSnapshot>((url) => ({
      id: this.nextId("fetch"),
      url,
      platform: detectPlatformFromUrl(url),
      status: "running",
      outputDirectory: request.outputDirectory,
      attachments: [],
      createdAt: new Date().toISOString()
    }));
    for (const job of jobs) {
      this.activeFetchJobs.set(job.id, job);
    }

    return new Promise((resolve, reject) => {
      let remaining = jobs.length;
      let failed = false;
      for (const job of jobs) {
        const payload = {
          id: job.id,
          method: "fetch",
          params: { urls: [job.url], output_dir: request.outputDirectory }
        };
        child.stdin.write(`${JSON.stringify(payload)}\n`, (error?: Error | null) => {
          if (failed) {
            return;
          }
          if (error) {
            failed = true;
            for (const pendingJob of jobs) {
              this.activeFetchJobs.delete(pendingJob.id);
            }
            reject(error);
            return;
          }
          remaining -= 1;
          if (remaining === 0) {
            resolve(jobs);
          }
        });
      }
    });
  }

  cancelJob(jobId: string): Promise<FetchJobSnapshot> {
    return this.request("cancel", { id: jobId }).then(() => ({
      id: jobId,
      url: "",
      platform: "unknown",
      status: "cancelled",
      outputDirectory: "",
      createdAt: new Date().toISOString()
    }));
  }

  doctor(): Promise<DoctorSnapshot> {
    return this.request("doctor", { modules: ["feedgrab", "playwright", "patchright", "browserforge", "curl_cffi"] }).then((result) => {
      const payload = asRecord(result);
      const checks = [
        ...(Array.isArray(payload.checks) ? payload.checks.map(normalizeDoctorCheck) : []),
        ...localRuntimeDoctorChecks(this.env)
      ].map((check) => withRepairMetadata(check));
      const chromium = checks.find((check) => check.name === "chromium");
      return {
        python: findDiagnosticMessage(checks, "python") ?? "unknown",
        browser: chromium?.status === "ok" ? "ready" : "missing",
        network: "unknown",
        writableOutput: findDiagnosticStatus(checks, "output_dir") !== "error",
        notes: [],
        checks
      };
    });
  }

  async repairDoctor(checkName: string): Promise<DoctorRepairResult> {
    const action = repairActionForCheckName(checkName);
    if (!action) {
      return {
        ok: false,
        action: checkName,
        message: "该诊断项暂无自动安装/更新动作"
      };
    }
    if (action === "all") {
      const results = await Promise.all([this.installPythonDependencies(), this.installPlaywrightChromium()]);
      const failed = results.find((result) => !result.ok);
      return {
        ok: !failed,
        action,
        message: failed ? `部分依赖安装失败：${failed.message}` : "所有依赖已检查更新",
        log: results.flatMap((result) => result.log ?? []),
        error: failed?.error
      };
    }
    if (action === "python-dependencies") {
      return this.installPythonDependencies();
    }
    return this.installPlaywrightChromium();
  }

  settingsSnapshot(): Promise<SettingsSnapshot> {
    return this.request("settings_snapshot", {}).then((result) => {
      const payload = asRecord(result);
      const items = Array.isArray(payload.items) ? payload.items.map(asRecord) : [];
      const env = this.runtimeEnvironment();
      const outputDirectory = findSettingString(items, "OUTPUT_DIR") ?? env.OUTPUT_DIR ?? "";
      const obsidianVault = findSettingString(items, "OBSIDIAN_VAULT") ?? env.OBSIDIAN_VAULT ?? "";
      return {
        outputDirectory,
        obsidianVault,
        effectiveOutputDirectory: obsidianVault || outputDirectory,
        concurrency: 1,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      };
    });
  }

  settingsSchema(): Promise<SettingsSchema> {
    return this.request("settings_schema", {}).then(normalizeSettingsSchema).catch(() => defaultSettingsSchema(this.runtimeEnvironment()));
  }

  settingsUpdate(values: Record<string, SettingsFieldValue>): Promise<SettingsUpdateResult> {
    return this.request("settings_update", { values }).then((result) => {
      const payload = asRecord(result);
      const updated = Array.isArray(payload.updated) ? payload.updated.map(asRecord) : [];
      return {
        ok: payload.ok !== false,
        updated: updated.map((item) => ({
          name: String(item.name ?? ""),
          value: String(item.value ?? "")
        })),
        settingsPath: typeof payload.settings_path === "string" ? payload.settings_path : undefined,
        error: typeof payload.error === "string" ? payload.error : undefined
      };
    }).catch((error: unknown) => ({
      ok: false,
      updated: [],
      error: error instanceof Error ? `保存设置失败：${error.message}` : "保存设置失败"
    }));
  }

  loginStatus(request: LoginStatusRequest = {}): Promise<LoginStatus[]> {
    const platforms: SupportedPlatform[] = request.platforms ?? loginPlatforms;
    return this.request("login_status", { platforms, refresh: Boolean(request.refresh) }).then((result) => {
      const payload = asRecord(result);
      const rows = Array.isArray(payload.platforms) ? payload.platforms.map(asRecord) : [];
      const now = new Date().toISOString();
      return rows.map((row) => {
        const platform = isSupportedPlatform(row.platform) ? row.platform : "unknown";
        const status = normalizeLoginStatus(row.status);
        const capability = asRecord(row.capability);
        const capabilityLabel = typeof capability.display_name === "string" ? capability.display_name : undefined;
        return {
          platform,
          label: typeof row.label === "string" ? row.label : capabilityLabel ?? platformLabel(platform),
          status,
          lastChecked: now,
          sessionPath: typeof row.session_path === "string" ? row.session_path : undefined,
          cookieCount: typeof row.cookie_count === "number" ? row.cookie_count : undefined,
          accountCount: typeof row.account_count === "number" ? row.account_count : undefined,
          validCount: typeof row.valid_count === "number" ? row.valid_count : undefined,
          expiredCount: typeof row.expired_count === "number" ? row.expired_count : undefined,
          unreadableCount: typeof row.unreadable_count === "number" ? row.unreadable_count : undefined,
          validationMode:
            row.validation_mode === "online" || row.validation_mode === "structural" || row.validation_mode === "presence"
              ? row.validation_mode
              : undefined,
          message: typeof row.message === "string" ? row.message : undefined,
          loginRequired: typeof row.login_required === "boolean" ? row.login_required : undefined
        };
      });
    });
  }

  importLoginSessions(sourceDirectory?: string, platform?: SupportedPlatform): Promise<LoginSessionImportResult> {
    const sourceDir = sourceDirectory || this.env?.FEEDGRAB_INSTALL_SESSIONS_DIR || process.env.FEEDGRAB_INSTALL_SESSIONS_DIR || "";
    return this.request("import_login_sessions", { source_dir: sourceDir, platform, sync: true }).then((result) => {
      const payload = asRecord(result);
      const imported = normalizeImportRows(payload.imported);
      const skipped = normalizeImportRows(payload.skipped);
      const disabled = normalizeImportRows(payload.disabled);
      const ignored = normalizeImportRows(payload.ignored);
      const sourceMissing = ignored.some((row) => row.reason === "source_dir_missing");
      return {
        ok: payload.ok !== false && !sourceMissing,
        sourceDirectory: typeof payload.source_dir === "string" ? payload.source_dir : sourceDir || undefined,
        targetDirectory: typeof payload.target_dir === "string" ? payload.target_dir : undefined,
        imported,
        skipped,
        disabled,
        ignored,
        error:
          typeof payload.error === "string"
            ? payload.error
            : sourceMissing
              ? `sessions 来源目录不存在：${sourceDir || "未配置"}`
              : undefined
      };
    }).catch((error: unknown) => ({
      ok: false,
      sourceDirectory: sourceDir || undefined,
      imported: [],
      skipped: [],
      disabled: [],
      ignored: [],
      error: error instanceof Error ? `导入登录态失败：${error.message}` : "导入登录态失败"
    }));
  }

  loginPlatform(platform: SupportedPlatform): Promise<LoginPlatformResult> {
    return new Promise((resolve) => {
      const args = this.loginArgs(platform);
      const child = spawn(this.command, args, {
        cwd: this.cwd,
        env: this.loginEnvironment(),
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true
      });
      const chunks: string[] = [];
      const errors: string[] = [];
      child.stdout.on("data", (chunk: Buffer) => chunks.push(chunk.toString("utf8")));
      child.stderr.on("data", (chunk: Buffer) => errors.push(chunk.toString("utf8")));
      child.on("error", (error) => {
        resolve({
          ok: false,
          platform,
          status: "missing",
          message: error.message,
          error: error.message
        });
      });
      child.on("exit", (code) => {
        const message = [...chunks, ...errors].join("").trim();
        resolve({
          ok: code === 0,
          platform,
          status: code === 0 ? "connected" : "expired",
          message: message || (code === 0 ? `${platformLabel(platform)} 登录完成` : `${platformLabel(platform)} 登录失败`),
          error: code === 0 ? undefined : message || `登录进程退出，退出码 ${code ?? "未知"}`
        });
      });
    });
  }

  outputList(): Promise<OutputArtifact[]> {
    return this.request("output_list", {}).then((result) => {
      const payload = asRecord(result);
      const items = Array.isArray(payload.items) ? payload.items.map(asRecord) : [];
      return items.map((item, index) => {
        const path = String(item.path ?? "");
        return {
          id: `artifact-${index + 1}`,
          title: path.split(/[\\/]/).at(-1) || "artifact",
          platform: path.split(/[\\/]/).at(-2) || "Output",
          markdownPath: path,
          attachments: [],
          createdAt: new Date().toISOString()
        };
      });
    });
  }

  private installPythonDependencies(): Promise<DoctorRepairResult> {
    if (!this.canRunPythonModuleCommands()) {
      return Promise.resolve({
        ok: false,
        action: "python-dependencies",
        message: "当前为打包运行时，Python 依赖需要通过新版安装包更新",
        error: "bundled_worker_runtime"
      });
    }
    return this.runRepairCommand("python-dependencies", [
      "-m",
      "pip",
      "install",
      "-U",
      "playwright",
      "patchright",
      "browserforge",
      "curl_cffi"
    ]);
  }

  private installPlaywrightChromium(): Promise<DoctorRepairResult> {
    if (!this.canRunPythonModuleCommands()) {
      return Promise.resolve({
        ok: false,
        action: "playwright-browsers",
        message: "当前为打包运行时，请使用包含 Chromium 的新版安装包更新",
        error: "bundled_worker_runtime"
      });
    }
    return this.runRepairCommand("playwright-browsers", ["-m", "playwright", "install", "chromium"]);
  }

  private canRunPythonModuleCommands(): boolean {
    return this.args.length >= 2 && this.args[0] === "-m" && this.args[1] === "feedgrab.worker";
  }

  private runRepairCommand(action: string, args: string[]): Promise<DoctorRepairResult> {
    return new Promise((resolve) => {
      const child = spawn(this.command, args, {
        cwd: this.cwd,
        env: {
          ...process.env,
          ...this.env,
          PYTHONIOENCODING: "utf-8"
        },
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true
      });
      const chunks: string[] = [];
      const timeout = setTimeout(() => {
        child.kill();
        resolve({
          ok: false,
          action,
          message: "安装/更新超时，已停止本次操作",
          log: chunks,
          error: "timeout"
        });
      }, 15 * 60 * 1000);
      child.stdout.on("data", (chunk: Buffer) => chunks.push(chunk.toString("utf8").trim()));
      child.stderr.on("data", (chunk: Buffer) => chunks.push(chunk.toString("utf8").trim()));
      child.on("error", (error) => {
        clearTimeout(timeout);
        resolve({ ok: false, action, message: error.message, log: chunks, error: error.message });
      });
      child.on("exit", (code) => {
        clearTimeout(timeout);
        const ok = code === 0;
        resolve({
          ok,
          action,
          message: ok ? "依赖已安装/更新，正在重新检测" : `安装/更新失败，退出码 ${code ?? "unknown"}`,
          log: chunks.filter(Boolean).slice(-20),
          error: ok ? undefined : chunks.filter(Boolean).slice(-1)[0]
        });
      });
    });
  }

  private request(method: string, params: Record<string, unknown>): Promise<unknown> {
    return this.requestWithId(this.nextId(method), method, params);
  }

  private requestWithId(id: string, method: string, params: Record<string, unknown>): Promise<unknown> {
    this.ensureProcess();
    const child = this.child;
    if (!child) {
      return Promise.reject(new Error("Python worker 进程不可用"));
    }

    return new Promise((resolve, reject) => {
      this.pending.set(id, {
        resolve: (event) => resolve(event.result),
        reject
      });
      child.stdin.write(`${JSON.stringify({ id, method, params })}\n`);
    });
  }

  private ensureProcess(): void {
    if (this.child) {
      return;
    }

    const child = spawn(this.command, this.args, {
      cwd: this.cwd,
      env: {
        ...process.env,
        ...this.env,
        PYTHONIOENCODING: "utf-8"
      },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true
    });
    this.child = child;

    child.stdout.on("data", (chunk: Buffer) => this.handleStdout(chunk.toString("utf8")));
    child.stderr.on("data", (chunk: Buffer) => {
      const message = chunk.toString("utf8").trim();
      if (message) {
        console.warn(`[feedgrab worker] ${message}`);
      }
    });
    child.on("error", (error) => {
      for (const pending of this.pending.values()) {
        pending.reject(error);
      }
      this.pending.clear();
      this.child = undefined;
      this.failActiveFetchJobs(error.message);
      this.emitEvent({
        event: "error",
        error: {
          code: "worker_spawn_error",
          message: error.message,
          recoverable: true
        }
      });
    });
    child.on("exit", (code) => {
      const error = new Error(`feedgrab worker 已退出，退出码 ${code ?? "未知"}`);
      for (const pending of this.pending.values()) {
        pending.reject(error);
      }
      this.pending.clear();
      this.child = undefined;
      this.failActiveFetchJobs(error.message);
    });
  }

  private handleStdout(chunk: string): void {
    this.buffer += chunk;
    const lines = this.buffer.split(/\r?\n/);
    this.buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) {
        continue;
      }
      this.handleEvent(line);
    }
  }

  private handleEvent(line: string): void {
    let event: FeedgrabWorkerEvent;
    try {
      event = JSON.parse(line) as FeedgrabWorkerEvent;
    } catch {
      return;
    }

    this.emitEvent(event);

    if (event.event === "artifact" && event.id && event.artifact?.path) {
      this.artifacts.set(event.id, event.artifact.path);
      return;
    }
    if (event.method === "fetch" && event.id && ["error", "done", "cancelled"].includes(event.event)) {
      this.activeFetchJobs.delete(event.id);
    }
    if (event.event === "ready" || event.event === "progress" || event.event === "log" || event.event === "diagnostic") {
      return;
    }

    const id = event.id ?? "";
    const pending = this.pending.get(id);
    if (!pending) {
      return;
    }

    if (event.event === "error") {
      this.pending.delete(id);
      pending.reject(new Error(event.error?.message ?? "worker 执行失败"));
      return;
    }
    if (event.event === "done" || event.event === "cancelled") {
      this.pending.delete(id);
      pending.resolve(event);
    }
  }

  private failActiveFetchJobs(message: string): void {
    const jobs = [...this.activeFetchJobs.values()];
    this.activeFetchJobs.clear();
    for (const job of jobs) {
      this.emitEvent({
        id: job.id,
        event: "error",
        method: "fetch",
        url: job.url,
        error: {
          code: "worker_exited",
          message,
          recoverable: true
        }
      });
      this.emitEvent({
        id: job.id,
        event: "done",
        method: "fetch",
        result: {
          fetched: 0,
          errors: 1,
          error: message
        }
      });
    }
  }

  private nextId(prefix: string): string {
    const id = `${prefix}_${this.seq}`;
    this.seq += 1;
    return id;
  }

  private loginArgs(platform: SupportedPlatform): string[] {
    if (this.args.length >= 2 && this.args[0] === "-m" && this.args[1] === "feedgrab.worker") {
      return ["-m", "feedgrab.cli", "login", platform];
    }
    return ["login", platform];
  }

  private loginEnvironment(): NodeJS.ProcessEnv {
    const baseEnv = this.runtimeEnvironment();
    return {
      ...baseEnv,
      ...readSavedSettingsEnvironment(baseEnv.FEEDGRAB_SETTINGS_PATH, baseEnv),
      PYTHONIOENCODING: "utf-8"
    };
  }

  private runtimeEnvironment(): NodeJS.ProcessEnv {
    return {
      ...process.env,
      ...this.env
    };
  }

  private waitForJobAccepted(jobId: string, resolve: () => void): () => void {
    let settled = false;
    const listener = (event: FeedgrabWorkerEvent): void => {
      if (event.id !== jobId) {
        return;
      }
      if (event.event === "job_started" || event.event === "progress" || event.event === "log" || event.event === "done") {
        cleanup();
        resolve();
      }
    };
    const timer = setTimeout(() => {
      cleanup();
      resolve();
    }, 500);
    const cleanup = (): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      this.listeners.delete(listener);
    };
    this.listeners.add(listener);
    return cleanup;
  }

  private emitEvent(event: FeedgrabWorkerEvent): void {
    for (const listener of this.listeners) {
      listener(event);
    }
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function normalizeDoctorCheck(value: unknown): DoctorCheck {
  const payload = asRecord(value);
  const status = normalizeDoctorStatus(payload.status);
  const name = String(payload.name ?? "check");
  const repair = normalizeDoctorRepair(payload.repair);
  return {
    name,
    label: String(payload.label ?? diagnosticLabel(name)),
    status,
    message: typeof payload.message === "string" ? payload.message : "",
    details: asRecord(payload.details),
    repair
  };
}

function normalizeDoctorRepair(value: unknown): DoctorCheck["repair"] | undefined {
  const payload = asRecord(value);
  const id = typeof payload.id === "string" ? payload.id : "";
  if (!id) {
    return undefined;
  }
  return {
    id,
    label: typeof payload.label === "string" ? payload.label : "安装/更新",
    available: payload.available !== false
  };
}

function withRepairMetadata(check: DoctorCheck): DoctorCheck {
  if (check.status === "ok" || check.repair) {
    return check;
  }
  const action = repairActionForCheckName(check.name);
  if (!action) {
    return check;
  }
  return {
    ...check,
    repair: {
      id: action,
      label: "安装/更新",
      available: true
    }
  };
}

function normalizeDoctorStatus(value: unknown): DoctorCheck["status"] {
  if (value === "ok" || value === "warning" || value === "error" || value === "unknown") {
    return value;
  }
  return "unknown";
}

function localRuntimeDoctorChecks(env?: NodeJS.ProcessEnv): DoctorCheck[] {
  const chromiumVersion = process.versions.chrome ?? "";
  const browserPath = env?.PLAYWRIGHT_BROWSERS_PATH || process.env.PLAYWRIGHT_BROWSERS_PATH || "";
  const hasPlaywrightChromium = playwrightChromiumInstalled(browserPath);
  return [
    { name: "node", label: "Node.js", status: "ok", message: process.version },
    {
      name: "electron",
      label: "Electron",
      status: process.versions.electron ? "ok" : "unknown",
      message: process.versions.electron ?? "unknown"
    },
    {
      name: "chromium",
      label: "Chromium",
      status: chromiumVersion ? "ok" : "warning",
      message: chromiumVersion || "未识别"
    },
    {
      name: "playwright_browsers_path",
      label: "Playwright 浏览器目录",
      status: browserPath ? "ok" : "warning",
      message: browserPath || "未配置"
    },
    {
      name: "playwright_chromium",
      label: "Playwright Chromium",
      status: hasPlaywrightChromium ? "ok" : "warning",
      message: hasPlaywrightChromium ? "已安装" : "未安装"
    }
  ];
}

function playwrightChromiumInstalled(browserPath: string): boolean {
  if (!browserPath || !existsSync(browserPath)) {
    return false;
  }
  try {
    return readdirSync(browserPath, { withFileTypes: true }).some(
      (entry) => entry.isDirectory() && entry.name.toLowerCase().startsWith("chromium")
    );
  } catch {
    return false;
  }
}

function repairActionForCheckName(checkName: string): "all" | "python-dependencies" | "playwright-browsers" | undefined {
  if (checkName === "all") {
    return "all";
  }
  if (
    checkName === "import:playwright" ||
    checkName === "import:patchright" ||
    checkName === "import:browserforge" ||
    checkName === "import:curl_cffi"
  ) {
    return "python-dependencies";
  }
  if (checkName === "playwright_browsers_path" || checkName === "playwright_chromium" || checkName === "chromium") {
    return "playwright-browsers";
  }
  return undefined;
}

function diagnosticLabel(name: string): string {
  const labels: Record<string, string> = {
    python: "Python",
    output_dir: "输出目录",
    data_dir: "登录态和数据目录",
    proxy_connectivity: "代理连通性",
    "import:feedgrab": "feedgrab 包",
    "import:playwright": "Playwright",
    "import:patchright": "Patchright",
    "import:browserforge": "browserforge",
    "import:curl_cffi": "curl_cffi"
  };
  return labels[name] ?? name;
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

function findSettingString(items: Array<Record<string, unknown>>, name: string): string | undefined {
  const item = items.find((entry) => entry.name === name);
  if (!item || item.value === undefined || item.value === null) {
    return undefined;
  }
  return String(item.value);
}

function findDiagnosticMessage(items: unknown[], name: string): string | undefined {
  const item = items.map(asRecord).find((entry) => entry.name === name);
  return typeof item?.message === "string" ? item.message : undefined;
}

function findDiagnosticStatus(items: unknown[], name: string): string | undefined {
  const item = items.map(asRecord).find((entry) => entry.name === name);
  return typeof item?.status === "string" ? item.status : undefined;
}

function normalizeSettingsSchema(value: unknown): SettingsSchema {
  const payload = asRecord(value);
  const rawPlatforms = (Array.isArray(payload.platforms) ? payload.platforms : [])
    .map(normalizeSettingsPlatform)
    .filter((platform) => platform.id);
  const corePlatform = rawPlatforms.find((platform) => platform.id === "core");
  const platformGroups = rawPlatforms.filter((platform) => platform.id !== "core");
  const basicSource = Array.isArray(payload.basic)
    ? payload.basic
    : Array.isArray(payload.items)
      ? payload.items
      : corePlatform?.fields ?? [];
  const basic = basicSource.map(normalizeSettingField).filter((field) => field.name);
  return {
    basic: basic.length > 0 ? basic : defaultSettingsSchema().basic,
    platforms: platformGroups.length > 0 ? platformGroups : defaultSettingsSchema().platforms
  };
}

function normalizeSettingsPlatform(value: unknown): SettingsPlatformSchema {
  const payload = asRecord(value);
  const id = String(payload.id ?? payload.name ?? "");
  return {
    id,
    label: String(payload.label ?? payload.name ?? payload.title ?? id),
    fields: (Array.isArray(payload.fields) ? payload.fields : []).map(normalizeSettingField).filter((field) => field.name)
  };
}

function normalizeSettingField(rawField: unknown): SettingsFieldSchema {
  const payload = asRecord(rawField);
  const name = String(payload.name ?? "");
  const type = normalizeSettingFieldType(payload.type ?? payload.value_type);
  const field: SettingsFieldSchema = {
    name,
    label: String(payload.label ?? payload.description ?? name),
    type,
    secret: Boolean(payload.secret) || type === "secret",
    description: typeof payload.description === "string" ? payload.description : undefined,
    placeholder: typeof payload.placeholder === "string" ? payload.placeholder : undefined
  };

  const fieldValue = normalizeSettingValue(payload.value, type);
  if (fieldValue !== undefined) {
    field.value = fieldValue;
  }
  const defaultValue = normalizeSettingValue(payload.defaultValue ?? payload.default, type);
  if (defaultValue !== undefined) {
    field.defaultValue = defaultValue;
  }
  if (Array.isArray(payload.options)) {
    field.options = payload.options.map((option) => {
      const primitiveValue = normalizeSettingValue(option);
      if (primitiveValue !== undefined) {
        return {
          label: String(primitiveValue),
          value: primitiveValue
        };
      }
      const row = asRecord(option);
      const optionValue = normalizeSettingValue(row.value);
      return {
        label: String(row.label ?? optionValue ?? ""),
        value: optionValue ?? ""
      };
    });
  }
  return field;
}

function normalizeSettingFieldType(value: unknown): SettingsFieldSchema["type"] {
  if (value === "boolean" || value === "number" || value === "string" || value === "select" || value === "path" || value === "secret") {
    return value;
  }
  if (value === "bool") {
    return "boolean";
  }
  if (value === "integer") {
    return "number";
  }
  if (value === "enum") {
    return "select";
  }
  return "string";
}

function normalizeSettingValue(value: unknown, type?: SettingsFieldSchema["type"]): SettingsFieldValue | undefined {
  if (value === undefined || value === null) {
    return undefined;
  }
  if (type === "boolean") {
    if (typeof value === "boolean") {
      return value;
    }
    if (typeof value === "number") {
      return value !== 0;
    }
    if (typeof value === "string") {
      const normalized = value.trim().toLowerCase();
      if (["true", "1", "yes", "on"].includes(normalized)) {
        return true;
      }
      if (["false", "0", "no", "off", ""].includes(normalized)) {
        return false;
      }
    }
    return Boolean(value);
  }
  if (type === "number") {
    if (typeof value === "number") {
      return value;
    }
    if (typeof value === "string") {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : undefined;
    }
    return undefined;
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  return undefined;
}

function defaultSettingsSchema(env: NodeJS.ProcessEnv = process.env): SettingsSchema {
  return {
    basic: [
      { name: "OUTPUT_DIR", label: "输出目录", type: "path", value: env.OUTPUT_DIR || "" },
      { name: "OBSIDIAN_VAULT", label: "Obsidian Vault", type: "path", value: env.OBSIDIAN_VAULT || "", description: "高优先级" },
      {
        name: "FEEDGRAB_DATA_DIR",
        label: "登录态和数据目录",
        type: "path",
        value: env.FEEDGRAB_DATA_DIR || env.FEEDGRAB_INSTALL_SESSIONS_DIR || ""
      },
      { name: "BROWSER_USER_AGENT", label: "浏览器 User-Agent", type: "string", value: env.BROWSER_USER_AGENT || defaultRuntimeUserAgent() }
    ],
    platforms: [
      {
        id: "x",
        label: "X / Twitter",
        fields: [
          { name: "X_SEARCH_DAYS", label: "搜索天数", type: "number", value: 7 },
          { name: "TWITTERAPI_IO_KEY", label: "TwitterAPI.io Key", type: "secret", value: "[redacted]", secret: true }
        ]
      },
      {
        id: "feishu",
        label: "文档平台",
        fields: [{ name: "FEISHU_APP_SECRET", label: "飞书 App Secret", type: "secret", value: "[redacted]", secret: true }]
      }
    ]
  };
}

function defaultRuntimeUserAgent(): string {
  const chromiumVersion = process.versions.chrome ?? "";
  if (!chromiumVersion) {
    return "";
  }
  const osToken =
    process.platform === "win32"
      ? "Windows NT 10.0; Win64; x64"
      : process.platform === "darwin"
        ? "Macintosh; Intel Mac OS X 10_15_7"
        : "X11; Linux x86_64";
  return `Mozilla/5.0 (${osToken}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${chromiumVersion} Safari/537.36`;
}

function normalizeLoginStatus(value: unknown): LoginStatus["status"] {
  if (value === "ok" || value === "connected") {
    return "connected";
  }
  if (value === "missing") {
    return "missing";
  }
  if (value === "not_required" || value === "notRequired") {
    return "notRequired";
  }
  return "expired";
}

function normalizeImportRows(value: unknown): LoginSessionImportResult["imported"] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((row) => {
    const payload = asRecord(row);
    return {
      source: String(payload.source ?? ""),
      target: typeof payload.target === "string" ? payload.target : undefined,
      reason: typeof payload.reason === "string" ? payload.reason : undefined
    };
  });
}

function readSavedSettingsEnvironment(
  settingsPath: string | undefined,
  baseEnv: NodeJS.ProcessEnv = process.env
): NodeJS.ProcessEnv {
  if (!settingsPath) {
    return {};
  }
  try {
    const payload = JSON.parse(readFileSync(settingsPath, "utf8")) as Record<string, unknown>;
    const values = payload.values && typeof payload.values === "object" ? payload.values : payload;
    if (!values || typeof values !== "object" || Array.isArray(values)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(values as Record<string, unknown>)
        .map(([name, value]) => [name, settingsValueToEnvString(name, value, baseEnv)] as const)
        .filter((entry): entry is readonly [string, string] => typeof entry[1] === "string")
    ) as NodeJS.ProcessEnv;
  } catch {
    return {};
  }
}

function settingsValueToEnvString(name: string, value: unknown, baseEnv: NodeJS.ProcessEnv): string | undefined {
  if (value === undefined || value === null) {
    return undefined;
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : undefined;
  }
  if (typeof value === "string") {
    if (isLegacyDesktopDefaultPath(value)) {
      if (name === "OUTPUT_DIR") {
        return baseEnv.OUTPUT_DIR || undefined;
      }
      if (name === "OBSIDIAN_VAULT") {
        return "";
      }
    }
    if (name === "OUTPUT_DIR" && isDesktopDefaultOutputDirValue(value)) {
      return baseEnv.OUTPUT_DIR || undefined;
    }
    return normalizeSavedSettingsPath(name, value, baseEnv);
  }
  return undefined;
}

function isLegacyDesktopDefaultPath(value: string): boolean {
  return legacyDesktopDefaultPaths.has(normalizePathForMatch(value));
}

function normalizePathForMatch(value: string): string {
  return value.trim().replace(/\//g, "\\").replace(/\\+$/g, "").toLowerCase();
}

function isDesktopDefaultOutputDirValue(value: string): boolean {
  return ["", "output", ".", ".\\output", "\\output", "./output"].includes(normalizePathForMatch(value));
}

function normalizeSavedSettingsPath(name: string, value: string, baseEnv: NodeJS.ProcessEnv): string {
  if (name !== "FEEDGRAB_DATA_DIR") {
    return value;
  }
  const normalized = normalizePathForMatch(value);
  if (["", "sessions", ".", ".\\sessions", "\\sessions"].includes(normalized)) {
    return baseEnv.FEEDGRAB_INSTALL_SESSIONS_DIR || baseEnv.FEEDGRAB_DATA_DIR || value;
  }
  return value;
}

function platformLabel(platform: SupportedPlatform): string {
  const labels: Record<SupportedPlatform, string> = {
    twitter: "X / Twitter",
    xhs: "小红书",
    youtube: "YouTube",
    bilibili: "Bilibili",
    wechat: "微信公众号",
    github: "GitHub",
    linuxdo: "LinuxDo",
    idcflare: "IDCFlare",
    feishu: "飞书",
    kdocs: "金山文档",
    flowus: "FlowUs",
    zhihu: "知乎",
    zsxq: "知识星球",
    web: "网页",
    unknown: "未知"
  };
  return labels[platform];
}
