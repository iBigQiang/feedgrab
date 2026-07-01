import { useEffect, useMemo, useReducer, useRef, useState, type Dispatch, type ReactElement, type ReactNode } from "react";

import type {
  DoctorSnapshot,
  FeedgrabIpcApi,
  FeedgrabWorkerEvent,
  FetchMode,
  FetchRequest,
  FetchJobSnapshot,
  LoginSessionImportResult,
  LoginStatus,
  OutputArtifact,
  SettingsFieldSchema,
  SettingsFieldValue,
  SettingsSchema,
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
import sponsorMarkdown from "../../../docs/sponsor.md?raw";
import groupMarkdown from "../../group.md?raw";
import appIconUrl from "../../../docs/feedgrab-icons/windows/app.ico?url";
import desktopPackage from "../../package.json";

const documentBaseUrl = "https://github.com/iBigQiang/feedgrab/tree/feedgrab-desktop/docs/";
const documentRawBaseUrl = "https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/";
const proxiedDocumentRawBaseUrl = "https://edgeone.gh-proxy.com/https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/";
const markdownCacheTtlMs = 6 * 60 * 60 * 1000;
const desktopVersion = typeof desktopPackage.version === "string" ? desktopPackage.version : "";

const sponsorMarkdownConfig: RemoteMarkdownConfig = {
  remoteUrl: `${proxiedDocumentRawBaseUrl}sponsor.md`,
  cacheKey: "feedgrab.sponsorMarkdown.cache.v1",
  sessionCheckedKey: "feedgrab.sponsorMarkdown.remoteChecked.v1",
  fallbackMarkdown: sponsorMarkdown,
  tableAriaLabel: "赞助商列表",
  defaultImageAlt: "赞助商"
};

const communityMarkdownConfig: RemoteMarkdownConfig = {
  remoteUrl: `${proxiedDocumentRawBaseUrl}group.md`,
  cacheKey: "feedgrab.communityMarkdown.cache.v1",
  sessionCheckedKey: "feedgrab.communityMarkdown.remoteChecked.v1",
  fallbackMarkdown: groupMarkdown,
  tableAriaLabel: "社群信息",
  defaultImageAlt: "社群"
};

type SidebarIconName =
  | "download"
  | "list"
  | "folder"
  | "user"
  | "settings"
  | "activity"
  | "heart"
  | "key"
  | "users"
  | "x"
  | "github";

const navigation: Array<{ key: ViewKey; label: string; icon: SidebarIconName }> = [
  { key: "fetch", label: "抓取", icon: "download" },
  { key: "jobs", label: "任务", icon: "list" },
  { key: "output", label: "输出", icon: "folder" },
  { key: "login", label: "登录", icon: "user" },
  { key: "settings", label: "设置", icon: "settings" },
  { key: "doctor", label: "诊断", icon: "activity" },
  { key: "sponsor", label: "赞助", icon: "heart" },
  { key: "auth", label: "社群", icon: "users" }
];

const sampleUrls = [].join("\n");

type SelectedFetchPlatform = string;

type FetchPlatformOption = {
  key: SelectedFetchPlatform;
  id: "auto" | SupportedPlatform;
  label: string;
  command?: string;
  mode?: FetchMode;
};

const fetchPlatformOptions: FetchPlatformOption[] = [
  { key: "auto", id: "auto", label: "URL自动识别" },
  { key: "twitter", id: "twitter", label: "X / Twitter", command: "x-so", mode: "search" },
  { key: "wechat", id: "wechat", label: "微信公众号", command: "mpweixin-id", mode: "account" },
  { key: "xhs", id: "xhs", label: "小红书", command: "xhs-so", mode: "search" },
  { key: "youtube", id: "youtube", label: "YouTube", command: "ytb-so", mode: "search" },
  { key: "bilibili", id: "bilibili", label: "Bilibili" },
  { key: "github", id: "github", label: "GitHub" },
  { key: "linuxdo", id: "linuxdo", label: "LinuxDo" },
  { key: "idcflare", id: "idcflare", label: "IDCFlare" },
  { key: "feishu", id: "feishu", label: "飞书" },
  { key: "kdocs", id: "kdocs", label: "金山文档" },
  { key: "flowus", id: "flowus", label: "FlowUs" },
  { key: "youdao", id: "web", label: "有道云" },
  { key: "zhihu", id: "zhihu", label: "知乎", command: "zhihu-so", mode: "search" },
  { key: "zsxq", id: "zsxq", label: "知识星球" },
  { key: "reddit", id: "reddit", label: "Reddit", command: "reddit-so", mode: "search" },
  { key: "xiaoyuzhou", id: "web", label: "小宇宙" },
  { key: "ximalaya", id: "web", label: "喜马拉雅" },
  { key: "rss", id: "web", label: "RSS" },
  { key: "telegram", id: "web", label: "Telegram" },
  { key: "paid-news", id: "web", label: "付费新闻" },
  { key: "web", id: "web", label: "任意网页" }
];

const compactBasicSettingNames = new Set(["CHROME_CDP_LOGIN", "CHROME_CDP_PORT", "FORCE_REFETCH"]);
const pathPickerSettingNames = new Set(["OUTPUT_DIR", "OBSIDIAN_VAULT", "FEEDGRAB_DATA_DIR"]);
const legacyDesktopDefaultPaths = new Set(["e:\\obsidian\\qiang_obsidian\\inbox"]);

type FetchPlan = {
  urls: string[];
  targets: string[];
  valid: boolean;
  request: FetchRequest;
  commandPreview?: string;
  error?: string;
};

function SidebarIcon(props: { name: SidebarIconName }): ReactElement {
  switch (props.name) {
    case "download":
      return (
        <StrokeIcon>
          <path d="M12 3v11" />
          <path d="m8 10 4 4 4-4" />
          <path d="M5 20h14" />
        </StrokeIcon>
      );
    case "list":
      return (
        <StrokeIcon>
          <path d="M8 6h12" />
          <path d="M8 12h12" />
          <path d="M8 18h12" />
          <path d="M4 6h.01" />
          <path d="M4 12h.01" />
          <path d="M4 18h.01" />
        </StrokeIcon>
      );
    case "folder":
      return (
        <StrokeIcon>
          <path d="M3 7.5h7l2 2h9v8.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
          <path d="M3 7.5V6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1.5" />
        </StrokeIcon>
      );
    case "user":
      return (
        <StrokeIcon>
          <circle cx="12" cy="8" r="4" />
          <path d="M4 20a8 8 0 0 1 16 0" />
        </StrokeIcon>
      );
    case "settings":
      return (
        <StrokeIcon>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.04.04a2 2 0 1 1-2.83 2.83l-.04-.04A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6V20a2 2 0 1 1-4 0v-.06a1.7 1.7 0 0 0-1-.6 1.7 1.7 0 0 0-1.88.34l-.04.04a2 2 0 1 1-2.83-2.83l.04-.04A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1H4a2 2 0 1 1 0-4h.06a1.7 1.7 0 0 0 .6-1 1.7 1.7 0 0 0-.34-1.88l-.04-.04a2 2 0 1 1 2.83-2.83l.04.04A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6V4a2 2 0 1 1 4 0v.06a1.7 1.7 0 0 0 1 .6 1.7 1.7 0 0 0 1.88-.34l.04-.04a2 2 0 1 1 2.83 2.83l-.04.04A1.7 1.7 0 0 0 19.4 9c.22.33.43.67.6 1H20a2 2 0 1 1 0 4h-.06c-.17.33-.38.67-.54 1Z" />
        </StrokeIcon>
      );
    case "activity":
      return (
        <StrokeIcon>
          <path d="M4 13h4l2-7 4 14 2-7h4" />
        </StrokeIcon>
      );
    case "heart":
      return (
        <StrokeIcon>
          <path d="M20.8 6.2a5 5 0 0 0-7.1 0L12 7.9l-1.7-1.7a5 5 0 0 0-7.1 7.1L12 22l8.8-8.7a5 5 0 0 0 0-7.1Z" />
        </StrokeIcon>
      );
    case "key":
      return (
        <StrokeIcon>
          <circle cx="8" cy="15" r="4" />
          <path d="m11 12 9-9" />
          <path d="m15 4 2 2" />
          <path d="m17 2 3 3" />
        </StrokeIcon>
      );
    case "users":
      return (
        <StrokeIcon>
          <circle cx="9" cy="8" r="3" />
          <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
          <path d="M16 11a3 3 0 1 0-.9-5.9" />
          <path d="M18 20a5 5 0 0 0-3-4.6" />
        </StrokeIcon>
      );
    case "x":
      return (
        <svg className="sidebar-svg" viewBox="0 0 24 24" focusable="false" aria-hidden="true">
          <path
            fill="currentColor"
            d="M17.8 3h3.3l-7.2 8.2L22.3 21h-6.6l-5.1-6-5.9 6H1.4l7.7-8.8L1 3h6.8l4.7 5.5L17.8 3Zm-1.2 16.3h1.8L6.8 4.6H4.9l11.7 14.7Z"
          />
        </svg>
      );
    case "github":
      return (
        <svg className="sidebar-svg" viewBox="0 0 24 24" focusable="false" aria-hidden="true">
          <path
            fill="currentColor"
            d="M12 2a10 10 0 0 0-3.2 19.5c.5.1.7-.2.7-.5v-1.8c-2.9.6-3.5-1.2-3.5-1.2-.5-1.1-1.1-1.4-1.1-1.4-.9-.6.1-.6.1-.6 1 0 1.6 1 1.6 1 .9 1.6 2.4 1.1 2.9.9.1-.7.4-1.1.7-1.4-2.3-.3-4.7-1.2-4.7-5A3.9 3.9 0 0 1 7.5 8a3.6 3.6 0 0 1 .1-2.6s.8-.3 2.7 1a9.4 9.4 0 0 1 4.9 0c1.9-1.3 2.7-1 2.7-1 .5 1.2.2 2.1.1 2.6a3.9 3.9 0 0 1 1 2.7c0 3.8-2.4 4.7-4.7 5 .4.3.7.9.7 1.8V21c0 .3.2.6.8.5A10 10 0 0 0 12 2Z"
          />
        </svg>
      );
  }
}

function StrokeIcon(props: { children: ReactNode }): ReactElement {
  return (
    <svg
      className="sidebar-svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
      aria-hidden="true"
    >
      {props.children}
    </svg>
  );
}

export function App(): ReactElement {
  const [state, dispatch] = useReducer(appReducer, undefined, createInitialAppState);
  const [urlText, setUrlText] = useState(sampleUrls);
  const [selectedFetchPlatform, setSelectedFetchPlatform] = useState<SelectedFetchPlatform>("auto");
  const [loginStatus, setLoginStatus] = useState<LoginStatus[]>([]);
  const [loginImportResult, setLoginImportResult] = useState<LoginSessionImportResult | undefined>();
  const [toast, setToast] = useState<{ message: string; tone: "info" | "success" | "warning" | "error" } | undefined>();
  const [repairingDoctor, setRepairingDoctor] = useState("");
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
                ? "Python 后台工作进程已连接。"
                : "浏览器测试后台工作进程已连接。"
          }
        })
      )
      .catch((error: unknown) =>
        dispatch({
          type: "job/log",
          payload: {
            level: "error",
            message: error instanceof Error ? error.message : "后台工作进程连接失败"
          }
        })
      );
    void api.settingsSnapshot().then((settings) =>
      dispatch({
        type: "settings/load",
        payload: settings,
        resolvedOutputDirectory: settings.effectiveOutputDirectory || settings.outputDirectory || loadSavedOutputDirectory()
      })
    );
    void api.settingsSchema().then((schema) => dispatch({ type: "settings/schema", payload: schema }));
    void api.doctor().then((doctor) => dispatch({ type: "doctor/load", payload: doctor }));
    void api.loginStatus().then(setLoginStatus);

    return api.onWorkerEvent((event) => handleWorkerEvent(event, dispatch));
  }, [api]);

  useEffect(() => {
    if (!toast) {
      return undefined;
    }
    const timer = window.setTimeout(() => setToast(undefined), 2000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const fetchPlan = useMemo(
    () =>
      buildFetchPlan(
        urlText,
        selectedFetchPlatform,
        state.outputDirectory,
        state.settingsSchema,
        state.pendingSettings
      ),
    [urlText, selectedFetchPlatform, state.outputDirectory, state.settingsSchema, state.pendingSettings]
  );
  const urls = fetchPlan.urls;
  const runningJobs = state.jobs.filter((job) => job.status === "running").length;
  const failedJobs = state.jobs.filter((job) => job.status === "failed").length;
  const completedJobs = state.jobs.filter((job) => job.status === "completed").length;
  const showMetrics = state.selectedView === "fetch" || state.selectedView === "jobs" || state.selectedView === "output";

  function startFetch(): void {
    if (!fetchPlan.valid) {
      dispatch({
        type: "job/log",
        payload: { level: "warning", message: fetchPlan.error ?? "请输入至少一个抓取目标" }
      });
      return;
    }
    if (Object.keys(state.pendingSettings).length > 0) {
      dispatch({
        type: "job/log",
        payload: { level: "warning", message: "有未保存设置，请先保存设置后再开始抓取。" }
      });
      return;
    }

    void api
      .startFetch(fetchPlan.request)
      .then((jobs) => {
        for (const job of [...jobs].reverse()) {
          dispatch({ type: "job/upsert", payload: job });
        }
        dispatch({
          type: "job/log",
          payload: {
            jobId: jobs[0]?.id,
            level: "info",
            message: `后台工作进程已接收 ${jobs.length} 条任务，输出到 ${state.outputDirectory}`
          }
        });
      })
      .catch((error: unknown) => {
        dispatch({
          type: "job/log",
          payload: {
            level: "error",
            message: error instanceof Error ? error.message : "后台工作进程调用失败"
          }
        });
      });
  }

  function cancelJob(job: UiJob): void {
    dispatch({ type: "job/cancel", payload: { jobId: job.id } });
    void api.cancelJob(job.id);
  }

  function chooseOutputDirectory(): void {
    chooseDirectoryForSetting("OUTPUT_DIR");
  }

  function chooseDirectoryForSetting(name: string): void {
    const title =
      name === "OBSIDIAN_VAULT"
        ? "选择 Obsidian Vault 目录"
        : name === "FEEDGRAB_DATA_DIR"
          ? "选择登录态和数据目录"
          : "选择 feedgrab 输出目录";
    void api.chooseOutputDirectory({ title }).then((result) => {
      if (result.ok && result.path) {
        if (name === "OUTPUT_DIR") {
          saveOutputDirectory(result.path);
          dispatch({ type: "settings/outputDirectory", payload: result.path });
          void api
            .settingsUpdate({ OUTPUT_DIR: result.path })
            .then(() => refreshSettingsFromWorker())
            .catch(() => undefined);
          return;
        }
        dispatch({ type: "settings/edit", payload: { name, value: result.path } });
      }
    });
  }

  function refreshLoginStatus(platforms?: SupportedPlatform[]): void {
    const request = platforms ? { refresh: true, platforms } : { refresh: true };
    const label = platforms?.length === 1 ? platformLabel(platforms[0] ?? "unknown") : "";
    void api
      .loginStatus(request)
      .then((statuses) => {
        if (platforms?.length) {
          setLoginStatus((current) => mergeLoginStatuses(current, statuses));
        } else {
          setLoginStatus(statuses);
        }
        showToast(label ? `${label} 登录态已刷新` : "已刷新全部平台登录态", "success");
      })
      .catch((error: unknown) =>
        {
          const message = error instanceof Error ? error.message : "登录态检测失败";
          showToast(message, "error");
        }
      );
  }

  function importLoginSessions(sourceDirectory?: string, platform?: SupportedPlatform): void {
    const importRequest = platform
      ? api.importLoginSessions(sourceDirectory, platform)
      : sourceDirectory
        ? api.importLoginSessions(sourceDirectory)
        : api.importLoginSessions();
    void importRequest
      .then((result) => {
        setLoginImportResult(result);
        showToast(result.ok ? "已完成登录态导入" : result.error ?? "登录态导入失败", result.ok ? "success" : "warning");
        return api.loginStatus(platform ? { refresh: true, platforms: [platform] } : { refresh: true });
      })
      .then((statuses) => {
        if (platform) {
          setLoginStatus((current) => mergeLoginStatuses(current, statuses));
          return;
        }
        setLoginStatus(statuses);
      })
      .catch((error: unknown) =>
        {
          const message = error instanceof Error ? error.message : "登录态导入失败";
          showToast(message, "error");
        }
      );
  }

  function loginPlatform(platform: SupportedPlatform): void {
    showToast(`正在打开 ${platformLabel(platform)} 登录流程，登录成功后请等待客户端提示已保存。`, "info");
    void api
      .loginPlatform(platform)
      .then((result) => {
        showToast(result.message, result.ok ? "success" : "error");
        return api.loginStatus({ refresh: true, platforms: [platform] });
      })
      .then((statuses) => {
        setLoginStatus((current) => mergeLoginStatuses(current, statuses));
      })
      .catch((error: unknown) =>
        {
          const message = error instanceof Error ? error.message : "登录流程启动失败";
          showToast(message, "error");
        }
      );
  }

  function repairDoctor(checkName: string): void {
    setRepairingDoctor(checkName);
    void api
      .repairDoctor(checkName)
      .then((result) => {
        showToast(result.message, result.ok ? "success" : "error");
        return api.doctor();
      })
      .then((doctor) => dispatch({ type: "doctor/load", payload: doctor }))
      .catch((error: unknown) => {
        showToast(error instanceof Error ? error.message : "依赖安装/更新失败", "error");
      })
      .finally(() => setRepairingDoctor(""));
  }

  function updateSetting(name: string, value: SettingsFieldValue): void {
    dispatch({ type: "settings/edit", payload: { name, value } });
  }

  function saveSettings(): void {
    void api
      .settingsUpdate(state.pendingSettings)
      .then(async (result) => {
        dispatch({ type: "settings/saved", payload: result });
        if (result.ok) {
          await refreshSettingsFromWorker();
        }
      })
      .catch((error: unknown) =>
        dispatch({
          type: "settings/saved",
          payload: {
            ok: false,
            updated: [],
            error: error instanceof Error ? error.message : "设置保存失败"
          }
        })
      );
  }

  async function refreshSettingsFromWorker(): Promise<void> {
    const [settings, schema] = await Promise.all([api.settingsSnapshot(), api.settingsSchema()]);
    dispatch({
      type: "settings/load",
      payload: settings,
      resolvedOutputDirectory: settings.effectiveOutputDirectory || settings.outputDirectory || loadSavedOutputDirectory()
    });
    dispatch({ type: "settings/schema", payload: schema });
  }

  function showToast(message: string, tone: "info" | "success" | "warning" | "error" = "info"): void {
    setToast({ message, tone });
  }

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="主导航">
        <div className="brand">
          <img className="brand-mark brand-icon" src={appIconUrl} alt="" aria-hidden="true" />
          <div>
            <strong>feedgrab</strong>
            <small>桌面版</small>
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
              <span className="nav-icon" aria-hidden="true">
                <SidebarIcon name={item.icon} />
              </span>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          {desktopVersion ? <div className="sidebar-version">版本号：v{desktopVersion}</div> : null}
          <div className="author-panel">
            <div className="author-row">
              <span className="author-label">作者：</span>
              <span className="author-value">强子手记</span>
            </div>
            <div className="author-row">
              <span className="author-label">主页：</span>
              <a className="author-text-link" href="https://x.com/iBigQiang" target="_blank" rel="noreferrer">
                @iBigQiang
              </a>
            </div>
            <div className="author-row">
              <span className="author-label">推特：</span>
              <span className="author-value author-social-value">
                <a className="author-icon-link" href="https://x.com/iBigQiang" target="_blank" rel="noreferrer" aria-label="推特">
                  <span aria-hidden="true" className="author-link-icon">
                    <SidebarIcon name="x" />
                  </span>
                </a>
                <span>X</span>
              </span>
            </div>
            <div className="author-row">
              <span className="author-label">仓库：</span>
              <span className="author-value author-social-value">
                <a
                  className="author-icon-link"
                  href="https://github.com/iBigQiang/feedgrab/tree/feedgrab-desktop"
                  target="_blank"
                  rel="noreferrer"
                  aria-label="仓库"
                >
                  <span aria-hidden="true" className="author-link-icon">
                    <SidebarIcon name="github" />
                  </span>
                </a>
                <span>GitHub</span>
              </span>
            </div>
          </div>
        </div>
      </aside>

      <main className="workspace">
        {toast ? (
          <div className={`toast ${toast.tone}`} role="status" data-testid="toast">
            {toast.message}
          </div>
        ) : null}
        <header className={showMetrics ? "topbar with-metrics" : "topbar"}>
          <div>
            <h1>{headingFor(state.selectedView)}</h1>
          </div>
          {showMetrics ? (
            <div className="status-strip" aria-label="任务状态">
              <Metric label="运行" value={runningJobs} />
              <Metric label="完成" value={completedJobs} />
              <Metric label="失败" value={failedJobs} />
            </div>
          ) : null}
        </header>

        {state.selectedView === "fetch" ? (
          <FetchView
            urlText={urlText}
            setUrlText={setUrlText}
            selectedPlatform={selectedFetchPlatform}
            setSelectedPlatform={setSelectedFetchPlatform}
            fetchPlan={fetchPlan}
            lastNotice={state.lastNotice}
            outputDirectory={state.outputDirectory}
            urls={urls}
            logs={state.logs}
            startFetch={startFetch}
            chooseOutputDirectory={chooseOutputDirectory}
          />
        ) : null}
        {state.selectedView === "jobs" ? <JobsView jobs={state.jobs} cancelJob={cancelJob} /> : null}
        {state.selectedView === "output" ? (
          <OutputView
            outputs={state.outputs}
            jobs={state.jobs}
            api={api}
            clearOutputs={() => dispatch({ type: "output/clear" })}
          />
        ) : null}
        {state.selectedView === "login" ? (
          <LoginView
            statuses={loginStatus}
            importResult={loginImportResult}
            refreshLoginStatus={refreshLoginStatus}
            importLoginSessions={importLoginSessions}
            loginPlatform={loginPlatform}
          />
        ) : null}
        {state.selectedView === "settings" ? (
          <SettingsView
            settings={state.settings}
            settingsSchema={state.settingsSchema}
            pendingSettings={state.pendingSettings}
            outputDirectory={state.outputDirectory}
            chooseDirectoryForSetting={chooseDirectoryForSetting}
            updateSetting={updateSetting}
            saveSettings={saveSettings}
          />
        ) : null}
        {state.selectedView === "doctor" ? (
          <DoctorView doctor={state.doctor} repairDoctor={repairDoctor} repairingDoctor={repairingDoctor} />
        ) : null}
        {state.selectedView === "sponsor" ? <SponsorView /> : null}
        {state.selectedView === "auth" ? <AuthView /> : null}
      </main>
    </div>
  );
}

function FetchView(props: {
  urlText: string;
  setUrlText: (value: string) => void;
  selectedPlatform: SelectedFetchPlatform;
  setSelectedPlatform: (value: SelectedFetchPlatform) => void;
  fetchPlan: FetchPlan;
  lastNotice?: string;
  outputDirectory: string;
  urls: string[];
  logs: Array<{ id: string; level: LogLevel; message: string; createdAt: string }>;
  startFetch: () => void;
  chooseOutputDirectory: () => void;
}): ReactElement {
  return (
    <section className="split-layout">
      <div className="primary-pane">
        <div className="platform-preview" aria-label="平台识别结果">
          <strong>现已支持的平台：</strong>
          {fetchPlatformOptions.map((option) => (
            <button
              type="button"
              key={option.key}
              className={props.selectedPlatform === option.key ? "is-active" : ""}
              onClick={() => props.setSelectedPlatform(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <label className="field-label" htmlFor="content-links">
          抓取目标（URL / 关键词 / 关键词组 / 账号）
        </label>
        <textarea
          id="content-links"
          value={props.urlText}
          onChange={(event) => props.setUrlText(event.target.value)}
          placeholder={"一行一个URL或词（不含冒号前部分），样例如下：\n--------------------\nX 单贴：https://x.com/iBigQiang/status/2015088004109615266\nX 长文：https://x.com/iBigQiang/status/2061419867862082034\nX 关键词批量：codex\nX 词组批量抓：claude code,codex,Hermes,opencode\nX 按书签批量：https://x.com/i/bookmarks/2007306111150674215\nX 按列表批量：https://x.com/i/lists/2062881752830673350\nX 按账号批量：https://x.com/iBigQiang\n公众号文章：https://mp.weixin.qq.com/s/e_nzlfUQkWIwhll08uyTTg\n公众号批量：强子手记\nGitHub：https://github.com/iBigQiang/feedgrab\n其他：URL（单贴地址、wiki首页地址等）"}
          spellCheck={false}
        />
        {props.fetchPlan.commandPreview ? (
          <p className="command-preview">将执行：{props.fetchPlan.commandPreview}</p>
        ) : null}
        {props.lastNotice ? <p className="inline-notice">{props.lastNotice}</p> : null}
        <div className="form-row">
          <div className="output-directory-group">
            <span className="output-directory-label">输出目录：</span>
            <code className="output-directory-path" title={props.outputDirectory}>
              {props.outputDirectory}
            </code>
            <button type="button" className="secondary-action" onClick={props.chooseOutputDirectory}>
              选择
            </button>
          </div>
          <button type="button" className="primary-action" onClick={props.startFetch}>
            开始抓取
          </button>
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
      {props.jobs.map((job) => {
        const artifactCount = job.artifactPaths.length;
        const latestArtifactPath = job.markdownPath ?? job.artifactPaths.at(-1);
        const summaryMessage = job.lastMessage ?? statusLabel(job.status);
        const errorDetail = job.error && !summaryMessage.includes(job.error) ? job.error : undefined;
        return (
          <article className="job-row" key={job.id}>
            <div className="job-main">
              <strong>{job.commandPreview ?? job.url}</strong>
              <div className="job-summary" aria-label="任务进度">
                <span className="job-message">{summaryMessage}</span>
                {artifactCount > 0 ? <span className="job-artifact-count">已保存 {artifactCount} 个 Markdown</span> : null}
                {errorDetail ? <span className="job-error">{errorDetail}</span> : null}
              </div>
              {latestArtifactPath ? <code className="job-artifact-path">{latestArtifactPath}</code> : null}
            </div>
            <div className="job-actions">
              <span className={`status-pill ${job.status}`}>{statusLabel(job.status)}</span>
              <button type="button" onClick={() => props.cancelJob(job)} disabled={job.status !== "running"}>
                取消
              </button>
            </div>
          </article>
        );
      })}
    </section>
  );
}

function OutputView(props: {
  outputs: OutputArtifact[];
  jobs: UiJob[];
  api: FeedgrabIpcApi;
  clearOutputs: () => void;
}): ReactElement {
  const outputs = [...props.outputs].sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt));
  const runningCount = props.jobs.filter((job) => job.status === "running").length;
  return (
    <section className="output-panel">
      <div className="output-toolbar">
        <span>
          {runningCount > 0
            ? `当前有 ${runningCount} 个任务运行中，Markdown 生成后会即时出现在这里。`
            : "仅显示本次打开客户端后产生的输出记录，不扫描或删除输出目录文件。"}
        </span>
        <button type="button" className="secondary-action" onClick={props.clearOutputs} disabled={outputs.length === 0}>
          清空记录
        </button>
      </div>
      <div className="output-list">
        {outputs.map((item, index) => (
          <article className="output-row" key={item.id}>
            <strong className="output-seq">{`#${outputs.length - index}`}</strong>
            <div className="output-main">
              <span>{item.platform}</span>
              <strong>{item.title}</strong>
              <code>{item.markdownPath}</code>
            </div>
            <div className="output-actions">
              <button type="button" onClick={() => void props.api.openPath(item.markdownPath)}>
                打开
              </button>
            </div>
          </article>
        ))}
        {outputs.length === 0 ? (
          <EmptyState
            title={runningCount > 0 ? "等待首个输出" : "暂无输出"}
            detail={
              runningCount > 0
                ? "运行中的任务每保存一个 Markdown，都会自动追加到这里。"
                : "本页只记录当前客户端会话内新生成的 Markdown 产物。"
            }
          />
        ) : null}
      </div>
    </section>
  );
}

function LoginView(props: {
  statuses: LoginStatus[];
  importResult?: LoginSessionImportResult;
  refreshLoginStatus: (platforms?: SupportedPlatform[]) => void;
  importLoginSessions: (sourceDirectory?: string, platform?: SupportedPlatform) => void;
  loginPlatform: (platform: SupportedPlatform) => void;
}): ReactElement {
  const statuses = completeLoginStatuses(props.statuses);
  return (
    <section className="login-panel">
      <div className="panel-toolbar">
        <button type="button" className="secondary-action" onClick={() => props.refreshLoginStatus()}>
          重新检测
        </button>
        <button type="button" className="primary-action" onClick={() => props.importLoginSessions()}>
          导入本机登录态/安装目录 sessions
        </button>
      </div>
      <div className="login-import-summary">
        {props.importResult ? (
          <>
            <span>导入来源：{props.importResult.sourceDirectory ?? "默认 sessions 目录"}</span>
            <strong>{`导入 ${props.importResult.imported.length} / 跳过 ${props.importResult.skipped.length} / 停用 ${props.importResult.disabled?.length ?? 0} / 忽略 ${props.importResult.ignored.length}`}</strong>
          </>
        ) : (
          <span>默认从安装包或开发目录的 sessions 文件夹导入，导入后会显示实际来源。</span>
        )}
      </div>
      <div className="compact-grid">
        {statuses.map((item) => (
          <article className="status-item login-status-item" key={item.platform}>
            <div>
              <strong>{item.label}</strong>
              <small>{new Date(item.lastChecked).toLocaleString()}</small>
            </div>
            <span className={`status-pill ${item.status}`}>{loginLabel(item.status)}</span>
            <small>{loginStatusDetail(item)}</small>
            <div className="inline-actions">
              <button type="button" onClick={() => props.refreshLoginStatus([item.platform])}>
                检测
              </button>
              <button type="button" onClick={() => props.loginPlatform(item.platform)}>
                登录
              </button>
              <button type="button" onClick={() => props.importLoginSessions(undefined, item.platform)}>
                导入
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

const loginPlatformDefaults: Array<Pick<LoginStatus, "platform" | "label" | "status" | "loginRequired">> = [
  { platform: "twitter", label: "X / Twitter", status: "missing", loginRequired: true },
  { platform: "xhs", label: "小红书", status: "missing", loginRequired: true },
  { platform: "wechat", label: "微信公众号", status: "missing", loginRequired: true },
  { platform: "feishu", label: "飞书", status: "missing", loginRequired: true },
  { platform: "kdocs", label: "金山文档", status: "missing", loginRequired: true },
  { platform: "flowus", label: "FlowUs", status: "missing", loginRequired: true },
  { platform: "reddit", label: "Reddit", status: "missing", loginRequired: true },
  { platform: "zhihu", label: "知乎", status: "missing", loginRequired: true },
  { platform: "linuxdo", label: "LinuxDo", status: "missing", loginRequired: true },
  { platform: "idcflare", label: "IDCFlare", status: "missing", loginRequired: true },
  { platform: "zsxq", label: "知识星球", status: "missing", loginRequired: true },
  { platform: "github", label: "GitHub", status: "notRequired", loginRequired: false },
  { platform: "youtube", label: "YouTube", status: "notRequired", loginRequired: false },
  { platform: "bilibili", label: "Bilibili", status: "notRequired", loginRequired: false },
  { platform: "web", label: "网页", status: "notRequired", loginRequired: false }
];

function completeLoginStatuses(statuses: LoginStatus[]): LoginStatus[] {
  const byPlatform = new Map<SupportedPlatform, LoginStatus>();
  for (const status of statuses) {
    if (status.platform === "unknown") {
      continue;
    }
    byPlatform.set(status.platform, status);
  }
  const now = new Date().toISOString();
  for (const defaults of loginPlatformDefaults) {
    if (!byPlatform.has(defaults.platform)) {
      byPlatform.set(defaults.platform, {
        platform: defaults.platform,
        label: defaults.label,
        status: defaults.status,
        lastChecked: now,
        loginRequired: defaults.loginRequired
      });
    }
  }
  const order = new Map(loginPlatformDefaults.map((item, index) => [item.platform, index]));
  return [...byPlatform.values()].sort((left, right) => {
    const leftOrder = order.get(left.platform) ?? 10_000;
    const rightOrder = order.get(right.platform) ?? 10_000;
    if (leftOrder !== rightOrder) {
      return leftOrder - rightOrder;
    }
    return left.label.localeCompare(right.label, "zh-Hans-CN");
  });
}

function SettingsView(props: {
  settings?: SettingsSnapshot;
  settingsSchema?: SettingsSchema;
  pendingSettings: Record<string, SettingsFieldValue>;
  outputDirectory: string;
  chooseDirectoryForSetting: (name: string) => void;
  updateSetting: (name: string, value: SettingsFieldValue) => void;
  saveSettings: () => void;
}): ReactElement {
  const [activeTab, setActiveTab] = useState<"basic" | "platform">("basic");
  const [activePlatformId, setActivePlatformId] = useState("");
  const settings = props.settings;
  const schema = props.settingsSchema ?? settingsSchemaFromSnapshot(settings);
  const orderedPlatforms = orderSettingsPlatforms(schema.platforms);
  const activePlatform = orderedPlatforms.find((platform) => platform.id === activePlatformId) ?? orderedPlatforms[0];
  const primaryBasicFields = schema.basic.filter((field) => !compactBasicSettingNames.has(field.name));
  const compactBasicFields = schema.basic.filter((field) => compactBasicSettingNames.has(field.name));
  const activePlatformGroups = activePlatform ? groupPlatformSettings(activePlatform) : [];
  const pendingCount = Object.keys(props.pendingSettings).length;

  return (
    <section className="settings-panel">
      <div className="settings-tabs" role="tablist" aria-label="设置分类">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "basic"}
          className={activeTab === "basic" ? "is-active" : ""}
          onClick={() => setActiveTab("basic")}
        >
          基础设置
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "platform"}
          className={activeTab === "platform" ? "is-active" : ""}
          onClick={() => setActiveTab("platform")}
        >
          平台设置
        </button>
      </div>
      {activeTab === "basic" ? (
        <div className="settings-list" role="tabpanel">
          {primaryBasicFields.map((field) => (
            <SchemaSettingField
              key={field.name}
              field={field}
              pendingSettings={props.pendingSettings}
              updateSetting={props.updateSetting}
              chooseDirectoryForSetting={
                pathPickerSettingNames.has(field.name) ? props.chooseDirectoryForSetting : undefined
              }
            />
          ))}
          {compactBasicFields.length > 0 ? (
            <section className="settings-compact-section" aria-label="Chrome CDP 与抓取控制">
              <div className="settings-compact-grid">
                {compactBasicFields.map((field) => (
                  <SchemaSettingField
                    key={field.name}
                    field={field}
                    pendingSettings={props.pendingSettings}
                    updateSetting={props.updateSetting}
                    compact
                  />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      ) : (
        <div className="platform-settings-list" role="tabpanel">
          <div className="platform-subtabs" role="tablist" aria-label="平台设置菜单">
            {orderedPlatforms.map((platform) => (
              <button
                key={platform.id}
                type="button"
                role="tab"
                aria-selected={platform.id === activePlatform?.id}
                className={platform.id === activePlatform?.id ? "is-active" : ""}
                onClick={() => setActivePlatformId(platform.id)}
              >
                {platform.label}
              </button>
            ))}
          </div>
          {activePlatform ? (
            <section className="platform-settings-group" key={activePlatform.id}>
              <h2>{activePlatform.label}</h2>
              {activePlatformGroups.length > 0 ? (
                activePlatformGroups.map((group) => (
                  <section className="platform-setting-section" key={group.id} aria-label={group.label}>
                    <h3>{group.label}</h3>
                    <div className="platform-setting-grid">
                      {group.fields.map((field) => (
                        <SchemaSettingField
                          key={field.name}
                          field={field}
                          pendingSettings={props.pendingSettings}
                          updateSetting={props.updateSetting}
                          compact
                        />
                      ))}
                    </div>
                  </section>
                ))
              ) : (
                <EmptyState title="暂无可编辑项" detail="该平台当前没有需要在桌面端配置的参数。" />
              )}
            </section>
          ) : (
            <EmptyState title="暂无平台设置" detail="当前 schema 没有返回平台配置项。" />
          )}
        </div>
      )}
      <div className="settings-savebar">
        <span>{pendingCount > 0 ? `${pendingCount} 项待保存` : "没有待保存改动"}</span>
        <button type="button" className="primary-action" onClick={props.saveSettings} disabled={pendingCount === 0}>
          保存设置
        </button>
      </div>
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
    const command = typeof event.result?.command === "string" ? event.result.command : "";
    const message = command
      ? `正在执行：${command}`
      : `任务已启动，共 ${String(event.result?.total ?? "?")} 个目标`;
    dispatch({ type: "job/message", payload: { jobId, message } });
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: "info",
        message: `任务已启动，共 ${String(event.result?.total ?? "?")} 个目标`
      }
    });
    return;
  }
  if (event.event === "progress") {
    const message = event.url ? `正在抓取：${event.url}` : event.message ?? "抓取进度更新";
    dispatch({ type: "job/message", payload: { jobId, message } });
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: "info",
        message
      }
    });
    return;
  }
  if (event.event === "log") {
    dispatch({ type: "job/message", payload: { jobId, message: event.message ?? "后台工作进程日志" } });
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: event.level ?? "info",
        message: event.message ?? "后台工作进程日志"
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
        message: `已保存文档：${event.artifact.path}`
      }
    });
    return;
  }
  if (event.event === "error") {
    const message = event.error?.message ?? "抓取失败";
    dispatch({
      type: "job/status",
      payload: {
        jobId,
        status: "failed",
        error: event.error?.message ?? "后台工作进程错误"
      }
    });
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: "error",
        message
      }
    });
    dispatch({ type: "job/message", payload: { jobId, message } });
    return;
  }
  if (event.event === "done") {
    const errors = typeof event.result?.errors === "number" ? event.result.errors : 0;
    const errorMessage = typeof event.result?.error === "string" ? event.result.error : "";
    const message = errors > 0 ? `抓取失败：${errorMessage || `${errors} 个目标失败`}` : "抓取完成";
    dispatch({
      type: "job/status",
      payload: {
        jobId,
        status: errors > 0 ? "failed" : "completed",
        error: errors > 0 ? errorMessage || `${errors} 个链接失败` : undefined
      }
    });
    dispatch({
      type: "job/log",
      payload: {
        jobId,
        level: errors > 0 ? "error" : "success",
        message
      }
    });
    dispatch({ type: "job/message", payload: { jobId, message } });
    return;
  }
  if (event.event === "cancelled") {
    dispatch({ type: "job/status", payload: { jobId, status: "cancelled" } });
    dispatch({ type: "job/message", payload: { jobId, message: "任务已取消" } });
  }
}

const outputPlatformLabels = new Map<string, string>([
  ["x", "X"],
  ["xhs", "XHS"],
  ["mpweixin", "mpweixin"],
  ["youtube", "YouTube"],
  ["bilibili", "Bilibili"],
  ["github", "GitHub"],
  ["reddit", "Reddit"],
  ["linuxdo", "LinuxDo"],
  ["idcflare", "IDCFlare"],
  ["feishu", "飞书"],
  ["kdocs", "KDocs"],
  ["flowus", "FlowUs"],
  ["zhihu", "知乎"],
  ["telegram", "Telegram"],
  ["rss", "RSS"],
  ["web", "网页"]
]);

function outputArtifactFromPath(markdownPath: string): OutputArtifact {
  const parts = markdownPath.split(/[\\/]/).filter(Boolean);
  const platformPart = parts.find((part) => outputPlatformLabels.has(part.toLowerCase()));
  return {
    id: `artifact-${markdownPath}`,
    title: parts.at(-1) ?? "文档",
    platform: platformPart ? outputPlatformLabels.get(platformPart.toLowerCase()) ?? platformPart : parts.at(-2) ?? "输出",
    markdownPath,
    attachments: [],
    createdAt: new Date().toISOString()
  };
}

function loadSavedOutputDirectory(): string {
  try {
    const saved = window.localStorage.getItem("feedgrab.outputDirectory") ?? "";
    if (isLegacyDesktopDefaultPath(saved)) {
      window.localStorage.removeItem("feedgrab.outputDirectory");
      return "";
    }
    return saved;
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

function isLegacyDesktopDefaultPath(value: string): boolean {
  return legacyDesktopDefaultPaths.has(value.trim().replace(/\//g, "\\").replace(/\\+$/g, "").toLowerCase());
}

function DoctorView(props: {
  doctor?: DoctorSnapshot;
  repairDoctor: (checkName: string) => void;
  repairingDoctor: string;
}): ReactElement {
  const doctor = props.doctor;
  const checks = doctor?.checks?.length ? doctor.checks : fallbackDoctorChecks(doctor);
  const repairableCount = checks.filter((check) => check.status !== "ok" && Boolean(check.repair) && check.repair?.available !== false).length;
  return (
    <section className="diagnostic-panel">
      <div className="diagnostic-toolbar">
        <button
          type="button"
          className="secondary-action danger-action"
          onClick={() => props.repairDoctor("all")}
          disabled={Boolean(props.repairingDoctor)}
        >
          {props.repairingDoctor === "all" ? "正在安装/更新..." : "安装/更新所有依赖"}
        </button>
        <small>{repairableCount > 0 ? `${repairableCount} 项可自动处理` : "所有核心依赖状态正常"}</small>
      </div>
      <div className="diagnostic-grid">
        {checks.map((check) => (
          <article className="diagnostic-row" key={check.name}>
            <span>{check.label || check.name}</span>
            <strong>{check.message || diagnosticStatusLabel(check.status)}</strong>
            <em className={`diagnostic-status ${check.status}`}>{diagnosticStatusLabel(check.status)}</em>
            <span className="diagnostic-action">
              {check.status !== "ok" && check.repair && check.repair.available !== false ? (
                <button
                  type="button"
                  onClick={() => props.repairDoctor(check.name)}
                  disabled={Boolean(props.repairingDoctor)}
                >
                  {props.repairingDoctor === check.name ? "处理中" : check.repair?.label ?? "安装/更新"}
                </button>
              ) : null}
            </span>
          </article>
        ))}
      </div>
    </section>
  );
}

type RemoteMarkdownConfig = {
  remoteUrl: string;
  cacheKey: string;
  sessionCheckedKey: string;
  fallbackMarkdown: string;
  tableAriaLabel: string;
  defaultImageAlt: string;
};

function SponsorView(): ReactElement {
  return <RemoteMarkdownView config={sponsorMarkdownConfig} />;
}

function CommunityView(): ReactElement {
  return <RemoteMarkdownView config={communityMarkdownConfig} />;
}

function RemoteMarkdownView(props: { config: RemoteMarkdownConfig }): ReactElement {
  const { config } = props;
  const [activeMarkdown, setActiveMarkdown] = useState(() => loadCachedMarkdown(config) ?? config.fallbackMarkdown);

  useEffect(() => {
    if (hasCheckedMarkdownThisSession(config)) {
      return undefined;
    }
    markMarkdownCheckedThisSession(config);
    let cancelled = false;

    void fetchOnlineMarkdown(config)
      .then((onlineMarkdown) => {
        if (!cancelled) {
          setActiveMarkdown(onlineMarkdown);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setActiveMarkdown((current) => current || config.fallbackMarkdown);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [config]);

  return (
    <section className="sponsor-panel">
      <div className="markdown-preview">{renderMarkdownBlocks(activeMarkdown, config)}</div>
    </section>
  );
}

type RemoteMarkdownCache = {
  markdown: string;
  fetchedAt: number;
};

type DocumentTableRow = {
  href: string;
  imageSrc?: string;
  imageAlt?: string;
  logoWidth?: string;
  imageWidth?: string;
  paragraphs: string[];
};

type HtmlImageBlock = {
  src: string;
  alt: string;
  width?: number;
};

function loadCachedMarkdown(config: RemoteMarkdownConfig): string | undefined {
  try {
    const rawCache = window.localStorage.getItem(config.cacheKey);
    if (!rawCache) {
      return undefined;
    }
    const cached = JSON.parse(rawCache) as Partial<RemoteMarkdownCache>;
    if (typeof cached.markdown !== "string" || typeof cached.fetchedAt !== "number") {
      return undefined;
    }
    if (Date.now() - cached.fetchedAt > markdownCacheTtlMs || cached.fetchedAt > Date.now() + 60_000) {
      return undefined;
    }
    return isUsableMarkdown(cached.markdown) ? cached.markdown : undefined;
  } catch {
    return undefined;
  }
}

function saveCachedMarkdown(config: RemoteMarkdownConfig, markdown: string): void {
  try {
    const cache: RemoteMarkdownCache = { markdown, fetchedAt: Date.now() };
    window.localStorage.setItem(config.cacheKey, JSON.stringify(cache));
  } catch {
    // localStorage can fail in restricted profiles; the bundled markdown remains the fallback.
  }
}

function hasCheckedMarkdownThisSession(config: RemoteMarkdownConfig): boolean {
  try {
    return window.sessionStorage.getItem(config.sessionCheckedKey) === "1";
  } catch {
    return false;
  }
}

function markMarkdownCheckedThisSession(config: RemoteMarkdownConfig): void {
  try {
    window.sessionStorage.setItem(config.sessionCheckedKey, "1");
  } catch {
    // Ignore storage failures and rely on the in-memory component lifecycle.
  }
}

function isUsableMarkdown(markdown: string): boolean {
  return markdown.trim().length >= 20;
}

async function fetchOnlineMarkdown(config: RemoteMarkdownConfig): Promise<string> {
  const remoteFetcher = window.feedgrab?.fetchRemoteMarkdown;
  if (remoteFetcher) {
    const result = await remoteFetcher(config.remoteUrl);
    if (!result.ok || !result.markdown) {
      throw new Error(result.error ?? "Markdown 请求失败");
    }
    if (!isUsableMarkdown(result.markdown)) {
      throw new Error("Markdown 内容为空或格式异常");
    }
    saveCachedMarkdown(config, result.markdown);
    return result.markdown;
  }
  const response = await fetch(config.remoteUrl, { cache: "no-cache" });
  if (!response.ok) {
    throw new Error(`Markdown 请求失败：${response.status}`);
  }
  const markdown = await response.text();
  if (!isUsableMarkdown(markdown)) {
    throw new Error("Markdown 内容为空或格式异常");
  }
  saveCachedMarkdown(config, markdown);
  return markdown;
}

function AuthView(): ReactElement {
  return <CommunityView />;
}

function LogPanel(props: { logs: Array<{ id: string; level: LogLevel; message: string; createdAt: string }> }): ReactElement {
  const panelRef = useRef<HTMLElement | null>(null);
  const newestLogId = props.logs.at(-1)?.id ?? "";
  useEffect(() => {
    if (panelRef.current) {
      panelRef.current.scrollTop = 0;
    }
  }, [newestLogId]);
  const logs = [...props.logs].reverse();
  return (
    <aside ref={panelRef} className="log-panel" aria-label="实时日志">
      <h2>实时日志</h2>
      {logs.map((log) => (
        <p key={log.id} className={`log-line ${log.level}`}>
          <time>{new Date(log.createdAt).toLocaleTimeString()}</time>
          <span data-testid="log-message">{log.message}</span>
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

function fallbackDoctorChecks(doctor?: DoctorSnapshot): NonNullable<DoctorSnapshot["checks"]> {
  if (!doctor) {
    return [{ name: "doctor", label: "诊断", status: "unknown", message: "未检查" }];
  }
  return [
    { name: "python", label: "Python", status: doctor.python === "未检查" ? "unknown" : "ok", message: doctor.python },
    { name: "browser", label: "浏览器", status: doctor.browser === "missing" ? "warning" : "ok", message: doctor.browser },
    { name: "network", label: "网络", status: doctor.network === "available" ? "ok" : "unknown", message: doctor.network },
    {
      name: "output_dir",
      label: "输出目录可写",
      status: doctor.writableOutput ? "ok" : "error",
      message: doctor.writableOutput ? "是" : "否"
    }
  ];
}

function diagnosticStatusLabel(status: NonNullable<DoctorSnapshot["checks"]>[number]["status"]): string {
  const labels: Record<NonNullable<DoctorSnapshot["checks"]>[number]["status"], string> = {
    ok: "正常",
    warning: "需检查",
    error: "异常",
    unknown: "未知"
  };
  return labels[status] ?? "未知";
}

type PlatformSettingGroup = {
  id: string;
  label: string;
  fields: SettingsFieldSchema[];
};

function orderSettingsPlatforms(platforms: SettingsSchema["platforms"]): SettingsSchema["platforms"] {
  const ordered = [...platforms];
  const redditIndex = ordered.findIndex((platform) => platform.id === "reddit");
  const zsxqIndex = ordered.findIndex((platform) => platform.id === "zsxq");
  if (redditIndex < 0 || zsxqIndex < 0 || redditIndex === zsxqIndex + 1) {
    return ordered;
  }
  const [reddit] = ordered.splice(redditIndex, 1);
  const nextZsxqIndex = ordered.findIndex((platform) => platform.id === "zsxq");
  ordered.splice(nextZsxqIndex + 1, 0, reddit);
  return ordered;
}

const platformFieldOrder: Record<string, Record<string, number>> = {
  discourse: {
    LINUXDO_REPLY_MODE: 10,
    LINUXDO_PAGE_LOAD_TIMEOUT: 20,
    LINUXDO_CDP_ENABLED: 30,
    IDCFLARE_REPLY_MODE: 40,
    IDCFLARE_PAGE_LOAD_TIMEOUT: 50,
    IDCFLARE_CDP_ENABLED: 60
  },
  media_ai: {
    GEMINI_API_KEY: 10,
    GROQ_API_KEY: 110,
    GROQ_WHISPER_MODEL: 120,
    TG_API_ID: 310,
    TG_API_HASH: 320
  },
  feishu: {
    FEISHU_APP_ID: 10,
    FEISHU_APP_SECRET: 20,
    FEISHU_CDP_ENABLED: 30,
    FEISHU_WIKI_DELAY: 40,
    FEISHU_DOWNLOAD_IMAGES: 50,
    FEISHU_PAGE_LOAD_TIMEOUT: 60,
    FEISHU_CUSTOM_DOMAINS: 70,
    KDOCS_CDP_ENABLED: 110,
    KDOCS_PAGE_LOAD_TIMEOUT: 120,
    KDOCS_DOWNLOAD_IMAGES: 130,
    FLOWUS_CDP_ENABLED: 210,
    FLOWUS_PAGE_LOAD_TIMEOUT: 220,
    FLOWUS_DOWNLOAD_IMAGES: 230,
    YOUDAO_DOWNLOAD_IMAGES: 310,
    GITHUB_TOKEN: 410
  },
  video_podcast: {
    YOUTUBE_API_KEY: 10,
    YOUTUBE_REGION: 20,
    YOUTUBE_LANG: 30,
    YOUTUBE_MAX_RESULTS: 40,
    YOUTUBE_DOWNLOAD_QUALITY: 50,
    YOUTUBE_WHISPER_LANG: 60,
    BILIBILI_SUBTITLE_ENABLED: 110,
    BILIBILI_SUBTITLE_LANG: 120,
    BILIBILI_SUBTITLE_WHISPER: 130,
    XIAOYUZHOU_ENABLED: 210,
    XIAOYUZHOU_WHISPER: 220,
    XIMALAYA_ENABLED: 310,
    XIMALAYA_WHISPER: 320
  },
  zhihu: {
    ZHIHU_CDP_ENABLED: 10,
    ZHIHU_PAGE_LOAD_TIMEOUT: 20,
    ZHIHU_DOWNLOAD_IMAGES: 30,
    ZHIHU_SEARCH_DAYS: 110,
    ZHIHU_SEARCH_LIMIT: 120,
    ZHIHU_SEARCH_SAVE_ANSWERS: 130,
    ZHIHU_SEARCH_DELAY: 140
  },
  reddit: {
    REDDIT_ENABLED: 10,
    REDDIT_CDP_ENABLED: 20,
    REDDIT_PAGE_LOAD_TIMEOUT: 30,
    REDDIT_MAX_COMMENTS: 40,
    REDDIT_FETCH_ALL_COMMENTS: 50,
    REDDIT_USER_AGENT: 60,
    REDDIT_SUB_LIMIT: 70,
    REDDIT_SUB_DELAY: 80,
    REDDIT_SEARCH_SORT: 110,
    REDDIT_SEARCH_TIME_RANGE: 120,
    REDDIT_SEARCH_LIMIT: 130,
    REDDIT_SEARCH_SAVE_POSTS: 140,
    REDDIT_SEARCH_SUBREDDIT: 150
  }
};

const settingTypeOrder: Record<SettingsFieldSchema["type"], number> = {
  select: 0,
  number: 1,
  string: 1,
  path: 1,
  secret: 1,
  boolean: 2
};

function groupPlatformSettings(platform: SettingsSchema["platforms"][number]): PlatformSettingGroup[] {
  const groups = new Map<string, PlatformSettingGroup>();
  for (const field of sortPlatformSettingFields(platform.id, platform.fields)) {
    const label = platformSettingSectionLabel(platform.id, field.name);
    const id = `${platform.id}-${label}`;
    const group = groups.get(id);
    if (group) {
      group.fields.push(field);
    } else {
      groups.set(id, { id, label, fields: [field] });
    }
  }
  return Array.from(groups.values()).map((group) => ({
    ...group,
    fields: sortPlatformSettingFields(platform.id, group.fields)
  }));
}

function sortPlatformSettingFields(platformId: string, fields: SettingsFieldSchema[]): SettingsFieldSchema[] {
  const explicitOrder = platformFieldOrder[platformId] ?? {};
  return fields
    .map((field, index) => ({ field, index }))
    .sort((left, right) => {
      const leftOrder = explicitOrder[left.field.name];
      const rightOrder = explicitOrder[right.field.name];
      if (typeof leftOrder === "number" || typeof rightOrder === "number") {
        const normalizedLeftOrder = leftOrder ?? 10_000 + left.index;
        const normalizedRightOrder = rightOrder ?? 10_000 + right.index;
        return normalizedLeftOrder - normalizedRightOrder;
      }
      const typeDelta = settingTypeOrder[left.field.type] - settingTypeOrder[right.field.type];
      return typeDelta === 0 ? left.index - right.index : typeDelta;
    })
    .map((item) => item.field);
}

function platformSettingSectionLabel(platformId: string, fieldName: string): string {
  if (platformId === "x") {
    if (fieldName === "TWITTERAPI_IO_KEY" || fieldName.startsWith("X_API_")) {
      return "付费 API";
    }
    if (fieldName.startsWith("X_SEARCH_")) {
      return "关键词搜索";
    }
    if (fieldName.startsWith("X_LIST_")) {
      return "List 批量采集";
    }
    if (fieldName.startsWith("X_USER_TWEET")) {
      return "账号推文批量";
    }
    if (fieldName.startsWith("X_BOOKMARK")) {
      return "书签批量";
    }
    return "单篇/线程采集";
  }
  if (platformId === "xhs") {
    if (fieldName.startsWith("XHS_USER_")) {
      return "作者批量";
    }
    if (fieldName.startsWith("XHS_SEARCH_")) {
      return "搜索批量";
    }
    return "单篇/API 抓取";
  }
  if (platformId === "wechat") {
    if (fieldName.startsWith("MPWEIXIN_SOGOU_")) {
      return "搜狗搜索";
    }
    if (fieldName.startsWith("MPWEIXIN_ID_")) {
      return "账号批量";
    }
    if (fieldName.startsWith("MPWEIXIN_ZHUANJI_")) {
      return "专辑批量";
    }
    if (fieldName.includes("COMMENT")) {
      return "评论抓取";
    }
    return "单篇抓取";
  }
  if (platformId === "discourse") {
    return fieldName.startsWith("IDCFLARE_") ? "IDCFlare" : "LinuxDo";
  }
  if (platformId === "reddit") {
    return fieldName.startsWith("REDDIT_SEARCH_") ? "帖子搜索" : "单贴/评论抓取";
  }
  if (platformId === "feishu") {
    if (fieldName.startsWith("KDOCS_")) {
      return "金山文档";
    }
    if (fieldName.startsWith("FLOWUS_")) {
      return "FlowUs";
    }
    if (fieldName.startsWith("YOUDAO_")) {
      return "有道云笔记";
    }
    if (fieldName.startsWith("GITHUB_")) {
      return "GitHub";
    }
    return "飞书";
  }
  if (platformId === "video_podcast") {
    if (fieldName.startsWith("YOUTUBE_")) {
      return "YouTube";
    }
    if (fieldName.startsWith("BILIBILI_")) {
      return "B 站";
    }
    if (fieldName.startsWith("XIAOYUZHOU_")) {
      return "小宇宙";
    }
    if (fieldName.startsWith("XIMALAYA_")) {
      return "喜马拉雅";
    }
    return "通用设置";
  }
  if (platformId === "zhihu") {
    if (fieldName.startsWith("ZHIHU_SEARCH_")) {
      return "搜索批量";
    }
    return "单篇抓取";
  }
  if (platformId === "media_ai") {
    if (fieldName.startsWith("TG_")) {
      return "Telegram";
    }
    if (fieldName.startsWith("GEMINI_")) {
      return "Gemini";
    }
    if (fieldName.startsWith("GROQ_")) {
      return "Groq 转录";
    }
    return "AI";
  }
  return "通用设置";
}

function normalizePortValue(value: SettingsFieldValue | undefined): number {
  const port = typeof value === "number" ? value : Number(value ?? 9222);
  if (Number.isFinite(port) && port >= 1 && port <= 65535) {
    return Math.floor(port);
  }
  return 9222;
}

function SchemaSettingField(props: {
  field: SettingsFieldSchema;
  pendingSettings: Record<string, SettingsFieldValue>;
  updateSetting: (name: string, value: SettingsFieldValue) => void;
  chooseDirectoryForSetting?: (name: string) => void;
  compact?: boolean;
}): ReactElement {
  const field = props.field;
  const inputId = `setting-${field.name}`;
  const value = props.pendingSettings[field.name] ?? field.value ?? field.defaultValue ?? defaultFieldValue(field);
  const labelText = settingFieldLabel(field);
  const labelHint = settingFieldLabelHint(field);
  const labelHintId = labelHint ? `${inputId}-hint` : undefined;
  const controlDescription = labelHint ? "" : field.description;
  const className = props.compact
    ? `setting-row schema-setting-row compact-setting-row schema-field-${field.type}`
    : `setting-row schema-setting-row schema-field-${field.type}`;

  return (
    <div className={className}>
      <div className={labelHint ? "setting-label has-hint" : "setting-label"}>
        <label htmlFor={inputId}>{labelText}</label>
        {labelHintId ? (
          <small id={labelHintId} className="setting-label-hint">
            {labelHint}
          </small>
        ) : null}
      </div>
      <div className="setting-control">
        {field.type === "boolean" ? (
          <input
            id={inputId}
            type="checkbox"
            aria-describedby={labelHintId}
            checked={settingBooleanValue(value)}
            onChange={(event) => props.updateSetting(field.name, event.currentTarget.checked)}
          />
        ) : null}
        {field.type === "select" ? (
          <select
            id={inputId}
            aria-describedby={labelHintId}
            value={String(value)}
            onChange={(event) =>
              props.updateSetting(field.name, settingOptionValue(field, event.currentTarget.value))
            }
          >
            {(field.options ?? []).map((option) => (
              <option key={String(option.value)} value={String(option.value)}>
                {option.label}
              </option>
            ))}
          </select>
        ) : null}
        {field.type === "number" ? (
          <input
            id={inputId}
            type="number"
            aria-describedby={labelHintId}
            value={String(value)}
            onChange={(event) => props.updateSetting(field.name, Number(event.currentTarget.value))}
          />
        ) : null}
        {field.type === "secret" ? (
          <input
            id={inputId}
            type="password"
            aria-describedby={labelHintId}
            value={String(value)}
            autoComplete="off"
            onChange={(event) => props.updateSetting(field.name, event.currentTarget.value)}
          />
        ) : null}
        {field.type === "path" && props.chooseDirectoryForSetting ? (
          <div className="setting-path-picker">
            <input
              id={inputId}
              type="text"
              aria-describedby={labelHintId}
              value={String(value)}
              placeholder={field.placeholder}
              onChange={(event) => props.updateSetting(field.name, event.currentTarget.value)}
            />
            <button type="button" onClick={() => props.chooseDirectoryForSetting?.(field.name)}>
              选择
            </button>
          </div>
        ) : null}
        {field.type === "string" || (field.type === "path" && !props.chooseDirectoryForSetting) ? (
          <input
            id={inputId}
            type="text"
            aria-describedby={labelHintId}
            value={String(value)}
            placeholder={field.placeholder}
            onChange={(event) => props.updateSetting(field.name, event.currentTarget.value)}
          />
        ) : null}
        {controlDescription ? <small>{controlDescription}</small> : null}
      </div>
    </div>
  );
}

function settingFieldLabel(field: SettingsFieldSchema): string {
  const label = field.label || field.name;
  if (field.name === "OBSIDIAN_VAULT") {
    return label.replace(/（高优先级）|\(高优先级\)/g, "").trim() || "Obsidian Vault";
  }
  return label;
}

function settingFieldLabelHint(field: SettingsFieldSchema): string {
  if (field.name === "OBSIDIAN_VAULT") {
    return field.description || "高优先级";
  }
  return "";
}

function defaultFieldValue(field: SettingsFieldSchema): SettingsFieldValue {
  if (field.type === "boolean") {
    return false;
  }
  if (field.type === "number") {
    return 0;
  }
  return "";
}

function settingBooleanValue(value: SettingsFieldValue | undefined): boolean {
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
  return false;
}

function settingOptionValue(field: SettingsFieldSchema, raw: string): SettingsFieldValue {
  const option = field.options?.find((item) => String(item.value) === raw);
  return option?.value ?? raw;
}

function browserPreviewUserAgent(): string {
  return typeof navigator !== "undefined" ? navigator.userAgent : "";
}

function settingsSchemaFromSnapshot(settings: SettingsSnapshot | undefined): SettingsSchema {
  const resolvedOutputDirectory =
    settings?.effectiveOutputDirectory || settings?.outputDirectory || loadSavedOutputDirectory() || "";
  const rawOutputDirectory = settings?.outputDirectory || "";
  const rawObsidianVault = settings?.obsidianVault || "";
  const resolvedDataDirectory = deriveSiblingDirectory(resolvedOutputDirectory, "output", "sessions");
  return {
    basic: [
      { name: "OUTPUT_DIR", label: "输出目录", type: "path", value: rawOutputDirectory },
      {
        name: "OBSIDIAN_VAULT",
        label: "Obsidian Vault",
        type: "path",
        value: rawObsidianVault,
        description: "高优先级"
      },
      { name: "CONCURRENCY", label: "并发上限", type: "number", value: settings?.concurrency ?? 1 },
      { name: "DOWNLOAD_IMAGES", label: "下载图片", type: "boolean", value: settings?.downloadImages ?? false },
      { name: "LOCALIZE_MEDIA", label: "媒体本地化", type: "boolean", value: settings?.localizeMedia ?? false },
      { name: "FEEDGRAB_DATA_DIR", label: "登录态和数据目录", type: "path", value: resolvedDataDirectory },
      { name: "BROWSER_USER_AGENT", label: "浏览器 User-Agent", type: "string", value: browserPreviewUserAgent() },
      { name: "CHROME_CDP_LOGIN", label: "登录时优先从 Chrome CDP 提取登录态", type: "boolean", value: false },
      { name: "CHROME_CDP_PORT", label: "Chrome CDP 端口", type: "number", value: 9222 },
      { name: "FORCE_REFETCH", label: "强制重新抓取", type: "boolean", value: false },
      { name: "FEEDGRAB_PROXY_ENABLED", label: "启用代理", type: "boolean", value: false },
      {
        name: "FEEDGRAB_PROXY_URL",
        label: "代理地址",
        type: "string",
        value: "",
        placeholder: "http://127.0.0.1:7890 或 socks5://127.0.0.1:7890",
        description: "支持 HTTP / SOCKS5 代理；日志和界面会隐藏密码。"
      },
      {
        name: "FEEDGRAB_NO_PROXY",
        label: "不走代理地址",
        type: "string",
        value: "127.0.0.1,localhost",
        description: "用英文逗号分隔，避免本地 worker、CDP 和内部服务被代理干扰。"
      }
    ],
    platforms: [
      {
        id: "x",
        label: "X / Twitter",
        fields: [
          { name: "X_GRAPHQL_ENABLED", label: "启用 GraphQL 深度抓取", type: "boolean", value: true },
          { name: "X_THREAD_MAX_PAGES", label: "线程最大分页数", type: "number", value: 20 },
          { name: "X_REQUEST_DELAY", label: "GraphQL 请求间隔秒数", type: "number", value: 1.5 },
          { name: "X_FETCH_AUTHOR_REPLIES", label: "抓取作者回帖", type: "boolean", value: false },
          { name: "X_FETCH_ALL_COMMENTS", label: "抓取全部评论", type: "boolean", value: false },
          { name: "X_BOOKMARKS_ENABLED", label: "启用书签批量抓取", type: "boolean", value: false },
          { name: "X_BOOKMARK_MAX_PAGES", label: "书签最大分页数", type: "number", value: 50 },
          { name: "X_USER_TWEETS_ENABLED", label: "启用账号推文批量抓取", type: "boolean", value: false },
          { name: "X_USER_TWEET_MAX_PAGES", label: "账号推文最大分页数", type: "number", value: 200 },
          { name: "X_SEARCH_ENABLED", label: "启用关键词搜索", type: "boolean", value: true },
          {
            name: "X_SEARCH_LANG",
            label: "搜索语言",
            type: "select",
            value: "zh",
            options: [
              { label: "不限", value: "" },
              { label: "中文 zh", value: "zh" },
              { label: "英文 en", value: "en" },
              { label: "日文 ja", value: "ja" }
            ]
          },
          { name: "X_SEARCH_DAYS", label: "搜索最近天数", type: "number", value: 1 },
          {
            name: "X_SEARCH_SORT",
            label: "搜索排序",
            type: "select",
            value: "live",
            options: [
              { label: "最新 live", value: "live" },
              { label: "热门 top", value: "top" }
            ]
          },
          {
            name: "X_API_PROVIDER",
            label: "API 提供方",
            type: "select",
            value: "graphql",
            options: [
              { label: "GraphQL 默认流程", value: "graphql" },
              { label: "TwitterAPI.io 付费 API", value: "api" }
            ]
          },
          { name: "TWITTERAPI_IO_KEY", label: "TwitterAPI.io Key", type: "secret", value: "[redacted]", secret: true }
        ]
      },
      {
        id: "xhs",
        label: "小红书",
        fields: [
          { name: "XHS_API_ENABLED", label: "启用 API 优先模式", type: "boolean", value: true },
          { name: "XHS_PINIA_ENABLED", label: "启用 Pinia Store 兜底", type: "boolean", value: true },
          { name: "XHS_API_DELAY", label: "API 请求间隔秒数", type: "number", value: 1 },
          { name: "XHS_FETCH_COMMENTS", label: "抓取单篇评论", type: "boolean", value: false },
          { name: "XHS_MAX_COMMENTS", label: "评论最大页数", type: "number", value: 5 },
          {
            name: "XHS_SEARCH_SORT",
            label: "搜索排序",
            type: "select",
            value: "general",
            options: [
              { label: "综合 general", value: "general" },
              { label: "热门 popular", value: "popular" },
              { label: "最新 latest", value: "latest" }
            ]
          },
          {
            name: "XHS_SEARCH_NOTE_TYPE",
            label: "搜索内容类型",
            type: "select",
            value: "all",
            options: [
              { label: "全部 all", value: "all" },
              { label: "视频 video", value: "video" },
              { label: "图文 image", value: "image" }
            ]
          },
          { name: "XHS_SEARCH_MAX_PAGES", label: "搜索最大页数", type: "number", value: 10 },
          { name: "XHS_USER_NOTES_ENABLED", label: "启用作者笔记批量抓取", type: "boolean", value: false }
        ]
      },
      {
        id: "wechat",
        label: "微信公众号",
        fields: [
          { name: "MPWEIXIN_SOGOU_ENABLED", label: "启用搜狗微信搜索", type: "boolean", value: false },
          { name: "MPWEIXIN_SOGOU_MAX_RESULTS", label: "搜狗搜索最大文章数", type: "number", value: 10 },
          { name: "MPWEIXIN_SOGOU_DELAY", label: "搜狗文章处理间隔秒数", type: "number", value: 3 },
          { name: "MPWEIXIN_ID_SINCE", label: "账号文章起始日期", type: "string", value: "", placeholder: "YYYY-MM-DD" },
          { name: "MPWEIXIN_ID_DELAY", label: "账号文章处理间隔秒数", type: "number", value: 3 },
          { name: "MPWEIXIN_FETCH_COMMENTS", label: "抓取精选评论", type: "boolean", value: false },
          { name: "MPWEIXIN_MAX_COMMENTS", label: "最大评论数", type: "number", value: 100 }
        ]
      },
      {
        id: "discourse",
        label: "Discourse论坛",
        fields: [
          {
            name: "LINUXDO_REPLY_MODE",
            label: "LinuxDo 回复模式",
            type: "select",
            value: settings?.replyMode ?? "author",
            options: [
              { label: "主贴 + 楼主自回 author", value: "author" },
              { label: "完整楼层 all", value: "all" },
              { label: "仅主贴 none", value: "none" }
            ]
          },
          { name: "LINUXDO_PAGE_LOAD_TIMEOUT", label: "LinuxDo 页面等待毫秒", type: "number", value: 15000 },
          { name: "LINUXDO_CDP_ENABLED", label: "LinuxDo 复用 Chrome CDP", type: "boolean", value: true },
          {
            name: "IDCFLARE_REPLY_MODE",
            label: "IDCFlare 回复模式",
            type: "select",
            value: settings?.replyMode ?? "author",
            options: [
              { label: "主贴 + 楼主自回 author", value: "author" },
              { label: "完整楼层 all", value: "all" },
              { label: "仅主贴 none", value: "none" }
            ]
          },
          { name: "IDCFLARE_PAGE_LOAD_TIMEOUT", label: "IDCFlare 页面等待毫秒", type: "number", value: 15000 },
          { name: "IDCFLARE_CDP_ENABLED", label: "IDCFlare 复用 Chrome CDP", type: "boolean", value: true }
        ]
      },
      {
        id: "reddit",
        label: "Reddit",
        fields: [
          { name: "REDDIT_ENABLED", label: "启用 Reddit 抓取", type: "boolean", value: true },
          { name: "REDDIT_CDP_ENABLED", label: "Reddit 复用 Chrome CDP", type: "boolean", value: true },
          { name: "REDDIT_PAGE_LOAD_TIMEOUT", label: "Reddit 页面等待毫秒", type: "number", value: 15000 },
          { name: "REDDIT_MAX_COMMENTS", label: "评论最大条数", type: "number", value: 50 },
          { name: "REDDIT_FETCH_ALL_COMMENTS", label: "抓取全部评论", type: "boolean", value: false },
          { name: "REDDIT_USER_AGENT", label: "Reddit User-Agent", type: "string", value: "" },
          { name: "REDDIT_SUB_LIMIT", label: "子版块抓取条数", type: "number", value: 25 },
          { name: "REDDIT_SUB_DELAY", label: "子版块帖子间隔秒数", type: "number", value: 2 },
          { name: "REDDIT_SEARCH_ENABLED", label: "启用 Reddit 帖子搜索", type: "boolean", value: true },
          {
            name: "REDDIT_SEARCH_SORT",
            label: "帖子搜索排序",
            type: "select",
            value: "relevance",
            options: [
              { label: "相关性 relevance", value: "relevance" },
              { label: "热门 hot", value: "hot" },
              { label: "最受欢迎 top", value: "top" },
              { label: "新 new", value: "new" },
              { label: "评论计数 comments", value: "comments" }
            ]
          },
          {
            name: "REDDIT_SEARCH_TIME_RANGE",
            label: "帖子搜索时间范围",
            type: "select",
            value: "all",
            options: [
              { label: "所有时间 all", value: "all" },
              { label: "去年 year", value: "year" },
              { label: "上个月 month", value: "month" },
              { label: "上周 week", value: "week" },
              { label: "今天 day", value: "day" },
              { label: "过去 1 小时 hour", value: "hour" }
            ]
          },
          { name: "REDDIT_SEARCH_LIMIT", label: "帖子搜索结果数", type: "number", value: 10 },
          { name: "REDDIT_SEARCH_SAVE_POSTS", label: "搜索后深抓单贴", type: "boolean", value: false },
          {
            name: "REDDIT_SEARCH_SUBREDDIT",
            label: "限定子版块",
            type: "string",
            value: "",
            placeholder: "ChatGPT（留空表示全站）"
          }
        ]
      },
      {
        id: "feishu",
        label: "文档平台",
        fields: [
          { name: "FEISHU_APP_ID", label: "飞书 App ID", type: "string", value: "" },
          { name: "FEISHU_APP_SECRET", label: "飞书 App Secret", type: "secret", value: "[redacted]", secret: true },
          { name: "FEISHU_CDP_ENABLED", label: "飞书复用 Chrome CDP", type: "boolean", value: false },
          { name: "FEISHU_WIKI_DELAY", label: "飞书知识库批量间隔秒数", type: "number", value: 2 },
          { name: "FEISHU_DOWNLOAD_IMAGES", label: "飞书图片下载到本地", type: "boolean", value: false },
          { name: "FEISHU_PAGE_LOAD_TIMEOUT", label: "飞书页面等待毫秒", type: "number", value: 5000 },
          { name: "FEISHU_CUSTOM_DOMAINS", label: "飞书私有化域名", type: "string", value: "" },
          { name: "KDOCS_CDP_ENABLED", label: "金山文档复用 Chrome CDP", type: "boolean", value: true },
          { name: "KDOCS_PAGE_LOAD_TIMEOUT", label: "金山文档页面等待毫秒", type: "number", value: 10000 },
          { name: "KDOCS_DOWNLOAD_IMAGES", label: "金山文档图片下载到本地", type: "boolean", value: false },
          { name: "FLOWUS_CDP_ENABLED", label: "FlowUs 复用 Chrome CDP", type: "boolean", value: true },
          { name: "FLOWUS_PAGE_LOAD_TIMEOUT", label: "FlowUs 页面等待毫秒", type: "number", value: 10000 },
          { name: "FLOWUS_DOWNLOAD_IMAGES", label: "FlowUs 图片下载到本地", type: "boolean", value: false },
          { name: "YOUDAO_DOWNLOAD_IMAGES", label: "有道云图片下载到本地", type: "boolean", value: false },
          { name: "GITHUB_TOKEN", label: "GitHub Token", type: "secret", value: "[redacted]", secret: true }
        ]
      },
      {
        id: "video_podcast",
        label: "视频播客",
        fields: [
          { name: "YOUTUBE_API_KEY", label: "YouTube Data API Key", type: "secret", value: "[redacted]", secret: true },
          { name: "YOUTUBE_REGION", label: "YouTube 搜索地区", type: "string", value: "US" },
          {
            name: "YOUTUBE_LANG",
            label: "YouTube 搜索语言",
            type: "select",
            value: "zh-CN",
            options: [
              { label: "中文 zh-CN", value: "zh-CN" },
              { label: "英文 en", value: "en" },
              { label: "日文 ja", value: "ja" }
            ]
          },
          { name: "YOUTUBE_MAX_RESULTS", label: "YouTube 搜索结果数", type: "number", value: 10 },
          {
            name: "YOUTUBE_DOWNLOAD_QUALITY",
            label: "YouTube 下载清晰度",
            type: "select",
            value: "1080p",
            options: [
              { label: "best", value: "best" },
              { label: "1080p", value: "1080p" },
              { label: "720p", value: "720p" },
              { label: "480p", value: "480p" }
            ]
          },
          { name: "YOUTUBE_WHISPER_LANG", label: "YouTube Whisper 语言", type: "string", value: "zh" },
          { name: "BILIBILI_SUBTITLE_ENABLED", label: "Bilibili 抓取字幕", type: "boolean", value: true },
          { name: "BILIBILI_SUBTITLE_LANG", label: "Bilibili 字幕语言", type: "string", value: "zh-CN" },
          { name: "BILIBILI_SUBTITLE_WHISPER", label: "Bilibili 无字幕时 Whisper", type: "boolean", value: false },
          { name: "XIAOYUZHOU_ENABLED", label: "小宇宙抓取启用", type: "boolean", value: true },
          { name: "XIAOYUZHOU_WHISPER", label: "小宇宙 Whisper 转录", type: "boolean", value: true },
          { name: "XIMALAYA_ENABLED", label: "喜马拉雅抓取启用", type: "boolean", value: true },
          { name: "XIMALAYA_WHISPER", label: "喜马拉雅 Whisper 转录", type: "boolean", value: true }
        ]
      },
      {
        id: "zhihu",
        label: "知乎",
        fields: [
          { name: "ZHIHU_CDP_ENABLED", label: "知乎复用 Chrome CDP", type: "boolean", value: true },
          { name: "ZHIHU_PAGE_LOAD_TIMEOUT", label: "知乎页面等待毫秒", type: "number", value: 10000 },
          { name: "ZHIHU_DOWNLOAD_IMAGES", label: "知乎图片下载到本地", type: "boolean", value: false },
          { name: "ZHIHU_SEARCH_DAYS", label: "知乎搜索最近天数", type: "number", value: 30 },
          { name: "ZHIHU_SEARCH_LIMIT", label: "知乎搜索最大结果数", type: "number", value: 50 },
          { name: "ZHIHU_SEARCH_SAVE_ANSWERS", label: "知乎搜索保存答案", type: "boolean", value: false },
          { name: "ZHIHU_SEARCH_DELAY", label: "知乎搜索请求间隔秒数", type: "number", value: 2 }
        ]
      },
      {
        id: "telegram",
        label: "Telegram",
        fields: [
          { name: "TG_API_ID", label: "Telegram API ID", type: "string", value: "" },
          { name: "TG_API_HASH", label: "Telegram API Hash", type: "secret", value: "[redacted]", secret: true }
        ]
      },
      { id: "rss", label: "RSS", fields: [] },
      { id: "web", label: "任意网页", fields: [] },
      {
        id: "zsxq",
        label: "知识星球",
        fields: []
      },
      {
        id: "media_ai",
        label: "媒体 / API",
        fields: [
          { name: "GROQ_API_KEY", label: "Groq API Key", type: "secret", value: "[redacted]", secret: true },
          { name: "GEMINI_API_KEY", label: "Gemini API Key", type: "secret", value: "[redacted]", secret: true },
          { name: "GROQ_WHISPER_MODEL", label: "Groq Whisper 模型", type: "string", value: "whisper-large-v3" },
          { name: "TG_API_ID", label: "Telegram API ID", type: "string", value: "" },
          { name: "TG_API_HASH", label: "Telegram API Hash", type: "secret", value: "[redacted]", secret: true }
        ]
      }
    ]
  };
}

function deriveSiblingDirectory(sourceDirectory: string, sourceName: string, siblingName: string): string {
  const trimmed = sourceDirectory.trim();
  if (!trimmed) {
    return "";
  }
  const normalized = trimmed.replace(/\//g, "\\").replace(/\\+$/g, "");
  const segments = normalized.split("\\");
  if ((segments.at(-1) ?? "").toLowerCase() !== sourceName.toLowerCase()) {
    return "";
  }
  segments[segments.length - 1] = siblingName;
  return segments.join("\\");
}

function loginStatusDetail(status: LoginStatus): string {
  if (typeof status.accountCount === "number") {
    const validCount = status.validCount ?? 0;
    const expiredCount = status.expiredCount ?? Math.max(status.accountCount - validCount, 0);
    return `${status.accountCount} 个账号，本地有效 ${validCount} 个，过期/异常 ${expiredCount} 个`;
  }
  if (status.message) {
    return status.message;
  }
  if (typeof status.cookieCount === "number") {
    return `${status.cookieCount} 个 Cookie`;
  }
  if (status.status === "notRequired") {
    return "该平台可直接抓取，登录态可选。";
  }
  return status.sessionPath ? `登录态文件：${status.sessionPath}` : "等待检测";
}

function mergeLoginStatuses(current: LoginStatus[], updates: LoginStatus[]): LoginStatus[] {
  const byPlatform = new Map(current.map((item) => [item.platform, item]));
  for (const item of updates) {
    byPlatform.set(item.platform, item);
  }
  return [...byPlatform.values()];
}

const redditSearchSorts = new Set(["relevance", "hot", "top", "new", "comments"]);
const redditSearchTimeRanges = new Set(["all", "year", "month", "week", "day", "hour"]);
const redditSearchSortsWithTime = new Set(["relevance", "top", "comments"]);

function buildFetchPlan(
  text: string,
  selectedPlatformKey: SelectedFetchPlatform,
  outputDirectory: string,
  settingsSchema?: SettingsSchema,
  pendingSettings: Record<string, SettingsFieldValue> = {}
): FetchPlan {
  const lines = parseTargetLines(text);
  const urls = lines.filter(isHttpUrl);
  const emptyRequest: FetchRequest = { urls: [], outputDirectory };
  if (lines.length === 0) {
    return { urls: [], targets: [], valid: false, request: emptyRequest, error: "请输入至少一个抓取目标" };
  }
  if (urls.length > 0) {
    if (urls.length !== lines.length) {
      return {
        urls,
        targets: [],
        valid: false,
        request: emptyRequest,
        error: "URL 和关键词请分开提交"
      };
    }
    return {
      urls,
      targets: [],
      valid: true,
      request: { urls, outputDirectory }
    };
  }

  const option = fetchPlatformOptions.find((item) => item.key === selectedPlatformKey);
  if (!option || option.id === "auto") {
    return {
      urls: [],
      targets: lines,
      valid: false,
      request: emptyRequest,
      error: "请输入 URL，或先选择一个支持关键词/账号抓取的平台"
    };
  }
  if (!option.command || !option.mode) {
    return {
      urls: [],
      targets: lines,
      valid: false,
      request: emptyRequest,
      error: `${option.label} 暂不支持直接输入关键词或账号`
    };
  }
  const structuredArgs = buildStructuredFetchArgs(option, lines, settingsSchema, pendingSettings);
  const commandPreview = `feedgrab ${option.command} ${structuredArgs.commandArgs.join(" ")}`;
  const request: FetchRequest = {
    urls: [],
    targets: lines,
    platform: option.id,
    mode: option.mode,
    commandPreview,
    outputDirectory
  };
  if (structuredArgs.options) {
    request.options = structuredArgs.options;
  }
  return {
    urls: [],
    targets: lines,
    valid: true,
    commandPreview,
    request
  };
}

function buildStructuredFetchArgs(
  option: FetchPlatformOption,
  lines: string[],
  settingsSchema: SettingsSchema | undefined,
  pendingSettings: Record<string, SettingsFieldValue>
): { commandArgs: string[]; options?: Record<string, SettingsFieldValue> } {
  if (option.id !== "reddit" || option.command !== "reddit-so") {
    return { commandArgs: lines.map(quoteCliArg) };
  }
  const options = buildRedditSearchOptions(settingsSchema, pendingSettings);
  const commandArgs = lines.map(quoteCliArg);
  commandArgs.push("--sort", quoteCliArg(String(options.sort)));
  if (typeof options.time === "string") {
    commandArgs.push("--time", quoteCliArg(options.time));
  }
  commandArgs.push("--limit", quoteCliArg(String(options.limit)));
  if (typeof options.subreddit === "string" && options.subreddit.trim().length > 0) {
    commandArgs.push("--subreddit", quoteCliArg(options.subreddit));
  }
  if (options.savePosts === true) {
    commandArgs.push("--save-posts");
  }
  return { commandArgs, options };
}

function buildRedditSearchOptions(
  settingsSchema: SettingsSchema | undefined,
  pendingSettings: Record<string, SettingsFieldValue>
): Record<string, SettingsFieldValue> {
  const sort = normalizeStringChoice(
    settingString("REDDIT_SEARCH_SORT", settingsSchema, pendingSettings, "relevance"),
    redditSearchSorts,
    "relevance"
  );
  const time = normalizeStringChoice(
    settingString("REDDIT_SEARCH_TIME_RANGE", settingsSchema, pendingSettings, "all"),
    redditSearchTimeRanges,
    "all"
  );
  const limit = normalizePositiveInteger(settingValue("REDDIT_SEARCH_LIMIT", settingsSchema, pendingSettings), 10);
  const subreddit = settingString("REDDIT_SEARCH_SUBREDDIT", settingsSchema, pendingSettings, "").trim();
  const savePosts = settingBooleanValue(settingValue("REDDIT_SEARCH_SAVE_POSTS", settingsSchema, pendingSettings));
  const options: Record<string, SettingsFieldValue> = { sort, limit };
  if (redditSearchSortsWithTime.has(sort)) {
    options.time = time;
  }
  if (subreddit.length > 0) {
    options.subreddit = subreddit;
  }
  if (savePosts) {
    options.savePosts = true;
  }
  return options;
}

function settingValue(
  name: string,
  settingsSchema: SettingsSchema | undefined,
  pendingSettings: Record<string, SettingsFieldValue>
): SettingsFieldValue | undefined {
  if (Object.prototype.hasOwnProperty.call(pendingSettings, name)) {
    return pendingSettings[name];
  }
  for (const platform of settingsSchema?.platforms ?? []) {
    const field = platform.fields.find((item) => item.name === name);
    if (field) {
      return field.value ?? field.defaultValue;
    }
  }
  return undefined;
}

function settingString(
  name: string,
  settingsSchema: SettingsSchema | undefined,
  pendingSettings: Record<string, SettingsFieldValue>,
  fallback: string
): string {
  const value = settingValue(name, settingsSchema, pendingSettings);
  return value === undefined || value === null ? fallback : String(value);
}

function normalizeStringChoice(value: string, allowedValues: Set<string>, fallback: string): string {
  const normalized = value.trim().toLowerCase();
  return allowedValues.has(normalized) ? normalized : fallback;
}

function normalizePositiveInteger(value: SettingsFieldValue | undefined, fallback: number): number {
  const numberValue = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(numberValue) && numberValue > 0 ? Math.floor(numberValue) : fallback;
}

function parseTargetLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((target) => target.trim())
    .filter(Boolean);
}

function isHttpUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

function quoteCliArg(value: string): string {
  if (!value) {
    return '""';
  }
  if (/[\s,"]/.test(value)) {
    return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
  }
  return value;
}

function headingFor(view: ViewKey): string {
  const labels: Record<ViewKey, string> = {
    fetch: "抓取工作台",
    jobs: "任务队列",
    output: "输出库",
    login: "登录中心",
    settings: "设置",
    doctor: "诊断",
    sponsor: "赞助",
    auth: "社群"
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
    ["fetch", "jobs", "output", "login", "settings", "doctor", "sponsor", "auth"].includes(value)
  );
}

function renderMarkdownBlocks(markdown: string, config: RemoteMarkdownConfig): ReactNode[] {
  const blocks: ReactNode[] = [];
  const lines = markdown.split(/\r?\n/);

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed || trimmed === "<div align=\"center\">" || trimmed === "</div>") {
      continue;
    }

    const tableBlock = collectDocumentTableBlock(lines, index);
    if (tableBlock) {
      const tableRows = extractDocumentTableRows(tableBlock.markdown, config.defaultImageAlt);
      if (tableRows.length) {
        blocks.push(renderDocumentTable(tableRows, config.tableAriaLabel, config.defaultImageAlt, `table-${index}`));
        index = tableBlock.endIndex;
        continue;
      }
    }

    const centeredImageBlock = parseCenteredHtmlImageBlock(lines, index);
    if (centeredImageBlock) {
      blocks.push(renderHtmlImageBlock(centeredImageBlock.image, `html-image-${index}`));
      index = centeredImageBlock.endIndex;
      continue;
    }

    const htmlImage = parseHtmlImage(trimmed);
    if (htmlImage) {
      blocks.push(renderHtmlImageBlock(htmlImage, `html-image-${index}`));
      continue;
    }
    if (/^---+$/.test(trimmed)) {
      blocks.push(<hr key={`hr-${index}`} />);
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const text = stripMarkdownImages(heading[2]);
      if (level <= 2) {
        blocks.push(<h2 key={`heading-${index}`}>{renderInlineMarkdown(text)}</h2>);
      } else if (level === 3) {
        blocks.push(<h3 key={`heading-${index}`}>{renderInlineMarkdown(text)}</h3>);
      } else {
        blocks.push(<h4 key={`heading-${index}`}>{renderInlineMarkdown(text)}</h4>);
      }
      continue;
    }

    const blockquote = trimmed.match(/^>\s*(.+)$/);
    if (blockquote) {
      blocks.push(<blockquote key={`quote-${index}`}>{renderInlineMarkdown(blockquote[1])}</blockquote>);
      continue;
    }

    const linkedImage = trimmed.match(/^\[!\[([^\]]*)\]\(([^)]+)\)\]\(([^)]+)\)$/);
    if (linkedImage) {
      const imageSrc = safeImageUrl(linkedImage[2]);
      const href = safeLinkUrl(linkedImage[3]);
      if (imageSrc && href) {
        blocks.push(
          <a key={`image-${index}`} href={href} target="_blank" rel="noreferrer" className="sponsor-hero-link">
            <img src={imageSrc} alt={linkedImage[1] || "赞助商"} />
          </a>
        );
      }
      continue;
    }

    blocks.push(<p key={`p-${index}`}>{renderInlineMarkdown(trimmed)}</p>);
  }

  return blocks;
}

function collectDocumentTableBlock(lines: string[], startIndex: number): { markdown: string; endIndex: number } | undefined {
  if (!/^<table(?:\s[^>]*)?>/i.test(lines[startIndex].trim())) {
    return undefined;
  }
  const tableLines = [lines[startIndex]];
  if (/<\/table>/i.test(lines[startIndex])) {
    return { markdown: tableLines.join("\n"), endIndex: startIndex };
  }
  for (let index = startIndex + 1; index < lines.length; index += 1) {
    tableLines.push(lines[index]);
    if (/<\/table>/i.test(lines[index])) {
      return { markdown: tableLines.join("\n"), endIndex: index };
    }
  }
  return undefined;
}

function renderDocumentTable(rows: DocumentTableRow[], ariaLabel: string, defaultImageAlt: string, key: string): ReactElement {
  return (
    <div className="sponsor-table-wrap" key={key}>
      <table className="sponsor-table" aria-label={ariaLabel}>
        <tbody>
          {rows.map((row, index) => {
            const cellStyle = row.logoWidth ? { width: row.logoWidth, minWidth: row.logoWidth } : undefined;
            const imageStyle = row.imageWidth ? { width: row.imageWidth } : row.logoWidth ? { width: row.logoWidth } : undefined;
            return (
              <tr key={`${row.href}-${index}`}>
                <td className="sponsor-logo-cell" style={cellStyle}>
                  {row.imageSrc ? (
                    <a href={row.href} target="_blank" rel="noreferrer" className="sponsor-logo-link">
                      <img src={row.imageSrc} alt={row.imageAlt || defaultImageAlt} style={imageStyle} />
                    </a>
                  ) : null}
                </td>
                <td>
                  {row.paragraphs.map((paragraph, paragraphIndex) => (
                    <p key={`${row.href}-${index}-paragraph-${paragraphIndex}`}>{renderInlineMarkdown(paragraph)}</p>
                  ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function parseCenteredHtmlImageBlock(lines: string[], startIndex: number): { image: HtmlImageBlock; endIndex: number } | undefined {
  const firstLine = lines[startIndex].trim();
  const singleLineMatch = firstLine.match(/^<p\s+[^>]*align=["']center["'][^>]*>\s*(<img\s+[^>]+?>)\s*<\/p>$/i);
  if (singleLineMatch) {
    const image = parseHtmlImage(singleLineMatch[1]);
    return image ? { image, endIndex: startIndex } : undefined;
  }
  if (!/^<p\s+[^>]*align=["']center["'][^>]*>\s*$/i.test(firstLine)) {
    return undefined;
  }

  let image: HtmlImageBlock | undefined;
  for (let index = startIndex + 1; index < lines.length; index += 1) {
    const trimmed = lines[index].trim();
    if (!trimmed) {
      continue;
    }
    if (/^<\/p>$/i.test(trimmed)) {
      return image ? { image, endIndex: index } : undefined;
    }
    const parsedImage = parseHtmlImage(trimmed);
    if (parsedImage && !image) {
      image = parsedImage;
      continue;
    }
    return undefined;
  }
  return undefined;
}

function parseHtmlImage(value: string): HtmlImageBlock | undefined {
  const imageMatch = value.match(/^<img\s+([^>]+?)\s*\/?>$/i);
  if (!imageMatch) {
    return undefined;
  }
  const src = safeImageUrl(extractHtmlAttribute(imageMatch[1], "src"));
  if (!src) {
    return undefined;
  }
  const alt = stripHtml(decodeHtmlEntities(extractHtmlAttribute(imageMatch[1], "alt"))) || "图片";
  const width = normalizeHtmlImageWidth(extractHtmlAttribute(imageMatch[1], "width"));
  return { src, alt, width };
}

function normalizeHtmlImageWidth(value: string): number | undefined {
  const match = value.trim().match(/^(\d{1,4})(?:px)?$/i);
  if (!match) {
    return undefined;
  }
  const width = Number(match[1]);
  return width >= 24 && width <= 1200 ? width : undefined;
}

function renderHtmlImageBlock(image: HtmlImageBlock, key: string): ReactElement {
  return (
    <div className="markdown-html-image-wrap" key={key}>
      <img className="markdown-html-image" src={image.src} alt={image.alt} width={image.width} />
    </div>
  );
}

function renderInlineMarkdown(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const tokenPattern = /(\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)|`[^`]+`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenPattern.exec(text))) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (linkMatch) {
      const href = safeLinkUrl(linkMatch[2]);
      nodes.push(
        href ? (
          <a key={`link-${match.index}`} href={href} target="_blank" rel="noreferrer">
            {linkMatch[1]}
          </a>
        ) : (
          linkMatch[1]
        )
      );
    } else if (token.startsWith("**") && token.endsWith("**")) {
      nodes.push(<strong key={`strong-${match.index}`}>{renderInlineMarkdown(token.slice(2, -2))}</strong>);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(<code key={`code-${match.index}`}>{token.slice(1, -1)}</code>);
    }
    lastIndex = tokenPattern.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

function extractDocumentTableRows(markdown: string, defaultTitle: string): DocumentTableRow[] {
  const tableMatch = markdown.match(/<table(?:\s[^>]*)?>([\s\S]*?)<\/table>/i);
  if (!tableMatch) {
    return [];
  }
  const rows = [...tableMatch[1].matchAll(/<tr>\s*<td([^>]*)>([\s\S]*?)<\/td>\s*<td[^>]*>([\s\S]*?)<\/td>\s*<\/tr>/gi)];
  return rows
    .map((row) => {
      const imageCellAttributes = row[1];
      const imageCell = row[2];
      const textCell = row[3];
      const href = safeLinkUrl(extractHtmlAttribute(imageCell, "href")) ?? "https://github.com/iBigQiang/feedgrab/tree/feedgrab-desktop";
      const imageSrc = safeImageUrl(extractHtmlAttribute(imageCell, "src"));
      const imageAlt = stripHtml(decodeHtmlEntities(extractHtmlAttribute(imageCell, "alt")));
      const logoWidth = normalizeSponsorWidth(extractHtmlAttribute(imageCellAttributes, "width"));
      const imageWidth = normalizeSponsorWidth(extractHtmlAttribute(imageCell, "width"));
      const paragraphs = extractHtmlCellParagraphs(textCell);
      return {
        href,
        imageSrc: imageSrc ?? undefined,
        imageAlt,
        logoWidth,
        imageWidth,
        paragraphs: paragraphs.length ? paragraphs : [defaultTitle]
      };
    })
    .filter((row) => row.paragraphs.length || row.imageSrc);
}

function normalizeSponsorWidth(value: string): string | undefined {
  const trimmed = value.trim().toLowerCase();
  if (!trimmed) {
    return undefined;
  }
  const match = trimmed.match(/^(\d{1,3})(?:px)?$/);
  if (!match) {
    return undefined;
  }
  const width = Number(match[1]);
  return width >= 80 && width <= 640 ? `${width}px` : undefined;
}

function extractHtmlAttribute(html: string, attribute: string): string {
  const match = html.match(new RegExp(`${attribute}=["']([^"']+)["']`, "i"));
  return match?.[1] ?? "";
}

function extractHtmlCellParagraphs(html: string): string[] {
  const withInlineMarkdown = html
    .replace(/<br\s*\/?>\s*<br\s*\/?>/gi, "\n\n")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>/gi, "\n\n")
    .replace(/<p(?:\s[^>]*)?>/gi, "")
    .replace(/<(strong|b)>([\s\S]*?)<\/\1>/gi, (_, _tag: string, content: string) => `**${stripHtml(content)}**`)
    .replace(/<a\s+[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, (_, href: string, label: string) => {
      const safeHref = safeLinkUrl(href);
      return safeHref ? `[${stripHtml(label)}](${safeHref})` : stripHtml(label);
    });
  return decodeHtmlEntities(stripHtml(withInlineMarkdown))
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.replace(/[ \t]*\n[ \t]*/g, " ").replace(/[ \t]+/g, " ").trim())
    .filter(Boolean);
}

function stripHtml(value: string): string {
  return value.replace(/<[^>]+>/g, "").trim();
}

function stripMarkdownImages(value: string): string {
  return value.replace(/!\[[^\]]*\]\([^)]+\)/g, "").trim();
}

function decodeHtmlEntities(value: string): string {
  return value
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;/g, "'");
}

function safeLinkUrl(value: string): string | undefined {
  try {
    const url = new URL(value, documentBaseUrl);
    return url.protocol === "https:" || url.protocol === "http:" || url.protocol === "mailto:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function safeImageUrl(value: string): string | undefined {
  try {
    const url = new URL(value, documentRawBaseUrl);
    if (url.protocol !== "https:" && url.protocol !== "http:") {
      return undefined;
    }
    return normalizeGitHubBlobImage(url);
  } catch {
    return undefined;
  }
}

function normalizeGitHubBlobImage(url: URL): string {
  if (url.hostname !== "github.com") {
    return url.href;
  }
  const parts = url.pathname.split("/").filter(Boolean);
  const blobIndex = parts.indexOf("blob");
  if (blobIndex !== 2 || parts.length <= 4) {
    return url.href;
  }
  const [owner, repo] = parts;
  const branch = parts[3];
  const filePath = parts.slice(4).join("/");
  return `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${filePath}`;
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
    reddit: "Reddit",
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

function detectPlatform(url: string): SupportedPlatform {
  if (/github\.com/i.test(url)) return "github";
  if (/reddit\.com|redd\.it/i.test(url)) return "reddit";
  if (/x\.com|twitter\.com/i.test(url)) return "twitter";
  if (/xiaohongshu\.com/i.test(url)) return "xhs";
  if (/youtube\.com|youtu\.be/i.test(url)) return "youtube";
  if (/mp\.weixin\.qq\.com/i.test(url)) return "wechat";
  if (/linux\.do/i.test(url)) return "linuxdo";
  if (/idcflare\.com/i.test(url)) return "idcflare";
  if (/feishu\.cn|larksuite\.com/i.test(url)) return "feishu";
  if (/kdocs\.cn|wps\.cn/i.test(url)) return "kdocs";
  if (/flowus\.cn/i.test(url)) return "flowus";
  if (/zhihu\.com/i.test(url)) return "zhihu";
  if (/zsxq\.com/i.test(url)) return "zsxq";
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
  const unavailable = () => Promise.reject(new Error("Electron 预加载脚本未加载，真实后台工作进程不可用"));
  return {
    ping: unavailable as FeedgrabIpcApi["ping"],
    detectPlatform: unavailable as FeedgrabIpcApi["detectPlatform"],
    startFetch: unavailable as FeedgrabIpcApi["startFetch"],
    cancelJob: unavailable as FeedgrabIpcApi["cancelJob"],
    doctor: unavailable as FeedgrabIpcApi["doctor"],
    repairDoctor: unavailable as FeedgrabIpcApi["repairDoctor"],
    settingsSnapshot: unavailable as FeedgrabIpcApi["settingsSnapshot"],
    settingsSchema: unavailable as FeedgrabIpcApi["settingsSchema"],
    settingsUpdate: unavailable as FeedgrabIpcApi["settingsUpdate"],
    ensureChromeCdp: unavailable as FeedgrabIpcApi["ensureChromeCdp"],
    loginStatus: unavailable as FeedgrabIpcApi["loginStatus"],
    importLoginSessions: unavailable as FeedgrabIpcApi["importLoginSessions"],
    loginPlatform: unavailable as FeedgrabIpcApi["loginPlatform"],
    outputList: unavailable as FeedgrabIpcApi["outputList"],
    openPath: unavailable as FeedgrabIpcApi["openPath"],
    chooseOutputDirectory: unavailable as FeedgrabIpcApi["chooseOutputDirectory"],
    fetchRemoteMarkdown: unavailable as FeedgrabIpcApi["fetchRemoteMarkdown"],
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
  const outputs: OutputArtifact[] = [];

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
      const targets = request.targets ?? [];
      if (request.urls.length === 0 && targets.length > 0) {
        const job = {
          id: `mock-${Date.now()}-1`,
          url: request.commandPreview ?? targets.join(", "),
          target: targets.join(", "),
          targets,
          platform: request.platform && request.platform !== "auto" ? request.platform : "unknown",
          mode: request.mode,
          commandPreview: request.commandPreview,
          status: "running",
          outputDirectory: request.outputDirectory,
          markdownPath: `${request.outputDirectory}\\search\\1.md`,
          attachments: [],
          createdAt: new Date().toISOString()
        } satisfies FetchJobSnapshot;
        setTimeout(() => {
          emit({ id: job.id, event: "job_started", method: "fetch", result: { total: 1 } });
          emit({ id: job.id, event: "log", method: "fetch", level: "info", message: request.commandPreview ?? "structured fetch" });
          emit({ id: job.id, event: "done", method: "fetch", result: { fetched: 1, errors: 0, command: request.commandPreview } });
        }, 0);
        return Promise.resolve([job]);
      }
      const jobs = request.urls.map((url, index) => {
        const platform = detectPlatform(url);
        return {
          id: `mock-${Date.now()}-${index + 1}`,
          url,
          platform,
          status: "running",
          outputDirectory: request.outputDirectory,
          markdownPath: `${request.outputDirectory}\\${platform}\\${index + 1}.md`,
          attachments: [],
          createdAt: new Date().toISOString()
        } satisfies FetchJobSnapshot;
      });
      setTimeout(() => {
        for (const job of jobs) {
          const markdownPath = job.markdownPath ?? `${request.outputDirectory}\\${job.platform}\\mock.md`;
          emit({ id: job.id, event: "job_started", method: "fetch", result: { total: 1 } });
          emit({ id: job.id, event: "progress", method: "fetch", url: job.url, stage: "fetch", message: "正在抓取" });
          emit({ id: job.id, event: "artifact", method: "fetch", url: job.url, artifact: { kind: "markdown", path: markdownPath } });
          emit({ id: job.id, event: "done", method: "fetch", result: { fetched: 1, errors: 0 } });
        }
      }, 0);
      return Promise.resolve(jobs);
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
        notes: ["浏览器内测试环境使用模拟后台工作进程，不访问真实平台。"]
      });
    },
    repairDoctor(checkName) {
      return Promise.resolve({ ok: true, action: checkName, message: "浏览器预览环境已模拟安装/更新" });
    },
    settingsSnapshot() {
      return Promise.resolve({
        outputDirectory: "",
        obsidianVault: "",
        effectiveOutputDirectory: "",
        concurrency: 1,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      });
    },
    settingsSchema() {
      return Promise.resolve(settingsSchemaFromSnapshot(undefined));
    },
    settingsUpdate(values) {
      return Promise.resolve({
        ok: true,
        updated: Object.entries(values).map(([name, value]) => ({ name, value: String(value) }))
      });
    },
    ensureChromeCdp(port) {
      return Promise.resolve({
        ok: true,
        port: normalizePortValue(port),
        started: false,
        message: "浏览器预览环境已模拟 Chrome CDP 连接"
      });
    },
    loginStatus(request) {
      const now = new Date().toISOString();
      const rows: LoginStatus[] = [
        { platform: "twitter", label: "X / Twitter", status: "missing", lastChecked: now },
        { platform: "xhs", label: "小红书", status: "expired", lastChecked: now },
        { platform: "wechat", label: "微信公众号", status: "connected", lastChecked: now },
        { platform: "feishu", label: "飞书", status: "missing", lastChecked: now },
        { platform: "kdocs", label: "金山文档", status: "missing", lastChecked: now },
        { platform: "flowus", label: "FlowUs", status: "missing", lastChecked: now },
        { platform: "reddit", label: "Reddit", status: "missing", lastChecked: now },
        { platform: "zhihu", label: "知乎", status: "missing", lastChecked: now },
        { platform: "linuxdo", label: "LinuxDo", status: "connected", lastChecked: now },
        { platform: "idcflare", label: "IDCFlare", status: "missing", lastChecked: now },
        { platform: "zsxq", label: "知识星球", status: "missing", lastChecked: now },
        { platform: "github", label: "GitHub", status: "notRequired", lastChecked: now },
        { platform: "youtube", label: "YouTube", status: "notRequired", lastChecked: now },
        { platform: "bilibili", label: "Bilibili", status: "notRequired", lastChecked: now },
        { platform: "web", label: "网页", status: "notRequired", lastChecked: now }
      ];
      return Promise.resolve(request?.platforms ? rows.filter((item) => request.platforms?.includes(item.platform)) : rows);
    },
    importLoginSessions(sourceDirectory, platform) {
      const sourceRoot = sourceDirectory || "D:\\AiCode\\feedgrab\\desktop\\sessions";
      const platforms = platform ? [platform] : ["twitter", "xhs", "wechat", "linuxdo", "reddit"];
      return Promise.resolve({
        ok: true,
        sourceDirectory: sourceRoot,
        targetDirectory: "D:\\feedgrab Desktop\\sessions",
        imported: platforms.map((item) => ({
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
    },
    openPath() {
      return Promise.resolve({ ok: true });
    },
    chooseOutputDirectory() {
      return Promise.resolve({ ok: true, path: "D:\\feedgrab Desktop\\output" });
    },
    fetchRemoteMarkdown(url) {
      return fetch(url, { cache: "no-cache" }).then(async (response) => ({
        ok: response.ok,
        markdown: response.ok ? await response.text() : undefined,
        error: response.ok ? undefined : `HTTP ${response.status}`
      }));
    }
  };
}
