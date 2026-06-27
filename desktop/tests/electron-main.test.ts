import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

describe("electron main window chrome", () => {
  it("disables the default application menu on Windows and Linux", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");

    expect(mainSource).toContain("Menu.setApplicationMenu(null)");
    expect(mainSource).toContain('process.platform !== "darwin"');
  });

  it("uses bundled Windows icons for the window and packaged shortcuts", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");
    const builderSource = readFileSync(join(process.cwd(), "electron-builder.yml"), "utf8");

    expect(mainSource).toContain("docs\", \"feedgrab-icons\", \"windows\", \"app.ico");
    expect(mainSource).toContain("docs\", \"feedgrab-icons\", \"windows\", \"Square44x44Logo.png");
    expect(mainSource).toContain("nativeImage.createFromPath");
    expect(mainSource).toContain('app.setAppUserModelId("com.feedgrab.desktop")');
    expect(mainSource).toContain("icon: loadAppWindowIcon()");
    expect(builderSource).toContain("from: ../docs/feedgrab-icons/windows/app.ico");
    expect(builderSource).toContain("to: app.ico");
    expect(builderSource).toContain("from: ../docs/feedgrab-icons/windows/Square44x44Logo.png");
    expect(builderSource).toContain("to: Square44x44Logo.png");
    expect(builderSource).toContain("icon: ../docs/feedgrab-icons/windows/app.ico");
  });

  it("exposes Chrome CDP startup through IPC", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");

    expect(mainSource).toContain('ipcMain.handle("feedgrab:ensureChromeCdp"');
    expect(mainSource).toContain("--remote-debugging-port=");
    expect(mainSource).toContain("chrome-cdp-profile");
  });

  it("keeps installer session templates as a read-only import source", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");
    const start = mainSource.indexOf("function ensureRuntimeDirectories");
    const end = mainSource.indexOf("function smokeLog");
    const ensureRuntimeDirectoriesSource = mainSource.slice(start, end);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    expect(ensureRuntimeDirectoriesSource).not.toContain("FEEDGRAB_INSTALL_SESSIONS_DIR");
  });

  it("keeps remote markdown fetching behind an allowlist", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");

    expect(mainSource).toContain("allowedRemoteMarkdownUrls");
    expect(mainSource).toContain('ipcMain.handle("feedgrab:fetchRemoteMarkdown"');
    expect(mainSource).toContain("https://edgeone.gh-proxy.com/https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/sponsor.md");
    expect(mainSource).toContain("https://edgeone.gh-proxy.com/https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/group.md");
    expect(mainSource).toContain("if (!allowedRemoteMarkdownUrls.has(url))");
  });
});
