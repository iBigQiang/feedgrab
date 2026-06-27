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

在 `desktop/` 下重新打包当前分支的 Windows 用户级安装包：

```powershell
cd D:\AiCode\feedgrab\desktop
& 'D:\nodejs\npm.cmd' run pack:user
```

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

示例：

```text
desktop-v0.1.0-20260627
```

Release asset 文件名使用无空格、稳定、可复制的名称：

```text
feedgrab-desktop-setup-<desktop-package-version>.exe
```

示例：

```text
feedgrab-desktop-setup-0.1.0.exe
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
gh release create desktop-v0.1.0-YYYYMMDD `
  --repo iBigQiang/feedgrab `
  --target feedgrab-desktop `
  --title "feedgrab Desktop v0.1.0 YYYY-MM-DD" `
  --notes "feedgrab Desktop preview installer for feedgrab-desktop branch." `
  "$installer#feedgrab-desktop-setup-0.1.0.exe"
```

如果 tag 或 release 已存在，则使用覆盖上传：

```powershell
gh release upload desktop-v0.1.0-YYYYMMDD `
  "$installer#feedgrab-desktop-setup-0.1.0.exe" `
  --repo iBigQiang/feedgrab `
  --clobber
```

## 5. 获取并核验真实在线下载地址

通过 GitHub API 获取真实 asset URL，不要只手写猜测地址：

```powershell
$url = gh api repos/iBigQiang/feedgrab/releases/tags/desktop-v0.1.0-YYYYMMDD `
  --jq '.assets[] | select(.name=="feedgrab-desktop-setup-0.1.0.exe") | .browser_download_url'
$url
curl.exe -I -L $url
```

要求：

- 返回的 URL 必须是 GitHub Release asset 的真实 `browser_download_url`。
- `curl.exe -I -L` 需要能正常跟随跳转，最终不是 404。
- 如果 Release 是 draft，先发布后再声称这是公开下载地址。

## 6. 更新桌面端 README 和发布文档

用真实下载地址更新：

- `D:\AiCode\feedgrab\desktop\README.md`
- `D:\AiCode\feedgrab\docs\feedgrab-desktop-packaging.md`
- 如总 README 有桌面端下载入口，同步更新 `README.md` / `README_en.md`
- 如本次属于版本记录，更新 `DEVLOG.md`

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
