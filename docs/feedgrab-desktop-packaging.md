# feedgrab Desktop Windows 打包方案

> 适用分支：`feedgrab-desktop`

本分支提供两个 Windows `.exe` 产物：

| 版本 | 命令 | 产物用途 |
|---|---|---|
| 开发者版 portable `.exe` | `npm run pack:dev` | 不安装到系统，双击即可打开，便于内测和调试 |
| 普通用户版安装器 `.exe` | `npm run pack:user` | NSIS 安装器，创建开始菜单和桌面快捷方式 |
| 同时构建两个版本 | `npm run pack:all` | 一次生成 portable 和安装器 |

## 当前预览发布

本次阶段性安装包只发布普通用户版 NSIS 安装器，文件作为 GitHub Release asset 上传，不提交到源码分支：

| 项 | 值 |
|---|---|
| Release tag | `desktop-v0.1.19-20260710` |
| 发布页 | <https://github.com/iBigQiang/feedgrab/releases/tag/desktop-v0.1.19-20260710> |
| 下载地址 | <https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.19-20260710/feedgrab-desktop-setup-0.1.19.exe> |
| 本地构建目录 | `D:\AiCode\feedgrab\desktop\release-packages\20260710-202312\` |
| 本地原始文件名 | `feedgrab-desktop-setup-0.1.19.exe` |
| Release asset 文件名 | `feedgrab-desktop-setup-0.1.19.exe` |
| 文件大小 | `376927297` bytes |
| 打包时间 | `2026-07-10 20:24 +08:00` |
| SHA256 | `44086358BAB9D8D8A1063DCD2D9D3EC4125C094B52E15613846A8E4B90448D23` |
| 签名状态 | 未签名 |
| 在线核验 | GitHub API `browser_download_url` 与上方下载地址一致，`curl.exe -I -L` 最终返回 `200 OK` |

发布 Release 时必须指定 `--target feedgrab-desktop`，避免 tag 指向 `main`。
发布文档更新后，`feedgrab-desktop` 分支根目录 `README.md` 必须与 `desktop/README.md` 保持全文一致，确保 GitHub 打开桌面分支时默认展示客户端说明。
每次重新发布安装包前必须递增 `desktop/package.json` 小版本号，例如 `0.1.13`、`0.1.14`，不要复用旧版本号生成新安装包。

安装包文件名由 `desktop/electron-builder.yml` 的 `nsis.artifactName` 控制，已配置为 `feedgrab-desktop-setup-${version}.exe`（连字符、无空格、全小写）。`npm run pack:user` 产物会自动符合 Release asset 规范名，无需手动复制重命名。`productName: feedgrab Desktop`（带空格）仍用于窗口标题、开始菜单快捷方式和安装器 UI 显示名，不受 `artifactName` 影响。

两个版本都会先构建 `feedgrab-runtime`，包含：

- `feedgrab-worker.exe`：由 PyInstaller 从 `feedgrab/worker.py` 冻结生成。
- `ms-playwright/`：Playwright Chromium 浏览器目录。
- runtime 环境变量：Electron main 会把 `PLAYWRIGHT_BROWSERS_PATH` 指向内置或托管 Chromium 目录。

安装包会把 `desktop/session-templates/` 打包到安装资源目录 `resources/session-templates/`（只读资源）：

- 这些 JSON 文件只允许保存空白模板，不提交真实 Cookie、Token 或浏览器登录态。
- 安装版启动时会把模板补齐到 `FEEDGRAB_DATA_DIR`，同名文件自动跳过，避免覆盖用户已有登录态文件。
- 默认安装版首次会把 `FEEDGRAB_DATA_DIR` 指向 `安装目录\sessions\`；用户可手动更改到其他目录。
- GUI 导入逻辑会忽略空白模板，只有填入真实值后才会被当作可导入 session。

卸载器会在普通卸载时询问是否保留 `output` 与 `sessions`。静默卸载、升级卸载默认保留。保留模式会原地保留安装目录中的 `output` 与 `sessions`，并保留安装目录父路径结构；重新安装到同一路径时，同名登录态和输出文件会跳过，不会被空白模板覆盖。

## 本地打包

```powershell
cd D:\AiCode\feedgrab
git switch feedgrab-desktop

cd desktop
& 'D:\nodejs\npm.cmd' install
& 'D:\nodejs\npm.cmd' run pack:all
```

产物输出到：

```text
D:\AiCode\feedgrab\desktop\release-packages\yyyyMMdd-HHmmss\
```

每次执行打包命令都会创建一个新的时间戳子目录，避免覆盖旧产物时被 Windows 文件锁阻塞。

构建过程中会生成本地目录：

```text
D:\AiCode\feedgrab\desktop\runtime\
D:\AiCode\feedgrab\desktop\build\
D:\AiCode\feedgrab\desktop\release-packages\
```

这些目录已加入 `.gitignore`，不提交到源码仓库。

## 从 GitHub 分支下载源码打包

```powershell
git clone --branch feedgrab-desktop https://github.com/iBigQiang/feedgrab.git
cd feedgrab\desktop
& 'D:\nodejs\npm.cmd' install
& 'D:\nodejs\npm.cmd' run pack:all
```

如果机器没有 `D:\nodejs\npm.cmd`，使用系统里的 `npm`：

```powershell
npm install
npm run pack:all
```

## 运行时选择规则

Electron main 按以下顺序选择 worker：

1. 如果 `resources/feedgrab-runtime/feedgrab-worker/feedgrab-worker.exe` 存在，使用内置 worker。
2. 否则回退到系统 Python：`python -m feedgrab.worker`。
3. 可用 `FEEDGRAB_DESKTOP_PYTHON` 指定系统 Python。
4. 可用 `FEEDGRAB_DESKTOP_RUNTIME_DIR` 指定自定义 runtime 目录。

内置 Chromium 路径：

```text
resources/feedgrab-runtime/ms-playwright/
```

如果内置 Chromium 不存在，runtime resolver 会把 `PLAYWRIGHT_BROWSERS_PATH` 指向用户数据目录下的托管位置，供后续首次自检修复流程使用。

安装版默认登录态目录和导入来源：

```text
安装目录\sessions\
```

开发版登录态导入来源：

```text
D:\AiCode\feedgrab\desktop\sessions\
```

## 验证

```powershell
cd D:\AiCode\feedgrab
python -m pytest tests/test_service_layer.py tests/test_service_desktop.py tests/test_worker_protocol.py -q -p no:cacheprovider

cd desktop
& 'D:\nodejs\npm.cmd' run typecheck
& 'D:\nodejs\npm.cmd' run lint
& 'D:\nodejs\npm.cmd' run test
& 'D:\nodejs\npm.cmd' run build
```

打包完成后，至少验证：

1. 双击 portable `.exe` 能打开 GUI。
2. 双击 NSIS `Setup.exe` 能安装并启动 GUI。
3. GUI 首屏显示 Python sidecar worker 已连接。
4. 抓取 GitHub 公开仓库链接能生成 Markdown artifact。
