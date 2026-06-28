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

  it("ensures Chrome CDP before platform login when CDP login is enabled", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");

    expect(mainSource).toContain("async function ensureChromeCdpForLogin");
    expect(mainSource).toContain("await ensureChromeCdpForLogin()");
    expect(mainSource).toContain("CHROME_CDP_LOGIN");
  });

  it("does not create a separate install sessions target outside FEEDGRAB_DATA_DIR", () => {
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

  it("ships session-templates as extraResources and excludes them from extraFiles sessions", () => {
    const builderSource = readFileSync(join(process.cwd(), "electron-builder.yml"), "utf8");
    const resourcesStart = builderSource.indexOf("extraResources:");
    const resourcesEnd = builderSource.indexOf("asar:", resourcesStart);
    expect(resourcesStart).toBeGreaterThanOrEqual(0);
    expect(resourcesEnd).toBeGreaterThan(resourcesStart);

    const extraResources = builderSource.slice(resourcesStart, resourcesEnd);
    expect(extraResources).toContain("- from: session-templates");
    expect(extraResources).toContain("to: session-templates");
    expect(builderSource).not.toContain("extraFiles:");
    expect(builderSource).toContain("nsis:");
    expect(builderSource).toContain("include: build-resources/installer.nsh");
  });

  it("ensures session templates are filled into FEEDGRAB_DATA_DIR with no overwrite on startup", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");

    expect(mainSource).toContain("function synchronizeSessionTemplates");
    expect(mainSource).toContain("const templateSourceCandidates");
    expect(mainSource).toContain("ensureRuntimeDirectories(runtime)");
    expect(mainSource).toContain("process.resourcesPath");
    expect(mainSource).toContain("if (!entry.isFile() || existsSync(targetPath))");
    expect(mainSource).toContain("synchronizeSessionTemplates(runtime.env.FEEDGRAB_DATA_DIR)");
  });

  it("adds NSIS uninstall protection for install data and prompts on regular uninstall", () => {
    const installerSource = readFileSync(join(process.cwd(), "build-resources", "installer.nsh"), "utf8");

    expect(installerSource).toContain("!macro customUnInit");
    expect(installerSource).toContain("!macro customRemoveFiles");
    expect(installerSource).toContain("${if} ${Silent}");
    expect(installerSource).not.toContain("${if} ${isUpdated}");
    expect(installerSource).toContain("MessageBox MB_ICONQUESTION|MB_YESNO|MB_DEFBUTTON2");
    expect(installerSource).not.toContain("installDataBackupRoot");
    expect(installerSource).not.toContain("feedgrab-uninstall-backup");
    expect(installerSource).not.toContain('Rename "$INSTDIR\\output"');
    expect(installerSource).not.toContain('Rename "$INSTDIR\\sessions"');

    const keepStart = installerSource.indexOf('${if} $keepInstallData == "1"');
    const keepEnd = installerSource.indexOf("${else}", keepStart);
    const keepBranch = installerSource.slice(keepStart, keepEnd);

    expect(keepStart).toBeGreaterThanOrEqual(0);
    expect(keepEnd).toBeGreaterThan(keepStart);
    expect(keepBranch).toContain('Delete "$INSTDIR\\${APP_EXECUTABLE_FILENAME}"');
    expect(keepBranch).toContain('Delete "$INSTDIR\\${UNINSTALL_FILENAME}"');
    expect(keepBranch).toContain('RMDir /r "$INSTDIR\\resources"');
    expect(keepBranch).toContain('RMDir /r "$INSTDIR\\locales"');
    expect(keepBranch).not.toContain('RMDir /r "$INSTDIR"');
    expect(keepBranch).not.toContain('RMDir "$INSTDIR"');
    expect(keepBranch).not.toContain("$INSTDIR\\output");
    expect(keepBranch).not.toContain("$INSTDIR\\sessions");
  });
});
