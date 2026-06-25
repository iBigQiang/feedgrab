import { describe, expect, it } from "vitest";

import { createMockPythonWorkerClient } from "../electron/python-worker";

describe("createMockPythonWorkerClient", () => {
  it("returns deterministic diagnostics and mock output without touching real platforms", async () => {
    const worker = createMockPythonWorkerClient();

    await expect(worker.ping()).resolves.toEqual({ ok: true, worker: "mock" });
    await expect(worker.detectPlatform("https://github.com/iBigQiang/feedgrab")).resolves.toBe("github");
    const doctor = await worker.doctor();
    expect(doctor.python).toContain("mock");
    expect(doctor.network).toBe("disabled");

    const job = await worker.startFetch({
      urls: ["https://example.com/article"],
      outputDirectory: "D:\\Notes\\Feeds"
    });
    const outputs = await worker.outputList();

    expect(job.status).toBe("running");
    expect(outputs[0]).toMatchObject({
      platform: "Web",
      markdownPath: "D:\\Notes\\Feeds\\Web\\article.md"
    });
  });
});
