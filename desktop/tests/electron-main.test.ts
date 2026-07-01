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

  it("asks whether closing the main window should minimize to tray or quit", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");

    expect(mainSource).toContain("function handleMainWindowClose");
    expect(mainSource).toContain("showMessageBoxSync");
    expect(mainSource).toContain("最小化到托盘");
    expect(mainSource).toContain("直接退出");
    expect(mainSource).toContain("window.hide()");
    expect(mainSource).toContain("new Tray");
    expect(mainSource).toContain('app.on("before-quit"');
  });

  it("ensures Chrome CDP before platform login only when the global login CDP setting is enabled", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");

    expect(mainSource).toContain("async function ensureChromeCdpForLogin");
    expect(mainSource).toContain("await ensureChromeCdpForLogin()");
    expect(mainSource).toContain("settings.CHROME_CDP_LOGIN");
    expect(mainSource).not.toContain("loginCdpSettingName(platform)");
    expect(mainSource).not.toContain("LOGIN_CDP_ENABLED");
  });

  it("allows Reddit in main-process platform validation", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");
    const start = mainSource.indexOf("function isSupportedPlatform");
    const end = mainSource.indexOf("function rememberOpenRoot", start);
    const isSupportedPlatformSource = mainSource.slice(start, end);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    expect(isSupportedPlatformSource).toContain('"reddit"');
  });

  it("validates structured fetch options as flat primitive values", () => {
    const mainSource = readFileSync(join(process.cwd(), "electron", "main.ts"), "utf8");
    const start = mainSource.indexOf("function isValidFetchRequest");
    const end = mainSource.indexOf("function isValidSettingsUpdate", start);
    const isValidFetchRequestSource = mainSource.slice(start, end);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    expect(isValidFetchRequestSource).toContain("request.options");
    expect(isValidFetchRequestSource).toContain("isValidSettingsUpdate(request.options)");
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
    const redditTemplate = readFileSync(join(process.cwd(), "session-templates", "reddit.json"), "utf8");
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
    expect(JSON.parse(redditTemplate)).toEqual({ cookies: [], origins: [] });
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
    expect(installerSource).not.toContain("IDYES +2");
    expect(installerSource).toContain("IDYES keepInstallData");
    expect(installerSource).toContain("keepInstallData:");
    expect(installerSource).toContain("Var /GLOBAL deleteAppData");
    expect(installerSource).toContain("是否同时删除客户端设置与缓存目录");
    expect(installerSource).toContain("$APPDATA\\feedgrab-desktop");
    expect(installerSource).toContain("IDYES deleteAppData");
    expect(installerSource).toContain("deleteAppData:");
    expect(installerSource).toContain('StrCpy $deleteAppData "0"');
    expect(installerSource).not.toContain("installDataBackupRoot");
    expect(installerSource).not.toContain("feedgrab-uninstall-backup");
    expect(installerSource).not.toContain('Rename "$INSTDIR\\output"');
    expect(installerSource).not.toContain('Rename "$INSTDIR\\sessions"');

    const uninitStart = installerSource.indexOf("!macro customUnInit");
    const uninitEnd = installerSource.indexOf("!macroend", uninitStart);
    const uninitSource = installerSource.slice(uninitStart, uninitEnd);
    const messageBoxAt = uninitSource.indexOf("MessageBox ");
    const yesLabelAt = uninitSource.indexOf("keepInstallData:", messageBoxAt);
    const skipLabelAt = uninitSource.indexOf("skipKeep:", yesLabelAt);
    const yesBranch = uninitSource.slice(yesLabelAt, skipLabelAt);

    expect(uninitStart).toBeGreaterThanOrEqual(0);
    expect(uninitEnd).toBeGreaterThan(uninitStart);
    expect(messageBoxAt).toBeGreaterThanOrEqual(0);
    expect(yesLabelAt).toBeGreaterThan(messageBoxAt);
    expect(skipLabelAt).toBeGreaterThan(yesLabelAt);
    expect(uninitSource).toMatch(/MessageBox[^\r\n]*\bIDYES keepInstallData\b/);
    expect(yesBranch).toContain('StrCpy $keepInstallData "1"');
    expect(yesBranch).not.toContain('StrCpy $keepInstallData "0"');

    const appDataMessageBoxAt = uninitSource.indexOf("是否同时删除客户端设置与缓存目录", skipLabelAt);
    const deleteLabelAt = uninitSource.indexOf("deleteAppData:", appDataMessageBoxAt);
    const skipAppDataLabelAt = uninitSource.indexOf("skipAppData:", deleteLabelAt);
    const deleteAppDataBranch = uninitSource.slice(deleteLabelAt, skipAppDataLabelAt);

    expect(appDataMessageBoxAt).toBeGreaterThan(skipLabelAt);
    expect(deleteLabelAt).toBeGreaterThan(appDataMessageBoxAt);
    expect(skipAppDataLabelAt).toBeGreaterThan(deleteLabelAt);
    expect(uninitSource).toMatch(/MessageBox[^\r\n]*\bIDYES deleteAppData\b/);
    expect(deleteAppDataBranch).toContain('StrCpy $deleteAppData "1"');
    expect(deleteAppDataBranch).not.toContain('StrCpy $deleteAppData "0"');

    const removeStart = installerSource.indexOf("!macro customRemoveFiles");
    const removeEnd = installerSource.indexOf("!macroend", removeStart);
    const removeSource = installerSource.slice(removeStart, removeEnd);
    const keepStart = removeSource.indexOf('${if} $keepInstallData == "1"');
    const keepEnd = removeSource.indexOf("${else}", keepStart);
    const keepBranch = removeSource.slice(keepStart, keepEnd);

    expect(removeStart).toBeGreaterThanOrEqual(0);
    expect(removeEnd).toBeGreaterThan(removeStart);
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

    const appDataDeleteStart = removeSource.indexOf('${if} $deleteAppData == "1"');
    const appDataDeleteEnd = removeSource.indexOf("${endif}", appDataDeleteStart);
    const appDataDeleteBranch = removeSource.slice(appDataDeleteStart, appDataDeleteEnd);

    expect(appDataDeleteStart).toBeGreaterThan(keepEnd);
    expect(appDataDeleteEnd).toBeGreaterThan(appDataDeleteStart);
    expect(appDataDeleteBranch).toContain('RMDir /r "$APPDATA\\feedgrab-desktop"');
  });
});
