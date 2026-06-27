import { describe, expect, it } from "vitest";

import { appReducer, createInitialAppState } from "../renderer/src/state/appReducer";

describe("appReducer", () => {
  it("upserts one worker job per submitted URL and keeps the latest active", () => {
    const state = createInitialAppState();

    const first = appReducer(state, {
      type: "job/upsert",
      payload: {
        id: "job-a",
        url: "https://example.com/a",
        platform: "web",
        status: "running",
        outputDirectory: "D:\\Notes\\Inbox",
        createdAt: "2026-06-26T09:00:00.000Z"
      }
    });
    const next = appReducer(first, {
      type: "job/upsert",
      payload: {
        id: "job-b",
        url: "https://example.com/b",
        platform: "web",
        status: "running",
        outputDirectory: "D:\\Notes\\Inbox",
        createdAt: "2026-06-26T09:00:01.000Z"
      }
    });

    expect(next.jobs).toHaveLength(2);
    expect(next.jobs[0]).toMatchObject({
      id: "job-b",
      url: "https://example.com/b",
      status: "running",
      outputDirectory: "D:\\Notes\\Inbox"
    });
    expect(next.jobs[1]?.url).toBe("https://example.com/a");
    expect(next.activeJobId).toBe("job-b");
  });

  it("appends worker logs and completes a running job without losing artifacts", () => {
    const started = appReducer(createInitialAppState(), {
      type: "job/upsert",
      payload: {
        id: "job-linuxdo",
        url: "https://linux.do/t/topic/2023688",
        platform: "linuxdo",
        status: "running",
        outputDirectory: "D:\\Notes\\Feeds",
        createdAt: "2026-06-26T09:00:00.000Z"
      }
    });
    const activeJobId = started.activeJobId ?? "";

    const withLog = appReducer(started, {
      type: "job/log",
      payload: {
        jobId: activeJobId,
        level: "info",
        message: "topic json fetched"
      }
    });
    const completed = appReducer(withLog, {
      type: "job/complete",
      payload: {
        jobId: activeJobId,
        markdownPath: "D:\\Notes\\Feeds\\LinuxDo\\topic.md",
        attachments: ["D:\\Notes\\Feeds\\LinuxDo\\attachments\\image.png"]
      }
    });

    expect(completed.jobs[0]).toMatchObject({
      status: "completed",
      markdownPath: "D:\\Notes\\Feeds\\LinuxDo\\topic.md",
      attachments: ["D:\\Notes\\Feeds\\LinuxDo\\attachments\\image.png"]
    });
    expect(completed.logs.at(-1)).toMatchObject({
      jobId: activeJobId,
      level: "success",
      message: "抓取完成"
    });
  });

  it("stores settings schema and tracks typed setting edits", () => {
    const withSchema = appReducer(createInitialAppState(), {
      type: "settings/schema",
      payload: {
        basic: [{ name: "OUTPUT_DIR", label: "输出目录", type: "path", value: "D:\\Notes\\Feeds" }],
        platforms: [
          {
            id: "x",
            label: "X / Twitter",
            fields: [{ name: "X_SEARCH_DAYS", label: "搜索天数", type: "number", value: 7 }]
          }
        ]
      }
    });
    const edited = appReducer(withSchema, {
      type: "settings/edit",
      payload: { name: "X_SEARCH_DAYS", value: 3 }
    });
    const saved = appReducer(edited, {
      type: "settings/saved",
      payload: {
        ok: true,
        updated: [{ name: "X_SEARCH_DAYS", value: "3" }]
      }
    });

    expect(withSchema.settingsSchema?.platforms[0]?.fields[0]?.name).toBe("X_SEARCH_DAYS");
    expect(edited.pendingSettings).toEqual({ X_SEARCH_DAYS: 3 });
    expect(saved.pendingSettings).toEqual({});
    expect(saved.logs.at(-1)).toMatchObject({
      level: "success",
      message: "设置已保存"
    });
  });

  it("clears only in-client output records", () => {
    const withOutput = appReducer(createInitialAppState(), {
      type: "output/add",
      payload: {
        id: "artifact-1",
        title: "article.md",
        platform: "GitHub",
        markdownPath: "D:\\Notes\\Feeds\\GitHub\\article.md",
        attachments: [],
        createdAt: "2026-06-26T09:00:00.000Z"
      }
    });
    const cleared = appReducer(withOutput, { type: "output/clear" });

    expect(cleared.outputs).toEqual([]);
    expect(cleared.logs.at(-1)).toMatchObject({
      level: "info",
      message: "已清空客户端输出记录"
    });
  });
});
