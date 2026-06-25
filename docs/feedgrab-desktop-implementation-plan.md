# feedgrab-desktop 实施计划

> 分支：`feedgrab-desktop`
> 来源：`tasks/Goal-2：给feedgrab创建分支feedgrab-desktop分支开发GUI客户端.txt`
> 边界：本地开发与验证；未经用户确认不得推送 GitHub。

## 目标

在独立分支中开发 `Electron + Vite + React + TypeScript + Python Sidecar Worker` GUI 客户端。GUI 不解析 CLI 文本，不直接调用 fetcher，而是通过 Electron main -> Python Sidecar Worker -> `feedgrab.service` 的结构化协议完成抓取、诊断、登录状态、设置和输出库能力。

## 当前基线

- `feedgrab/service/` 已存在，但多数服务仍是薄包装。
- `cmd_fetch()` 和 `mcp_server.py` 已接入 `FetchService`。
- 当前没有 `package.json`、`desktop/`、Electron、Vite、React 或 TypeScript 工程。
- `FetchService.fetch_url()` 会触发保存 Markdown、附件下载和去重索引写入；自动化测试必须使用 fake reader 或临时输出目录。

## 工作拆分

### 1. Service hardening

文件范围：

- `feedgrab/service/models.py`
- `feedgrab/service/fetch.py`
- `feedgrab/service/jobs.py`
- `feedgrab/service/settings.py`
- `feedgrab/service/doctor.py`
- `feedgrab/service/login.py`
- `feedgrab/service/output.py`
- `feedgrab/service/__init__.py`
- `tests/test_service_desktop.py`

要求：

- `FetchService.fetch_urls()` 返回每个 URL 的成功或失败结构，不再静默丢弃失败项。
- `JobService` 提供 job id、串行默认队列、状态查询、取消、重试、并发限制配置、事件、错误和 artifact 汇总。
- `SettingsService` 提供 typed config schema、snapshot、secret 标记和脱敏预览。
- `DoctorService` 输出结构化诊断项。
- `LoginService` 只返回 session 状态，不输出 cookie 原文。
- `OutputService` 返回 artifact 元数据和可打开路径结构，不在测试中实际打开系统程序。
- redactor 覆盖 dict/list/string/URL query/header/storage_state/CDP endpoint。

### 2. Python Sidecar Worker

文件范围：

- `feedgrab/worker.py`
- `tests/test_worker_protocol.py`

协议：

- stdio JSON Lines。
- 请求：`ping`、`detect_platform`、`fetch`、`cancel`、`doctor`、`settings_snapshot`、`login_status`、`output_list`。
- 事件：`ready`、`job_started`、`progress`、`log`、`artifact`、`error`、`done`、`cancelled`、`diagnostic`。
- Worker 只调用 `feedgrab.service`，不 import fetcher，不解析 CLI 文本。

### 3. Desktop 工程

文件范围：

- `desktop/package.json`
- `desktop/tsconfig*.json`
- `desktop/vite.config.ts`
- `desktop/index.html`
- `desktop/electron/main.ts`
- `desktop/electron/preload.cts`
- `desktop/electron/python-worker.ts`
- `desktop/renderer/src/**`
- `desktop/tests/**`

要求：

- npm 脚本：`dev`、`typecheck`、`lint`、`test`、`build`。
- Electron main：`nodeIntegration: false`、`contextIsolation: true`、preload 白名单、CSP。
- preload 源文件使用 `.cts`，构建后输出 `preload.cjs`，只暴露 typed IPC API。
- renderer 首屏是可用工具，不做 landing page。
- UI 包含抓取、任务、输出、登录、设置、诊断、授权占位。
- browser/Vitest 环境提供 mock 状态，不请求真实平台；构建版 `file://` 环境若 preload 缺失则显式报错，不静默伪装为 mock。
- Renderer 订阅 worker 事件，并用 `artifact/done/error/cancelled` 更新任务和输出库。
- `openPath` 只能打开已授权输出目录或 worker 产物，`ELECTRON_RENDERER_URL` 仅允许 localhost。

### 4. 文档与验收

文件范围：

- `README.md`
- `README_en.md`
- `DEVLOG.md`
- `docs/feedgrab-desktop-implementation-plan.md`

本地验证命令：

```powershell
git status --short --branch
python -m pytest tests/test_service_layer.py -q
python -m pytest tests/test_service_desktop.py tests/test_worker_protocol.py -q
python -c "from feedgrab.service import FetchService; print('service import ok')"
python -c "import mcp_server; print('mcp import ok')"
python -m feedgrab.cli
```

Desktop 工程生成并安装依赖后：

```powershell
cd desktop
npm run typecheck
npm run lint
npm run test
npm run build
```

构建版 Electron 页面截图：

```powershell
$env:FEEDGRAB_DESKTOP_SCREENSHOT_VIEW = "fetch" # fetch/jobs/output/login/settings/doctor/auth
$env:FEEDGRAB_DESKTOP_SCREENSHOT = "D:\AiCode\feedgrab\output_smoke\desktop-views\fetch.png"
.\node_modules\.bin\electron.cmd dist-electron/main.js
```

真实连接验收使用 `tasks/Goal-验收测试连接.txt`，必须在本地完成后暂停；未经用户确认不得推送。
