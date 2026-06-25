export type SupportedPlatform =
  | "twitter"
  | "xhs"
  | "youtube"
  | "bilibili"
  | "wechat"
  | "github"
  | "linuxdo"
  | "feishu"
  | "web"
  | "unknown";

export type WorkerPing = {
  ok: true;
  worker: "mock" | "python";
};

export type FetchRequest = {
  urls: string[];
  outputDirectory: string;
};

export type FetchJobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type FetchJobSnapshot = {
  id: string;
  url: string;
  platform: SupportedPlatform;
  status: FetchJobStatus;
  outputDirectory: string;
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

export type DoctorSnapshot = {
  python: string;
  browser: "ready" | "missing" | "mock";
  network: "disabled" | "available" | "unknown";
  writableOutput: boolean;
  notes: string[];
};

export type SettingsSnapshot = {
  outputDirectory: string;
  concurrency: number;
  downloadImages: boolean;
  localizeMedia: boolean;
  replyMode: "author" | "all" | "none";
};

export type LoginStatus = {
  platform: SupportedPlatform;
  label: string;
  status: "connected" | "expired" | "missing" | "notRequired";
  lastChecked: string;
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

export type FeedgrabIpcApi = {
  ping: () => Promise<WorkerPing>;
  detectPlatform: (url: string) => Promise<SupportedPlatform>;
  startFetch: (request: FetchRequest) => Promise<FetchJobSnapshot>;
  cancelJob: (jobId: string) => Promise<FetchJobSnapshot>;
  doctor: () => Promise<DoctorSnapshot>;
  settingsSnapshot: () => Promise<SettingsSnapshot>;
  loginStatus: () => Promise<LoginStatus[]>;
  outputList: () => Promise<OutputArtifact[]>;
  openPath: (path: string) => Promise<OpenPathResult>;
  chooseOutputDirectory: () => Promise<DirectorySelectionResult>;
  onWorkerEvent: (callback: (event: FeedgrabWorkerEvent) => void) => () => void;
};
