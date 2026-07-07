完成 feedgrab Desktop 当前分支的收尾发布工作。此命令只服务桌面端分支，必须在当前分支完成打包、发布安装包、更新文档、提交并推送；禁止切换到 `main`，禁止推送 `main`。

请按以下步骤执行：

## 1. 确认分支和变更范围

运行以下检查：

```powershell
git branch --show-current
git status --short
git diff --stat
```

要求：

- 当前分支必须是桌面端发布分支，例如 `feedgrab-desktop`。
- 如果当前分支不是桌面端分支，先停止并询问用户，不要自动切换分支。
- 只处理当前分支上的代码、文档和桌面端安装包发布工作。
- 不要执行 `git push origin main`，不要合并到 `main`。
- 不要强行把 `desktop/release-packages/` 里的 `.exe` 加入 Git；安装包应作为 GitHub Release asset 上传。

## 2. 运行收尾检查和桌面端验证

优先读取项目上下文：

- `AGENTS.md`
- `DEVLOG.md`
- `README.md`
- `README_en.md`
- `desktop/README.md`
- `docs/feedgrab-desktop-packaging.md`

在 `desktop/` 下运行桌面端验证：

```powershell
cd D:\AiCode\feedgrab\desktop
& 'D:\nodejs\npm.cmd' test
& 'D:\nodejs\npm.cmd' run lint
& 'D:\nodejs\npm.cmd' run build
```

如果本次改动涉及 Python service / worker 协议，也运行相关 Python 测试：

```powershell
cd D:\AiCode\feedgrab
python -m pytest tests/test_service_desktop.py tests/test_worker_protocol.py tests/test_service_layer.py -q -p no:cacheprovider
```

## 3. 重新打包 exe 安装包

在 `desktop/` 下先确认桌面端版本号。每次发布新的安装包前，必须递增
`desktop/package.json` 里的版本号，至少递增 patch 版本，例如 `0.1.0` → `0.1.1`
→ `0.1.2`。不要连续发布多个不同安装包却复用同一个版本号和文件名。

```powershell
cd D:\AiCode\feedgrab\desktop
$desktopVersion = (Get-Content package.json | ConvertFrom-Json).version
$desktopVersion
```

确认版本号已递增后，重新打包当前分支的 Windows 用户级安装包：

```powershell
cd D:\AiCode\feedgrab\desktop
& 'D:\nodejs\npm.cmd' run pack:user
```

> **禁止手动分步打包或跳过 runtime:build。** `npm run pack:user` 已通过 `&&` 串联内含三步：`build`（编译 TypeScript/Vite）→ `runtime:build`（重建 Python worker + Chromium 运行时）→ `package-windows.ps1 -SkipRuntimeBuild`（打包 exe）。直接单独运行 `package-windows.ps1 -SkipRuntimeBuild` 或跳过 `runtime:build` 会导致安装包中的 Python worker 还是旧版本，修改不生效。

打包后确认最新产物：

```powershell
$pkg = Get-ChildItem D:\AiCode\feedgrab\desktop\release-packages -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$installer = Get-ChildItem $pkg.FullName -Filter '*.exe' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$installer.FullName
Get-Item $installer.FullName | Select-Object Name,Length,LastWriteTime
Get-FileHash $installer.FullName -Algorithm SHA256
Get-AuthenticodeSignature $installer.FullName | Select-Object Status,SignerCertificate
```

记录：

- 安装包本地路径
- 文件大小
- SHA256
- 是否签名
- 打包时间

如果打包失败，先修复失败原因，不要继续发布。

## 4. 准备 GitHub Release asset

使用当前分支作为 Release target。推荐 tag 格式：

```text
desktop-v<desktop-package-version>-YYYYMMDD
```

Release asset 文件名使用无空格、稳定、可复制的名称：

```text
feedgrab-desktop-setup-<desktop-package-version>.exe
```

`desktop/electron-builder.yml` 的 `nsis.artifactName` 已配置为 `feedgrab-desktop-setup-${version}.exe`，`npm run pack:user` 产物会自动使用这个规范名，无需手动复制重命名。`gh release create` / `gh release upload` 的 `"$installer#$assetName"` 语法在产物名与 `$assetName` 一致时 `#assetName` 可省略（保留也无害）。

GitHub Release 介绍信息必须用中文写全，不要只写一句英文占位说明。Release notes 至少包含：

- 版本标题，例如 `feedgrab Desktop v0.1.13 阶段性测试版`
- 适用分支和版本性质，明确这是 `feedgrab-desktop` 分支的 Windows 桌面客户端预览安装包
- 下载信息：安装包文件名、真实下载地址、Release tag、目标分支、文件大小、SHA256、签名状态、打包时间
- 本版主要修复或新增功能，按用户可理解的中文条目列出
- 已运行的验证命令和结果，包括桌面端 test/lint/build、必要的 Python 测试、Release asset URL 核验
- 说明安装包不包含真实 Cookie、Token、API Key 或用户登录态

推荐先准备中文 notes 文本，将占位符替换为实际值后再传给 `gh release create` / `gh release edit`。为了保留 Markdown 反引号，建议使用 PowerShell 单引号 here-string：

```powershell
$notes = @'
## feedgrab Desktop v<desktopVersion> 阶段性测试版

这是 `feedgrab-desktop` 分支的 Windows 桌面客户端预览安装包，适合继续做客户端安装、登录态、多账号、抓取和卸载保留数据测试。本次安装包只发布普通用户版 NSIS 安装器，源码分支不提交 `.exe` 二进制产物。

### 下载信息

- Windows 安装包：`<assetName>`
- 下载地址：<发布后用 GitHub API 返回的 browser_download_url 回填>
- Release tag：`<tag>`
- 目标分支：`feedgrab-desktop`
- 文件大小：<安装包 bytes>
- SHA256：<安装包 SHA256>
- 签名状态：<已签名/未签名>
- 打包时间：<本地打包时间 +08:00>

### 本版主要修复

- <用中文列出本版用户可感知的修复或新增功能>

### 已做验证

- 桌面端：`npm test`、`npm run lint`、`npm run build`
- Python：<如涉及 service / worker，写入实际 pytest 命令和结果>
- Release asset：通过 GitHub API 获取真实 `browser_download_url`，并用 `curl.exe -I -L` 核验最终不是 404。

### 说明

本安装包仍是桌面端预览版本，不是 main 分支正式 CLI 发布。安装包不包含任何真实 Cookie、Token、API Key 或用户登录态；真实登录信息只会保存到用户设置的 `登录态和数据目录`。
'@
```

先提交并推送当前分支上的代码和文档基础更新，确保 GitHub Release tag 可以指向远端已有提交；仍然只推送当前分支：

```powershell
git add <本次需要提交的代码和文档文件>
git commit -m "chore: prepare desktop release"
git push origin feedgrab-desktop
```

如无需中间提交，也必须确保当前分支 HEAD 已推送到远端，再创建 Release。

创建或更新 GitHub Release：

```powershell
$releaseDate = Get-Date -Format yyyyMMdd
$tag = "desktop-v$desktopVersion-$releaseDate"
$assetName = "feedgrab-desktop-setup-$desktopVersion.exe"

gh release create $tag `
  --repo iBigQiang/feedgrab `
  --target feedgrab-desktop `
  --title "feedgrab Desktop v$desktopVersion $releaseDate" `
  --notes "$notes" `
  "$installer#$assetName"
```

如果 tag 或 release 已存在，则使用覆盖上传：

```powershell
gh release upload $tag `
  "$installer#$assetName" `
  --repo iBigQiang/feedgrab `
  --clobber
```

## 5. 获取并核验真实在线下载地址

通过 GitHub API 获取真实 asset URL，不要只手写猜测地址：

```powershell
$url = gh api "repos/iBigQiang/feedgrab/releases/tags/$tag" `
  --jq ".assets[] | select(.name==`"$assetName`") | .browser_download_url"
$url
curl.exe -I -L $url
```

要求：

- 返回的 URL 必须是 GitHub Release asset 的真实 `browser_download_url`。
- `curl.exe -I -L` 需要能正常跟随跳转，最终不是 404。
- 如果 Release 是 draft，先发布后再声称这是公开下载地址。
- 获取真实 `$url` 后，必须把 Release notes 中的下载地址占位符替换为该 `$url`，并运行 `gh release edit $tag --notes "$notes"` 更新 GitHub Release 页面，确保 <https://github.com/iBigQiang/feedgrab/releases> 上展示的是中文完整说明而不是英文占位说明。

## 6. 更新桌面端 README 和发布文档

用真实下载地址更新：

- `D:\AiCode\feedgrab\desktop\README.md`
- `D:\AiCode\feedgrab\docs\feedgrab-desktop-packaging.md`
- 根目录 `README.md` 必须由 `desktop\README.md` 全文覆盖；`README_en.md` 如保留桌面端下载入口，也要同步更新 URL/tag
- 如本次属于版本记录，更新 `DEVLOG.md`

在 `feedgrab-desktop` 分支上，仓库根目录 `README.md` 必须使用 `desktop\README.md`
的客户端说明全文覆盖。这样 GitHub 打开 `feedgrab-desktop` 分支时默认展示桌面客户端说明；
不要把这个规则应用到 `main`，也不要因此推送 `main`。

发布信息更新顺序必须是：先更新 `desktop\README.md`，再复制覆盖根目录 `README.md`，
最后运行以下命令确认两者一致：

```powershell
Compare-Object (Get-Content README.md) (Get-Content desktop\README.md)
```

命令无输出才算通过。

`desktop/README.md` 至少包含：

- Windows 安装包真实下载地址
- GitHub Release 页面地址
- 版本号 / tag
- 打包时间
- SHA256
- 签名状态
- 当前分支说明

## 7. 最终验证

在最终提交前运行：

```powershell
cd D:\AiCode\feedgrab\desktop
& 'D:\nodejs\npm.cmd' test
& 'D:\nodejs\npm.cmd' run lint
& 'D:\nodejs\npm.cmd' run build

cd D:\AiCode\feedgrab
git diff --check
git status --short
```

如有测试失败、lint 失败、构建失败、Release URL 不可访问，必须先修复，不要宣称收尾完成。

## 8. 最后统一提交并推送当前分支

确认 `desktop/README.md` 已经写入真实在线下载地址后，再做最终提交并推送：

```powershell
git add <本次最终代码和文档文件>
git commit -m "docs: update desktop release workflow"
git push origin feedgrab-desktop
```

结束条件：

- 当前分支代码和文档已推送到 `origin/feedgrab-desktop`
- GitHub Release asset 已上传
- 已获取并核验真实安装包下载地址
- `desktop/README.md` 已写入真实下载地址
- `README.md` 已同步为 `desktop/README.md` 的客户端说明
- 最后一轮更新已再次推送到当前分支
- `main` 未被推送或修改

最后向用户汇报：

- 当前分支名
- 安装包本地路径
- GitHub Release tag
- 真实下载地址
- SHA256
- 已运行的验证命令和结果
- 最终提交 hash 和推送结果
