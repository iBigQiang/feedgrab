import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";

import type {
  DoctorSnapshot,
  FeedgrabWorkerEvent,
  FetchJobSnapshot,
  FetchRequest,
  LoginStatus,
  OutputArtifact,
  SettingsSnapshot,
  SupportedPlatform,
  WorkerPing
} from "./ipc-types.js";

export type PythonWorkerClient = {
  ping: () => Promise<WorkerPing>;
  onEvent: (callback: (event: FeedgrabWorkerEvent) => void) => () => void;
  detectPlatform: (url: string) => Promise<SupportedPlatform>;
  startFetch: (request: FetchRequest) => Promise<FetchJobSnapshot>;
  cancelJob: (jobId: string) => Promise<FetchJobSnapshot>;
  doctor: () => Promise<DoctorSnapshot>;
  settingsSnapshot: () => Promise<SettingsSnapshot>;
  loginStatus: () => Promise<LoginStatus[]>;
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
};

const platformMatchers: Array<[SupportedPlatform, RegExp]> = [
  ["twitter", /(?:^|\.)x\.com|(?:^|\.)twitter\.com/i],
  ["xhs", /xiaohongshu\.com|xhslink\.com/i],
  ["youtube", /youtube\.com|youtu\.be/i],
  ["bilibili", /bilibili\.com|b23\.tv/i],
  ["wechat", /mp\.weixin\.qq\.com/i],
  ["github", /github\.com/i],
  ["linuxdo", /linux\.do|idcflare\.com/i],
  ["feishu", /feishu\.cn|larksuite\.com|larkoffice\.com/i]
];

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
    feishu: "Feishu",
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
  const outputs: OutputArtifact[] = [
    {
      id: "mock-output-linuxdo",
      title: "Discourse topic sample",
      platform: "LinuxDo",
      markdownPath: "D:\\Notes\\Feeds\\LinuxDo\\topic-sample.md",
      attachments: ["D:\\Notes\\Feeds\\LinuxDo\\attachments\\cover.png"],
      createdAt: new Date("2026-06-25T09:10:00.000Z").toISOString()
    },
    {
      id: "mock-output-github",
      title: "feedgrab README snapshot",
      platform: "GitHub",
      markdownPath: "D:\\Notes\\Feeds\\GitHub\\feedgrab.md",
      attachments: [],
      createdAt: new Date("2026-06-25T09:18:00.000Z").toISOString()
    }
  ];
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
      const url = request.urls[0] ?? "";
      const platform = detectPlatformFromUrl(url);
      const folder = platformFolder(platform);
      const id = `mock-job-${jobs.size + 1}`;
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

      jobs.set(id, job);
      outputs.unshift({
        id: `${id}-artifact`,
        title: titleFromUrl(url),
        platform: platformFolder(platform),
        markdownPath,
        attachments: job.attachments ?? [],
        createdAt: job.createdAt
      });
      setTimeout(() => {
        emit({ id, event: "job_started", method: "fetch", result: { total: request.urls.length } });
        emit({ id, event: "log", method: "fetch", level: "info", message: "mock fetch job started" });
        emit({ id, event: "progress", method: "fetch", stage: "fetch", message: "fetching", url });
        emit({ id, event: "artifact", method: "fetch", url, artifact: { kind: "markdown", path: markdownPath } });
        emit({ id, event: "done", method: "fetch", result: { fetched: request.urls.length, errors: 0 } });
      }, 0);

      return Promise.resolve(job);
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
      return Promise.resolve({
        python: "mock 3.12-compatible",
        browser: "mock",
        network: "disabled",
        writableOutput: true,
        notes: [
          "当前为静态 GUI mock，不会请求真实平台。",
          "接入 service worker 后再读取真实 Python、Playwright、登录状态。"
        ]
      });
    },
    settingsSnapshot() {
      return Promise.resolve({
        outputDirectory: "D:\\Notes\\Feeds",
        concurrency: 2,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      });
    },
    loginStatus() {
      const now = new Date().toISOString();
      return Promise.resolve([
        { platform: "twitter", label: "X / Twitter", status: "missing", lastChecked: now },
        { platform: "xhs", label: "小红书", status: "expired", lastChecked: now },
        { platform: "wechat", label: "微信公众号", status: "connected", lastChecked: now },
        { platform: "github", label: "GitHub", status: "notRequired", lastChecked: now },
        { platform: "linuxdo", label: "LinuxDo / Discourse", status: "connected", lastChecked: now }
      ]);
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
  private readonly listeners = new Set<(event: FeedgrabWorkerEvent) => void>();
  private readonly command: string;
  private readonly args: string[];
  private readonly cwd: string | undefined;

  constructor(options: PythonWorkerClientOptions) {
    this.command = options.command ?? "python";
    this.args = options.args ?? ["-m", "feedgrab.worker"];
    this.cwd = options.cwd;
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

  startFetch(request: FetchRequest): Promise<FetchJobSnapshot> {
    const id = this.nextId("fetch");
    this.ensureProcess();
    const child = this.child;
    if (!child) {
      return Promise.reject(new Error("worker process is not available"));
    }

    const url = request.urls[0] ?? "";
    const job: FetchJobSnapshot = {
        id,
        url,
        platform: detectPlatformFromUrl(url),
        status: "running",
        outputDirectory: request.outputDirectory,
        attachments: [],
        createdAt: new Date().toISOString()
    };

    const payload = {
      id,
      method: "fetch",
      params: { urls: request.urls, output_dir: request.outputDirectory }
    };

    return new Promise((resolve, reject) => {
      child.stdin.write(`${JSON.stringify(payload)}\n`, (error?: Error | null) => {
        if (error) {
          reject(error);
          return;
        }
        resolve(job);
      });
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
    return this.request("doctor", {}).then((result) => {
      const payload = asRecord(result);
      const checks = Array.isArray(payload.checks) ? payload.checks : [];
      return {
        python: findDiagnosticMessage(checks, "python") ?? "unknown",
        browser: "missing",
        network: "unknown",
        writableOutput: findDiagnosticStatus(checks, "output_dir") !== "error",
        notes: checks.map((check) => JSON.stringify(check))
      };
    });
  }

  settingsSnapshot(): Promise<SettingsSnapshot> {
    return this.request("settings_snapshot", {}).then((result) => {
      const payload = asRecord(result);
      const items = Array.isArray(payload.items) ? payload.items.map(asRecord) : [];
      return {
        outputDirectory: String(findSettingValue(items, "OUTPUT_DIR") || "D:\\Notes\\Feeds"),
        concurrency: 1,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      };
    });
  }

  loginStatus(): Promise<LoginStatus[]> {
    const platforms: SupportedPlatform[] = ["twitter", "xhs", "wechat", "github", "linuxdo"];
    return this.request("login_status", { platforms }).then((result) => {
      const payload = asRecord(result);
      const rows = Array.isArray(payload.platforms) ? payload.platforms.map(asRecord) : [];
      const now = new Date().toISOString();
      return rows.map((row) => {
        const platform = isSupportedPlatform(row.platform) ? row.platform : "unknown";
        const status = row.status === "ok" ? "connected" : row.status === "missing" ? "missing" : "expired";
        return {
          platform,
          label: platformLabel(platform),
          status,
          lastChecked: now
        };
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

  private request(method: string, params: Record<string, unknown>): Promise<unknown> {
    return this.requestWithId(this.nextId(method), method, params);
  }

  private requestWithId(id: string, method: string, params: Record<string, unknown>): Promise<unknown> {
    this.ensureProcess();
    const child = this.child;
    if (!child) {
      return Promise.reject(new Error("worker process is not available"));
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
      const error = new Error(`feedgrab worker exited with code ${code ?? "unknown"}`);
      for (const pending of this.pending.values()) {
        pending.reject(error);
      }
      this.pending.clear();
      this.child = undefined;
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
      pending.reject(new Error(event.error?.message ?? "worker error"));
      return;
    }
    if (event.event === "done" || event.event === "cancelled") {
      this.pending.delete(id);
      pending.resolve(event);
    }
  }

  private nextId(prefix: string): string {
    const id = `${prefix}_${this.seq}`;
    this.seq += 1;
    return id;
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

function isSupportedPlatform(value: unknown): value is SupportedPlatform {
  return (
    typeof value === "string" &&
    ["twitter", "xhs", "youtube", "bilibili", "wechat", "github", "linuxdo", "feishu", "web", "unknown"].includes(value)
  );
}

function findSettingValue(items: Array<Record<string, unknown>>, name: string): unknown {
  return items.find((item) => item.name === name)?.value;
}

function findDiagnosticMessage(items: unknown[], name: string): string | undefined {
  const item = items.map(asRecord).find((entry) => entry.name === name);
  return typeof item?.message === "string" ? item.message : undefined;
}

function findDiagnosticStatus(items: unknown[], name: string): string | undefined {
  const item = items.map(asRecord).find((entry) => entry.name === name);
  return typeof item?.status === "string" ? item.status : undefined;
}

function platformLabel(platform: SupportedPlatform): string {
  const labels: Record<SupportedPlatform, string> = {
    twitter: "X / Twitter",
    xhs: "小红书",
    youtube: "YouTube",
    bilibili: "Bilibili",
    wechat: "微信公众号",
    github: "GitHub",
    linuxdo: "LinuxDo / Discourse",
    feishu: "飞书",
    web: "网页",
    unknown: "未知"
  };
  return labels[platform];
}
