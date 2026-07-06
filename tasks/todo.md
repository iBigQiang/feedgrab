# 飞书视频媒体保存修复（第二轮实测修复）· 2026-07-06

> 用户报告：Codex 第一轮修复后实测视频仍保存不到（codex thread 019f35e7-af3c-7142-97be-1c2af3c37ced）
> 要求：实测文档保存到视频为止才算修复完成

---

## ✅ 还原 Codex 沟通过程（已完成）

- [x] 定位 Codex 会话日志 `~/.codex/sessions/2026/07/06/rollout-...019f35e7....jsonl`
- [x] 还原关键事实：Codex 沙箱访问飞书被 `ERR_NETWORK_ACCESS_DENIED` 拦截，第一轮修复**从未真实实测**，只过了单测
- [x] 复用 Codex 留下的 DOM 探测数据 `.tmp/feishu-media-live/inspect-{EuJo,YTnp}.json`（真实字段：`type=file` + `snapshot.file.{token,mimeType,size,name}`）

## ✅ 实测复现与定位（已完成）

- [x] 诊断脚本 `.tmp/diag_feishu_media.py` 直调 `evaluate_feishu_doc` + `blocks_to_markdown`，确认提取层/渲染层在 Codex 最终版已通（用户 14:21 实测的是中间版代码）
- [x] 发现遗留问题 1：下载优先 DOM 播放流 URL → 拿到的是**转码预览版**（22.4MB `.mov` 只下到 4MB）
- [x] 发现遗留问题 2：`feishu.py` 用 std logging 无 handler，下载 INFO 日志全程不可见，用户无法确认下载结果
- [x] 发现遗留问题 3：接受 206 部分响应 + 不校验 content-type，存在残片/错误页写成 `.mp4` 的风险

## ✅ 修复（已完成）

- [x] `_download_media_via_cdn`：原始文件端点 `/download/all/{token}/` 优先，DOM 流 URL 兜底（含相对路径补全 origin）
- [x] 响应校验：仅接受 200 且 content-type 非 text/html / application/json
- [x] `_get_media_info` 补提取 `size`（dict 与 attr 两分支），下载大小不符原件时日志标注 transcoded variant
- [x] `feishu.py` logger 换 loguru（一行改动，全部日志可见）

## ✅ 测试（已完成）

- [x] 更新 `test_blocks_to_markdown_renders_video_file_as_local_preview`（media dict 增加 size 字段断言）
- [x] 旧用例 `test_download_feishu_media_prefers_discovered_video_url`（断言旧的错误优先级）改写为两个新用例：
  - `test_download_feishu_media_prefers_original_file_url`
  - `test_download_feishu_media_falls_back_to_discovered_video_url`
- [x] `pytest tests/test_feishu_wiki.py tests/test_feishu_sheet_decode.py`：17 passed
- [x] `pytest tests`：358 passed
- [x] 注：本机 pytest 需 `--basetemp=.tmp/pytest-tmp`（`C:\Users\Qiang\AppData\Local\Temp\pytest-of-Qiang` 权限拒绝）

## ✅ 实测验证（已完成 — 用户验收标准）

- [x] `EuJowYkE6iM6fgk5Ykdc6boHnuh`（软件篇）：4 视频 + 3 图片全部落地；4 个 mp4 与飞书原件**逐字节一致**（4667460 / 4224672 / 5071178 / 6718760 bytes）；ftyp/moov/mdat box 结构完整；md 内 4 个 `<video>` 引用对应
- [x] `YTnpw38qhidAqAkYkL9cKPRpnKf`（云帧SOP）：2 视频（mp4 + mov）+ 2 图片全部落地；`.mov` 22403461 bytes 原件（修复前仅 4MB 转码版）
- [x] 桌面端同链路 `FetchService.fetch_url()` 实测 `ok=True`，产物一致

## ⏳ 待办

- [ ] 用户桌面端开发版复测确认（安装版 v0.1.15 为打包时旧代码，需下一次打包才带上本修复）
- [ ] `/ship` 或 `/ship-desktop` 收尾提交（注意 `.tmp/`、`.workbuddy/` 等 untracked 不要误提交）

## 复盘

**根因链**：
1. 第一轮（Codex）修复了真正的渲染断链（file block 渲染 + 容器 children 递归），方向正确
2. 但 Codex 沙箱无法访问飞书，"已完成"只是单测通过，DEVLOG 里却标了"已完成"
3. 用户最后一次实测（14:21）发生在 Codex 最后一轮代码（14:33）之前，实测的是中间版
4. 真实实测后发现的增量问题是下载 URL 优先级（转码版 vs 原件）与日志可见性

**经验**：
- 网络抓取链路的"完成"必须以真实 URL 端到端实测为准，单测只能锁行为不能证明链路可用
- 声称"优先使用 DOM 真实播放 URL"前要先验证该 URL 返回的是什么——播放器流 URL 通常是转码预览，不是原件
- 下载类功能日志必须可见（loguru），否则用户无法区分"没触发"和"触发了但失败"
