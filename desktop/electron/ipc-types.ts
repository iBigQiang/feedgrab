export type SupportedPlatform =
  | "twitter"
  | "xhs"
  | "youtube"
  | "bilibili"
  | "wechat"
  | "github"
  | "linuxdo"
  | "idcflare"
  | "feishu"
  | "kdocs"
  | "flowus"
  | "zhihu"
  | "zsxq"
  | "web"
  | "unknown";

export type WorkerPing = {
  ok: true;
  worker: "mock" | "python";
};

export type FetchMode = "url" | "search" | "account";

export type FetchRequest = {
  urls: string[];
  targets?: string[];
  platform?: SupportedPlatform | "auto";
  mode?: FetchMode;
  commandPreview?: string;
  outputDirectory: string;
};

export type FetchJobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type FetchJobSnapshot = {
  id: string;
  url: string;
  target?: string;
  targets?: string[];
  platform: SupportedPlatform;
  mode?: FetchMode;
  status: FetchJobStatus;
  outputDirectory: string;
  commandPreview?: string;
  createdAt: string;
  markdownPath?: string;
  attachments?: string[];
  error?: string;
};

export type WorkerEventName =
  | "ready"
  | "job_started"
  | "progress"
  | "log"
  | "artifact"
  | "error"
  | "done"
  | "cancelled"
  | "diagnostic";

export type FeedgrabWorkerEvent = {
  id?: string | null;
  event: WorkerEventName;
  method?: string;
  url?: string;
  stage?: string;
  level?: "info" | "warning" | "error" | "success";
  message?: string;
  result?: Record<string, unknown>;
  artifact?: {
    kind?: string;
    path?: string;
    content_type?: string;
    metadata?: Record<string, unknown>;
  };
  error?: {
    code: string;
    message: string;
    recoverable?: boolean;
    details?: Record<string, unknown>;
  };
  diagnostic?: Record<string, unknown>;
};

export type DoctorCheckStatus = "ok" | "warning" | "error" | "unknown";

export type DoctorCheck = {
  name: string;
  label: string;
  status: DoctorCheckStatus;
  message: string;
  details?: Record<string, unknown>;
  repair?: {
    id: string;
    label: string;
    available: boolean;
  };
};

export type DoctorSnapshot = {
  python: string;
  browser: "ready" | "missing" | "mock";
  network: "disabled" | "available" | "unknown";
  writableOutput: boolean;
  notes: string[];
  checks?: DoctorCheck[];
};

export type DoctorRepairResult = {
  ok: boolean;
  action: string;
  message: string;
  log?: string[];
  error?: string;
};

export type SettingsSnapshot = {
  outputDirectory: string;
  obsidianVault: string;
  effectiveOutputDirectory: string;
  concurrency: number;
  downloadImages: boolean;
  localizeMedia: boolean;
  replyMode: "author" | "all" | "none";
};

export type SettingsFieldValue = string | number | boolean;

export type SettingsFieldType = "boolean" | "number" | "string" | "select" | "path" | "secret";

export type SettingsOption = {
  label: string;
  value: SettingsFieldValue;
};

export type SettingsFieldSchema = {
  name: string;
  label: string;
  type: SettingsFieldType;
  value?: SettingsFieldValue;
  defaultValue?: SettingsFieldValue;
  description?: string;
  secret?: boolean;
  placeholder?: string;
  options?: SettingsOption[];
};

export type SettingsPlatformSchema = {
  id: string;
  label: string;
  fields: SettingsFieldSchema[];
};

export type SettingsSchema = {
  basic: SettingsFieldSchema[];
  platforms: SettingsPlatformSchema[];
};

export type SettingsUpdateResult = {
  ok: boolean;
  updated: Array<{ name: string; value: string }>;
  settingsPath?: string;
  error?: string;
};

export type LoginStatus = {
  platform: SupportedPlatform;
  label: string;
  status: "connected" | "expired" | "missing" | "notRequired";
  lastChecked: string;
  sessionPath?: string;
  cookieCount?: number;
  accountCount?: number;
  validCount?: number;
  expiredCount?: number;
  unreadableCount?: number;
  validationMode?: "presence" | "structural" | "online";
  message?: string;
  loginRequired?: boolean;
};

export type LoginStatusRequest = {
  refresh?: boolean;
  platforms?: SupportedPlatform[];
};

export type LoginSessionImportRecord = {
  source: string;
  target?: string;
  reason?: string;
};

export type LoginSessionImportResult = {
  ok: boolean;
  imported: LoginSessionImportRecord[];
  skipped: LoginSessionImportRecord[];
  disabled?: LoginSessionImportRecord[];
  ignored: LoginSessionImportRecord[];
  sourceDirectory?: string;
  targetDirectory?: string;
  error?: string;
};

export type LoginPlatformResult = {
  ok: boolean;
  platform: SupportedPlatform;
  message: string;
  status?: LoginStatus["status"];
  error?: string;
};

export type OutputArtifact = {
  id: string;
  title: string;
  platform: string;
  markdownPath: string;
  attachments: string[];
  createdAt: string;
};

export type OpenPathResult = {
  ok: boolean;
  error?: string;
};

export type DirectorySelectionResult = {
  ok: boolean;
  path?: string;
  cancelled?: boolean;
  error?: string;
};

export type DirectorySelectionOptions = {
  title?: string;
};

export type RemoteMarkdownResult = {
  ok: boolean;
  markdown?: string;
  error?: string;
};

export type ChromeCdpEnsureResult = {
  ok: boolean;
  port: number;
  started: boolean;
  message: string;
  url?: string;
  chromePath?: string;
  error?: string;
};

export type FeedgrabIpcApi = {
  ping: () => Promise<WorkerPing>;
  detectPlatform: (url: string) => Promise<SupportedPlatform>;
  startFetch: (request: FetchRequest) => Promise<FetchJobSnapshot[]>;
  cancelJob: (jobId: string) => Promise<FetchJobSnapshot>;
  doctor: () => Promise<DoctorSnapshot>;
  repairDoctor: (checkName: string) => Promise<DoctorRepairResult>;
  settingsSnapshot: () => Promise<SettingsSnapshot>;
  settingsSchema: () => Promise<SettingsSchema>;
  settingsUpdate: (values: Record<string, SettingsFieldValue>) => Promise<SettingsUpdateResult>;
  ensureChromeCdp: (port?: number) => Promise<ChromeCdpEnsureResult>;
  loginStatus: (request?: LoginStatusRequest) => Promise<LoginStatus[]>;
  importLoginSessions: (sourceDirectory?: string, platform?: SupportedPlatform) => Promise<LoginSessionImportResult>;
  loginPlatform: (platform: SupportedPlatform) => Promise<LoginPlatformResult>;
  outputList: () => Promise<OutputArtifact[]>;
  openPath: (path: string) => Promise<OpenPathResult>;
  chooseOutputDirectory: (options?: DirectorySelectionOptions) => Promise<DirectorySelectionResult>;
  fetchRemoteMarkdown: (url: string) => Promise<RemoteMarkdownResult>;
  onWorkerEvent: (callback: (event: FeedgrabWorkerEvent) => void) => () => void;
};
