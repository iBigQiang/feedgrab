# feedgrab Desktop Guide

**English** | **[中文](https://github.com/iBigQiang/feedgrab/blob/feedgrab-desktop/desktop/README.md)**

feedgrab Desktop is the Windows GUI branch of feedgrab, built for users who prefer not to work in the command line. The client uses an `Electron + Vite + React + TypeScript + Python Sidecar Worker` architecture: the UI handles configuration, login sessions, jobs and output management, while the fetching capability keeps reusing the `feedgrab.service` layer of this repository.

## Client Interface

![feedgrab Desktop fetch workbench](https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/feedgrab-desktop-index.png)

The screenshot above shows the "Fetch Workbench" main view right after the client starts. The left sidebar holds the Fetch, Jobs, Output, Login, Settings, Diagnostics, Sponsor and Community navigation, with the current version and update entry at the bottom. The top of the center panel lists the currently supported platforms with "Auto-detect URL" selected by default; the target input box accepts URLs, keywords, keyword groups and WeChat account names, one per line. Pick an output directory below it and click "Start Fetch". The top right shows running / completed / failed counters, and the right panel streams real-time logs.

## Downloads

- Direct Windows installer: [feedgrab-desktop-setup-0.1.23.exe](https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.23-20260831/feedgrab-desktop-setup-0.1.23.exe)
- Release page: [desktop-v0.1.23-20260831](https://github.com/iBigQiang/feedgrab/releases/tag/desktop-v0.1.23-20260831)
- Desktop client branch: [feedgrab-desktop](https://github.com/iBigQiang/feedgrab/tree/feedgrab-desktop)
- Branch source archive: [feedgrab-desktop.zip](https://github.com/iBigQiang/feedgrab/archive/refs/heads/feedgrab-desktop.zip)

The current preview release ships an installer for regular users:

| File | Audience | Notes |
| --- | --- | --- |
| `feedgrab-desktop-setup-0.1.23.exe` | Regular users | Double-click to install; Start Menu and desktop shortcuts are created automatically. |

This installer was built from the `feedgrab-desktop` branch at `2026-08-31 22:21`, unsigned. Local file SHA256:

```text
8563BCD4344D87CAEDA0F545AD4AA988CCDBBAC9734CB0447E9DF81D8F28B0EF
```

The download URL above comes from the `browser_download_url` field returned by the GitHub API and was verified with `curl.exe -I -L`, returning `200 OK` with a file size of `381046375` bytes.

Developer and portable builds can be produced locally with `npm run pack:dev` or `npm run pack:all`. Every new official installer bumps the desktop patch version so that multiple installers never report the same version.

## Install and Launch

1. Download `feedgrab-desktop-setup-0.1.23.exe` from GitHub Releases.
2. Run the installer and choose an installation directory.
3. Launch `feedgrab Desktop` from the desktop shortcut or the Start Menu.
4. On first launch, open the "Diagnostics" page and confirm the status of Python, the feedgrab package, Playwright/Patchright, Chromium, Node.js, Electron, the output directory and the session directory.

If something is missing, the Diagnostics page offers an "Install/Update" entry, and "Install/Update all dependencies" can fix everything in one pass. The regular installer tries to bundle the Python worker and browser runtime it needs, so users do not have to configure a command-line environment by hand.

Diagnostics also includes a "Proxy connectivity" check: it reports "Proxy disabled" when the proxy is off, and helps identify unreachable proxies or network timeouts once the proxy is enabled.

## Initial Configuration

Open the "Settings" page and review these basics first:

- `Output directory`: where fetched results are saved. The installed build defaults to `output` under the installation root, for example `D:\feedgrab Desktop\output`; you can also point it at an Obsidian inbox or a dedicated vault directory.
- `Obsidian Vault`: optional but higher priority; empty after installation, and once filled it becomes the preferred Markdown output root.
- `Session and data directory`: where cookie/session data is stored. The installed build defaults to `sessions` under the installation root, for example `D:\feedgrab Desktop\sessions`. The installer ships blank template files only; no real credentials are bundled.
- `Browser User-Agent`: detected from the current environment by default, and can be overridden manually.
- `Chrome CDP port`: `9222` by default; the client tries to detect and start an available port automatically.
- `Enable proxy` / `Proxy address` / `No-proxy list`: disabled by default. The proxy address supports `http://127.0.0.1:7890`, `socks5://127.0.0.1:7890` and `http://user:password@ip:port`; passwords are masked in the UI and logs. The no-proxy list defaults to `127.0.0.1,localhost` so that the local worker, the CDP port and internal client services are not disturbed by the proxy.

After proxy settings are saved, the client injects `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY` into the Python sidecar worker, and applies them to Python HTTP fetching, newly launched Playwright browsers and the online sponsor/community documents. If you reuse Chrome via CDP, the client does not force a proxy onto Chrome and instead inherits the proxy or VPN state of the Chrome instance you already opened.

"Basic settings" also exposes a global "Prefer extracting sessions from Chrome CDP when logging in" switch. When enabled, clicking "Log in" on any platform extracts cookies from the running Chrome first, which suits single-account use. When disabled, clicking "Log in" on a specific platform opens an isolated login window where you sign in manually and keep the window open until the client confirms the session was saved, which suits storing multiple accounts. Platform-level parameters still live under "Settings → Platform settings", grouped by X/Twitter, Xiaohongshu, WeChat Official Accounts, Discourse forums, document platforms, video/podcast, Zhihu, Telegram, RSS, arbitrary web pages, zsxq, media/API and more.

## Sessions

The client supports three session sources:

1. Detecting sessions already saved by the client.
2. Importing JSON cookie files from the installation directory or a local `sessions` directory.
3. Clicking "Log in" for a single platform: with "Prefer extracting sessions from Chrome CDP when logging in" enabled, the client pulls the session straight from the running Chrome; otherwise it opens an isolated login window, waits for you to finish logging in, and tells you when the session has been saved so you know when to close the window.

The installer no longer writes templates directly into the installation root. Instead `desktop/session-templates/` is packaged as `extraResources`, and templates are filled into `FEEDGRAB_DATA_DIR` at startup:

```text
FEEDGRAB_DATA_DIR/
  x_2.json
  x_3.json
  wechat.json
  xhs.json
  linuxdo.json
  ...
```

The templates contain the blank field structure only, never real cookies. Existing files with the same name are skipped at startup so your saved sessions are never overwritten. The installed build still defaults the session directory to the `installation directory\sessions` that `FEEDGRAB_DATA_DIR` points at, and you can move it elsewhere from the Settings page. Real cookies, tokens and API keys are never displayed in plain text in the UI.

During a normal uninstall the uninstaller asks whether to keep `output` and `sessions`. If you choose to keep them, or run a silent uninstall or an upgrade, the uninstaller preserves `output` and `sessions` in place along with the parent path structure, for example `D:\feedgrab Desktop\output` and `D:\feedgrab Desktop\sessions`. Reinstalling to the same path skips existing session and output files instead of overwriting them with blank templates.

## Basic Fetch Flow

1. Open the "Fetch" page.
2. Enter your target in "Fetch target (URL / keyword / keyword group / account)".
   - For an `http(s)` URL, the client keeps auto-detecting the platform and fetches by URL.
   - For a keyword or keyword group, first activate a platform button above, such as X / Twitter, Xiaohongshu, YouTube or Zhihu.
   - For a WeChat Official Account name, the first version defaults to account-level batch fetching.
3. Confirm the "Output directory" and click "Select" to change it if needed.
4. Click "Start Fetch".
5. Watch execution in the "Real-time logs" panel on the right.
6. Open the "Jobs" page to see the running, completed or failed status of each URL.
7. Open the "Output" page to open the Markdown files produced during this client session.

The Output page only lists records produced since the client started; it never scans or deletes Markdown files already present in the output directory. "Clear records" only clears the in-app list and does not delete files on disk.

## Main Pages

| Page | Purpose |
| --- | --- |
| Fetch | Enter URLs, keywords, keyword groups or WeChat accounts, choose an output directory and submit a fetch job. |
| Jobs | Track the status of each URL. |
| Output | Browse and quickly open the Markdown produced during this run. |
| Login | Detect, import and create sessions for each platform. |
| Settings | Manage basic settings and platform parameters. |
| Diagnostics | Check core dependencies, browsers, directory permissions and worker status. |
| Sponsor | Load the sponsorship notes online, falling back to the bundled document. |
| Community | Load the community notes online, falling back to the bundled document. |

## Supported Platforms

The client UI lists the currently supported platform categories, including:

- X / Twitter
- WeChat Official Accounts
- Xiaohongshu
- YouTube
- Bilibili
- GitHub
- Reddit
- Discourse forums
- Document platforms: Feishu, KDocs, FlowUs, Youdao Note and more
- Video and podcast: YouTube, Bilibili, Xiaoyuzhou, Ximalaya and more
- Zhihu
- Telegram
- RSS
- zsxq
- Paywalled news
- Arbitrary web pages

Platforms differ in what they need from sessions, API keys and the browser environment. If a platform fails to fetch, check the "Login" and "Diagnostics" pages first.

## Reddit Support

Reddit uses a conservative multi-backend route: direct `.json` plus saved cookies first, then Chrome CDP / Playwright sessions, with Jina kept as the final fallback. Session status is no longer inferred from the mere presence of `reddit.json`; diagnostics validate it against `https://www.reddit.com/api/me.json` so that guest cookies are not mistaken for a signed-in session.

Common commands:

```powershell
feedgrab doctor reddit
feedgrab login reddit
feedgrab reddit-so "codex" --sort comments --time all --limit 50
feedgrab reddit-sub ChatGPT --sort hot --limit 50
```

`feedgrab login reddit` prefers reusing or launching a regular Chrome/CDP manual login. Reddit does not support `--headless` automated login, which keeps the risk of "suspicious login" blocks low.

Key configuration:

- `REDDIT_REPLY_MODE=top|tree|all`: defaults to `top`, rendering top-level comments only; `tree` keeps the nested replies from the initial response; `all` additionally calls `/api/morechildren` to expand more comments.
- `REDDIT_MAX_PAGES=5`: maximum number of `after` cursor pages for `reddit-so` / `reddit-sub`.
- `REDDIT_RETRY_ATTEMPTS=3`: retry count when direct `.json` hits `429 Retry-After`, 5xx or network errors.
- `REDDIT_MORECHILDREN_ROUNDS=2`: only effective in `all` mode, limiting `/api/morechildren` expansion rounds.
- `REDDIT_MAX_COMMENTS=50`: maximum comments rendered per post.
- `REDDIT_DOWNLOAD_MEDIA=false`: keeps online image URLs by default; set to `true` to download images from posts and comments into `attachments/{item_id}/` next to the note and rewrite them as relative paths, which suits long-term offline archiving.

Images in posts and comments are embedded with Markdown image syntax and render directly in Obsidian preview mode; non-image links stay as regular links. Notes fetched by older versions are not rewritten automatically and need to be fetched again to get embedded images.

## Development Setup

Developers can start the client from the branch source:

```powershell
cd D:\AiCode\feedgrab\desktop
npm install
npm run dev
```

`npm run dev` only starts the Vite web preview. To test the real Electron client, run the provided dev launch script or the Electron start command so that the main process, preload, Python sidecar worker and renderer all run together.

Common checks:

```powershell
cd D:\AiCode\feedgrab\desktop
npm run typecheck
npm test
npm run build
```

## Local Packaging

Common packaging command on Windows:

```powershell
cd D:\AiCode\feedgrab\desktop
npm run pack:user
```

Package every variant:

```powershell
cd D:\AiCode\feedgrab\desktop
npm run pack:all
```

Build artifacts go to:

```text
D:\AiCode\feedgrab\desktop\release-packages\
```

Packaging builds the frontend, the Electron main process and the Python worker runtime, then copies the required static assets and blank session templates.

## FAQ

### The worker is not connected after launch

Open the "Diagnostics" page first and check Python, the feedgrab package and the worker status. In a development environment, make sure dependencies are installed and that you started from the `desktop` directory.

### The web page differs from the installed client

`http://127.0.0.1:5173/` in a browser is the Vite web preview, not the full Electron client. For file pickers, opening paths, the worker, session import and runtime diagnostics, always trust the Electron client.

### Nothing changed after importing sessions

Verify that the JSON files in the imported directory have the correct structure and that real sessions are not mixed with blank templates. After importing, click "Re-detect" and check the valid, expired and abnormal account counts for each platform.

### The fetch succeeded but there is no output

Check that the "Output directory" is correct and look at the artifact path in the job log. The Output page only shows records produced since the client started, which is not the full history of the output directory.

### The installer contains no real cookies

That is expected. The installer only ships blank templates and never includes the developer's local cookies, tokens, API keys or any user privacy data.
