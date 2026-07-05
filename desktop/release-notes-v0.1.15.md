## feedgrab Desktop v0.1.15 阶段性测试版

这是 `feedgrab-desktop` 分支的 Windows 桌面客户端预览安装包，适合继续做客户端安装、登录态、多账号、抓取和卸载保留数据测试。本次安装包只发布普通用户版 NSIS 安装器，源码分支不提交 `.exe` 二进制产物。

### 下载信息

- Windows 安装包：`feedgrab-desktop-setup-0.1.15.exe`
- 下载地址：https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.15-20260705/feedgrab-desktop-setup-0.1.15.exe
- Release tag：`desktop-v0.1.15-20260705`
- 目标分支：`feedgrab-desktop`
- 文件大小：376917514 bytes
- SHA256：`4C1694E4C418B6AD48FAA9CB9E9C94425AEB6DA69FDD0669982CA840B7DA742C`
- 签名状态：未签名
- 打包时间：2026-07-05 17:54:40 +08:00

### 本版主要修复

- **侧边栏版本号样式修复**：底部"版本号：v0.1.15"去除加粗效果（`font-weight` 700 → 400），并与上方的分隔横线一起在侧栏 footer 内居中对齐；下方作者信息行（作者：强子手记 / 主页 / 推特 / 仓库）保持左对齐不变。
- **设置迁移保留用户 OBSIDIAN_VAULT**：修复桌面端设置迁移逻辑，保留用户已配置的 `OBSIDIAN_VAULT` 路径，不再因检测到 legacy 默认路径而误清空用户已设置的值。

### 已做验证

- 桌面端：`npm test`（83 passed）、`npm run lint`（通过）、`npm run build`（通过）
- Python：`pytest tests/test_service_desktop.py tests/test_worker_protocol.py tests/test_service_layer.py`（100 passed）
- Release asset：通过 GitHub API 获取真实 `browser_download_url`，并用 `curl.exe -I -L` 核验最终不是 404。

### 说明

本安装包仍是桌面端预览版本，不是 main 分支正式 CLI 发布。安装包不包含任何真实 Cookie、Token、API Key 或用户登录态；真实登录信息只会保存到用户设置的 `登录态和数据目录`。
