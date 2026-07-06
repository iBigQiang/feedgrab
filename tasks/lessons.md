# 历史教训（lessons）

> 每次收到用户修正后沉淀于此；开工前先回顾，杜绝重复犯错。

## 2026-07-06 · AI 修复"已完成"≠ 实测完成

**背景**：Codex 修复飞书视频保存 bug，单测 357 passed 后在 DEVLOG 标记"状态：已完成"，但其沙箱访问飞书被网络拦截，真实 URL 端到端从未跑通。用户实测视频依然保存不到，返工一轮。

**规则**：
1. 网络抓取/下载类功能，**必须用真实 URL 实测到产物落地**（文件存在 + 大小/结构校验）才能标记完成；单测通过只是必要条件
2. 接手他人（含 AI）声称"已完成"的修复时，先查其验证记录里有没有真实实测；没有就先实测复现，再谈下一步
3. DEVLOG"状态：已完成"必须附实测证据（产物路径、字节数、校验结果），不能只列测试计数

## 2026-07-06 · 播放器流 URL ≠ 原始文件 URL

**背景**：飞书 DOM `<video src>` 的 `/stream/download/video/{token}/` 是转码预览流（22.4MB 原件只返回 4MB），原始文件在 `/download/all/{token}/`。第一轮修复把优先级搞反。

**规则**：
1. 抓取媒体时优先找**原始文件端点**，播放器 src 只做兜底；接入前先对比下载产物与平台记录的原始 size
2. 下载响应必须校验 content-type（拒绝 text/html / application/json 错误页写成媒体文件）、拒绝 206 残片

## 2026-07-06 · 下载类功能日志必须用户可见

**背景**：`feishu.py` 用 std logging（无 handler，INFO 被吞），feedgrab 全局可见日志是 loguru。下载成功与否终端毫无动静，用户无法区分"没触发"和"失败"，加大排查成本。

**规则**：feedgrab 内所有 fetcher 日志统一用 `from loguru import logger`；新增文件时检查 import，不用 `logging.getLogger`

## 2026-07-06 · 本机 pytest 环境

**背景**：`C:\Users\Qiang\AppData\Local\Temp\pytest-of-Qiang` 权限拒绝访问，凡用 `tmp_path` fixture 的用例 setup 即 ERROR。

**规则**：本机跑 pytest 一律加 `--basetemp=.tmp/pytest-tmp`
