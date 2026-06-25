# feedgrab Desktop Windows 打包方案

> 适用分支：`feedgrab-desktop`

本分支提供两个 Windows `.exe` 产物：

| 版本 | 命令 | 产物用途 |
|---|---|---|
| 开发者版 portable `.exe` | `npm run pack:dev` | 不安装到系统，双击即可打开，便于内测和调试 |
| 普通用户版安装器 `.exe` | `npm run pack:user` | NSIS 安装器，创建开始菜单和桌面快捷方式 |
| 同时构建两个版本 | `npm run pack:all` | 一次生成 portable 和安装器 |

两个版本都会先构建 `feedgrab-runtime`，包含：

- `feedgrab-worker.exe`：由 PyInstaller 从 `feedgrab/worker.py` 冻结生成。
- `ms-playwright/`：Playwright Chromium 浏览器目录。
- runtime 环境变量：Electron main 会把 `PLAYWRIGHT_BROWSERS_PATH` 指向内置或托管 Chromium 目录。

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
