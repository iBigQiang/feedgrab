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

---

# X 视频 Obsidian 内嵌播放 + 桌面端媒体下载开关 · 2026-07-10

- [x] schema.py 视频渲染改 `<video controls src>`（含引用推文，URL html.escape）
- [x] media.py `_replace_urls_in_md` 兼容转义 URL 形式
- [x] platform_settings.py + App.tsx fallback 暴露 `X_DOWNLOAD_MEDIA`（"推文媒体下载到本地"，单篇/线程采集分组）
- [x] pytest 360 passed（+2 新用例）；desktop test 83 passed / lint / build 通过
- [x] 真实推文双模式实测：在线 `<video src=在线mp4>` / 本地 attachments 3/3 落地、mp4 结构校验通过
- [x] DEVLOG + .env.example 更新

## 复盘

改动极小（4 文件 + 测试）：视频内嵌复用飞书已验证写法；下载开关后端链路早已完整，仅补一个 schema 字段即自动贯通 UI→IPC→os.environ→config。唯一坑点是 `<video src>` 内 URL 经 html.escape 后与下载替换逻辑的原始 URL 不一致，已在 media.py 兼容。引用推文媒体仍在线引用（原有下载清单范围），如需扩展另立迭代。

## 追加修复（用户实测反馈）· 引用推文媒体未本地化

- [x] 根因：extra["images"/"videos"] 聚合只含线程推文自身媒体，quoted_tweet 媒体从未入下载清单
- [x] 修复：schema.from_twitter 单点并入 quoted 媒体（去重保序），覆盖单篇 + 7 个批量调用点
- [x] pytest 361 passed（+1 用例）；实测 attachments 5/5 落地，引用 mp4 2.2MB ISO MP4 校验通过，md 无在线残留

---

# 桌面端自动更新 spawn EFTYPE 修复 · 2026-07-10

- [x] 根因：updater.ts downloadFile 的 data 回调从未 fileStream.write(chunk)，安装包落盘 0 字节 → spawn 空 exe 报 EFTYPE
- [x] 修复：补写盘 + content-length 大小校验 + spawnInstaller Promise 化（监听 error/spawn 事件）+ 报错附安装包路径
- [x] UI：下载进度 / 更新报错统一改左下角版本号上方气泡（进度持续显示，报错停留 8s 可换行）
- [x] 验证：desktop test 83 passed / lint / build 通过；electron 真实 URL 实测 2375312 bytes 完整落盘 + zip 结构校验
- [ ] 随下一次桌面端打包（0.1.19）发布后用户实测自动更新闭环
- [x] 端到端实测（0.1.17→0.1.18）：更新按钮 → 弹窗 → 气泡进度 → 376926034 bytes 完整落盘 → 安装器启动成功 → 注册表 DisplayVersion 0.1.18
- [x] 追加：spawn 参数补 --force-run，静默安装完成后自动重启新版应用（实测 D:\feedgrab Desktop 自动拉起）；安装中气泡文案说明静默安装行为
- [x] 实测遗留清理：package.json 版本还原 0.1.18、临时脚本/日志/TEMP 安装包已删

---

# 公众号批量采集 200013 限流与失败页落盘修复 · 2026-08-21

> 方案文档：`docs/开发及迭代方案调研报告/2026-08-21-微信公众号批量采集200013限流与失败页落盘修复方案.md`
> 触发：`feedgrab mpweixin-id "袋鼠帝AI客栈"` 报 ret=200013，CLI 却打印"✅ 抓取完成，总数 0"

## 诊断（已完成）

- [x] 实测拿到微信官方原文 `{"ret":200013,"err_msg":"freq control"}`
- [x] 限流边界矩阵：换目标号(4个)/换微信账号/换接口/换 count(1,5,20) 全部 200013；查自己的号 + searchbiz + 单篇正文 + 搜狗全部正常
- [x] 根因：历史已抓 782 篇 × page_size=5 = 150+ 次列表请求，配额耗尽
- [x] 连带查出 44 篇失败页空壳落盘（39 篇源内容失效 + 5 篇风控验证页），产生于 `browser.py:783-798` fallback 分支
- [x] 连带查出残留进度 `next_begin=15` 因清理条件缺陷永远清不掉 → 老码小张最新 15 篇永远漏抓
- [x] 已核验 5 篇风控页对应真实 URL 未入 dedup，重跑可补回

## 实施

- [x] P0-1 限流不再谎报成功：`MPWeixinFreqControlError` + 日志补 err_msg + CLI 明确报错并 exit(1)（未知 ret 也一并改为抛出，原先静默当"列表读完"）
- [x] P0-2 失败页识别：`browser.py` 加 `detect_wechat_unavailable` → `unavailable_reason`；**四条链路全部接入**（单篇 wechat.py 明确终止不降级 Jina / 批量 mpweixin_account.py / 专辑 mpweixin_album.py / 搜狗 wechat_search.py）；删除违规隐私 skip+入 dedup、captcha fail 不落盘不入 dedup、连续 5 篇 captcha 中止
- [x] P0-3 进度语义：页内保存写当前页 begin、整页完成才推进；`completed` 标志，仅正常读完/日期截止才清进度
- [x] P1 减压配置：`MPWEIXIN_ID_PAGE_SIZE=20` / `PAGE_DELAY=8` / `PAGE_JITTER=0.4` / `MAX_ARTICLES=0` / `FREQ_RETRY=0`
- [x] P0-4 存量清理：44 篇空壳 md（893→849）+ 1 个残留进度文件，删除前已备份至 `.tmp/removed_wechat_shells/`
- [x] `.env.example` 同步新配置 + 限流成因说明

## 验证

- [x] 限流呈现实测：日志 `ret=200013 err_msg=freq control`，CLI 中文说明 + 建议，退出码 1，无 ✅；`offset=0 count=20` 新页长已生效
- [x] 失败页识别实测：已删除文章 URL → "该内容已被发布者删除"，未降级 Jina、未落盘、退出码 1
- [x] 搜狗链路实测：已删除 / 违规 / 隐私三类 URL 分别返回 `deleted` / `violation` / `privacy`
- [x] 正常路径不回归实测：正常文章 `Browser OK` 完整落盘，退出码 0
- [x] 单测：限流抛出 + 进度保留 + 占位页四态 + dedup 分流 + 抖动区间 + 配置上限 + 空页边界
- [x] 全量回归 `pytest tests --basetemp=.tmp/pytest-tmp`：380 passed（基线 361，+19）
- [x] `count` 上限实测：用「查自己的号」绕开 200013，`count=5/20/40` 均 `ret=0 ok`，确认调大页长不会被服务端拒绝
- [x] 桌面端链路核验：`FetchService` 抛 `ServiceError` → `worker._emit_exception` 提取 code/message 发渲染进程 → 批量 continue 不中断
- [x] 收尾三关：质量门禁（380 passed + compileall）/ 代码审查（发现并修复 1 处）/ 安全审查（无 HIGH/MEDIUM 发现）

## 待限流解除后补测（不阻塞本轮交付）

- [ ] `MPWEIXIN_ID_PAGE_SIZE=20` 是否被微信受理（count 实际上限未知，限流态下无法验证）
- [ ] 完整批量端到端跑通 + 5 篇风控页补抓验证
- [ ] 限流冷却时长观测（未做，探测请求本身可能刷新滑动窗口，需低频 20-30 分钟一次）

## 复盘

**这个 bug 的真正代价不是限流本身，而是限流被伪装成成功。** 微信的 `freq control` 是外部客观限制，等就完事；但 `_fetch_article_list` 把 `ret != 0` 一律 `return [], True, 0`（"列表读完了"），上层照常走完流程打印 ✅，用户看到"总数 0，已抓取 0"完全无从判断发生了什么。同一个坏模式在 `evaluate_wechat_article` 的 fallback 分支重演一次：页面没有 `#js_content` 就把 `page.title()` + `body.innerText` 当成功结果返回，于是 44 篇占位页变成了标题"微信公众平台"的空壳 md，还都计入了 `fetched`。

**共同点**：把"拿到了一个响应"等同于"拿到了想要的东西"。修法也一致——让失败态有名字（`unavailable_reason` / 专用异常），让调用方能分流，让 CLI 能如实报告。

**诊断顺序值得复用**：先拿官方原文（`err_msg` 而不是裸 ret 码），再做边界矩阵（换目标/换账号/换接口/换参数四个维度），矩阵一出来，"多账号轮换"这个最直觉的方案当场被否决，省掉了一轮无效实现。

**范围扩张是对的**：原方案只写了批量层接入占位页识别，实施中发现 `evaluate_wechat_article` 被四条链路共用，只改一处等于留三个同样的洞。搜狗链路还自己复制了一份 fallback 逻辑，必须同步改。

**收尾审查抓到了自己挖的坑**：`/code-review` 查出我新加的 `if not articles: completed = True` 把"本页没解出图文"当成"列表读完"，会清进度并漏抓后续——和这次要消灭的那类静默失败一模一样。实测确认 `is_complete` 只在 `publish_list` 为空时为真（`begin=0 size=20 → articles=12, is_complete=False`），两者语义不同，已收紧并补测试。**教训**：修某类 bug 时最容易用同一类写法引入它，收尾审查不能因为"是我刚写的"就跳过。

**遗留**：完整批量端到端仍被限流挡着（`count=20` 已单独实测被服务端受理）；冷却时长未观测；`art_page` 异常时不关闭是 pre-existing 泄漏，本轮未修（见 DEVLOG 已知遗留）。
