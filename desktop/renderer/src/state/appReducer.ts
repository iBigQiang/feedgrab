import type {
  DoctorSnapshot,
  FetchJobSnapshot,
  FetchJobStatus,
  OutputArtifact,
  SettingsSnapshot
} from "../../../electron/ipc-types";

export type ViewKey = "fetch" | "jobs" | "output" | "login" | "settings" | "doctor" | "auth";
export type LogLevel = "info" | "warning" | "error" | "success";

export type UiJob = {
  id: string;
  url: string;
  status: FetchJobStatus;
  outputDirectory: string;
  platform?: string;
  markdownPath?: string;
  attachments: string[];
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
  doctor?: DoctorSnapshot;
  activeJobId?: string;
  lastNotice?: string;
  nextJobSeq: number;
  nextLogSeq: number;
};

export type AppAction =
  | { type: "view/select"; payload: ViewKey }
  | { type: "settings/load"; payload: SettingsSnapshot }
  | { type: "settings/outputDirectory"; payload: string }
  | { type: "doctor/load"; payload: DoctorSnapshot }
  | { type: "output/load"; payload: OutputArtifact[] }
  | { type: "output/add"; payload: OutputArtifact }
  | { type: "fetch/start"; payload: { urls: string[]; outputDirectory: string } }
  | { type: "job/upsert"; payload: FetchJobSnapshot }
  | { type: "job/status"; payload: { jobId: string; status: FetchJobStatus; error?: string } }
  | { type: "job/artifact"; payload: { jobId: string; markdownPath: string; attachments: string[] } }
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
    case "fetch/start": {
      const urls = action.payload.urls.map((url) => url.trim()).filter(Boolean);
      const createdJobs = urls.map<UiJob>((url, index) => {
        const id = `job-${state.nextJobSeq + index}`;
        return {
          id,
          url,
          status: index === 0 ? "running" : "queued",
          outputDirectory: action.payload.outputDirectory,
          attachments: [],
          createdAt: new Date().toISOString()
        };
      });
      const firstJobId = createdJobs[0]?.id;
      const notice = `已创建 ${createdJobs.length} 个任务`;

      return {
        ...state,
        outputDirectory: action.payload.outputDirectory,
        jobs: [...createdJobs, ...state.jobs],
        logs: [
          ...state.logs,
          createLog(state.nextLogSeq, "info", `${notice}，输出到 ${action.payload.outputDirectory}`, firstJobId)
        ],
        activeJobId: firstJobId ?? state.activeJobId,
        lastNotice: notice,
        nextJobSeq: state.nextJobSeq + createdJobs.length,
        nextLogSeq: state.nextLogSeq + 1
      };
    }
    case "job/upsert": {
      const existing = state.jobs.some((job) => job.id === action.payload.id);
      const nextJob: UiJob = {
        id: action.payload.id,
        url: action.payload.url,
        status: action.payload.status,
        outputDirectory: action.payload.outputDirectory,
        platform: action.payload.platform,
        markdownPath: action.payload.markdownPath,
        attachments: action.payload.attachments ?? [],
        createdAt: action.payload.createdAt,
        error: action.payload.error
      };
      return {
        ...state,
        jobs: existing ? state.jobs.map((job) => (job.id === nextJob.id ? { ...job, ...nextJob } : job)) : [nextJob, ...state.jobs],
        activeJobId: nextJob.id,
        lastNotice: `已创建 ${nextJob.url} 抓取任务`
      };
    }
    case "job/status":
      return {
        ...state,
        jobs: state.jobs.map((job) =>
          job.id === action.payload.jobId ? { ...job, status: action.payload.status, error: action.payload.error } : job
        )
      };
    case "job/artifact":
      return {
        ...state,
        jobs: state.jobs.map((job) =>
          job.id === action.payload.jobId
            ? {
                ...job,
                markdownPath: action.payload.markdownPath,
                attachments: action.payload.attachments
              }
            : job
        )
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
                attachments: action.payload.attachments
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
