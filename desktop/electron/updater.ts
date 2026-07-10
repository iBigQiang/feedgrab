import { app, net } from "electron";
import { spawn } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync, readdirSync, renameSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import type { UpdateCheckResult, UpdateDownloadProgress } from "./ipc-types.js";

const RELEASES_API_URL = "https://api.github.com/repos/iBigQiang/feedgrab/releases";
const TAG_VERSION_REGEX = /desktop-v(\d+\.\d+\.\d+)/;
const SETUP_ASSET_PATTERN = /^feedgrab-desktop-setup-.*\.exe$/i;
const GITHUB_API_TIMEOUT_MS = 15000;
const DOWNLOAD_TIMEOUT_MS = 30 * 60 * 1000;

type GitHubReleaseAsset = {
  name: string;
  browser_download_url: string;
  size: number;
};

type GitHubRelease = {
  tag_name: string;
  name: string;
  html_url: string;
  published_at: string;
  body: string;
  assets: GitHubReleaseAsset[];
  prerelease: boolean;
  draft: boolean;
};

function compareVersions(latest: string, current: string): number {
  const latestParts = latest.split(".").map((n) => Number.parseInt(n, 10));
  const currentParts = current.split(".").map((n) => Number.parseInt(n, 10));
  const maxLen = Math.max(latestParts.length, currentParts.length);
  for (let i = 0; i < maxLen; i += 1) {
    const l = latestParts[i] ?? 0;
    const c = currentParts[i] ?? 0;
    if (l > c) return 1;
    if (l < c) return -1;
  }
  return 0;
}

function extractVersionFromTag(tagName: string): string | undefined {
  const match = tagName.match(TAG_VERSION_REGEX);
  return match?.[1];
}

function findSetupAsset(assets: GitHubReleaseAsset[]): GitHubReleaseAsset | undefined {
  return assets.find((a) => SETUP_ASSET_PATTERN.test(a.name));
}

export function isPortableInstallation(): boolean {
  if (!app.isPackaged) return false;
  const exePath = process.execPath;
  const exeName = path.basename(exePath).toLowerCase();
  if (exeName.includes("portable")) return true;
  const appRoot = path.dirname(exePath);
  const uninstallerPath = path.join(appRoot, "Uninstall feedgrab Desktop.exe");
  return !existsSync(uninstallerPath);
}

function fetchJson(url: string, headers: Record<string, string>): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const request = net.request(url);
    for (const [key, value] of Object.entries(headers)) {
      request.setHeader(key, value);
    }
    const timer = setTimeout(() => {
      reject(new Error("GitHub API 请求超时"));
      request.abort();
    }, GITHUB_API_TIMEOUT_MS);

    const chunks: Buffer[] = [];
    request.on("response", (response) => {
      const statusCode = response.statusCode ?? 0;
      if (statusCode === 403) {
        const reset = response.headers["x-ratelimit-reset"];
        const resetTime = reset ? new Date(Number(reset) * 1000).toLocaleTimeString() : "";
        clearTimeout(timer);
        reject(new Error(`GitHub API 限流${resetTime ? `，预计 ${resetTime} 后恢复` : ""}`));
        return;
      }
      if (statusCode !== 200) {
        clearTimeout(timer);
        reject(new Error(`GitHub API 返回 ${statusCode}`));
        return;
      }
      response.on("data", (chunk: Buffer) => chunks.push(chunk));
      response.on("end", () => {
        clearTimeout(timer);
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
        } catch {
          reject(new Error("GitHub API 返回了无法解析的 JSON"));
        }
      });
    });
    request.on("error", (error) => {
      clearTimeout(timer);
      reject(new Error(`网络请求失败：${error.message}`));
    });
    request.end();
  });
}

export async function checkForUpdates(): Promise<UpdateCheckResult> {
  const currentVersion = app.getVersion();
  const portable = isPortableInstallation();
  try {
    const json = await fetchJson(RELEASES_API_URL, {
      "User-Agent": `feedgrab-desktop/${currentVersion}`,
      Accept: "application/vnd.github+json"
    });
    const releases = Array.isArray(json) ? (json as GitHubRelease[]) : [];
    const desktopReleases = releases.filter(
      (r) => !r.draft && !r.prerelease && extractVersionFromTag(r.tag_name)
    );
    if (desktopReleases.length === 0) {
      return {
        hasUpdate: false,
        latestVersion: currentVersion,
        currentVersion,
        downloadUrl: "",
        releasePageUrl: "",
        releaseNotes: "",
        publishedAt: "",
        isPortable: portable
      };
    }
    desktopReleases.sort((a, b) => new Date(b.published_at).getTime() - new Date(a.published_at).getTime());
    const latest = desktopReleases[0];
    const latestVersion = extractVersionFromTag(latest.tag_name) ?? currentVersion;
    const asset = findSetupAsset(latest.assets);
    const hasUpdate = compareVersions(latestVersion, currentVersion) > 0;
    return {
      hasUpdate,
      latestVersion,
      currentVersion,
      downloadUrl: asset?.browser_download_url ?? "",
      releasePageUrl: latest.html_url,
      releaseNotes: latest.body ?? "",
      publishedAt: latest.published_at,
      isPortable: portable
    };
  } catch (error) {
    return {
      hasUpdate: false,
      latestVersion: currentVersion,
      currentVersion,
      downloadUrl: "",
      releasePageUrl: "",
      releaseNotes: "",
      publishedAt: "",
      isPortable: portable,
      error: error instanceof Error ? error.message : "检查更新失败"
    };
  }
}

export function downloadFile(
  url: string,
  destPath: string,
  onProgress: (progress: UpdateDownloadProgress) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = net.request(url);
    request.setHeader("User-Agent", `feedgrab-desktop/${app.getVersion()}`);
    const timer = setTimeout(() => {
      reject(new Error("下载超时"));
      request.abort();
    }, DOWNLOAD_TIMEOUT_MS);

    request.on("response", (response) => {
      const statusCode = response.statusCode ?? 0;
      if (statusCode !== 200) {
        clearTimeout(timer);
        reject(new Error(`下载失败：HTTP ${statusCode}`));
        return;
      }
      const totalHeader = response.headers["content-length"];
      const totalBytes = totalHeader ? Number.parseInt(Array.isArray(totalHeader) ? totalHeader[0] ?? "" : totalHeader, 10) : 0;
      let downloadedBytes = 0;
      const fileStream = createWriteStream(destPath);

      response.on("data", (chunk: Buffer) => {
        downloadedBytes += chunk.length;
        fileStream.write(chunk);
        const percent = totalBytes > 0 ? Math.floor((downloadedBytes / totalBytes) * 100) : 0;
        onProgress({ percent, downloadedBytes, totalBytes });
      });

      response.on("end", () => {
        fileStream.end(() => {
          clearTimeout(timer);
          if (totalBytes > 0 && downloadedBytes !== totalBytes) {
            reject(new Error(`下载不完整：已下载 ${downloadedBytes} / ${totalBytes} bytes`));
            return;
          }
          resolve();
        });
      });

      response.on("error", (error) => {
        clearTimeout(timer);
        fileStream.destroy();
        reject(new Error(`下载中断：${error.message}`));
      });
      fileStream.on("error", (error) => {
        clearTimeout(timer);
        reject(new Error(`文件写入失败：${error.message}`));
      });
    });

    request.on("error", (error) => {
      clearTimeout(timer);
      reject(new Error(`下载请求失败：${error.message}`));
    });
    request.end();
  });
}

function legacyUpdateDownloadDir(): string {
  return path.join(tmpdir(), "feedgrab-desktop-update");
}

export function getUpdateDownloadDir(): string {
  // 打包运行时安装包下载到客户端安装目录下的 update 子目录，便于用户定位
  if (app.isPackaged) {
    const dir = path.join(path.dirname(process.execPath), "update");
    try {
      mkdirSync(dir, { recursive: true });
      return dir;
    } catch {
      // 安装目录不可写时回退到系统临时目录
    }
  }
  const fallback = legacyUpdateDownloadDir();
  mkdirSync(fallback, { recursive: true });
  return fallback;
}

export function cleanupUpdateDownloads(): void {
  // 应用启动时清理上次更新遗留的安装包：安装成功后新版本首次启动即删除；
  // 安装失败/取消的残留也一并清掉，下次更新会重新下载
  const dirs = new Set([legacyUpdateDownloadDir()]);
  if (app.isPackaged) {
    dirs.add(path.join(path.dirname(process.execPath), "update"));
  }
  for (const dir of dirs) {
    if (!existsSync(dir)) continue;
    let entries: string[];
    try {
      entries = readdirSync(dir);
    } catch {
      continue;
    }
    for (const name of entries) {
      if (!/\.(exe|part)$/i.test(name)) continue;
      try { unlinkSync(path.join(dir, name)); } catch { /* 可能仍被安装器占用，留待下次清理 */ }
    }
  }
}

function spawnInstaller(installerPath: string): Promise<void> {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(installerPath, ["/S", "--force-run"], {
        detached: true,
        stdio: "ignore",
        windowsHide: true
      });
    } catch (error) {
      reject(error instanceof Error ? error : new Error(String(error)));
      return;
    }
    // Windows 下 spawn 的失败（如 EFTYPE）通过异步 error 事件抛出，try/catch 捕获不到
    child.once("error", (error) => reject(error));
    child.once("spawn", () => {
      child.unref();
      resolve();
    });
  });
}

export async function downloadAndInstallUpdate(
  downloadUrl: string,
  onProgress: (progress: UpdateDownloadProgress) => void
): Promise<{ ok: boolean; error?: string; installerPath?: string }> {
  if (!downloadUrl) {
    return { ok: false, error: "无效的下载地址" };
  }

  const tempDir = getUpdateDownloadDir();

  const fileName = path.basename(new URL(downloadUrl).pathname) || "feedgrab-desktop-setup.exe";
  const downloadPath = path.join(tempDir, fileName);
  const partPath = `${downloadPath}.part`;

  for (const f of [downloadPath, partPath]) {
    if (existsSync(f)) {
      try { unlinkSync(f); } catch { /* ignore */ }
    }
  }

  try {
    await downloadFile(downloadUrl, partPath, onProgress);
    renameSync(partPath, downloadPath);
  } catch (error) {
    if (existsSync(partPath)) {
      try { unlinkSync(partPath); } catch { /* ignore */ }
    }
    return {
      ok: false,
      error: error instanceof Error ? error.message : "下载失败",
      installerPath: downloadPath
    };
  }

  try {
    await spawnInstaller(downloadPath);
    return { ok: true, installerPath: downloadPath };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      ok: false,
      error: `启动安装器失败：${message}（安装包已保存到 ${downloadPath}，可手动运行安装）`,
      installerPath: downloadPath
    };
  }
}
