# X 全渠道权限核验 + 账号私有渠道锁主账号 · 2026-08-31

> 用户要求：除历史书签外，核查 x.com 还有哪些渠道必须主账号权限，做成和书签一样锁定
> `sessions/twitter.json`；公开页面继续小号轮换。**不要猜测，用真实抓取验证。**
> 方案文档：`docs/开发及迭代方案调研报告/2026-08-31_X平台全渠道权限实测与主账号锁定方案.md`

---

## ✅ 权限矩阵实测（已完成）

- [x] 18 项渠道 × 2 账号（主号 @iBigQiang / 备用号 x_2.json = @nuklein）交叉实测
- [x] 排除伪信号 1「探针结构误判」：`TweetResultByRestId` 返回 `data.tweetResult` 无 `entryId`，
      按 entryId 计数把单贴/长文误报 EMPTY → 改用递归查 `full_text` 判定
- [x] 排除伪信号 2「无数据 ≠ 无权限」：先按 `favorite_count`/`retweet_count` 筛出高互动第三方推文
      （@rehan_shei，12817 赞/995 转）再重测，结论翻转
- [x] 定性三级边界：公开（任何登录态）→ 账号本人（likes）→ 推文作者本人（favoriters）

## ✅ 代码实施（已完成）

- [x] `twitter_user_tweets.py`：likes 走 `primary_only=True`，tweets/replies 保持轮换
- [x] `twitter_retweeters.py`：`_MODE_CONFIG` 加 `primary_only` 字段（favoriters=True / retweeters=False）
- [x] `reader.py`：`_read_user_likes` / `_read_tweet_user_list(favoriters)` 缺主账号即 `RuntimeError`
- [x] 修正两处错误归因文案（原把平台机制说成"对方设了隐私/隐藏了列表"，会误导用户去调设置）
- [x] 修复书签路由：X 迁到 `/i/history`，用精确正则匹配避免未来 `/i/history/<其他子页>` 被吞

## ✅ 端到端实测（10 项全部产物落地）

- [x] 总书签 `/i/history`（新修路由）→ 16/20 落地，索引 11858→11874
- [x] 账号喜欢 `/likes`（新锁主账号）→ 18 条落地，索引 11874→11892
- [x] 点赞者 `/status/<id>/likes`（新锁主账号）→ 39 个用户
- [x] 转推者 `/status/<id>/retweets`（回归轮换）→ 36 个用户
- [x] 单贴 → Tier 0 GraphQL 落地
- [x] 长文 → Article `content_state` 原生渲染落地
- [x] 列表批量 → 发现 94 / 新增 5 / dedup 跳过 89 / 失败 0
- [x] 书签文件夹批量 → 14/20 落地，索引 11958→11972
- [x] 账号批量 → 15/16 落地，索引 11977→11992
- [x] 词组批量搜（忽略过滤项）→ 359 条 / 2 关键词
- [x] 单元测试 412 → 423 passed（+11）

## ✅ 文档收尾（已完成）

- [x] `DEVLOG.md` 顶部新增 v0.25.1 条目
- [x] `CLAUDE.md` X 分平台约定补 primary_only 渠道清单 + 书签 URL 现行形态
- [x] `README_en.md` 更新书签 URL、修正 likes/favoriters 措辞、新增权限矩阵小节
- [x] `pyproject.toml` 0.25.0 → 0.25.1
- [x] `tasks/lessons.md` 补强 heredoc 反斜杠教训（含控制字符自检步骤）

## 结果复盘

**做对的**：坚持"不要猜测，用真实抓取验证"。两个伪信号如果不排除，会得出两个相反的错误结论 ——
第一版探针会认定"单贴/长文也需要主账号"（过度收紧，把公开渠道也锁死），第二版未筛高互动推文时
会认定"retweeters 也需要主账号"（同样过度收紧）。是"先确认目标本身有数据"这一步救回来的。

**做错的**：写 DEVLOG 时在 quoted heredoc 里写 Windows 路径 `X` + 双反斜杠 + `bookmarks`，
被压成一层后 `\b` 当退格转义落盘，产出肉眼不可见的污染。`tasks/lessons.md` 2026-08-21 已记录过
同类坑（`✅` 被折叠），规则也写对了，但这次没执行。已在 lessons 里补上"路径写正斜杠 +
写完 grep 控制字符自检"两条可机械执行的步骤，把"记得小心"降级成"跑一条命令"。

**顺带发现（非本轮引入，未处理）**：
- likes 尾部 TwitterAPI.io 补充抓取报 `HTTP 402 Credits is not enough`（付费额度耗尽），
  主链路不受影响，但额度不足时应跳过该 Tier 而非报错
- `twitter_graphql.py → _prompt_cookie_refresh_via_cdp()` 的 `print("警告 ...")` 在非 `cli.main()`
  入口（MCP / service / 直接脚本）会因 GBK 编码崩溃

---

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

---

# X 书签文件夹批量抓取失效修复 · 2026-08-31

> 用户报告：抓 `https://x.com/i/history/bookmarks/2012897882299572505` 结果不对，
> 早期版本已实现该功能（产物在 `E:\Obsidian\Qiang_Obsidian\inbox\X\bookmarks`）。
> 用户判断：X 书签链接结构变了，多了 `history` 目录段。要求实测接口/DOM 字段变化后修复。

## ✅ 诊断（已完成）

- [x] 复现：`/i/history/bookmarks/<id>` 被 `_detect_platform` 漏判 → fall-through 到 `return "twitter"`，当成单篇推文抓
- [x] 用户判断得到证实：新 URL 形态确实是根因第一环（旧形态 `/i/bookmarks/<id>` 路由正常）
- [x] 实测 5 个备用账号 `x_2`~`x_6` 的 `BookmarkFoldersSlice`：一致返回 HTTP 200 + `errors[code=37, AuthorizationError]`
      "User is not authorized to use bookmark collections"，`data.viewer.user_results.result` 只有 `__typename`
- [x] 排除"字段改名导致解析断裂"的可能：`data` 结构与代码 Path 0（`viewer.user_results.result.bookmark_collections_slice`）吻合，
      是服务端用 errors 拒绝内容，不是解析 bug；queryId `i78YDd0Tza-dV4SYs58kRg` 仍有效（否则会是 operation not found）
- [x] 实测「全部书签」`Bookmarks` operation（免费功能，无需 Premium）：5 个账号全部 200 + **无 errors** +
      `data.bookmark_timeline_v2.timeline.instructions = []` → **登录态有效，只是这些号书签为空**，整条 GraphQL 链路健康
- [x] 实测 `BookmarkFolderTimeline` 响应字段：`bookmark_folder_timeline` → **`bookmark_collection_timeline`**（X 把 folder 改叫 collection）
- [x] 确认 `sessions/twitter.json` 已退化游客态（9 个 cookie 全 guest，无 `auth_token`/`ct0`），
      因此 `_load_all_cookie_sets()` 跳过它、选中无 Premium 的 `x_2` —— 主账号重登后会自动优先，无需改优先级代码
- [x] 佐证主账号历史上确有权限：`X/bookmarks/1997844819398533499/` 存有 6 篇 md（4 月 21 日）

## ✅ 修复（已完成，4 处）

- [x] `reader.py:44` `_detect_platform` 识别 `/i/history/bookmarks`（旧形态保留）
- [x] `twitter_bookmarks.py` `_parse_bookmark_url` 正则改 `/i/(?:history/)?bookmarks(?:/([0-9]+))?`（用 `[0-9]` 规避转义折叠）
- [x] `twitter_graphql.py` `parse_bookmark_entries` 补 Path 2b `bookmark_collection_timeline`
- [x] **静默失败分流**（本轮真正的质量修复）：`twitter_bookmarks.py` 新增 `_graphql_error_summary()`，
      分页循环区分「空响应=正常读完」与「200+errors=失败」；首页失败 `raise RuntimeError`，
      中间页失败保留已抓数据并 break；提示明确指出"书签是账号私有数据，轮换账号读不到同一份书签"
- [x] **姊妹 bug**：`fetch_bookmark_folders` 同样把权限错误降级成含糊的 "Could not parse folders" + "Found 0 folders"，
      改为 ERROR 级明确报出原文 + "需要 X Premium 且只能读自己的文件夹"

## ✅ 测试（已完成）

- [x] 新增 `tests/test_twitter_bookmarks_url.py`：8 个用例（新旧 URL 路由 + 邻近路由无回归 + 四形态解析 +
      `bookmark_collection_timeline` 解析 + 错误摘要 + 权限错误抛异常 + 真空响应正常结束 + 文件夹权限错误命名）
- [x] `pytest tests --basetemp=.tmp/pytest-tmp`：398 passed（基线 390）
- [x] 端到端行为实测：修复前静默上报 total=0，修复后抛
      `RuntimeError: 书签抓取失败：Authorization: User is not authorized to use bookmark collections. 提示：书签是账号私有数据……`

## ⏳ 阻塞中：真实抓取验证（需用户操作）

- [ ] **恢复主账号登录态**：`feedgrab login twitter`，或开 `chrome --remote-debugging-port=9222` 登录 X 后走 CDP 提取
      （当前 CDP 9222 不可达，5 个备用号均无 bookmark collections 权限，书签是账号私有数据 → 轮换在语义上无解）
- [ ] 用主账号对 `https://x.com/i/history/bookmarks/2012897882299572505` 端到端抓取，确认 md 落地到 `X/bookmarks/<文件夹名>/`
- [ ] 浏览器实测定性 `/i/history/bookmarks/<id>` 页面实际调用的 operation（现有间接证据已指向 `BookmarkFolderTimeline` 未改名）
- [ ] 按 `lessons.md`「网络抓取类必须真实 URL 实测到产物落地」，**验证通过前不写 DEVLOG、不 ship**

## 附带发现（不阻塞本轮）

- [ ] `[GraphQL] Failed to init transaction generator: 'NoneType' object has no attribute 'group'` 每次调用必现：
      第三方库 `xclienttransaction` 1.0.2（2026-03-18）的正则跟不上 X 前端改版，PyPI 已有 1.0.3（2026-06-26）。
      不影响书签（该接口不需要 transaction-id），但影响 SearchTimeline。属环境变更，待用户确认后再升级
- [ ] `desktop/.venv` 是只有 pip 的空壳孤儿 venv（全项目零引用，desktop 走 npm、Python worker 在 `runtime/feedgrab-runtime/`），是否清理待用户发话

---

## 156. X 书签：主账号优先机制（08-31 续，同批次）

> 用户要求："登录态每次都是优先使用 twitter.json 的 无效才轮换 x_2.json 到 x_6.json，
> 至少以后书签这种要权限的必须 这个账号优先，其他推文公开页面还是多账号轮换"

### 核心洞察

书签是**账号私有数据**：轮换到备用号只能读到那个账号自己的书签，或直接权限错误。
所以私有数据场景的正确行为不是"多试几个账号"，而是"钉住主账号 + 失败即明确报错"。

### 实施

- [x] `twitter_cookies.py` 新增主账号概念：`_PRIMARY_SOURCE_LABELS`（env / `twitter.json` /
      `x.json` / CDP 算主账号；带下标的 `x_2`~`x_6`、`twitter_2` 算轮换备用号）
      + `is_primary_cookie_label()` + `load_primary_twitter_cookies()`（无主账号返回 {} 并 ERROR 指引登录）
- [x] `fetch_with_cookie_rotation(..., primary_only=True)`：走 `_fetch_with_primary_cookie()` 单发不轮换；
      无主账号时**连请求都不发**（避免拿备用号白跑一趟才报权限错误）。其余 6 个批量 fetcher 不受影响
- [x] `reader.py::_read_bookmarks` 改用 `load_primary_twitter_cookies()`，失败信息说明"不能用备用号代抓"
- [x] `twitter_bookmarks.py` 分页两个分支均传 `primary_only=True`，日志文案同步为单账号语义

### 顺带修掉的三个真实缺陷

- [x] **migration 遮蔽 bug**（根因级）：`_load_all_playwright_sessions()` 原本只在 `twitter.json`
      **不存在**时才从 legacy 目录迁移。游客态空壳（文件存在但无 auth_token）会永久遮蔽
      `~/.feedgrab/sessions/twitter.json` 里的有效登录态 → 主账号被静默跳过、直接轮换备用号。
      改为"不存在**或**无有效 token"才迁移，且只接受 legacy 里**有有效 token** 的文件（不倒退覆盖）
- [x] 空壳 session 被跳过时打 WARNING（点名文件、缺哪个 cookie、"游客态空壳"），不再静默
- [x] **首页无响应仍上报成功**（与上一节权限错误同款坏模式的另一半）：401 让 `response=None` 时
      `break` 出循环 → 返回 `total=0` → CLI 打印 `✅ 书签抓取 0/0`。改为首页无数据即
      `raise RuntimeError`（含 401/403 成因 + `feedgrab login twitter` 指引）；中间页失败仍保留已抓数据
- [x] **GBK 控制台 crash**：401 提示里的 emoji 在 GBK codepage 下 `print` 抛 `UnicodeEncodeError`，
      把"请重新登录"这句最关键指引变成崩溃。`cli.main()` 入口新增 `_harden_stdout_encoding()`
      （`reconfigure(errors="replace")`）；`PYTHONIOENCODING=gbk` 实测不再崩

### 诊断能力

- [x] `feedgrab doctor x` 的 Cookie 段按角色分区：主账号（缺失则 ERROR 并说明"书签等私有数据将无法抓取"）
      / 备用号 N 个（标注"仅用于公开内容轮换"）。这次困惑的根源正是看不出 `twitter.json` 是空壳

### 测试

- [x] 新增 `tests/test_twitter_primary_account.py`：12 个用例（主/备标签判定 × 2、主账号加载优先与
      只有备用号时返回空、`primary_only` 不轮换/取主账号响应/无主账号不发请求/吞异常、公开内容仍轮换回归、
      空壳不遮蔽 legacy、有效 session 不被 legacy 覆盖、空壳 WARNING）
- [x] `tests/test_twitter_bookmarks_url.py` +2：首页无响应必须抛异常、书签分页必须传 `primary_only=True`
- [x] `pytest tests --basetemp=.tmp/pytest-tmp`：**412 passed**（本批基线 398 → 412）
- [x] 端到端实测：修复前 401 场景输出 `✅ [twitter] 书签抓取 0/0`，修复后输出
      `❌ 书签抓取失败：主账号请求无响应（常见原因是登录态过期，GraphQL 返回 401/403）…`

### 实测发现（重要）

- migration 修复**一修即生效**：`sessions/twitter.json`（游客态空壳，2049B）被
      `~/.feedgrab/sessions/twitter.json`（10588B，含 40 位 auth_token）自动覆盖，
      `doctor x` 随即认出主账号 `a9f00def...`。**属修复的预期行为**（空壳无信息价值）
- 但该 legacy token 实测 **GraphQL 401**（Bookmarks 与 BookmarkFolderTimeline 均 401）→
      主账号登录态确实已过期，**重新登录仍是唯一剩余阻塞**

### 仍待办 -> 已全部关闭（2026-08-31，强哥手动重新登录后）

- [x] `feedgrab login twitter` 恢复主账号 -> 端到端确认 md 落地（见下方 158 的 10 项实测矩阵）
- [x] 登录后复核 transaction generator 已自愈（确认它是"无有效登录态"的下游症状：
      x.com 返回的是 32553 字节登录墙页面，不含 `ondemand.s` manifest，故正则命中 None）
- [x] 验证通过后写 DEVLOG（v0.25.1）+ `/ship`

### 157. Twitter 登录态定性实测（账号身份反查）

强哥质疑「twitter.json 登录态是有效的啊」，遂做完整对照实测。结论：**该文件登录态确实已失效**，
但这个信号有价值 —— 它暴露了诊断能力的缺口（此前只凭单次 401 下结论，无对照、无身份反查）。

**五重证据（同一 header 构造，唯一变量是 cookie）**

| 验证手段 | `sessions/twitter.json` | `x_2`~`x_6` |
|---|---|---|
| GraphQL Bookmarks | 401 | 200 无 errors |
| `api.x.com/1.1/account/verify_credentials` | 401 code=32 | 404 code=34（端点下线，鉴权通过）|
| `x.com/i/api/1.1/account/multi/list` | 401 code=32 | 200 + screen_name |
| 浏览器加载 storage_state 开 `/i/bookmarks` | 重定向 onboarding 登录页 | — |
| 页面内 `settings.json` | 403 | — |

**账号身份清单（`x.com/i/api/1.1/account/multi/list.json` 反查）**

| 文件 | 账号 | 鉴权 |
|---|---|---|
| `sessions/twitter.json` | 无法反查（401） | ❌ 失效 |
| `x_2.json` | @nuklein | ✅ |
| `x_3.json` | @Sim_c17 | ✅ |
| `x_4.json` | @CCisler89 | ✅ |
| `x_5.json` | @lavezzi19 | ✅ |
| `x_6.json` | @RAPHYHBK | ✅ |

→ 5 个备用号全是陌生小号，**@iBigQiang 不在其中**。这从正面印证了本轮 `primary_only` 机制的必要性：
书签是 @iBigQiang 私有数据，轮换这 5 个号在语义上永远读不到，只会白跑 + 掩盖真因。

**诊断方法沉淀**
- `x.com/i/api/1.1/account/multi/list.json` 是可用的身份反查端点（`api.x.com/1.1/...` 已 404）。
  值得考虑接入 `doctor x`，让每个 cookie 槽位直接显示 `@screen_name` + 鉴权状态。（待办）
- Chrome 磁盘直读 cookie 不可行：Chrome 137+ App-Bound Encryption 使 `browser_cookie3` 报
  `RequiresAdminError`，需管理员权限。
- 项目托管 CDP（`_start_managed_chrome_cdp`）用独立 profile
  （`sessions/.browser-profiles/twitter-cdp`），不复用本机 Chrome 登录态，故仍需手动登录一次。

**下一步（阻塞在强哥）**：`feedgrab login twitter` 用 @iBigQiang 重新登录 → 复跑
`feedgrab https://x.com/i/history/bookmarks/2012897882299572505` → 确认 md 落地
`X/bookmarks/<文件夹名>/` → 再写 DEVLOG v0.25.1 + `/ship`。

---

## 158. X 全渠道权限核验 + 账号私有渠道锁主账号（v0.25.1 交付）

强哥的原始要求：「除了历史书签批量抓取以外，还有哪些是必须要主账号权限才能抓取的，做成和书签一样锁定主账号
`twitter.json` 登录态；其他不强调权限的公开页面还是小号轮换机制 …… **不要猜测，用真实抓取验证**」。

### 核验（已完成）

- [x] 18 项渠道 x 2 账号（主号 `@iBigQiang` / 备用号 `x_2.json`=`@nuklein`）交叉实测
- [x] 排除两个伪信号：探针按 `entryId` 计数误报单贴/长文为 EMPTY（`TweetResultByRestId` 返回
      `data.tweetResult`，结构里没有 `entryId`）；`timeline: {}` 只说明"没内容返回"，
      必须先确认目标本身有数据再归因（换用第三方 12817 赞 / 995 转推文重测，结论当场翻转）
- [x] 确认三级边界：公开（任何登录态）-> 账号本人（书签 / 用户 likes）-> 推文作者本人（favoriters）

### 实施（已完成）

- [x] `twitter_user_tweets.py`：`primary_only=(mode == "likes")`，tweets / replies 不受影响
- [x] `twitter_retweeters.py`：`_MODE_CONFIG` 新增 `primary_only` 字段（favoriters=True / retweeters=False）
- [x] `reader.py`：`_read_user_likes` / `_read_tweet_user_list` 缺主账号前置 `RuntimeError` 并解释"为什么备用号没用"
- [x] 修正两处把平台机制误说成对方隐私设置的文案
- [x] 修复书签 URL 路由：X 已迁到 `/i/history`（+ `/i/history/bookmarks/<id>`），legacy `/i/bookmarks` 仍解析

### 审查三关回头改（`/ship` 第 3-5 步，已完成 6 处）

- [x] CLI `x-favoriters` 补主账号闸门 —— 此前缺主账号时用备用号发请求，打印"总数：0"，
      看起来像这条推文没人点赞（code-review 找到）
- [x] `parse_tweet_user_list_url` 两处正则加 `(?:[\w-]+\.)?` 子域组 —— `www.x.com` / `mobile.twitter.com`
      会照常路由进来，正则漏子域就解析成 `(None, None)`，闸门落空且功能整条 `ValueError`
- [x] 新增 `mode_requires_primary()` 读 `_MODE_CONFIG`，reader 与 CLI 共用 —— 消掉"两处真相"
- [x] 新增 `cookie_rate_limit_remaining()` + 归因分叉 —— 钉死单账号后轮换的"跳过限流账号"失效，
      15 分钟冷却会被报成"登录态已过期"，误导用户重新登录（code-review 找到）
- [x] 路由正则 `\d` 统一为 `[0-9]` —— 与 `_parse_bookmark_url` 口径对齐，
      否则阿拉伯-印度数字的 folder id 会让"抓某个文件夹"静默变成"抓全部书签"（security-review 顺带发现）
- [x] `desktop/renderer/src/App.tsx` 输入框示例改 `/i/history/bookmarks/<id>` + 补"X 总书签批量"一行

### 测试（已完成）

- [x] 三个文件共 46 例，**390 -> 436 passed**（`pytest tests/ -q --basetemp=.tmp/pytest-tmp`）：
      `test_twitter_primary_account.py` 20（策略本身）/ `test_twitter_bookmarks_url.py` 14（URL 三形态 + 口径一致）/
      `test_twitter_primary_gate.py` 12（入口覆盖面 + 失败归因）
- [x] 数字口径核实：`--ignore` 掉三个新文件跑出 390，即基线；156 节里写的"398 -> 412"是当时的错误计数，以本节为准

### 真实抓取验证（已完成 —— 不是跑测试，是真抓）

- [x] 10 项渠道端到端矩阵全部产物落地（详见 DEVLOG v0.25.1 表格）
- [x] 修复后三条复验：CLI `x-favoriters` 主账号 5 页 **194 用户**（落地 `.md` + `.csv`）/
      CLI `x-retweeters` `[6/6 可用]` 2 页 **47 用户**（确认公开渠道没被闸门带走）/
      `https://www.x.com/.../likes` 走 reader 路由 **194 用户**（修复前必抛 `ValueError`）

### 安全审查（已完成）

- [x] `/security-review`：**无达到报告门槛的安全漏洞**
- [x] 两条非安全观察已修（见上"审查三关回头改"第 3、5 项）
- [x] 两条经核实驳回：`_fetch_home_html` 落盘缓存不含凭据值且 `sessions/` 已 gitignore、
      同目录本就有明文 cookie，增量暴露为零；主/备账号选错的后果是空结果而非越权
      （授权由 X 服务端 ACL 执行，本地闸门只影响体验）

### 结果复盘

**做对的**：坚持"不要猜测，用真实抓取验证"这条硬要求，救回两个会写进文档的错误结论
（单贴/长文误判为需要权限、favoriters 误判为公开）。伪信号 2 尤其关键 —— 如果不先确认
"这条推文本身有 995 次转推"，就会把"转推数为 0"读成"没权限"，进而给公开渠道也上主账号锁，
白白牺牲 6 个账号的配额。

**做错的、值得记住的**：
1. 第一版只在 `reader.py` 加闸门，漏了 CLI 入口。同一条策略有两个入口时，改一个就等于没改 ——
   `mode_requires_primary()` 这类"单一判据函数"应该在写第一个调用点时就抽出来，而不是等审查抓到。
2. 主账号闸门把"多账号轮换"这套隐式基础设施抽掉了一半：`load_twitter_cookies()` 内部本来会跳过
   限流账号，钉死单账号后这个能力静默失效。**替换掉一层抽象时，要列清它顺手做了哪些事**。
3. 文档里的测试数字我写的是"412 -> 423"，是凭印象填的。后来用 `--collect-only` + `--ignore`
   实测才拿到 390 -> 436。**凡是要写进文档的数字，一律现场跑一遍再填**。

### 状态：已完成 ✅
