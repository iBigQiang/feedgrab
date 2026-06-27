import { contextBridge, ipcRenderer } from "electron";

import type {
  FeedgrabIpcApi,
  FeedgrabWorkerEvent,
  FetchRequest,
  LoginStatusRequest,
  SettingsFieldValue,
  SupportedPlatform
} from "./ipc-types.js";

const api: FeedgrabIpcApi = {
  ping: () => ipcRenderer.invoke("feedgrab:ping") as ReturnType<FeedgrabIpcApi["ping"]>,
  detectPlatform: (url: string) =>
    ipcRenderer.invoke("feedgrab:detectPlatform", url) as ReturnType<FeedgrabIpcApi["detectPlatform"]>,
  startFetch: (request: FetchRequest) =>
    ipcRenderer.invoke("feedgrab:startFetch", request) as ReturnType<FeedgrabIpcApi["startFetch"]>,
  cancelJob: (jobId: string) =>
    ipcRenderer.invoke("feedgrab:cancelJob", jobId) as ReturnType<FeedgrabIpcApi["cancelJob"]>,
  doctor: () => ipcRenderer.invoke("feedgrab:doctor") as ReturnType<FeedgrabIpcApi["doctor"]>,
  repairDoctor: (checkName: string) =>
    ipcRenderer.invoke("feedgrab:repairDoctor", checkName) as ReturnType<FeedgrabIpcApi["repairDoctor"]>,
  settingsSnapshot: () =>
    ipcRenderer.invoke("feedgrab:settingsSnapshot") as ReturnType<FeedgrabIpcApi["settingsSnapshot"]>,
  settingsSchema: () =>
    ipcRenderer.invoke("feedgrab:settingsSchema") as ReturnType<FeedgrabIpcApi["settingsSchema"]>,
  settingsUpdate: (values: Record<string, SettingsFieldValue>) =>
    ipcRenderer.invoke("feedgrab:settingsUpdate", values) as ReturnType<FeedgrabIpcApi["settingsUpdate"]>,
  ensureChromeCdp: (port?: number) =>
    ipcRenderer.invoke("feedgrab:ensureChromeCdp", port) as ReturnType<FeedgrabIpcApi["ensureChromeCdp"]>,
  loginStatus: (request?: LoginStatusRequest) =>
    ipcRenderer.invoke("feedgrab:loginStatus", request) as ReturnType<FeedgrabIpcApi["loginStatus"]>,
  importLoginSessions: (sourceDirectory?: string, platform?: SupportedPlatform) =>
    ipcRenderer.invoke("feedgrab:importLoginSessions", sourceDirectory, platform) as ReturnType<
      FeedgrabIpcApi["importLoginSessions"]
    >,
  loginPlatform: (platform: SupportedPlatform) =>
    ipcRenderer.invoke("feedgrab:loginPlatform", platform) as ReturnType<FeedgrabIpcApi["loginPlatform"]>,
  outputList: () => ipcRenderer.invoke("feedgrab:outputList") as ReturnType<FeedgrabIpcApi["outputList"]>,
  openPath: (path: string) =>
    ipcRenderer.invoke("feedgrab:openPath", path) as ReturnType<FeedgrabIpcApi["openPath"]>,
  chooseOutputDirectory: () =>
    ipcRenderer.invoke("feedgrab:chooseOutputDirectory") as ReturnType<FeedgrabIpcApi["chooseOutputDirectory"]>,
  fetchRemoteMarkdown: (url: string) =>
    ipcRenderer.invoke("feedgrab:fetchRemoteMarkdown", url) as ReturnType<FeedgrabIpcApi["fetchRemoteMarkdown"]>,
  onWorkerEvent: (callback: (event: FeedgrabWorkerEvent) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, workerEvent: FeedgrabWorkerEvent) => callback(workerEvent);
    ipcRenderer.on("feedgrab:workerEvent", listener);
    return () => ipcRenderer.removeListener("feedgrab:workerEvent", listener);
  }
};

contextBridge.exposeInMainWorld("feedgrab", api);
