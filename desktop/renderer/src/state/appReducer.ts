import type {
  DoctorSnapshot,
  FetchJobSnapshot,
  FetchJobStatus,
  OutputArtifact,
  SettingsFieldValue,
  SettingsSchema,
  SettingsSnapshot
} from "../../../electron/ipc-types";

export type ViewKey = "fetch" | "jobs" | "output" | "login" | "settings" | "doctor" | "sponsor" | "auth";
export type LogLevel = "info" | "warning" | "error" | "success";

export type UiJob = {
  id: string;
  url: string;
  target?: string;
  targets?: string[];
  status: FetchJobStatus;
  outputDirectory: string;
  platform?: string;
  mode?: string;
  commandPreview?: string;
  markdownPath?: string;
  attachments: string[];
  artifactPaths: string[];
  lastMessage?: string;
  createdAt: string;
  error?: string;
};

export type UiLog = {
  id: string;
  jobId?: string;
  level: LogLevel;
  message: string;
  createdAt: string;
};

export type AppState = {
  selectedView: ViewKey;
  outputDirectory: string;
  jobs: UiJob[];
  logs: UiLog[];
  outputs: OutputArtifact[];
  settings?: SettingsSnapshot;
  settingsSchema?: SettingsSchema;
  pendingSettings: Record<string, SettingsFieldValue>;
  doctor?: DoctorSnapshot;
  activeJobId?: string;
  lastNotice?: string;
  nextJobSeq: number;
  nextLogSeq: number;
};

export type AppAction =
  | { type: "view/select"; payload: ViewKey }
  | { type: "settings/load"; payload: SettingsSnapshot }
  | { type: "settings/schema"; payload: SettingsSchema }
  | { type: "settings/edit"; payload: { name: string; value: SettingsFieldValue } }
  | { type: "settings/saved"; payload: { ok: boolean; updated: Array<{ name: string; value: string }>; error?: string } }
  | { type: "settings/outputDirectory"; payload: string }
  | { type: "doctor/load"; payload: DoctorSnapshot }
  | { type: "output/load"; payload: OutputArtifact[] }
  | { type: "output/add"; payload: OutputArtifact }
  | { type: "output/clear" }
  | { type: "job/upsert"; payload: FetchJobSnapshot }
  | { type: "job/status"; payload: { jobId: string; status: FetchJobStatus; error?: string } }
  | { type: "job/artifact"; payload: { jobId: string; markdownPath: string; attachments: string[] } }
  | { type: "job/message"; payload: { jobId: string; message: string } }
  | { type: "job/log"; payload: { jobId?: string; level: LogLevel; message: string } }
  | { type: "job/complete"; payload: { jobId: string; markdownPath: string; attachments: string[] } }
  | { type: "job/cancel"; payload: { jobId: string } }
  | { type: "notice/clear" };

export function createInitialAppState(): AppState {
  return {
    selectedView: "fetch",
    outputDirectory: "D:\\Notes\\Feeds",
    jobs: [],
    logs: [
      {
        id: "log-0",
        level: "info",
        message: "GUI 已启动，正在检测本地 worker。",
        createdAt: new Date("2026-06-25T08:00:00.000Z").toISOString()
      }
    ],
    outputs: [],
    pendingSettings: {},
    nextJobSeq: 1,
    nextLogSeq: 1
  };
}

function createLog(seq: number, level: LogLevel, message: string, jobId?: string): UiLog {
  return {
    id: `log-${seq}`,
    jobId,
    level,
    message,
    createdAt: new Date().toISOString()
  };
}

function isTerminalJobStatus(status: FetchJobStatus): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

function createPlaceholderJob(jobId: string, status: FetchJobStatus, error?: string): UiJob {
  return {
    id: jobId,
    url: jobId,
    status,
    outputDirectory: "",
    attachments: [],
    artifactPaths: [],
    createdAt: new Date().toISOString(),
    error
  };
}

function appendUniquePath(paths: string[], nextPath: string): string[] {
  return paths.includes(nextPath) ? paths : [...paths, nextPath];
}

function mergeJob(existingJob: UiJob | undefined, nextJob: UiJob): UiJob {
  if (!existingJob) {
    return nextJob;
  }
  const preserveTerminalStatus = isTerminalJobStatus(existingJob.status) && nextJob.status === "running";
  return {
    ...existingJob,
    ...nextJob,
    status: preserveTerminalStatus ? existingJob.status : nextJob.status,
    error: preserveTerminalStatus ? existingJob.error : nextJob.error,
    markdownPath: nextJob.markdownPath ?? existingJob.markdownPath,
    attachments: nextJob.attachments.length > 0 ? nextJob.attachments : existingJob.attachments,
    artifactPaths: nextJob.artifactPaths.length > 0 ? nextJob.artifactPaths : existingJob.artifactPaths,
    lastMessage: nextJob.lastMessage ?? existingJob.lastMessage
  };
}

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "view/select":
      return { ...state, selectedView: action.payload };
    case "settings/load":
      return {
        ...state,
        settings: action.payload,
        outputDirectory: action.payload.outputDirectory
      };
    case "settings/schema":
      return {
        ...state,
        settingsSchema: action.payload
      };
    case "settings/edit":
      return {
        ...state,
        pendingSettings: {
          ...state.pendingSettings,
          [action.payload.name]: action.payload.value
        }
      };
    case "settings/saved":
      return {
        ...state,
        pendingSettings: action.payload.ok ? {} : state.pendingSettings,
        logs: [
          ...state.logs,
          createLog(
            state.nextLogSeq,
            action.payload.ok ? "success" : "error",
            action.payload.ok ? "设置已保存" : action.payload.error ?? "设置保存失败"
          )
        ],
        nextLogSeq: state.nextLogSeq + 1
      };
    case "settings/outputDirectory":
      return {
        ...state,
        outputDirectory: action.payload,
        settings: state.settings ? { ...state.settings, outputDirectory: action.payload } : state.settings,
        logs: [...state.logs, createLog(state.nextLogSeq, "info", `输出目录已选择：${action.payload}`)],
        nextLogSeq: state.nextLogSeq + 1
      };
    case "doctor/load":
      return { ...state, doctor: action.payload };
    case "output/load":
      return { ...state, outputs: action.payload };
    case "output/add":
      return {
        ...state,
        outputs: [action.payload, ...state.outputs.filter((item) => item.markdownPath !== action.payload.markdownPath)]
      };
    case "output/clear":
      return {
        ...state,
        outputs: [],
        logs: [...state.logs, createLog(state.nextLogSeq, "info", "已清空客户端输出记录")],
        nextLogSeq: state.nextLogSeq + 1
      };
    case "job/upsert": {
      const existingJob = state.jobs.find((job) => job.id === action.payload.id);
      const nextJob: UiJob = {
        id: action.payload.id,
        url: action.payload.url,
        target: action.payload.target,
        targets: action.payload.targets,
        status: action.payload.status,
        outputDirectory: action.payload.outputDirectory,
        platform: action.payload.platform,
        mode: action.payload.mode,
        commandPreview: action.payload.commandPreview,
        markdownPath: action.payload.markdownPath,
        attachments: action.payload.attachments ?? [],
        artifactPaths: action.payload.markdownPath ? [action.payload.markdownPath] : [],
        createdAt: action.payload.createdAt,
        error: action.payload.error
      };
      const mergedJob = mergeJob(existingJob, nextJob);
      return {
        ...state,
        jobs: existingJob
          ? state.jobs.map((job) => (job.id === mergedJob.id ? { ...job, ...mergedJob } : job))
          : [mergedJob, ...state.jobs],
        activeJobId: mergedJob.id,
        lastNotice: `已创建 ${mergedJob.commandPreview ?? mergedJob.target ?? mergedJob.url} 抓取任务`
      };
    }
    case "job/message":
      return {
        ...state,
        jobs: state.jobs.some((job) => job.id === action.payload.jobId)
          ? state.jobs.map((job) =>
              job.id === action.payload.jobId ? { ...job, lastMessage: action.payload.message } : job
            )
          : [{ ...createPlaceholderJob(action.payload.jobId, "running"), lastMessage: action.payload.message }, ...state.jobs]
      };
    case "job/status":
      return {
        ...state,
        jobs: state.jobs.some((job) => job.id === action.payload.jobId)
          ? state.jobs.map((job) =>
              job.id === action.payload.jobId
                ? { ...job, status: action.payload.status, error: action.payload.error }
                : job
            )
          : isTerminalJobStatus(action.payload.status)
            ? [createPlaceholderJob(action.payload.jobId, action.payload.status, action.payload.error), ...state.jobs]
            : state.jobs
      };
    case "job/artifact":
      return {
        ...state,
        jobs: state.jobs.some((job) => job.id === action.payload.jobId)
          ? state.jobs.map((job) =>
              job.id === action.payload.jobId
                ? {
                    ...job,
                    markdownPath: action.payload.markdownPath,
                    attachments: action.payload.attachments,
                    artifactPaths: appendUniquePath(job.artifactPaths, action.payload.markdownPath)
                  }
                : job
            )
          : [
              {
                ...createPlaceholderJob(action.payload.jobId, "running"),
                markdownPath: action.payload.markdownPath,
                attachments: action.payload.attachments,
                artifactPaths: [action.payload.markdownPath]
              },
              ...state.jobs
            ]
      };
    case "job/log":
      return {
        ...state,
        logs: [
          ...state.logs,
          createLog(state.nextLogSeq, action.payload.level, action.payload.message, action.payload.jobId)
        ],
        nextLogSeq: state.nextLogSeq + 1
      };
    case "job/complete":
      return {
        ...state,
        jobs: state.jobs.map((job) =>
          job.id === action.payload.jobId
            ? {
                ...job,
                status: "completed",
                markdownPath: action.payload.markdownPath,
                attachments: action.payload.attachments,
                artifactPaths: appendUniquePath(job.artifactPaths, action.payload.markdownPath)
              }
            : job
        ),
        logs: [...state.logs, createLog(state.nextLogSeq, "success", "抓取完成", action.payload.jobId)],
        nextLogSeq: state.nextLogSeq + 1
      };
    case "job/cancel":
      return {
        ...state,
        jobs: state.jobs.map((job) => (job.id === action.payload.jobId ? { ...job, status: "cancelled" } : job)),
        logs: [...state.logs, createLog(state.nextLogSeq, "warning", "任务已取消", action.payload.jobId)],
        nextLogSeq: state.nextLogSeq + 1
      };
    case "notice/clear":
      return { ...state, lastNotice: undefined };
    default:
      return state;
  }
}
