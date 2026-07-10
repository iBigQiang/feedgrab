# feedgrab Desktop 客户端说明

feedgrab Desktop 是 feedgrab 的 Windows 图形客户端分支，面向不想长期使用命令行的用户。客户端采用 `Electron + Vite + React + TypeScript + Python Sidecar Worker` 架构：界面负责配置、登录态、任务和输出管理，抓取能力继续复用本仓库的 `feedgrab.service` 服务层。

## 下载入口

- Windows 安装包直链：[feedgrab-desktop-setup-0.1.19.exe](https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.19-20260710/feedgrab-desktop-setup-0.1.19.exe)
- 发布页：[desktop-v0.1.19-20260710](https://github.com/iBigQiang/feedgrab/releases/tag/desktop-v0.1.19-20260710)
- 当前桌面客户端分支：[feedgrab-desktop](https://github.com/iBigQiang/feedgrab/tree/feedgrab-desktop)
- 分支源码压缩包：[feedgrab-desktop.zip](https://github.com/iBigQiang/feedgrab/archive/refs/heads/feedgrab-desktop.zip)

当前预览发布提供普通用户安装器：

| 文件 | 适合对象 | 说明 |
| --- | --- | --- |
| `feedgrab-desktop-setup-0.1.19.exe` | 普通用户 | 双击安装，自动创建开始菜单和桌面快捷方式。 |

本次安装包来自 `feedgrab-desktop` 分支，打包时间 / 构建时间 `2026-07-10 20:24`，签名状态：未签名。本地文件 SHA256：

```text
44086358BAB9D8D8A1063DCD2D9D3EC4125C094B52E15613846A8E4B90448D23
```

上方下载地址来自 GitHub API 返回的 `browser_download_url`，已用 `curl.exe -I -L` 核验，最终返回 `200 OK`，文件大小 `376927297` bytes。

开发者/便携版可在本地运行 `npm run pack:dev` 或 `npm run pack:all` 生成。后续每次重新打包正式安装包都会递增桌面端小版本号，避免多个安装包都显示同一个版本。

## 安装与启动

1. 从 GitHub Releases 下载 `feedgrab-desktop-setup-0.1.19.exe`。
2. 双击安装包，按提示选择安装目录。
3. 安装完成后，通过桌面快捷方式或开始菜单启动 `feedgrab Desktop`。
4. 首次启动后进入“诊断”页面，确认 Python、feedgrab 包、Playwright/Patchright、Chromium、Node.js、Electron、输出目录和登录态目录状态。

如果缺少依赖，诊断页会显示“安装/更新”入口；也可以使用“安装/更新所有依赖”批量修复。普通安装包会尽量内置运行所需的 Python worker 和浏览器运行环境，避免用户手动配置命令行环境。

诊断页还包含“代理连通性”检查：代理关闭时显示“代理未启用”；代理开启后可用于判断代理不可达、网络超时等问题。

## 首次配置

打开“设置”页面后，建议先确认这些基础设置：

- `输出目录`：抓取结果保存位置。安装版首次默认指向安装根目录的 `output`，例如 `D:\feedgrab Desktop\output`；也可以改成 Obsidian 收件箱或专门的资料库目录。
- `Obsidian Vault`：可选，高优先级；安装初始留空，填写后会优先作为 Markdown 输出根目录。
- `登录态和数据目录`：保存 cookie/session 数据的位置。安装版首次默认指向安装根目录的 `sessions`，例如 `D:\feedgrab Desktop\sessions`。安装包会带空白模板文件，真实登录信息不会内置。
- `浏览器 User-Agent`：默认自动从当前环境读取，也可以手动覆盖。
- `Chrome CDP 端口`：默认 `9222`，客户端会尽量自动检测和启动可用端口。
- `启用代理` / `代理地址` / `不走代理地址`：默认关闭；代理地址支持 `http://127.0.0.1:7890`、`socks5://127.0.0.1:7890`、`http://用户名:密码@IP:端口`，密码会在界面和日志中隐藏。不走代理地址默认 `127.0.0.1,localhost`，避免本地 worker、CDP 端口和客户端内部服务被代理干扰。

保存代理设置后，客户端会把代理注入 Python sidecar worker 的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 环境变量，并用于 Python HTTP 抓取、Playwright 新启动浏览器和在线赞助/社群文档加载。若选择复用 Chrome CDP，客户端不会强行改 Chrome 代理，而是继承用户已打开 Chrome 的代理或 VPN 状态。

“基础设置”里提供全局“登录时优先从 Chrome CDP 提取登录态”开关：勾选时，所有平台点击“登录”都会优先从当前 Chrome 抽取 cookie，适合单账号；不勾选时，点击具体平台“登录”会打开隔离登录窗口，由用户手动登录，并保持窗口打开直到客户端提示登录态已保存，适合保存多账号。平台级参数仍在“设置 → 平台设置”中维护，按 X/Twitter、小红书、微信公众号、Discourse 论坛、文档平台、视频播客、知乎、Telegram、RSS、任意网页、知识星球、媒体/API 等分类展示。

## 登录态与 sessions

客户端支持三种登录态来源：

1. 检测当前客户端已保存的登录态。
2. 从安装目录或本地 `sessions` 目录导入 JSON cookie 文件。
3. 点击单个平台“登录”：若基础设置已勾选“登录时优先从 Chrome CDP 提取登录态”，客户端直接抽取当前 Chrome 登录态；若未勾选，则打开隔离登录窗口，由用户完成登录，并等待客户端提示登录态已保存后再关闭窗口。

安装包不再直接把模板写入安装根目录，而是把 `desktop/session-templates/` 以 `extraResources` 打包到安装资源目录，并在启动时按以下规则补齐到 `FEEDGRAB_DATA_DIR`：

```text
FEEDGRAB_DATA_DIR/
  x_2.json
  x_3.json
  wechat.json
  xhs.json
  linuxdo.json
  ...
```

模板里包含空白字段结构，不包含真实 cookie。启动时会跳过同名文件，避免覆盖用户已存在的登录态文件。安装版默认登录态目录仍为 `FEEDGRAB_DATA_DIR` 指向的 `安装目录\\sessions`，用户可以在设置页改到其他目录。真实 cookie、token、API Key 不会在界面中明文展示。

卸载器会在普通卸载时询问是否保留 `output` 与 `sessions`。如果选择保留、执行静默卸载或升级，卸载器会原地保留安装目录中的 `output` 与 `sessions`，并保留安装目录父路径结构，例如 `D:\feedgrab Desktop\output` 与 `D:\feedgrab Desktop\sessions`；重新安装到同一路径时，同名登录态和输出文件会跳过，不会被空白模板覆盖。

## 基本抓取流程

1. 进入“抓取”页面。
2. 在“抓取目标（URL / 关键词 / 关键词组 / 账号）”中输入目标。
   - 输入 `http(s)` URL 时，客户端继续自动识别平台并按 URL 抓取。
   - 输入关键词或关键词组时，先点亮上方平台按钮，例如 X / Twitter、小红书、YouTube、知乎。
   - 输入微信公众号账号名时，第一版默认走公众号账号批量抓取。
3. 确认“输出目录”，必要时点击“选择”更换保存位置。
4. 点击“开始抓取”。
5. 在右侧“实时日志”查看执行过程。
6. 到“任务”页面查看每个 URL 的运行、完成或失败状态。
7. 到“输出”页面打开本次客户端运行期间生成的 Markdown 文件。

输出页只展示客户端本次运行以来产生的抓取记录，不会扫描或删除输出目录中已有的 Markdown 文件。“清空记录”只清空客户端内的输出记录，不删除磁盘文件。

## 当前主要页面

| 页面 | 用途 |
| --- | --- |
| 抓取 | 输入 URL、关键词、关键词组或公众号账号，选择输出目录，提交抓取任务。 |
| 任务 | 查看每个 URL 的任务状态。 |
| 输出 | 查看本次运行生成的 Markdown 记录并快速打开。 |
| 登录 | 检测、导入和创建各平台登录态。 |
| 设置 | 管理基础设置和平台参数。 |
| 诊断 | 检查核心依赖、浏览器、目录权限和 worker 状态。 |
| 赞助 | 在线加载项目赞助说明，失败时回退到内置文档。 |
| 社群 | 在线加载社群说明，失败时回退到内置文档。 |

## 支持的平台概览

客户端界面会显示当前已支持的平台分类，包括：

- X / Twitter
- 微信公众号
- 小红书
- YouTube
- Bilibili
- GitHub
- Reddit
- Discourse 论坛
- 文档平台：飞书、金山文档、FlowUs、有道云笔记等
- 视频播客：YouTube、B站、小宇宙、喜马拉雅等
- 知乎
- Telegram
- RSS
- 知识星球
- 付费新闻
- 任意网页

不同平台对登录态、API Key、浏览器环境的要求不同。如果某个平台抓取失败，优先查看“登录”和“诊断”页面。

## Reddit 支持

Reddit 采用保守的多后端路线：direct `.json` + 已保存 Cookie 优先，随后尝试 Chrome CDP / Playwright session，最后保留 Jina 兜底。登录态不再只看 `reddit.json` 是否存在，诊断会用 `https://www.reddit.com/api/me.json` 做真实校验，避免游客 Cookie 被误判为已登录。

常用命令：

```powershell
feedgrab doctor reddit
feedgrab login reddit
feedgrab reddit-so "codex" --sort comments --time all --limit 50
feedgrab reddit-sub ChatGPT --sort hot --limit 50
```

`feedgrab login reddit` 会优先复用或启动普通 Chrome/CDP 手动登录；Reddit 不支持 `--headless` 自动化登录，以降低“异常登录/非法登录”拦截风险。

关键配置：

- `REDDIT_REPLY_MODE=top|tree|all`：默认 `top`，只渲染顶层评论；`tree` 保留初始响应里的嵌套回复；`all` 会额外调用 `/api/morechildren` 展开更多评论。
- `REDDIT_MAX_PAGES=5`：`reddit-so` / `reddit-sub` 使用 `after` cursor 的最大分页数。
- `REDDIT_RETRY_ATTEMPTS=3`：direct `.json` 遇到 `429 Retry-After`、5xx 或网络错误时的重试次数。
- `REDDIT_MORECHILDREN_ROUNDS=2`：仅 `all` 模式生效，限制 `/api/morechildren` 展开轮数。
- `REDDIT_MAX_COMMENTS=50`：单帖最多渲染的评论条数。

## 开发环境启动

开发者可以从分支源码启动客户端：

```powershell
cd D:\AiCode\feedgrab\desktop
npm install
npm run dev
```

`npm run dev` 只启动 Vite Web 预览。要测试真正的 Electron 客户端，需要运行项目提供的开发启动脚本或 Electron 启动命令，确保主进程、preload、Python sidecar worker 和 renderer 一起工作。

常用检查命令：

```powershell
cd D:\AiCode\feedgrab\desktop
npm run typecheck
npm test
npm run build
```

## 本地打包

Windows 下常用打包命令：

```powershell
cd D:\AiCode\feedgrab\desktop
npm run pack:user
```

打包全部版本：

```powershell
cd D:\AiCode\feedgrab\desktop
npm run pack:all
```

打包产物默认输出到：

```text
D:\AiCode\feedgrab\desktop\release-packages\
```

打包过程会构建前端、Electron 主进程、Python worker 运行时，并复制必要静态资源和空白 session 模板。

## 常见问题

### 打开后 worker 未连接

先进入“诊断”页面查看 Python、feedgrab 包和 worker 状态。开发环境下请确认已经安装依赖并从 `desktop` 目录启动。

### Web 页面和安装版界面不一致

浏览器中的 `http://127.0.0.1:5173/` 是 Vite Web 预览，不等同于完整 Electron 客户端。涉及文件选择、打开路径、worker、登录态导入、运行时诊断等功能时，应以 Electron 客户端为准。

### 登录态导入后状态没有变化

确认导入目录中 JSON 文件结构正确，且没有把真实登录态和空白模板混用。导入完成后点击“重新检测”，查看每个平台的有效账号、过期账号和异常账号数量。

### 抓取成功但没有看到输出

检查“输出目录”是否正确，确认任务日志中的产物路径。输出页只显示本次客户端运行后产生的记录，不代表输出目录中的全部历史文件。

### 安装包没有内置真实 cookie

这是预期行为。安装包只包含空白模板，不包含开发者本机 cookie、token、API Key 或用户隐私数据。
