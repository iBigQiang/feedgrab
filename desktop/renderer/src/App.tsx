import { useEffect, useMemo, useReducer, useState, type Dispatch, type ReactElement } from "react";

import type {
  DoctorSnapshot,
  FeedgrabIpcApi,
  FeedgrabWorkerEvent,
  FetchJobSnapshot,
  LoginStatus,
  OutputArtifact,
  SettingsSnapshot,
  SupportedPlatform
} from "../../electron/ipc-types";
import {
  appReducer,
  createInitialAppState,
  type LogLevel,
  type UiJob,
  type ViewKey
} from "./state/appReducer";

const navigation: Array<{ key: ViewKey; label: string; symbol: string }> = [
  { key: "fetch", label: "抓取", symbol: "+" },
  { key: "jobs", label: "任务", symbol: ">" },
  { key: "output", label: "输出", symbol: "#" },
  { key: "login", label: "登录", symbol: "@" },
  { key: "settings", label: "设置", symbol: "*" },
  { key: "doctor", label: "诊断", symbol: "!" },
  { key: "auth", label: "授权", symbol: "$" }
];

const sampleUrls = [
  "https://github.com/iBigQiang/feedgrab",
  "https://linux.do/t/topic/2470643",
  "https://mp.weixin.qq.com/s/g7ASDLvrVN9eNgYDvrNKeA"
].join("\n");

export function App(): ReactElement {
  const [state, dispatch] = useReducer(appReducer, undefined, createInitialAppState);
  const [urlText, setUrlText] = useState(sampleUrls);
  const [loginStatus, setLoginStatus] = useState<LoginStatus[]>([]);
  const api = useMemo(() => resolveFeedgrabApi(), []);

  useEffect(() => {
    const view = viewFromSearch();
    if (view) {
      dispatch({ type: "view/select", payload: view });
    }
  }, []);

  useEffect(() => {
    void api
      .ping()
      .then((ping) =>
        dispatch({
          type: "job/log",
          payload: {
            level: "success",
            message:
              ping.worker === "python"
                ? "Python sidecar worker 已连接。"
                : "浏览器测试 mock worker 已连接。"
          }
        })
      )
      .catch((error: unknown) =>
        dispatch({
          type: "job/log",
          payload: {
            level: "error",
            message: error instanceof Error ? error.message : "worker 连接失败"
          }
        })
      );
    void api.settingsSnapshot().then((settings) =>
      dispatch({
        type: "settings/load",
        payload: {
          ...settings,
          outputDirectory: loadSavedOutputDirectory() || settings.outputDirectory
        }
      })
    );
    void api.doctor().then((doctor) => dispatch({ type: "doctor/load", payload: doctor }));
    void api.loginStatus().then(setLoginStatus);
    void api.outputList().then((outputs) => dispatch({ type: "output/load", payload: outputs }));

    return api.onWorkerEvent((event) => handleWorkerEvent(event, dispatch));
  }, [api]);

  const urls = parseUrls(urlText);
  const runningJobs = state.jobs.filter((job) => job.status === "running").length;
  const failedJobs = state.jobs.filter((job) => job.status === "failed").length;
  const completedJobs = state.jobs.filter((job) => job.status === "completed").length;

  function startFetch(): void {
    if (urls.length === 0) {
      dispatch({
        type: "job/log",
        payload: { level: "warning", message: "请输入至少一个有效链接" }
      });
      return;
    }

    void api
      .startFetch({ urls, outputDirectory: state.outputDirectory })
      .then((job) => {
        dispatch({ type: "job/upsert", payload: job });
        dispatch({
          type: "job/log",
          payload: {
            jobId: job.id,
            level: "info",
            message: `worker 已接收 ${urls.length} 条链接，输出到 ${state.outputDirectory}`
          }
        });
      })
      .catch((error: unknown) => {
        dispatch({
          type: "job/log",
          payload: {
            level: "error",
            message: error instanceof Error ? error.message : "worker 调用失败"
          }
        });
      });
  }

  function cancelJob(job: UiJob): void {
    dispatch({ type: "job/cancel", payload: { jobId: job.id } });
    void api.cancelJob(job.id);
  }

  function chooseOutputDirectory(): void {
    void api.chooseOutputDirectory().then((result) => {
      if (result.ok && result.path) {
        saveOutputDirectory(result.path);
        dispatch({ type: "settings/outputDirectory", payload: result.path });
      }
    });
  }

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="主导航">
        <div className="brand">
          <span className="brand-mark">fg</span>
          <div>
            <strong>feedgrab</strong>
            <small>Desktop</small>
          </div>
        </div>
        <nav className="nav-list">
          {navigation.map((item) => (
            <button
              key={item.key}
              type="button"
              className={state.selectedView === item.key ? "nav-item is-active" : "nav-item"}
              onClick={() => dispatch({ type: "view/select", payload: item.key })}
              title={item.label}
            >
              <span aria-hidden="true">{item.symbol}</span>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="runtime-panel">
          <span>本地优先</span>
          <strong>Python Sidecar</strong>
          <small>结构化协议，默认不上传诊断和凭据。</small>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">商业化 GUI 客户端分支</p>
            <h1>{headingFor(state.selectedView)}</h1>
          </div>
          <div className="status-strip" aria-label="任务状态">
            <Metric label="运行" value={runningJobs} />
            <Metric label="完成" value={completedJobs} />
            <Metric label="失败" value={failedJobs} />
          </div>
        </header>

        {state.lastNotice ? <div className="notice">{state.lastNotice}</div> : null}

        {state.selectedView === "fetch" ? (
          <FetchView
            urlText={urlText}
            setUrlText={setUrlText}
            outputDirectory={state.outputDirectory}
            urls={urls}
            logs={state.logs}
            startFetch={startFetch}
            chooseOutputDirectory={chooseOutputDirectory}
          />
        ) : null}
        {state.selectedView === "jobs" ? <JobsView jobs={state.jobs} cancelJob={cancelJob} /> : null}
        {state.selectedView === "output" ? <OutputView outputs={state.outputs} api={api} /> : null}
        {state.selectedView === "login" ? <LoginView statuses={loginStatus} /> : null}
        {state.selectedView === "settings" ? (
          <SettingsView settings={state.settings} outputDirectory={state.outputDirectory} chooseOutputDirectory={chooseOutputDirectory} />
        ) : null}
        {state.selectedView === "doctor" ? <DoctorView doctor={state.doctor} /> : null}
        {state.selectedView === "auth" ? <AuthView /> : null}
      </main>
    </div>
  );
}

function FetchView(props: {
  urlText: string;
  setUrlText: (value: string) => void;
  outputDirectory: string;
  urls: string[];
  logs: Array<{ id: string; level: LogLevel; message: string; createdAt: string }>;
  startFetch: () => void;
  chooseOutputDirectory: () => void;
}): ReactElement {
  return (
    <section className="split-layout">
      <div className="primary-pane">
        <label className="field-label" htmlFor="content-links">
          内容链接
        </label>
        <textarea
          id="content-links"
          value={props.urlText}
          onChange={(event) => props.setUrlText(event.target.value)}
          spellCheck={false}
        />
        <div className="form-row">
          <div>
            <span className="field-label">输出目录</span>
            <code>{props.outputDirectory}</code>
          </div>
          <button type="button" className="secondary-action" onClick={props.chooseOutputDirectory}>
            选择
          </button>
          <button type="button" className="primary-action" onClick={props.startFetch}>
            开始抓取
          </button>
        </div>
        <div className="platform-preview" aria-label="平台识别结果">
          {props.urls.map((url) => (
            <span key={url}>{platformLabel(detectPlatform(url))}</span>
          ))}
        </div>
      </div>
      <LogPanel logs={props.logs} />
    </section>
  );
}

function JobsView(props: { jobs: UiJob[]; cancelJob: (job: UiJob) => void }): ReactElement {
  return (
    <section className="table-surface">
      {props.jobs.length === 0 ? <EmptyState title="暂无任务" detail="从抓取页提交链接后，任务会显示在这里。" /> : null}
      {props.jobs.map((job) => (
        <article className="job-row" key={job.id}>
          <div>
            <strong>{job.url}</strong>
            <small>{job.outputDirectory}</small>
          </div>
          <span className={`status-pill ${job.status}`}>{statusLabel(job.status)}</span>
          <button type="button" onClick={() => props.cancelJob(job)} disabled={job.status !== "running"}>
            取消
          </button>
        </article>
      ))}
    </section>
  );
}

function OutputView(props: { outputs: OutputArtifact[]; api: FeedgrabIpcApi }): ReactElement {
  return (
    <section className="output-grid">
      {props.outputs.map((item) => (
        <article className="artifact-item" key={item.id}>
          <span>{item.platform}</span>
          <strong>{item.title}</strong>
          <code>{item.markdownPath}</code>
          <button type="button" onClick={() => void props.api.openPath(item.markdownPath)}>
            打开
          </button>
        </article>
      ))}
      {props.outputs.length === 0 ? <EmptyState title="暂无输出" detail="抓取成功后的 Markdown 和附件会进入输出库。" /> : null}
    </section>
  );
}

function LoginView(props: { statuses: LoginStatus[] }): ReactElement {
  return (
    <section className="compact-grid">
      {props.statuses.map((item) => (
        <article className="status-item" key={item.platform}>
          <strong>{item.label}</strong>
          <span className={`status-pill ${item.status}`}>{loginLabel(item.status)}</span>
          <small>{new Date(item.lastChecked).toLocaleString()}</small>
        </article>
      ))}
    </section>
  );
}

function SettingsView(props: {
  settings?: SettingsSnapshot;
  outputDirectory: string;
  chooseOutputDirectory: () => void;
}): ReactElement {
  const settings = props.settings;
  return (
    <section className="settings-list">
      <div className="setting-row">
        <span>输出目录</span>
        <strong>{props.outputDirectory || settings?.outputDirectory || "未配置"}</strong>
        <button type="button" onClick={props.chooseOutputDirectory}>
          选择
        </button>
      </div>
      <SettingRow label="并发上限" value={settings ? String(settings.concurrency) : "未读取"} />
      <SettingRow label="图片本地化" value={settings?.localizeMedia ? "开启" : "关闭"} />
      <SettingRow label="回复模式" value={settings?.replyMode ?? "author"} />
    </section>
  );
}

function handleWorkerEvent(
  event: FeedgrabWorkerEvent,
  dispatch: Dispatch<Parameters<typeof appReducer>[1]>
): void {
  if (event.method !== "fetch") {
    return;
  }
  const jobId = event.id ?? undefined;
  if (!jobId) {
    return;
  }

  if (event.event === "job_started") {
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: "info",
        message: `任务已启动，共 ${String(event.result?.total ?? "?")} 条链接`
      }
    });
    return;
  }
  if (event.event === "progress") {
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: "info",
        message: event.url ? `正在抓取：${event.url}` : event.message ?? "抓取进度更新"
      }
    });
    return;
  }
  if (event.event === "log") {
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: event.level ?? "info",
        message: event.message ?? "worker 日志"
      }
    });
    return;
  }
  if (event.event === "artifact" && event.artifact?.path) {
    const artifact = outputArtifactFromPath(event.artifact.path);
    dispatch({
      type: "job/artifact",
      payload: {
        jobId,
        markdownPath: event.artifact.path,
        attachments: []
      }
    });
    dispatch({ type: "output/add", payload: artifact });
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: "success",
        message: `已生成产物：${event.artifact.path}`
      }
    });
    return;
  }
  if (event.event === "error") {
    dispatch({
      type: "job/status",
      payload: {
        jobId,
        status: "failed",
        error: event.error?.message ?? "worker error"
      }
    });
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: "error",
        message: event.error?.message ?? "抓取失败"
      }
    });
    return;
  }
  if (event.event === "done") {
    const errors = typeof event.result?.errors === "number" ? event.result.errors : 0;
    dispatch({
      type: "job/status",
      payload: {
        jobId,
        status: errors > 0 ? "failed" : "completed",
        error: errors > 0 ? `${errors} 个链接失败` : undefined
      }
    });
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: errors > 0 ? "error" : "success",
        message: errors > 0 ? `抓取完成，但有 ${errors} 个失败` : "抓取完成"
      }
    });
    return;
  }
  if (event.event === "cancelled") {
    dispatch({ type: "job/status", payload: { jobId, status: "cancelled" } });
  }
}

function outputArtifactFromPath(markdownPath: string): OutputArtifact {
  const parts = markdownPath.split(/[\\/]/).filter(Boolean);
  return {
    id: `artifact-${markdownPath}`,
    title: parts.at(-1) ?? "artifact",
    platform: parts.at(-2) ?? "Output",
    markdownPath,
    attachments: [],
    createdAt: new Date().toISOString()
  };
}

function loadSavedOutputDirectory(): string {
  try {
    return window.localStorage.getItem("feedgrab.outputDirectory") ?? "";
  } catch {
    return "";
  }
}

function saveOutputDirectory(outputDirectory: string): void {
  try {
    window.localStorage.setItem("feedgrab.outputDirectory", outputDirectory);
  } catch {
    // Ignore storage-denied environments; the path is still applied for this session.
  }
}

function DoctorView(props: { doctor?: DoctorSnapshot }): ReactElement {
  const doctor = props.doctor;
  return (
    <section className="diagnostic-panel">
      <SettingRow label="Python" value={doctor?.python ?? "未检查"} />
      <SettingRow label="浏览器" value={doctor?.browser ?? "unknown"} />
      <SettingRow label="网络" value={doctor?.network ?? "unknown"} />
      <SettingRow label="输出目录可写" value={doctor?.writableOutput ? "是" : "否"} />
      <ul>
        {(doctor?.notes ?? []).map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </section>
  );
}

function AuthView(): ReactElement {
  return (
    <section className="auth-panel">
      <h2>授权占位</h2>
      <p>当前分支只保留本地 license scaffold 和 FeatureGate 位置，不接入真实支付或远程授权服务。</p>
      <div className="auth-bands">
        <span>Free：单 URL 抓取</span>
        <span>Pro：批量、媒体、本地更新</span>
        <span>Business：团队授权、诊断支持</span>
      </div>
    </section>
  );
}

function LogPanel(props: { logs: Array<{ id: string; level: LogLevel; message: string; createdAt: string }> }): ReactElement {
  return (
    <aside className="log-panel" aria-label="实时日志">
      <h2>实时日志</h2>
      {props.logs.map((log) => (
        <p key={log.id} className={`log-line ${log.level}`}>
          <time>{new Date(log.createdAt).toLocaleTimeString()}</time>
          {log.message}
        </p>
      ))}
    </aside>
  );
}

function Metric(props: { label: string; value: number }): ReactElement {
  return (
    <div className="metric">
      <strong>{props.value}</strong>
      <span>{props.label}</span>
    </div>
  );
}

function EmptyState(props: { title: string; detail: string }): ReactElement {
  return (
    <div className="empty-state">
      <strong>{props.title}</strong>
      <span>{props.detail}</span>
    </div>
  );
}

function SettingRow(props: { label: string; value: string }): ReactElement {
  return (
    <div className="setting-row">
      <span>{props.label}</span>
      <strong>{props.value}</strong>
    </div>
  );
}

function parseUrls(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((url) => url.trim())
    .filter((url) => /^https?:\/\//i.test(url));
}

function headingFor(view: ViewKey): string {
  const labels: Record<ViewKey, string> = {
    fetch: "抓取工作台",
    jobs: "任务队列",
    output: "输出库",
    login: "登录中心",
    settings: "设置",
    doctor: "诊断",
    auth: "授权"
  };
  return labels[view];
}

function viewFromSearch(): ViewKey | undefined {
  const view = new URLSearchParams(window.location.search).get("view");
  return isViewKey(view) ? view : undefined;
}

function isViewKey(value: unknown): value is ViewKey {
  return (
    typeof value === "string" &&
    ["fetch", "jobs", "output", "login", "settings", "doctor", "auth"].includes(value)
  );
}

function statusLabel(status: UiJob["status"]): string {
  const labels: Record<UiJob["status"], string> = {
    queued: "排队",
    running: "运行中",
    completed: "完成",
    failed: "失败",
    cancelled: "已取消"
  };
  return labels[status];
}

function loginLabel(status: LoginStatus["status"]): string {
  const labels: Record<LoginStatus["status"], string> = {
    connected: "已连接",
    expired: "已过期",
    missing: "未登录",
    notRequired: "无需登录"
  };
  return labels[status];
}

function platformLabel(platform: SupportedPlatform): string {
  const labels: Record<SupportedPlatform, string> = {
    twitter: "X / Twitter",
    xhs: "小红书",
    youtube: "YouTube",
    bilibili: "Bilibili",
    wechat: "微信公众号",
    github: "GitHub",
    linuxdo: "Discourse",
    feishu: "飞书",
    web: "网页",
    unknown: "未知"
  };
  return labels[platform];
}

function detectPlatform(url: string): SupportedPlatform {
  if (/github\.com/i.test(url)) return "github";
  if (/x\.com|twitter\.com/i.test(url)) return "twitter";
  if (/xiaohongshu\.com/i.test(url)) return "xhs";
  if (/youtube\.com|youtu\.be/i.test(url)) return "youtube";
  if (/mp\.weixin\.qq\.com/i.test(url)) return "wechat";
  if (/linux\.do|idcflare\.com/i.test(url)) return "linuxdo";
  if (/feishu\.cn|larksuite\.com/i.test(url)) return "feishu";
  return /^https?:\/\//i.test(url) ? "web" : "unknown";
}

function resolveFeedgrabApi(): FeedgrabIpcApi {
  if (window.feedgrab) {
    return window.feedgrab;
  }
  if (window.location.protocol === "file:") {
    return createUnavailableApi();
  }
  return createFallbackApi();
}

function createUnavailableApi(): FeedgrabIpcApi {
  const unavailable = () => Promise.reject(new Error("Electron preload 未加载，真实 worker 不可用"));
  return {
    ping: unavailable as FeedgrabIpcApi["ping"],
    detectPlatform: unavailable as FeedgrabIpcApi["detectPlatform"],
    startFetch: unavailable as FeedgrabIpcApi["startFetch"],
    cancelJob: unavailable as FeedgrabIpcApi["cancelJob"],
    doctor: unavailable as FeedgrabIpcApi["doctor"],
    settingsSnapshot: unavailable as FeedgrabIpcApi["settingsSnapshot"],
    loginStatus: unavailable as FeedgrabIpcApi["loginStatus"],
    outputList: unavailable as FeedgrabIpcApi["outputList"],
    openPath: unavailable as FeedgrabIpcApi["openPath"],
    chooseOutputDirectory: unavailable as FeedgrabIpcApi["chooseOutputDirectory"],
    onWorkerEvent: () => () => undefined
  };
}

function createFallbackApi(): FeedgrabIpcApi {
  const listeners = new Set<(event: FeedgrabWorkerEvent) => void>();
  const emit = (event: FeedgrabWorkerEvent): void => {
    for (const listener of listeners) {
      listener(event);
    }
  };
  const outputs: OutputArtifact[] = [
    {
      id: "mock-output-feedgrab",
      title: "feedgrab README snapshot",
      platform: "GitHub",
      markdownPath: "D:\\Notes\\Feeds\\GitHub\\feedgrab.md",
      attachments: [],
      createdAt: new Date("2026-06-25T09:18:00.000Z").toISOString()
    }
  ];

  return {
    ping() {
      return Promise.resolve({ ok: true, worker: "mock" });
    },
    onWorkerEvent(callback) {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    detectPlatform(url) {
      return Promise.resolve(detectPlatform(url));
    },
    startFetch(request) {
      const url = request.urls[0] ?? "";
      const platform = detectPlatform(url);
      const id = `mock-${Date.now()}`;
      const job = {
        id,
        url,
        platform,
        status: "running",
        outputDirectory: request.outputDirectory,
        markdownPath: `${request.outputDirectory}\\${platform}\\mock.md`,
        attachments: [],
        createdAt: new Date().toISOString()
      } satisfies FetchJobSnapshot;
      setTimeout(() => {
        const markdownPath = `${request.outputDirectory}\\${platform}\\mock.md`;
        emit({ id, event: "job_started", method: "fetch", result: { total: request.urls.length } });
        emit({ id, event: "progress", method: "fetch", url, stage: "fetch", message: "fetching" });
        emit({ id, event: "artifact", method: "fetch", url, artifact: { kind: "markdown", path: markdownPath } });
        emit({ id, event: "done", method: "fetch", result: { fetched: request.urls.length, errors: 0 } });
      }, 0);
      return Promise.resolve(job);
    },
    cancelJob(jobId) {
      return Promise.resolve({
        id: jobId,
        url: "",
        platform: "unknown",
        status: "cancelled",
        outputDirectory: "",
        createdAt: new Date().toISOString()
      });
    },
    doctor() {
      return Promise.resolve({
        python: "mock",
        browser: "mock",
        network: "disabled",
        writableOutput: true,
        notes: ["浏览器内测试环境使用 mock worker，不访问真实平台。"]
      });
    },
    settingsSnapshot() {
      return Promise.resolve({
        outputDirectory: "D:\\Notes\\Feeds",
        concurrency: 1,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      });
    },
    loginStatus() {
      const now = new Date().toISOString();
      return Promise.resolve([
        { platform: "twitter", label: "X / Twitter", status: "missing", lastChecked: now },
        { platform: "wechat", label: "微信公众号", status: "connected", lastChecked: now },
        { platform: "github", label: "GitHub", status: "notRequired", lastChecked: now }
      ]);
    },
    outputList() {
      return Promise.resolve(outputs);
    },
    openPath() {
      return Promise.resolve({ ok: true });
    },
    chooseOutputDirectory() {
      return Promise.resolve({ ok: true, path: "D:\\Notes\\Feeds" });
    }
  };
}
