import { describe, expect, it } from "vitest";

import { appReducer, createInitialAppState } from "../renderer/src/state/appReducer";

describe("appReducer", () => {
  it("creates queued jobs from pasted URLs and marks the first as active", () => {
    const state = createInitialAppState();

    const next = appReducer(state, {
      type: "fetch/start",
      payload: {
        urls: ["https://example.com/a", "https://example.com/b"],
        outputDirectory: "D:\\Notes\\Inbox"
      }
    });

    expect(next.jobs).toHaveLength(2);
    expect(next.jobs[0]).toMatchObject({
      url: "https://example.com/a",
      status: "running",
      outputDirectory: "D:\\Notes\\Inbox"
    });
    expect(next.jobs[1]?.status).toBe("queued");
    expect(next.activeJobId).toBe(next.jobs[0]?.id);
  });

  it("appends worker logs and completes a running job without losing artifacts", () => {
    const started = appReducer(createInitialAppState(), {
      type: "fetch/start",
      payload: {
        urls: ["https://linux.do/t/topic/2023688"],
        outputDirectory: "D:\\Notes\\Feeds"
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
});
