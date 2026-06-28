# feedgrab Desktop 中文使用说明

> 适用分支：`feedgrab-desktop`
> 文档日期：2026-06-28
> 当前状态：Windows 安装器预览版。已提供 `feedgrab-desktop` 分支安装包，尚未提供代码签名、自动更新、真实授权激活或应用商店发布包。

`feedgrab Desktop` 是 feedgrab 的桌面 GUI 客户端分支，技术栈为：

```text
Electron + Vite + React + TypeScript
  -> Electron preload typed IPC
  -> Python Sidecar Worker (stdio JSON Lines)
  -> feedgrab.service
  -> UniversalReader / fetchers / storage
```

桌面客户端不重写 Python 抓取核心，也不解析 CLI 文本输出。它通过 Electron main 启动 `python -m feedgrab.worker`，再由 Python sidecar worker 调用 `feedgrab.service` 和现有抓取器，最终生成与 CLI 兼容的 Markdown、附件和去重索引。

## 1. 当前能做什么

当前 GUI 已包含 7 个页面：

| 页面 | 当前能力 |
|---|---|
| 抓取 | 粘贴单个或多个 `http(s)` 链接，选择输出目录，启动抓取，查看平台识别标签和实时日志 |
| 任务 | 查看任务状态；状态包括排队、运行中、完成、失败、已取消；运行中任务可取消 |
| 输出 | 查看已生成的 Markdown 产物，并通过受限路径白名单打开文件 |
| 登录 | 读取部分平台 session 状态；不显示 Cookie 原文 |
| 设置 | 展示输出目录、并发上限、图片本地化、回复模式；当前 GUI 内主要可改输出目录 |
| 诊断 | 展示 Python、浏览器、网络、输出目录可写性等基础诊断信息 |
| 授权 | 商业化占位页；当前不接入真实支付、License 激活或远程授权服务 |

需要特别注意：当前桌面端已具备登录状态检测/导入、平台登录入口、安装器打包和基础设置页，但还没有自动更新、授权服务器、完整 API Key 编辑器、Cookie 编辑器、输出库搜索/删除/导出等完整商业化能力。需要登录的平台可通过客户端登录页或既有 CLI/session 流程准备登录态。

## 1.1 Windows 安装包预览

当前预览安装包发布在 GitHub Releases：

- 发布页：<https://github.com/iBigQiang/feedgrab/releases/tag/desktop-v0.1.1-20260628>
- Windows 安装包：<https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.1-20260628/feedgrab-desktop-setup-0.1.1.exe>
- SHA256：`92F661F9811283674EB69944A90B2E3ED792C8480AE71A287973B05FFFFC792D`

安装包由 `feedgrab-desktop` 分支构建，内置 Python sidecar worker、Playwright Chromium runtime 和空白 session 模板。安装包未做代码签名，Windows 首次运行可能出现安全提示。

## 2. 下载源码

### 2.1 远端已有 `feedgrab-desktop` 分支时

```powershell
git clone --branch feedgrab-desktop https://github.com/iBigQiang/feedgrab.git
cd feedgrab
git branch --show-current
```

最后一行应输出：

```text
feedgrab-desktop
```

### 2.2 已经有本地仓库时

```powershell
cd D:\AiCode\feedgrab
git fetch origin
git switch feedgrab-desktop
git branch --show-current
```

如果远端尚未发布 `feedgrab-desktop` 分支，就不能只靠 `pip install feedgrab` 获得桌面客户端。桌面客户端需要完整源码中的 `desktop/` 目录。

## 3. 安装基础环境

### 3.1 必需环境

| 环境 | 建议 |
|---|---|
| Windows | Windows 10 / 11 |
| Git | 用于克隆和切换分支 |
| Python | `>= 3.10`；本分支当前在 Python 3.12 环境验证过 |
| Node.js / npm | 建议 Node.js 20 LTS 或 22 LTS；Vite/Electron 构建需要 npm |
| Chromium 浏览器内核 | 由 `patchright install chromium` 或 `playwright install chromium` 安装 |

先检查版本：

```powershell
git --version
python --version
node --version
npm --version
```

如果 PowerShell 禁止激活虚拟环境，可以只在当前窗口临时放开：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 3.2 安装 Python 后端依赖

推荐在仓库根目录创建 `.venv`，并在同一个 PowerShell 窗口中启动 Electron。这样 Electron sidecar worker 会使用这个虚拟环境里的 `python`。

```powershell
cd D:\AiCode\feedgrab
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[all]"
patchright install chromium
```

如果 `patchright install chromium` 不可用，可改用 Playwright 浏览器安装：

```powershell
python -m playwright install chromium
```

`".[all]"` 会安装 feedgrab 的主要可选依赖，包括 Telegram、MCP、Playwright、patchright、browserforge、curl_cffi、X/Twitter transaction id、微信 Markdown 解析、小红书 API、飞书 Open API 等。它不包含所有系统级工具，例如 `ffmpeg`。

### 3.3 可选的视频和音频依赖

如果只抓取普通网页、GitHub、Discourse、微信公众号文章等，通常不需要额外安装这些依赖。若要使用 YouTube 下载、音频转录或 Whisper 兜底能力，再安装：

```powershell
python -m pip install yt-dlp
```

`ffmpeg` 需要系统安装。Windows 用户可用自己习惯的方式安装，例如从 ffmpeg 官网下载，或使用 winget/chocolatey 等包管理器。

Whisper 转录需要 Groq API Key：

```powershell
$env:GROQ_API_KEY = "your_groq_key"
```

### 3.4 安装桌面端依赖

仓库内已有 `desktop/package-lock.json`，推荐优先用 `npm ci` 做可复现安装：

```powershell
cd D:\AiCode\feedgrab\desktop
npm ci
```

如果 `npm ci` 因本地 npm 版本或锁文件状态报错，再用：

```powershell
npm install
```

如果 Windows 的 `npm` shim 异常，可改用 Node 安装目录或 npm 实际路径下的 `npm.cmd`。

## 4. 基础配置

### 4.1 输出目录

安装版首次启动时，GUI 默认把“输出目录”指向主程序安装目录下的 `output` 子目录，例如 `D:\feedgrab Desktop\output`。用户也可以在“抓取”或“设置”页点击“选择”，改成 Obsidian 收件箱或其他资料库目录。桌面端会把这个路径作为本次抓取的 `OUTPUT_DIR` 传给 worker，不会自动改写 `.env`。

“Obsidian Vault”安装初始留空；只有用户明确填写后，才会作为高优先级 Markdown 输出根目录。

CLI 路径仍遵循原规则：

1. `OBSIDIAN_VAULT` 优先。
2. 其次使用 `OUTPUT_DIR`。
3. 两者都没有时，CLI 抓取会提示未配置输出目录。

PowerShell 临时设置示例：

```powershell
$env:OUTPUT_DIR = "D:\feedgrab-output"
```

### 4.2 session / Cookie 目录

CLI 默认 session 目录是仓库根目录下的 `sessions/`。桌面客户端分两种情况：

- 开发环境：默认从 `D:\AiCode\feedgrab\desktop\sessions` 导入本机登录态。
- 安装版：安装目录根部会带一个 `sessions` 子目录，里面来自仓库的 `desktop/session-templates` 空白模板，用户可以手动填写或通过登录流程生成真实登录态。

需要改运行数据目录时，在启动 CLI 或桌面端前设置：

```powershell
$env:FEEDGRAB_DATA_DIR = "D:\feedgrab-sessions"
```

注意：仓库不会提交真实 Cookie。`desktop/session-templates` 只保存空白 JSON 模板；GUI 导入时会忽略空白模板，避免把模板当作有效登录态，也避免误停用用户已有账号。

### 4.3 User-Agent

推荐先用 CLI 检测本机 Chrome UA：

```powershell
feedgrab detect-ua
```

当前 CLI 会读取 `.env`，但桌面 sidecar worker 主要读取进程环境变量。若 GUI 抓取也需要指定 UA，请在启动 Electron 前显式设置：

```powershell
$env:BROWSER_USER_AGENT = "Mozilla/5.0 ..."
```

### 4.4 常用 API Key 和环境变量

按需在启动前设置：

```powershell
$env:GITHUB_TOKEN = "your_github_token"
$env:YOUTUBE_API_KEY = "your_youtube_api_key"
$env:FEISHU_APP_ID = "your_feishu_app_id"
$env:FEISHU_APP_SECRET = "your_feishu_app_secret"
$env:TWITTERAPI_IO_KEY = "your_twitterapi_io_key"
$env:TG_API_ID = "your_telegram_api_id"
$env:TG_API_HASH = "your_telegram_api_hash"
```

`.env.example` 中有完整配置项说明。当前桌面 GUI 尚未提供完整 `.env` 编辑器，所以关键变量建议在启动桌面端的 PowerShell 中设置。

## 5. 自检

### 5.1 Python 后端自检

在仓库根目录、虚拟环境已激活的 PowerShell 中运行：

```powershell
cd D:\AiCode\feedgrab
.\.venv\Scripts\Activate.ps1

python -m feedgrab.cli
python -c "from feedgrab.service import FetchService; print('service import ok')"
python -c "import mcp_server; print('mcp import ok')"
```

检查 worker 协议：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
@'
{"id":"smoke_ping","method":"ping","params":{}}
'@ | python -m feedgrab.worker
```

正常情况下会看到两类 JSON 行：

- `ready`：worker 已启动，并列出支持的方法。
- `done` / `ping` / `pong=true`：ping 请求成功。

### 5.2 CLI 诊断

```powershell
feedgrab doctor
feedgrab doctor x
feedgrab doctor xhs
feedgrab doctor mpweixin
feedgrab doctor feishu
```

### 5.3 桌面端构建自检

```powershell
cd D:\AiCode\feedgrab\desktop
npm run typecheck
npm run lint
npm run test
npm run build
```

当前 `desktop/package.json` 只有这些脚本：

- `dev`：启动 Vite renderer dev server。
- `pack:dev`：构建开发者 portable `.exe`。
- `pack:user`：构建普通用户 NSIS 安装器。
- `pack:all`：同时构建 portable 和 NSIS 安装器。
- `typecheck`：检查 renderer 和 Electron TypeScript。
- `lint`：运行 ESLint。
- `test`：运行 Vitest。
- `build`：构建 renderer 和 Electron main/preload。

当前没有 `npm start`、`electron:dev`、`make` 或自动更新发布脚本。安装器产物默认输出到 `desktop\release-packages\yyyyMMdd-HHmmss\`，该目录不提交到源码分支，正式下载走 GitHub Release asset。

### 5.4 截图 smoke

构建完成后可让 Electron 自动打开指定页面、截图并退出：

```powershell
cd D:\AiCode\feedgrab\desktop
$env:FEEDGRAB_DESKTOP_MOCK = "true"
$env:FEEDGRAB_DESKTOP_SMOKE_LOG = "true"
$env:FEEDGRAB_DESKTOP_SCREENSHOT_VIEW = "fetch"
$env:FEEDGRAB_DESKTOP_SCREENSHOT = "D:\AiCode\feedgrab\output_smoke\desktop-views\fetch.png"
$env:FEEDGRAB_DESKTOP_SCREENSHOT_DELAY_MS = "1200"
.\node_modules\.bin\electron.cmd .
```

`FEEDGRAB_DESKTOP_SCREENSHOT_VIEW` 支持：

```text
fetch, jobs, output, login, settings, doctor, auth
```

## 6. 启动桌面客户端

### 6.1 推荐：构建版启动

```powershell
cd D:\AiCode\feedgrab
.\.venv\Scripts\Activate.ps1

cd desktop
npm run build
.\node_modules\.bin\electron.cmd .
```

重点：必须在启动 Electron 的同一个 PowerShell 中激活 `.venv`，否则 Electron 会调用系统 PATH 中的 `python`。如果系统 Python 没安装 feedgrab，worker 会启动失败。

### 6.2 开发调试启动

第一个 PowerShell：启动 Vite renderer。

```powershell
cd D:\AiCode\feedgrab\desktop
npm run dev
```

第二个 PowerShell：启动 Electron，并指向本地 Vite 地址。

```powershell
cd D:\AiCode\feedgrab
.\.venv\Scripts\Activate.ps1

cd desktop
$env:ELECTRON_RENDERER_URL = "http://127.0.0.1:5173"
.\node_modules\.bin\electron.cmd .
```

`ELECTRON_RENDERER_URL` 只允许 `http://127.0.0.1`、`http://localhost` 或 `http://[::1]`。如果改动了 Electron main/preload 代码，需要重新运行：

```powershell
npm run build
```

### 6.3 只看界面 mock

不想启动真实 Python worker，只想检查界面时：

```powershell
cd D:\AiCode\feedgrab\desktop
$env:FEEDGRAB_DESKTOP_MOCK = "true"
.\node_modules\.bin\electron.cmd .
```

mock 模式不会请求真实平台，不代表抓取能力已经跑通。

## 7. 首次登录和平台准备

GUI 当前不会弹出登录浏览器，也不会写入 Cookie。请在 CLI 中完成登录：

```powershell
cd D:\AiCode\feedgrab
.\.venv\Scripts\Activate.ps1

feedgrab login twitter
feedgrab login xhs
feedgrab login wechat
feedgrab login feishu
feedgrab login kdocs
feedgrab login zhihu
feedgrab login zsxq
```

可选：复用已登录 Chrome 的 CDP Cookie 提取。

```powershell
$env:CHROME_CDP_LOGIN = "true"
feedgrab login twitter
feedgrab login xhs
feedgrab login kdocs
$env:CHROME_CDP_LOGIN = ""
```

登录后回到桌面端，打开“登录”页查看 session 状态。登录页只显示状态和时间，不显示 Cookie 原文。

无需登录即可尝试的平台通常包括：

- GitHub 公开仓库或 README。
- 公开网页。
- 部分公开 Discourse / LinuxDo / IDCFlare 帖子。
- 部分公开视频元数据或字幕路径。

需要登录、Cookie 或 API Key 的平台，仍以 CLI 诊断结果为准。

## 8. 基本抓取使用方法

### 8.1 单链接抓取

1. 启动桌面客户端。
2. 打开“抓取”页。
3. 在“抓取目标（URL / 关键词 / 关键词组 / 账号）”输入框中粘贴一个 URL，例如：

```text
https://github.com/iBigQiang/feedgrab
```

4. 确认默认输出目录，或点击“选择”改成自己的输出目录，例如：

```text
D:\feedgrab Desktop\output
```

5. 点击“开始抓取”。
6. 在右侧实时日志中观察 `worker 已接收`、`任务已启动`、`正在抓取`、`已生成产物`、`抓取完成`。
7. 打开“输出”页，点击产物旁的“打开”。

### 8.2 多链接批量抓取

在“抓取目标（URL / 关键词 / 关键词组 / 账号）”中每行放一个 URL：

```text
https://github.com/iBigQiang/feedgrab
https://linux.do/t/topic/2470643
https://mp.weixin.qq.com/s/g7ASDLvrVN9eNgYDvrNKeA
```

点击“开始抓取”后，worker 会按当前实现串行抓取，避免多个 URL 同时修改 `OUTPUT_DIR`。成功和失败会分别进入任务日志；部分失败不会影响其他 URL 的继续处理。

### 8.3 关键词或账号抓取

输入内容不是 URL 时，先在“现已支持的平台”中点亮目标平台，再填写关键词、关键词组或账号名。客户端会显示“将执行：...”预览，但后台提交的是结构化任务，不拼接 shell 命令。

示例：

```text
claude code,openclaw
```

选择 X / Twitter 时对应 `feedgrab x-so "claude code,openclaw"`；选择小红书时对应 `feedgrab xhs-so "claude code,openclaw"`；选择 YouTube 时对应 `feedgrab ytb-so "claude code,openclaw"`；选择知乎时对应 `feedgrab zhihu-so "claude code,openclaw"`；选择微信公众号时第一版默认按账号批量，对应 `feedgrab mpweixin-id "账号名"`。

### 8.4 任务页

任务页用于查看：

- 当前任务 URL。
- 输出目录。
- 状态：排队、运行中、完成、失败、已取消。
- 运行中任务的“取消”按钮。

当前任务页不是完整历史任务数据库；关闭应用后任务状态不会作为长期任务中心保存。

### 8.5 输出页

输出页展示 worker 返回或输出服务扫描到的 Markdown 产物。点击“打开”时，Electron main 会检查路径：

- 已授权输出目录内的文件可以打开。
- worker 本次返回的产物可以打开。
- 支持打开的常见后缀包括 `.md`、`.csv`、`.srt`、图片、音频和视频文件。
- 未授权目录或不存在的路径会被拒绝。

### 8.6 设置页

当前设置页可以查看和修改基础设置、代理设置和平台配置。网络代理位于“基础设置”，包含：

- `启用代理`：默认关闭。
- `代理地址`：支持 `http://127.0.0.1:7890`、`socks5://127.0.0.1:7890`、`http://用户名:密码@IP:端口`，密码会在 UI 和日志中隐藏。
- `不走代理地址`：默认 `127.0.0.1,localhost`，避免本地 worker、CDP 端口和 Electron 内部服务被代理干扰。

保存后，桌面端会把代理注入 Python sidecar worker 的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 环境变量，并让 Python HTTP 抓取、Playwright 新启动浏览器和 Electron 在线赞助/社群文档加载使用同一设置。若复用 Chrome CDP，客户端只继承用户已打开 Chrome 的代理/VPN 状态，不强行修改 Chrome 代理。

部分平台高级选项仍可通过环境变量或 `.env.example` 中的配置项管理，例如：

- `X_DOWNLOAD_MEDIA`
- `X_FETCH_ALL_COMMENTS`
- `XHS_FETCH_COMMENTS`
- `LINUXDO_REPLY_MODE`
- `FEISHU_DOWNLOAD_IMAGES`
- `KDOCS_DOWNLOAD_IMAGES`
- `GITHUB_TOKEN`

对 GUI 生效的环境变量，建议在启动 Electron 前于同一个 PowerShell 中设置。

## 9. CLI 与 GUI 的关系

桌面端覆盖通用 URL 抓取工作台，并已接入一部分搜索/账号类任务。抓取页输入 URL 时保持自动识别；输入关键词或账号时，先选择平台，客户端会生成结构化任务并显示预览，例如：

```powershell
feedgrab "https://example.com/article"
feedgrab "https://url1.com" "https://url2.com"
feedgrab x-so "AI Agent"
feedgrab xhs-so "AI Agent"
feedgrab mpweixin-id "公众号名"
feedgrab ytb-so "AI Agent"
feedgrab zhihu-so "AI Agent"
```

以下命令型能力仍建议继续使用 CLI：

```powershell
feedgrab clip
feedgrab mpweixin-so "AI Agent"
feedgrab feishu-wiki "https://xxx.feishu.cn/wiki/ABC123"
feedgrab ytb-dlv "https://www.youtube.com/watch?v=xxx"
feedgrab ytb-dla "https://www.youtube.com/watch?v=xxx"
feedgrab ytb-dlz "https://www.youtube.com/watch?v=xxx"
feedgrab ytb-all "https://www.youtube.com/watch?v=xxx"
```

注意：feedgrab 的普通抓取命令不是 `feedgrab fetch <url>`，而是直接：

```powershell
feedgrab "https://example.com/article"
```

## 10. 常见问题

### 10.1 Electron 打开后提示 worker 连接失败

通常是 Electron 找到的 `python` 不是你安装依赖的 Python。

检查：

```powershell
cd D:\AiCode\feedgrab
.\.venv\Scripts\Activate.ps1
python -c "import sys; print(sys.executable)"
python -c "import feedgrab; print('feedgrab import ok')"
python -m feedgrab.worker
```

然后在同一个 PowerShell 中重新启动 Electron：

```powershell
cd desktop
.\node_modules\.bin\electron.cmd .
```

### 10.2 `electron.cmd` 不存在

说明桌面依赖还没安装：

```powershell
cd D:\AiCode\feedgrab\desktop
npm ci
```

### 10.3 `npm` 或 `node` 不存在

安装 Node.js 后重新打开 PowerShell，再检查：

```powershell
node --version
npm --version
```

### 10.4 浏览器抓取失败或提示 Playwright / patchright 缺失

先确认 Python optional 依赖和 Chromium 已安装：

```powershell
cd D:\AiCode\feedgrab
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[all]"
patchright install chromium
```

或：

```powershell
python -m playwright install chromium
```

### 10.5 GUI 抓取没有输出文件

先确认 GUI“设置”里的输出目录是否正确；安装版默认是主程序安装目录下的 `output`。CLI 路径则检查：

```powershell
$env:OUTPUT_DIR
$env:OBSIDIAN_VAULT
```

如果明确填写了 Obsidian Vault，注意 `OBSIDIAN_VAULT` 优先于 `OUTPUT_DIR`。

### 10.6 登录页显示未登录

先用 CLI 登录：

```powershell
feedgrab login twitter
feedgrab login xhs
feedgrab login wechat
```

登录完成后重启桌面端，再查看“登录”页。

### 10.7 微信、飞书、金山文档、知识星球等私有内容抓取失败

这类平台通常依赖 Cookie、浏览器 session、Open API 凭据或 CDP 复用。先用 CLI 专项诊断和登录确认：

```powershell
feedgrab doctor
feedgrab login feishu
feedgrab login kdocs
feedgrab login zsxq
```

若使用 CDP，请先启动带 remote debugging 的 Chrome，并在启动 CLI/GUI 前设置相关环境变量。

### 10.8 X/Twitter 抓取失败或 429

常见原因是 Cookie 失效、账号限流、GraphQL queryId 或 transaction id 依赖缺失。先运行：

```powershell
feedgrab doctor x
feedgrab login twitter
```

如果需要更强抓取能力，确保已安装 `[all]` 或至少 `twitter`/`stealth` extras。

## 11. 安全和隐私说明

- GUI renderer 运行在 `sandbox: true`、`contextIsolation: true`、`nodeIntegration: false` 下。
- renderer 不直接读取文件系统、Cookie、API Key 或 Python 进程。
- IPC 只通过 preload 暴露白名单 API。
- Python worker 输出会做敏感字段脱敏，不应显示 Cookie 原文、token 原文或 session 原文。
- `openPath` 只允许打开已授权输出目录或 worker 产物，避免任意路径打开。
- 当前分支不接入真实远程授权服务，也不会把诊断和凭据上传到远程服务。

## 12. 推荐首次完整流程

```powershell
# 1. 获取源码
git clone --branch feedgrab-desktop https://github.com/iBigQiang/feedgrab.git
cd feedgrab

# 2. Python 环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[all]"
patchright install chromium

# 3. CLI 初始化和诊断
feedgrab setup
feedgrab doctor

# 4. 桌面依赖和构建
cd desktop
npm ci
npm run typecheck
npm run lint
npm run test
npm run build

# 5. 启动 GUI
.\node_modules\.bin\electron.cmd .
```

启动后按这个顺序使用：

1. 进入“抓取”页。
2. 确认默认输出目录，或点击“选择”指定自己的输出目录。
3. 粘贴一个 GitHub 或公开网页链接做首次抓取。
4. 查看“实时日志”和“任务”页状态。
5. 在“输出”页打开生成的 Markdown。
6. 对需要登录的平台，回到 CLI 执行 `feedgrab login <platform>` 后重启 GUI。
