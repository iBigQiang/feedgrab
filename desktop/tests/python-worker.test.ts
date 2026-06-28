import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { createMockPythonWorkerClient, createPythonWorkerClient } from "../electron/python-worker";

async function waitForCondition(predicate: () => boolean, timeoutMs = 1000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("Timed out waiting for expected worker event");
}

describe("createMockPythonWorkerClient", () => {
  it("returns deterministic diagnostics and mock output without touching real platforms", async () => {
    const worker = createMockPythonWorkerClient();

    await expect(worker.ping()).resolves.toEqual({ ok: true, worker: "mock" });
    await expect(worker.detectPlatform("https://github.com/iBigQiang/feedgrab")).resolves.toBe("github");
    const doctor = await worker.doctor();
    expect(doctor.python).toContain("mock");
    expect(doctor.network).toBe("disabled");

    const jobs = await worker.startFetch({
      urls: ["https://example.com/article", "https://github.com/iBigQiang/feedgrab"],
      outputDirectory: "D:\\Notes\\Feeds"
    });
    const outputs = await worker.outputList();

    expect(jobs).toHaveLength(2);
    expect(jobs[0]).toMatchObject({ status: "running", url: "https://example.com/article" });
    expect(outputs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          platform: "Web",
          markdownPath: "D:\\Notes\\Feeds\\Web\\article.md"
        }),
        expect.objectContaining({
          platform: "GitHub",
          markdownPath: "D:\\Notes\\Feeds\\GitHub\\feedgrab.md"
        })
      ])
    );
  });

  it("supports login refresh, installer session import, platform login, schema, and settings update", async () => {
    const worker = createMockPythonWorkerClient();

    const refreshed = await worker.loginStatus({ refresh: true });
    expect(refreshed.some((item) => item.platform === "twitter")).toBe(true);

    await expect(worker.importLoginSessions("D:\\Program Files\\feedgrab\\sessions")).resolves.toMatchObject({
      ok: true,
      imported: expect.arrayContaining([
        expect.objectContaining({
          source: "D:\\Program Files\\feedgrab\\sessions\\twitter.json"
        })
      ])
    });

    await expect(worker.loginPlatform("twitter")).resolves.toMatchObject({
      ok: true,
      platform: "twitter"
    });

    const schema = await worker.settingsSchema();
    expect(schema.basic.map((field) => field.name)).toContain("OUTPUT_DIR");
    expect(schema.platforms.flatMap((platform) => platform.fields).map((field) => field.name)).toContain("FEISHU_APP_SECRET");

    await expect(worker.settingsUpdate({ OUTPUT_DIR: "D:\\Notes\\Feeds", X_SEARCH_DAYS: 3 })).resolves.toMatchObject({
      ok: true,
      updated: expect.arrayContaining([expect.objectContaining({ name: "X_SEARCH_DAYS", value: "3" })])
    });
  });

  it("creates mock search jobs from structured non-URL fetch requests", async () => {
    const worker = createMockPythonWorkerClient();

    const jobs = await worker.startFetch({
      urls: [],
      targets: ["openclaw"],
      platform: "twitter",
      mode: "search",
      commandPreview: 'feedgrab x-so "openclaw"',
      outputDirectory: "D:\\Notes\\Feeds"
    });

    expect(jobs).toEqual([
      expect.objectContaining({
        id: expect.stringContaining("mock-job"),
        url: 'feedgrab x-so "openclaw"',
        platform: "twitter",
        mode: "search",
        commandPreview: 'feedgrab x-so "openclaw"'
      })
    ]);
  });

  it("keeps raw output fields and computes effective output directory for mock snapshots", async () => {
    const worker = createMockPythonWorkerClient();

    const snapshot = await worker.settingsSnapshot();

    expect(snapshot).toMatchObject({
      outputDirectory: "",
      obsidianVault: "",
      effectiveOutputDirectory: "",
      concurrency: 2,
      downloadImages: true,
      localizeMedia: true,
      replyMode: "author"
    });
  });
});

describe("createPythonWorkerClient protocol mapping", () => {
  it("maps desktop login and settings calls to the JSONL sidecar protocol", async () => {
    const script = `
      const readline = require("node:readline");
      const rl = readline.createInterface({ input: process.stdin });
      rl.on("line", (line) => {
        const request = JSON.parse(line);
        if (request.method === "login_status") {
          process.stdout.write(JSON.stringify({
            id: request.id,
            event: "done",
            method: request.method,
            result: { platforms: [{ platform: "twitter", status: request.params.refresh ? "ok" : "missing" }] }
          }) + "\\n");
        } else if (request.method === "import_login_sessions") {
          process.stdout.write(JSON.stringify({
            id: request.id,
            event: "done",
            method: request.method,
            result: { imported: [{ source: request.params.source_dir + "/twitter.json", target: "D:/main/sessions/twitter.json" }], skipped: [], ignored: [] }
          }) + "\\n");
        } else if (request.method === "settings_schema") {
          process.stdout.write(JSON.stringify({
            id: request.id,
            event: "done",
            method: request.method,
            result: {
              platforms: [
                { id: "core", name: "基础设置", fields: [{ name: "OUTPUT_DIR", label: "输出目录", type: "path" }] },
                {
                  id: "proxy",
                  name: "代理",
                  fields: [
                    { name: "FEEDGRAB_PROXY_ENABLED", label: "启用代理", type: "boolean", value: "false" }
                  ]
                },
                {
                  id: "discourse",
                  name: "Discourse论坛",
                  fields: [
                    {
                      name: "LINUXDO_REPLY_MODE",
                      label: "回复模式",
                      type: "enum",
                      default: "author",
                      options: [
                        { label: "只看楼主", value: "author" },
                        { label: "全部楼层", value: "all" }
                      ]
                    }
                  ]
                },
                {
                  id: "x",
                  name: "X / Twitter",
                  fields: [
                    { name: "X_SEARCH_SAVE_TWEETS", label: "保存单条推文 Markdown", type: "boolean", default: "true" }
                  ]
                }
              ]
            }
          }) + "\\n");
        } else if (request.method === "settings_update") {
          process.stdout.write(JSON.stringify({
            id: request.id,
            event: "done",
            method: request.method,
            result: { updated: Object.entries(request.params.values).map(([name, value]) => ({ name, value: String(value) })) }
          }) + "\\n");
        } else if (request.method === "settings_snapshot") {
          process.stdout.write(JSON.stringify({
            id: request.id,
            event: "done",
            method: request.method,
            result: {
              items: [
                { name: "OUTPUT_DIR", value: "D:\\\\Notes\\\\Outputs" },
                { name: "OBSIDIAN_VAULT", value: "D:\\\\Notes\\\\Vault" }
              ]
            }
          }) + "\\n");
        } else {
          process.stdout.write(JSON.stringify({
            id: request.id,
            event: "error",
            error: { code: "unexpected_method", message: request.method }
          }) + "\\n");
        }
      });
    `;
    const worker = createPythonWorkerClient({
      command: process.execPath,
      args: ["-e", script],
      env: { FEEDGRAB_INSTALL_SESSIONS_DIR: "D:/installer/sessions" }
    });

    await expect(worker.loginStatus({ refresh: true })).resolves.toEqual([
      expect.objectContaining({ platform: "twitter", status: "connected" })
    ]);
    await expect(worker.importLoginSessions()).resolves.toMatchObject({
      imported: [{ source: "D:/installer/sessions/twitter.json", target: "D:/main/sessions/twitter.json" }]
    });
    await expect(worker.settingsSchema()).resolves.toMatchObject({
      basic: [{ name: "OUTPUT_DIR", label: "输出目录", type: "path" }],
      platforms: [
        {
          id: "proxy",
          fields: [{ name: "FEEDGRAB_PROXY_ENABLED", type: "boolean", value: false }]
        },
        {
          id: "discourse",
          label: "Discourse论坛",
          fields: [{ name: "LINUXDO_REPLY_MODE", type: "select" }]
        },
        {
          id: "x",
          fields: [{ name: "X_SEARCH_SAVE_TWEETS", type: "boolean", defaultValue: true }]
        }
      ]
    });
    await expect(worker.settingsUpdate({ X_SEARCH_DAYS: 7 })).resolves.toMatchObject({
      updated: [{ name: "X_SEARCH_DAYS", value: "7" }]
    });
    await expect(worker.settingsSnapshot()).resolves.toEqual({
      outputDirectory: "D:\\Notes\\Outputs",
      obsidianVault: "D:\\Notes\\Vault",
      effectiveOutputDirectory: "D:\\Notes\\Vault",
      concurrency: 1,
      downloadImages: true,
      localizeMedia: true,
      replyMode: "author"
    });
  });

  it("sends structured fetch targets to the JSONL sidecar protocol", async () => {
    const seen: unknown[] = [];
    const script = `
      const readline = require("node:readline");
      const rl = readline.createInterface({ input: process.stdin });
      rl.on("line", (line) => {
        const request = JSON.parse(line);
        process.stdout.write(JSON.stringify({
          id: request.id,
          event: "done",
          method: request.method,
          result: request.params
        }) + "\\n");
      });
    `;
    const worker = createPythonWorkerClient({
      command: process.execPath,
      args: ["-e", script]
    });
    worker.onEvent((event) => {
      if (event.event === "done") {
        seen.push(event.result);
      }
    });

    const jobs = await worker.startFetch({
      urls: [],
      targets: ["openclaw"],
      platform: "twitter",
      mode: "search",
      commandPreview: 'feedgrab x-so "openclaw"',
      outputDirectory: "D:\\Notes\\Feeds"
    });

    expect(jobs).toEqual([
      expect.objectContaining({
        url: 'feedgrab x-so "openclaw"',
        platform: "twitter",
        mode: "search"
      })
    ]);
    expect(seen[0]).toMatchObject({
      targets: ["openclaw"],
      platform: "twitter",
      mode: "search",
      command_preview: 'feedgrab x-so "openclaw"',
      output_dir: "D:\\Notes\\Feeds"
    });
  });

  it("does not treat a missing installer sessions source as a successful import", async () => {
    const script = `
      const readline = require("node:readline");
      const rl = readline.createInterface({ input: process.stdin });
      rl.on("line", (line) => {
        const request = JSON.parse(line);
        process.stdout.write(JSON.stringify({
          id: request.id,
          event: "done",
          method: request.method,
          result: {
            source_dir: request.params.source_dir,
            imported: [],
            skipped: [],
            disabled: [],
            ignored: [{ source: request.params.source_dir, reason: "source_dir_missing" }]
          }
        }) + "\\n");
      });
    `;
    const worker = createPythonWorkerClient({
      command: process.execPath,
      args: ["-e", script],
      env: { FEEDGRAB_INSTALL_SESSIONS_DIR: "D:/missing/sessions" }
    });

    await expect(worker.importLoginSessions()).resolves.toMatchObject({
      ok: false,
      sourceDirectory: "D:/missing/sessions",
      imported: [],
      error: expect.stringContaining("sessions")
    });
  });

  it("projects saved settings into the login subprocess environment", async () => {
    const tempRoot = mkdtempSync(path.join(tmpdir(), "feedgrab-login-env-"));
    const settingsPath = path.join(tempRoot, "settings.json");
    const outputPath = path.join(tempRoot, "login-env.json");
    writeFileSync(
      path.join(tempRoot, "login"),
      `
        const fs = require("node:fs");
        fs.writeFileSync(${JSON.stringify(outputPath)}, JSON.stringify({
          argv: process.argv.slice(2),
          dataDir: process.env.FEEDGRAB_DATA_DIR || "",
          outputDir: process.env.OUTPUT_DIR || "",
          obsidianVault: process.env.OBSIDIAN_VAULT || ""
        }));
      `,
      "utf8"
    );
    writeFileSync(
      settingsPath,
      JSON.stringify({
        values: {
          FEEDGRAB_DATA_DIR: "\\sessions",
          OUTPUT_DIR: "D:\\Notes\\Feeds"
        }
      }),
      "utf8"
    );

    const worker = createPythonWorkerClient({
      command: process.execPath,
      args: [],
      cwd: tempRoot,
      env: {
        FEEDGRAB_SETTINGS_PATH: settingsPath,
        FEEDGRAB_INSTALL_SESSIONS_DIR: "D:\\feedgrab Desktop\\sessions",
        FEEDGRAB_DATA_DIR: "C:\\Users\\Qiang\\AppData\\Roaming\\feedgrab-desktop\\sessions"
      }
    });

    await expect(worker.loginPlatform("feishu")).resolves.toMatchObject({
      ok: true,
      platform: "feishu"
    });

    const captured = JSON.parse(readFileSync(outputPath, "utf8")) as {
      argv: string[];
      dataDir: string;
      outputDir: string;
      obsidianVault: string;
    };
    expect(captured.argv).toEqual(["feishu"]);
    expect(captured.dataDir).toBe("D:\\feedgrab Desktop\\sessions");
    expect(captured.outputDir).toBe("D:\\Notes\\Feeds");
    expect(captured.obsidianVault).toBe("");
  });

  it("does not project legacy desktop defaults into the login subprocess environment", async () => {
    const tempRoot = mkdtempSync(path.join(tmpdir(), "feedgrab-login-legacy-env-"));
    const settingsPath = path.join(tempRoot, "settings.json");
    const outputPath = path.join(tempRoot, "login-env.json");
    writeFileSync(
      path.join(tempRoot, "login"),
      `
        const fs = require("node:fs");
        fs.writeFileSync(${JSON.stringify(outputPath)}, JSON.stringify({
          dataDir: process.env.FEEDGRAB_DATA_DIR || "",
          outputDir: process.env.OUTPUT_DIR || "",
          obsidianVault: process.env.OBSIDIAN_VAULT || ""
        }));
      `,
      "utf8"
    );
    writeFileSync(
      settingsPath,
      JSON.stringify({
        values: {
          OUTPUT_DIR: "E:\\Obsidian\\Qiang_Obsidian\\inbox",
          OBSIDIAN_VAULT: "E:\\Obsidian\\Qiang_Obsidian\\inbox",
          FEEDGRAB_DATA_DIR: ""
        }
      }),
      "utf8"
    );

    const worker = createPythonWorkerClient({
      command: process.execPath,
      args: [],
      cwd: tempRoot,
      env: {
        FEEDGRAB_SETTINGS_PATH: settingsPath,
        FEEDGRAB_INSTALL_SESSIONS_DIR: "D:\\feedgrab Desktop\\sessions",
        FEEDGRAB_DATA_DIR: "C:\\Users\\Qiang\\AppData\\Roaming\\feedgrab Desktop\\sessions",
        OUTPUT_DIR: "D:\\feedgrab Desktop\\output",
        OBSIDIAN_VAULT: ""
      }
    });

    await expect(worker.loginPlatform("feishu")).resolves.toMatchObject({
      ok: true,
      platform: "feishu"
    });

    const captured = JSON.parse(readFileSync(outputPath, "utf8")) as {
      dataDir: string;
      outputDir: string;
      obsidianVault: string;
    };
    expect(captured.dataDir).toBe("D:\\feedgrab Desktop\\sessions");
    expect(captured.outputDir).toBe("D:\\feedgrab Desktop\\output");
    expect(captured.obsidianVault).toBe("");
  });

  it("projects empty saved desktop output to the install output directory", async () => {
    const tempRoot = mkdtempSync(path.join(tmpdir(), "feedgrab-login-empty-output-env-"));
    const settingsPath = path.join(tempRoot, "settings.json");
    const outputPath = path.join(tempRoot, "login-env.json");
    writeFileSync(
      path.join(tempRoot, "login"),
      `
        const fs = require("node:fs");
        fs.writeFileSync(${JSON.stringify(outputPath)}, JSON.stringify({
          outputDir: process.env.OUTPUT_DIR || ""
        }));
      `,
      "utf8"
    );
    writeFileSync(
      settingsPath,
      JSON.stringify({
        values: {
          OUTPUT_DIR: ""
        }
      }),
      "utf8"
    );

    const worker = createPythonWorkerClient({
      command: process.execPath,
      args: [],
      cwd: tempRoot,
      env: {
        FEEDGRAB_SETTINGS_PATH: settingsPath,
        OUTPUT_DIR: "D:\\feedgrab Desktop\\output"
      }
    });

    await expect(worker.loginPlatform("feishu")).resolves.toMatchObject({
      ok: true,
      platform: "feishu"
    });

    const captured = JSON.parse(readFileSync(outputPath, "utf8")) as { outputDir: string };
    expect(captured.outputDir).toBe("D:\\feedgrab Desktop\\output");
  });

  it("uses runtime env for fallback settings schema when sidecar schema fails", async () => {
    const script = `
      const readline = require("node:readline");
      const rl = readline.createInterface({ input: process.stdin });
      rl.on("line", (line) => {
        const request = JSON.parse(line);
        process.stdout.write(JSON.stringify({
          id: request.id,
          event: "error",
          method: request.method,
          error: { code: "schema_failed", message: "schema failed" }
        }) + "\\n");
      });
    `;
    const worker = createPythonWorkerClient({
      command: process.execPath,
      args: ["-e", script],
      env: {
        OUTPUT_DIR: "D:\\feedgrab Desktop\\output",
        FEEDGRAB_DATA_DIR: "D:\\feedgrab Desktop\\sessions",
        FEEDGRAB_INSTALL_SESSIONS_DIR: "D:\\feedgrab Desktop\\sessions"
      }
    });

    const schema = await worker.settingsSchema();
    const basicFields = Object.fromEntries(schema.basic.map((field) => [field.name, field]));

    expect(basicFields.OUTPUT_DIR?.value).toBe("D:\\feedgrab Desktop\\output");
    expect(basicFields.FEEDGRAB_DATA_DIR?.label).toBe("登录态和数据目录");
    expect(basicFields.FEEDGRAB_DATA_DIR?.value).toBe("D:\\feedgrab Desktop\\sessions");
  });

  it("marks running fetch jobs failed when the sidecar process exits", async () => {
    const script = `
      const readline = require("node:readline");
      const rl = readline.createInterface({ input: process.stdin });
      rl.on("line", (line) => {
        const request = JSON.parse(line);
        process.stdout.write(JSON.stringify({
          id: request.id,
          event: "job_started",
          method: "fetch",
          result: { total: 1 }
        }) + "\\n");
        process.stdout.write(JSON.stringify({
          id: request.id,
          event: "progress",
          method: "fetch",
          url: request.params.urls[0],
          message: "fetching"
        }) + "\\n");
        setTimeout(() => process.exit(7), 10);
      });
    `;
    const worker = createPythonWorkerClient({
      command: process.execPath,
      args: ["-e", script]
    });
    const events: Array<{ id?: string | null; event: string; method?: string }> = [];
    worker.onEvent((event) => events.push(event));

    const jobs = await worker.startFetch({
      urls: ["https://x.com/thinkszyg/status/2061278800491729292"],
      outputDirectory: "D:\\Notes\\Feeds"
    });
    await waitForCondition(() =>
      events.some((event) => event.id === jobs[0]?.id && event.event === "error" && event.method === "fetch")
    );

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: jobs[0]?.id,
          event: "error",
          method: "fetch"
        })
      ])
    );
  });
});
