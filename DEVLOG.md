# feedgrab DEVLOG

开发日志 — 记录每次升级迭代的确定方案、实施细节和状态追踪，作为项目演进的记忆文件。

## v0.25.1 · 2026-08-31 · X 全渠道权限实测 + 账号私有渠道锁主账号

### 背景

书签批量此前已锁主账号（备用号代抓拿不到别人的书签）。用户要求把 **x.com 全部渠道**逐一核验一遍，
找出还有哪些属于"必须主账号权限"，做成和书签一样锁定 `sessions/twitter.json`；不强调权限的公开页面
继续走小号轮换。明确要求：**不要猜测，用真实抓取验证**。

方案文档：`docs/开发及迭代方案调研报告/2026-08-31_X平台全渠道权限实测与主账号锁定方案.md`

### 实测方法与两个必须排除的伪信号

18 项渠道 × 2 个账号（主号 `@iBigQiang` / 备用号 `x_2.json` = `@nuklein`）交叉实测。过程中踩到两个坑，
都会把"有权限"误判成"无权限"：

**伪信号 1 — 探针结构误判**：`TweetResultByRestId` 返回 `data.tweetResult`，结构里**没有 `entryId`**。
第一版探针按 entryId 计数，把单贴 / 长文误报为 EMPTY。改用递归查找 `full_text` 判定后，两者实际都正常。

**伪信号 2 — 无数据 ≠ 无权限**：`TimelineTerminateTimeline` / `timeline: {}` 只说明"没内容返回"，
可能仅仅是那条推文转推数为 0。必须**先确认目标本身有数据**再归因。做法：从列表 timeline 提取
`favorite_count` / `retweet_count` 排序，挑出高互动的第三方推文（@rehan_shei，12817 赞 / 995 转）重测——
结论随即翻转：retweeters 主号小号都拿到 20 条（公开），favoriters 连主号也是空（作者本人限定）。

### 实测确认的三级权限边界

| 渠道 | 边界 | 交叉实测 |
|------|------|---------|
| 书签 | 账号本人 | 已有结论，本轮沿用 |
| 用户 likes（`/<user>/likes`） | **账号本人** | 我的 likes：主号 OK 20 / 小号 EMPTY；别人的 likes：主号也 EMPTY |
| favoriters（`/status/<id>/likes`） | **推文作者本人** | 我的推文：主号 OK 20 / 小号 EMPTY；别人 12817 赞推文：主号也 EMPTY |
| retweeters（`/status/<id>/retweets`） | **公开** | 别人 995 转推文：主号 OK 20 / 小号 OK 20 |
| 单贴 / 长文 / 账号批量 / 列表 / 列表成员 / 关注列表 / 关键词搜索 / 人物搜索 | **公开** | 主号小号均正常 |

即：X 有三级权限边界 —— 公开（任何登录态） → 账号本人（likes） → 推文作者本人（favoriters）。
对后两者做轮换，只会丢掉唯一可能成功的那个账号。

### 前置基础设施（同批改动，此前未记录）

本轮之前已为「书签锁主账号」铺的底座，与上述改动同批提交：

- `twitter_cookies.py`：新增主账号语义三件套 —— `_PRIMARY_SOURCE_LABELS` 白名单、
  `is_primary_cookie_label()`（`env` / `playwright(twitter.json)` / `cookie_file(x.json)` / `chrome_cdp`
  算主账号，`x_2`~`x_6` / `twitter_2` 算备用号）、`load_primary_twitter_cookies()`（**只**返回主账号，
  绝不降级到备用号）、`fetch_with_cookie_rotation(primary_only=True)`（单发不轮换；无主账号时连请求都不发）
- `twitter_bookmarks.py`：书签两条分页路径均改 `primary_only=True`；`_parse_bookmark_url` 正则
  升级为 `/i/(?:history/)?bookmarks(?:/([0-9]+))?` 兼容新旧两种拼写；新增 `_graphql_error_summary()`，
  主账号请求失败时给出"登录态过期"的明确指引而不是静默 0 条
- `twitter_graphql.py`：① 登录态首页与游客登录墙分两个缓存槽
  （只有已登录页带 transaction-id 签名器需要的 `ondemand.s` manifest，混用会导致签名失败）；
  ② `BookmarkFolders` 的 HTTP 200 + 非空 `errors` 数组显式报错 —— X 用这种形式返回权限失败，
  结构上 `data` 合法但为空，此前会静默退化成"0 个文件夹"
- `cli.py`：`_harden_stdout_encoding()` 对 stdout/stderr 做 `reconfigure(errors="replace")`，
  修 GBK 控制台下 emoji `print()` 抛 `UnicodeEncodeError` 把"请重新登录"提示变成崩溃的问题；
  `doctor x` 按角色分列主账号 / 备用号，主账号缺失时直接 fail 并提示 `feedgrab login twitter`

### 实施

**1. likes 锁主账号**（`twitter_user_tweets.py`）
`fetch_with_cookie_rotation(..., primary_only=(mode == "likes"))`，`tweets` / `replies` 不受影响仍轮换。

**2. favoriters 锁主账号**（`twitter_retweeters.py`）
`_MODE_CONFIG` 新增 `primary_only` 字段：`favoriters=True` / `retweeters=False`，配实测依据注释。

**3. reader 层前置硬失败**（`reader.py`）
`_read_user_likes` / `_read_tweet_user_list(mode=favoriters)` 先 `load_primary_twitter_cookies()`，
缺主账号直接 `RuntimeError` 并解释"为什么备用号没用"，不再拿备用号白跑一趟拿空结果。
retweeters 保持 `load_twitter_cookies()` 轮换。

**4. 修正两处错误归因文案**
- `twitter_user_tweets.py`：原"该用户可能将其 Likes 设为私密（Twitter 默认行为）" → 改为"X 已将点赞列表私有化，只有本人登录态能读"
- `twitter_retweeters.py`：原"可能作者隐藏了点赞列表" → 改为"X 只把点赞者列表展示给推文作者本人"

两处原文案把平台机制说成了对方的隐私设置，会误导用户去调设置。

**5. 修复书签 URL 路由缺陷**（`reader.py → _detect_platform`）
X 已把书签总览从 `/i/bookmarks` 迁到 `/i/history`（文件夹形态同步变成 `/i/history/bookmarks/<id>`），
裸 `/i/history` 此前不被识别为书签 URL。用精确正则 `^/i/history(?:/bookmarks(?:/[0-9]+)?)?/?$` 匹配，
而非前缀匹配 —— 避免未来 `/i/history/<其他子页>` 被静默吞成书签 URL。legacy `/i/bookmarks` 继续解析。
用 `[0-9]` 而非 `\d`：`\d` 还会匹配阿拉伯-印度数字，而 `_parse_bookmark_url` 用的是 `[0-9]+`，
两边口径不一致会让「抓某个文件夹」静默变成「抓全部书签」。

### 端到端实测（10 项渠道，全部产物落地）

| # | 渠道 | 结果 |
|---|------|------|
| 1 | 总书签 `/i/history`（新修路由） | ✅ 16/20 落地 `X/bookmarks/all/`，索引 11858→11874 |
| 2 | 账号喜欢 `/likes`（新锁主账号） | ✅ 18 条落地 `X/likes_author/iBigQiang/`，索引 11874→11892 |
| 3 | 点赞者 `/status/<id>/likes`（新锁主账号） | ✅ 39 个用户（主账号读自己推文） |
| 4 | 转推者 `/status/<id>/retweets`（回归轮换） | ✅ 36 个用户 |
| 5 | 单贴 | ✅ Tier 0 GraphQL 落地 |
| 6 | 长文 Article | ✅ `content_state` 原生渲染落地 |
| 7 | 列表批量 | ✅ 发现 94 / 新增 5 / dedup 跳过 89 / 失败 0 |
| 8 | 书签文件夹批量 | ✅ 14/20 落地，索引 11958→11972 |
| 9 | 账号批量 | ✅ 15/16 落地，索引 11977→11992 |
| 10 | 词组批量搜（忽略过滤项） | ✅ 359 条 / 2 关键词 |

单元测试 390 → **436 passed**（+46，三个文件均为本轮新建）：

| 文件 | 例数 | 锁什么 |
|------|------|--------|
| `tests/test_twitter_primary_account.py` | 20 | 「哪些渠道钉主账号」这条策略本身：实测矩阵写进注释、`_spy_rotation` 断言 `primary_only` 传参、真实空响应形态构造器、空结果文案必须指向"作者本人"而非"作者隐藏" |
| `tests/test_twitter_bookmarks_url.py` | 14 | 书签 URL 三形态（裸 `/i/history` / `/i/history/bookmarks` / 带文件夹 id）+ `/i/history/views` 不被吞 + 路由与解析对 folder id 的口径一致 |
| `tests/test_twitter_primary_gate.py` | 12 | 闸门的**入口覆盖面**与**失败归因**：子域不绕过、`mode_requires_primary` 单一来源、CLI 与 reader 同一套闸门、限流不误诊成登录态过期、白名单锁生成侧真实 label |

### 审查三关修复（code-review / security-review / verify 三轮回头改）

推送前按 `/ship` 跑完三关，又改出 6 处。都是"第一版能跑但边界不对"的类型：

| # | 问题 | 修复 |
|---|------|------|
| 1 | CLI `x-favoriters` 完全没有主账号闸门，缺主账号时用备用号发请求，打印"总数：0" —— 看起来像这条推文没人点赞 | `cli.py → cmd_twitter_tweet_user_list` 加与 reader 同一套闸门 + 同一套中文指引 |
| 2 | `parse_tweet_user_list_url` 正则写死裸域，`www.x.com` / `mobile.twitter.com` 解析成 `(None, None)`；而 `_detect_platform` 只看 domain 含 `x.com` 照常放行 → 闸门落空、功能整条 `ValueError` | 两处正则加 `(?:[\w-]+\.)?` 子域组 |
| 3 | `reader.py` 写死 `mode == "favoriters"` 判断要不要主账号，与 `_MODE_CONFIG` 两处真相 | 新增 `mode_requires_primary(mode)` 读 `_MODE_CONFIG`，reader / CLI 共用；未知 mode 保守要主账号 |
| 4 | 主账号被限流时，`primary_only` 没有可轮换对象，请求必 429，上层只剩"无响应"一个信号 → 报成"登录态已过期"，误导用户重新登录 | 新增 `cookie_rate_limit_remaining()`；请求前查冷却直接终止，失败后按"本次触发限流（登录态没问题，等冷却）"/"无响应（请检查登录态）"分叉归因 |
| 5 | 路由用 `\d`、`_parse_bookmark_url` 用 `[0-9]`，阿拉伯-印度数字的 folder id 会路由进书签但解析成 `type="all"` → "抓某个文件夹"静默变成"抓全部书签" | 路由统一 `[0-9]`，并补一例断言两侧口径一致 |
| 6 | 桌面端输入框示例仍写 legacy `https://x.com/i/bookmarks/<id>` | `desktop/renderer/src/App.tsx` 改为 `/i/history/bookmarks/<id>`，并补一行"X 总书签批量：`https://x.com/i/history`" |

修复后的三条真实抓取复验（不是跑测试，是真抓）：

| 渠道 | 结果 |
|------|------|
| CLI `x-favoriters 2015088004109615266` | ✅ 主账号 `playwright(twitter.json)` / 5 页 / **194 用户** → `output\X\users\favoriters\2015088004109615266_2026-08-31.md` + `.csv` |
| CLI `x-retweeters` 同一推文 | ✅ `load_twitter_cookies [6/6 可用]` / 2 页 / **47 用户**（确认公开渠道仍走轮换，没被闸门带走） |
| `https://www.x.com/iBigQiang/status/2015088004109615266/likes` 走 reader 路由 | ✅ **194 用户**（修复前必抛 `ValueError: 无法识别推文用户列表 URL`） |

安全审查结论：**无达到报告门槛的安全漏洞**。两条非安全观察已在上表第 3、5 项修掉。
另有两条经核实驳回：`_fetch_home_html` 落盘缓存不含凭据值且 `sessions/` 已 gitignore、同目录本就有明文
cookie，增量暴露为零；主/备账号选错的后果是空结果而非越权（授权由 X 服务端 ACL 执行，本地闸门只影响体验）。

### 已知遗留（非本轮引入）

- likes 尾部的 TwitterAPI.io 补充抓取路径报 `HTTP 402 Credits is not enough` —— 付费额度耗尽，
  主链路结果不受影响，后续可考虑额度不足时跳过该 Tier 而非报错。
- `twitter_graphql.py → _prompt_cookie_refresh_via_cdp()` 的 `print("⚠️ ...")` 在非 `cli.main()`
  入口（MCP / service / 直接脚本）会因 GBK 编码崩溃。
- 可选增强：把 `x.com/i/api/1.1/account/multi/list.json` 身份反查接入 `doctor x`，
  让每个 cookie 槽位显示 `@screen_name` + 鉴权状态。

## 2026-08-31 · Reddit 图片在 Obsidian 不渲染修复 + REDDIT_DOWNLOAD_MEDIA

### 背景

用户用桌面客户端抓 `r/ChatGPT` 的 30 prompts 帖，产出 md 在 Obsidian 预览模式下图片不显示，仍是链接；而图片地址在浏览器可正常打开。

### 根因

**直接原因**：产物里是链接语法 `[url](url)` 而非图片语法 `![](url)`，Obsidian 只对后者渲染图片。与图片地址可用性无关。

**代码根因**：`reddit.py → _strip_html()` 的 `<a>` 转换正则 `r"[\2](\1)"` **无条件**把所有锚点降级为链接。Reddit 把评论里粘贴的裸图片 URL 渲染成 `<a href="...">...</a>` 放进 `body_html`，于是内联图片一律被拍平成普通链接。

**佐证**：`grep '!\[' reddit.py` 无任何匹配，而 github/youdao/weibo/kdocs/twitter/flowus/feishu 等 8 个 fetcher 均生成图片语法——Reddit 是唯一未做图片内嵌的平台，属功能缺口而非回归。

**附带缺口**：`_render_media_lines()` 的预览图/图集/原始媒体均为纯文本 URL；`_extract_media_fields()` 只收集主帖 preview+gallery，**不含评论图片**（本案 18 张图全在评论里，原 `images` 为空）。

### 实测前提

`preview.redd.it` 无防盗链：无 Referer、非浏览器 UA、Obsidian 风格 Electron UA 三种请求均返回 `200 image/jpeg`。故默认内嵌在线地址即可预览。`utils/media.py` 对 Reddit 开箱即用无需修改：generic fallback 提取文件名正确（`urlparse` 已剥离 query），未知 platform 不改写签名 URL、不加 Referer——恰好符合 Reddit 需求。

### 实施

- **`_is_image_url()`**：去 query 后按扩展名判定（jpg/jpeg/png/gif/webp/bmp/svg/avif）。Reddit 把 gif 转码为 mp4 却保留 `.gif` 后缀，故 `format=mp4|webm` 显式排除，避免内嵌破图。
- **`_anchor_to_markdown()`** 替换原模板替换：图片 → `![alt](url)`（裸链接 alt 留空，有描述文字则保留），非图片仍为 `[text](url)`。
- **转义层数归一**：实测 `old.reddit.com` 的 `body_html` 单层转义、`www.reddit.com` 双层（`&amp;amp;`），文档级只 unescape 一次会让 www 来源的 URL 残留字面量 `&amp;`。故对 href 单独再 unescape 一次——对已干净的 old 形态幂等，URL 本就不该含 HTML 实体。
- **`_collect_markdown_image_urls()`**：从**已渲染正文**回收 `![](url)` 合并进 `images`。关键设计：`utils.media._replace_urls_in_md()` 靠字符串精确匹配改写，只有"正文内嵌什么就下载什么"才能保证下载与改写不脱节，单一数据源无需维护两条链路一致性。
- **`_render_media_lines()`**：经 `_media_value()` 判定后图片输出 `- 预览图：![](url)`，保留元信息标签同时可预览；视频等非图片保持纯 URL。
- **`config.reddit_download_media()`** + **`reader.py` REDDIT 分支** + `.env.example`：默认 false 在线内嵌，`REDDIT_DOWNLOAD_MEDIA=true` 下载到 `attachments/{item_id}/`，与 X/XHS/WeChat/Weibo 既定模式一致。

### 验证结果

- `pytest tests/` **390 passed**（新增 9 个用例：图片 URL 判定含 mp4 转码排除、裸/描述链接分流、非图片链接不误转、两种转义层数归一、评论图片回收、媒体行内嵌、开关默认值、Reddit 文件名提取）。
- **真实数据端到端**：Reddit 已收紧游客访问（`old.reddit.com` 的 `.json` 302 到 `/login/?reason=lor2`，`www` 侧 403），本地 `sessions/reddit.json` 仅 6 个 cookie 且缺 `reddit_session`，故 CLI 直抓全 Tier 失败。改用浏览器同源 `fetch` 取到真实 payload（200 / 259KB），喂给**真实渲染链路** `_extract_post_data → _extract_comments → _render_post → from_reddit → save_to_markdown`：产出 md 中 18 张图全部 `![](...)`、`&amp;` 计数为 0、非图片链接 `[Gemini poster](...)` 未被误转。
- **下载开关**：`download_media(..., platform="reddit")` 实测 18/18 全部落 `attachments/`，md 中 18 处改写为相对路径，文件名 `y9fx2ebsj2mh1.jpeg` 干净。
- **视觉**：渲染在线内嵌版本，6/6 图片 `naturalWidth > 0` 无破图，截图确认正常显示。

### 已知遗留

- 抓取网络层当前受 Reddit 游客限制阻断，与本次改动无关；用户需 `feedgrab login reddit` 重建含 `reddit_session` 的登录态后才能重抓。
- 存量 md 不回改（用户明确选择）：已存文档中的图片仍是链接语法，需重抓才会变为内嵌。

### 状态：已完成 ✅（渲染与下载链路已验证；真实网络抓取待登录态恢复后复测）

### 桌面端 0.1.21 发布

本次修复属 CLI 核心层，`main` 与 `feedgrab-desktop` 同受影响，经用户确认后双分支同步：

- `main`：cherry-pick `4c25f8c3` → `7ae2a0f3`。DEVLOG 因两分支顶部条目不同产生冲突，改用 `cherry-pick -n` + `git checkout HEAD -- DEVLOG.md` 复位后手工插入本条目，代码文件全部干净应用；`pytest tests/` 264 passed（main 测试集不含桌面端专有用例）后推送。
- `feedgrab-desktop`：`desktop/package.json` 递增到 `0.1.21`，`npm run pack:user` 完整三步打包，把修复后的 `reddit.py` 冻结进内置 Python worker。
- 安装包大小：`381038607` bytes；SHA256：`9808237802EFB3668F7E3A1D42826CA0F7782A977B72BC68C672AD3D095CC9CE`；未签名；打包时间 2026-08-31 02:01 +08:00。
- GitHub Release：`desktop-v0.1.21-20260831`（target `feedgrab-desktop`，`draft=false`），asset 已上传，`browser_download_url` 经 `curl.exe -I -L` 核验最终 `200 OK`、`Content-Length: 381038607` 与本地逐字节一致。
- 同步更新 `desktop/README.md`（含新增 `REDDIT_DOWNLOAD_MEDIA` 说明）→ 覆盖根 `README.md`（Compare-Object 无输出）、`README_en.md` 下载入口、`docs/feedgrab-desktop-packaging.md` 发布信息表。
- **清掉 v0.1.20 的遗留**：v0.1.20 条目中记录的「侧边栏作者/X 关注链接改动只进源码、未进安装包」，本次打包已一并带入，0.1.21 安装包首次包含该改动。

---

## 2026-08-30 · 桌面端 v0.1.20 · 侧边栏作者信息文案与主页链接调整

### 背景

用户反馈客户端左下角作者信息两行文案需要对调并改指向：第一行应展示 X 账号 `@iBigQiang`，第二行「主页」应展示中文品牌名「强子手记」并链接到个人主页 <https://huangqiang.me>，而不是继续指向 X 个人页（下方「推特：X」行已经承担了 X 入口，主页行重复指向 X 属于信息冗余）。

### 实施

- `desktop/renderer/src/App.tsx`（`author-panel`）：作者行 `author-value` 由 `强子手记` 改为 `@iBigQiang`（保持纯文本，无链接）；主页行 `author-text-link` 的 `href` 由 `https://x.com/iBigQiang` 改为 `https://huangqiang.me`，链接文字由 `@iBigQiang` 改为 `强子手记`。
- 「仓库：GitHub」「推特：X」两行不动，仍分别指向 `feedgrab-desktop` 分支与 X 主页。
- `desktop/tests/App.test.tsx`：同步两处断言——`作者：@iBigQiang` 行文本 + `强子手记` 链接 href 指向 `https://huangqiang.me`；`author-row` 全量文本快照更新为 `["作者：@iBigQiang", "主页：强子手记", "仓库：GitHub", "推特：X"]`。
- 外链无需改主进程：`electron/main.ts` 的 `setWindowOpenHandler` 对任意 `https://` 统一放行到系统浏览器，`huangqiang.me` 自动生效。
- `desktop/package.json` 版本递增到 `0.1.20`；本次 `npm install` 顺带把落后的 `package-lock.json`（仍停在 `0.1.14`）同步到 `0.1.20`。

### 验证结果

- `npm test` 83 passed（5 个测试文件）；`npm run lint` 通过（`--max-warnings=0`）；`npm run build` 通过（typecheck + vite build + electron tsc）。
- `python -m pytest tests/test_service_desktop.py tests/test_worker_protocol.py tests/test_service_layer.py -q` → 106 passed（本次未改 service/worker 协议，作为回归交叉验证）。
- 注：打包前发现本地 `desktop/node_modules` 残缺（`vitest` 缺失、`.bin` 为空），执行 `npm install` 补齐 376 个包后测试链路恢复。

### 桌面端 0.1.20 发布

- `npm run pack:user` 完整三步打包（build → runtime:build → package-windows.ps1）退出码 0，产物 `feedgrab-desktop-setup-0.1.20.exe`。
- 安装包大小：`381036146` bytes；SHA256：`248B7847B60823CFDC62623E420D9A55603F868BFE186CEB7C4FD2267036CA30`；未签名；打包时间 2026-08-30 20:36 +08:00。
- GitHub Release：`desktop-v0.1.20-20260830`（target `feedgrab-desktop`，`draft=false`），asset 已上传，`browser_download_url` 经 `curl.exe -I -L` 核验最终 `200 OK`、`Content-Length: 381036146` 与本地文件逐字节一致。
- 同步更新 `desktop/README.md` → 覆盖根 `README.md`（Compare-Object 无输出）、`README_en.md` 下载入口、`docs/feedgrab-desktop-packaging.md` 发布信息表。
- v0.1.19 用户可直接在客户端左下角点击「更新」升级到本版本（自动更新自 0.1.19 起可用）。

### 追加（发布后源码级改动，未重新打包）

用户要求把两处 X 入口统一改为「关注意图链接」，方便访客一键跳到关注确认页：

- `App.tsx` 新增模块级常量 `xFollowUrl = "https://x.com/intent/follow?screen_name=iBigQiang"`，三处引用同一常量避免地址分散。
- 作者行 `@iBigQiang` 由纯文本 `author-value` 改为 `author-text-link` 链接，指向 `xFollowUrl`。
- 推特行**图标与「X」文字都成为链接**：图标 `author-icon-link` 的 href 由 `https://x.com/iBigQiang` 改为 `xFollowUrl`；原纯文本 `<span>X</span>` 改为 `author-text-link` 链接同址。
- 「仓库：GitHub」行保持原样（仅图标可点，文字不可点）。
- 视觉零变化的依据：`styles.css` 中 `.author-value` 与 `.author-text-link` 共用同一组 `color: #f6f0e4/ font-size: 13px / font-weight: 400 / text-decoration: none`，改成链接只额外获得与主页行一致的 hover 金色 `#f0cf80`。
- 测试同步 4 处断言（`App.test.tsx` 共 3 个用例受影响）：作者链接 href、推特图标 href、新增「X」文字链接 href、赞助页用例中的 `xLink` href；原「只有社交图标可点」的用例名与 `getByText("X").closest("a")).toBeNull()` 断言按新语义改写，GitHub 行的同类断言保留。

验证：`npm test` 83 passed、`npm run lint` 通过、`npm run build` 通过；另用 Vite dev server + Playwright 真实渲染核验，快照确认三个 `/url` 均为 `xFollowUrl`，截图与改动前视觉一致。

> ⚠️ **本节改动只推送源码，未重新打包**：已发布的 `desktop-v0.1.20-20260830` 安装包（SHA256 `248B7847...`）仍是不含本节改动的版本，用户下载安装后左下角作者行与「X」文字仍不可点击。这两处链接会随下一次递增版本号的打包（v0.1.21+）进入安装包。

### 状态：已完成 ✅（安装包待下次打包时携带追加改动）

---

## 2026-08-21 · v0.26.6-dev · 微信公众号 200013 限流不再谎报成功 + 占位页落盘修复

### 背景

用户跑 `feedgrab mpweixin-id "袋鼠帝AI客栈"` 报 `ret=200013`，但 CLI 紧接着打印"✅ 微信公众号账号批量抓取完成，总数 0"——失败被报告成成功。用户确认 MP 后台 Cookie 是当天更新且有效的。

### 实测诊断

微信官方原文：`{"ret": 200013, "err_msg": "freq control"}`（频率控制），现有代码只记录 `ret`、丢弃 `err_msg`。

限流边界矩阵（全部真实请求）：

| 探测项 | 结果 |
| --- | --- |
| searchbiz 搜号 / 查**自己**的号 / appmsg type=10 自己的素材 | `ret=0 ok` |
| appmsgpublish 查他人 count=5 / 20 / **1** | `200013` |
| appmsg type=9 老接口查他人 | `200013` |
| 换 4 个目标号（量子位 / 机器之心 / InfoQ / 袋鼠帝） | 全部 `200013` |
| 换第二个微信账号（不同 bizuin/token） | 全部 `200013` |
| profile_ext 历史消息页 | `ret=-3 no session`（需微信客户端） |
| 单篇正文抓取 / 搜狗 `mpweixin-so` | 正常 |

**结论**：限流精确作用于「用 MP 后台查询**他人**公众号文章列表」这一能力，与目标号、微信账号、接口版本、`count` 取值均无关；`count=1` 也被拒，说明按**请求次数**计量，改参数无法规避。登录态本身健康。**根因**：笔记库中已抓 782 篇（老码小张 336 + 新智元 330 + 饼干哥哥 116），而 `page_size=5`，等于打了 150+ 次列表请求，配额耗尽。

连带查出：笔记库 44 篇 md 标题为"微信公众平台"、正文 22–49 字，是被当作文章保存的占位页（26 已删除 / 11 违规 / 2 隐私 / **5 风控验证页**），全部计入了 `fetched` 成功数。产生于 `browser.py` 中 `evaluate_wechat_article` 的 fallback 分支——页面无 `#js_content` 时直接用 `page.title()` + `body.innerText` 构造"成功"结果。另有残留进度 `next_begin=15`，因清理条件 `if not date_cutoff_reached or fetched > 0` 在"全部 dedup 跳过 + 触发日期截止"时恒为假而永远清不掉，导致老码小张最新 15 篇每次都被跳过。

### 实施

- **限流不再谎报**（`mpweixin_account.py`）：新增 `MPWeixinFreqControlError` / `MPWeixinRiskControlError`；`_fetch_article_list` 日志补 `err_msg`，200013 抛异常，其余非 0 错误也改为抛出（原先静默当作"列表读完"）；`result` 新增 `interrupted` 字段；CLI 区分"完成 ✅"与"未完成 ⚠"，中断时提示进度已保存。
- **占位页识别**（`browser.py` 新增 `detect_wechat_unavailable`）：5 类文案归一为 `deleted` / `violation` / `privacy` / `captcha`，仅在缺少 `#js_content` 时判定，且要求"标题退化为微信公众平台"+"正文命中已知文案"双条件（风控 URL 单条件即可），文案变更时退化为原行为不误杀正常文章。**四条链路全部接入**：单篇 `wechat.py`（明确终止不降级到 Jina，避免 Jina 抓同一占位页文案）、批量 `mpweixin_account.py`、专辑 `mpweixin_album.py`、搜狗 `wechat_search.py`。分流策略：删除/违规/隐私 → 计 skipped 且写 dedup（确实无需再抓）；**风控页 → 计 failed，不落盘不写 dedup**（保持可重试），连续 5 篇风控则中止本轮。
- **进度语义修复**：页内逐篇保存写当前页 `begin`，整页处理完才推进，消除"页内中断跳篇"；引入 `completed` 标志，仅列表读完或日期截止才清进度，限流/异常一律保留。
- **减压配置**（默认值已生效）：`MPWEIXIN_ID_PAGE_SIZE=20`（列表请求数降至 1/4）、`MPWEIXIN_ID_PAGE_DELAY=8`、`MPWEIXIN_ID_PAGE_JITTER=0.4`、`MPWEIXIN_ID_MAX_ARTICLES=0`、`MPWEIXIN_ID_FREQ_RETRY=0`（退避 60s/300s/900s，默认关闭）。
- **存量清理**：删除 44 篇占位页空壳（893 → 849 篇）+ 1 个残留进度文件，删除前整体备份。

### 已实测否决的方案

多微信账号轮换（换号同样 200013，配额不按账号计）、调整 count / 改用 appmsg 老接口 / 换目标号（全部 200013）、`profile_ext` 历史消息页（需微信客户端登录态）。

### 验证结果

- `pytest tests --basetemp=.tmp/pytest-tmp`：**380 passed**（361 → 380，新增 19 个用例覆盖限流抛出、进度保留、空页边界、占位页四态识别、dedup 分流、抖动区间、配置上限）。
- **限流呈现实测**（当时正处限流态）：日志输出 `ret=200013 err_msg=freq control`，CLI 打印中文限流说明与三条建议，退出码 1，不再出现 ✅；日志确认 `offset=0 count=20` 新页长已生效。
- **`count` 上限实测**：用「查自己的号」绕开 200013（同一接口、同一套 `count` 解析），`count=5/20/40` 均 `ret=0 ok`（`count=20` 返回该号全部 12 条），确认调大页长不会被服务端拒绝。
- **占位页识别实测**：`feedgrab https://mp.weixin.qq.com/s/H7YS5wlC4Rfv_lilxujC3A` → "微信文章不可抓取：该内容已被发布者删除"，未降级 Jina、未落盘、退出码 1。搜狗链路对已删除 / 违规 / 隐私三类 URL 分别返回 `deleted` / `violation` / `privacy`。
- **正常路径不回归**：抓取当日新文（歸藏的AI工具箱 2026-08-21）成功，10751 bytes、正文 2949 字、12 张图、front matter 完整，占位页检测对其返回空（未误伤）。
- **桌面端链路**：`FetchService.fetch_url()` 抛 `ServiceError`，`worker.py` 的 `_emit_exception` 提取 code/message 发给渲染进程并 `continue`，批量不中断。行为由"静默存空壳并计成功"变为"明确报错并计入 errors"。

### 代码审查发现并修复（收尾阶段）

`/code-review` 查出本次改动自身引入的一处边界缺陷：`if not articles: completed = True` 把"本页没解出图文"等同于"列表读完"，会清掉进度并漏抓后续文章——正是本次要消灭的那类静默失败。实测确认两者语义不同（`begin=0 size=20 → articles=12, is_complete=False`，分页要靠下一页返回空才终止），已收紧为仅 `is_complete` 时才算读完，空页继续翻页，并补测试 `test_empty_page_is_not_treated_as_end_of_listing`。

### 已知遗留（不阻塞本次交付）

- `art_page` 在 `evaluate_wechat_article` 抛异常时不会关闭（pre-existing，account/album 两处同形），长批量下可能累积页面句柄。
- `mpweixin_album.py` 跨模块导入私有函数 `_record_unavailable`，可接受但非最佳形态。
- 占位页文案匹配依赖微信页面文案，微信改版会漏判；漏判时退化为原行为（落盘），不会误杀正常文章。

### 状态：已完成 ✅（限流解除后需补测完整批量端到端 + 5 篇风控页补抓）

---

## 2026-07-10 · 桌面端自动更新修复：安装包 0 字节导致 spawn EFTYPE + 进度/报错改左下角气泡

### 背景

用户实测 v0.1.17 → v0.1.18 自动更新：下载完成后右上角 Toast 报"启动安装器失败：spawn EFTYPE"，安装器从未启动；下载进度显示在"更新"按钮上遮盖按钮，报错位置也与更新交互区（左下角版本号）割裂。

### 根因

`desktop/electron/updater.ts → downloadFile()` 的 `response.on("data")` 只累计进度**从未把 chunk 写入 fileStream**，下载产物是 0 字节空文件；rename 后 spawn 一个空 `.exe`，libuv 判定非有效可执行格式返回 `EFTYPE`。下载目录为 `%TEMP%\feedgrab-desktop-update\`。次要问题：Windows 下 spawn 失败经异步 `error` 事件抛出，原 try/catch 捕获不到（本次是 IPC 层兜底才显示出来）。

### 实施

- `updater.ts`：data 回调补 `fileStream.write(chunk)`；下载完成后校验 `downloadedBytes === totalBytes`（content-length 存在时），不完整则报错不安装；spawn 改为 `spawnInstaller()` Promise 化，监听 `spawn`/`error` 事件；失败信息附带安装包完整路径提示可手动安装；结果新增 `installerPath` 字段（`ipc-types.ts` 同步）。
- `App.tsx`：下载/安装进度与全部更新报错从右上角 Toast 改为版本号上方气泡（`update-bubble-progress` 持续显示进度百分比，报错气泡停留 8s）。
- `styles.css`：新增 progress 气泡样式；error 气泡允许换行 + max-width（报错含路径较长）。
- **追加（用户要求）下载目录改客户端安装目录**：新增 `getUpdateDownloadDir()`——打包运行时下载到 `<安装目录>\update\`（如 `D:\feedgrab Desktop\update\`），目录不可写时回退 `%TEMP%\feedgrab-desktop-update\`（开发模式同此）；新增 `cleanupUpdateDownloads()` 在 `app.whenReady` 时清理两处目录遗留的 `.exe`/`.part`（安装成功后新版本首次启动即自动删除安装包，失败残留下次启动清理）。

### 验证结果

- `npm test` 83 passed；`npm run lint`、`npm run build` 通过。
- 真实 URL 实测（electron 环境直调 `downloadFile`）：GitHub 2375312 bytes 归档完整落盘 `%TEMP%\feedgrab-desktop-update\`，zipfile 结构校验通过（修复前同链路产物为 0 字节）。
- **端到端实测（0.1.17→0.1.18 全链路）**：临时降 `package.json` 版本至 0.1.17 → 开发版点击"更新"→"立即更新" → 气泡进度正常显示 → 安装包 376926034 bytes 完整落盘（与 release 逐字节一致）→ 安装器成功启动（EFTYPE 消失）→ 静默安装完成，注册表 DisplayVersion 0.1.17→0.1.18。
- **追加修复：静默安装后自动重启应用**——实测发现 `/S` 静默装完后应用不会自动启动（用户视角"应用关了没动静"）。spawn 参数补 `--force-run`（electron-builder assisted installer NSIS 模板原生支持：`${isForceRun} && ${Silent}` 时执行 StartApp）；实测直跑 `setup-0.1.18.exe /S --force-run`：安装完成后 `D:\feedgrab Desktop\feedgrab Desktop.exe` + `feedgrab-worker` 自动拉起。"安装中"气泡文案同步改为"下载完成，应用即将退出并后台静默安装，装好后自动启动新版"。

### 桌面端 0.1.19 发布

- `desktop/package.json` 版本递增到 `0.1.19`，`npm run pack:user` 完整三步打包生成 `feedgrab-desktop-setup-0.1.19.exe`。
- 安装包大小：`376927297` bytes；SHA256：`44086358BAB9D8D8A1063DCD2D9D3EC4125C094B52E15613846A8E4B90448D23`；未签名；打包时间 2026-07-10 20:24 +08:00。
- GitHub Release：`desktop-v0.1.19-20260710`（target `feedgrab-desktop`），asset 已上传，`browser_download_url` 经 `curl -I -L` 核验 `200 OK`。
- 同步更新 `desktop/README.md` → 覆盖根 `README.md`（Compare-Object 一致）、`README_en.md` 下载入口、`docs/feedgrab-desktop-packaging.md` 发布信息表。
- 注意：0.1.18 及更早版本内置旧更新逻辑，升级到 0.1.19 仍需手动安装；从 0.1.19 起客户端内自动更新完全可用。

### 状态：已完成 ✅

---

## 2026-07-10 · v0.26.5-dev · X 推文视频 Obsidian 内嵌播放 + 桌面端"推文媒体下载到本地"开关

### 背景

用户抓取推文后 md 中视频只是 `[▶ video](https://video.twimg.com/....mp4?tag=28)` 纯链接，Obsidian 无法内嵌预览播放，点击只能跳浏览器。同时后端已有的 `X_DOWNLOAD_MEDIA` 媒体本地化能力未暴露到桌面端设置界面。

### 实施

- `schema.py`：新增 `_video_embed()` helper，`_render_twitter_tweet_part()` 与 `_render_quoted_tweet()` 的视频渲染由 `[▶ video](url)` 改为 `<video controls src="{html.escape(url)}"></video>`（复用飞书已验证的 Obsidian 内嵌写法；URL 必须转义，Obsidian 会误解析含未转义 `&` 的 src）。
- `utils/media.py`：`_replace_urls_in_md()` 对每个远程 URL 同时匹配原始形式与 `html.escape` 转义形式，保证下载后 `<video src>` 内的转义 URL 也能替换为本地相对路径。
- `service/platform_settings.py`：X 平台新增 boolean 字段 `X_DOWNLOAD_MEDIA`（label"推文媒体下载到本地"，默认 false），自动落入桌面端"单篇/线程采集"分组；`desktop/renderer/src/App.tsx` 离线 fallback schema 同步。
- `.env.example` 注释补充桌面端可视化配置入口。
- **修复（用户实测反馈）**：勾选下载时引用推文（quoted tweet）媒体仍是在线链接。根因：7 个下载调用点统一读 `content.extra["images"/"videos"]`，而该清单聚合时只拍平线程推文自身媒体。修复于 `schema.from_twitter()` 单点：把 `thread_tweets[*].quoted_tweet` 的媒体并入 extra 清单（去重保序），单篇与全部批量模式一次覆盖。

### 验证结果

- `python -m pytest tests --basetemp=.tmp/pytest-tmp`：361 passed（新增 3 用例：video 标签渲染 + 转义 URL 替换 + 引用推文媒体下载清单）。
- 真实 URL 实测 `https://x.com/liyue_ai/status/2075136397326139804`：
  - 在线模式（默认）：md 中 `<video controls src="https://video.twimg.com/...mp4?tag=28"></video>`，主推文与引用推文均为内嵌播放器。
  - 本地模式（`X_DOWNLOAD_MEDIA=true`）：`attachments/10dc747be1dd/` 落地 5/5 文件（主推文 mp4 1633466 bytes + 引用推文 mp4 2249238 bytes，均 file 校验 ISO MP4），主推文与引用推文媒体全部引用本地相对路径，md 中无 twimg 残留。
- `desktop`: `npm test` 83 passed；`npm run lint`、`npm run build` 通过。

### 桌面端 0.1.18 发布

- `desktop/package.json` 版本递增到 `0.1.18`，`npm run pack:user` 生成 `feedgrab-desktop-setup-0.1.18.exe`。
- 安装包大小：`376926034` bytes；SHA256：`DF53CFBF360DF4F0E515EADE15F15C8B7A517D960692D6A0F727EB909EDF4A49`；未签名；打包时间 2026-07-10 02:53 +08:00。
- GitHub Release：`desktop-v0.1.18-20260710`（target `feedgrab-desktop`），asset 已上传，`browser_download_url` 经 `curl -I -L` 核验 `200 OK`。
- 同步更新 `desktop/README.md` → 覆盖根 `README.md`（Compare-Object 一致）、`README_en.md` 下载入口、`docs/feedgrab-desktop-packaging.md` 发布信息表。

### 状态：已完成 ✅

---

## 2026-07-07 · v0.26.5-dev · 桌面端 0.1.17 版本更新通知功能发布

### 背景

`feedgrab-desktop` 分支完成版本更新通知功能开发和诊断页误报修复后，按桌面端专用收尾流程发布新的 Windows 预览安装包。本次新增客户端内置版本检查和一键静默升级能力，并修复打包运行时下诊断页"安装/更新所有依赖"的误报问题。主分支 `main` 本轮不改动。

### 实施

- 新增 `desktop/electron/updater.ts`：通过 GitHub Releases API 检查最新版本，对比当前版本号，发现新版本后下载 NSIS 安装包并静默安装（`/S` 参数），安装期间保留 `sessions/` 和 `output/` 目录（依赖 `installer.nsh` 已有的静默模式数据保留逻辑）。
- 新增 IPC 通道 `feedgrab:checkForUpdates` / `feedgrab:downloadAndInstallUpdate` / `feedgrab:updateProgress`，preload 桥接到渲染进程。
- 侧边栏底部版本号旁添加"更新"按钮：每日首次打开自动检查（`localStorage` 记录当日已检查），发现新版本时按钮高亮，点击弹窗显示发布说明，一键下载安装。
- 检查结果提示从右上角 Toast 改为版本号上方气泡显示，3 秒后自动消失。
- 修复诊断页误报：`repairDoctor("all")` 在打包运行时下先调用 `this.doctor()` 预检查，全部正常时直接返回成功，不再无条件返回 `bundled_worker_runtime` 失败。
- 界面文案优化："版本号"改为"版本"，"检查更新"改为"更新"。
- `ship-desktop.md` 补充禁止手动分步打包或跳过 `runtime:build` 的规则。

### 验证结果

- `desktop`: `npm test`：83 passed。
- `desktop`: `npm run lint`：通过（0 warnings）。
- `desktop`: `npm run build`：typecheck + vite build + tsc electron 全通过。
- `desktop`: `npm run pack:user`：成功生成 `feedgrab-desktop-setup-0.1.17.exe`。
- 安装包大小：`376924974` bytes。
- 安装包 SHA256：`AFED1EB23CA73BE0049F5636608BDDEE4C30E79FB4E34C43B8E57004A698C897`。
- 安装包签名状态：未签名。
- GitHub Release：`desktop-v0.1.17-20260707`，asset `feedgrab-desktop-setup-0.1.17.exe` 已上传。
- 下载地址核验：GitHub API 返回的 `browser_download_url` 为 `https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.17-20260707/feedgrab-desktop-setup-0.1.17.exe`，`curl.exe -I -L` 最终返回 `200 OK`，`Content-Length: 376924974`。

### 状态：已完成 ✅

---

## 2026-07-06 · v0.26.5-dev · 桌面端 0.1.16 飞书媒体本地化修复发布

### 背景

`feedgrab-desktop` 分支完成飞书文档视频/媒体本地化两轮修复并实测通过后（详见下一条目），按桌面端专用收尾流程发布新的 Windows 预览安装包，让普通用户拿到修复。主分支 `main` 本轮不改动。

### 实施

- 桌面端安装包版本递增到 `0.1.16`，重新生成普通用户 NSIS 安装器。
- 随包带上飞书媒体修复：视频/音频/附件下载到 `attachments/`、原始文件端点优先、下载日志可见、响应有效性校验（细节见下一条目）。
- 同步 `desktop/tests/App.test.tsx` 侧边栏作者行顺序断言（仓库行在前、推特行在后，与工作区 App.tsx 实际顺序一致）。
- GitHub Release notes 使用中文完整说明，并回填真实 `browser_download_url`。
- 更新 `desktop/README.md` → 覆盖根 `README.md`、`README_en.md` 下载入口、`docs/feedgrab-desktop-packaging.md` 发布信息表。

### 验证结果

- `desktop`: `npm test`：83 passed（含更新后的作者行顺序断言）。
- `desktop`: `npm run lint`：通过。
- `desktop`: `npm run build`：通过。
- `python -m pytest tests/test_service_desktop.py tests/test_worker_protocol.py tests/test_service_layer.py tests/test_feishu_wiki.py tests/test_feishu_sheet_decode.py -q`：122 passed。
- `desktop`: `npm run pack:user`：成功生成 `D:\AiCode\feedgrab\desktop\release-packages\20260706-190917\feedgrab-desktop-setup-0.1.16.exe`。
- 安装包大小：`376921707` bytes。
- 安装包 SHA256：`26698FD25B641221FF18993EB8B90356384A0FA06CC3F06F74D850B162705AA0`。
- 安装包签名状态：未签名。
- GitHub Release：`desktop-v0.1.16-20260706`，asset `feedgrab-desktop-setup-0.1.16.exe` 已上传。
- 下载地址核验：GitHub API 返回的 `browser_download_url` 为 `https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.16-20260706/feedgrab-desktop-setup-0.1.16.exe`，`curl.exe -I -L` 最终返回 `200 OK`，`Content-Length: 376921707`。

### 状态：已完成 ✅

---

## 2026-07-06 · v0.26.5-dev · 飞书文档媒体本地化修复

### 背景

桌面端 v0.1.15 抓取飞书文档 `https://jcn11fio79id.feishu.cn/wiki/EuJowYkE6iM6fgk5Ykdc6boHnuh` 时，图片能保存到 `attachments/`，但源文档中的视频没有进入 Markdown，也没有生成本地附件。排查确认当前飞书链路只收集 `images_info` 并调用 `download_feishu_images()`，视频/文件类 block 未被渲染或下载。

### 实施（第一轮 · Codex）

- 扩展 `feedgrab/fetchers/feishu.py` 的 Block→Markdown 渲染：保留原 `images_info`，新增 `media_info`，支持 `file` / `video` / `audio` / `media` 以及 `fallback + snapshot.type=video` 等媒体 block。
- 真实页面诊断确认飞书视频字段为 `type=file` + `snapshot.file.{token,mimeType,name,size,width,height}`，且视频 file block 可能嵌套在 heading/text 子块中；因此补齐 `text` / `heading` 的 children 渲染，避免子级视频被父块吞掉。
- 修复表格单元格与 `synced_reference` / `fallback` 容器吞掉子级媒体 block 的渲染断链（表格渲染透传 `media/img_subdir/images`；容器保留嵌入块输出同时继续渲染 children）。
- 浏览器提取阶段从 DOM `<video src=".../space/api/box/stream/download/video/{token}/?...">` 收集真实播放 URL，写入 `media_info.url`。
- 视频输出为 Obsidian 可预览的 `<video controls src="attachments/{item_id}/xxx.mp4"></video>`；音频输出为 `<audio controls ...>`；其他文件输出为本地附件链接。
- 新增 `download_feishu_media()`，复用飞书 Open API / CDN 下载路径，统一保存图片、视频、音频和附件到 `attachments/{item_id}/`；旧的 `download_feishu_images()` 保留兼容。
- `FEISHU_DOWNLOAD_IMAGES` 继续作为旧配置键生效，新增 `feishu_download_media()` 兼容 `FEISHU_DOWNLOAD_MEDIA`，桌面设置文案改为"飞书媒体下载到本地"。
- 不修改飞书 Sheet / 表格解码逻辑。

### 实施（第二轮 · 实测修复）

第一轮修复只通过了单元测试（Codex 沙箱访问飞书被网络拦截，真实 URL 端到端从未跑通），用户实测视频仍未保存。本轮真实实测两个含视频文档定位并修复三个遗留问题：

- **下载 URL 优先级反了**：第一轮"优先 DOM 播放 URL"实测拿到的是飞书转码预览版（22.4MB 的 `.mov` 原件只下到 4MB 转码流）。改为原始文件端点 `/space/api/box/stream/download/all/{token}/` 优先（实测返回与原件逐字节一致），DOM 播放流 URL 仅作兜底（含相对路径补全）。
- **响应有效性校验**：下载只接受 `status=200` 且 content-type 非 `text/html` / `application/json`（防止登录页/错误 JSON 被写成 `.mp4`）；移除对 206 部分响应的接受（避免残片文件）。
- **下载日志不可见**：`feishu.py` 用 std `logging`（无 handler，INFO 被吞），CLI/桌面端看不到任何下载动静，用户无法确认是否成功。统一换成 loguru，`Downloaded media N: xxx (N bytes)` 全程可见；`_get_media_info` 补提取 `size`，下载大小与原件不一致时日志明确标注 transcoded variant。

### 验证结果

- `python -m pytest tests/test_feishu_wiki.py tests/test_feishu_sheet_decode.py -q`：17 passed（含新增"原始 URL 优先"与"回退 DOM 流 URL"两用例）。
- `python -m pytest tests -q`：358 passed。
- **实测 `EuJowYkE6iM6fgk5Ykdc6boHnuh`**：4 视频 + 3 图片全部落地，4 个 mp4 与飞书原件逐字节一致（4667460 / 4224672 / 5071178 / 6718760 bytes），ftyp/moov/mdat box 结构完整可播放，Markdown 内 4 个 `<video>` 引用与文件一一对应。
- **实测 `YTnpw38qhidAqAkYkL9cKPRpnKf`**：2 视频（含 `.mov`）+ 2 图片全部落地，`.mov` 由第一轮的 4MB 转码版修复为 22403461 bytes 原件。
- **桌面端同链路验证**：`FetchService.fetch_url()`（桌面端 worker 实际调用路径）实测 `ok=True`，视频原件落地一致。

### 状态：已完成 ✅（实测视频落地验证通过）

---

## 2026-07-05 · v0.26.5-dev · 桌面端 0.1.15 侧边栏版本号样式修复 + OBSIDIAN_VAULT 迁移保留发布

### 背景

`feedgrab-desktop` 分支针对桌面端做两处小修复：侧边栏底部版本号样式不对齐，以及设置迁移误清空用户已配置的 `OBSIDIAN_VAULT`。主分支 `main` 本轮不改动。

### 实施

- 桌面端安装包版本递增到 `0.1.15`，重新生成普通用户 NSIS 安装器。
- 侧边栏底部"版本号"去除加粗效果（`font-weight` 700 → 400），并与上方的分隔横线一起在 `.sidebar-footer` 内居中对齐；下方作者信息行（作者：强子手记 / 主页 / 推特 / 仓库）保持左对齐不变。
  - `.sidebar-footer` 新增 `width: 100%` + `justify-items: center`。
  - `.sidebar-version` 新增 `width: 100%` + `text-align: center`；`font-weight: 700 → 400`。
  - `.author-panel` 新增 `width: 100%`（保持横线 `border-top` 拉满 footer 宽度，**不**加 `justify-items: center`，避免作者行被居中）。
- 修复 `feedgrab/service/settings.py` 设置迁移逻辑：保留用户已配置的 `OBSIDIAN_VAULT` 路径，不再因检测到 legacy 默认路径而误清空用户已设置的值。
- 回归测试 `desktop/tests/App.test.tsx` 的 `keeps the sidebar version understated and centered with its divider` 断言版本号 normal weight + 居中、横线来自全宽 author-panel。
- GitHub Release notes 使用中文完整说明，并回填真实 `browser_download_url`。

### 验证结果

- `desktop`: `npm test`：83 passed。
- `desktop`: `npm run lint`：通过。
- `desktop`: `npm run build`：通过。
- `python -m pytest tests/test_service_desktop.py tests/test_worker_protocol.py tests/test_service_layer.py -q -p no:cacheprovider`：100 passed（含本次 `test_desktop_settings_migrates_legacy_default_output_but_preserves_vault`）。
- `desktop`: `npm run pack:user`：成功生成 `D:\AiCode\feedgrab\desktop\release-packages\20260705-175349\feedgrab Desktop Setup 0.1.15.exe`。
- 安装包大小：`376917514` bytes。
- 安装包 SHA256：`4C1694E4C418B6AD48FAA9CB9E9C94425AEB6DA69FDD0669982CA840B7DA742C`。
- 安装包签名状态：未签名。
- GitHub Release：`desktop-v0.1.15-20260705`，asset `feedgrab-desktop-setup-0.1.15.exe` 已上传。
- 下载地址核验：GitHub API 返回的 `browser_download_url` 为 `https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.15-20260705/feedgrab-desktop-setup-0.1.15.exe`，`curl.exe -I -L` 最终返回 `200 OK`，`Content-Length: 376917514`。

### 状态：已完成 ✅

---

## 2026-07-03 · v0.26.5-dev · 桌面端 0.1.14 X/Twitter Article 搜索覆盖优化发布

### 背景

`feedgrab-desktop` 分支针对 X/Twitter 关键词搜索做小迭代，目标是在搜索中文关键词时尽可能覆盖更多 Article 长文结果，并改善搜索汇总表中 Article 行的摘要显示质量。主分支 `main` 本轮不改动。

### 实施

- 桌面端安装包版本递增到 `0.1.14`，重新生成普通用户 NSIS 安装器。
- X/Twitter 关键词搜索新增 `全部 zh+zxx` 语言选项，覆盖中文普通推文和 `lang:zxx` 的 Article 长文卡片。
- X/Twitter 关键词搜索新增 `全量 Live+Top` 排序选项，同时搜索 Latest 和 Top 后合并去重。
- 英文关键词默认扩展大小写组合，例如 `WorkBuddy` 会覆盖 `WorkBuddy`、`Workbuddy`、`workbuddy`。
- 搜索结果合并后按查看数重新排序，再应用最大结果数，减少高浏览 Article 被截断的概率。
- 搜索汇总表的 Article 摘要优先显示长文标题，其次显示长文正文开头，最后才回退到外层推文文本。
- 桌面端 X/Twitter 设置页同步新增以上搜索语言和排序选项。
- GitHub Release notes 使用中文完整说明，并回填真实 `browser_download_url`。

### 验证结果

- `desktop`: `npm test`：82 passed。
- `desktop`: `npm run lint`：通过。
- `desktop`: `npm run build`：通过。
- `python -m pytest tests/test_service_desktop.py tests/test_worker_protocol.py tests/test_service_layer.py tests/test_twitter_keyword_search.py -q -p no:cacheprovider`：110 passed。
- `desktop`: `npm run pack:user`：成功生成 `D:\AiCode\feedgrab\desktop\release-packages\20260703-123302\feedgrab Desktop Setup 0.1.14.exe`。
- 安装包大小：`376915729` bytes。
- 安装包 SHA256：`63A67DD51F09FB27A64F76D1005D022CFBD3B9E86C6EE5C0290B2351D65557B8`。
- 安装包签名状态：未签名。
- GitHub Release：`desktop-v0.1.14-20260703`，asset `feedgrab-desktop-setup-0.1.14.exe` 已上传。
- 下载地址核验：GitHub API 返回的 `browser_download_url` 为 `https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.14-20260703/feedgrab-desktop-setup-0.1.14.exe`，`curl.exe -I -L` 最终返回 `200 OK`，`Content-Length: 376915729`。

### 状态：已完成 ✅

---

## 2026-07-01 · v0.26.4-dev · 桌面端 0.1.13 阶段性收尾发布

### 背景

`feedgrab-desktop` 分支完成一轮客户端登录、多账号、卸载保留数据、Reddit 接入和社群页 Markdown 渲染修复后，按桌面端专用收尾流程发布新的 Windows 预览安装包。主分支 `main` 本轮不改动。

### 实施

- 桌面端安装包版本递增到 `0.1.13`，重新生成普通用户 NSIS 安装器。
- 修复卸载器保留 `output` / `sessions` 的跳转问题，并新增是否清理 `AppData\Roaming\feedgrab-desktop` 的独立提示。
- 登录中心改为全局 `CHROME_CDP_LOGIN` 规则：勾选时从当前 Chrome 抽取登录态，不勾选时走隔离登录窗口并在平台成功态后保存 session。
- 多账号手动登录使用隔离浏览器 profile，避免第二个账号复用第一个账号的窗口状态。
- 优化微信公众号、小红书、FlowUs、飞书、Reddit 等平台登录保存逻辑，降低半成品 cookie 被误保存的概率。
- 移除手动登录窗口里的 `--disable-blink-features=AutomationControlled` 启动参数，减少 Chrome 不受支持命令行标记提示。
- 新增 Reddit 桌面端阶段性支持：登录态检测、空白 session 模板、单帖抓取、关键词搜索、service/worker/GUI 链路和回归测试。
- 修复桌面端关闭按钮行为，支持托盘最小化/退出选择。
- 修复社群页 Markdown 表格渲染，使段落换行、加粗和表格单元格垂直对齐更接近 GitHub 预览。
- GitHub Release notes 改为中文完整说明，写入版本重点、下载信息、SHA256、签名状态和验证结果。

### 验证结果

- `desktop`: `npm test`：82 passed。
- `desktop`: `npm run lint`：通过。
- `desktop`: `npm run build`：通过。
- `python -m pytest tests/test_service_desktop.py tests/test_worker_protocol.py tests/test_service_layer.py -q -p no:cacheprovider`：104 passed。
- `desktop`: `npm run pack:user`：成功生成 `D:\AiCode\feedgrab\desktop\release-packages\20260701-164041\feedgrab Desktop Setup 0.1.13.exe`。
- 安装包大小：`376912943` bytes。
- 安装包 SHA256：`8E8B843B2F3D9820AA5F146CDF06CC3B8BDD040E33BDE68B1AB8A2A888E4747E`。
- 安装包签名状态：未签名。
- GitHub Release：`desktop-v0.1.13-20260701`，asset `feedgrab-desktop-setup-0.1.13.exe` 已上传。
- 下载地址核验：GitHub API 返回的 `browser_download_url` 为 `https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.13-20260701/feedgrab-desktop-setup-0.1.13.exe`，`curl.exe -I -L` 最终返回 `200 OK`，`Content-Length: 376912943`。

### 状态：已完成 ✅

---

## 2026-06-28 · v0.26.3-dev · 桌面端 0.1.2 安装包收尾发布

### 背景

`feedgrab-desktop` 分支完成安装目录数据保留、Obsidian Vault 输出优先级、X/Twitter GraphQL 优先调度和 Markdown 标题/front matter 回归修复后，按桌面端专用收尾流程重新发布安装包。主分支 `main` 本轮不改动。

### 实施

- 桌面端安装包版本从 `0.1.1` 递增到 `0.1.2`，避免多个安装包复用同一版本号。
- 重新执行 `desktop` 下的 `npm run pack:user`，生成普通用户 NSIS 安装器。
- GitHub Release tag 使用 `desktop-v0.1.2-20260628`，asset 使用无空格文件名 `feedgrab-desktop-setup-0.1.2.exe`。
- `desktop/README.md`、根目录 `README.md`、`README_en.md`、`docs/feedgrab-desktop-packaging.md` 和 `feedgrab-desktop-readme.md` 同步更新到 0.1.2 的真实下载地址、SHA256、文件大小和签名状态。
- 修正赞助页内置 Markdown 测试断言，使测试跟随当前 `docs/sponsor.md` 的实际标题和链接。

### 验证结果

- `desktop`: `npm test`：65 passed。
- `desktop`: `npm run lint`：通过。
- `desktop`: `npm run build`：通过。
- `python -m pytest tests/test_service_desktop.py tests/test_worker_protocol.py tests/test_service_layer.py -q -p no:cacheprovider`：60 passed。
- `desktop`: `npm run pack:user`：成功生成 `D:\AiCode\feedgrab\desktop\release-packages\20260628-132707\feedgrab Desktop Setup 0.1.2.exe`。
- 安装包大小：`376844911` bytes。
- 安装包 SHA256：`A2D02AFEF94801300832A310E28153ED2E9B40C92AB271B42C5528EA1DD1934E`。
- 安装包签名状态：未签名。
- GitHub Release：`desktop-v0.1.2-20260628`，asset `feedgrab-desktop-setup-0.1.2.exe` 已上传。
- 下载地址核验：GitHub API 返回的 `browser_download_url` 为 `https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.2-20260628/feedgrab-desktop-setup-0.1.2.exe`，`curl.exe -I -L` 最终返回 `200 OK`，`Content-Length: 376844911`。

### 状态：已完成 ✅

---

## 2026-06-27 · v0.26.2-dev · 桌面端安装包阶段性发布

### 背景

`feedgrab-desktop` 分支已完成基础代理设置、关键词/账号抓取入口、登录/设置/诊断页优化和赞助/社群在线文档渲染修复，需要生成普通用户可安装的 Windows 预览包，并把真实下载入口同步到当前分支文档。主分支 `main` 本轮不改动。

### 方案决策

- 只打包普通用户 NSIS 安装器，命令为 `desktop` 目录下的 `npm run pack:user`。
- 安装包体积约 384MB，不提交到 Git 分支，继续遵守 `.gitignore` 中 `desktop/release-packages/` 的本地产物约定。
- 安装包作为 GitHub Release asset 发布，tag 固定为 `desktop-v0.1.0-20260627`，并显式指向 `feedgrab-desktop` 分支。
- Release asset 使用无空格文件名 `feedgrab-desktop-setup-0.1.0.exe`，避免下载链接转义歧义；本地 electron-builder 原始文件名仍为 `feedgrab Desktop Setup 0.1.0.exe`。

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `desktop/README.md` | 修改 | 增加真实安装包下载地址、发布页、SHA256 和安装说明 |
| `docs/feedgrab-desktop-packaging.md` | 修改 | 记录当前预览发布 tag、asset URL、本地产物路径、签名状态和发布约束 |
| `docs/sponsor.md` / `docs/group.md` | 新建/修改 | 提供赞助和社群页在线 Markdown 源文件，客户端失败时回退内置文档 |
| `feedgrab-desktop-readme.md` | 修改 | 将旧的“无安装器脚本”说明更新为当前打包脚本和预览安装包状态 |
| `README.md` / `README_en.md` | 修改 | 增加桌面客户端说明、打包说明和预览安装包入口 |
| `.claude/commands/ship-desktop.md` | 新建 | 增加桌面端当前分支收尾命令，限定重打包 exe、发布 Release asset、核验真实下载地址并最终推送当前分支 |
| `desktop/renderer/src/App.tsx` / `styles.css` / `desktop/tests/App.test.tsx` | 修改 | 修复赞助页远程 Markdown 表格顺序和 HTML 打赏图渲染，补充回归测试 |

### 验证结果

- `desktop`: `npm test -- App.test.tsx`：29 passed。
- `desktop`: `npm test`：48 passed。
- `python -m pytest tests/test_service_desktop.py tests/test_worker_protocol.py tests/test_service_layer.py tests/test_mpweixin_account.py -q -p no:cacheprovider`：51 passed。
- `desktop`: `npm run lint`：通过。
- `desktop`: `npm run build`：通过。
- `git diff --check` / `git diff --cached --check`：通过。
- `desktop`: `npm run pack:user`：成功生成 `D:\AiCode\feedgrab\desktop\release-packages\20260627-215707\feedgrab Desktop Setup 0.1.0.exe`。
- 安装包大小：`384214024` bytes。
- 安装包 SHA256：`B66642BE164F94C9E6959082467AD4158327ADA9E836617B7BA729F9629E72B2`。
- 安装包签名状态：未签名。
- GitHub Release：`desktop-v0.1.0-20260627`，asset `feedgrab-desktop-setup-0.1.0.exe` 已上传，GitHub API digest 为 `sha256:b66642be164f94c9e6959082467ad4158327ada9e836617b7ba729f9629e72b2`。
- 下载地址核验：GitHub API 返回的 `browser_download_url` 为 `https://github.com/iBigQiang/feedgrab/releases/download/desktop-v0.1.0-20260627/feedgrab-desktop-setup-0.1.0.exe`，`curl.exe -I -L` 最终返回 `200 OK`，`Content-Length: 384214024`。

### 状态：已完成 ✅

---

## 2026-06-27 · v0.26.1-dev · 桌面端代理与关键词抓取入口

### 背景

桌面客户端基础设置需要提供真正生效的网络代理能力，解决普通用户只配置本机 HTTP/SOCKS 代理而非全局 TUN/VPN 时，客户端能打开但国际站抓取失败的问题。同时，抓取页原先只接受 URL，无法承接 `x-so`、`xhs-so`、`ytb-so`、`zhihu-so`、`mpweixin-id` 等搜索/账号类任务。

### 实施

- 基础设置新增 `FEEDGRAB_PROXY_ENABLED`、`FEEDGRAB_PROXY_URL`、`FEEDGRAB_NO_PROXY`，支持 `http://127.0.0.1:7890`、`socks5://127.0.0.1:7890`、`http://用户名:密码@IP:端口`，UI/日志/JSON 输出会隐藏密码。
- 新增 `feedgrab/service/proxy.py`，集中处理代理启用状态、密码脱敏、标准环境变量投射、requests/curl_cffi 代理配置和 Playwright 代理配置。
- `SettingsService` 在读取/保存桌面设置后投射 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 及小写变量；`http_client` 自动使用代理；Playwright 新建浏览器/context 读取代理配置。复用 Chrome CDP 时仍继承用户已打开 Chrome 的网络状态，不强改 Chrome 代理。
- Electron runtime 启动 Python sidecar worker 时注入保存的代理环境；Electron main 通过代理感知 IPC 拉取在线 `sponsor.md` / `group.md`，并在设置保存后刷新 Electron session 代理。
- 诊断页新增 `proxy_connectivity` 检查，区分代理未启用、代理未配置、代理不可达和网络超时等状态。
- 抓取页平台按钮改为可选，输入框改为“抓取目标（URL / 关键词 / 关键词组 / 账号）”。URL 输入保持原自动识别；非 URL 输入按选中平台生成结构化任务和命令预览：X/Twitter → `x-so`，小红书 → `xhs-so`，YouTube → `ytb-so`，知乎 → `zhihu-so`，微信公众号第一版默认账号批量 → `mpweixin-id`。
- worker `fetch` 方法兼容原 URL 列表，同时新增 `platform/mode/targets` 结构化任务分支，通过白名单映射调用已有 CLI 函数，不拼 shell 命令。

### 测试

- `python -m pytest tests -q`：245 passed。
- `desktop`: `npm test`：38 passed。
- `desktop`: `npm run typecheck`、`npm run lint`、`npm run build` 通过。
- 直接运行根目录 `python -m pytest -q` 会收集旧桌面打包产物中的第三方 `bs4/tests`，因多个 release/runtime 目录存在同名模块在收集阶段失败；本次验证改用仓库自身 `tests/` 目录。

### 兼容策略

- URL 抓取 IPC、CLI URL 抓取、Markdown/front matter、去重索引和 session 文件语义保持不变。
- 代理关闭时不主动改写未配置 feedgrab 代理的系统代理环境；只有保存了 feedgrab 代理字段才投射到 worker 进程。
- Electron 远程 Markdown IPC 只允许预置 GitHub raw/proxy 文档 URL，不开放任意网络请求。

---

## 2026-06-25 · v0.26.0-dev · feedgrab-desktop GUI 客户端分支

### 背景

基于 `开发及迭代方案调研报告/feedgrab-GUI客户端长期技术选型综合调研报告.md`，在独立 `feedgrab-desktop` 分支启动可商业化桌面客户端开发。主路线采用 `Electron + Vite + React + TypeScript + Python Sidecar Worker`，不重写 Python 抓取核心，不把 GUI 做成 CLI 文本输出解析器。

### 实施

- 加固 `feedgrab/service/`：批量抓取结果保留每个 URL 的成功/失败结构，新增递归脱敏、Job 队列/取消/重试/并发限制配置、typed settings snapshot、结构化 doctor、login session 状态、output artifact 元数据。
- 新增 `feedgrab/worker.py`：stdio JSON Lines sidecar worker，支持 `ping`、`detect_platform`、`fetch`、`cancel`、`doctor`、`settings_snapshot`、`login_status`、`output_list`，事件覆盖 `ready`、`job_started`、`progress`、`log`、`artifact`、`error`、`done`、`cancelled`、`diagnostic`；`fetch` 支持 GUI 传入 `output_dir`，并串行化实际抓取以避免 `OUTPUT_DIR` 环境竞争。
- 新增 `desktop/`：Electron main/preload、typed IPC、Python worker client、Vite/React/TypeScript renderer、抓取/任务/输出/登录/设置/诊断/授权占位 UI，以及 Vitest 测试。
- Electron renderer 运行在 `sandbox: true` + `contextIsolation: true` 下，preload 以 `.cts` 源文件输出为 `preload.cjs`，只暴露白名单 IPC；`index.html` 增加 CSP，`ELECTRON_RENDERER_URL` 限定 localhost，`openPath` 限定已授权输出目录或 worker 产物。
- Renderer 订阅真实 worker 事件，使用 `job_started/progress/log/artifact/error/done/cancelled` 更新任务、日志、输出库；输出目录选择会本地持久化，并随抓取请求传给 worker。
- `mcp_server.py` 与 CLI 批量路径兼容新的失败结果结构：成功项保持原内容输出，失败项返回或打印结构化错误，避免 `None` content 崩溃。
- 新增 `docs/feedgrab-desktop-implementation-plan.md` 记录本分支边界、文件范围和验证命令。

### 测试

- `python -m pytest -q -p no:cacheprovider`：233 passed。
- `python -m feedgrab.cli`：正常打印帮助页。
- `python -m feedgrab.worker` JSONL smoke：输出 `ready` 与 `done/ping`。
- `desktop`: `npm run typecheck`、`npm run lint`、`npm run test`、`npm run build` 通过。
- Electron worker client smoke：构建后的 `dist-electron/python-worker.js` 启动 Python sidecar，真实抓取 `https://github.com/iBigQiang/feedgrab`，收到 `job_started`、`progress`、`artifact`、`done`，产物落到 `output_smoke/electron-client/`。
- Electron built app smoke：默认真实 Python sidecar worker 路径成功渲染 7 个页面截图：`output_smoke/desktop-views/{fetch,jobs,output,login,settings,doctor,auth}.png`。

### 兼容策略

- 保持 CLI 命令、Markdown/front matter、去重索引、session 文件和 fetcher 行为不变。
- GUI renderer 不直接访问 Node、文件系统、Cookie、API Key、session 原文或 Python 进程；所有能力经 preload 白名单 IPC。
- 商业授权仅保留 scaffold/FeatureGate 占位，不接入真实支付或远程授权服务。

---

## 2026-06-25 · v0.25.0 · 第一阶段 service layer 架构升级

### 背景

为后续 GUI 客户端和 MCP 共用同一后端能力，新增 `feedgrab/service/` 服务层。第一阶段目标是建立稳定结构化 API，不改现有 CLI 命令、输出目录、Markdown/front matter、去重索引、session 文件格式或平台抓取策略。

### 实施

- 新增 `feedgrab/service/models.py`：`FetchRequest`、`FetchResult`、`Artifact`、`ProgressEvent`、`ServiceError`、`DiagnosticResult`，均提供 JSON-safe `to_dict()`。
- 新增 `FetchService.fetch_url()` / `fetch_urls()` / `detect_platform()` / `list_inbox()`，内部复用现有 `UniversalReader.read()`，保留原有落盘、媒体下载和去重副作用。
- `UniversalReader.read()` 在保存 Markdown 后通过临时属性 `_feedgrab_saved_path` 暴露 artifact 路径，不写入 `UnifiedContent.extra`，不影响 `to_dict()`、Markdown 或 front matter。
- `cmd_fetch()` 改为调用 `FetchService`，终端输出格式保持：普通单 URL 仍打印 `[source_type] title + url + content preview`，特殊批量 URL 仍打印 summary content，多 URL 仍打印 `Fetched N/M URLs`。
- `mcp_server.py` 改为通过 `FetchService` 调用 reader 能力，修复旧的 `UniversalReader(inbox=...)` 入口漂移；MCP 工具名和 pretty JSON 返回格式保持不变。
- 新增低风险服务骨架：`OutputService`、`LoginService`、`SettingsService`、`DoctorService`、`JobService`，均只包装现有实现，不引入 GUI/桌面/授权逻辑。
- FlowUs 验收修复：默认在线图片 URL 模式改为解析并写入 `cdn2.flowus.cn` 签名图片链接，避免保留易过期的飞书 `asynccode` 链接；`FLOWUS_DOWNLOAD_IMAGES=true` 时仍保持本地 `attachments/` 下载模式。

### 测试

- 新增 `tests/test_service_layer.py` 覆盖 service models、`FetchService`、CLI 兼容层、MCP service 调用。
- 红灯确认：新增测试最初因 `feedgrab.service` 缺失、CLI 无 `FetchService`、MCP 旧构造漂移失败。
- 绿灯结果：`python -m pytest` 通过 206 tests；本机 `.pytest_cache` 无写权限，仅产生 cache warning。
- CLI smoke：`python -m feedgrab.cli` 正常打印帮助页。
- MCP smoke：`python -c "import mcp_server; print('mcp import ok')"` 正常导入。
- FlowUs 实测：在线 URL 版生成 64 个 `cdn2.flowus.cn` 签名图片链接，抽样 HEAD 为 `200 image/png`；本地附件版生成 64 个 `attachments/5fc04c30c5dd/` 引用和 64 个图片文件。

### 兼容策略

- 不拆平台 fetcher，不改变 `UniversalReader.read()` 的公开返回类型。
- 不改变 `save_to_markdown()`、`download_media()`、`dedup.py` 的路径和文件格式语义。
- MCP `read_url()` / `read_batch()` 仍返回 `UnifiedContent.to_dict()` 的 pretty JSON 字符串，artifact 路径仅作为 service 结果字段提供给直接调用 service 的客户端。

---

## 2026-05-21 · v0.24.1 · Twitter 多账号 429 轮换修复

### 背景

用户报告：用 `feedgrab https://x.com/AdrianPunk115` 批量抓取，明明 `sessions/` 里有 6 个账号 cookie，但第一个账号 `3a43aed6...` 触发 429 后，重试 3 次都用同一个被限流的账号失败 → 整体停止在 557 条，远低于该账号实际推文量。日志：

```
[CookieRotation] 账号 3a43aed6... 被标记限流，15 分钟后自动恢复
[UserTweets] API 返回空响应，5秒后第 1/3 次重试...
GraphQL 429 Rate Limited — too many requests
[CookieRotation] 账号 3a43aed6... 被标记限流，15 分钟后自动恢复
[UserTweets] API 返回空响应，5秒后第 2/3 次重试...
...
[UserTweets] 3次重试后仍无响应，停止分页
```

### 根因

入口处 `twitter.py` 一次性 `cookies = load_twitter_cookies()` 加载固定 cookie 字典，调用 `fetch_user_tweets(profile_url, cookies, mode)` 传入。`fetch_user_tweets` 内的分页循环在 `if not response` 时仅 `time.sleep(5)` 后**用同一个 cookies 字典**重试 3 次。虽然 `_execute_graphql` 在 429 时调用 `mark_cookie_rate_limited()` 把当前账号加入限流字典，但**上游重试时并没有重新调用 `load_twitter_cookies()` 拿到一个未限流账号**，所以 3 次都用同一个失败账号。

排查发现**所有 7 个 Twitter 批量 fetcher 都有同样问题**（程度不一）：

| 文件 | 旧行为 |
|------|--------|
| `twitter_user_tweets.py` | 重试 3 次，但不刷 cookies（本次 bug 现场） |
| `twitter_bookmarks.py` | 直接 break，无重试 |
| `twitter_list_tweets.py` | 直接 break，无重试 |
| `twitter_user_lists.py` | 直接 break，无重试 |
| `twitter_retweeters.py` | 直接 break，无重试 |
| `twitter_search_people.py` | 直接 break，无重试 |
| `twitter_keyword_search.py` | 半成品：仅重试 1 次拿一个新 cookie |

### 实施

#### 抽出统一 helper：`fetch_with_cookie_rotation()`

放在 `twitter_cookies.py`，3 个新 public 函数：

```python
def count_total_accounts() -> int: ...           # 总账号数
def count_available_accounts() -> int: ...       # 当前未限流账号数
def earliest_rate_limit_recovery_seconds() -> int: ...  # 最早解封倒计时

def fetch_with_cookie_rotation(
    fetcher_callable, *args,
    label: str = "GraphQL",
    network_retry_delay: float = 5.0,
    **kwargs,
) -> tuple[Optional[Any], dict]:
    """每次失败重新调用 load_twitter_cookies() 拿可用账号；
    所有账号都限流才返回 None。"""
```

核心循环逻辑：

1. 计算 `total = count_total_accounts()`
2. 最多循环 `total` 次（保证每个账号都试一遍）
3. 每次循环开始重新 `cookies = load_twitter_cookies()` — 利用 `load_twitter_cookies` 内已有的"跳过限流账号"逻辑天然轮换到下一个可用账号
4. 调用 `fetcher_callable(*args, cookies=cookies, **kwargs)`
5. 切换账号时打印 `[label] 切换账号重试 (N/total) — 新账号: xxx... 剩余可用 M/total`
6. 单账号场景防死循环：如果连续两次拿到同一 `auth_token` 前 8 字符且仍失败，立即终止
7. 全部账号失败：明确打印 `[label] >>> 所有 N 个 Twitter 账号均已被限流 <<< 最早 Xs 后自动恢复`

#### 调用方迁移

7 个 fetcher 的 `if not response` 分支统一改为：

```python
response, rotated_cookies = fetch_with_cookie_rotation(
    page_fetcher, user_id,
    label="UserTweets", cursor=cursor,
)
if rotated_cookies:
    cookies = rotated_cookies   # 让后续页继承轮换后的账号
if not response:
    logger.error(f"[UserTweets] >>> 第 N 页所有 6 个账号均失败 <<< 已抓取 K 条，停止分页")
    break
```

注意 `cookies` 透传更新——这样下一页直接用新账号开始，避免每次都从第 1 个账号重新加载。

#### 文件清单

- `feedgrab/fetchers/twitter_cookies.py` — 新增 `fetch_with_cookie_rotation()` + 3 个统计函数（~100 行）
- `feedgrab/fetchers/twitter_user_tweets.py` — 重试逻辑改 helper
- `feedgrab/fetchers/twitter_bookmarks.py` — 单点失败改 helper
- `feedgrab/fetchers/twitter_list_tweets.py` — 同上
- `feedgrab/fetchers/twitter_user_lists.py` — 同上
- `feedgrab/fetchers/twitter_retweeters.py` — 同上
- `feedgrab/fetchers/twitter_search_people.py` — 同上
- `feedgrab/fetchers/twitter_keyword_search.py` — 替换半成品方案
- `tests/test_twitter_cookie_rotation.py` — 新增 8 case（首次成功 / 第二账号轮换 / 全限流 / 单账号防死循环 / 无 cookie / 异常吞噬 / kwargs 透传 / 实时统计）

### 实测结果（`feedgrab https://x.com/AdrianPunk115`）

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 抓取条数 | 557 条 | **632 条**（+75 / +13.4%） |
| 429 后行为 | 3 次同账号重试失败 → 停止 | 自动切换到下一个未限流账号继续 |
| 轮换次数 | 0 | 2 次（3a43aed6 → ae0669d0 → 467488bb） |
| 单元测试 | 193 | **201**（+8 新 case） |

观察到的关键日志：

```
22:58:31 GraphQL 429 Rate Limited — too many requests
22:58:31 [CookieRotation] 账号 3a43aed6... 被标记限流，15 分钟后自动恢复
22:58:31 Twitter cookies loaded from cookie_file(x_2.json) (auth_token=ae0669d0...) [5/6 可用]
22:58:36 [UserTweets] 切换账号重试 (2/6) — 新账号: ae0669d0... 剩余可用 5/6
22:58:37 [UserTweets] 切换到账号 ae0669d0... 第 2 次尝试成功
22:58:37 [UserTweets] 第 51 页获取 1 条，累计 569 条   # 突破之前的 557 停止线
...
23:01:06 [CookieRotation] 账号 ae0669d0... 被标记限流，15 分钟后自动恢复
23:01:11 [UserTweets] 切换账号重试 (2/6) — 新账号: 467488bb... 剩余可用 4/6
23:01:59 [UserTweets] 共获取 632 条推文条目  # 服务端 cursor 用尽自然结束
```

### 关键经验

- **`load_twitter_cookies()` 已经实现了"跳过限流账号、返回下一个可用"的逻辑**——但被旧重试代码绕过了。修复的本质是"在重试循环里**重新调用** `load_twitter_cookies()`"
- **多账号 cookie 轮换的最强语义**：重试次数 = 账号总数，每次重试天然切到下一个可用账号；只有所有账号都被限流才真正终止
- **关键日志的格式约定**：`>>> 关键事件 <<<` 三个尖括号包夹是项目里常用的高亮标记，方便用户在大量日志中一眼识别
- **`cookies` 透传**：helper 返回 `(response, last_cookies)`，调用方应该把 `cookies = rotated_cookies` 接住，让下一页直接用新账号开始（否则又会从第 1 个账号重新尝试）
- **单账号场景的死循环防御**：如果只有 1 个账号且失败，helper 必须立即终止；否则 `load_twitter_cookies` 在"所有都限流"时会返回最快解封的账号 → 同一个失败账号被反复尝试

### 测试覆盖

- 旧 193 + 新 8 = **201 个 unit test 全过**
- 实测真实账号 1 个 + 6 个 cookie 轮换链路验证
- 6 个 fetcher 的修改均通过 `python -c "from feedgrab.fetchers import ...; print('OK')"` 冒烟检查

---

## 2026-05-19 · v0.24.0 · FlowUs 息流文档抓取支持

### 背景

参考 KDocs / 飞书模式新增 FlowUs（flowus.cn）平台支持。FlowUs 是 Notion 风格的协作文档平台，采用 Vite SPA 架构，正文完全通过 `/api/docs/{uuid}` 加载（block-tree 扁平字典，按 `subNodes` 递归还原）。方案文档：`开发及迭代方案调研报告/20260519-v0.24.0-FlowUs息流文档抓取方案.md`。

### 关键决策

- **API 直连为主**：与 KDocs（ProseMirror DOM 抓取）、飞书（block snapshot 解析）不同，FlowUs 主路径是 `requests.get('/api/docs/{uuid}')`，无需浏览器解析 DOM。浏览器路径仅在公开 HTTP 被拒（付费/私有）时启用，复用本机 Chrome 的 `next_auth` JWT cookie。
- **公开 + 私有双兼容**：无 cookie 也先尝试一次 HTTP；只有 API 返回 1407/1401/401/403 这类鉴权码且本地无 cookie 时才下沉浏览器。公开链接零开销直出。
- **JWT cookie 持久化**：CDP 提取本机 Chrome 的 `next_auth` + `next_auth.sig` 双 cookie 后写入 `sessions/flowus.json`，后续重复抓取直接走 Tier 0 HTTP 复用。Launch fallback 路径强制要求**两个** cookie 同时存在才落盘，避免用残缺 cookie 污染未来 CDP 提取结果。
- **`next_auth.sig` 的关键性**：实测发现 FlowUs 鉴权用 `next_auth`（JWT body）+ `next_auth.sig`（HMAC 签名）**两条 cookie 联合验证**，缺任何一个都返回 1407。这与一般"单 JWT cookie"模式不同，且容易被忽略（DevTools 里两条 cookie 分两行展示，复制 next_auth 单条值时签名 cookie 不会被一起带上）。
- **媒体下载默认关**：FlowUs 默认输出在线 `cdn2.flowus.cn` 签名 CDN 图片 URL，便于 Obsidian 直接预览；开启 `FLOWUS_DOWNLOAD_IMAGES=true` 时下载为本地 `attachments/`。

### 实施清单

#### Block 类型映射（实测样本 418 block）

| type | 含义 | Markdown |
|------|------|----------|
| 0 | 页面/根 | `# title`（由 fetcher 单独输出） |
| 1 | paragraph | `text\n` |
| 4 | bullet | `- text` |
| 5 | ordered | `1. text`（按 depth 重置 counter） |
| 7 | heading（`data.level` 1-6） | `## text` / `### text` |
| 12 | quote | `> text` |
| 14 | media（`data.display`: image/audio/video/file） | `![alt](link)` / `[🎬 ...]` / `[📎 ...]` |
| 25 | code（`data.format.language`） | ` ````lang\ncode\n```` ` |

#### Segment enhancer

- `bold` → `**text**`
- `italic` → `*text*`
- `code` → `` `text` ``
- `backgroundColor` → `==text==`（Obsidian 高亮）
- `segment.type === 3` + `url` 字段 → `[text](url)`

#### 抓取链路（Tier 0/1/2/3）

1. **Tier 0 HTTP**：`utils/http_client.get('/api/docs/{uuid}', headers=..., cookies=load_flowus_cookies())` — 公开/已登录都走这条
2. **Tier 1 CDP**：`playwright.connect_over_cdp('ws://127.0.0.1:9222/devtools/browser')` → 找 `flowus.cn` cookie context → `new_page()` → 页面内 `fetch('/api/docs/{uuid}')` → 提取 cookie 保存
3. **Tier 2 Launch**：Playwright 启动新浏览器（带 saved `sessions/flowus.json`），页面内 `fetch()` 取 JSON；如果产出包含 `next_auth` 则落盘
4. **Tier 3 Jina**：仅当 API 不可达且非定义性错误码（1407/1404/401/403）时下沉

#### 文件清单

- `feedgrab/fetchers/flowus.py`（~700 行）— 全新 fetcher
- `feedgrab/config.py` — `flowus_cdp_enabled()` / `flowus_page_load_timeout()` / `flowus_download_images()`
- `feedgrab/schema.py` — `SourceType.FLOWUS` + `from_flowus()`
- `feedgrab/reader.py` — 平台路由（`is_flowus_url`）+ `_fetch` dispatch + 图片下载 hook + dedup 平台映射
- `feedgrab/login.py` — `PLATFORM_URLS["flowus"]` + `_CDP_COOKIE_DOMAINS["flowus"]` + `_CDP_COOKIE_URLS["flowus"]`
- `feedgrab/utils/storage.py` — `PLATFORM_FOLDER_MAP[FLOWUS] = "FlowUs"` + `published` 时间映射 + front matter (`doc_token` / `share_code` / `space_title` / `edit_time`)
- `.env.example` — 三个 FlowUs 环境变量样例
- `pyproject.toml` — v0.24.0

### URL 形态

- `https://flowus.cn/share/{uuid}?code={code}` — 公开分享链接（code 可有可无）
- `https://flowus.cn/{username}/{uuid}` — 个人空间链接（需登录）
- `https://flowus.cn/{username}/share/{uuid}` — 用户空间下的分享链接

### 实测结果

- ✅ 公开链接 `https://flowus.cn/baochang/share/1e8b026a-cb5a-41bb-8f2c-61fed1d3cc54`：零 cookie 直接 HTTP 200，输出 Markdown 1 篇（《OPPO母亲节文案翻车...》）front matter / 标题 / 正文 / 高亮 / 图片链接 全部正确
- ✅ 付费链接 `https://flowus.cn/share/08d68f8b-...?code=TU56HX`：注入 `next_auth` + `next_auth.sig` 双 cookie 后，Tier 0 HTTP 0.5 秒抓完完整付费文档（934 行 Markdown，59 张图片），抓取链路 `/api/docs/{uuid}` 一次到位
- ✅ 图片本地化（`FLOWUS_DOWNLOAD_IMAGES=true`）：DOM 滚动 + 双 pass 加载 → 提取 `cdn2.flowus.cn/oss/.../?time=&token=&role=sharePaid` 签名 URL → 直 HTTP 拉 → **59/59 全部成功，39 MB 完整本地化**

### 图片下载的关键设计

FlowUs 图片在 block JSON 里只有 `data.link`（作者引用的飞书 CDN URL，受 hotlink 保护，server-side 取 400）和 `data.ossName`（FlowUs 自家 OSS 路径，无 token 也取不到）。
渲染图片的真实 URL `cdn2.flowus.cn/<ossName>?time=&token=&role=sharePaid` **由前端客户端按某未公开算法签名生成**，未保存到任何 API response 中。

解决方案：当用户开启图片下载时，启动 headless Playwright 渲染同一篇 doc，触发前端把签名 URL 注入到 DOM `<img>` 元素的 `src` 上：
1. `viewport: 1920×3000` 一次性显示更多
2. 三 pass 滚动（第一 pass 步进 400px、慢节奏；后续 800px 快速过）+ `img[loading="lazy"] → "eager"` 强制加载
3. `networkidle` 等所有图片请求结束
4. `querySelectorAll('img')` 抓 `cdn2.flowus.cn/oss/{path}` URL，按 `ossName` 建立 `{path: signed_url}` 映射
5. Python 直拉签名 URL（**无需 cookie，token 自带签名**）

`asyncio.run()` 与 reader.py 的 async caller 会冲突，所以下载线程 `threading.Thread` 隔离。

### 风险与缓解

- **飞书图床链接时效性**：FlowUs 文档内的图片大量引用 `my.feishu.cn/space/api/box/stream/download/asynccode/?code=...`（1 小时过期）。默认 `FLOWUS_DOWNLOAD_IMAGES=false` 会解析为可预览的 FlowUs 签名 CDN URL；开启则本地化到 `attachments/`
- **付费/私有文档**：API 返回 `code=1407` 时本地无 cookie 才下沉浏览器，已登录场景直接 Tier 0 拿到；明确终止码（1407/1404/401/403）+ 已有 cookie 时不再下沉 Jina，避免 fallback 写出垃圾文件
- **JWT 过期**：`next_auth` 默认 30 天有效。失效后再次走 CDP 即可自动刷新 `sessions/flowus.json`

---

## 2026-05-18 · v0.23.0 · twitter-web-exporter 融合 Phase 2：P1-P2 五项功能落地

### 背景

v0.22.0 在 twe 对标方案中完成 P0 全部（5 个新 GraphQL operation + 3 项解析鲁棒性增强）+ P1-2 sortIndex 稳定排序 + P1-3 基础设施。本版本继续推进 P1-P2 剩余 5 个功能点（P1-3 thread 接入、P2-1 头像原图、P2-2 媒体文件名 pattern、P2-3 Retweeters/Favoriters、P2-4 People-tab 搜索）。完整方案：`开发及迭代方案调研报告/20260518-v0.23.0-P1-P2实施方案.md`。

P1-1（抽出 `_iter_timeline_instructions` 通用 helper，重构 7 处独立 parser）涉及 7 个 parser 的微妙差异（item filter / pin / addToModule / sortIndex / cursor 类型 / fallback path），回归风险较高，与用户确认**拆分到 v0.23.1 单独 ship**。helper 设计草案见 `tasks/twe_research/` 与方案文档。

### 实施清单

#### P2-1 — Twitter profile 头像原图替换

- `utils/media.py:_optimize_url` Twitter 分支：在 `pbs.twimg.com/profile_images/` URL 上去掉尺寸前缀（`_normal` / `_bigger` / `_mini` / `_400x400`），保留可选 query string
- 单元测试 `tests/test_media_url_optimize.py`（7 case 覆盖 4 种前缀 + query 保留 + 非头像 URL 不影响 + 媒体 URL 不被错位）

#### P2-3 — Retweeters / Favoriters operation

- 新增 `twitter_graphql.py` 常量与函数：
  - `FALLBACK_RETWEETERS_QUERY_ID = "Mbs-2NiTvy32oHDerWtVhg"` / `FALLBACK_FAVORITERS_QUERY_ID = "G27_CXbgIP3G9Fod_2RMUA"`（fa0311/twitter-openapi placeholder.json）
  - `fetch_retweeters_page` / `fetch_favoriters_page`（`tweetId` 变量，复用 `USER_LIST_FEATURES`）
  - `parse_retweeters_users` / `parse_favoriters_users`（路径 `data.retweeters_timeline.timeline.instructions` / `data.favoriters_timeline.timeline.instructions`，复用 `_parse_user_list_response`）
  - 加入 `main_ops_missing` 集合 + `_fallback_query_ids` 字典（4 级 fallback 全员复用）
- 新增 fetcher 文件 `fetchers/twitter_retweeters.py`：`fetch_tweet_user_list(url_or_id, cookies)`，输出 `X/users/{mode}/{tweet_id}_{date}.{md,csv}` 按 followers_count 倒序
- `reader.py` URL 路由：`^/<u>/status/<id>/retweets/?$` → retweeters；`^/<u>/status/<id>/likes/?$` → favoriters（**必须放在 `/status/` 单推检测之前**）
- CLI 命令：`feedgrab x-retweeters <tweet_url_or_id>` / `feedgrab x-favoriters <tweet_url_or_id>`
- 优雅降级：favoriters 返回 0 用户时打 WARNING 提示「作者可能隐藏点赞列表 — Twitter 默认行为」
- 新增 env vars：`X_TWEET_USER_LIST_ENABLED` / `_MAX_PAGES=5` / `_DELAY=2.0` / `_PER_PAGE=40`
- 单元测试 `tests/test_twitter_retweeters.py`（10 case）：URL 路由 + tweet_id 提取 + parser 输出 + cursor 解析 + 空响应

#### P2-4 — People-tab 搜索（`x-so --people`）

- `twitter_graphql.py` 新增 `parse_search_people_entries`（与 `parse_search_entries` 同路径但仅保留 `itemType=TimelineUser`，复用 sortIndex 排序）
- 新增 `fetchers/twitter_search_people.py`：`search_people(keyword, cookies)`，输出 `X/search-people/{keyword}_{date}.{md,csv}` 按 followers_count 倒序
- `cli.py:cmd_twitter_search` 入口检测 `--people` 选项 → 分支到 `_run_search_people`
- 新增 env vars：`X_SEARCH_PEOPLE_MAX_PAGES=3` / `_DELAY=2.0` / `_PER_PAGE=20`
- 单元测试 `tests/test_twitter_search_people.py`（4 case）：parser 保留 user / 过滤 tweet / cursor 提取 / 空响应

#### P1-3 — ModeratedTimeline 接入 thread 主路径

- `twitter_thread.py` 新增 Phase 8（opt-in via `X_FETCH_MODERATED_REPLIES=true`）：
  - 在 Phase 7 后调用 `_fetch_moderated_replies(root_id, cookies)`
  - 调用 `fetch_moderated_timeline_page` + `parse_moderated_timeline_entries`（v0.22.0 已建基础设施）分页直到 bottom cursor 为空
  - 标记 moderated tweet 加 `_is_moderated=True` 并合并到 `all_entries`
  - 单独导出 `result["moderated_replies"]` + `result["has_moderated_replies"]` 供下游消费
- `schema.py:from_twitter` 透传 `moderated_replies` / `has_moderated_replies` 到 extra
- `fetchers/twitter.py` thread 分支返回值带 `moderated_replies` / `has_moderated_replies`
- `utils/storage.py`：
  - Twitter front matter 加 `moderated_replies_count`
  - Twitter MD body 加「⚠️ 被作者隐藏的回复」区段（含 callout 说明仅作者本人 Cookie 可见）
- `config.py` 新增 `x_fetch_moderated_replies()` + `x_moderated_replies_max_pages()`（默认 false / 3 页）
- 优雅降级：API 对非作者 Cookie 返回 404 → 静默吞掉 + 单条 INFO 解释（"端点对当前 Cookie 不可见"），不阻塞主流程
- 单元测试 `tests/test_twitter_moderated.py`（8 case）：开关默认 / 空响应 / 单页 / 分页 / 异常吞噬

#### P2-2 — X 媒体文件名 pattern 系统（opt-in）

- `utils/media.py:download_media` 新增 `context: dict = None` 可选参数（默认 None = 沿用 CDN-stem 命名，向后兼容）
- 新增 `_apply_filename_pattern(pattern, fallback_name, ctx, num, media_type)` helper：
  - 支持 9 个 token：`{date}` / `{datetime}` / `{screen_name}` / `{user_id}` / `{tweet_id}` / `{num}` / `{type}` / `{ext}` / `{name}`
  - **`{tweet_id}` 优先从 `ctx["url"]` 提取真实 Snowflake**（避免 feedgrab 内部 12 字符 hash），fallback 到 `ctx["tweet_id"]`
  - 严格白名单 token 替换 + 二次 filename 安全化（`re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", ...)`）+ 长度截断 200 字符
  - created_at 支持 4 种格式解析（Twitter v1.1 / ISO 8601 / ...）
- 7 处调用方传 context dict（`reader.py` + 6 个 X 批量 fetcher）：`tweet_id` / `screen_name` / `user_id` / `created_at` / `url`
- 新 env var：`X_MEDIA_FILENAME_PATTERN`（默认空 = 沿用旧行为）
- 单元测试 `tests/test_media_pattern.py`（11 case）：token 替换 / 缺失 created_at fallback / path traversal 安全化 / 无 token / 无扩展名 / 长度截断 / env 默认关闭 / url 优先于 ctx tweet_id

### 实测验证（账号 @ai_xiaomu）

| 场景 | URL / 命令 | 结果 |
|------|-----------|------|
| P2-1 默认抓取 | `https://x.com/ai_xiaomu` | ✅ 873/790/83/0，UserTweets 主路径无回归 |
| P2-3 retweeters | `x-retweeters 2051099012288356592` | ✅ 5 页 × 35 ≈ 176 个转推者，按 followers_count 倒序 |
| P2-3 favoriters 隐私降级 | `x-favoriters 2051099012288356592` | ✅ 0 用户 + WARNING 提示「作者可能隐藏」 |
| P2-3 URL 路由 | `https://x.com/ai_xiaomu/status/.../retweets` | ✅ 走 reader.py 调度 |
| P2-4 人物搜索 | `x-so "ai_xiaomu" --people` | ✅ 3 个匹配用户（按粉丝倒序） |
| P2-4 主路径回归 | `x-so "ai_xiaomu" --days 1 --min-faves 10` | ✅ 4 条 / Latest 模式无回归 |
| P1-3 默认关闭 | thread 抓取 | ✅ 未触发 Phase 8 |
| P1-3 启用 + 404 优雅降级 | `X_FETCH_MODERATED_REPLIES=true` + thread URL | ✅ 端点 404 被吞 + INFO 提示 + 主路径完成 |
| P2-2 默认 CDN-stem | `X_DOWNLOAD_MEDIA=true` 单推 | ✅ `HIj-jn8aMAAHHwh.jpg` |
| P2-2 pattern 生效 | `X_MEDIA_FILENAME_PATTERN="{date}_{screen_name}_{tweet_id}_{num}.{ext}"` | ✅ `20260518_ai_xiaomu_2056173124073525356_1.jpg` |
| 全套回归 | `pytest tests/` | ✅ **193/193 通过**（v0.22.0 是 153） |

### 新增单元测试统计

- v0.22.0: 153
- v0.23.0: 193（+40：P2-1 ×7 / P2-2 ×11 / P2-3 ×10 / P2-4 ×4 / P1-3 ×8）

### 新增 env vars（`.env.example` 已同步）

- `X_TWEET_USER_LIST_ENABLED` / `_MAX_PAGES` / `_DELAY` / `_PER_PAGE`
- `X_SEARCH_PEOPLE_MAX_PAGES` / `_DELAY` / `_PER_PAGE`
- `X_FETCH_MODERATED_REPLIES` / `X_MODERATED_REPLIES_MAX_PAGES`
- `X_MEDIA_FILENAME_PATTERN`

### 关键经验

- **Retweeters / Favoriters 与 Followers 同 schema**：返回 `TimelineUser` entries，直接复用 `_parse_user_list_response` + `extract_user_data`，零额外解析代码
- **People-tab 与普通搜索同 GraphQL endpoint**：只是 `product` variable 从 `"Latest"/"Top"` 改为 `"People"`，但 entry `itemType` 从 `TimelineTweet` 切到 `TimelineUser` — parser 必须区分
- **ModeratedTimeline 端点对非作者 Cookie 返回 404**：这是 Twitter 默认行为（仅作者本人能查询自己被隐藏的回复），不是 bug — 优雅降级 + INFO 提示比抛错更友好
- **媒体文件名 pattern 的 `{tweet_id}` 必须从 url 提取**：feedgrab 内部 `content.id` 是 MD5 hash 前 12 字符，不是 Twitter Snowflake；helper 内部从 `ctx["url"]` regex `/status/(\d+)` 拿真实 ID
- **path traversal 安全化必须二次清洗**：单独清洗 token 值 + 最终拼接结果都要走 `_FS_UNSAFE.sub("_", ...)`，防止 `../` 或 `screen_name=../etc/passwd` 类输入污染输出文件路径

### P1-1 推迟说明

调研发现 7 个独立 parser（`parse_user_tweets_entries` / `_parse_user_list_response` / `parse_moderated_timeline_entries` / `parse_list_tweets_entries` / `parse_bookmark_entries` / `parse_search_entries` / `parse_tweet_entries`）的差异维度涵盖：`item_filter` 三态（Tweet/User/None）/ `handle_pin_entry` / `handle_add_to_module` / `handle_replace_entry` / `cursor_types` / `sort_by_sortindex` / `fallback_path_scan` / `return_cursors`。helper 设计草案已落到调研报告。一次性重构涉及 #4/#5/#6/#7 的 sortIndex 默认启用变更（输出顺序变化），与用户协商后**单独拆到 v0.23.1**，便于做完整回归测试。

## 2026-05-17 · v0.22.0 · 融合 twitter-web-exporter：补 5 个高价值 GraphQL operation + 3 个解析鲁棒性增强

### 背景

调研 [prinsss/twitter-web-exporter](https://github.com/prinsss/twitter-web-exporter)（2.4k stars 油猴脚本）后发现：feedgrab 在"主动抓取技术"（queryId 4 级 fallback / x-client-transaction-id 签名 / Cookie 多账号轮换 / 6 级容灾）领先 twe 两代，但 **operationName 覆盖**（feedgrab 11 个 vs twe 19 个）和**响应 JSON 解析鲁棒性**有缺口。本次借鉴 twe 把高价值缺口补齐。完整对比报告：`开发及迭代方案调研报告/20260517-twitter-web-exporter-vs-feedgrab对比与融合方案.md`。

### 实施清单

#### A. 解析鲁棒性增强（3 项）

- **A1. `TweetTombstone` / `TweetUnavailable` 显式日志降级**
  - 来源：twe `utils/api.ts:222-247`
  - 实现：`extract_tweet_data()` 主推文 + quoted tweet 两处都加 typename 分支
  - 主推文遇到 tombstone/unavailable → `logger.warning` + 返回 None（不再静默吃掉）
  - quoted tweet 遇到 tombstone/unavailable → 返回 `{"is_tombstone": True, "tombstone_text": "..."}` 标记
  - 收益：被删除/隐藏的引用推文不再落地空数据

- **A2. `TimelinePinEntry` 置顶推文单独提取**
  - 来源：twe `modules/user-tweets/api.ts:48-57`
  - 实现：`parse_user_tweets_entries()` 在 instruction 循环加 `TimelinePinEntry` 分支
  - pin entry 标记 `_is_pinned: True` 后插入 entries 头部
  - `extract_tweet_data()` 把标记传到返回 dict 的 `is_pinned` 字段
  - `schema.from_twitter()` 透传到 extra，`storage.py` front matter 输出 `is_pinned: true`
  - `twitter_user_tweets.py` 在 thread/article 路径重新 fetch 时也保留 pinned 标记

- **A3. 视频 variant 不过滤 content_type**
  - 来源：twe `utils/api.ts:326-339`
  - 实现：主推文 + quoted 两处都改成"过滤有 bitrate 的 variant，按 bitrate 排序最大"
  - 旧版仅 `content_type == "video/mp4"`，新版兼容 webm 等未来格式
  - HLS m3u8 因没有 bitrate 字段自然被过滤掉

#### B. 5 个新 GraphQL operation（按用户优先级排序）

| operationName | queryId（fa0311/twitter-openapi）| 路径 | 模式 |
|--------------|-------------------------------|------|------|
| Followers | `IOh4aS6UdGWGJUYTqliQ7Q` | `data.user.result.timeline.timeline.instructions` | 返回 User |
| Following | `zx6e-TLzRkeDO_a7p4b3JQ` | 同上 | 返回 User |
| BlueVerifiedFollowers | `GQ1yZjbfSiPfi_5gznKMPw` | 同上 | 返回 User |
| ListMembers | `EkmM6fQjaFMaQbj2wGFQ9w` | `data.list.members_timeline.timeline.instructions` | 返回 User |
| ListSubscribers | `_av5eJHyhOzx9nTQkQg0iQ` | `data.list.subscribers_timeline.timeline.instructions` | 返回 User |
| Likes | `lIDpu_NWL7_VhimGGt0o6A` | `data.user.result.timeline.timeline.instructions` | 返回 Tweet |
| UserTweetsAndReplies | `6hvhmQQ9zPIR8RZWHFAm4w` | 同上 | 返回 Tweet |

- 4 级 queryId fallback 全员复用（cache → community fa0311 → JS bundle 扫描 → hardcoded）
- 7 个 op 都加进 `main_ops_missing` set，让 JS bundle 扫描可以覆盖
- Likes / UserTweetsAndReplies 直接复用 `parse_user_tweets_entries`（响应结构与 UserTweets 兼容）
- Followers/Following/BlueVerifiedFollowers/ListMembers/ListSubscribers 用新的 `parse_user_timeline_users` + `parse_list_members_users` + `parse_list_subscribers_users`
- 新文件 `feedgrab/fetchers/twitter_user_lists.py`：统一处理 5 种 user-list 模式（按 followers_count 倒序输出 MD 表 + CSV）

#### C. URL 路由 + 批量分发

- `reader.py` 识别 5 个新 URL pattern：
  - `/{user}/followers` / `/{user}/following` / `/{user}/verified_followers` → `twitter_user_lists`
  - `/{user}/likes` → `twitter_user_likes`
  - `/{user}/with_replies` → `twitter_user_replies`
  - `/i/lists/<id>/members` / `/i/lists/<id>/subscribers` → `twitter_user_lists`（必须在 list-tweets 检测之前）
- `_read_user_lists` / `_read_user_likes` / `_read_user_replies` 三个新方法挂在 `read()` 主分支
- `twitter_user_tweets.fetch_user_tweets(mode=...)` 增加 mode 参数（"tweets"/"likes"/"replies"），按 mode 选不同 page_fetcher 和不同输出 subfolder（`status_author/` / `likes_author/` / `replies_author/`）

#### D. 友好降级

- Likes 端点首次返回空时打 WARNING：`@<user> 的喜欢列表为空 — 该用户可能将其 Likes 设为私密（Twitter 默认行为）`
- 避免用户面对"总数 0"的沉默输出迷茫

### 实测验证（账号 @ai_xiaomu）

| 场景 | URL | 结果 |
|------|-----|------|
| A2 PinEntry 提取 | `/ai_xiaomu`（主页）| ✅ 21 entries，1 个置顶推文 `is_pinned=True` |
| B1 Followers | `/ai_xiaomu/followers` | ✅ 50 个用户，MD 按 followers_count 倒序 + CSV 17 字段 |
| B1 Following | `/ai_xiaomu/following` | ✅ 48 个用户 |
| B4 With_replies | `/ai_xiaomu/with_replies` | ✅ 38 条 / 16 新保存 / 22 跳过（已存在或转推），保存到 `replies_author/黄小木` |
| D Likes 私密提示 | `/ai_xiaomu/likes` | ✅ WARNING 触发："该用户可能将其 Likes 设为私密" |
| 全套测试无回归 | `pytest tests/` | ✅ 145/145 通过 |

### 新增单元测试

`tests/test_twitter_p0_enhancements.py`（10 个 case）：
- TweetTombstone / TweetUnavailable 主推文 + quoted 4 个 case
- TimelinePinEntry 头部插入 + extract_tweet_data 透传 + 无 pin 不误标 3 个 case
- 视频 variant：mp4 内最高 bitrate / 跨格式 webm 可选 / 全 HLS 跳过 3 个 case

### 新增 env vars（`.env.example` 已同步）

- `X_USER_LIST_ENABLED` / `X_USER_LIST_MAX_PAGES` / `X_USER_LIST_DELAY` / `X_USER_LIST_PER_PAGE`
- `X_USER_LIKES_ENABLED`
- `X_USER_REPLIES_ENABLED`

### P1 增量：sortIndex 稳定排序 + ModeratedTimeline 基础设施

- **P1-2 sortIndex 排序**（twe `utils/api.ts:24-29`）：新增 `_entry_sort_index` + `_sort_entries_by_sortindex` 通用 helper，按 snowflake-id BigInt 倒序。`parse_user_tweets_entries` 和 `_parse_user_list_response` 都接入；置顶推文在 sort 后再插入头部不受排序影响。实测 ai_xiaomu 主页：[PIN] 仍在首位，非置顶推文 sortIndex 严格 desc（4287 → 4286 → 4285 → 4284 → 4282，与预期完全一致）。
- **P1-3 ModeratedTimeline 基础设施**（twe `tweet-detail_api.ts:58-60`）：新增 `FALLBACK_MODERATED_TIMELINE_QUERY_ID` + `MODERATED_TIMELINE_FEATURES` + `fetch_moderated_timeline_page` + `parse_moderated_timeline_entries`。响应路径 `data.tweet.result.timeline_response.timeline.instructions` 与 TweetDetail 不同。默认未启用（无调用方），等用户场景明确再接入 thread 主路径，避免回归。
- **新增单元测试**：6 个 sortIndex case（int 解析、BigInt、缺失/空值、desc 排序、out-of-order 输入、pin 头部稳定性）+ 2 个 ModeratedTimeline parser case。
- **总测试**：145 → 153，全套通过。

### P1-1 通用 helper 重构（推迟到 v0.23.0）

调研报告中的 P1-1（抽出 `_extract_timeline_entries` 通用 helper 替换 6 处重复 instruction 解析）改动面较大、回归风险较高，单独做一个独立版本 v0.23.0 便于 review + 验证。本次 v0.22.0 不实施。

### 关键经验

- **twe 不是抓取技术参考，是字段解析参考**：油猴脚本运行在浏览器内被动拦截 XHR，零反爬负担。feedgrab 在主动签名/Cookie/容灾上领先两代，但 twe 沉淀的 19 个 endpoint 的响应路径和 typename 分支细节值得照搬。
- **TimelinePinEntry 是 instruction 级别，不是 entry 级别**：必须在 `for instruction in instructions:` 循环里处理，而不是在 `TimelineAddEntries.entries[]` 里找。
- **Likes API 默认隐私**：Twitter 把 Likes 设为隐私是默认行为，公开 Likes 才是少数。fetcher 必须给出明确日志，不能沉默返回 0。
- **`content_type` 过滤过紧会失去未来兼容性**：取所有有 bitrate 的 variant 按 bitrate 排序最大才是稳健做法。HLS m3u8 没有 bitrate 字段，自然被排除。
- **fa0311/twitter-openapi placeholder.json 是 queryId 金矿**：单一 JSON 暴露 80+ 个 operationName 的当前值，配合 feedgrab 已有的 4 级 fallback 机制，新增 op 几乎零运维成本。

## 2026-05-08 · v0.21.0 · 新增「知识星球」（Zsxq）平台支持

### 背景

用户在知识星球（zsxq.com）累积了大量长文章和短帖讨论，目前 feedgrab 没有专门支持，会落 generic/Jina 兜底。但 zsxq 完全不开放给未登录用户（401/302），Jina 必失败 → 必须设计专属 fetcher 复用浏览器登录态。本次以 `https://articles.zsxq.com/id_sz9kew31q6we.html`（长文章）和 `https://t.zsxq.com/yUX3P`（短链）为示例验证 URL。

### 调研要点

- **API base**：`https://api.zsxq.com/v2/`（不是 v1.10）
- **文章页**：`articles.zsxq.com/id_<hashid>.html` 是 SSR HTML，正文在 `.ql-editor` 内（参考 [yann0917/knowledge](https://github.com/yann0917/knowledge) `service.go` `GetArticle`）
- **单 topic API**：`GET /v2/topics/{topic_id}/info`
- **鉴权**：Cookie 模式（核心 `zsxq_access_token`，`.zsxq.com` 域）+ 固定 UA + `X-Timestamp` + `X-Version: 2.37.0`
- **未登录响应**：401 / 302 → wx.zsxq.com/dweb2/login（实测验证）
- **短链**：`t.zsxq.com/<code>` 302 跳转移动 H5 URL（`?topic_id=<digits>&inviter_id=...`）
- **topic 类型**：talk / question + answer / article / **solution**（v0.21.0 全部覆盖；`solution` 是 zsxq 较新的"问答+解决方案"形态，实测 API 返回数据中 `topic.title` 为提问，`topic.solution.text` 为解答）

### 方案决策

- **范式**：完全对齐 LinuxDo 的四级 Tier 链路（HTTP cookie → CDP 复用 → Stealth Browser → Jina）
- **Tier 0 双形态**：
  - article：HTTP GET `articles.zsxq.com/id_<hashid>.html` → BeautifulSoup `.ql-editor` → markdownify
  - topic：HTTP GET `api.zsxq.com/v2/topics/<tid>/info` → JSON → 五形态分发渲染
- **Tier 1**：CDP `connect_over_cdp(ws://9222/devtools/browser)` → 找 `.zsxq.com` cookie 的 context → `page.goto` 取 HTML / `page.evaluate(fetch(api))` 取 JSON
- **Tier 2**：Stealth Playwright launch + `sessions/zsxq.json`，complete the same flow
- **Tier 3 Jina**：保留姿态，但 zsxq 强登录态门控基本无效；明确 401/404/business-failed 时直接终止不下沉
- **短链 302 解析前置**：在 `parse_zsxq_url()` 之前调 `_resolve_zsxq_short_url()`，参照 Douyin v.douyin.com 做法
- **评论三态**：`ZSXQ_COMMENT_MODE=none|all|author`（默认 none），对齐 LinuxDo `LINUXDO_REPLY_MODE`
- **媒体下载**：`ZSXQ_DOWNLOAD_MEDIA` 开关，默认 false（保守起步，留 v0.21.x 完善 referer 防盗链处理）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/zsxq.py` | 新增 | ~830 行核心 fetcher（短链 302 / URL 解析 / `.ql-editor` 解析 / 五形态 topic 渲染 / 三态评论 / 四级 Tier） |
| `feedgrab/reader.py` | 修改 | `_detect_platform()` 加 `zsxq.com` 域名识别 + `_fetch()` 加 dispatch + `_dedup_plat_map[ZSXQ]="Zsxq"` |
| `feedgrab/schema.py` | 修改 | `SourceType.ZSXQ` 枚举 + `from_zsxq()` 工厂 |
| `feedgrab/utils/storage.py` | 修改 | `PLATFORM_FOLDER_MAP[ZSXQ]="Zsxq"` + 文件名规则 + zsxq extras front matter 字段 + published 解析 |
| `feedgrab/login.py` | 修改 | `PLATFORM_URLS["zsxq"]` + `_CDP_COOKIE_DOMAINS["zsxq"]` + `_CDP_COOKIE_URLS["zsxq"]` 三 dict 各加一行 |
| `feedgrab/config.py` | 修改 | 新增 7 个配置函数：`zsxq_enabled` / `zsxq_cdp_enabled` / `zsxq_page_load_timeout` / `zsxq_comment_mode` / `zsxq_max_comments` / `zsxq_download_media` / `zsxq_api_version` |
| `.env.example` | 修改 | 追加 zsxq 配置段 |
| `tests/test_zsxq.py` | 新增 | 21 个回归测试（URL 解析 / `.ql-editor` HTML→MD / 五形态 topic JSON / 终止判定 / 三态评论筛选 / schema 工厂 / front matter） |
| `pyproject.toml` | 修改 | 0.20.1 → 0.21.0 |
| `开发及迭代方案调研报告/20260508-v0.21.0-知识星球文章抓取需求规格.md` | 新增 | 完整需求规格（450+ 行） |
| `开发及迭代方案调研报告/20260508-v0.21.0-知识星球-实施计划.md` | 新增 | plan 文件（230+ 行） |

### 关键解析细节

- **作者**：优先 `<meta name="author">`，回退到 `.author-info .nick-name` span（zsxq 文章页 SSR 结构）
- **group_name**：优先 `<meta property="og:site_name">`，回退到 `.group-name` span
- **group_id**：从 `.group-info a[href]` 中正则匹配 `/group/(\d+)`
- **cover_image**：优先 `<meta property="og:image">`，回退到 `.ql-editor` 内第一个 `<img src>`
- **Quill 代码块**：`<pre class="ql-syntax" spec-language="X">` → 4 反引号围栏（与 LinuxDo / Feishu 对齐）
- **空标题清理**：移除 Quill 偶尔产生的空 `# ` 标题行（regex `^#{1,6}\s*$`）
- **topic title 截断**：超过 60 字符截断 + `…`（避免 solution 形态把整篇提问内容都拼进 title）
- **短链 query 解析**：`?topic_id=<digits>` 形态映射为 topic 类型（H5 / 邀请短链跳转目标）

### 验证结果

- ✅ 实测 `feedgrab https://articles.zsxq.com/id_sz9kew31q6we.html` 一次成功落盘 .md（Tier 0 直接打通）
  - 标题 / 作者「不养猫的薛定谔」/ group_id / group_name / cover_image 全部解析正确
- ✅ 实测短链 `feedgrab https://t.zsxq.com/yUX3P` → 自动 302 解析 → 正确路由到 topic API
- ✅ 实测长链 `feedgrab https://wx.zsxq.com/group/.../topic/...` → Tier 2 浏览器内 fetch JSON → 正确渲染 solution 形态（提问 + 解答）
- ✅ 边界用例：不存在的 article hashid → 走完三级 Tier 后明确报错"知识星球文章不存在或已被删除"，**不写垃圾 .md**
- ✅ `pytest tests/test_zsxq.py -v` 21 个用例全部通过
- ✅ `pytest tests/` 全套 135 个测试通过（无回归）

### 状态

已完成 ✅

---

## 2026-05-06 · v0.20.1 · 修复长 thread 被误判为 Article 导致 quoted tweet 丢失

### 背景

用户实测一条长 thread（root 推文 700+ 字符 + 2 条带 quoted tweet 的自回复）落盘后，发现两个引用推文的内容、作者、链接全部丢失。Markdown 里只剩下三段裸文本，引用块完全没渲染。

### 根因

`feedgrab/schema.py → from_twitter()` 旧逻辑用启发式判断 Article：

```python
is_article = len(root_text) > 500 and len(tweets) <= 3
```

只看 root 文本长度 + thread 数量，不看 GraphQL/Jina 是否真的拿到了 Article body。结果上面那个真实 thread（root 长 + 3 条以内）被误判为 Article，走进 article 渲染分支 —— 而 article 分支只处理 `images` / `videos`，**完全不调用 `_render_quoted_tweet()`**，所以引用推文被静默丢弃。

### 方案决策

- **判定权威化**：改用 `_has_article_body(article_data)` —— 只有当 `article_data["body"]` 实际内容超过 200 字符（GraphQL 的 `content_state` 原生渲染或 Jina 兜底真的产出了 Article 正文）时，才认定为 Article。其他情况一律按 thread 处理。
- **统一渲染路径**：抽出 `_render_twitter_tweet_part(t, prefix)` 处理单条推文（文本 + 媒体 + 引用推文），article 和 thread 共用一套循环。article 模式下，第一条推文用 `article_body` 替换文本，但媒体、引用推文、自回复仍按正常流程渲染 —— 真正的 Article（带封面图、长正文、几条简短自回复）能拿到完整内容，假的 Article（被误判的长 thread）也能保住引用块。
- **回归测试**：新增 `tests/test_twitter_rendering.py::test_long_thread_with_quoted_tweet_is_not_misclassified_as_article`，用真实 thread 形态（root 700+ 字符 + 2 条带 quoted tweet 自回复 + `article_data: {}`）断言两个引用推文文本和作者标记都出现在最终 Markdown 里。

### 改动范围

- `feedgrab/schema.py`：新增 `_render_twitter_tweet_part()` / `_has_article_body()`；重写 `from_twitter()` 中的 thread 渲染分支（30 行净减少，逻辑更扁平）。
- `tests/test_twitter_rendering.py`：新增回归测试 1 个。
- `pyproject.toml`：版本 0.20.0 → 0.20.1。
- `CLAUDE.md`：当前版本号 + 迭代历史摘要表新增 v0.20.1 行。

### 验证结果

- ✅ `pytest tests/test_twitter_rendering.py -v` 通过（1 passed in 0.02s）。
- ✅ 真实 thread 形态：root 长文 + 2 条带 quoted tweet 自回复 → 两个引用块完整渲染（`> **作者** (@handle)` + 引用文本）。
- ✅ 真正 Article 形态（`article_data.body` 非空 + 封面图）：cover image 仍在最顶部，正文用 article_body 替换，引用推文 + 媒体仍保留。
- ✅ 单条推文 / 短 thread / 带 quoted tweet 普通推文：行为不变。

## 2026-05-05 · v0.20.0 · 五平台扩展（HackerNews / Medium / Reddit / Weibo / Douyin）

### 背景

经 [TOP 5 ROI 对比技术方案](开发及迭代方案调研报告/20260505-AI-agent-浏览器与采集工具对比技术方案.md) 与 [v0.20.0 需求规格](开发及迭代方案调研报告/20260505-v0.20.0-五平台需求规格.md) 评审，按"由易到难、覆盖中英文+海外+短视频"原则，一次性补齐 5 个高 ROI 平台。所有决策点（4 个范围决策 + 3 个工程决策）已与用户确认，发版策略 A（一次性 v0.20.0 集中发布）。

### 方案决策

- **HackerNews**：使用 [Hacker News Firebase API v0](https://github.com/HackerNews/API)，0 Cookie / 0 反爬，单条 item + 一层评论（默认 50），支持 `feedgrab hn top|new|best|ask|show|jobs --limit N` 列表批量。
- **Medium**：单篇 `Tier 0 Jina Reader → Tier 1 JSON-LD articleBody → Tier 2 Stealth Browser`；用户/出版物批量直接走 `medium.com/feed/<@username|publication-slug>` RSS + 单篇 Tier 链补全；member-only 文章在 Jina 输出检测后优雅降级，front matter 标 `is_member_only`。
- **Reddit**：`Tier 0 old.reddit.com .json + 自报 UA → Tier 1 CDP 复用 Chrome reddit.com cookie → Tier 2 Stealth Playwright + sessions/reddit.json → Tier 3 Jina`，浏览器内 `fetch(json_url)` 复用登录态；评论按 score 排序取首屏 50 条；`reddit-sub` 支持 hot/new/top/best/rising 五种排序。
- **Weibo**：m.weibo.cn 移动端 API（`/statuses/show?id=<mid>` + `/api/container/getIndex`），SUB Cookie 走 `WEIBO_COOKIE` env 或 `sessions/weibo.json`，无 SUB 时仍可拿基础公开数据；SSR 兜底解析 `$render_data`；转发链路保留原微博引用块。
- **Douyin**：`Tier 0 CDP 复用 Chrome → Tier 1 Stealth Playwright launch + sessions/douyin.json → Tier 2 SSR RENDER_DATA 解析 → Tier 3 Jina`；明确决策**不破解** a_bogus / X-Bogus / msToken（签名算法每月变），依赖浏览器内执行自动签名（与 XHS Pinia Store 注入同思路）；短链 `v.douyin.com/<code>` 走 302 解析 aweme_id。
- **跨模块约定**：`schema.SourceType` 新增 5 个枚举值（全小写命名）；`storage.PLATFORM_FOLDER_MAP` 注册 5 个目录（首字母大写：HackerNews / Medium / Reddit / Weibo / Douyin）；`reader.UniversalReader._detect_platform` 加 5 段域名识别；`save_to_markdown` 改为返回路径字符串（向后兼容，原先无人依赖返回值）。

### 改动范围

- 新增 fetcher：`feedgrab/fetchers/hackernews.py`（~360 行）、`medium.py`（~310 行）、`reddit.py`（~430 行）、`weibo.py`（~340 行）、`douyin.py`（~310 行）。
- 集成层：`schema.py`（5 个 `from_*` 工厂函数 + 5 个 SourceType 枚举值）、`reader.py`（域名识别 + 5 个 dispatch 分支）、`config.py`（5 平台配置函数共 ~120 行）、`utils/storage.py`（PLATFORM_FOLDER_MAP + 文件名规则 + front matter 扩展字段 + published 解析分支）。
- CLI 子命令：`hn <category> --limit N` / `medium-user @<handle> --limit N` / `medium-pub <slug> --limit N` / `reddit-sub <sub> --sort hot|new|top|best --limit N` / `weibo-user <uid> --limit N`。
- 配置：`.env.example` 追加 5 个平台配置段；`pyproject.toml` 版本升 0.20.0。

### 关键决策细节

- **HN created_at 用 ISO 8601** (`YYYY-MM-DDTHH:MM:SSZ`) — 让现有 `parse_twitter_date_local` 直接解析，无需改 storage.py 主分支；显示头另用 `_format_unix_time_display` 渲染人类可读时间。
- **Reddit 默认 UA** = `feedgrab/0.20.0 (+https://github.com/iBigQiang/feedgrab)`，自报身份合规，`REDDIT_USER_AGENT` 留空时使用。
- **微博 created_at** 走 `email.utils.parsedate_to_datetime` 解析 RFC 2822（`Sun Mar 17 12:34:56 +0800 2024`），统一转为 ISO 8601。
- **Reddit 评论树**：MVP 仅首屏顶层 50 条，过滤 stickied，不展开 "load more comments"；`REDDIT_FETCH_ALL_COMMENTS` 留作 v0.21+ 占位。
- **保留 SourceType.WEB 不动**：自定义域名 Medium / 未识别页面继续走付费墙 + Jina 兜底，避免 v0.20.0 触动 generic 分支造成回归。

### 验证结果

- ✅ HackerNews：实抓 `news.ycombinator.com/item?id=1`（"Y Combinator", pg, 2006-10-09, 57 分 / 3 评论）成功；`feedgrab hn top --limit 2` 实跑两条 top story 落盘成功。
- ✅ URL 检测：5 个新平台 + redd.it 短链 + v.douyin.com 短链 + medium.com / *.medium.com 子域名 + weibo.com / weibo.cn 多域名 全部正确归类。
- ✅ Medium URL 解析：`@user/article` / `@user` 主页 / `subdomain.medium.com/article` / `publication/article` / `publication` 主页 5 种形态分类正确。
- ✅ Reddit Tier 链：直接 .json 403 → 浏览器 fetch 自动接管 → 失败下沉 Jina；端到端调用链贯通。
- ✅ 回归测试：113 个既有测试全部通过（feishu / linuxdo / idcflare / paywall / p1_platforms）。
- ⏳ Weibo / Douyin / Medium / Reddit 完整真实抓取留作 v0.20.0 发布后用户实跑反馈。

## 2026-04-30 · v0.19.0 · IDCFlare 平台支持 + 飞书知识库目录/表格修复

### 背景

LinuxDo 的 Discourse 专用链路落地后，用户继续提出两类实际问题：

- **Discourse 扩展诉求**：希望把 `idcflare.com` 也纳入和 LinuxDo 同级的专用抓取链路，而不是退回 generic/Jina；同时论坛类帖子默认不应抓完整楼层，而应以“主贴 + 楼主自回”为主，并保留可切换配置。
- **飞书知识库回归问题**：`feishu-wiki` 在部分新知识库页面中拿不到左侧虚拟树目录，导致只抓到当前页；另外飞书代码块和表格在知识库/单篇混合链路下出现回归，表现为 fallback code 丢内容、代码围栏嵌套失配、以及表格被压成一行导致单元格错位。

### 方案决策

- **新增 IDCFlare / Discourse 专用抓取器**：延续 LinuxDo 的 JSON-first 思路，采用 `游客 topic JSON → CDP 复用 Chrome → Stealth Playwright 页面内 fetch → Jina 最后兜底`，避免游客错误页和 DOM 结构抖动污染正文。
- **Discourse 回复改为三态配置**：新增 `LINUXDO_REPLY_MODE` / `IDCFLARE_REPLY_MODE`，支持 `author`（默认，仅主贴 + 楼主自回）/ `all`（完整楼层）/ `none`（仅主贴），并把 `reply_mode`、`rendered_reply_count` 写入 front matter，保证可追溯。
- **会话预热改为本地 session 优先**：LinuxDo / IDCFlare 在抓取前先尝试 `CDP Cookie 同步 → 浏览器访客态 seed`，首次成功后落地 `sessions/{platform}.json`，后续抓取直接复用，不再每次依赖 CDP 在线。
- **飞书知识库目录改抓虚拟树节点**：不再只扫 sidebar anchor，而是优先读取树节点 `data-node-uid` / `wikiToken`，支持展开虚拟滚动树、去重重复节点、清理零宽字符标题，确保 `feishu-wiki` 能真正枚举整库文档。
- **飞书 Markdown 渲染统一修复**：
  - fallback snapshot 中 `type=code` 的 block 直接按真实代码块渲染
  - 代码块统一使用 4 个反引号围栏，规避正文中内嵌三反引号导致的 fence 提前闭合
  - 表格优先读取 `snapshot.rows_id / columns_id` 推断行列，修复知识库文档中整表被压成一行的问题

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/idcflare.py` | 新增 | IDCFlare / Discourse 专用抓取器（JSON-first + CDP/浏览器/Jina 多级兜底 + 会话预热 + 楼主回复筛选） |
| `feedgrab/fetchers/linuxdo.py` | 修改 | 新增 `reply_mode` 三态筛选、session warmup、登录引导增强、代码块/表情图过滤修复 |
| `feedgrab/fetchers/feishu.py` | 修改 | fallback code snapshot 渲染、四反引号围栏、表格维度识别改读 `rows_id/columns_id` |
| `feedgrab/fetchers/feishu_wiki.py` | 修改 | 改为虚拟目录树抓取、复用 `_evaluate_feishu_doc_on_page()`、统一文档页图片预下载与标题清理 |
| `feedgrab/config.py` | 修改 | 新增 `IDCFLARE_*` 配置项 + `LINUXDO_REPLY_MODE` / `IDCFLARE_REPLY_MODE` |
| `feedgrab/login.py` | 修改 | `feedgrab login idcflare` + CDP Cookie 域名识别 |
| `feedgrab/reader.py` | 修改 | 新增 `idcflare` 平台识别、路由、去重目录映射 |
| `feedgrab/schema.py` | 修改 | 新增 `SourceType.IDCFLARE`、`from_idcflare()`，并扩展 Discourse front matter 元数据 |
| `feedgrab/utils/storage.py` | 修改 | 新增 `IDCFlare/` 输出目录、文件名规则、Discourse `reply_mode/rendered_reply_count` front matter |
| `feedgrab/fetchers/kdocs.py` | 修改 | 代码块统一使用四反引号围栏 |
| `feedgrab/fetchers/wechat_search.py` | 修改 | 代码块统一使用四反引号围栏 |
| `feedgrab/fetchers/twitter_fxtwitter.py` | 修改 | Article code-block 统一使用四反引号围栏 |
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | Article code-block 统一使用四反引号围栏 |
| `tests/test_linuxdo.py` | 修改 | 新增楼主回复模式、session warmup、blockquote 代码块、贴纸 GIF 过滤回归测试 |
| `tests/test_idcflare.py` | 新增 | IDCFlare URL/Markdown/JSON/回复模式/session warmup 回归测试 |
| `tests/test_feishu_wiki.py` | 新增 | 飞书知识库目录节点归一化、fallback code、四反引号、`rows_id/columns_id` 表格维度回归测试 |

### 验证结果

- ✅ `pytest tests/test_feishu_wiki.py tests/test_feishu_sheet_decode.py tests/test_linuxdo.py tests/test_idcflare.py -q` → 通过
- ✅ 实抓 `python -m feedgrab.cli "https://linux.do/t/topic/1959236"` 成功，默认输出主贴 + 楼主自回模式
- ✅ 实抓 `python -m feedgrab.cli "https://idcflare.com/t/topic/44294"` 成功，走 IDCFlare 专用链路而非 generic/Jina
- ✅ 实抓 `python -m feedgrab.cli "https://ycnj2htgnvdy.feishu.cn/wiki/RSabw1BDWiCVQVkR6LKcyABznvd"` 成功，表格不再整行错位
- ✅ 实抓 `python -m feedgrab.cli "https://ycnj2htgnvdy.feishu.cn/wiki/WtREwKEJXiYPJ3k9xabcyWfmnDd"` 成功，表格恢复为正常行列

### 状态

已完成 ✅

---

## 2026-04-22 · v0.18.0 · LinuxDo / Discourse 平台支持 + 折叠块混合渲染

### 背景

用户执行通用网页抓取 `feedgrab https://linux.do/t/topic/2023688` 时，旧的 generic/Jina 兜底会把论坛错误页保存成 `Manual/Page Not Found` 一类无效 Markdown，帖子本身的楼层、作者、时间、分类、标签等结构化信息全部丢失。另一方面，LinuxDo 帖子中的 `<details><summary>点我展开</summary>` 折叠块在 Markdown 导出后容易失效，要么无法折叠，要么直接平铺显示，影响 Obsidian 阅读体验。

### 方案决策

- **新增 Discourse 专用链路**：LinuxDo 不再走 generic/Jina 主路径，而是采用 `游客 JSON API → 已登录 Cookie / Cloudflare Cookie 的 CDP 页面内 fetch → Stealth Playwright 页面内 fetch → Jina 最后兜底`
- **终止错误页下沉**：对明确的 404 / 私有帖 / 需登录场景提前终止，不再继续 Jina，避免把错误页当正文写入 `Manual/`
- **折叠块混合渲染**：
  - 简单折叠块输出 Obsidian collapsible callout：`> [!feedgrab-fold]-`
  - 复杂折叠块输出纯 HTML：`<details class="feedgrab-fold ...">`，保留真实折叠能力与跨 Markdown 工具的兼容性
- **论坛帖专属输出**：新增 `SourceType.LINUXDO`、`LinuxDo/` 输出目录、论坛 front matter 和文件名规则，让作者、时间、分类、楼层统计直接进入 Obsidian 索引

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/linuxdo.py` | 新增 | LinuxDo / Discourse 专用抓取器（JSON-first + CDP/浏览器/Jina 多级兜底 + 折叠块 HTML/Callout 混合渲染） |
| `feedgrab/reader.py` | 修改 | 新增 `linuxdo` 平台识别、路由分发和去重平台映射 |
| `feedgrab/schema.py` | 修改 | 新增 `SourceType.LINUXDO` 与 `from_linuxdo()` 工厂 |
| `feedgrab/utils/storage.py` | 修改 | 新增 `LinuxDo` 目录映射、ISO 时间格式化、front matter 字段与文件名规则 |
| `feedgrab/config.py` | 修改 | 新增 `LINUXDO_CDP_ENABLED` / `LINUXDO_PAGE_LOAD_TIMEOUT` 配置读取 |
| `feedgrab/login.py` | 修改 | `feedgrab login linuxdo` + CDP Cookie 域名识别 |
| `tests/test_linuxdo.py` | 新增 | URL 解析、Markdown 转换、折叠块渲染、平台识别、UnifiedContent 映射回归测试 |

### 验证结果

- ✅ `pytest -q tests/test_linuxdo.py` → 通过
- ✅ `pytest -q tests/test_linuxdo.py tests/test_p1_platforms.py tests/test_paywall.py` → 通过
- ✅ 实抓 `python -m feedgrab.cli "https://linux.do/t/topic/2032561"` 成功，生成 `LinuxDo/` Markdown
- ✅ 实抓 `python -m feedgrab.cli "https://linux.do/t/topic/2032344"` 成功，`点我展开` 折叠块在导出 Markdown 中保留可折叠效果
- ✅ 实抓 `python -m feedgrab.cli "https://linux.do/t/topic/2023688"` 现在会明确报错“帖子不存在或无权访问”，不再保存错误页 Markdown

### 状态

已完成 ✅

---

## 2026-04-19 · v0.17.0 · 小宇宙 / 喜马拉雅 / B 站字幕（P1 三平台支持）

### 背景

feedgrab v0.16.0 完成付费墙绕过后，继续执行 `迭代方案调研报告/20260419-qiaomu-anything-to-notebooklm对比分析.md` 的 P1 计划：**三个头部中文音频/视频平台 —— 小宇宙（xiaoyuzhoufm.com）、喜马拉雅（ximalaya.com）、B 站字幕/转录**。其中小宇宙之前 `_detect_platform` 已识别为 `"podcast"` 但无 fetcher，落 Jina 兜底只能拿 SSR 简介；喜马拉雅完全未识别；B 站 `fetchers/bilibili.py` 只抓元数据，无字幕/转录能力。

P1 目标：**任意播客 URL → 完整带时间戳的中文转录 Markdown**，不依赖 qiaomu 用的付费第三方 Get笔记 API，全部自研逆向 + 复用已有 YouTube Whisper 管线。

### 功能内容

1. **新增 `feedgrab/utils/transcribe.py`**（~180 行）—— Whisper 共享薄层，不重构 youtube.py
   - `groq_transcribe_file(audio_path)` 本地文件转录（≥95 MB 自动 ffmpeg 分片）
   - `groq_transcribe_url(audio_url, referer)` 下载 + 转录 + 自动清理
   - `format_transcript(snippets, description)` 委托 `youtube._parse_chapters` + `_segment_into_sentences` + `_format_transcript_markdown`
   - `subtitle_body_to_snippets(body)` 将 B 站字幕 `body` 转成 Whisper snippets 格式

2. **新增 `feedgrab/utils/bilibili_wbi.py`**（~150 行）—— B 站 WBI 签名自研实现
   - `fetch_wbi_keys()` 从 `/x/web-interface/nav` 提取 `img_key` + `sub_key`，5 分钟磁盘缓存（`sessions/cache/bilibili_wbi_key.json`）
   - `get_mixin_key()` 64 元素 `MIXIN_KEY_ENC_TAB` 置换 → 取前 32 字符
   - `sign_wbi_params()` 按 key 排序 + 过滤 `!()*'` + URL 编码 + `wts` + `md5(query+mixin_key)` → `w_rid`
   - 真实网络验证：生成 `mixin_key=ea1db124af3c7062474693fa704f4ff8`（与社区文档金标准一致）

3. **新增 `feedgrab/fetchers/xiaoyuzhou.py`**（~180 行）—— 小宇宙单集抓取
   - Tier 1：HTTP 抓页面 → 正则提取 `<script id="__NEXT_DATA__">` → `json.loads` → `props.pageProps.episode.media.source.url` (m4a)
   - Tier 2：`groq_transcribe_url(m4a, referer="https://www.xiaoyuzhoufm.com/")` 转录
   - shownotes HTML → Markdown（markdownify）
   - 完整元数据：title / podcast_name / podcast_id / author / duration / pubDate / image / shownotes

4. **新增 `feedgrab/fetchers/ximalaya.py`**（~190 行）—— 喜马拉雅单集抓取
   - Tier 1：`GET /revision/track/v2/audio?ptype=1&trackId={id}` → `data.src` + `canPlay`
   - 若 `canPlay=False`（付费节目）：logger.warning 提示 + 返回可用元数据（不强破解）
   - Tier 2：`GET /revision/track/simple?trackId={id}` → 标题 + 专辑 + 主播
   - Tier 3：`groq_transcribe_url(src, referer="https://www.ximalaya.com/")`（仅免费节目）
   - 支持 3 种 URL 格式：`/sound/{id}` / `/{cat}/{aid}/{tid}` / 移动 `m.ximalaya.com/sound/{id}`

5. **扩展 `feedgrab/fetchers/bilibili.py`**（+~170 行）—— 字幕抓取 + Whisper 兜底
   - 现有 `x/web-interface/view` → 补充 `aid` / `cid`（之前没取）
   - Tier 1 `/x/player/v2`（无签名，覆盖老视频）→ Tier 2 `/x/player/wbi/v2`（WBI 签名，覆盖新视频）
   - 字幕选择：exact lang → `zh-CN` → `zh-Hans` → `zh-Hant` → `zh` → `ai-zh` → `en` → first
   - 字幕 JSON 下载 + `subtitle_body_to_snippets` → `format_transcript` 统一 Markdown 输出
   - Tier 3 Whisper 兜底：`BILIBILI_SUBTITLE_WHISPER=true` 时调 `youtube._transcribe_via_whisper`（yt-dlp 支持 B 站）
   - 新增 extra 字段：`aid` / `cid` / `like_count` / `coin_count` / `favorite_count` / `has_transcript`

6. **2 个新 SourceType**：`SourceType.XIAOYUZHOU` / `SourceType.XIMALAYA`，对应 `output/Xiaoyuzhou/` / `output/Ximalaya/`

7. **2 个新工厂 + 1 个扩展工厂**（`schema.py`）：
   - `from_xiaoyuzhou(data)` / `from_ximalaya(data)` 新建
   - `from_bilibili(data)` 扩展：`content` 拼接 description + `## 🎙️ 转录` + transcript；`extra` 新增 aid/cid/like/coin/favorite/has_transcript
   - 共享 `_build_podcast_content()` 拼装 `## 📝 Shownotes` + `## 🎙️ 完整转录` 两段

8. **reader.py 路由**：`xiaoyuzhoufm.com` → `"xiaoyuzhou"`（原 `"podcast"`），新增 `ximalaya.com` → `"ximalaya"`，`_fetch` +2 分支

9. **配置项（7 个）**：
   - `XIAOYUZHOU_ENABLED` / `XIAOYUZHOU_WHISPER`（均默认 true）
   - `XIMALAYA_ENABLED` / `XIMALAYA_WHISPER`（均默认 true）
   - `BILIBILI_SUBTITLE_ENABLED`（默认 true）/ `BILIBILI_SUBTITLE_LANG`（默认 `zh-CN`）/ `BILIBILI_SUBTITLE_WHISPER`（默认 false，节约 Groq 额度）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/utils/transcribe.py` | 新增 | Whisper 共享层（4 个公开 API） |
| `feedgrab/utils/bilibili_wbi.py` | 新增 | WBI 签名 + nav 缓存 |
| `feedgrab/fetchers/xiaoyuzhou.py` | 新增 | SSR 提取 + Whisper |
| `feedgrab/fetchers/ximalaya.py` | 新增 | Web API + canPlay 降级 + Whisper |
| `feedgrab/fetchers/bilibili.py` | 重写 | Tier 0 元数据 + Tier 1/2 字幕 + Tier 3 Whisper |
| `feedgrab/config.py` | 修改 | +7 个 `xiaoyuzhou_*()` / `ximalaya_*()` / `bilibili_subtitle_*()` 配置函数 |
| `feedgrab/schema.py` | 修改 | +2 SourceType + 2 from_* 工厂 + 扩展 from_bilibili + `_build_podcast_content` 共享 |
| `feedgrab/reader.py` | 修改 | 路由：+ `ximalaya`，`xiaoyuzhou` 替代原 `podcast`；`_fetch` +2 分支 |
| `feedgrab/utils/storage.py` | 修改 | `PLATFORM_FOLDER_MAP` +2 条 |
| `.env.example` | 修改 | +3 个 section（小宇宙 / 喜马拉雅 / B 站字幕） |
| `tests/test_p1_platforms.py` | 新增 | 41 条单元测试 |

### 验证结果

- ✅ `pytest tests/test_p1_platforms.py -v` → **41 passed**
- ✅ `pytest tests/ -q` → **70 passed in 0.42s**（29 旧 + 41 新）
- ✅ WBI 签名网络验证：`mixin_key=ea1db124af3c7062474693fa704f4ff8`（社区文档金标准）
- ✅ B 站实抓（`BV1GJ411x7h7`）：Tier 0 元数据 + Tier 1 v2 + Tier 2 WBI 全部调通；音乐视频无字幕，优雅降级（`has_transcript: False`）
- ✅ 小宇宙 fetcher：HTTP 404 等错误正确抛 RuntimeError
- ✅ YouTube 回归：`format_transcript` 正确委托 youtube.py 的 `_parse_chapters` + `_segment_into_sentences` + `_format_transcript_markdown`，输出含章节标题（`## Intro [0:00]`）+ `[HH:MM:SS → HH:MM:SS]` 时间戳
- ✅ 路由检测：`xiaoyuzhoufm.com→xiaoyuzhou`、`ximalaya.com→ximalaya`、`bilibili→bilibili`、`github→github`（未被新逻辑劫持）
- ✅ 配置默认值正确（`BILIBILI_SUBTITLE_WHISPER=false` 节约 Groq 额度）

### 设计决策

- **不重构 youtube.py**：新建 `utils/transcribe.py` 作为"共享薄层"委托 youtube.py 内部函数（`_whisper_single`/`_whisper_chunked`/`_segment_into_sentences`/`_format_transcript_markdown`/`_parse_chapters`），零动现有代码保证 YouTube 回归
- **小宇宙走 SSR 不用移动 API**：移动 API 需 device token；`__NEXT_DATA__` 完整（episode + podcast + shownotes + m4a URL），技术路线与 xhs `__INITIAL_STATE__` 一致
- **喜马拉雅付费节目不破解**：`canPlay=false` 优雅降级为"元数据 + warning"，feedgrab 定位知识管理而非盗版
- **B 站字幕 3 级兜底，Whisper 默认关**：字幕免费免额度；Whisper 显式启用避免意外超配额
- **WBI 签名自研不引 bilibili-api-python**：算法公开稳定 4 年未变，仅 150 行 Python，无需引入 >10k 行大库
- **3 个独立 SourceType**：与现有每平台独立 SourceType 风格一致，独立目录便于 Obsidian 索引

### 状态

已完成 ✅

---

## 2026-04-19 · v0.16.0 · 付费墙绕过 + JSON-LD articleBody 提取（融合 qiaomu fetch_url.sh）

### 背景

feedgrab 此前对未识别 URL（generic 路径）只用 Jina Reader 一层兜底，对 NYT/WSJ/FT/Economist/Bloomberg/SCMP 等 300+ 主流付费新闻网站命中率低：Jina 对软付费墙勉强能抓一部分，对硬付费墙（The Information、Bloomberg premium）基本返回付费墙提示页。参考调研报告 `迭代方案调研报告/20260419-qiaomu-anything-to-notebooklm对比分析.md`，`qiaomu-anything-to-notebooklm/scripts/fetch_url.sh` 的 6 层付费墙级联绕过策略（含 JSON-LD 提取）经评估为唯一独立价值显著的融合点。本次用 Python 重写移植，作为 Jina 之前的前置兜底。

### 功能内容

1. **新增 `feedgrab/utils/jsonld.py`**：通用 JSON-LD `articleBody` 提取器，支持 Schema.org NewsArticle / Article / BlogPosting 等 10 种类型，`@graph` 嵌套数组、`@type` 数组、`author` 多种形态（str/dict/list），多 JSON-LD 块中选最长 body
2. **新增 `feedgrab/fetchers/paywall.py`**：7 级 Tier 级联绕过引擎，4 套硬编码域名列表
   - **Tier 0** 直接 JSON-LD 探测（非付费站点也跑，<1s 拿到 articleBody，比 Jina 快 5-10 倍）
   - **Tier 1** Googlebot UA + `X-Forwarded-For: 66.249.66.1` + Google Referer（22 个头部付费站）
   - **Tier 2** Bingbot UA（4 站）
   - **Tier 3** 通用策略 a/b/c/d：Googlebot / Bingbot / Facebook Referer / Twitter Referer（44 站）
   - **Tier 4** AMP 页面（尝试 `/amp`、`?outputType=amp`、`.amp.html`、`?amp` 4 种模式 + `.html` → `.amp.html` 重写，12 站）
   - **Tier 5** EU IP (`X-Forwarded-For: 185.X.X.X`) + Google Referer
   - **Tier 6** archive.today 存档（CAPTCHA 自动检测 → `logger.warning` 提示 + 跳过）
   - **Tier 7** Google Cache（`webcache.googleusercontent.com`）
   - 全部失败返回 `None` → 由 `reader.py` 回退到 Jina（保持原有行为）
3. **关键辅助函数**（移植自 fetch_url.sh）：`_has_content()`（500+ 字符 + 8+ 行 + 过滤 404/Access Denied）、`_is_paywall_content()`（20+ 种付费墙文案正则）、`_is_captcha_page()`（CAPTCHA/Cloudflare Challenge）、`_match_domain()`（管道分隔域名匹配，兼容 `www.` 前缀和子域名）
4. **HTML 解析双通道**：JSON-LD articleBody 优先（快速路径，`tier*+jsonld` 标记），markdownify 整页兜底（`tier*+markdown` 标记），自动剥离 `<script>/<style>/<nav>/<footer>/<aside>/<form>`
5. **HTTP 客户端复用**：所有请求走 `utils/http_client.py` 的 curl_cffi Chrome TLS 指纹，但每个 Tier 覆盖 UA（Googlebot/Bingbot/Chrome）+ Referer + 自定义 headers + `cookies={}` 清空 cookie 罐子（等价于 `curl -b ""`）
6. **SourceType.WEB + from_web**：付费墙抓到的内容使用新类型，落到 `output/Web/` 独立目录，与其他未分类内容（MANUAL → Manual/）隔离。`author` / `published` / `image` / `strategy` 存 `extra`
7. **reader.py 集成**：`_fetch()` 原行 351-360 的 Jina 兜底改造为 `try_paywall_bypass() → Jina` 二级兜底；异常时 `logger.warning` 后回落 Jina，保证零回归
8. **配置项（7 个）**：`PAYWALL_ENABLED`（默认 true）、`PAYWALL_TIMEOUT`（15s）、`PAYWALL_USE_AMP` / `PAYWALL_USE_ARCHIVE` / `PAYWALL_USE_GOOGLE_CACHE`（均默认 true）、`PAYWALL_DOMAINS_EXTRA`（用户追加自定义域名，管道分隔）、`PAYWALL_JSONLD_FOR_ALL`（默认 true，让 Tier 0 对所有 generic URL 生效）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/utils/jsonld.py` | 新增 | JSON-LD 提取器（~165 行） |
| `feedgrab/fetchers/paywall.py` | 新增 | 7 级付费墙绕过引擎（~530 行） |
| `feedgrab/config.py` | 修改 | +7 个 `paywall_*()` 配置函数 |
| `feedgrab/schema.py` | 修改 | +`SourceType.WEB` + `from_web()` 工厂 |
| `feedgrab/reader.py` | 修改 | `_fetch()` 兜底分支：paywall → Jina 二级兜底，`from_web` 导入 |
| `feedgrab/utils/storage.py` | 修改 | `PLATFORM_FOLDER_MAP[SourceType.WEB] = "Web"` |
| `.env.example` | 修改 | 新增 `=== 付费墙绕过 ===` section |
| `tests/test_paywall.py` | 新增 | 26 条单元测试（JSON-LD 提取 + 内容验证器 + 域名匹配 + 域名列表合法性） |

### 验证结果

- ✅ `python -m pytest tests/test_paywall.py -v` → **26 passed in 0.39s**
- ✅ `python -m pytest tests/ -q` → **29 passed in 0.39s**（含历史 3 条飞书 Sheet 解码回归）
- ✅ 模块 import / SourceType.WEB / from_web 行为正确
- ✅ 7 Tier 级联流程完整跑通（WSJ 用例各 Tier 按序执行，全失败正确返回 None）
- ✅ GitHub 路由回归：`source_type: SourceType.GITHUB`，27842 字符 README 完整提取，未被 paywall 劫持
- ✅ 付费域名识别：`is_paywall_domain(wsj.com)=True`、`is_paywall_domain(example.com)=False`
- ✅ `_has_content` / `_is_paywall_content` / `_is_captcha_page` 边界用例全覆盖
- ✅ 配置项默认值正确读取（`PAYWALL_ENABLED=True` / `TIMEOUT=15` / `JSONLD_FOR_ALL=True`）

### 设计决策

- **新增 `SourceType.WEB` 而非复用 `MANUAL`**：付费墙新闻落到 `output/Web/` 独立目录，与 `Manual/` 隔离，便于 Obsidian 视图索引
- **JSON-LD 提取独立成 `utils/jsonld.py`**：非付费站点也常带 JSON-LD（SEO 标配），未来可给 GitHub README / Medium free / Notion 等复用
- **不移植 Bash 版 fetch_url.sh**：(1) Windows 无 Git Bash 用户不能用；(2) feedgrab 全 Python；(3) curl_cffi 比原生 curl 更难被检测
- **archive.today CAPTCHA 降级策略**：feedgrab 是库不是 CLI 工具，不能 exit 75。检测到 CAPTCHA 时 `logger.warning` 提示用户手动打开 archive URL 验证，流程继续走 Google Cache / Jina
- **域名列表硬编码 + env 追加**：开箱即用 + 用户扩展（`PAYWALL_DOMAINS_EXTRA=foo.com|bar.com`）

### 状态

已完成 ✅

---

## 2026-04-14 · v0.15.2 · 飞书内嵌表格错位修复（懒加载 blocks 合并 + packed slot 解码）

### 背景

用户抓取飞书文档 `https://uz9e9pqslc.feishu.cn/wiki/TviRwSkP5iKr1FkIdwWc7ObbnKc` 后发现多张嵌入电子表格在导出的 Markdown 中出现明显错列：从第二行开始厂商、类型、价格等列整体左滑，导致整张表不可用。联调真实页面后确认问题并非单纯等待时间不足，而是飞书 Sheet 的懒加载数据链路和单元格映射逻辑同时存在缺口。

### 功能内容

1. **懒加载表格预热**：`browser.py` / `feishu_wiki.py` 在提取前对整页执行分段滚动预热，主动触发飞书嵌入 Sheet 的懒加载请求
2. **补抓 `/sheet/block` 数据**：响应拦截器除了 `client_vars` 外，新增捕获 `/space/api/v3/sheet/block` 返回的 blocks，并挂回提取结果
3. **快照 blocks 合并**：`_merge_sheet_snapshot_blocks()` 将懒加载返回的 blocks 与 `client_vars.snapshot.blocks` 合并，确保工作簿级 meta 能找到完整 block payload
4. **packed slot 映射修复**：`_extract_sheet_slot_mapping()` 优先解析 slot blob 中 field 1 的 packed varint 序列，正确恢复重复复用的厂商/类型/价格单元格，解决第二行开始整体错列问题
5. **client_vars 主动补抓增强**：`FEISHU_SHEET_FETCH_JS` 不再强依赖错误的 token 拆分，允许按真实返回的 `token + sheetId` 回填缓存 key
6. **知识库批量同步修复**：`feishu_wiki.py` 复用同一套滚动预热、block 合并和预解码逻辑，避免单篇修好而批量模式继续错位
7. **真实样本回归测试**：新增 `tests/test_feishu_sheet_decode.py`，使用真实 `client_vars + sheet/block` 样本验证高性价比模型表和图像/嵌入表不再错列

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/feishu.py` | 修改 | 新增 packed varint 解码、懒加载 block 合并、sheet cache alias/fallback 优化、表格解码修复 |
| `feedgrab/fetchers/browser.py` | 修改 | 新增 `/sheet/block` 响应拦截、分段滚动预热、表格数据合并透传 |
| `feedgrab/fetchers/feishu_wiki.py` | 修改 | 批量抓取路径同步接入 block 拦截、滚动预热、合并后预解码 |
| `tests/test_feishu_sheet_decode.py` | 新增 | 真实飞书样本回归测试（3 条） |

### 验证结果

- ✅ `python -m pytest tests/test_feishu_sheet_decode.py -q` → `3 passed`
- ✅ 实测重抓目标文档成功：`python -m feedgrab.cli "https://uz9e9pqslc.feishu.cn/wiki/TviRwSkP5iKr1FkIdwWc7ObbnKc"`
- ✅ 导出文件 `E:\Obsidian\Qiang_Obsidian\inbox\Feishu\AI奶爸：XAI教程.md` 中错位表格恢复正常
- ✅ 关键行验证通过：
  - `gpt-4.1-nano | OpenAI | $0.1 入 / $0.4 出 | 极致性价比`
  - `nova-micro | AWS | $0.035 入 / $0.14 出 | 最便宜之一`
  - `gemini-3-pro-image-preview | 图像生成 | Google | $2 入 / $12 出`
  - `whisper-1 | 语音识别 | OpenAI | $0.006/分钟`

### 状态

已完成 ✅

## 2026-04-13 · v0.15.1 · YouTube Whisper 时间戳 + ytb-all 双目录修复

### 背景

用户执行 `feedgrab ytb-all` 后发现两个问题：(1) 同一视频的 4 个文件分散在两个目录（作者名空格不一致）；(2) 无字幕视频的 MD 文件没有带时间轴的转录内容。

### 功能内容

1. **Groq Whisper verbose_json 升级**：`_transcribe_via_whisper()` 从纯文本输出改为 `verbose_json` + `timestamp_granularities[]=segment`，返回 snippets 格式，复用完整的断句+章节+段落分组管线，输出与 Tier 0/1 格式完全一致
2. **大文件分片**：音频 >100MB 时自动 ffmpeg 按 600s 切片（10s overlap），逐片调用 Groq API，时间戳偏移累加 + overlap 去重
3. **yt-dlp 反爬增强**：新增 `_cookies_args()` 默认从 Chrome 提取 cookies 绕过 YouTube bot 检测，Cookie 提取失败（Chrome DB 锁定）自动降级到无 cookies 模式
4. **ytb-all 双目录修复**：`_youtube_resolve_meta()` 的 `safe_author` 增加空格 collapse（`\s+ → " "`），与 `_sanitize_filename()` 行为一致
5. **无字幕提示**：Tier 3 description fallback 时添加 `> **Note**: 本视频无可用字幕` 提示
6. **配置项**：`GROQ_WHISPER_MODEL`（默认 whisper-large-v3）、`YOUTUBE_WHISPER_LANG`（默认 zh）、`YTDLP_COOKIES_BROWSER`（默认 chrome）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/youtube.py` | 修改 | `_transcribe_via_whisper()` 重写 + `_whisper_single()`/`_whisper_chunked()` + `_cookies_args()` + Tier 2 调用适配 + Tier 3 提示 |
| `feedgrab/cli.py` | 修改 | `_youtube_resolve_meta()` safe_author + filename_prefix 空格 collapse |
| `feedgrab/config.py` | 修改 | +`groq_whisper_model()` + `youtube_whisper_lang()` |
| `.env.example` | 修改 | +Whisper 配置项说明 |

### 验证结果

- ✅ 无字幕视频 `nlK7-zuYDcs` Whisper 转录成功（330 segments, 5 chapters, 6585 chars）
- ✅ MD 输出带 `[HH:MM:SS → HH:MM:SS]` 时间戳 + 章节标题
- ✅ `has_transcript: true` 在 front matter 中
- ✅ `_youtube_resolve_meta()` 输出单空格目录名，与 `save_to_markdown` 一致
- ✅ Cookie 提取失败自动降级到无 cookies 模式

### 状态

已完成 ✅

---

## 2026-04-11 · v0.15.0 · 知乎 (Zhihu) 平台支持

### 背景

新增知乎平台抓取能力，支持问答页面（前 3 楼回答）和专栏文章的单篇抓取，以及关键词搜索（`zhihu-so` 命令）。

### 功能内容

1. **单篇问答抓取**：`feedgrab https://www.zhihu.com/question/{qid}/answer/{aid}`，默认抓取前 3 楼回答
2. **单篇专栏文章**：`feedgrab https://zhuanlan.zhihu.com/p/{pid}`
3. **三级兜底策略**：API v4 + Cookie → Playwright CDP/Launch + DOM 提取 → Jina Reader
4. **前 3 楼多回答格式**：横线分隔 `[1/3楼]`、`[2/3楼]`、`[3/3楼]`，每楼正文开头输出互动数据行
5. **完整互动数据**：赞同（voteup_count）、评论（comment_count）、收藏（favlists_count）、喜欢（thanks_count）
6. **front matter 元数据**：取前 3 楼中最高赞回答的互动数据写入元数据
7. **关键词搜索**：`feedgrab zhihu-so "关键词"` — API/Playwright 双层兜底，按赞数降序汇总表格（MD + CSV）
8. **多关键词批量**：逗号分隔 `feedgrab zhihu-so "k1,k2"` + `--merge` 合并模式
9. **CDP 直连**：`ZHIHU_CDP_ENABLED=true` 复用已打开的 Chrome 抓取知乎
10. **登录管理**：`feedgrab login zhihu` 保存 session + CDP Cookie 提取

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/zhihu.py` | 新增 | 单篇抓取（API v4 → Playwright DOM → Jina 三级兜底 + 前 3 楼多回答） |
| `feedgrab/fetchers/zhihu_search.py` | 新增 | 关键词搜索（API/Playwright 双层 + 汇总表格 + CSV） |
| `feedgrab/schema.py` | 修改 | +SourceType.ZHIHU + from_zhihu() |
| `feedgrab/config.py` | 修改 | +7 个 ZHIHU_* 配置函数 |
| `feedgrab/reader.py` | 修改 | +知乎平台检测 + 路由 |
| `feedgrab/cli.py` | 修改 | +zhihu-so 命令 + cmd_zhihu_search() + 帮助文本 |
| `feedgrab/login.py` | 修改 | +知乎平台注册（PLATFORM_URLS + CDP 域名） |
| `feedgrab/utils/storage.py` | 修改 | +PLATFORM_FOLDER_MAP + front matter + 多楼 body 格式 |
| `.env.example` | 修改 | +知乎配置段 |

### 验证结果

- ✅ 单篇问答（Playwright CDP DOM 提取，前 3 楼互动数据完整）
- ✅ 单篇专栏文章（Playwright DOM 提取，图片链接完整）
- ✅ zhihu-so 命令注册正常
- ✅ feedgrab login zhihu 平台注册正常
- ✅ 输出 Markdown 格式正确（front matter + 多楼分隔 + 互动数据行）

### 状态

已完成 ✅

---

## 2026-04-09 · v0.14.1 · 有道云笔记 (Youdao Note) 平台支持

### 背景

新增有道云笔记（note.youdao.com）分享页面抓取支持。有道云笔记有直接的 JSON API（无需登录），图片使用直接 CDN URL（非 blob:），无虚拟滚动，实现复杂度低于 KDocs/飞书。

### 功能内容

1. **JSON API 直接获取**（Tier 0）：`/yws/api/note/{shareKey}` 零依赖获取笔记内容（<300ms），解析压缩 JSON 格式（数字键编码：`"5"` 子块、`"6"` 特殊标记、`"7"` 内联元素、`"8"` 文本、`"9"` 样式）
2. **Playwright iframe DOM 提取**（Tier 1）：API 失败时从 `iframe#content-body` 内 `.bulb-editor` 容器提取内容
3. **Jina Reader 兜底**（Tier 2）：最终兜底
4. **URL 参数自动清理**：`clean_youdao_url()` 只保留 `id` 参数，去掉 `type`/`_time` 等无用尾巴
5. **标题智能检测**：通过 font-size 样式推断标题级别（≥24=H1, ≥20=H2, ≥16=H3）+ bold 样式确认
6. **链接完整提取**：type=3 链接块的 `hf` 属性渲染为 `[text](url)` Markdown 链接
7. **列表支持**：有序/无序列表 + 嵌套级别（`lt`=列表类型, `ll`=列表级别）
8. **图片下载开关**：`YOUDAO_DOWNLOAD_IMAGES=true` 将图片下载到 `attachments/{item_id}/` 子目录
9. **代码块 4 反引号**：最外层用 4 个反引号包裹（防嵌套），内容含 4+ 个连续反引号时 fence+1
10. **内联样式**：bold/italic/strikethrough/underline → Markdown 标记

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/youdao.py` | 新增 | 核心 fetcher（~430 行），JSON API 解析 + Playwright 兜底 + 图片下载 |
| `feedgrab/schema.py` | 修改 | `SourceType.YOUDAO` 枚举 + `from_youdao()` 转换函数 |
| `feedgrab/reader.py` | 修改 | 平台检测 + URL 清理 + 路由 + 图片下载触发 + 去重映射 |
| `feedgrab/config.py` | 修改 | `youdao_download_images()` 配置函数 |
| `feedgrab/utils/storage.py` | 修改 | `NoteYouDao` 目录映射 + front matter + 标题去重 |
| `.env` / `.env.example` | 修改 | `YOUDAO_DOWNLOAD_IMAGES` 配置项 |
| `CLAUDE.md` | 修改 | 平台表格 + 架构文件树 + 设计决策 + 迭代历史 |
| `README.md` / `README_EN.md` | 修改 | 平台数量 8+ → 11 + 有道云笔记平台行 |

### 验证结果

- ✅ URL 参数自动清理（`type`/`_time` 去除，只保留 `id`）
- ✅ API Tier 0 命中（<300ms），标题/正文/图片完整
- ✅ 输出到 `{OUTPUT_DIR}/NoteYouDao/` 目录
- ✅ 9 张图片 CDN URL 正确提取
- ✅ 14 个链接完整渲染
- ✅ 6 个 H1 标题 + 23 个 H3 标题正确检测
- ✅ 26 个列表项（含 8 个嵌套）正确渲染
- ✅ front matter 包含 share_key/page_views/edit_time

### 状态

已完成 ✅

---

## 2026-04-07 · v0.14.0 · 金山文档 (KDocs) 平台支持

### 背景

新增金山文档（kdocs.cn）平台抓取支持。金山文档基于 WPS WebOffice SPA + ProseMirror 编辑器，采用虚拟滚动渲染，图片使用 blob: URL，需要针对性解决内容完整提取、图片 URL 解析、代码块识别三个技术难点。

### 功能内容

1. **Playwright ProseMirror DOM 提取**（Tier 0）：滚动 `.otl-scroll-container` 逐屏提取 `.block_tile` 内容，自动去重，支持标题/段落/列表/待办/分割线/代码块/图片 7 种块类型
2. **虚拟滚动对抗**：每次滚动 600px + 200ms 等待 + 提取可见 DOM，去重 key 基于块类型+文本前 80 字符，hr 用序号去重防止坍缩
3. **图片 shapes API 解析**：拦截 `attachment/shapes` 网络响应，获取 `sourcekey → CDN URL` 映射，结合 DOM `<img sourcekey>` 属性将 blob: URL 替换为真实 CDN URL
4. **代码块提取**：识别 `<nodeview data-node-type="code_block">` → `<pre lang>` → `<code class="code-block-content">` 结构，保留语言标记
5. **CDP 直连模式**：`KDOCS_CDP_ENABLED=true` 复用已打开的 Chrome 抓取需要登录的金山文档（匹配 `.kdocs.cn`/`.wps.cn` cookie），失败自动降级到 Launch 模式
6. **图片下载开关**：`KDOCS_DOWNLOAD_IMAGES=true` 将图片下载到 `attachments/{item_id}/` 子目录，Markdown 使用相对路径（默认关闭，保留 CDN 链接）
7. **Jina Reader 兜底**（Tier 1）：Playwright 失败时自动降级
8. **登录支持**：`feedgrab login kdocs` 保存 session 到 `sessions/kdocs.json`
9. **重复分割线修复**：虚拟滚动提取时空 block_tile 会产生大量重复 `---`，正则合并 + 清理多余空行
10. **CDP 模式 viewport 修复**：CDP 页面强制设置 1920x1080 viewport + 滚动前先 scrollTop=0 + 滚动容器就绪检查，确保与 Launch 模式一致的 347 blocks 提取量

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/kdocs.py` | 新增 | 核心 fetcher（660 行），ProseMirror DOM 提取 + shapes API 图片解析 + CDP 直连 + 图片下载 |
| `feedgrab/schema.py` | 修改 | `SourceType.KDOCS` 枚举 + `from_kdocs()` 转换函数（含 `images_info`/`img_subdir`） |
| `feedgrab/reader.py` | 修改 | `_detect_platform()` kdocs 路由 + `_fetch()` kdocs 分支 + 图片下载触发 |
| `feedgrab/config.py` | 修改 | `kdocs_cdp_enabled()` + `kdocs_page_load_timeout()` + `kdocs_download_images()` |
| `feedgrab/login.py` | 修改 | kdocs 平台 URL + CDP cookie 域名配置 |
| `feedgrab/utils/storage.py` | 修改 | `SourceType.KDOCS: "KDocs"` 目录映射 |
| `.env.example` | 修改 | 金山文档配置说明（CDP/超时/图片下载） |

### 验证结果

- ✅ 公开文档 `https://www.kdocs.cn/l/cagyDv2WRhvP` 提取 347 blocks（标题/段落/列表/代码块/分割线/图片完整）
- ✅ 4 张图片通过 shapes API 正确解析为 CDN URL（`weboffice-temporary.ks3-cn-beijing.wpscdn.cn`）
- ✅ 代码块正确提取（含语言标记如 JSON）
- ✅ 重复分割线已合并（24 个合理分割线，无重复）
- ✅ CDP 模式提取与 Launch 模式一致（347 blocks）
- ✅ `KDOCS_DOWNLOAD_IMAGES=true` 图片信息正确收集（4 张，相对路径 `attachments/{item_id}/xxx`）
- ✅ `__WPSENV__` 元数据正确提取（标题/作者/创建时间/修改时间）

### 状态

已完成 ✅

---

## 2026-04-05 · v0.13.7 · x-so 搜索三级兜底 + SearchTimeline POST 迁移 + ondemand.s 容错增强

### 背景
Twitter 前端频繁变更 `ondemand.s` 文件路径，`xclienttransaction` 库 1.0.1 的正则无法匹配新路径，导致 `x-client-transaction-id` 生成失败，SearchTimeline GraphQL 返回 404。升级到 1.0.2 修复了当前问题，但需要防御未来再次破裂。

### 修复内容

**1. ondemand.s 容错增强（`twitter_graphql.py`）：**
- ondemand.s HTTP 请求添加 try/except（此前超时直接崩溃）
- 空 ondemand_text 不写入磁盘缓存（防止缓存污染 1 小时）
- 日志从 DEBUG 提升为 WARNING，提示用户升级库

**2. x-so 浏览器搜索三级兜底（`twitter_keyword_search.py`）：**
- Tier 0: GraphQL SearchTimeline（现有方案，<2s/页）
- Tier 1: CDP 直连（复用已打开 Chrome，秒级启动，零浏览器开销）
- Tier 2: Playwright launch（隐身浏览器 + session 预热）
- GraphQL 失败时自动降级，数据格式完全兼容（同一 `extract_tweet_data()`）
- 复用 `SearchResponseCollector` + `_scroll_and_collect_search`（来自 `twitter_search_tweets.py`）
- CDP 连接模式仿 `_connect_feishu_cdp()`，Cookie 域名匹配 `.x.com`/`.twitter.com`

**3. SearchTimeline GET → POST 迁移（`twitter_graphql.py`）：**
- Twitter 已将 SearchTimeline 端点从 GET 迁移到 POST（参考 twitter-cli 实现）
- `_execute_graphql()` 新增 `use_post` 参数，POST 时参数移到 JSON body
- POST body 额外包含 `queryId` 字段，features 完整传递（无需紧凑编码）
- `x-client-transaction-id` 签名传入 `method="POST"`
- 其他端点（TweetDetail/UserTweets 等）保持 GET 不变

**4. 配置项：**
- `X_SEARCH_BROWSER_FALLBACK`（默认 true）— 控制是否启用浏览器兜底

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修复 | ondemand.s 异常处理 + 缓存污染防护 + WARNING 日志 |
| `feedgrab/fetchers/twitter_keyword_search.py` | 功能 | 新增 `_connect_twitter_cdp_for_search()` + `_launch_browser_for_search()` + `_search_via_browser()` + `search_twitter_keyword()` 改为 async |
| `feedgrab/config.py` | 功能 | 新增 `x_search_browser_fallback()` |
| `feedgrab/cli.py` | 适配 | `asyncio.run()` 包装 async 调用 |
| `.env.example` | 文档 | 新增 `X_SEARCH_BROWSER_FALLBACK` 说明 |

### 验证结果
- GraphQL 正常路径：`feedgrab x-so openclaw --days 2` → 20 条推文，不触发浏览器
- 模拟 GraphQL 失败：monkey-patch 返回 None → 自动降级 CDP → 10 条推文
- CDP 直连：Chrome 开启 `--remote-debugging-port=9222` → 拦截 SearchTimeline 响应成功

## 2026-04-03 · v0.13.6 · Article 长文超链接完整保存

### 背景
Twitter Article 长文使用 Draft.js `content_state` 格式存储。`_render_article_body()` 只处理了 atomic 块中的 MEDIA/MARKDOWN 实体，非 atomic 块（段落、标题、列表等）的 `entityRanges` 完全被跳过，导致正文中的超链接（网站链接、@mention 链接等）丢失。

### 修复内容

**GraphQL 版 `_render_article_body()`（`twitter_graphql.py`）：**
- 新增 `_apply_article_inline()` 函数，处理非 atomic 块的 entityRanges + inlineStyleRanges
- 不区分实体类型（LINK/MENTION/等），从 `data` 中提取 `url`/`href`/`value`，有 URL 就渲染为 `[text](url)`
- 同时补充了 inlineStyleRanges 处理（Bold/Italic），此前 GraphQL 版缺失
- 偏移量修正：链接插入后重新计算 style 偏移位置，避免错位

**FxTwitter 版 `_render_article_body()`（`twitter_fxtwitter.py`）：**
- 新增 `_apply_fxtwitter_inline()` 函数，逻辑与 GraphQL 版对齐
- 补充 entityMap 构建（从 `content.entityMap` 读取）
- atomic 块也支持 LINK 实体渲染

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修复 | 新增 `_apply_article_inline()`，非 atomic 块调用处理 entityRanges + inlineStyleRanges |
| `feedgrab/fetchers/twitter_fxtwitter.py` | 修复 | 新增 `_apply_fxtwitter_inline()` + entityMap 构建 + atomic LINK 渲染 |

### 验证结果
- 语法检查通过 ✅
- 模块导入验证通过 ✅
- 实际推文抓取测试通过（Article 长文 6 个超链接全部正确渲染）✅

### 状态：已完成 ✅

---

## 2026-04-01 · v0.13.5 · GraphQL 初始化热路径优化 + 日志精简

### 背景
v0.13.4 中 `_get_transaction_id()` 存在缓存失效 bug：当 `ondemand.s` 解析失败时，`_transaction_generator` 保持 None，缓存条件 `is None or TTL过期` 中 `is None` 短路导致 TTL 保护失效。单篇推文抓取的 3 次 GraphQL 请求各自重新初始化（重新 HTTP 获取 x.com 首页），产生 33 行日志（6 条 WARNING + 25 条 DEBUG），给用户造成恐慌。

### 修复内容

**Bug 1 — 缓存条件逻辑错误（核心）：**
- 根因：`_transaction_generator is None or TTL过期`，None 检查短路使 TTL 保护失效
- 修复：条件改为 `_transaction_generator_timestamp == 0 or TTL过期`，以时间戳为主判断
- 异常路径也设置时间戳，防止 `except Exception` 导致无限重试
- 使用生成器前增加 None 保护（`if _transaction_generator is None: return ""`）

**Bug 2 — Homepage 不共享缓存：**
- 根因：`_get_transaction_id()` 直接 `http_client.get("https://x.com")` 而不使用 `_fetch_home_html()` 内存缓存
- 修复：改用 `_fetch_home_html(ua)` 共享 `_cached_home_html`，同一进程只 HTTP 一次

**Bug 3 — Feature flag 重复日志：**
- 根因：7 个 feature dict 有大量重叠 key，同一 flag 在多个 dict 中各记一次 DEBUG
- 修复：`unique_keys` 集合去重，逐条 DEBUG 全部删除，只保留一条 INFO 汇总

**Bug 4 — 默认日志级别过低：**
- 根因：loguru 默认 handler 级别 DEBUG（level=10），所有 DEBUG 日志直接输出到终端
- 修复：CLI 入口设置默认级别 INFO，`FEEDGRAB_LOG_LEVEL=DEBUG` 可选开启

**优化 — Cookie 重复加载：**
- `fetch_twitter()` 加载 Cookie 后传递给 `_fetch_via_graphql()`，避免后者再次加载

**优化 — WARNING 降级：**
- `ondemand.s` 相关 WARNING 降为 DEBUG（TweetDetail 不需要 transaction-id）
- 多条重复 DEBUG 合并为一条

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修复 | 缓存条件修复 + homepage 共享缓存 + feature flag 去重 + WARNING→DEBUG + 冗余日志删除 |
| `feedgrab/fetchers/twitter.py` | 优化 | Cookie 传递避免重复加载（`_fetch_via_graphql` 新增 `cookies` 参数） |
| `feedgrab/cli.py` | 新增 | 默认日志级别 INFO（`FEEDGRAB_LOG_LEVEL` 环境变量） |
| `.env.example` | 新增 | `FEEDGRAB_LOG_LEVEL` 配置说明 |
| `README.md` | 更新 | 配置表新增日志级别条目 |

### 效果对比

| 指标 | v0.13.4 | v0.13.5 |
|------|---------|---------|
| 总日志行数 | 33 行 | 8 行 |
| WARNING | 6 条 | 0 条 |
| DEBUG 噪音 | 25 条 | 0 条（INFO 级别不输出） |
| Cookie 加载 | 2 次 | 1 次 |
| Homepage HTTP | 3 次 | 1 次 |
| Feature flag 明细 | 15-32 条 | 1 条汇总 |

### 验证结果
- 语法检查通过 ✅
- 模块导入验证通过 ✅
- 实际推文抓取测试通过（功能无影响）✅
- 日志输出精简至 8 行关键节点 ✅

### 状态：已完成 ✅

---

## 2026-03-28 · v0.13.4 · XClientTransaction 错误修复 + GraphQL 401 CDP 刷新引导

### 背景
用户报告两个问题：(1) XClientTransaction 生成 `x-client-transaction-id` 时 `get_ondemand_file_url` 返回 None 导致 `'NoneType' object has no attribute 'split'` 错误；(2) GraphQL 401 错误时缺乏引导，用户不知道需要通过 CDP 刷新 Cookie。

### 修复内容

**Bug 1 — XClientTransaction None 错误：**
- 根因：`get_ondemand_file_url()` 当 X 网站结构变化时返回 None，直接作为 URL 传递给 `http_client.get()`
- 修复：`_get_transaction_id()` 添加 None 检查，如果 ondemand_url 为 None 则跳过 ondemand.s 获取，使用空字符串初始化
- 缓存：`_load_transaction_cache()` 增加空值检查，避免缓存无效的 ondemand_text
- 优雅降级：当 ondemand_text 为空时，跳过 Transaction ID 生成（SearchTimeline 可能返回 404，但其他端点仍可工作）

**Bug 2 — GraphQL 401 Cookie 刷新引导：**
- 新增 `_prompt_cookie_refresh_via_cdp()` 函数，当检测到 401/403 错误时：
  - 检测 CDP 是否可用（检查 127.0.0.1:{port}/json/version）
  - CDP 不可用时：输出手动刷新 Cookie 的指引
  - CDP 可用时：交互式询问是否自动获取新 Cookie
  - 成功刷新后：自动重试当前请求
- 新增 `_build_cookie_header()` 辅助函数，将 cookie dict 转换为 header 字符串
- 冷却机制：`_COOKIE_PROMPT_COOLDOWN = 60` 秒，避免短时间内重复提示

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修复 | `_get_transaction_id()` None 检查；`_load_transaction_cache()` 空值过滤；新增 `_prompt_cookie_refresh_via_cdp()` + `_build_cookie_header()`；`_execute_graphql()` 401/403 时调用刷新引导 |

### 验证结果
- 语法检查通过 ✅
- 模块导入验证通过 ✅

### 状态：已完成 ✅

---

## 2026-03-27 · v0.13.3 · 小红书 Pinia Store 注入兜底层

### 背景
feedgrab 小红书抓取依赖 `xhshow` 签名库生成 `x-s`/`x-s-common`/`x-t` 反爬头。当小红书更新签名算法时，xhshow 会失效（461/471 验证码），需等待库更新。调研 bb-browser 发现更可靠方式：**Pinia Store 注入** — 在小红书页面上下文中直接调用 Vue Store Action，触发前端原生请求，签名/Cookie/TLS 全部天然真实。

### 方案决策

新增 Tier 0.5 Pinia 兜底层，插入 xhshow API 和 Jina 之间：

```
Tier 0    xhshow API 签名（纯 HTTP，最快，~0.5s）     ← 现有
Tier 0.5  Pinia Store 注入（浏览器原生请求，~1-2s）   ← 新增
Tier 1    Jina Reader                                  ← 现有
Tier 2    Playwright DOM 解析                          ← 现有
```

核心技术（双通道模式，源自 bb-sites）：
1. Monkey-patch `XMLHttpRequest.prototype.open/send` 设置拦截器
2. 调用 Pinia Store Action（`noteStore.getNoteDetailByNoteId(id)`）触发前端原生 XHR
3. 拦截器捕获原始 JSON 响应（避免 Vue Proxy 包装）
4. `finally` 块恢复原始 XHR 方法

Pinia 访问路径：`document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('storeName')`

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/xhs_pinia.py` | 新增 | Pinia 核心模块（4 个 JS 片段 + CDP/Launch 浏览器管理 + 3 个公共 API） |
| `feedgrab/fetchers/xhs.py` | 修改 | 单篇 Tier 0.5 插入（`pinia_feed_note` 兜底） |
| `feedgrab/fetchers/xhs_search_notes.py` | 修改 | 搜索批量 Pinia 兜底 + `_process_pinia_search_results()` + `xhs-so` Pinia 兜底 |
| `feedgrab/fetchers/xhs_user_notes.py` | 修改 | 用户笔记批量 Pinia 兜底 + `_process_pinia_user_results()` |
| `feedgrab/config.py` | 新增 | `xhs_pinia_enabled()` 配置函数 |
| `.env.example` | 修改 | Pinia 配置说明 |

### 覆盖的入口

- **单篇**（`fetch_xhs()`）：API 失败 → Pinia feed_note → Jina → Playwright
- **搜索批量**（`fetch_search_notes()`）：API 搜索失败 → Pinia 搜索 → 浏览器模式
- **用户笔记批量**（`fetch_user_notes()`）：API 列表失败 → Pinia 用户笔记 → 浏览器模式
- **xhs-so 命令**（`search_xhs_keyword()`）：API 不可用 → Pinia 搜索 → 报错

### 降级逻辑

```
XHS_PINIA_ENABLED=false → 跳过 Pinia
无 sessions/xhs.json 且 CDP 不可用 → 跳过 Pinia
CDP 连接失败 → 尝试 Launch
Pinia 不可用（__vue_app__ 未就绪）→ 跳过 Pinia
Store Action 执行失败 → 跳过 Pinia
所有失败静默降级到下一 Tier
```

### 验证结果
- 语法检查：4 个文件全部通过 ✅
- 模块导入链：`xhs_pinia` → `xhs` → `xhs_search_notes` → `xhs_user_notes` 全部 OK ✅
- 配置函数：`xhs_pinia_enabled()` 默认 true ✅

### 状态：已完成 ✅

---

## 2026-03-27 · v0.13.2 · 飞书 CDP 直连（复用已打开的 Chrome 抓取飞书文档）

### 背景
feedgrab 飞书抓取的 Tier 2（Playwright Launch）每次调用 `evaluate_feishu_doc()` 都启动新 Chrome 实例，耗时数秒、内存 200-500MB。调研 bb-browser 和 opencli 后发现核心高价值点：CDP 直连用户已运行的 Chrome。feedgrab 已有 `login.py` 中的 CDP 连接能力（`connect_over_cdp`），扩展到飞书抓取成本很低。

### 方案决策

新增 Tier 1 CDP 直连，插入 Open API 和 Launch 之间：

```
Tier 0    Open API（需 APP_ID/SECRET）
Tier 1    CDP 直连（需 Chrome --remote-debugging-port + 飞书已登录）  ← 新增
Tier 2    Launch 新浏览器（需 sessions/feishu.json）
Tier 3    内部导出 API
Tier 4    Jina
```

核心设计决策：
- **Context 复用**：复用用户已有 context（新建 page/tab），不创建新 context。用户 context 含完整 localStorage/sessionStorage，飞书 SPA 重度依赖
- **代码重构**：抽取 `_evaluate_feishu_doc_on_page(url, page, skip_warmup)` 公共函数，CDP/launch 双路径共享同一套提取逻辑
- **CDP 内嵌降级**：`evaluate_feishu_doc()` 内部先试 CDP 再 launch，`feishu.py` 几乎零改动
- **配置**：新增 `FEISHU_CDP_ENABLED`（默认 false），复用 `CHROME_CDP_PORT`（默认 9222）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/config.py` | 新增 | `feishu_cdp_enabled()` 函数 |
| `feedgrab/fetchers/browser.py` | 重构 | 新增 `_connect_feishu_cdp()` + 抽取 `_evaluate_feishu_doc_on_page()` + `evaluate_feishu_doc()` CDP→Launch 降级 |
| `feedgrab/fetchers/feishu.py` | 修改 | Tier 路由更新（0→0, 0.5→1, 1→2, 1.5→3, 2→4）+ session 检查放宽（CDP 模式不强制要求 session） |
| `feedgrab/fetchers/feishu_wiki.py` | 修改 | 批量模式 CDP 优先（`_connect_feishu_cdp` → launch 兜底）+ skip_warmup |
| `.env.example` | 新增 | `FEISHU_CDP_ENABLED` 配置说明 |

### 降级逻辑

```
FEISHU_CDP_ENABLED=false → 跳过 CDP
Chrome 未开 debug port → ws 连接失败 → 回退 launch
无飞书 cookie 的 context → 回退 launch
JS evaluate 失败 → 回退 launch
```

### 验证结果
- CDP 连接成功：84 个飞书 cookie 发现 ✅
- 完整内容提取：3776 行输出（title/author/blockTree/sheet/images）✅
- Chrome 新 tab 打开后自动关闭 ✅
- 降级测试：错误端口 9999 → `ECONNREFUSED` → 无缝回退 launch ✅
- 模块导入验证：全部 OK ✅

### 状态：已完成 ✅

---

## 2026-03-27 · v0.13.1 · 飞书 Block→Markdown 三项修复（代码块嵌套 + 加粗保留 + 目录生成）

### 背景
飞书文档抓取后发现三个 Markdown 渲染问题：(1) 代码块内含 ``` 导致外层代码块提前闭合，正文被吞入代码块；(2) Playwright Block 树中的加粗标记丢失；(3) ISV 目录组件未渲染。

### 修复内容

| 问题 | 根因 | 修复 |
|------|------|------|
| 代码块嵌套断裂 | 固定 ``` 围栏无法包含内含 ``` 的内容 | `_block_to_md()` 检测内容中最长反引号序列，使用 CommonMark 可变长度围栏（N+1 个反引号） |
| 加粗格式丢失 | `_get_elements()` 仅支持 SDK `elements` 格式，不识别 Playwright Delta ops | 新增 `zoneState.content.ops` 提取路径（`[{insert, attributes: {bold}}]`），复用已有 `_apply_inline_style()` |
| 目录（TOC）为空 | ISV block 类型未处理 + 无标题扫描机制 | `_collect_headings()` 预扫描全文标题 → `_render_isv_block()` 读取 `showCataLogLevel` 生成缩进列表 |

### 技术细节

- **可变长度代码围栏**：`re.finditer(r"`+", text)` 找最长反引号序列，若 ≥3 则用 `longest+1` 个反引号做围栏
- **Delta ops 提取**：Playwright 的 block 树中富文本存储在 `zoneState.content.ops`（Delta 格式），与 SDK 的 `block["text"]["elements"]` 不同，但下游 `_apply_inline_style()` 兼容两种格式
- **TOC 递归保护**：新增 `_is_root` 参数防止 `_render_children()` → `blocks_to_markdown()` 递归调用时重新清空标题缓存（callout/quote 等容器 block 传入 `depth=0` 会触发此问题）
- **ISV block 识别**：飞书第三方组件统一为 `isv` 类型，通过 `snapshot.data.showCataLogLevel` 字段区分目录组件

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/feishu.py` | 修复 | `_block_to_md()` 可变长度围栏；`_get_elements()` Delta ops 路径；`_collect_headings()` + `_render_isv_block()` TOC 生成；`blocks_to_markdown()` + `_render_children()` + `_render_table()` `_is_root` 递归保护 |

### 验证结果
- 代码块：内含 ``` 的代码块使用 ````` 围栏正确包裹 ✅
- 加粗：`**转型的勇气来自于对未来的清晰认知**` 保留在输出中 ✅
- 目录：35 个标题按 `showCataLogLevel=3` 过滤生成缩进列表 ✅

### 状态：已完成 ✅

---

## 2026-03-23 · v0.13.0 · YouTube InnerTube API + 智能断句 + 章节解析

### 背景
feedgrab 的 YouTube 字幕抓取完全依赖 yt-dlp subprocess，存在三个问题：(1) 需要安装 yt-dlp + ffmpeg 二进制依赖；(2) 字幕输出是一整段无结构文本（丢弃时间戳、无断句、无分段）；(3) 不支持章节分割。参考 baoyu-youtube-transcript（@dotey 宝玉）的 InnerTube API 方案，融合为新 Tier 0 层。

### 方案决策

**InnerTube API（Tier 0）**：调用 YouTube 内部 `youtubei/v1/player` 端点获取字幕，零外部依赖零 API quota。使用 ANDROID 客户端身份绕过部分限制。字幕 XML `<text start="" dur="">` 解析为结构化 snippet。双重 HTML 实体解码（`&amp;#39;` → `'`）。EU consent 页面自动处理。`fmt=srv3` 参数剥离获取默认 XML 格式。

**智能断句**：三阶段管线——按句尾标点拆分（`.?!。？！…` 等）→ 跨 snippet 合并为完整句子（CJK 字符无空格拼接，拉丁字符加空格）→ 段落分组（≤5句/段，>2s 间隔强制分段）。时间戳按字符长度比例分配。**无标点兜底**：自动字幕标点率 <10% 时跳过断句，直接 snippet 级分组，避免整段文本无结构。

**章节解析**：从 description 正则匹配 `HH:MM:SS 标题` 格式，≥2 个章节才生效。按章节时间范围分割句子，输出 `## 章节标题 [M:SS]` + 带时间戳段落。

### 降级链

```
Tier 0   InnerTube API（零依赖零 quota）+ 智能断句 + 章节  ← 新增
Tier 1   yt-dlp 字幕 + 智能断句                           ← 升级
Tier 2   Groq Whisper 转录                                ← 不变
Tier 3   API description / Jina 兜底                       ← 不变
```

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/youtube.py` | 修改 | 新增 `_fetch_innertube_transcript()`（InnerTube API）；`_segment_into_sentences()` + `_group_into_paragraphs()`（断句管线）；`_parse_chapters()` + `_format_transcript_markdown()`（章节 + 格式化）；`_parse_srt_to_snippets()` 替代原 `_parse_srt()`（返回结构化数据）；`fetch_youtube()` 插入 Tier 0 + 统一输出管线；`_extract_video_id()` 增加 `/shorts/` URL 支持 |

### 验证结果
- TED 演讲（8jPQjjsBbIc）：260 snippets → 117 自然句子，1.7s 完成 ✅
- 影视飓风（bzfBdivz_KQ）：425 snippets，中文自动字幕无标点 → snippet 级分组 ✅
- Rick Astley（dQw4w9WgXcQ）：61 snippets，歌词正确处理 ✅
- JavaScript Pro Tips（Mus_vwhTCq0）：374 snippets，无标点 → fallback 分组 ✅

### 状态：已完成 ✅

---

## 2026-03-22 · v0.12.5 · Claude Code Skill 发布（5 个技能，支持 `npx skills add` 一键安装）

### 背景
将 feedgrab 制作成可通过 `npx skills add iBigQiang/feedgrab` 一键安装的 Claude Code 技能集合，让其他用户能在 Claude Code 中直接使用 feedgrab 的内容抓取能力。基于 [Vercel Labs skills](https://github.com/vercel-labs/skills) 开放标准，遵循 `SKILL.md` frontmatter 规范。

### 技能列表

| 技能 | 命令 | 说明 |
|------|------|------|
| `feedgrab` | `/feedgrab <URL>` | 核心抓取 — 给 URL 返回结构化 Markdown，支持 11 平台 |
| `feedgrab-batch` | `/feedgrab-batch` | 批量抓取 — 书签、用户推文、搜索、微信批量、飞书知识库等 |
| `feedgrab-setup` | `/feedgrab-setup` | 安装引导 — pip install + setup + Cookie 配置 + doctor 诊断 |
| `analyzer` | `/analyze <URL>` | 内容分析 — 多维度结构化分析报告 |
| `video` | 自动触发 | 视频转录 — yt-dlp 字幕 + Groq Whisper 转录 + 结构化摘要 |

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `skills/feedgrab/SKILL.md` | 新建 | 核心抓取技能（平台路由表 + 安装检测 + 错误处理） |
| `skills/feedgrab-batch/SKILL.md` | 新建 | 批量抓取技能（命令映射 + 环境变量速查 + 断点续传） |
| `skills/feedgrab-setup/SKILL.md` | 新建 | 安装引导技能（5 步交互 + doctor 验证） |
| `skills/analyzer/SKILL.md` | 重命名+升级 | skill.md → SKILL.md + YAML frontmatter |
| `skills/video/SKILL.md` | 重命名+升级 | skill.md → SKILL.md + YAML frontmatter + 精简注释 |

### 安装方式

```bash
npx skills add iBigQiang/feedgrab
```

### 状态：已完成 ✅

---

## 2026-03-21 · v0.12.4 · GitHub 中文 README 检测增强（HTML `<a>` 标签 + 直接文件 URL + 翻译声明跳过）

### 背景
v0.12.3 修复了从 README 内容中查找子目录中文文档的功能，但仅支持 Markdown `[中文](url)` 链接格式。实际上很多仓库（如 `thedotmack/claude-mem`）使用 HTML `<a href="docs/i18n/README.zh.md">🇨🇳 中文</a>` 格式，且链接文本含 emoji 前缀。同时，直接给定中文 README 的 `/blob/main/path/to/file.md` URL 时，`parse_github_url()` 丢弃文件路径，退化为抓取英文 README。

### 方案决策

**Bug 1 — HTML `<a>` 标签 + emoji 前缀链接不识别**：
- `_find_chinese_readme_from_content()` 原有 6 个正则模式仅匹配 `[*{0,2}中文*{0,2}](url)` 格式
- 重构为两类模式：Markdown `[any prefix 中文 any suffix](url)` + HTML `<a href="url">...中文...</a>`
- 6 个关键词（中文/简体中文/繁體中文/Chinese/ZH-CN/zh-CN）构建正则交替组
- 允许链接文本中任意前缀/后缀（emoji、空格、bold/italic 等）

**Bug 2 — 直接文件 URL 路径被丢弃**：
- `parse_github_url()` 返回值从 `(owner, repo)` 扩展为 `(owner, repo, file_path)`
- 从 `/blob/{branch}/path/to/file` URL 提取 file_path（仅当最后一段含 `.` 扩展名时）
- `fetch_github()` 新增 Step 1b：有 specific_file 时直接获取，跳过中文 README 搜索

**优化 — 翻译声明跳过**：
- `_extract_readme_summary()` 新增过滤：匹配 `auto-translat/machine-translat/自动翻译/机器翻译` 的行不作为标题
- 避免 `🌐 这是自动翻译。欢迎社区修正!` 成为文件名

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/github.py` | 修改 | `parse_github_url()` 返回 3 元组（+file_path）；`_find_chinese_readme_from_content()` 重构正则（Markdown+HTML 双格式）；`_extract_readme_summary()` 跳过翻译声明行；`fetch_github()` 新增 Step 1b 直接文件抓取 |

### 验证结果
- `thedotmack/claude-mem`（HTML `<a>` 格式）：首页 URL → 成功检测 `docs/i18n/README.zh.md` ✅
- `thedotmack/claude-mem/blob/main/docs/i18n/README.zh.md`（直接文件 URL）→ 直接抓取中文版 ✅
- `saicaca/fuwari`（Markdown `[**中文**](url)` 格式）→ 回归测试通过 ✅
- 标题提取：跳过翻译声明，正确提取项目描述 ✅

### 状态：已完成 ✅

---

## 2026-03-19 · v0.12.3 · 三项 Bug 修复（微信短链 + GitHub 中文 README + 图片链接 + clip 命令）

### 背景
三个用户报告的 Bug：(1) 微信短链带追踪参数（`?scene=1&click_id=3`）时 PowerShell `&` 解析报错；(2) GitHub 仓库中文 README 在非根目录（如 `docs/README.zh-CN.md`）时找不到；(3) GitHub README 中相对路径图片在本地 Markdown 中无法预览。

### 方案决策

**Bug 1 — 微信短链 query 参数清理 + `feedgrab clip` 命令**：
- `_normalize_wechat_url()` 短链格式剥离全部 query 参数和 fragment（短链的 `/s/hash` 就是文章标识，追踪参数是垃圾）
- PowerShell 的 `&` 报错发生在 shell 解析阶段，Python 程序无法拦截。新增 `feedgrab clip` 命令从系统剪贴板读取 URL，完全绕过 shell 解析
- `_read_clipboard()` 跨平台实现：Windows `Get-Clipboard` / macOS `pbpaste` / Linux `xclip`/`xsel`
- 从剪贴板文本中用正则提取第一个 URL，自动清理尾部标点

**Bug 2 — GitHub 中文 README 搜索扩展**：
- 原实现只查根目录 8 种变体文件名，无法发现子目录中的中文文档
- 新增 `_find_chinese_readme_from_content()`：解析默认 README 内容中的语言导航链接
- 6 种中文标记模式：`中文`/`简体中文`/`繁體中文`/`Chinese`/`ZH-CN`/`zh-CN`，兼容 bold/italic 包裹（`[**中文**](path)`）
- 支持相对路径（`./docs/README.zh-CN.md`）和完整 GitHub URL（自动提取相对路径）
- 零额外 API 调用（复用已获取的默认 README 内容）

**Bug 3 — GitHub README 图片链接补全**：
- 新增 `_resolve_relative_urls()`：将相对图片路径转为 `raw.githubusercontent.com` 绝对 URL
- 同时处理 Markdown `![alt](path)` 和 HTML `<img src="path">` 两种格式
- 正确处理 README 所在子目录的相对路径（基于 `readme_file` 计算 base_dir）
- `../` 路径规范化，绝对 URL / data URI / 锚点链接保持不变

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/reader.py` | 修改 | `_normalize_wechat_url()` 短链格式剥离全部 query + fragment |
| `feedgrab/fetchers/github.py` | 修改 | 新增 `_find_chinese_readme_from_content()`（~45 行）+ `_resolve_relative_urls()`（~50 行）；`fetch_github()` 新增 Step 3b + Step 4 |
| `feedgrab/cli.py` | 修改 | 新增 `_read_clipboard()`（跨平台剪贴板）+ `cmd_clip()`（URL 提取+抓取）+ `clip` 命令路由 + 帮助信息更新 |

### 验证结果
- Bug 1：`?scene=1&click_id=3` 短链 → 干净 URL ✅；长链保留 `__biz/mid/idx/sn` ✅；`feedgrab clip` 剪贴板读取正常 ✅
- Bug 2：`saicaca/fuwari` 仓库成功从 `[**中文**](https://github.com/.../docs/README.zh-CN.md)` 链接发现中文版，`readme_file=docs/README.zh-CN.md` ✅
- Bug 3：`axtonliu/axton-obsidian-visual-skills` 仓库 `assets/excalidraw-demo.png` 补全为 `raw.githubusercontent.com/.../assets/excalidraw-demo.png` ✅；外部绝对 URL 不变 ✅

### 状态：已完成 ✅

---

## 2026-03-17 · v0.12.2 · 微信公众号视频提取 + 媒体下载

### 背景
微信公众号文章中嵌入的视频（`<span class="video_iframe" data-mpvid>`）在当前抓取流程中被丢弃，Markdown 输出显示为"视频加载失败，请刷新页面再试"。实际上页面 JS 脚本中包含可直接访问的 MP4 地址（`mpvideo.qpic.cn` 域名，带签名时效），需要提取到 Markdown 中并支持可选下载。

### 方案决策

**视频 URL 提取（JS evaluate + 脚本解析）**：
- `<video>` 标签的 `src` 因资源拦截通常为空，不可靠
- 改为从页面 `<script>` 文本中用字符串分割法提取 `mpvideo.qpic.cn` URL
- 多质量版本去重：f10002(SD) / f10004(HD) / f10102 / f10104，自动选择最高质量
- URL 清理：JS hex escape `\x26amp;` → `&`，`http://` → `https://`（mpvideo CDN 要求 HTTPS）

**HTML 预处理注入**：
- `_build_wechat_result()` 在调用 `md_converter` 前将视频 URL 注入 `span.video_iframe` 容器
- `_preprocess_wechat_html()` 将 `<video src>` 和 `span.video_iframe` 替换为 `[▶ 视频](url)` 链接
- 同时清理"视频加载失败"错误文本和 `.js_video_poster` 容器

**媒体下载**：
- 仿 Twitter/XHS 模式：`MPWEIXIN_DOWNLOAD_MEDIA=true` 开启
- `utils/media.py` 新增 `platform="wechat"` 分支（filename 提取 + HTTPS 升级 + Referer 头）
- 覆盖范围：单篇（`reader.py`）+ 按账号批量 + 专辑批量

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/browser.py` | 修改 | JS evaluate 新增视频提取（脚本解析 + 质量选优）；`_build_wechat_result()` HTML 注入视频链接；`_clean_wechat_video_url()` URL 清理 |
| `feedgrab/fetchers/wechat_search.py` | 修改 | `_preprocess_wechat_html()` 新增视频元素→链接替换 + 错误文本清理 |
| `feedgrab/schema.py` | 修改 | `from_wechat()` extra 新增 `videos` / `images` 字段 |
| `feedgrab/config.py` | 修改 | 新增 `mpweixin_download_media()` 配置函数 |
| `feedgrab/utils/media.py` | 修改 | `platform="wechat"` 分支（filename / headers / URL 优化） |
| `feedgrab/reader.py` | 修改 | 单篇 WeChat 媒体下载触发 |
| `feedgrab/fetchers/mpweixin_account.py` | 修改 | 批量媒体下载触发 |
| `feedgrab/fetchers/mpweixin_album.py` | 修改 | 批量媒体下载触发 |
| `.env.example` | 修改 | 新增 `MPWEIXIN_DOWNLOAD_MEDIA` 配置项 |

### 验证结果
- 测试文章 `https://mp.weixin.qq.com/s/GlNS_Lzf6pacqqNCGGXD4A`（含 1 个视频）
- 默认模式：视频链接正确出现在 Markdown 第 149 行 `[▶ 视频](https://mpvideo.qpic.cn/...f10104.mp4?...)` ✅
- 下载模式：视频成功下载到 `attachments/42d48779ff55/` (582KB f10104 最高质量) ✅
- Markdown URL 替换为相对路径 `attachments/42d48779ff55/xxx.f10104.mp4` ✅
- "视频加载失败"错误文本被正确清理 ✅

### 状态：已完成 ✅

---

## 2026-03-17 · v0.12.1 · 微信公众号评论抓取（实验性）

### 背景
用户希望在抓取微信公众号文章时同时保存精选评论。微信文章评论通过 `appmsg_comment` API 动态加载，不在文章 HTML 中渲染。

### 方案决策

**评论基础设施**：
- JS evaluate 提取 `comment_id` + `appmsg_token`（从页面脚本变量）
- `fetch_wechat_comments()` 在页面上下文内调用 `appmsg_comment` API（继承 cookie）
- 解析 `elected_comment` 列表：主评论 + 子评论（reply_new.reply_list）
- Markdown 渲染为 blockquote 格式（用户名 + 点赞数 + 内容 + 缩进子评论）

**认证限制发现**：
- `appmsg_comment` API 需要微信客户端 session（`uin`/`key`/`pass_ticket`/`appmsg_token`），只有在微信 app 内打开文章时才会注入
- 普通浏览器访问时 `appmsg_token = ""`，API 返回 `ret=-3 "no session"`
- MP 后台 session（`feedgrab login wechat`）是管理 session，不等于阅读者 session
- 当前代码优雅降级：无 session 时输出 WARNING 日志，不影响文章抓取

**覆盖范围**：单篇（`wechat.py`）+ 按账号批量（`mpweixin_account.py`）+ 专辑批量（`mpweixin_album.py`）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/config.py` | 修改 | 新增 `mpweixin_fetch_comments()` + `mpweixin_max_comments()` 配置函数 |
| `feedgrab/fetchers/browser.py` | 修改 | JS evaluate 提取 comment_id + appmsg_token；`fetch_wechat_comments()` 新函数 ~75 行；"no session" 错误 WARNING |
| `feedgrab/fetchers/wechat.py` | 修改 | 单篇评论抓取触发（`MPWEIXIN_FETCH_COMMENTS=true` 开启） |
| `feedgrab/fetchers/mpweixin_account.py` | 修改 | 按账号批量评论抓取触发 |
| `feedgrab/fetchers/mpweixin_album.py` | 修改 | 专辑批量评论抓取触发 |
| `feedgrab/schema.py` | 修改 | `from_wechat()` 传递 `comment_list` 到 extra |
| `feedgrab/utils/storage.py` | 修改 | `_format_markdown()` 渲染评论 blockquote 区块 |
| `.env.example` | 修改 | 新增 `MPWEIXIN_FETCH_COMMENTS` / `MPWEIXIN_MAX_COMMENTS` 配置段 |

### 验证结果
- 评论 API 调用正常触发，参数完整（`f=json` + `comment_id` + `appmsg_token`）✅
- 无 session 时 WARNING 日志清晰输出，文章抓取不受影响 ✅
- 模块导入检查通过 ✅

### 状态：已完成 ✅（评论功能基础设施就绪，等待可行的微信客户端认证方案）

---

## 2026-03-16 · v0.12.0 · CDP Cookie 提取 + 微信专辑批量 + 媒体文件本地化

### 背景
三个独立增益功能：(1) Chrome 146 支持 `chrome://inspect/#remote-debugging` 一键开启 CDP，可用于从运行中的浏览器直接提取 Cookie，省去手动登录流程；(2) 微信公众号专辑页面可批量抓取全部文章；(3) Twitter/小红书图片视频可下载到本地 `attachments/` 目录，Markdown 中使用相对路径，Obsidian 离线可读。

### 方案决策

**CDP Cookie 提取**（`CHROME_CDP_LOGIN=true`）：
- 两级策略：Tier 0 Playwright `connect_over_cdp(ws://127.0.0.1:{port}/devtools/browser)` — Chrome 146 兼容（HTTP `/json/*` 端点返回 404，只能走 WebSocket）；Tier 1 传统 HTTP 发现 + 原始 WebSocket — `--remote-debugging-port` 兼容
- Playwright context cookies 已是 storage_state 格式，按平台域名过滤后直接保存
- 默认关闭，`CHROME_CDP_LOGIN=true` 开启后 `feedgrab login <platform>` 优先走 CDP，失败自动回退浏览器登录

**微信专辑批量**（`mpweixin-zhuanji`）：
- 以 `mpweixin_account.py` 为模板，结构完全对齐
- 差异：不需要 MP 后台 session（公开专辑可直接访问）；分页用 `begin_msgid`/`begin_itemidx`（非偏移量）
- 断点续传 + mpweixin 共享去重索引 + 日期过滤

**媒体文件本地化**（`X_DOWNLOAD_MEDIA` / `XHS_DOWNLOAD_MEDIA`）：
- 通用模块 `utils/media.py`，仿飞书图片下载模式
- Twitter 图片 `name=orig` 原图质量；XHS 去 CDN resize 后缀 + Referer 防盗链
- 单篇（`reader.py`）+ 全部 8 个批量 fetcher 均已适配
- 单张失败保留远程 URL，不影响其他下载

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/login.py` | 修改 | 重写 CDP 登录：两级策略（Playwright ws:// + 传统 HTTP+WebSocket），+160 行 |
| `feedgrab/config.py` | 修改 | 新增 6 个配置函数：CDP 开关/端口、媒体下载开关×2、专辑日期/间隔 |
| `feedgrab/fetchers/mpweixin_album.py` | 新建 | 微信专辑批量 fetcher ~280 行（分页、断点续传、去重、日期过滤） |
| `feedgrab/utils/media.py` | 新建 | 通用媒体下载模块 ~190 行（下载+URL优化+MD替换） |
| `feedgrab/cli.py` | 修改 | 新增 `mpweixin-zhuanji` 路由 + `cmd_mpweixin_album()` |
| `feedgrab/reader.py` | 修改 | 单篇 Twitter/XHS 媒体下载触发（仿飞书模式） |
| `feedgrab/fetchers/twitter_bookmarks.py` | 修改 | 批量媒体下载触发 +8 行 |
| `feedgrab/fetchers/twitter_user_tweets.py` | 修改 | 批量媒体下载触发 +8 行 |
| `feedgrab/fetchers/twitter_list_tweets.py` | 修改 | 批量媒体下载触发 +8 行 |
| `feedgrab/fetchers/twitter_search_tweets.py` | 修改 | 批量媒体下载触发 +8 行 |
| `feedgrab/fetchers/twitter_keyword_search.py` | 修改 | 批量媒体下载触发（`--save` 模式）+8 行 |
| `feedgrab/fetchers/twitter_api_user_tweets.py` | 修改 | 批量媒体下载触发 +8 行 |
| `feedgrab/fetchers/xhs_user_notes.py` | 修改 | 批量媒体下载触发 +15 行 |
| `feedgrab/fetchers/xhs_search_notes.py` | 修改 | 批量媒体下载触发 +15 行 |
| `.env.example` | 修改 | 新增 CDP / 专辑 / 媒体下载配置段 |

### 验证结果
- CDP Cookie 提取：Chrome 146 Remote Debugging 模式，`CHROME_CDP_LOGIN=true feedgrab login twitter` → 27 cookies 提取成功，`feedgrab doctor x` 13 项全部通过 ✅
- 媒体下载：`X_DOWNLOAD_MEDIA=true feedgrab https://x.com/wong2__/status/2032697322451382324` → `attachments/9a54ff583902/HDWWgyVaMAUUGuI.jpg` 下载成功，.md 中 URL 已替换为相对路径 ✅
- 双扩展名 bug（`.jpg.jpg`）修复验证通过 ✅

### 状态：已完成 ✅

---

## 2026-03-15 · v0.11.4 · Twitter 列表批量抓取 — 汇总表格文档（MD + CSV）

### 背景
`feedgrab x-so <keyword>` 关键词搜索完成后会生成一个按查看数排序的 MD + CSV 汇总表格，方便快速浏览结果。Twitter 列表批量抓取（`feedgrab https://x.com/i/lists/{id}`）此前只逐条保存单篇推文 .md，缺少同样的汇总视图。用户希望列表抓取也能生成汇总文档，并通过配置开关控制。

### 方案决策
- **配置开关**：新增 `X_LIST_TWEETS_SUMMARY`（默认 false），在 `config.py` 中添加 `x_list_tweets_summary()` 布尔函数
- **独立汇总函数**：在 `twitter_list_tweets.py` 中新增 `_generate_list_summary()`，不复用 `twitter_keyword_search.py` 的 `_generate_summary_table()`（后者 YAML front matter 含搜索语义字段），但表格渲染模式保持一致
- **表格列**：`# | 作者 | 内容摘要 | 日期 | 点赞 | 转帖 | 回复 | 查看 | 收藏 | 在线查看`，按查看数降序排列
- **内容摘要列**：使用 Obsidian wikilink `[[文件名|摘要文本]]` 链接到保存的对应 .md 文档，特殊字符（`|`、`[`、`]`、换行）转义处理
- **在线查看列**（最右列）：外部链接 `[查看](https://x.com/{author}/status/{id})`
- **CSV**：UTF-8 BOM 编码，摘要截断 80 字符（MD 40），显式 URL 列
- **数据收集**：在处理循环中收集 `collected_tweets`（tweet_data）和 `saved_paths`（tweet_id → 文件路径），循环结束后一次性生成汇总

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/config.py` | 修改 | 新增 `x_list_tweets_summary()` 配置函数 |
| `feedgrab/fetchers/twitter_list_tweets.py` | 修改 | 新增 `_generate_list_summary()` 汇总表格生成（~120 行）；`fetch_list_tweets()` 添加数据收集 + 汇总调用 + 返回值 `summary_path` |
| `feedgrab/reader.py` | 修改 | `_read_list_tweets()` 输出追加汇总表格路径 |
| `.env.example` | 修改 | 新增 `X_LIST_TWEETS_SUMMARY` 配置项 |

### 验证结果
- 模块编译检查通过：`twitter_list_tweets.py` 和 `reader.py` 导入无报错 ✅

### 状态：已完成 ✅

---

## 2026-03-15 · v0.11.3 · 飞书嵌入电子表格 Tier 0 API 修复（block_type 映射 + SDK token 提取）

### 背景
飞书文档中嵌入的电子表格（`block_type=30`）在 Tier 0 Open API 路径下无法被识别和渲染。`_BLOCK_TYPE_MAP` 缺少 `30: "sheet"` 映射，导致 API 返回的 sheet block 被当作 `unknown_30` 丢弃；同时 `_render_embedded_block()` 只能从 Playwright 的 `snapshot.type/token` 提取信息，无法处理 SDK Block 对象的 `block.sheet.token` 属性。

### 方案决策
- **补充 block_type 映射**：通过 lark_oapi SDK 源码和飞书 Open API 文档（[chyroc/lark](https://github.com/chyroc/lark) Go SDK 交叉验证），确认 `block_type=30` 对应 Sheet（电子表格），在 `_BLOCK_TYPE_MAP` 中新增 `30: "sheet"`
- **SDK Block 属性提取**：在 `_render_embedded_block()` 中增加 SDK Block 对象的属性检测逻辑——当 `snapshot` 为空时，依次检查 `block.sheet.token`（电子表格）和 `block.bitable.token`（多维表格），构造等效 snap 字典，使后续 `_fetch_embedded_sheet()` 能正确获取 token 并通过 Sheets Open API 读取数据渲染 GFM 表格

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/feishu.py` | 修改 | `_BLOCK_TYPE_MAP` 新增 `30: "sheet"`；`_render_embedded_block()` 增加 SDK Block 属性提取（sheet/bitable token） |

### 验证结果
- 6 个单元测试全部通过：block_type 映射、SDK Sheet/Bitable token 提取、Playwright 回归、缓存命中、blocks_to_markdown 端到端 ✅
- 单篇文档实测：2 个嵌入表格正确拦截 + 解码，输出 6 个 GFM 表格（15 行数据）✅

### 状态：已完成 ✅

---

## 2026-03-14 · v0.11.2 · 飞书图片 CDN 下载修复 + 浏览器预下载 + 按文档独立图片目录

### 背景
飞书文档图片下载存在两个问题：
1. **CDN 403 错误**：企业私有化部署的图片实际由 `internal-api-drive-stream.feishu.cn` 中央 CDN 提供，而非页面所在域名，导致 HTTP 下载全部 403
2. **图片目录管理混乱**：所有文档的图片平铺在同一个 `attachments/` 目录，几百张图片难以管理和对应

### 方案决策
- **三阶段浏览器预下载**：Phase 1 network interceptor 捕获页面加载时的图片 → Phase 2 滚动触发懒加载 → Phase 3 从 DOM `<img>` 元素发现真实 CDN 域名和 `mount_node_token`，用 JS `fetch()` 批量下载（10 张/批）
- **按文档独立图片子目录**：`attachments/{item_id}/`，item_id 与 front matter 的 `item_id` 字段完全一致（MD5(url)[:12]），方便查找对应关系
- **预下载优先写入**：`_download_images_via_cdn()` 优先写入 `_bytes`（浏览器预下载的二进制数据），跳过 HTTP 请求

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/browser.py` | 修改 | 新增 image response interceptor + CDN 域名发现 JS + 图片批量 fetch JS + `_find_image_tokens_from_tree` 双路径检查 + 三阶段预下载流程 |
| `feedgrab/fetchers/feishu.py` | 修改 | `blocks_to_markdown()` / `_block_to_md()` / `_render_children()` 新增 `img_subdir` 参数；`download_feishu_images()` 支持子目录；预下载 `_bytes` 优先写入；CDN headers 修复（Origin + CSRF） |
| `feedgrab/fetchers/feishu_wiki.py` | 修改 | 两个 Tier 均计算 `img_subdir` 并传递；Sheet 拦截器 + sheet 缓存 + 图片预下载集成；页面等待优化（author selector 替代固定等待） |
| `feedgrab/reader.py` | 修改 | 传递 `img_subdir` 到 `download_feishu_images()` |
| `feedgrab/schema.py` | 修改 | `from_feishu()` 传递 `img_subdir` 到 extra 字典 |

### 验证结果
- 654 张图片测试文档：654/654 全部预下载成功（0 intercepted + 654 JS fetched）✅
- 图片保存到 `attachments/ac5b0cf176d7/` 子目录 ✅
- Markdown 中 639 处引用路径 `attachments/ac5b0cf176d7/xxx.png` 与文件一致 ✅
- item_id 子目录与 front matter `item_id: ac5b0cf176d7` 完全匹配 ✅

### 状态：已完成 ✅

---

## 2026-03-14 · v0.11.1 · 浏览器层 3 处 bug 修复（微信指标丢失 + Twitter 隐身引擎统一）

### 背景
分析 Lightpanda 浏览器融合方案时（结论：不适合 feedgrab），顺带审查了 `browser.py` 和 Twitter 浏览器路径，发现 3 处遗留问题。

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/browser.py` | 修改 | `_build_wechat_result` 死代码修复 — `return` 前移至 cgiMetrics 处理之后，恢复微信阅读量/点赞/在看/分享/评论数输出 |
| `feedgrab/fetchers/twitter.py` | 修改 | Tier 3 Playwright 改用 `stealth_launch` + `get_stealth_context_options` + `setup_resource_blocking`（原仅 1 条启动参数） |
| `feedgrab/fetchers/twitter_search_tweets.py` | 修改 | 搜索补充抓取改用隐身引擎 + context 级资源拦截（原 vanilla playwright + 无拦截） |

### 验证结果
- 三个文件 `py_compile` 编译通过 ✅
- `_build_wechat_result` 不再提前 return，cgiMetrics 数据正常追加 ✅
- Twitter Tier 3 + 搜索补充统一走 52 条隐身参数 + 7 类资源拦截 + 11 个 tracking 域名拦截 ✅

### 状态：已完成 ✅

---

## 2026-03-14 · v0.11.0 · 飞书文档抓取（单篇 + 知识库批量 + 嵌入表格 + 图片下载）

### 背景
feedgrab 新增第 9 个平台 — 飞书/Lark。支持单篇文档抓取和知识库批量抓取，输出 Obsidian 兼容 Markdown。

### 方案决策
- **多级兜底架构**：Tier 0 Open API（`lark-oapi`）→ Tier 1 Playwright `window.PageMain` Block 树提取 → Tier 1.5 内部导出 API → Tier 2 Jina Reader
- **patchright 不兼容飞书**：飞书 ERR_CONNECTION_CLOSED，必须用 vanilla `playwright.async_api`（headed Chrome）
- **Block→Markdown 转换器**：支持 20+ 种 block 类型（heading/list/code/quote/table/image/equation/todo/callout/divider/grid/iframe/embed）
- **嵌入电子表格二级提取**：Canvas 渲染的 Sheet 无 DOM 可抓，通过 `client_vars` 内部 API + Protobuf 5 层解码提取单元格数据，渲染为 GFM 表格
- **图片文件名清理**：`_sanitize_filename()` 替换 `()@#%[]{}|<>!` 和空格，避免 Markdown 图片语法断裂
- **标题清理**：`_clean_feishu_title()` 过滤零宽字符（U+200B-U+206F, U+FEFF）+ 折叠换行 + 去后缀
- **知识库批量**：Open API 递归节点树 → 逐篇 blocks API → Playwright 兜底。断点续传 + 去重索引

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/feishu.py` | 新建 | 单篇抓取（三级兜底）+ Block→Markdown 转换器 + Protobuf Sheet 解码 + 图片下载 |
| `feedgrab/fetchers/feishu_wiki.py` | 新建 | 知识库批量抓取（Open API 递归 + Playwright 兜底 + 断点续传） |
| `feedgrab/fetchers/browser.py` | 修改 | `FEISHU_DOC_JS_EVALUATE` + `evaluate_feishu_doc()` + Sheet 内部 API 拦截/调用 |
| `feedgrab/reader.py` | 修改 | 飞书域名检测 + `from_feishu` 路由 + 图片下载集成 + 去重索引 |
| `feedgrab/schema.py` | 修改 | `SourceType.FEISHU` + `from_feishu()` 工厂函数 |
| `feedgrab/config.py` | 修改 | 7 个飞书配置函数（APP_ID/SECRET/WIKI_*/DOWNLOAD_IMAGES/CUSTOM_DOMAINS/PAGE_LOAD_TIMEOUT） |
| `feedgrab/cli.py` | 修改 | `feishu-wiki` 命令 + `cmd_feishu_wiki()` |
| `feedgrab/login.py` | 修改 | `feishu`/`lark` 登录入口 |
| `feedgrab/utils/storage.py` | 修改 | Feishu 平台目录映射 + 文件名格式 + front matter + 跳过标题 heading + `save_to_markdown()` 返回路径 |
| `pyproject.toml` | 修改 | `feishu` 可选依赖组（`lark-oapi>=1.5`） |
| `.env.example` | 修改 | 飞书配置项说明 |

### 验证结果
- 单篇 Playwright 抓取 ✅ — `feedgrab https://my.feishu.cn/wiki/Eaf3wWF51igr9gkKYShcHyfAnMd`
- 嵌入电子表格提取 ✅ — 12×4 MacBook 对比表正确渲染为 GFM 表格
- 图片下载 ✅ — `FEISHU_DOWNLOAD_IMAGES=true` 保存到 `attachments/` 子目录
- 图片文件名清理 ✅ — `Apple (中国大陆).jpg` → `Apple-中国大陆.jpg`
- YAML front matter 格式 ✅ — 标题单行、无重复 heading
- 知识库批量 ✅ — `feishu-wiki` 命令递归抓取

### 状态：已完成 ✅

---

## 2026-03-13 · v0.10.1 · 多关键词批量搜索 + 搜索结果质量修复

### 背景
用户使用 `xhs-so` 和 `x-so` 搜索时，每次只能搜一个关键词。日常场景常需一次性搜多个关键词（如 "claude code,openclaw,DeepSeek"），依次手动跑效率低。同时搜索结果存在非笔记 item 混入（广告/推荐词）和合并排序缺失等质量问题。

### 方案决策
- **逗号分隔格式**：`feedgrab xhs-so "k1,k2,k3"` — 双引号包裹，逗号分隔（支持中英文逗号），关键词内可含空格
- **双模式**：默认独立模式（各关键词各自一个文件），`--merge` 或环境变量开启合并模式（所有结果到一个文件，加"关键词"列）
- **合并模式全局排序**：Twitter 按查看数、XHS 按点赞数全局排序（非分段排序）
- **搜索质量修复**：API 层 `model_type` 过滤 + 表格层空行过滤

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/cli.py` | 修改 | `_split_keywords()` 辅助函数 + 两个 cmd 函数支持多关键词循环/合并 + `--merge` CLI flag + help 文本 |
| `feedgrab/config.py` | 修改 | 新增 `x_search_merge_keywords()` + `xhs_search_merge_keywords()` |
| `feedgrab/fetchers/xhs_api.py` | 修改 | `get_all_search_notes()` 新增 `model_type` 过滤（与浏览器模式一致）+ 跳过无 note_id 的残缺 item |
| `feedgrab/fetchers/xhs_search_notes.py` | 修改 | `_generate_xhs_summary_table()` 加 `show_keyword` 参数 + 空行过滤 + `search_xhs_keyword()` 返回 notes + `skip_summary` |
| `feedgrab/fetchers/twitter_keyword_search.py` | 修改 | `_generate_summary_table()` 加 `show_keyword` 参数 + 内置全局排序 + 返回 tweets + `skip_summary` |
| `.env.example` | 修改 | 新增 `X_SEARCH_MERGE_KEYWORDS` + `XHS_SEARCH_MERGE_KEYWORDS` + 多关键词用法示例 |

### 验证结果
- `feedgrab xhs-so "claude code,openclaw" --limit 20` — 独立模式生成 2 个文件 ✅
- `feedgrab xhs-so "claude code,openclaw" --limit 20 --merge` — 合并模式生成 1 个文件，"关键词"列正确，按点赞全局排序 ✅
- `feedgrab xhs-so "claude code"` — 单关键词兼容，169→161 条（过滤 8 条非 note 垃圾）✅
- `feedgrab x-so "梯子,VPN,v2ray,小火箭,openclash"` — Twitter 5 关键词合并模式 ✅

### 状态：已完成 ✅

---

## 2026-03-13 · v0.10.0 · 小红书 API 层集成 + xhs-so 搜索命令

### 背景
feedgrab 的小红书功能完全依赖 Jina Reader 和 Playwright 浏览器自动化，速度慢（每篇 ~5s）、需要 headed 模式、依赖 DOM 结构稳定。参考 [jackwener/xiaohongshu-cli](https://github.com/jackwener/xiaohongshu-cli) 的逆向 API 方案，通过 `xhshow` 签名库实现纯 HTTP 调用，单篇 <1s、无需浏览器、数据更完整。

### 方案决策
- 将 xiaohongshu-cli 的 API 能力作为新 Tier 0 集成到 feedgrab 三层兜底架构
- 签名配置使用真实系统平台和 UA（通过 `platform.system()` + `get_user_agent()` 自动检测），避免 Windows 环境用 macOS UA 被反爬识别
- Cookie 来源复用 `sessions/xhs.json` Playwright storage_state（零成本，无需新登录流程）
- `xhshow` 为可选依赖，未安装时自动降级到浏览器模式

### 新增功能

| 功能 | 说明 |
|------|------|
| 单篇 API 抓取 | API → Jina → Playwright 三级兜底（~0.5s vs 原 ~5s） |
| 作者批量 API | cursor 自动分页（30条/页），失败降级到浏览器三层策略 |
| 搜索批量 API | page 分页 + 排序/类型筛选，失败降级到浏览器 |
| `xhs-so` 命令 | 关键词搜索汇总表（MD + CSV），仿 `x-so` 模式 |
| 评论抓取 | `XHS_FETCH_COMMENTS=true` 时提取评论全文 + 子评论 |
| xsec_token 缓存 | LRU 磁盘缓存 500 条（`sessions/cache/xhs_token_cache.json`） |
| doctor xhs 增强 | xhshow 安装检测 + API 连通性 + Cookie 有效性 |

### 配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `XHS_API_ENABLED` | true | API 优先模式开关 |
| `XHS_API_DELAY` | 1.0 | API 请求间隔秒数 |
| `XHS_FETCH_COMMENTS` | false | 单篇时获取评论全文 |
| `XHS_MAX_COMMENTS` | 5 | 评论最大页数（~20条/页） |
| `XHS_SEARCH_SORT` | general | 搜索排序: general/popular/latest |
| `XHS_SEARCH_NOTE_TYPE` | all | 搜索类型: all/video/image |
| `XHS_SEARCH_MAX_PAGES` | 10 | 搜索最大页数（每页 20 条） |

### 改动范围

| 文件 | 操作 | 说明 |
|------|------|------|
| `feedgrab/fetchers/xhs_api.py` | **新建** | XHS API 客户端核心（~550 行） |
| `feedgrab/fetchers/xhs.py` | 修改 | 加入 API Tier 0 + 评论抓取 |
| `feedgrab/fetchers/xhs_user_notes.py` | 修改 | API cursor 分页优先 |
| `feedgrab/fetchers/xhs_search_notes.py` | 修改 | API page 分页 + xhs-so 搜索函数 |
| `feedgrab/cli.py` | 修改 | xhs-so 命令 + doctor xhs 增强 |
| `feedgrab/config.py` | 修改 | 7 个 XHS API 配置函数 |
| `feedgrab/schema.py` | 修改 | from_xiaohongshu 扩展 extra 字段 |
| `feedgrab/utils/storage.py` | 修改 | 评论渲染到 Markdown 末尾 |
| `pyproject.toml` | 修改 | xhs 可选依赖组 |
| `.env.example` | 修改 | XHS API 配置项说明 |

### xhs-so 用法
```
feedgrab xhs-so "AI Agent"                           # 综合搜索
feedgrab xhs-so "AI Agent" --sort popular             # 按热门排序
feedgrab xhs-so "AI Agent" --type video               # 只搜视频
feedgrab xhs-so "AI Agent" --sort latest --limit 50   # 最新 50 条
feedgrab xhs-so "AI Agent" --save                     # 同时保存单篇 .md
```

输出：`{OUTPUT_DIR}/XHS/search/{排序}/{关键词}_{日期}.{md,csv}`

### 移植来源
从 [jackwener/xiaohongshu-cli](https://github.com/jackwener/xiaohongshu-cli) v0.6.0 移植核心能力：
- 请求/重试/限速逻辑（Gaussian 抖动 + 验证码冷却 + 指数退避）
- API 端点定义（Feed/UserPosted/SearchNotes/Comments）
- xsec_token LRU 缓存
- 签名配置（适配 feedgrab 真实 UA/平台）

**不集成**的部分：写操作、QR 登录、通知系统、创作者平台。

### 状态：已完成 ✅

---

## 2026-03-12 · v0.9.14 · 批量抓取数据完整性 + 线程退化保护

### 背景
全面审计所有 Twitter 批量抓取路径后发现两个问题：
1. `_build_single_tweet_data()` 缺少 8 个扩展元数据字段（`quote_count`/`lang`/`source_app`/`possibly_sensitive`/`is_blue_verified`/`followers_count`/`statuses_count`/`listed_count`），导致批量模式单条保存的推文 front matter 不如 GraphQL 全线程抓取完整
2. 书签/用户推文/搜索补充/列表抓取中，线程推文 `_fetch_via_graphql()` 失败时异常冒泡，导致整条推文被跳过而非退化为单条保存

### 方案决策
- **扩展元数据**：在 `_build_single_tweet_data()` 中补齐 8 个字段，全部 5 个批量 fetcher 共用此函数，改一处全部生效
- **线程退化保护**：在 4 个文件的 thread 分支中加 try/except，GraphQL 线程重建失败时退化为 `_build_single_tweet_data()` 单条保存（`api_user_tweets` 已有统一 GraphQL try/except 无需改动）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_bookmarks.py` | 修改 | `_build_single_tweet_data()` 新增 8 个扩展元数据 + thread 退化保护 |
| `feedgrab/fetchers/twitter_user_tweets.py` | 修改 | thread 退化保护 |
| `feedgrab/fetchers/twitter_search_tweets.py` | 修改 | thread 退化保护 |
| `feedgrab/fetchers/twitter_list_tweets.py` | 修改 | thread 退化保护 |

### 验证结果
- 代码审查确认 `extract_tweet_data()` 已填充全部 8 个字段 ✅
- 5 个批量 fetcher 全部 import `_build_single_tweet_data()`，改一处全部受益 ✅
- 线程退化：4 个文件 + `api_user_tweets` 已有 = 全部 5 个批量 fetcher 覆盖 ✅

### 状态：已完成 ✅

---

## 2026-03-12 · v0.9.13 · 批量 Article 优先 GraphQL content_state（消除 Jina 瓶颈）

### 背景
用户发现书签批量抓取中，Article 长文章走了 Jina Reader（每篇 ~10-15 秒，2 次 HTTP + hollow 修补），而非预期的 GraphQL 优先策略。经排查发现 v0.6.2 新增 `_render_article_body()` 时只更新了单篇路径（`twitter.py`），5 个批量 fetcher 的 article 分支从未同步更新，仍然直接调 Jina。实际上 GraphQL 层已在 `extract_tweet_data()` → `_extract_article_ref()` 中渲染好了 `article["body"]`，但批量分支不看这个字段。

### 方案决策
- 在所有批量 fetcher 的 article 分支中，先检查 `article.get("body")` 是否存在且 >200 字
- 有：直接使用 GraphQL content_state 渲染结果，零额外网络请求
- 无：fallback 到 `_fetch_article_body()` 走 Jina（与单篇路径 `_try_fetch_article_body()` 的 Priority 1/2 策略一致）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_bookmarks.py` | 修改 | article 分支新增 content_state 优先检查 |
| `feedgrab/fetchers/twitter_user_tweets.py` | 修改 | 同上 |
| `feedgrab/fetchers/twitter_list_tweets.py` | 修改 | 同上 |
| `feedgrab/fetchers/twitter_search_tweets.py` | 修改 | 同上 |
| `feedgrab/fetchers/twitter_api_user_tweets.py` | 修改 | 同上 |

### 验证结果
- 书签文件夹 `手机卡esim`（19 条，8 篇 Article）：全部 8 篇 Article 走 GraphQL content_state ✅
- 修复前：~3 分钟（每篇 Article ~10-15s Jina）→ 修复后：**~30 秒**（零 Jina 调用）
- 日志确认：`Article — GraphQL content_state: @author` 而非 `Jina fetch`

### 状态：已完成 ✅

---

## 2026-03-12 · v0.9.12 · `feedgrab doctor` 诊断命令

### 背景
feedgrab 的 Twitter 集成依赖多个组件（Cookie、queryId、x-client-transaction-id、可选依赖、网络连通性），任一环节出问题都会导致抓取失败。用户排障困难，需要逐一检查。参考 twitter-cli 的 `twitter doctor` 命令实现一键诊断。

### 方案决策
- **按平台分区**：`feedgrab doctor [x|xhs|mpweixin]`，不带参数则全平台检查
- **Twitter/X 检查**：可选依赖 → Cookie 状态 → queryId 解析 → x-client-transaction-id 生成 → x.com + 社区源连通性
- **小红书检查**：浏览器引擎 → session 存在性 → xiaohongshu.com 连通性
- **微信公众号检查**：浏览器引擎 → wechat.json session 存在性 + 过期检测（>96h）→ mp.weixin.qq.com 连通性
- **三级状态**：✅ passed / ⚠️ warning / ❌ error，汇总输出，每个失败项附带修复指令

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/cli.py` | 修改 | 新增 `cmd_doctor()` 函数（~120 行）+ `main()` 路由 + help 信息 |

### 验证结果
- `feedgrab doctor x` — 13/13 全过 ✅
- `feedgrab doctor xhs` — 正确提示 session 未登录 ⚠️
- `feedgrab doctor mpweixin` — 正确检测 session 过期（153h > 96h 阈值）⚠️
- `feedgrab doctor` — 全平台检查正常 ✅

### 状态：已完成 ✅

---

## 2026-03-12 · v0.9.11 · Feature Flags 动态更新

### 背景
feedgrab 的 GraphQL features 字典是硬编码的，Twitter 前端迭代后可能新增或修改 feature flag 的默认值，导致请求参数过时。参考 [jackwener/twitter-cli](https://github.com/jackwener/twitter-cli) 从 x.com 主页 HTML 提取当前 feature 开关值的做法，实现动态同步。

### 方案决策
- **正则提取**：`_update_features_from_html(html)` 用正则 `"key": { "value": true/false }` 从 x.com 主页内联脚本中提取 feature flag 值
- **只更新已有 key**：绝不新增 key（避免 URL 膨胀），仅更新 7 个 features 字典中已存在的 key
- **零额外 HTTP 请求**：复用 `_get_transaction_id()` 已获取/缓存的 `home_html`，在 transaction 初始化后立即调用
- **实测效果**：检测到 33 个 flag 变化（如 `tweet_awards_web_tipping_enabled: True→False`、`responsive_web_grok_image_annotation_enabled: False→True`）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 新增 `_ALL_FEATURES_DICTS` 注册表 + `_update_features_from_html()` 提取函数；在 `_get_transaction_id()` 中调用 |

### 验证结果
- `feedgrab x-so "Claude Code" --days 1 --limit 3` — 33 flags 动态更新 + SearchTimeline 正常 ✅
- `feedgrab https://x.com/0xMilkRabbit/status/...` — TweetDetail Tier 0 命中 ✅

### 状态：已完成 ✅

---

## 2026-03-12 · v0.9.10 · Feature Flags 紧凑编码

### 背景
Twitter GraphQL 请求的 URL 中包含 `features` 参数，包含约 30 个 feature flag（True/False 布尔值）。当前实现将所有 flag 都发送（包括 False 值），导致 URL 过长，增加被 HTTP 414 URI Too Long 拒绝的风险。参考 [jackwener/twitter-cli](https://github.com/jackwener/twitter-cli) 只发送 True 值的做法进行优化。

### 方案决策
- **紧凑编码**：在 `_execute_graphql()` 中，将 `features` dict 过滤为只含 True 值的子集后再 JSON 序列化。Twitter 服务端将缺失的 key 视为 false，行为不变
- **效果**：SearchTimeline URL 减少约 689 字节（~30%），其他端点减少约 485 字节

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | `_execute_graphql()` 中新增 `compact_features` 过滤，仅发送 True 值的 features |

### 验证结果
- `feedgrab x-so "Claude Code" --days 1 --limit 3` — SearchTimeline 正常 ✅
- `feedgrab https://x.com/0xMilkRabbit/status/2032018202134212868` — TweetDetail Tier 0 命中 ✅

### 状态：已完成 ✅

---

## 2026-03-12 · v0.9.9 · GraphQL 冷启动加速（磁盘缓存 + 社区 queryId 源）

### 背景
每次 feedgrab 进程启动时，`_get_transaction_id()` 需要请求 x.com 首页 + ondemand.s JS 文件（2 次 HTTP，~3-5 秒）来初始化签名生成器；`resolve_query_ids()` 需要请求首页 + 多个 JS chunk 来解析 queryId（3-8 次 HTTP）。对 `feedgrab x-so` 等频繁使用的命令，冷启动延迟体感明显。参考 [jackwener/twitter-cli](https://github.com/jackwener/twitter-cli) 的磁盘缓存和社区 queryId 源方案进行优化。

### 方案决策
- **x-client-transaction-id 磁盘缓存**：将 x.com 首页 HTML + ondemand.s JS 缓存到 `{data_dir}/cache/twitter_transaction_cache.json`（1 小时 TTL）。进程重启时从磁盘加载，避免 2 次 HTTP 请求。同时将 `home_html` 填充到 `_cached_home_html`，使 queryId JS 扫描也受益
- **queryId 社区源**：新增 `fa0311/twitter-openapi` 社区维护的 queryId 源作为 Tier 1（单次 HTTP 请求获取 87 个 queryId）。解析优先级变为：Tier 0 磁盘缓存 → Tier 1 社区源 → Tier 2 JS bundle 扫描 → Tier 3 硬编码回退。queryId 也缓存到磁盘（`twitter_queryid_cache.json`，1 小时 TTL）
- **Fallback queryIds 更新**：7 个硬编码 fallback queryId 更新为社区源最新值（`SearchTimeline`、`TweetDetail`、`Bookmarks`、`UserTweets` 等）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 新增磁盘缓存辅助函数（`_load_transaction_cache`/`_save_transaction_cache`/`_load_queryid_cache`/`_save_queryid_cache`）、社区源函数（`_resolve_community_query_ids`）；改造 `_get_transaction_id()` 支持磁盘缓存；改造 `resolve_query_ids()` 支持四级优先级；更新 7 个 fallback queryId |

### 验证结果
- `feedgrab x-so "Claude Code" --days 1 --limit 3` — 首次运行：社区源获取 87 个 queryId，搜索正常 ✅
- 二次运行：queryId + transaction-id 均命中磁盘缓存，零 HTTP 请求 ✅
- `feedgrab https://x.com/0xMilkRabbit/status/...` — Tier 0 GraphQL 一次命中，社区 queryId 正常 ✅
- `feedgrab https://x.com/hualun/status/...` — Tier 0 GraphQL 一次命中 ✅
- 冷启动性能：~5s → ~1s（社区源）；热启动：~3s → **0s**（全磁盘缓存）

### 状态：已完成 ✅

---

## 2026-03-07 · v0.9.8 · x-so 纯 GraphQL 升级 + x-client-transaction-id 反检测

### 背景
v0.9.7 的 `x-so` 命令使用 headed 浏览器（Playwright）打开 Twitter 搜索页面，通过滚动加载 + GraphQL 响应拦截收集推文数据。问题：需要弹出浏览器窗口、占用桌面、启动慢（~10 秒）、滚动等待慢。`twitter_graphql.py` 中已有 `fetch_search_timeline_page()` 纯 GraphQL 函数，应该直接复用。

### 方案决策
- **纯 GraphQL 替代浏览器**：`search_twitter_keyword()` 改为同步函数，直接调用 `fetch_search_timeline_page()` 分页获取搜索结果，无需 Playwright 浏览器
- **`x-client-transaction-id` 反检测**：Twitter 对 SearchTimeline 端点强制要求此签名头（缺失返回 404）。集成 `XClientTransaction` 库在 `_execute_graphql()` 中自动为所有 GraphQL 请求生成此头。算法基于 x.com 主页 SVG 动画 + `ondemand.s` JS 索引 + SHA-256 签名
- **优雅降级**：未安装 `XClientTransaction` 时警告提示，不影响不需要此头的端点（如 TweetDetail）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 新增 `_get_transaction_id()` 生成 x-client-transaction-id，注入 `_execute_graphql()` |
| `feedgrab/fetchers/twitter_keyword_search.py` | 修改 | 替换浏览器搜索为纯 GraphQL 分页调用，`async def` → `def` |
| `feedgrab/cli.py` | 修改 | `asyncio.run()` → 直接调用（不再需要 async） |
| `pyproject.toml` | 修改 | 新增 `twitter` 可选依赖组（XClientTransaction + beautifulsoup4） |

### 验证结果
- `feedgrab x-so "AI Agent" --days 1 --limit 5` — 秒级完成，无浏览器弹出 ✅
- `feedgrab x-so openclaw --days 3 --sort top --limit 10` — 热门排序正常 ✅
- 单篇推文抓取 `feedgrab https://x.com/xxx/status/xxx` — TweetDetail 正常工作 ✅
- x-client-transaction-id 30 分钟缓存复用 ✅

### 追加优化（同日）
- **输出格式优化**：`cssclasses: wide` front matter + emoji 表头 + 日期列前移 + 居中对齐
- **排序改为按查看数**：默认按 `views` 降序排列（替代原互动加权公式）
- **内容摘要超链接**：MD 中去掉独立链接列，摘要文本直接作为超链接
- **CSV 同步输出**：同目录生成 `.csv` 文件（UTF-8 BOM，Excel 友好），保留明文链接列
- **蓝 V 标记 + 显示名**：作者列优先使用 `author_name`（显示名）替代 `@handle`，蓝 V 认证作者前加 ✅ emoji
- **微信 URL 参数清理**：自动剥离 `scene`/`click_id`/`sessionid` 等追踪参数，仅保留 `__biz`/`mid`/`idx`/`sn` 四个文章标识参数，解决 PowerShell `&` 解析冲突和去重索引不一致问题

### 状态：已完成 ✅

---

## 2026-03-07 · v0.9.7 · Twitter 关键词搜索（x-so 命令）

### 背景
feedgrab 已有 Twitter 单篇/书签/用户推文/列表等抓取方式，但缺少按关键词搜索 Twitter 的能力。用户需要快速了解某个关键词（如 "openclaw"）在 Twitter 上的讨论热度和观点分布，输出按互动量排序的汇总表格即可，不需要逐篇保存。

### 方案决策
- **浏览器搜索 + GraphQL 拦截**：复用 `twitter_search_tweets.py` 的 `SearchResponseCollector` 和 `_scroll_and_collect_search()`，通过 `page.on("response")` 拦截 SearchTimeline GraphQL 响应获取结构化数据
- **汇总表格为主**：默认只输出一个按互动量排序的 Markdown 表格（YAML front matter + 表格），不保存单篇推文 .md
- **可选单篇保存**：`X_SEARCH_SAVE_TWEETS=true` 或 `--save` 开关，保存完整推文到子目录
- **自动引号包装**：`feedgrab x-so openclaw` 自动在搜索时添加引号精确匹配，用户无需手动加引号
- **Raw 模式**：`--raw` 标志让用户完全控制搜索查询语法（lang/since/filter 等操作符）
- **互动排序公式**：`likes*3 + retweets*2 + bookmarks*2 + replies`
- **配置默认值**：11 个 `X_SEARCH_*` 环境变量提供默认语言(zh)、天数(1)、排序(live)等

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_keyword_search.py` | 新建 | 核心搜索逻辑（查询拼接 + 浏览器搜索 + 互动排序 + 表格生成） |
| `feedgrab/cli.py` | 修改 | 新增 `x-so` 命令路由 + `cmd_twitter_search()` |
| `feedgrab/config.py` | 修改 | 新增 `x_search_*` 系列 11 个配置函数 |
| `.env.example` | 修改 | 新增 Twitter/X 关键词搜索配置段 |

### 验证结果
- `feedgrab x-so openclaw` — 40 条推文，按互动排序输出汇总表 ✅
- 查询自动构建：`"openclaw" lang:zh since:2026-03-06 -is:retweet` ✅
- YAML front matter 包含 query/total/search_tab/created ✅
- 表格含 作者/内容摘要/👍/🔄/💬/👁/📌/日期/链接 九列 ✅

### 状态：已完成 ✅

---

## 2026-03-07 · v0.9.6 · GitHub 仓库 README 抓取（中文优先）

### 背景
feedgrab 在平台覆盖上缺少 GitHub 支持。用户需要丢一个 GitHub 仓库 URL，就能自动抓取 README（中文优先）并保存为 Obsidian Markdown。支持仓库首页、README 文件页、其他内页三种 URL 格式，统一回退到仓库级别。

### 方案决策
- **GitHub REST API**：3 次 API 调用完成抓取（仓库元数据 + 根目录列表 + README 内容），无需浏览器
- **中文 README 优先**：列出根目录所有文件，按优先级匹配 8 种中文 README 变体（`README_CN.md`、`README.zh-CN.md` 等），匹配后直接获取中文版本
- **README 摘要提取**：从 README 内容中提取第一行有意义的描述文本作为标题（跳过 heading、badge、HTML、blockquote、短文本），替代 GitHub API 的英文 description
- **仓库级去重**：`item_id = MD5("{owner}/{repo}")[:12]`，同一仓库无论从哪个 URL 进入都产生相同 ID
- **无 Token 可用**：未配置 `GITHUB_TOKEN` 时 60 次/小时（按 IP），配置后 5000 次/小时
- **URL 解析**：`parse_github_url()` 统一处理仓库首页/blob 文件页/tree 目录页/issues 等内页，取前两段 path 作为 owner/repo

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/github.py` | 新建 | GitHub REST API 抓取核心（URL 解析 + 元数据 + 中文 README 优先 + 摘要提取） |
| `feedgrab/schema.py` | 修改 | 新增 `SourceType.GITHUB` 枚举值 + `from_github()` 工厂方法 |
| `feedgrab/reader.py` | 修改 | 新增 `github.com` 域名检测 + 路由分发 + 多平台去重映射 |
| `feedgrab/utils/storage.py` | 修改 | 新增 GitHub 文件夹映射 + 文件名格式（`{owner}_{repo}：{摘要}`）+ front matter |
| `feedgrab/config.py` | 修改 | 新增 `github_token()` 配置函数 |
| `.env.example` | 修改 | 新增 `GITHUB_TOKEN` 配置说明 |

### 验证结果
- `feedgrab https://github.com/iBigQiang/feedgrab` — 仓库首页抓取 ✅，文件名 `iBigQiang_feedgrab：万能内容抓取器 — 从任意平台抓取、转录和消化内容。.md`
- `feedgrab https://github.com/iBigQiang/feedgrab/blob/main/README.md` — README 文件页 ✅，正确回退到仓库级别
- `feedgrab https://github.com/iBigQiang/feedgrab/tree/main/feedgrab/fetchers` — 内页 URL ✅，正确回退到仓库级别
- `feedgrab https://github.com/nicepkg/aide` — 中文 README 优先 ✅，检测到 `README_CN.md` 并使用
- front matter 包含 stars/forks/language/license/topics 等完整元数据 ✅
- 去重索引生成在 `GitHub/index/item_id_url.json` ✅

### 状态：已完成 ✅

---

## 2026-03-06 · v0.9.5 · YouTube Data API v3 搜索 + 单视频下载命令

### 背景
feedgrab 原有的 YouTube 抓取仅依赖 Jina Reader 获取元数据，缺少搜索能力和视频/音频/字幕文件下载能力。对比分析了 yt-search-download 和 union-search-skill 两个第三方仓库后，决定融合 YouTube Data API v3 实现搜索模块，同时升级单视频抓取为 API 优先策略。

### 方案决策
- **YouTube Data API v3 搜索**：免费 10,000 quota/天，search.list=100 单位，videos.list=1 单位。两阶段查询：search → videoId list → videos.list 批量详情
- **API 优先单视频**：替代 Jina-first 元数据获取，1 quota 单位获取完整元数据（标题/作者/时长/播放量/标签/缩略图/字幕标记）
- **多语言字幕回退**：`[sub_lang, "zh-CN", "zh-Hans", "zh-Hant", "zh", "en", "en-US"]`，覆盖手动字幕和自动字幕
- **yt-dlp JS 运行时修复**：yt-dlp 默认只启用 deno，`_js_runtime_args()` 自动检测 deno/node/bun 并加 `--remote-components ejs:github`
- **三个下载命令**：`ytb-dlv`(视频MP4)、`ytb-dla`(音频MP3)、`ytb-dlz`(字幕SRT)，输出目录和文件名与 MD 保持一致
- **频道搜索修复**：channel 限定搜索时跳过 `regionCode`/`relevanceLanguage` 参数（会导致空结果）
- **Cookie 检测简化**：移除不可靠的自动检测（Windows 上 Chrome DB 锁定），改为 `YT_COOKIES_BROWSER` 环境变量控制

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/youtube_search.py` | 新建 | YouTube Data API v3 搜索引擎 + yt-dlp 下载（视频/音频/字幕） |
| `feedgrab/fetchers/youtube.py` | 重写 | API 优先元数据 + 多语言字幕回退 + JS 运行时修复 |
| `feedgrab/cli.py` | 修改 | 新增 `ytb-so`/`ytb-dlv`/`ytb-dla`/`ytb-dlz` 四个命令 |
| `feedgrab/schema.py` | 修改 | `from_youtube()` 扩展支持完整 API 元数据（时长/播放量/标签等） |
| `feedgrab/utils/storage.py` | 修改 | YouTube 文件名前缀 + front matter + 封面图 + 字幕分段 |
| `.env.example` | 修改 | 新增 YouTube API 配置项模板 |

### 验证结果
- `feedgrab ytb-so "AI Agent"` — 搜索成功，10 条结果保存到 `YouTube/search/AI Agent/` ✅
- `feedgrab ytb-so "教程" --channel @Fireship --limit 3` — 频道限定搜索 3 条结果 ✅
- `feedgrab https://www.youtube.com/watch?v=g56TThyELm0` — API 元数据 + zh-Hant 字幕成功 ✅
- `feedgrab https://www.youtube.com/watch?v=bBG25aoIS0s` — zh-CN 手动字幕成功（修复前失败） ✅
- `feedgrab ytb-dlv <url>` — 视频下载到 `{OUTPUT_DIR}/YouTube/` 目录 ✅
- `feedgrab ytb-dla <url>` — 音频下载，长链接和 youtu.be 短链接都兼容 ✅
- `feedgrab ytb-dlz <url>` — 字幕 SRT 下载成功 ✅
- 文件名格式统一：`author_date：title.{mp4,mp3,srt,md}` ✅

### 状态：已完成 ✅

---

## 2026-03-06 · v0.9.4 · 微信单篇抓取策略反转：Browser 优先

### 背景
微信单篇抓取原先采用 Jina 优先策略（Tier 1 Jina → Tier 2 Browser → Tier 3 Browser retry），但实际运行中 Jina 几乎每次都因微信 CDN 超时而白等 30 秒，且返回数据不完整（缺少 author/date/cover/tags）。而 Browser 使用 WeChat JS evaluate 提取的数据最全（9 类元数据 + 富文本 Markdown），成功率高。参考 X/Twitter 的 GraphQL 优先策略，将 Browser 提升为 Tier 1。

### 方案决策
- 反转抓取层级：Browser → Jina → Browser retry（与 X/Twitter 的 GraphQL-first 策略对齐）
- 提取 `_browser_fetch()` 内部函数，Tier 1 和 Tier 3 复用同一段浏览器抓取逻辑
- Jina 降级为 Tier 2 轻量兜底，仅在浏览器环境不可用时触发

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/wechat.py` | 重写 | 抓取策略从 Jina→Browser 反转为 Browser→Jina→Browser retry |

### 验证结果
- 普通长文（`mp.weixin.qq.com/s/pQioMCCW9sCOZ1BW8fRD9A`）：Browser Tier 1 直接成功，约 4 秒完成（原方案需 34+ 秒等待 Jina 超时）✅
- 小绿书图片帖（`mp.weixin.qq.com/s/lk60C8tBWknMzFTQRUIFTQ`）：Browser Tier 1 成功提取标题+作者+内容 ✅

### 状态：已完成 ✅

---

## 2026-03-06 · v0.9.3 · 微信代码块修复 + 小绿书元数据回退

### 背景
微信公众号文章中使用 plain `<pre><code>` 格式的代码块在抓取后所有行被压缩为一行，原因是 `BeautifulSoup.get_text()` 丢弃了 `<br>` 标签的换行语义。同时，当文章包含 10+ 个代码块时，占位符还原出现前缀碰撞（`WECHAT-CODEBLOCK-1` 匹配到 `WECHAT-CODEBLOCK-10` 的前缀），导致部分代码块被错误替换并残留数字尾巴。此外，markdownify 在处理占位符时吃掉了两侧的换行，导致代码围栏（` ``` `）与相邻图片/文本粘连。

另一个问题：微信"小绿书"图片帖（`itemShowType=16`）的 DOM 结构与普通长文不同 — `#activity-name` 和 `#js_name` 元素不存在，导致标题和作者提取全部为空，文件名缺少 `作者名_日期：` 前缀。

### 方案决策

**代码块修复（3 个子问题）：**
1. `<br>` → `\n`：在 `_preprocess_wechat_html()` 的两个代码块处理器中（`.code-snippet__fix` 和 plain `<pre>`），调用 `get_text()` 前先将所有 `<br>` 标签替换为 `\n` 文本节点
2. 占位符前缀碰撞：还原时反向遍历（从最大索引到 0），避免 `CODEBLOCK-1` 匹配 `CODEBLOCK-10`
3. 围栏间距：还原时在 fence 前后加 `\n\n`，确保代码块与相邻内容有空行分隔

**小绿书元数据回退（`WECHAT_ARTICLE_JS_EVALUATE` 增强）：**
- 标题回退链：`#activity-name` → `og:title` → `.rich_media_title`
- 作者回退链：`#js_name` → JS 脚本 `nick_name` 正则 → `window.cgiDataNew.nick_name`

通过 Playwright 实测确认：小绿书页面的 `cgiDataNew` 对象包含完整的 `nick_name`（如 "饼干哥哥AGI"）和 `title`，是最可靠的回退数据源。

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/wechat_search.py` | 修改 | `_preprocess_wechat_html()` 两处 `<br>→\n` + `_html_to_markdown()` 反向还原 + fence 间距 |
| `feedgrab/fetchers/browser.py` | 修改 | `WECHAT_ARTICLE_JS_EVALUATE` 新增标题/作者三级回退 |

### 验证结果
- 代码块测试（`mp.weixin.qq.com/s/pQioMCCW9sCOZ1BW8fRD9A`，12 个 plain `<pre>` 块）：
  - 12 个代码块全部正确识别和还原 ✅
  - Python/JSON/Prompt 内容有正确的多行格式（86 行、55 行、31 行等）✅
  - 代码围栏与相邻图片有空行分隔 ✅
  - 无占位符残留数字 ✅
- 小绿书测试（`mp.weixin.qq.com/s/lk60C8tBWknMzFTQRUIFTQ`）：
  - 标题 "X上疯传 从2050个n8n 工作流中总结出的🔟个要点"（`og:title`）✅
  - 作者 "饼干哥哥AGI"（`cgiDataNew.nick_name`）✅
  - 发布日期 "2025-08-03 23:44"（`create_time`）✅

### 状态：已完成 ✅

---

## 2026-03-06 · v0.9.2 · 微信公众号按账号批量抓取 + cgiDataNew 元数据管线

### 背景
feedgrab 已支持微信公众号单篇抓取和搜狗搜索批量抓取，但缺少按公众号账号批量枚举全部历史文章的能力。通过分析 [wechat-article-exporter](https://github.com/nichenke/wechat-article-exporter) 的 MP 后台 API 逆向方案，发现可以利用 `feedgrab login wechat` 保存的 MP 后台 session，调用 `searchbiz`（搜索公众号→fakeid）和 `appmsgpublish`（分页文章列表）API 实现全量枚举。

同时调研了 `window.cgiDataNew.user_info.appmsg_bar_data` 的阅读量/点赞/评论提取可行性。测试结果：匿名访问时 `appmsg_bar_data` 为空对象，互动数据需要微信认证会话才会填充。代码已预埋管线，未来认证会话可用时自动启用。

### 方案决策

**MP 后台 API 按账号批量抓取**
1. `mpweixin_account.py` 新建：核心 fetcher，使用 Playwright `page.evaluate()` + `fetch(url, {credentials: 'include'})` 调用 MP API（自动携带 session cookie）。
2. `searchbiz` API：按名称搜索公众号，精确匹配优先，返回 fakeid。
3. `appmsgpublish` API：按 fakeid 分页枚举文章列表（每页 5 条），`publish_page.publish_list` 内含 `publish_info.appmsgex[]` 文章数组。
4. 日期过滤：`MPWEIXIN_ID_SINCE` 配置项控制截止日期，到达后停止分页。
5. 断点续传：`_progress_*.json` 缓存文件记录 `next_begin/fetched/skipped/failed`，中断后自动恢复。完成后自动清理。
6. 去重：复用 `utils/dedup.py` 的 `mpweixin` 平台索引，与搜狗搜索、单篇抓取共享。
7. 逐篇抓取：每篇文章在新标签页打开，复用 `evaluate_wechat_article()` + `_html_to_markdown()` 提取全文。
8. 输出目录：`{OUTPUT_DIR}/mpweixin/account/{公众号名}/`。
9. API title fallback：当浏览器页面提取 title 为空时，使用 API 返回的 title 作为回退。

**cgiDataNew 元数据管线**
1. `browser.py` 的 JS evaluate 新增第 8 段：提取 `window.cgiDataNew.user_info.appmsg_bar_data` 的 `read_num/old_like_count/like_count/share_count/comment_count`。
2. `_build_wechat_result()` 透传 cgiMetrics → `schema.py` → `storage.py` 条件输出（仅当 reads > 0 时展示）。

**storage.py 优化**
1. 文件名日期截断为仅日期（去掉时间部分 `[:10]`）。
2. WeChat 正文不再重复输出标题 heading。

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/mpweixin_account.py` | 新建 | MP 后台 API 按账号批量抓取（searchbiz + appmsgpublish + 断点续传 + 去重） |
| `feedgrab/cli.py` | 修改 | 新增 `cmd_mpweixin_account()` + `mpweixin-id` 命令路由 |
| `feedgrab/config.py` | 修改 | 新增 `mpweixin_id_since()` + `mpweixin_id_delay()` |
| `feedgrab/fetchers/browser.py` | 修改 | 新增 cgiDataNew 元数据提取（JS evaluate 第 8 段） |
| `feedgrab/schema.py` | 修改 | `from_wechat()` 新增 reads/likes/wow/shares/comments 字段 |
| `feedgrab/utils/storage.py` | 修改 | WeChat 文件名日期截断 + 正文去标题 + 条件性互动指标输出 |
| `.env.example` | 修改 | 新增 `MPWEIXIN_ID_SINCE` + `MPWEIXIN_ID_DELAY` 配置 |

### 验证结果
- `MPWEIXIN_ID_SINCE=2025-08-01 feedgrab mpweixin-id "饼干哥哥AGI"` 实测：
  - Session 加载 + 账号搜索 + fakeid 获取 ✅
  - 文章分页枚举（每页 5 篇） ✅
  - 逐篇打开 + 全文提取 + Markdown 保存 ✅
  - 去重索引更新 ✅
  - 日期过滤停止 ✅
  - 已成功抓取 48+ 篇文章到 `mpweixin/account/饼干哥哥AGI/` ✅
  - 发现并修复 title fallback bug（部分文章 title 为空导致文件名异常） ✅

### 状态：已完成 ✅

---

## 2026-03-06 · v0.9.1 · 微信公众号抓取增强（元数据 + markdownify + 图片防盗链）

### 背景
feedgrab 的微信公众号单篇抓取（`wechat.py`）浏览器回退路径使用通用 `innerText` 提取，丢失了标题层级、图片、链接等富文本结构，也无法提取封面图、发布日期、摘要等元数据。与 `wechat_search.py` 的深度提取能力存在差距。同时 `_html_to_markdown()` 使用手写正则转换 HTML，不支持表格、有序列表、代码块、h5-h6 等复杂结构。微信图片（mmbiz.qpic.cn）有 Referer 校验，在 Obsidian 中查看会 403。

通过对比分析 [wechat-article-to-markdown](https://github.com/jackwener/wechat-article-to-markdown) 和 [wechat-article-exporter](https://github.com/nichenke/wechat-article-exporter) 两个 GitHub 项目，提取了可融合的技术方案。

### 方案决策（三阶段）

**P0 — 元数据提取 + 路径统一**
1. `browser.py` 新增 `WECHAT_ARTICLE_JS_EVALUATE`：在 JS 层面一次性提取 `#activity-name`（标题）、`#js_name`（作者）、`#publish_time`（发布时间）、`og:image`（封面）、`og:description`（摘要）、`#js_tags`（标签）、`#js_view_source`（原文链接）、`create_time`（三层正则从 JS 脚本提取精确时间戳）、`msg_cdn_url`（高质量封面图）、`#js_content innerHTML`（富文本 HTML）。
2. `browser.py` 新增 `_build_wechat_result()` + `evaluate_wechat_article()` 统一处理函数。
3. `wechat.py` Tier 2 从通用 `fetch_via_browser()` 改为 `evaluate_wechat_article()` + `_html_to_markdown()`，与 `wechat_search.py` 共享同一套提取逻辑。
4. `schema.py` `from_wechat()` 更新：cover_image 优先级（文章页 > 搜狗缩略图）、tags 支持、新增 extra 字段。
5. `storage.py` 修复 WeChat `cover_image` 重复输出。

**P1 — markdownify 替换正则转换器**
1. `wechat_search.py` 的 `_html_to_markdown()` 重写：markdownify + BeautifulSoup 预处理。
2. `_preprocess_wechat_html()` 处理：lazy image（data-src→src）、SVG/tracking pixel 过滤、WeChat `.code-snippet__fix` 代码块（占位符策略）、噪音元素移除。
3. 移除旧的 `_WECHAT_EXTRACT_JS` 和 `_strip_tags()` 正则转换器。

**P2 — 图片防盗链修复**
1. `storage.py` 为 WeChat 文章在 front matter 后插入 `<meta name="referrer" content="no-referrer">`，让 Obsidian/浏览器不发送 Referer，避免 mmbiz.qpic.cn 图片 403。

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/browser.py` | 修改 | 新增 `WECHAT_ARTICLE_JS_EVALUATE` + `_build_wechat_result()` + `evaluate_wechat_article()` |
| `feedgrab/fetchers/wechat.py` | 修改 | Tier 2 改为 WeChat 专用提取（evaluate_wechat_article + _html_to_markdown） |
| `feedgrab/fetchers/wechat_search.py` | 修改 | `_html_to_markdown()` 重写（markdownify + BS4 预处理 + 代码块占位符） |
| `feedgrab/schema.py` | 修改 | `from_wechat()` 更新 cover_image 优先级 + tags + 新 extra 字段 |
| `feedgrab/utils/storage.py` | 修改 | WeChat cover_image 去重 + no-referrer meta 标签 |
| `pyproject.toml` | 修改 | 新增 `wechat` 依赖组（markdownify + beautifulsoup4） |

### 验证结果
- 单篇抓取 `mp.weixin.qq.com/s/ng_0-madiZ2eiXBU2dTNgQ`：
  - 标题 "给OpenClaw开天眼！解决了10个跨境电商网站爬虫难题" ✅
  - 作者 "饼干哥哥AGI" ✅
  - 发布日期 "2026-03-03 19:02"（create_time JS 提取） ✅
  - 封面图 msg_cdn_url 高质量 ✅
  - 摘要 "解决90%跨境数据抓取问题。"（og:description） ✅
  - 富文本保留：##/### 标题层级、超链接、图片、有序/无序列表 ✅
  - cover_image 不再重复 ✅
  - no-referrer meta 标签已插入 ✅

### 状态：已完成 ✅

---

## 2026-03-06 · v0.9.0 · curl_cffi TLS 指纹 + 搜狗浏览器统一

### 背景
feedgrab 的 HTTP 请求使用标准 `requests` 库，Python 默认 TLS 指纹（JA3/JA4）与真实浏览器差异明显，服务端可在 TLS 握手阶段直接识别为机器流量。此外，搜狗微信搜索存在 HTTP 搜索→浏览器抓取的"指纹分裂"——搜索用 urllib（Python TLS），抓取用 Playwright（Chrome TLS），两阶段指纹不一致。

### 方案决策
1. **统一 HTTP 客户端**（`utils/http_client.py`）：curl_cffi `Session(impersonate="chrome")` 模拟 Chrome TLS 指纹（JA3/JA4 完全匹配），fallback 到标准 requests。连接复用（persistent session）。异常兼容层将 curl_cffi 异常重新包装为 `requests.Timeout`/`requests.ConnectionError`/`requests.RequestException`。`raise_for_status()` 辅助函数确保 curl_cffi Response 的状态码异常也被包装为 `requests.HTTPError`。
2. **全量迁移**：9 个文件的 `requests.get()`/`requests.post()`/`urllib.request.urlopen()` 全部迁移到 `http_client.get()`/`http_client.post()`，异常处理代码无需改动。
3. **搜狗搜索浏览器统一**：`fetch_content=True` 时搜索也走浏览器（获取 Cookie + 提取结果一步完成），消除 HTTP↔浏览器指纹分裂。HTTP 模式仅在浏览器不可用时兜底。

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/utils/http_client.py` | 新建 | 统一 HTTP 客户端：curl_cffi TLS 指纹 → requests fallback + 异常兼容 + raise_for_status |
| `feedgrab/fetchers/jina.py` | 修改 | 2 个 `requests.get` → `http_client.get` |
| `feedgrab/fetchers/bilibili.py` | 修改 | 1 个 `requests.get` → `http_client.get` |
| `feedgrab/fetchers/twitter.py` | 修改 | 2 个 `requests.get`（Syndication + oEmbed）→ `http_client.get` |
| `feedgrab/fetchers/twitter_fxtwitter.py` | 修改 | `urllib.request.urlopen` → `http_client.get` + 异常处理重写 |
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 3 个 `requests.get`（GraphQL + JS bundle）→ `http_client.get` |
| `feedgrab/fetchers/twitter_api.py` | 修改 | 1 个 `requests.get`（付费 API）→ `http_client.get` |
| `feedgrab/fetchers/wechat_search.py` | 修改 | `urllib.request.urlopen` → `http_client.get` + 浏览器搜索统一 |
| `feedgrab/fetchers/youtube.py` | 修改 | 1 个 `requests.post`（Whisper）→ `http_client.post` |
| `pyproject.toml` | 修改 | `curl_cffi>=0.7` 加入 stealth/all 依赖组 |

### 验证结果
- curl_cffi 引擎正确启用：UA 显示 Chrome/142.0.0.0
- Jina Reader 端到端测试：200 OK，内容正确
- `raise_for_status` 兼容性：404 响应正确抛出 `requests.HTTPError`
- 所有 9 个模块导入无错误
- 本地 CDP 连接（twitter_cookies.py）保持原样不迁移

### 状态：已完成 ✅

## 2026-03-06 · v0.8.4 · Referer 伪装 + 资源拦截

### 背景
浏览器导航无 referer（从 about:blank 直接访问目标站），服务端可轻易识别为机器流量。批量抓取时加载了所有字体、媒体、tracking 脚本，浪费带宽且拖慢速度。

### 方案决策
1. **Referer 伪装**（adapted from Scrapling `fingerprints.py`）：根据目标 URL 域名自动生成搜索引擎 referer — 中国平台→百度、其他→Google。短子域名（en/mp/m/api）自动跳过取主域名。仅首次导航设置，后续页面间导航由浏览器自动携带前一页 URL。
2. **资源拦截**（adapted from Scrapling `navigation.py`）：在 context 级别通过 `route("**/*")` 拦截 7 类非必要资源（font/media/beacon/websocket/manifest/texttrack/eventsource）+ 11 个 tracking 域名（Google Analytics/GTM/Facebook/Hotjar/Sentry 等）。保留 image/stylesheet/xhr 确保 SPA 渲染和内容提取不受影响。

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/browser.py` | 修改 | 新增 `generate_referer()` + `setup_resource_blocking()` + `fetch_via_browser()` 应用两者 |
| `feedgrab/fetchers/xhs_user_notes.py` | 修改 | context 级资源拦截 + 首次导航 referer |
| `feedgrab/fetchers/xhs_search_notes.py` | 修改 | context 级资源拦截 + 首次导航 referer |
| `feedgrab/fetchers/wechat_search.py` | 修改 | context 级资源拦截 + 首次导航 referer |

### 验证结果
- Referer 生成正确：XHS/微信→百度，通用→Google，子域名（en/mp/m）正确跳过
- 端到端测试（sspai.com）：81 请求通过 + 7 请求拦截（6 字体 + 1 tracking 脚本），页面正常提取
- 所有 4 个模块导入无错误

### 状态：已完成 ✅

## 2026-03-06 · v0.8.3 · browserforge 浏览器指纹一致性

### 背景
feedgrab 各 HTTP 模块的 User-Agent 和请求头严重不一致：jina.py 使用 `feedgrab/0.1`（直接暴露工具身份），twitter_fxtwitter.py 和 twitter.py 使用极简的 `Mozilla/5.0`，wechat_search.py 使用另一个独立的完整 UA 但缺少 Sec-Ch-Ua 配套。这些不一致容易被服务器识别为非真实浏览器流量。

### 方案决策
引入 browserforge 库生成完整且内部一致的浏览器请求头集合（UA + sec-ch-ua + Accept + Sec-Fetch 等 11 项），缓存到会话级别。

关键设计：
- **版本号精确匹配**：从 `BROWSER_USER_AGENT` 环境变量提取 Chrome 版本号，browserforge 按该版本 pin 生成匹配的 sec-ch-ua，确保 `Chrome/132` 对应 `"Google Chrome";v="132"`
- **OS 自动检测**：`platform.system()` → browserforge OS 参数，header 与实际运行平台一致
- **分层使用**：API 调用（Jina/FxTwitter/Syndication）仅统一 UA，HTML 页面请求（搜狗）用全套 header
- **优雅降级 + 提示**：browserforge 未安装时 loguru WARNING 输出安装指导，降级为基础 header

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/config.py` | 修改 | 新增 `get_stealth_headers()` — browserforge 全套一致 header 生成 + 会话缓存 + 降级提示 |
| `feedgrab/fetchers/jina.py` | 修改 | UA: `feedgrab/0.1` → `get_user_agent()` |
| `feedgrab/fetchers/twitter_fxtwitter.py` | 修改 | UA: `Mozilla/5.0` → `get_user_agent()` + 新增 import |
| `feedgrab/fetchers/twitter.py` | 修改 | Syndication UA: `Mozilla/5.0` → `get_user_agent()` |
| `feedgrab/fetchers/wechat_search.py` | 修改 | 3 个硬编码 header → `get_stealth_headers()` 全套 11 个 |
| `pyproject.toml` | 修改 | `browserforge>=1.1` 加入 stealth/all 依赖组 |

### 验证结果
- browserforge 生成 11 项一致 header，UA Chrome/132 与 sec-ch-ua "Google Chrome";v="132" 精确匹配
- BROWSER_USER_AGENT 环境变量覆盖（Chrome/133）→ sec-ch-ua 自动匹配 v="133"
- 浏览器 context UA、HTTP header UA、config UA 三处完全一致
- 所有 6 个修改模块导入无错误
- browserforge 未安装时正确输出 WARNING 提示及安装命令

### 状态：已完成 ✅

---

## 2026-03-06 · v0.8.2 · 隐身浏览器引擎升级（patchright + stealth flags）

### 背景
feedgrab 的 Playwright 浏览器抓取方案反检测能力极弱——仅有一条 `--disable-blink-features=AutomationControlled` 启动参数，几乎等于"裸奔"。小红书（反爬最严格）和搜狗微信搜索容易被识别为自动化流量。

通过深度分析 [Scrapling](https://github.com/D4Vinci/Scrapling) 项目的反检测技术方案，发现其 patchright + stealth flags + 环境伪装的组合方案投入产出比极高，可以精准移植到 feedgrab。

### 方案决策
- **不直接替换 playwright**，而是 patchright 前置为 Tier 1、playwright 兜底为 Tier 3（间隔编号预留扩展空间）
- **集中管理隐身配置**：在 `browser.py` 新增统一的 stealth 工具函数，所有 fetcher 共享
- 从 Scrapling 适配 52 条 Chrome 隐身启动参数 + 5 条有害默认参数屏蔽
- 浏览器 context 补全环境伪装（viewport/screen/locale/color_scheme/device_scale_factor 等）
- `pyproject.toml` 新增 `stealth` 可选依赖组

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/browser.py` | 重写 | 新增 stealth 引擎模块（双引擎选择 + 52 条启动参数 + context 反指纹配置 + stealth_launch/get_stealth_context_options 工具函数）；重写 fetch_via_browser 使用新引擎 |
| `feedgrab/fetchers/xhs_user_notes.py` | 修改 | 替换为 stealth 引擎，移除硬编码 playwright import 和旧参数 |
| `feedgrab/fetchers/xhs_search_notes.py` | 修改 | 同上 |
| `feedgrab/fetchers/wechat_search.py` | 修改 | 同上 |
| `pyproject.toml` | 修改 | 新增 `stealth = ["patchright>=1.0"]` 可选依赖 |

### 验证结果
- patchright 引擎自动检测：`get_stealth_engine_name()` → `"patchright"` ✅
- 未安装 patchright 时自动降级：→ `"playwright"` ✅
- 通用网页（少数派）：headless 模式，3,043 字符 ✅
- 通用网页（Wikipedia）：headless 模式，84,341 字符 ✅
- XHS 小红书：自动切换 headed 模式 + session 加载 ✅
- 所有文件语法检查通过 ✅

### 状态：已完成 ✅

---

## 2026-03-06 · v0.8.1 · 搜狗微信搜索增强（mpweixin 目录 + 配置开关 + 多页 + 富文本）

### 背景
v0.8.0 搜狗微信搜索功能上线后的实测反馈：
1. 输出目录 `WeChat/search/` 不够明确（WeChat 易与个人微信混淆），需改为 `mpweixin/search_sogou/{keyword}/`
2. 默认 10 篇太少，需要多页抓取支持和配置开关
3. 正文通过 Jina/browser.py 通用链抓取时丢失所有格式（只有 `innerText` 纯文本），需要富文本 Markdown
4. 搜索元数据（公众号名、日期、缩略图、摘要）未保存到 md 文件
5. Sogou antispider 拦截 headless 浏览器和直接 HTTP 跳转

### 方案决策
- **目录重命名**：`WeChat/` → `mpweixin/`，搜索子目录 `search_sogou/{keyword}/`
- **配置开关**：`MPWEIXIN_SOGOU_ENABLED`（默认 false）、`MPWEIXIN_SOGOU_MAX_RESULTS`（默认 10，上限 100）、`MPWEIXIN_SOGOU_DELAY`
- **多页搜索**：`_sogou_search_multi()` 按需翻页（每页 10 条，搜狗最多约 10 页）
- **反爬绕过**：headed 浏览器 + 先访问搜索页获取 Cookie → 从同 context 新标签页访问跳转链接
- **富文本**：直接用已有浏览器实例提取 `#js_content` HTML，自定义 `_html_to_markdown()` 转换（h1-h4/bold/italic/img data-src/link/list/blockquote）
- **元数据完整**：`from_wechat()` 新增 `extra` 传递 publish_date/thumbnail/summary/search_keyword → front matter + 正文开头封面图

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/wechat_search.py` | 重写 | 多页搜索 + 浏览器直提 HTML→Markdown + 反爬 Cookie 策略 |
| `feedgrab/config.py` | 修改 | 新增 `mpweixin_sogou_*` 配置函数 |
| `feedgrab/cli.py` | 修改 | 配置开关检查 + `--limit` 覆盖 |
| `feedgrab/schema.py` | 修改 | `from_wechat()` 传递搜索元数据 + 封面图前置 |
| `feedgrab/utils/storage.py` | 修改 | WeChat→mpweixin 目录 + front matter 元数据 + 文件名带公众号+日期 |
| `.env.example` | 修改 | 新增 `MPWEIXIN_SOGOU_*` 配置项文档 |

### 验证结果
- `MPWEIXIN_SOGOU_ENABLED=true feedgrab mpweixin-so openclaw --limit 2` → 2/2 成功
- 输出路径：`mpweixin/search_sogou/openclaw/人人都是产品经理_2026-02-17：OpenClaw 被 OpenAI 收购了。.md`
- 富文本：**加粗**、*斜体*、`![image](mmbiz.qpic.cn/...)` 图片、段落分隔均正确
- front matter：title/source/author/published/thumbnail/summary/search_keyword 完整
- 未启用时：`feedgrab mpweixin-so xxx` 提示 "Set MPWEIXIN_SOGOU_ENABLED=true"

### 状态：已完成 ✅

---

## 2026-03-06 · v0.8.0 · FxTwitter Tier 0.3 兜底 + 搜狗微信搜索

### 背景
1. 分析了 [x-tweet-fetcher](https://github.com/ythx-101/x-tweet-fetcher) 项目，发现其核心数据源为 FxTwitter 公共 API（`api.fxtwitter.com`），无需认证即可获取丰富的推文数据，完整度显著高于 Syndication。
2. 搜狗微信搜索（`weixin.sogou.com`）可按关键词发现微信公众号文章，补充现有的单篇微信抓取能力。

### 方案决策
- **FxTwitter Tier 0.3**：插入 GraphQL（Tier 0）和 Syndication（Tier 0.5）之间，数据完整度接近 GraphQL（有 views/bookmarks/Article Draft.js），缺少 blue_verified/listed_count/线程展开。单篇失败直接降级；批量模式连续 3 次失败触发 circuit breaker，当前任务后续跳过 FxTwitter。
- **搜狗微信搜索**：新增 `feedgrab mpweixin-so <keyword>` 命令，通过搜狗搜索发现文章 → Playwright 解析加密跳转 → 复用现有 wechat.py 抓取全文 → 去重保存。
- **六级兜底链**：GraphQL → FxTwitter → Syndication → oEmbed → Jina → Playwright

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_fxtwitter.py` | 新建 | FxTwitter API 客户端 + circuit breaker + Article Draft.js 渲染 |
| `feedgrab/fetchers/wechat_search.py` | 新建 | 搜狗微信搜索（HTML 解析 + Playwright 跳转解析 + 批量抓取） |
| `feedgrab/fetchers/twitter.py` | 修改 | 插入 Tier 0.3 FxTwitter 兜底层 |
| `feedgrab/cli.py` | 修改 | 新增 `mpweixin-so` 命令 + 帮助文本 |
| `feedgrab/fetchers/twitter_bookmarks.py` | 修改 | 任务启动时 reset circuit breaker |
| `feedgrab/fetchers/twitter_user_tweets.py` | 修改 | 任务启动时 reset circuit breaker |
| `feedgrab/fetchers/twitter_list_tweets.py` | 修改 | 任务启动时 reset circuit breaker |
| `feedgrab/fetchers/twitter_api_user_tweets.py` | 修改 | 任务启动时 reset circuit breaker |
| `强子笔记/x-tweet-fetcher技术方案分析.md` | 新建 | x-tweet-fetcher 架构分析报告 |
| `强子笔记/FxTwitter与搜狗微信搜索评估报告.md` | 新建 | FxTwitter + 搜狗微信搜索数据完整度评估 |

### 验证结果
- FxTwitter API 实测：普通推文、Article 长文数据完整返回（views/bookmarks/Article Draft.js blocks）
- 搜狗微信搜索实测："openclaw" 返回 10 条结果（标题/摘要/公众号名/时间戳/缩略图）
- 搜狗跳转链接 HTTP 直请求触发反爬 → 改用 Playwright 浏览器解析
- Circuit breaker 逻辑验证通过（3 次连续失败后停用 FxTwitter）

### 状态：已完成 ✅

---

## 2026-03-05 · v0.7.1 · tweet_type 分类 + 日期解析修复

### 背景
1. 需要在 Obsidian 中通过元数据字段筛选不同类型的推文（普通/线程/长文），新增 `tweet_type` 字段。
2. berryxia 的长文缺少 `published` 发布时间，而同类型的长文正常。排查发现 `parse_twitter_date_local()` 的 ISO 8601 格式检测使用 `"T" in created_at`，会误匹配星期名称中的 `T`（如 `Tue`、`Thu`），导致走错分支解析失败。

### 方案决策
- **tweet_type 分类**：在 `from_twitter()` 中根据 `is_article`/线程长度判定 `status`/`thread`/`article`，输出到 front matter
- **日期解析修复**：将 `"T" in created_at` 改为 `re.search(r"\d{4}-\d{2}-\d{2}T", created_at)` 精确匹配 ISO 8601 的 `YYYY-MM-DDT` 模式

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/config.py` | 修改 | 修复 ISO 8601 日期检测误匹配 `Tue`/`Thu` 中的 `T` |
| `feedgrab/schema.py` | 修改 | `from_twitter()` 新增 `tweet_type` 分类逻辑 |
| `feedgrab/utils/storage.py` | 修改 | front matter 输出 `tweet_type` 字段 |

### 验证结果
- berryxia 长文 `published: 2026-03-03` 正确输出（修复前为空）
- 所有日期格式（`Tue`/`Thu`/`Wed`/`Fri`/ISO 8601）均正确解析
- 三种类型的推文 front matter 字段完整一致

### 状态：已完成 ✅

---

## 2026-03-05 · v0.7.0 · GraphQL 数据完整提取 + 引用推文增强 + 富文本标记

### 背景
系统分析 GraphQL 返回的完整数据后，发现大量有价值数据未被提取：1）引用推文只拿到截断的 280 字 `full_text`，丢失完整长文、图片、视频；2）note_tweet 的 `richtext_tags`（粗体/斜体标记）完全忽略；3）作者信息（粉丝数、蓝标认证、发推数等）和推文元数据（被引用次数、语言、发布客户端等）未保存到 front matter。

### 方案决策
- **引用推文完整提取**：从 `quoted_status_result` 中提取 `note_tweet.text`（完整长文不截断）、展开 t.co 链接、提取图片/视频/互动指标，渲染为完整 blockquote
- **richtext_tags 转 Markdown**：`_apply_richtext_tags()` 将 Draft.js 索引式标记转换为 `**bold**`/`*italic*`，从末尾向前插入避免索引偏移
- **新增 8 个 front matter 字段**：`quotes`、`is_blue_verified`、`followers_count`、`statuses_count`、`listed_count`、`lang`、`source_app`、`possibly_sensitive`
- **title 净化**：`_clean_title()` 剥离 Markdown 格式标记，确保 title 是纯文本

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 新增 `_apply_richtext_tags()`、`_parse_source_app()`；`extract_tweet_data()` 增加 8 个新字段 + 引用推文完整提取（长文+媒体+指标+t.co展开+richtext） |
| `feedgrab/fetchers/twitter.py` | 修改 | `_fetch_via_graphql()` 两个分支透传新字段；`_clean_title()` 剥离 `**` 标记 |
| `feedgrab/schema.py` | 修改 | 新增 `_render_quoted_tweet()` 完整引用渲染（含图片/视频/URL）；`from_twitter()` extra 透传新字段 |
| `feedgrab/utils/storage.py` | 修改 | front matter 输出 8 个新元数据字段 |

### 验证结果
- `@binghe/status/2003639692542247190`（21条线程）：`AI**漫剧创业**` 粗体正确渲染；title 纯文本无 `**`；front matter 含 `is_blue_verified: true`、`followers_count: 40173`、`quotes: 2` 等完整元数据
- `@iBigQiang/status/2015088004109615266`（带引用推文）：引用推文完整长文+2张图片+t.co展开+作者URL全部到位；旧版仅一行截断文本

### 状态：已完成 ✅

---

## 2026-03-05 · v0.6.2 · Twitter Article GraphQL 原生渲染

### 背景
抓取 Twitter Article（长文）时，正文通过 Jina Reader 抓取 `/article/` 页面获得。但 Jina 的 Markdown 渲染器会丢掉 cashtag 链接（`$MODEL`、`$BASE_URL`）和 mention 链接（`@username`），导致保存的 Markdown 文件正文内容不完整。

### 方案决策
- **根因分析**：GraphQL API 的 `article.article_results.result.content_state` 已经包含完整的 Article 正文（Draft.js 富文本格式），之前未解析利用，错误地走了 Jina 抓取
- **GraphQL 原生渲染**：新增 `_render_article_body()` 将 Draft.js `content_state.blocks` 直接渲染为 Markdown，支持段落、标题、有序/无序列表、代码块、图片、引用块
- **零额外请求**：Article 正文数据随 TweetDetail GraphQL 请求一起返回，本地解析即可，不需要任何额外网络请求
- **Jina 降级为 fallback**：仅在 Syndication tier（无 content_state）时才走 Jina 抓取
- **Jina 空洞修补**：为 Jina fallback 路径新增 `_patch_jina_hollows()` — 检测 Markdown 中被丢掉的 cashtag/mention 空洞，用 Jina text 模式（`X-Return-Format: text`）修补

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 新增 `_render_article_body()` Draft.js → Markdown 渲染器；`_extract_article_ref()` 新增 `body` 字段 |
| `feedgrab/fetchers/twitter.py` | 修改 | `_try_fetch_article_body()` 优先用 GraphQL body，Jina 降为 fallback |
| `feedgrab/fetchers/jina.py` | 修改 | 新增 `fetch_via_jina_text()` 纯文本模式获取 |
| `feedgrab/fetchers/twitter_bookmarks.py` | 修改 | 新增 `_detect_hollows()` 和 `_patch_jina_hollows()` 空洞检测修补 |

### 验证结果
- 测试推文：`xiangxiang103/status/2029137537621737817`（含 PowerShell 代码块、`$MODEL` cashtag、`@username` mention 的 Article）
- `$MODEL`、`$BASE_URL`、`$API_KEY`：全部完整保留
- `@LawrenceW_Zen`、`@innomad_io`：全部完整保留
- 代码块（146 行 PowerShell）：格式正确
- 5 张内嵌图片 + cover image：全部输出
- 3 个 H2 标题 + 有序列表：格式正确
- 零 Jina 网络请求，耗时显著减少

### 状态：已完成 ✅

---

### 背景
保存的 Markdown 文件中，当 likes/bookmarks/replies 等指标值为 0 时会被省略，导致元数据缺失和"值为 0"无法区分，影响 Obsidian Dataview 查询准确性。

### 方案决策
- **全量输出指标**：Twitter（likes/retweets/replies/bookmarks/views）和小红书（likes/collects/comments）的指标无条件输出，包括值为 0 的字段
- **保持纯英文 key**：评估了中英双语 key 方案（如 `喜欢_likes`），因 Dataview 兼容性问题决定不采用，改为待实现的 Obsidian CSS/Types 用户侧方案

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/utils/storage.py` | 修改 | Twitter/XHS 指标去掉 `if val` 判断，全量输出 |
| `DEVLOG.md` | 修改 | 新增"待实现计划"区块（Obsidian 中文别名方案） |

### 状态：已完成 ✅

---

## 待实现计划

### Obsidian 元数据中文别名方案

**背景**：front matter key 保持纯英文（`likes`、`bookmarks`）以确保 Dataview 等插件兼容性，但中文用户阅读时希望能直观看到中文含义。

**方案**：通过 Obsidian 用户侧配置实现，不修改代码：
1. **CSS snippet** — 给 Properties 面板的 key 加中文 tooltip 或替换显示文字
2. **Obsidian Types** — 利用属性类型系统给 key 设置中文别名

**状态**：待实现。确定方案后编写用户教程，写入 README 文档。

---

## 2026-03-04 · v0.6.0 · Twitter List 列表批量抓取 + 目录结构优化

### 背景
用户订阅了 Twitter List（如 AI KOL 列表），希望定期批量抓取列表中最近 N 天的推文。同时优化所有批量模式的输出目录结构，使其更清晰易管理。

### 方案决策
- **GraphQL API**：复用 `ListByRestId`（列表元数据）+ `ListLatestTweetsTimeline`（列表推文分页），动态 queryId 解析 + 硬编码 fallback
- **日期过滤**：`X_LIST_TWEETS_DAYS`（默认1天），代码层按 `parse_twitter_date_local()` 过滤
- **会话去重**：预扫描 `conversation_id` 计数，多条目会话只处理根推文（`conv_id == tweet_id`），根推文自动升级为 thread 类型走 GraphQL 深度抓取
- **输出目录三层结构**：`lists_{N}day/{YYYYMMDD}/{列表名}/`，同一天的多个列表聚合在日期目录下
- **全模式目录优化**：用户推文 `status_author/{昵称}/`、书签 `bookmarks/{名称}/`、全部书签 `bookmarks/all/`
- **clean-index 命令**：清理索引目录中的批量记录和断点缓存，保留全局去重索引
- **Article 误判修复**：收紧 stub 判定（去掉 t.co 后剩余 < 30字符），防止正常推文误走 Jina
- **emoji SVG 过滤**：`_format_markdown()` 统一过滤 `abs-0.twimg.com/emoji/` 图片标签

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/config.py` | 修改 | 新增 List 相关配置函数 |
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 新增 List GraphQL API 支持 |
| `feedgrab/fetchers/twitter_list_tweets.py` | **新建** | List 批量抓取主逻辑 |
| `feedgrab/fetchers/twitter_user_tweets.py` | 修改 | 输出目录改为 `status_author/{昵称}` |
| `feedgrab/fetchers/twitter_api_user_tweets.py` | 修改 | 输出目录改为 `status_author/{昵称}` |
| `feedgrab/fetchers/twitter_bookmarks.py` | 修改 | 输出目录改为 `bookmarks/{名称}` |
| `feedgrab/fetchers/twitter.py` | 修改 | 收紧 article stub 判定逻辑 |
| `feedgrab/utils/storage.py` | 修改 | emoji SVG 图片过滤 |
| `feedgrab/reader.py` | 修改 | URL 路由识别 `/i/lists/` |
| `feedgrab/cli.py` | 修改 | 新增 `clean-index` 命令 + List 批量模式 |
| `.env.example` | 修改 | 新增 List 配置项文档 |

### 验证结果
- "中推圈AI KOL" 列表：190 条推文成功抓取
- "虚拟资源" 列表：31 条条目 → 28 条保存 + 3 条会话去重跳过，0 重复文件
- 目录结构：`lists_1day/20260304/软件工具/`、`bookmarks/OpenClaw/`、`status_author/Geek/` 验证通过
- `feedgrab clean-index --yes`：42 个文件 28.7MB 清理成功
- Article 误判修复：含链接的正常推文不再误走 Jina

### 状态：已完成 ✅

---

## 2026-03-04 · v0.5.2 · Syndication API 作为 Tier 0.5 兜底

### 背景
Twitter 有一个免费、无需认证的 Syndication API（`cdn.syndication.twimg.com`），数据比 oEmbed 丰富得多（含媒体 URL、互动指标、用户信息）。作为 GraphQL 和 oEmbed 之间的降级兜底层，在 Cookie 过期/限流时仍能获取 80% 的数据。

### 方案决策
- **端点**：`https://cdn.syndication.twimg.com/tweet-result?id={tweetId}&token={token}`
- **Token 计算**：`((id / 1e15) * Math.PI).toString(36).replace(/(0+|\.)/g, '')`（参考 yt-dlp 和 Vercel react-tweet 逆向实现）
- **五级兜底**：Tier 0 GraphQL → **Tier 0.5 Syndication** → Tier 1 oEmbed → Tier 2 Jina → Tier 3 Playwright
- **数据能力**：文本、图片、视频、hashtags、likes、replies、article 检测（缺 retweets/bookmarks/views）
- **Article 检测**：Syndication 返回 article 字段时，复用 `_try_fetch_article_body()` 走 Jina 获取正文
- **cover_image 增强**：article cover > 显式 cover_image > 首张图片（三级回退）
- **日期解析**：`parse_twitter_date_local()` 新增 ISO 8601 支持（Syndication 返回 `2022-10-28T03:49:11.000Z` 格式）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter.py` | 修改 | 新增 `_fetch_via_syndication()`、`_syndication_token()`；提取 `_try_fetch_article_body()` 公共函数；调度器插入 Tier 0.5 |
| `feedgrab/config.py` | 修改 | `parse_twitter_date_local()` 新增 ISO 8601 格式支持 |
| `feedgrab/schema.py` | 修改 | `from_twitter()` cover_image 三级回退逻辑 |

### 验证结果
- Syndication API 成功获取推文文本、图片、视频 URL、互动数据
- Article 推文正确检测并通过 Jina 获取完整正文
- Token 计算与 yt-dlp/react-tweet 实现一致
- 有 Cookie 时正常走 GraphQL，Syndication 作为 GraphQL 失败后的第一降级层

### 参考
- [Vercel react-tweet](https://github.com/vercel/react-tweet) — Token 计算源码
- [yt-dlp PR #12107](https://github.com/yt-dlp/yt-dlp/pull/12107) — Python 端 Token 计算实现

### 状态：已完成 ✅

---

## 2026-03-04 · v0.5.1 · 修复 API 补充搜索操作符不可靠问题

### 背景
v0.5.0 的 API 补充抓取在 dontbesilent 等账号测试中返回 0 条推文。经三轮诊断发现 TwitterAPI.io 的搜索操作符全部不可靠：
- `until:` 超过 1 天前的日期 → 返回 0 条
- `since:` 截断结果（1514 条只返回 353 条）
- `max_id` 直接跳转到历史 ID → 返回 0 条
- 只有增量 `max_id`（从上一页最小 ID 递减）能正常工作

### 方案决策
**移除所有搜索操作符，改为纯代码层过滤**：
- 查询只用 `from:{screen_name}` + 增量 `max_id`（从最新向最旧翻页）
- `since_date` 过滤在代码中完成（解析每条推文的 `created_at`）
- 连续 3 页全部早于 `since_date` → 检测为搜索索引空洞，自动停止
- `initial_max_id` 参数标记为忽略（直接跳转不可行）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_api_user_tweets.py` | 修改 | 重写 `_discover_tweets_via_search()` 分页循环：移除 `since:`/`until:` 操作符，增加代码层日期过滤和索引空洞检测 |

### 验证结果
- **vista8 (向阳乔木)**：GraphQL 855 条 + API 补充 4211 条（217 页），`since_date=2025-01-01` 代码层过滤正确，在连续 3 页早于目标日期后自动停止
- **dontbesilent**：发现 345 条（搜索索引在近期推文和 2019 年之间有空洞，属 API 端限制）
- 搜索索引完整的账号补充抓取完全正常

### 状态：已完成 ✅

---

## 2026-03-03 · v0.5.0 · TwitterAPI.io 付费 API 接入 + Cookie 轮换 + 断点续传

### 背景
feedgrab 按账号批量抓取 X/Twitter 的两阶段方案（GraphQL UserTweets ~800 条 + Playwright 浏览器搜索补充）存在三个瓶颈：
1. **浏览器搜索不适合服务器部署**：Playwright 依赖有头浏览器，无法在无 GUI 服务器运行
2. **GraphQL 429 限流**：大量推文需要逐条 GraphQL 调用，单账号容易被限流
3. **中断丢失**：发现阶段全在内存，中途崩溃（如 API 401/网络断开）所有数据丢失

### 方案决策

#### 1. TwitterAPI.io 付费 API（替代浏览器搜索补充）
- **Advanced Search API**：`$0.15/千条`，支持 `from:user since:date` 等高级搜索语法
- **两种接入模式**：
  - `X_API_PROVIDER=graphql`（默认）：GraphQL 主流程 + API 补充（替代浏览器搜索）
  - `X_API_PROVIDER=api`：全量走付费 API（服务器部署，无需 Cookie）

#### 2. max_id 分页（而非 cursor 分页）
实测 TwitterAPI.io 的 cursor 分页对大账号不完整（op7418 2.3 万推文只返回 130 条），而 `max_id:{last_id - 1}` 写在搜索查询中的 ID 分页方案返回完整结果：

| 分页方式 | dontbesilent (1497条) | op7418 (23000条) |
|---------|----------------------|------------------|
| cursor | 178 条 (12%) | 130 条 (0.6%) |
| **max_id** | **1497 条 (100%)** | **8889 条** |

#### 3. Smart Direct Save（智能直保）
`X_API_SAVE_DIRECTLY=true` 时，普通推文直接用 API 数据保存（跳过 GraphQL），仅长文(article)和线程(thread)强制走 GraphQL 获取完整媒体/正文。大幅减少 GraphQL 调用次数和 429 风险。

#### 4. Cookie 多账号轮换
- `sessions/` 目录支持多个 Cookie 文件：`twitter.json`（主）+ `x_2.json` + `x_3.json`...
- GraphQL 429 时自动标记当前账号，下次请求切换到未限流账号
- 15 分钟冷却期后自动恢复

#### 5. 断点续传
- **Phase 1 (发现)**：每页推文实时写入 `.api_discovery_{username}.jsonl` 缓存，中断后从最小 ID 处续传
- **Phase 2 (处理)**：dedup 索引每 50 条自动持久化，重跑时自动跳过已保存推文

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_api.py` | 新建 | TwitterAPI.io HTTP 客户端（重试/退避/认证错误处理） |
| `feedgrab/fetchers/twitter_api_user_tweets.py` | 新建 | API 批量抓取（发现+过滤+处理+断点续传+缓存） |
| `feedgrab/fetchers/twitter_cookies.py` | 修改 | 多账号加载 + 429 轮换（15 分钟冷却自动恢复） |
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 429 时触发 Cookie 轮换标记 |
| `feedgrab/fetchers/twitter_user_tweets.py` | 修改 | 补充触发点加 API 分支（有 API Key → API 补充，否则浏览器搜索） |
| `feedgrab/reader.py` | 修改 | `X_API_PROVIDER=api` 全量 API 路径路由 |
| `feedgrab/config.py` | 修改 | 新增 6 个配置函数（API Key/Provider/Save Mode/互动过滤） |
| `.env.example` | 修改 | TwitterAPI.io 配置段 + Cookie 轮换教程 + F12 获取方法 |
| `sessions/x_2.json` | 新建 | 第二个 Cookie 账号模板文件 |

### 验证结果
- **API 发现**：op7418 测试 449 页 / 8889 条推文，max_id 分页稳定、零间隙
- **Smart Direct Save**：8889 条推文中 ~14% 需要 GraphQL（线程/长文），其余直接 API 保存
- **Cookie 轮换**：2 个账号环境测试，429 后自动切换，轮换逻辑正确
- **断点续传**：缓存 JSONL 实时写入，`is_complete` 标记正常

### 状态：已完成 ✅

---

## 2026-03-02 · v0.4.0 · 浏览器搜索补充抓取 — 突破 UserTweets 800 条限制

### 背景
`feedgrab https://x.com/dontbesilent` 按账号全量抓取受 Twitter UserTweets API 服务端限制，每次最多返回 ~800 条推文。该博主 2025 年全年活跃，但只能抓到 2025-12-08 之后的内容。需要一种补充方案获取更早的历史推文。

### 方案决策

#### 方案演进（3 次迭代）
1. **SearchTimeline GraphQL API 直接调用**（最初方案）— queryId 频繁变化，即使从浏览器 DevTools 获取正确 queryId，请求仍返回 404（URL 编码差异或 headers 校验）
2. **页面 JS 注入拦截 XHR**（第二次尝试）— `page.goto()` 导航后 JS 环境重置，注入的拦截器丢失，无法捕获首批 GraphQL 响应
3. **Playwright `page.on("response")` 事件**（最终方案）— 在 Python 层面注册响应拦截器，跨导航持久有效，捕获所有 SearchTimeline GraphQL 响应

#### 最终架构
```
阶段1: UserTweets GraphQL API（现有，~800条，纯 API 高速）
         ↓ 检测到历史缺口（earliest_tweet_date > X_USER_TWEETS_SINCE）
阶段2: Playwright 浏览器搜索补充（新增）
         → 启动 Chrome + 加载 sessions/twitter.json
         → 预热访问 x.com/home 激活 session
         → 按月分片导航到 x.com/search?q=from:user since:X until:Y
         → page.on("response") 拦截 SearchTimeline GraphQL 响应
         → 自动滚动加载更多 → 解析推文 → 去重 → 保存 Markdown
```

#### 关键设计
- **SearchResponseCollector 类**：Python 层面的响应拦截器，通过 `page.on("response")` 注册，解析 `data.search_by_raw_query.search_timeline.timeline.instructions` 路径
- **Session 预热**：先访问 x.com/home 激活登录态，再导航到搜索页
- **URL 格式**：`urllib.parse.quote()` 编码，不带 `&f=live` 参数（匹配浏览器手动搜索行为）
- **月度分片**：从 UserTweets 最早日期往回按月分片，连续 3 个空月度提前终止
- **去重共享**：两阶段共用同一个 `item_id_url.json` 索引
- **API 格式兼容**：修复 `extract_tweet_data()` 兼容新版 Twitter API（`screen_name`/`name` 从 `user_legacy` 移到 `user_core`）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_search_tweets.py` | 新建 | 浏览器搜索补充模块（SearchResponseCollector + 月度分片 + 滚动采集） |
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | SearchTimeline API 常量/函数 + `extract_tweet_data()` 兼容新版 API 格式 |
| `feedgrab/fetchers/twitter_user_tweets.py` | 修改 | 集成搜索补充调用 + earliest_tweet_date 检测 |
| `feedgrab/config.py` | 修改 | 新增 `x_search_supplementary_enabled()` + `x_search_max_pages_per_chunk()` |
| `.env.example` | 修改 | 新增搜索补充配置说明 |

### 验证结果
- 测试用户 `@dontbesilent`（X_USER_TWEETS_SINCE=2025-01-01）
- 阶段1 UserTweets API：~800 条推文（2025-12-08 ~ 2026-03-01），索引 1003 条
- 阶段2 浏览器搜索补充：处理 774 条，新增 284 条，跳过 490 条（去重），失败 0 条
- 最终索引：1003 → 1290 条，总增 287 条历史推文
- 注意：后期月度分片可能因平台风控返回空结果（非数据缺失）

### 状态：已完成 ✅

---

## 2026-03-01 · v0.3.2 · Article 正文抓取修复 + GraphQL 单篇重试

### 背景
批量抓取 `@dontbesilent` 全量推文时发现两个问题：
1. **Article 正文为垃圾内容**：长文推文（如 `dontbesilent/status/2023370066734338381`）保存的 Markdown 正文是 Twitter 登录页 chrome（"New to X?", "Sign up now"...），而非文章正文。根因是 Jina 通过 `/status/` URL 抓取时返回登录页，垃圾内容 >200 字符通过了长度校验
2. **GraphQL 单篇无重试**：单篇推文的 GraphQL 调用失败（连接重置或 RuntimeError）时直接降级到 oEmbed，丢失元数据（likes/views/bookmarks 等）

### 方案决策

#### Article 正文抓取：垃圾检测 + article URL 优先
- 新增 `_is_jina_garbage()` — 匹配 9 个 Twitter 页面 chrome 特征词，命中 ≥2 个判定为垃圾
- 新增 `_fetch_article_body()` 共享函数 — 优先用 `/article/{id}` URL（从 GraphQL 元数据中的 `article.rest_id` 构建），失败后回退 `/status/` URL，每个 URL 重试 2 次，全程带垃圾检测
- 3 处 article 分支（`twitter.py`、`twitter_bookmarks.py`、`twitter_user_tweets.py`）统一调用共享函数，消除重复代码

#### GraphQL 单篇重试：统一重试循环
- 将 `_fetch_via_graphql` 调用包裹在 try/except 重试循环中（1 次初始 + 3 次重试，间隔 5 秒）
- 同时覆盖"返回空数据"和"抛出 RuntimeError"两种失败模式
- Auth 错误（401/403）不重试，直接抛出到外层降级处理

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_bookmarks.py` | 修改 | 新增 `_is_jina_garbage()` + `_fetch_article_body()` 共享函数；article 分支简化为调用共享函数 |
| `feedgrab/fetchers/twitter_user_tweets.py` | 修改 | article 分支简化为调用 `_fetch_article_body()`；新增 import |
| `feedgrab/fetchers/twitter.py` | 修改 | GraphQL 单篇重试逻辑（try/except 循环）；article 分支简化为调用 `_fetch_article_body()` |

### 验证结果
- `_is_jina_garbage()` 单元验证：垃圾内容（"New to X?", "Sign up now"...）→ True，正常文章内容 → False
- 3 个模块 import 测试通过

### 状态：已完成 ✅

---

## 2026-03-01 · v0.3.1 · 日期时区修复 + 视频嵌入 + 分页增强

### 背景
真实抓取 `@dontbesilent` 全量推文时发现三个问题：
1. **日期差一天**：推文网页显示"2025年12月26日"，但抓取结果为 `published: 2025-12-25`。根因是 Twitter API 返回 UTC 时间，代码直接 `strftime` 未转本地时区
2. **视频丢失**：含视频的推文只保存了封面截图，没有视频 MP4 链接。`extract_tweet_data()` 正确提取了 `videos`，但 `from_twitter()` 只渲染 `images` 忽略了 `videos`
3. **分页不全**：默认 `X_USER_TWEET_MAX_PAGES=50`（≈1000 条），高产博主不够。且分页请求失败（连接重置）时无重试直接中断

### 方案决策

#### 日期时区：集中化工具函数
- 在 `config.py` 新增 `parse_twitter_date_local(created_at, fmt)` 工具函数
- 核心逻辑：`parsedate_to_datetime()` → `dt.astimezone()`（UTC→系统本地时区）→ `strftime()`
- 替换 4 处分散的日期解析代码，统一走此函数
- `astimezone()` 无参数使用系统时区（Python 3.9+ 内置），无需额外依赖

#### 视频嵌入：双渲染（封面图+视频链接）
- 在 `from_twitter()` 的 Article 模式和普通线程模式两处，images 循环后追加 videos 循环
- 格式 `[▶ video](mp4_url)`，与 `twitter_markdown.py` 一致
- 封面图保留作为 Obsidian 内视觉预览

#### 分页增强：扩容+重试
- 默认最大页数 50→200（≈4000 条推文）
- 分页请求失败后重试 3 次，每次间隔 5 秒

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/config.py` | 修改 | 新增 `parse_twitter_date_local()` 工具函数；`x_user_tweet_max_pages()` 默认值 50→200 |
| `feedgrab/utils/storage.py` | 修改 | 3 处日期解析替换为 `parse_twitter_date_local()` 调用（`_format_twitter_datetime` / `_generate_filename` / `_format_markdown`） |
| `feedgrab/fetchers/twitter_user_tweets.py` | 修改 | `_parse_tweet_date()` 替换为 `parse_twitter_date_local()`；分页失败后重试 3 次（5 秒间隔） |
| `feedgrab/schema.py` | 修改 | `from_twitter()` Article 模式和线程模式两处添加 videos 渲染 `[▶ video](mp4_url)` |
| `.env.example` | 修改 | 更新 `X_USER_TWEET_MAX_PAGES` 默认值注释 50→200 |

### 验证结果
**日期修复**：推文 `dontbesilent/status/2004233380997796009`（UTC 16:50 Dec 25）
- 修复前：文件名 `dontbesilent_2025-12-25：...`，front matter `published: 2025-12-25`
- 修复后：文件名 `dontbesilent_2025-12-26：...`，front matter `published: 2025-12-26`（与 Twitter 网页一致）

**视频嵌入**：同一推文含视频
- 修复前：只有 `![image](...amplify_video_thumb...jpg)` 封面截图
- 修复后：封面图 + `[▶ video](...mp4?tag=21)` 视频链接并存

### 状态：已完成 ✅

---

## 2026-02-28 · v0.3.0 · feedgrab setup 一键部署引导

### 背景
当前首次部署需要用户手动执行多个命令（`detect-ua`、`login xhs`、`login twitter`、配置 `.env` 等），且存在隐式依赖（必须先 `detect-ua` 再 `login`，否则 UA 不一致导致 session 失效）。对普通用户门槛过高。

### 方案
新增 `feedgrab setup` 命令，一键按顺序引导完成所有部署步骤：

```
$ feedgrab setup

[1/4] 检查依赖环境...
  ✅ Python 3.10+
  ✅ Playwright 已安装
  ✅ Chrome 浏览器已检测到

[2/4] 检测浏览器指纹...
  🔍 读取本机 Chrome User-Agent...
  ✅ Chrome/145.0.0.0 已写入 .env

[3/4] 平台登录（可按需跳过）
  🔑 登录小红书？(Y/n) y
  🌐 请在弹出的浏览器窗口中扫码登录...
  ✅ 小红书 session 已保存

  🔑 登录 Twitter/X？(Y/n) n
  ⏭ 已跳过（后续可用 feedgrab login twitter 单独登录）

[4/4] 创建配置文件...
  ✅ .env 已生成（基于 .env.example）

🎉 部署完成！试试：
  feedgrab "https://www.xiaohongshu.com/explore/xxx"
```

### 设计原则
- **命令名 `feedgrab setup`**：语义明确，不与"启动服务"混淆
- **顺序强绑定**：`detect-ua` 必须在所有 `login` 之前执行，确保 UA 一致
- **每步可跳过**：用户按需选择要登录的平台
- **幂等可重入**：重复运行时检测到已完成的步骤自动跳过（如 UA 已检测、session 未过期）
- **Cookie 过期提示**：抓取时检测到 session 失效，提示 `feedgrab setup` 或 `feedgrab login <platform>` 重新登录
- **依赖自动安装**：检测 Playwright 未安装时提示一键安装命令

### 状态：已完成 ✅

---

## 2026-02-28 · v0.2.9 · 小红书搜索结果批量抓取 + UA 一致性修复

### 背景
v0.2.8 完成了按作者主页批量抓取。用户希望新增按搜索关键词批量抓取：在小红书搜索关键词后，给定搜索结果页 URL，批量抓取搜索到的笔记。同时发现 `feedgrab login xhs` 创建的 session 频繁过期，根因是 User-Agent 不一致。

### 方案决策

#### 搜索批量抓取
- **复用三层策略**：搜索结果页和作者主页技术架构完全一致（Vue 3 SSR + 瀑布流），复用同一套 Tier 0/1/2 策略
- **Tier 0**：`__INITIAL_STATE__.search.feeds`（~40 篇，零 API 调用）— 与作者页的 `user.notes` 路径不同
- **Tier 1**：XHR 拦截 `/api/sns/web/v1/search/notes`（与作者页的 `user_posted` 端点不同）
- **Tier 2**：逐篇导航 + `evaluate_xhs_note()` 深度提取（与作者页完全复用）
- **目录命名**：`search_{关键词}`（如 `search_开学第一课`）
- **URL 双重解码**：搜索 URL 中 keyword 可能被双重编码（`%25E5%25BC%2580`），循环 unquote 直到稳定
- **无日期过滤**：搜索结果按相关性排序（非时间顺序），日期过滤无意义

#### User-Agent 集中管理
- **根因**：`login.py` 硬编码 macOS + Chrome 120 UA，而批量抓取使用 Windows + Chrome 132 UA，8 处独立硬编码
- **影响**：同一 session 的 UA 从 Mac 切换到 Windows，触发 XHS 风控导致 session 失效
- **修复方案**：
  - `config.py` 新增 `DEFAULT_USER_AGENT` 常量 + `get_user_agent()` 函数
  - 优先读取 `BROWSER_USER_AGENT` 环境变量，缺省使用内置默认值
  - **8 处硬编码**全部替换为 `get_user_agent()` 调用：`login.py`(2处)、`browser.py`、`twitter.py`、`bilibili.py`、`twitter_cookies.py`、`xhs_search_notes.py`、`xhs_user_notes.py`
  - 新增 `feedgrab detect-ua` CLI 命令：启动本机真实 Chrome → 读取 `navigator.userAgent` → 自动写入 `.env`
  - 首次部署时运行 `feedgrab detect-ua` 即可获取真实环境 UA，确保登录和抓取完全一致

### 数据流

```
feedgrab "https://www.xiaohongshu.com/search_result?keyword=开学第一课&source=..."
  → reader._detect_platform() → "xhs_search"
  → reader._read_search_notes(url)
    → xhs_search_notes.fetch_search_notes(url)
      ├─ _parse_search_url() → keyword = "开学第一课"（双重 URL decode）
      ├─ 启动 Playwright 有头 Chrome（复用 xhs.json session）
      ├─ 检测验证码 → _handle_captcha_or_login()（从 xhs_user_notes 复用）
      ├─ Tier 0: state.search.feeds → 40 篇
      ├─ Tier 1: XHR 拦截 /api/sns/web/v1/search/notes + 滚动加载
      ├─ Tier 2: 逐篇 evaluate_xhs_note() + save_to_markdown()
      ├─ 去重索引更新（platform="XHS"，与作者批量共享索引）
      └─ 批量记录 → XHS/index/search_{keyword}_all_{ts}.json
```

### 搜索页面结构（实测结果）

**`__INITIAL_STATE__`**:
- 路径：`state.search.feeds`（Array，~40 项）
- 每项：`{id, modelType, xsecToken, noteCard: {displayTitle, type, user, interactInfo}}`
- 注意：`noteCard.noteId` 为空，笔记 ID 在 `item.id`（与作者页 `user.notes` 不同）
- 字段命名：camelCase（Vue 响应式对象）

**XHR 分页 API**:
- 端点：`edith.xiaohongshu.com/api/sns/web/v1/search/notes`（POST）
- 响应：`{code: 0, data: {has_more, items}}`，每页 ~22 条
- items 字段命名：snake_case（`note_card`, `xsec_token`, `display_title`）

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/xhs_search_notes.py` | **新建** ~280行 | 搜索批量抓取核心：Tier 0 `search.feeds` + Tier 1 `search/notes` XHR 拦截 + Tier 2 逐篇深度 |
| `feedgrab/fetchers/xhs_user_notes.py` | 修改 ~15行 | `_handle_captcha_or_login()` 通用化：参数从 `profile_url` 改为 `target_url`，URL 检查兼容 `/search_result` |
| `feedgrab/reader.py` | 修改 ~25行 | URL 检测 `/search_result` → `xhs_search` + 路由 + `_read_search_notes()` |
| `feedgrab/config.py` | 新增 ~20行 | `xhs_search_enabled()` / `xhs_search_max_scrolls()` / `xhs_search_delay()` |
| `feedgrab/cli.py` | 修改 ~5行 | 帮助文本 + 搜索 URL 批量输出检测 |
| `feedgrab/login.py` | 修改 ~4行 | UA 统一为 Windows + Chrome 132（修复 session 频繁过期） |
| `.env.example` | 新增 ~4行 | `XHS_SEARCH_ENABLED` / `XHS_SEARCH_MAX_SCROLLS` / `XHS_SEARCH_DELAY` |

### 目录结构

```
{OUTPUT_DIR}/XHS/
├── index/
│   ├── item_id_url.json                         ← XHS 全局去重索引（搜索+作者共享）
│   ├── notes_墨客老师资料库_all_*.json            ← 作者批量记录
│   └── search_开学第一课_all_*.json              ← 搜索批量记录
├── notes_墨客老师资料库/                          ← 作者批量笔记
└── search_开学第一课/                             ← 搜索批量笔记（多作者混合）
    ├── 安安老师_2026-02-25：新学期开学第一课这样上🔥被校长夸爆了.md
    ├── 沐然老师_2026-02-26：开学第一课，三年级这样上超轻松！.md
    └── ...
```

### 验证结果
- **Tier 0**：40 篇搜索结果提取成功
- **Tier 2 深度抓取**：38 成功，2 去重跳过（与作者批量重叠），0 失败
- **去重回归**：第二次运行 37 跳过 + 3 新增（搜索结果动态变化属正常）
- **文件名**：全部包含正确日期格式 `作者_YYYY-MM-DD：标题.md`
- **Front matter**：完整（likes/collects/comments/tags/images/location/author_url/cover_image）
- **source URL**：含 xsec_token，链接可直接访问
- **跨模式去重**：搜索结果中出现的笔记如果已被作者批量抓取过，正确跳过

### 状态：已完成 ✅


---

## 2026-02-27 · v0.2.8 · 小红书按作者批量抓取

### 背景
v0.2.7 完成了 XHS 单篇深度抓取（图片、互动数据、标签、日期、作者主页）。用户希望新增按作者主页批量抓取：给定主页 URL（如 `https://www.xiaohongshu.com/user/profile/5eb416f...`），批量抓取该博主所有笔记。参照已有的 Twitter 用户推文批量抓取 (`twitter_user_tweets.py`) 模式。

### 核心挑战

XHS 没有公开 API，且反爬机制严格：
1. **无公开 API**：必须用 Playwright 浏览器打开主页 → 滚动加载瀑布流 → 收集笔记 URL → 逐一提取完整数据
2. **461 反机器人检测**：XHS 对 Playwright 控制的浏览器返回 461 状态码，重定向到 captcha 验证页面。经测试发现这是**服务端 Session 级别**的检测——即使用纯 HTTP 请求（无浏览器）携带被标记的 Session Cookie，也会被 302 重定向到验证码页面
3. **Session 标记**：通过 `feedgrab login xhs`（Playwright 启动 Chrome）创建的 Session 会被 XHS 在登录阶段标记为自动化 Session，后续所有请求都会触发 461

### 反爬对抗历程

尝试了 8+ 种方案均被检测：

| 尝试 | 方案 | 结果 |
|------|------|------|
| 1 | `channel="chrome"` + `--disable-blink-features=AutomationControlled` | 被检测 |
| 2 | Stealth JS 注入（`navigator.webdriver` 覆盖等） | 被检测 |
| 3 | 先访问 explore 页面预热，再访问 profile | 被检测 |
| 4 | `headless=False` 有头模式 | 被检测 |
| 5 | `launch_persistent_context` 使用真实 Chrome 用户数据 | 被检测 |
| 6 | 子进程启动 Chrome + CDP 连接 | 被检测 |
| 7 | `undetected-chromedriver` headless | 被检测 |
| 8 | `undetected-chromedriver` headed | 被检测 |
| 9 | 纯 HTTP 请求（无浏览器） | 302 重定向 → 证实是 Session 级标记 |

**关键发现**：问题不在浏览器指纹，而在 Session 本身。通过 Playwright 登录创建的 Session 已被服务端标记。

### 最终方案：有头浏览器 + 验证码手动解决

放弃绕过反爬，改为**拥抱验证码**：

1. 使用真实 Chrome（`channel="chrome"`）+ 有头模式（`headless=False`）
2. 加载已有 Session 文件打开主页
3. **自动检测**验证码/登录重定向
4. **CLI 提示用户**在弹出的浏览器窗口中手动完成验证码
5. 验证通过后**自动保存更新的 Session**
6. 继续批量抓取操作

### 三层抓取策略

取代原计划的单一 DOM scraping，实际实现了三层策略：

| 层级 | 方式 | 说明 |
|------|------|------|
| Tier 0 | `__INITIAL_STATE__` 提取 | 从 Vue SSR 渲染的页面数据中直接解析笔记列表（约 30 篇），无需滚动 |
| Tier 1 | XHR 拦截器 + 自动滚动 | `page.on("response")` 拦截 `user_posted` API 分页响应，配合自动滚动加载更多笔记 |
| Tier 2 | 逐篇深度抓取 | 带 `xsec_token` 导航到每篇笔记详情页，复用 `evaluate_xhs_note()` 提取完整内容和元数据 |

### 数据流

```
feedgrab https://www.xiaohongshu.com/user/profile/5eb416f...
  → reader._detect_platform() → "xhs_user_notes"
  → reader._read_user_notes(url)
    → xhs_user_notes.fetch_user_notes(url)
      ├─ 解析主页 URL → user_id
      ├─ 启动 Playwright 有头 Chrome（复用 xhs.json session）
      ├─ 检测验证码/登录 → _handle_captcha_or_login()
      ├─ Tier 0: __INITIAL_STATE__ 提取首批笔记 (~30篇)
      ├─ Tier 1: XHR 拦截 + 自动滚动加载更多
      ├─ 加载 XHS 去重索引
      ├─ Tier 2: 逐篇深度抓取：
      │    去重检查 → 跳过已抓取
      │    同浏览器导航到笔记页（带 xsec_token）
      │    evaluate_xhs_note() → 提取完整数据
      │    from_xiaohongshu() → save_to_markdown()
      │    日期检查：< since_date → 连续 3 篇旧笔记则停止
      │    更新去重索引 + 延迟
      ├─ 保存去重索引
      └─ 保存批量记录 JSON
```

### 方案决策

- **有头浏览器而非 headless**：XHS 服务端检测 Session 来源，但对有头模式 + 手动验证码后的 Session 放行
- **单浏览器复用**：整个批量过程只创建一个 browser context，避免每篇笔记 3-5s 启动开销
- **`__INITIAL_STATE__` 优先**：Vue SSR 数据无需额外请求，直接获取约 30 篇笔记
- **source URL 保留 `xsec_token`**：确保保存的链接可直接点击访问（无 token 会 403）
- **连续旧笔记阈值 = 3**：主页可能有置顶笔记（日期较旧），阈值 3 跳过置顶后仍能正确停止
- **平台独立去重索引**：XHS 和 Twitter 各自维护索引，互不干扰，reset 命令也能正确定位

### 日期解析增强（第 4 种格式）

v0.2.7 支持三种日期格式。批量测试中发现第 4 种格式导致 8/32 篇笔记文件名缺少日期：

| 格式 | 示例 | v0.2.7 | v0.2.8 |
|------|------|--------|--------|
| MM-DD + 属地 | `02-18 福建` | ✅ | ✅ |
| 全日期 | `编辑于 2025-08-16` | ✅ | ✅ |
| 相对时间 | `3天前 江苏` | ✅ | ✅ |
| **编辑于 + 相对时间** | `编辑于 昨天 10:17 福建` | ❌ | ✅ |
| **编辑于 + 相对天数** | `编辑于 3天前 福建` | ❌ | ✅ |

修复：在 `_parse_xhs_date()` 起始处增加 `text = re.sub(r"^编辑于\s*", "", text)` 统一剥离 "编辑于" 前缀，使后续解析逻辑能正确匹配。

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/xhs_user_notes.py` | **新建** ~250行 | 核心批量抓取：有头 Chrome + 验证码处理 + 三层抓取（`__INITIAL_STATE__` / XHR 拦截 / 逐篇深度）+ 去重 + 日期过滤 |
| `feedgrab/fetchers/browser.py` | 重构 ~20行 | 提取 `XHS_NOTE_JS_EVALUATE` 模块级常量 + `evaluate_xhs_note()` 辅助函数供批量复用；XHS 单篇改为有头模式 |
| `feedgrab/utils/dedup.py` | 修改 ~10行 | 所有函数新增 `platform` 参数（默认 `"X"` 保持向后兼容） |
| `feedgrab/utils/storage.py` | 修改 ~10行 | `_parse_xhs_date()` 新增 "编辑于" 前缀剥离，支持第 4-5 种日期格式 |
| `feedgrab/config.py` | 新增 ~25行 | 4 个 XHS 批量配置函数：`xhs_user_notes_enabled()` / `xhs_user_note_max_scrolls()` / `xhs_user_note_delay()` / `xhs_user_notes_since()` |
| `feedgrab/reader.py` | 修改 ~30行 | URL 检测（`/user/profile/` → `xhs_user_notes`）+ `_read_user_notes()` + 单篇去重平台感知 |
| `feedgrab/cli.py` | 修改 ~15行 | 帮助文本 + 批量输出 + reset 平台感知 |
| `.env.example` | 新增 ~5行 | `XHS_USER_NOTES_ENABLED` / `XHS_USER_NOTE_MAX_SCROLLS` / `XHS_USER_NOTE_DELAY` / `XHS_USER_NOTES_SINCE` |

### 目录结构

```
{OUTPUT_DIR}/XHS/
├── index/
│   ├── item_id_url.json                    ← XHS 去重索引
│   └── notes_墨客老师资料库_all_*.json       ← 批量记录
└── notes_墨客老师资料库/                     ← 批量笔记
    ├── 墨客老师资料库_2026-02-22：熊出没年年有熊主题初中小学开学第一课绝了.md
    ├── 墨客老师资料库_2026-02-18：开学第一课还没思路的班主任看过来👀.md
    └── ...
```

### 验证结果
- 32/32 篇笔记成功抓取（Tier 0 `__INITIAL_STATE__`，无需滚动）
- 所有 32 个文件名均包含正确日期格式（修复第 4 种格式后）
- source URL 包含 `xsec_token`，链接可直接点击访问
- 去重索引正常工作（重复运行全部 skip）
- 批量记录 JSON 正确保存到 `XHS/index/` 目录
- front matter 完整：likes、collects、comments、images、date、tags、author_url、cover_image

### 状态：已完成 ✅


---

## 2026-02-27 · v0.2.7 · 小红书笔记深度抓取

### 背景
小红书抓取仅提取标题、正文、作者三个基本字段，缺少图片、互动数据、标签、发布日期等关键信息。对标 Twitter 的完整抓取能力，XHS 输出质量明显不足。

### 方案决策
- **Playwright JS 扩展**：在 headless Chromium 中执行 JS 提取完整页面数据
- **图片提取**：从 Swiper 轮播容器提取，过滤 `swiper-slide-duplicate`，按 `data-swiper-slide-index` 排序保证翻页顺序
- **互动数据**：点赞、收藏、评论三个计数从 `.engage-bar .count` 提取
- **日期解析**：支持三种格式 — `"02-18 福建"`（MM-DD+属地，补当前年份）、`"编辑于 2025-08-16"`（最后编辑日期）、`"3天前 江苏"`（相对时间+属地，抓取时转为绝对日期）
- **作者主页**：从 `.author-wrapper a[href*="/user/profile/"]` 提取干净 URL（去掉追踪参数）
- **标签策略**：元数据取前 3 个（Obsidian 搜索用），正文保留全部 `#标签`（还原原帖风格）
- **文件名格式**：与 Twitter 统一为 `作者名_YYYY-MM-DD：标题.md`
- **Jina 登录页检测**：Jina 返回登录页时自动降级到 Playwright

### 改动范围

| 文件 | 改动 |
|------|------|
| `feedgrab/fetchers/browser.py` | XHS JS evaluate 扩展（图片、互动、日期、标签、作者主页 URL），result dict 新增 6 字段 |
| `feedgrab/fetchers/xhs.py` | Tier 2 透传新字段 + Jina 登录页检测降级 |
| `feedgrab/schema.py` | `from_xiaohongshu()` 填充完整 extra（author_url, cover_image, likes, collects, comments, images, date） |
| `feedgrab/utils/storage.py` | `_parse_xhs_date()` 支持三种日期格式（MM-DD/编辑于/相对时间）；`_parse_xhs_location()` 兼容"编辑于"和相对时间格式；文件名 XHS 分支；front matter 新增 author_url/metrics/location/cover_image；正文：文字→标签→图片；元数据 tags 限前 3 个；`_resolve_filepath` head 读取 512→2048 字节 |

### XHS 输出格式
```yaml
---
title: "开学第一课还没思路的班主任看过来👀"
source: "https://www.xiaohongshu.com/explore/69948f62..."
author:
  - "墨客老师资料库"
author_url: "https://www.xiaohongshu.com/user/profile/5eb416f..."
published: 2026-02-18
created: 2026-02-27
cover_image: "https://sns-webpic-qc.xhscdn.com/..."
likes: 179
collects: 242
comments: 28
location: "福建"
tags:
  - "开学第一课ppt"
  - "开学第一课"
  - "教师开学第一课"
item_id: db22cbe3d9c0
---

# 开学第一课还没思路的班主任看过来👀

正文内容...

#开学第一课ppt #开学第一课 #教师开学第一课 #教师必备 #班主任 ...

![1](https://...)
![2](https://...)
```

文件名：`墨客老师资料库_2026-02-18：开学第一课还没思路的班主任看过来👀.md`

### 验证结果
- 帖子 1（有正文+16张图）：标题、正文、10个标签、16张图片按翻页顺序、互动数据、发布日期+属地 全部正确
- 帖子 2（纯图文+12张图，"编辑于"格式）：日期正确解析、9个标签、12张图片、无正文（原帖无文字）
- 帖子 3（纯图文+18张图，"3天前 江苏"相对时间）：日期正确转为绝对日期 2026-02-24、属地"江苏"正确提取、10个标签、18张图片

### 状态：已完成 ✅


---

## 2026-02-27 · v0.2.6b · 移除 unified_inbox.json + feedgrab list 重写

### 背景
`unified_inbox.json` 是 x-reader 原始设计的遗留产物，所有平台、所有采集方式混存到一个 JSON 文件（500 条上限），与 `.md` 文件 100% 数据冗余。每次抓取都要全量读写该文件，唯一消费方 `feedgrab list` 也几乎没人用。

### 方案决策
- **彻底移除** `unified_inbox.json` 及相关写入逻辑（`save_to_json`、`save_content`、`UnifiedInbox` 引用）
- **重写 `feedgrab list`** 为目录扫描统计摘要，零状态文件
- **移除** `INBOX_FILE` 环境变量和 `cmd_clear` 命令

### 改动范围

| 文件 | 改动 |
|------|------|
| `feedgrab/cli.py` | 删除 inbox 依赖，重写 `cmd_list()` 为目录统计，删除 `cmd_clear()`，帮助文档更新 |
| `feedgrab/reader.py` | 移除 `UnifiedInbox` import 和 `self.inbox` 逻辑 |
| `feedgrab/utils/storage.py` | 删除 `save_to_json()` 和 `save_content()` |
| `feedgrab/config.py` | 删除 `get_inbox_path()` |
| `feedgrab/schema.py` | `UnifiedInbox.__init__` 恢复简单默认值（类保留但不再被调用） |
| `.env.example` | 移除 `INBOX_FILE` 配置项 |

### feedgrab list 新输出
```
📦 feedgrab 内容统计 (E:\Obsidian\Qiang_Obsidian\inbox)

  🐦 X: 609 篇
     bookmarks_OpenClaw/  34 篇
     bookmarks_Polymarket/  11 篇
     status/  450 篇
     status_强子手记/  114 篇

  ───────────────
  总计: 609 篇
```

### 状态：已完成 ✅


---

## 2026-02-27 · v0.2.6c · feedgrab reset 命令

### 背景
批量抓取后如需重新抓取某个子目录（如文件名格式更新后），需要同时清理 .md 文件和去重索引中对应的 item_id。手动操作容易遗漏，新增 `feedgrab reset <folder>` 命令自动化此流程。

### 功能
```bash
feedgrab reset bookmarks_OpenClaw    # 重置书签文件夹
feedgrab reset status_强子手记        # 重置账号推文
```

执行流程：
1. 在 `{OUTPUT_DIR}/` 下各平台目录中查找匹配的子目录
2. 扫描所有 .md 文件的 YAML front matter，提取 `item_id`
3. 显示待删除文件数和 item_id 数，等待用户确认
4. 从去重索引 `item_id_url.json` 中移除对应条目
5. 删除 .md 文件

找不到目录时自动列出所有可用子目录。

### 改动范围

| 文件 | 改动 |
|------|------|
| `feedgrab/cli.py` | 新增 `cmd_reset()` + main 路由 + 帮助文档 |

### 状态：已完成 ✅


---

## 2026-02-27 · v0.2.6 · 按推特账号批量抓取 + 文件名优化

### 背景
v0.2.5b 完成了书签批量抓取（全量 + 文件夹）、统一去重索引、扁平目录结构。用户希望新增按推特账号批量抓取功能：给定作者主页 URL（如 `https://x.com/iBigQiang`），批量抓取该账号的所有原创推文（或指定日期之后的推文）。

### 方案决策
- **新增 API**：`UserByScreenName`（screen_name → userId + display_name）、`UserTweets`（用户推文时间线分页）
- **目录命名**：`status_{display_name}`（如 `status_强子手记`），获取不到时降级为 `status_{screen_name}`
- **日期过滤**：环境变量 `X_USER_TWEETS_SINCE=2025-10-01`，不设置则抓全部（API 无原生过滤，客户端逐页检查 `created_at`）
- **功能开关**：`X_USER_TWEETS_ENABLED=false`（默认关闭，与书签批量一致）
- **跳过转推**：仅抓原创推文，检测 `retweeted_status_result` 跳过 RT
- **会话去重**：预扫描全部条目，识别多条目会话（自回复线程），跳过非根条目，升级根推文为线程处理，避免重复保存
- **文件名优化**：格式从 `author_name：标题.md` 改为 `author_name_YYYY-MM-DD：标题.md`，便于按日期排序
- **批量记录增强**：JSON 记录新增 `published`（推文发布日期）和 `item_id` 字段

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 新增 `UserByScreenName` + `UserTweets` API：2 个 fallback queryId 常量、2 个 features 字典、3 个函数（`fetch_user_by_screen_name()`、`fetch_user_tweets_page()`、`parse_user_tweets_entries()`）；扩展 `resolve_query_ids()` + `_fallback_query_ids()` |
| `feedgrab/fetchers/twitter_user_tweets.py` | 新建 | 账号批量抓取核心：URL 解析、分页获取、日期过滤、RT 跳过、会话去重（预扫描）、分类处理（复用书签的 `_classify_tweet` / `_build_single_tweet_data`）、批量记录保存 |
| `feedgrab/config.py` | 修改 | 新增 4 个配置函数：`x_user_tweets_enabled()`、`x_user_tweet_max_pages()`、`x_user_tweet_delay()`、`x_user_tweets_since()` |
| `feedgrab/reader.py` | 修改 | `_detect_platform()` 新增 profile URL 检测（排除 `/i/`、`/home` 等系统路径）；新增 `_read_user_tweets()` 方法 |
| `feedgrab/utils/storage.py` | 修改 | `_generate_filename()` Twitter 文件名格式增加发布日期：`author_name_YYYY-MM-DD：标题` |
| `feedgrab/fetchers/twitter_bookmarks.py` | 修改 | 批量记录新增 `published` 和 `item_id` 字段（5 处 `bookmark_list.append`） |
| `.env.example` | 修改 | 新增 4 个配置项 |

### 数据流
```
feedgrab https://x.com/iBigQiang
  → reader._detect_platform() → "twitter_user_tweets"
  → reader._read_user_tweets(url)
    → twitter_user_tweets.fetch_user_tweets(url, cookies)
      → _parse_profile_url() → screen_name = "iBigQiang"
      → fetch_user_by_screen_name() → (user_id, display_name="强子手记")
      → 分页: fetch_user_tweets_page() → parse_user_tweets_entries()
        → 逐条 extract_tweet_data()
        → 日期过滤: created_at < X_USER_TWEETS_SINCE → 停止
        → 跳过 RT
      → 预扫描: 构建 conversation_id → count 映射
      → 逐条处理:
          跳过非根自回复 | 升级根推文为线程
          single → _build_single_tweet_data() → save
          thread → _fetch_via_graphql() → save
          article → _build_single_tweet_data() + Jina → save
      → 去重: dedup.add_item() + save_index()
      → 记录: index/status_{screen_name}_all_{ts}.json
```

### 目录结构（改动后）
```
{OUTPUT_DIR}/X/
├── index/
│   ├── item_id_url.json                         ← 全局去重索引
│   ├── bookmarks_all_*.json                     ← 书签批量记录
│   ├── bookmarks_OpenClaw_*.json                ← 书签文件夹记录
│   └── status_iBigQiang_all_*.json              ← 账号抓取记录
├── status/                                      ← 单篇抓取
├── status_强子手记/                              ← iBigQiang 账号推文
├── bookmarks/                                   ← 全部书签
└── bookmarks_OpenClaw/                          ← 书签文件夹
```

### 会话去重算法
UserTweets API 同时返回根推文和自回复，不做处理会导致重复文件：
1. **预扫描**：遍历全部条目，构建 `conversation_id → count` 映射
2. **识别多条目会话**：`count > 1` 的 conversation_id
3. **跳过非根条目**：`conversation_id != tweet_id` 的自回复
4. **升级根推文**：`single` → `thread`（触发完整线程抓取，包含所有自回复）
5. **追踪已处理会话**：`processed_conv_ids` 集合防止重复处理

### 验证结果
- 平台检测：10/10 用例通过（profile URL / 系统路径排除 / 书签 / 单篇）
- 用户解析：iBigQiang → user_id=1001044583273418752, display_name=强子手记
- 分页抓取：2 页 39 条，RT 跳过正常
- 会话去重：修复前 35 文件（7 重复），修复后 28 文件（0 重复）
- 日期过滤：`X_USER_TWEETS_SINCE=2026-02-25` 正确过滤并停止分页
- 去重回归：第二次运行全部 skip
- 文件名格式：`强子手记_2026-02-24：最近看到好多新蓝V都成功✅认证了创作者身份。.md`
- 批量记录：JSON 包含 `published` 和 `item_id` 字段

### 状态：已完成 ✅


---

## 2026-02-27 · v0.2.5b · 书签文件夹 + 统一去重 + 目录扁平化

### 背景
v0.2.5 书签批量抓取只支持全部书签，文件夹 URL（`x.com/i/bookmarks/{folderId}`）降级为全量。此外去重索引仅在书签模块内部使用，单篇抓取不写入索引。目录结构嵌套过深（`X/bookmarks/OpenClaw/`）。

### 方案决策
- **书签文件夹**：新增 `BookmarkFoldersSlice` API 获取文件夹名称，`BookmarkFolderTimeline` API 获取指定文件夹推文
- **统一去重**：抽离全局去重模块 `feedgrab/utils/dedup.py`，索引文件迁移到 `{OUTPUT_DIR}/X/index/item_id_url.json`
- **索引格式**：`{"item_id": ["日期", "URL"]}`，每条一行，紧凑可读
- **目录扁平化**：单篇→`status/`，全部书签→`bookmarks/`，文件夹书签→`bookmarks_{name}/`，消除嵌套

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/utils/dedup.py` | 新建 | 全局去重索引模块：load/save/add/has_item + 旧格式自动迁移 |
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 新增 `fetch_bookmark_folders()`、`fetch_bookmark_folder_page()`；扩展 `parse_bookmark_entries()` 多路径；扩展 `resolve_query_ids()` + `_fallback_query_ids()` |
| `feedgrab/fetchers/twitter_bookmarks.py` | 修改 | 移除旧索引函数改用 dedup 模块；新增 `_resolve_folder_name()`；分页路由文件夹/全量；目录扁平化 |
| `feedgrab/utils/storage.py` | 修改 | `save_to_markdown()` 支持 `category` 子目录 |
| `feedgrab/reader.py` | 修改 | 单篇 Twitter 设置 `category="status"`；保存后写入去重索引 |

### 目录结构（改动后）
```
{OUTPUT_DIR}/X/
├── index/                        ← 运维数据
│   ├── item_id_url.json          ← 全局去重索引
│   └── bookmarks_*.json          ← 批量抓取记录
├── status/                       ← 单篇抓取
├── bookmarks/                    ← 全部书签（无文件夹）
├── bookmarks_OpenClaw/           ← 书签文件夹
└── bookmarks_撸毛课/             ← 另一个书签文件夹
```

### 去重策略
| 模式 | 读索引 | 写索引 | 跳过重复 |
|------|--------|--------|----------|
| 单篇抓取 | 否 | 是 | 否（用户主动请求） |
| 书签批量 | 是 | 是 | 是 |
| 未来作者批量 | 是 | 是 | 是 |

### 状态：已完成 ✅


---

## 2026-02-27 · v0.2.5 · Twitter 书签批量抓取

### 背景
用户需要批量抓取 Twitter 书签中收藏的推文，支持 `feedgrab https://x.com/i/bookmarks` 命令。

### 方案决策
- **方案选择**：Approach B（混合模式）—— 从书签 API 响应直接提取推文数据，仅对线程和长文章做二次 API 调用
- **GraphQL 端点**：复用 `_execute_graphql()`，新增 `Bookmarks` 操作（queryId 从 JS bundle 动态解析，fallback `-LGfdImKeQz0xS_jjUwzlA`）
- **响应路径**：`data.bookmark_timeline_v2.timeline.instructions`（不同于 TweetDetail 的 `threaded_conversation_with_injections_v2`）
- **去重策略**：本地 `.item_id_index.json` 索引文件（用户建议，比扫描目录高效）
- **URL 列表**：每次抓取保存 `output/X/bookmarks/bookmarks_all_{timestamp}.json`
- **安全措施**：默认关闭（`X_BOOKMARKS_ENABLED=false`），分页间隔 1.5s，推文处理间隔 2.0s

### 改动范围

| 文件 | 类型 | 改动 |
|------|------|------|
| `feedgrab/fetchers/twitter_bookmarks.py` | 新建 | 书签批量抓取核心：分页获取、分类处理（单条/线程/文章）、去重索引、URL 列表保存 |
| `feedgrab/fetchers/twitter_graphql.py` | 修改 | 新增 `BOOKMARK_FEATURES`/`BOOKMARK_FIELD_TOGGLES`、`fetch_bookmarks_page()`、`parse_bookmark_entries()`；扩展 `resolve_query_ids()` 和 `_fallback_query_ids()` |
| `feedgrab/config.py` | 修改 | 新增 `x_bookmarks_enabled()`、`x_bookmark_max_pages()`、`x_bookmark_delay()` |
| `feedgrab/reader.py` | 修改 | `_detect_platform()` 识别 `/i/bookmarks` URL；新增 `_read_bookmarks()` 批量流程 |
| `feedgrab/cli.py` | 修改 | 书签 URL 输出汇总信息 |
| `.env.example` | 修改 | 新增 `X_BOOKMARKS_ENABLED`、`X_BOOKMARK_MAX_PAGES`、`X_BOOKMARK_DELAY` |

### 数据流
```
feedgrab https://x.com/i/bookmarks
  → reader._detect_platform() → "twitter_bookmarks"
  → reader._read_bookmarks()
    → twitter_bookmarks.fetch_bookmarks()
      → twitter_graphql.fetch_bookmarks_page() (分页获取全部)
      → 逐条 extract_tweet_data() → 分类:
          单条 → 直出 → from_twitter() → save_to_markdown()
          线程 → fetch_tweet_thread() → 完整线程 → save
          文章 → Jina body → save
      → 保存 .item_id_index.json + bookmarks URL 列表
```

### 状态：已完成 ✅


---

## 2026-02-27 · v0.2.4d · t.co 短链接展开为原始 URL

### 背景
推文正文中的外部链接（如微信公众号）显示为 `https://t.co/xxx` 短链接，而非用户实际可见的完整 URL。GraphQL 返回的 `entities.urls` 中已包含 `expanded_url`（原始链接），但 `extract_tweet_data()` 未做替换。

### 方案决策
在 `extract_tweet_data()` 提取 `full_text` 后，遍历 URL 实体（note_tweet `entity_set.urls` 优先，回退 `legacy.entities.urls`），将正文中的 `url`（t.co）替换为 `expanded_url`（原始完整链接）。

### 改动范围
| 文件 | 改动 |
|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | `extract_tweet_data()` 中 `full_text` 提取后增加 t.co → expanded_url 替换 |

### 验证结果
**binghe 推文**：`https://t.co/WngCfV5mTC` → `https://mp.weixin.qq.com/s/t6xjY07Yf7VIflDWvXjk4A`，输出文件中无残留 t.co 链接。

### 状态：全部完成 ✅


---

## 2026-02-27 · v0.2.4c · 修复作者回帖漏抓 + Article 检测增强 + 排序 bug

### 背景
测试 binghe 推文发现两个问题：
1. 作者嵌套回复 2 条只抓到 1 条 — `created_at` 字符串排序导致条目被错误 slice 掉
2. 长文章偶尔正文只输出 t.co 短链接 — `is_article_stub` 检测仅看合并文本长度，多推文线程合并后超 200 字符就检测失败

### 根因分析
- **排序 bug**：Twitter `created_at` 格式为 `"Fri Dec 26 04:50:10 +0000 2025"`，字符串排序按星期几字母序（Fri < Wed），导致 12月26日的条目排在 12月24日之前，被 `root_idx` slice 切掉
- **作者回帖过滤**：条件 `in_reply_to_user_id != root_user_id` 排除了作者回复自己（对评论者回复的继续回复）
- **Article 检测**：`is_article_stub` 仅检查合并文本 `len(text) < 200 and "https://t.co/"` — 多推文线程合并后轻松超 200 字符

### 方案决策
- **排序修复**：`all_entries.sort()` 改用 Tweet ID（Snowflake ID 单调递增）代替 `created_at` 字符串
- **作者回帖**：移除 `in_reply_to_user_id != root_user_id` 条件，所有不在线程链中的作者推文均视为回帖
- **Article 检测增强**：主信号用 `article_data.has_content`，次信号检查首条推文文本（非合并文本）
- **Jina 重试**：Article 正文获取失败时自动重试 1 次（间隔 2 秒）
- **线程编号**：主贴不加 `[1/21]` 前缀，续帖从 `[1/20]` 编号，主贴与续帖层次更清晰

### 改动范围

| 文件 | 改动 |
|------|------|
| `feedgrab/fetchers/twitter_thread.py` | Phase 7 排序改用 `int(id)`；作者回帖/评论排序统一改用 ID；移除 `in_reply_to_user_id` 过滤条件 |
| `feedgrab/fetchers/twitter.py` | `is_article_stub` 改为 `article_data.has_content` + 首条推文文本检测；Jina 获取增加重试机制；线程主贴不加编号前缀 |
| `feedgrab/schema.py` | 线程主贴不加编号前缀，续帖从 `[1/N]` 编号 |

### 验证结果
**binghe 推文**（binghe/status/2003639692542247190）：
- 修复前：1 条作者回帖、6 条评论
- 修复后：2 条作者回帖（时间正序）、10 条评论
- 线程 21 条不变

**鱼总长文章**（AI_Jasonyu/status/2026455606970954087）：
- Article 正确检测，cover_image 正常，正文完整内容

### 状态：全部完成 ✅


---

## 2026-02-27 · v0.2.4 · 修复标题过长 + 图片丢失 + 标签硬编码 + cover_image 逻辑 + 图片格式

### 背景
实测抓取普通推文和长文章发现多个问题：标题使用推文前100字符太长且含换行符，单条推文正文缺少图片，YAML tags 硬编码 `clippings`/`twitter` 未提取推文 `#hashtag`，cover_image 对普通推文和长文章处理不合理，Jina 返回的长文章正文图片使用非标准嵌套 Markdown 格式。

### 方案决策
- **标题智能截断**：`_clean_title()` 函数 — 过滤换行/制表/控制字符，50字符内优先在句号（。！？.!?）处断开
- **图片嵌入**：单条推文去掉多余的 `[1/1]` 前缀，保留图片嵌入逻辑
- **标签提取**：四层穿透提取推文 `#hashtag`，无 hashtag 时不输出 tags 字段，不插入硬编码值
- **Hashtag 源**：优先从 `note_tweet.entity_set.hashtags` 提取（长推文），回退到 `legacy.entities.hashtags`
- **cover_image 区分**：仅长文章（Article）输出 cover_image（从 `cover_media.media_info.original_img_url` 提取），普通推文不输出
- **长文章封面**：正文开头插入 `![cover](url)` 显示封面图
- **Jina 图片格式**：`[![alt](img)](link)` 嵌套格式统一转为标准 `![image](img)`
- **额外修复**：`article` 为 `None` 时 `.get()` 崩溃防护

### 改动范围

| 文件 | 改动 |
|------|------|
| `feedgrab/fetchers/twitter_graphql.py` | `extract_tweet_data()` 提取 hashtags（note_tweet 优先）；`_extract_article_ref()` 提取 cover_image |
| `feedgrab/fetchers/twitter.py` | `_clean_title()` 智能截断；透传 hashtags + article_data；Jina 图片格式正规化；`article` None 防护 |
| `feedgrab/schema.py` | 单条推文去 `[1/1]`；Article 正文开头插入封面图；cover_image 仅长文章；tags 只含 hashtag |
| `feedgrab/utils/storage.py` | tags 从 `item.tags` 读取，无 tag 不输出；文件名截断 150→50 |

### 验证结果
**普通推文**（iBigQiang/status/2026279968171606479）：
- 标题智能截断在句号处：`最近看到好多新蓝V都成功✅认证了创作者身份。`
- 无 cover_image 字段，图片内联在正文
- Tags 只有 `互关`、`蓝v关注必回`，无硬编码值

**长文章**（AI_Jasonyu/status/2026455606970954087）：
- cover_image 从 article cover_media 提取：`https://pbs.twimg.com/media/HB7xEvcaAAAmexY.jpg`
- 正文开头显示 `![cover](...)`
- 正文图片全部为标准 `![image](url)` 格式，无嵌套链接

### 状态：全部完成 ✅


---

## 2026-02-27 · v0.2.4b · 评论开关组合逻辑优化

### 背景
四种开关组合中，`X_FETCH_AUTHOR_REPLIES=false` + `X_FETCH_ALL_COMMENTS=true` 时应按时间线输出所有人评论（含作者嵌套回复），而非仅输出他人评论。

### 方案决策

| 组合 | 作者回帖 | 评论区 |
|------|---------|--------|
| 都开 | 独立章节（时间序） | 仅他人（按赞数） |
| 仅 ALL_COMMENTS | 无 | 所有非线程条目（时间序） |
| 仅 AUTHOR_REPLIES | 独立章节 | 无 |
| 都关 | 无 | 无 |

### 关键分析
作者回复分两种类型，当前设计已正确区分：
- **连续自回复（内容分段）**：`_is_same_thread()` 捕获为 `thread_tweets`（始终作为正文）
- **嵌套回复他人评论**：归入 `author_replies`（C 类，可选开关控制）

### 改动范围
| 文件 | 改动 |
|------|------|
| `feedgrab/fetchers/twitter_thread.py` | B 类评论收集逻辑根据 AUTHOR_REPLIES 开关分支：都开时仅他人按赞数排序，仅 ALL_COMMENTS 时所有非线程条目按时间排序 |

### 状态：全部完成 ✅


---

## 2026-02-26 · v0.2.3 · Cookie 集中管理 + 评论回复采集开关

### 背景
当前 cookie/session 分散在 `~/.feedgrab/cookies/` 和 `~/.feedgrab/sessions/` 两处，路径硬编码在各个 fetcher 中，用户难以管理。Cookie 缺失时虽然有 warning，但没有阻断执行，用户容易忽略导致数据不完整。此外用户需要可选采集推文作者回帖和全部评论。

### 方案决策
- **路径统一**：所有 cookie/session 收归到项目根目录 `sessions/`（通过 `FEEDGRAB_DATA_DIR` 配置，默认 `sessions`）
- **扁平结构**：cookie 文件和 Playwright session 在同一目录，不再分 `cookies/` 和 `sessions/` 子目录
- **集中配置**：新建 `feedgrab/config.py` 管理路径常量和 feature flag
- **向后兼容**：自动检测 `.feedgrab/cookies/`、`.feedgrab/sessions/`、`~/.feedgrab/` 老路径，找到后迁移到新位置
- **Cookie 检查**：Tier 0 前强制检查 cookie，缺失时显示醒目引导框
- **评论开关**：`.env` 新增 `X_FETCH_AUTHOR_REPLIES`、`X_FETCH_ALL_COMMENTS` 开关

### 改动范围

| 文件 | 改动 |
|------|------|
| `feedgrab/config.py`（新建） | 集中管理路径常量和配置读取 |
| `feedgrab/fetchers/twitter_cookies.py` | cookie 路径改用 config.py，cookie 文件改名 `x.json`，老路径自动迁移 |
| `feedgrab/fetchers/browser.py` | session 路径改用 config.py |
| `feedgrab/login.py` | session 路径改用 config.py |
| `feedgrab/fetchers/twitter.py` | Cookie 缺失醒目提示框 + Cookie 过期(401/403)提示 |
| `.env.example` | 新增 `FEEDGRAB_DATA_DIR`、评论采集开关 |
| `.gitignore` | 确保 `.feedgrab/` 被忽略 |

### 目录结构
```
项目根目录/
├── sessions/                    # 所有平台认证数据（FEEDGRAB_DATA_DIR，默认 sessions）
│   ├── x.json                   # Twitter cookies: {"auth_token": "...", "ct0": "..."}
│   ├── twitter.json             # Twitter Playwright storage_state
│   ├── xhs.json                 # 小红书 Playwright storage_state
│   └── wechat.json              # 微信 Playwright storage_state
├── output/                      # 抓取内容输出
├── .env                         # 配置文件
└── feedgrab/                    # 源码
```

### 实施步骤

| 阶段 | 步骤 | 文件 | 状态 |
|------|------|------|------|
| A | 新建 config.py，集中路径和开关 | `feedgrab/config.py` | ✅ |
| A | 迁移 cookie/session 路径引用 | `twitter_cookies.py`, `browser.py`, `login.py` | ✅ |
| A | 更新 .env.example + .gitignore | `.env.example`, `.gitignore` | ✅ |
| A | 老路径向后兼容（自动复制） | `twitter_cookies.py` | ✅ |
| B | Cookie 强制前置检查 + 醒目提示 | `twitter.py` | ✅ |
| C | 作者回帖采集 | `twitter_thread.py`, `twitter.py`, `schema.py`, `storage.py` | ✅ |
| D | 全部评论采集 | `twitter_thread.py`, `twitter.py`, `schema.py`, `storage.py` | ✅ |

### C+D 实施细节

**核心设计**：零额外 API 调用，复用 `fetch_tweet_thread()` 已有分页数据，将 `all_entries` 按 `user_id` 分三类：
- A 类（已有）：作者自回复链 → `thread_tweets`
- B 类：其他用户评论 → `comments`（按点赞降序，上限 `X_MAX_COMMENTS`）
- C 类：作者回复评论者 → `author_replies`（按时间升序）

**数据流**：`twitter_thread.py` 分类 → `twitter.py` 透传 → `schema.py` 存入 extra → `storage.py` 渲染 Markdown 章节

**验证结果**（2026-02-26 测试 AI_Jasonyu/status/2026455606970954087）：
- 采集到 21 条作者回帖 + 30 条评论
- Markdown 末尾正确渲染 `## 作者回帖` 和 `## 评论区 (30条)` 章节
- 默认关闭（不设 env）时输出与之前完全一致

### 状态：全部完成 ✅


---

## 2026-02-26 · v0.2.2 修复 Twitter 数据断层 + 丰富元数据 + Cookie 引导

### 背景
真实抓取测试发现：GraphQL 已获取 20+ 字段（likes/views/bookmarks 等），但在 `_fetch_via_graphql()` → `from_twitter()` → `_format_markdown()` 三层传递中全部丢失。Cookie 缺失时 Tier 0 被静默跳过，Jina 返回的冗余前缀混入正文，front matter 不兼容 Obsidian 格式。参考 `x_tracker` 项目的字段标准。

### 方案决策
- **数据断层修复**：在 `_fetch_via_graphql()` 两个 return 路径中，从 root tweet 提升全部指标到顶层 dict
- **Schema 补全**：`from_twitter()` extra 新增 replies/bookmarks/views/created_at/author_name/cover_image
- **Jina 清洗**：过滤 `URL Source:`/`Published Time:`/`Markdown Content:` 前缀行
- **Obsidian 兼容**：front matter 对齐 Obsidian Properties 格式，零值指标不输出
- **Cookie 引导**：缺失时输出 warning + 操作指引，过期(401/403)时提示刷新

### 实施步骤
| 步骤 | 文件 | 说明 |
|------|------|------|
| 1 | `feedgrab/fetchers/twitter.py` | `_fetch_via_graphql()` 提升 likes/retweets/replies/bookmarks/views/created_at/author_name/images/videos 到顶层；`fetch_twitter()` 添加 cookie 缺失/过期提示 |
| 2 | `feedgrab/schema.py` | `from_twitter()` extra 补充完整字段，首张图片作为 cover_image |
| 3 | `feedgrab/fetchers/jina.py` | 新增 `_JINA_META_PREFIXES` 常量，过滤 Jina 元数据前缀行 |
| 4 | `feedgrab/utils/storage.py` | `_format_markdown()` 输出 Obsidian 兼容 YAML front matter（title/source/author/author_name/published/created/cover_image/指标/tags） |

### Front Matter 目标格式（有 Cookie 完整模式）
```yaml
---
title: "OpenClaw新手完整学习路径"
source: "https://x.com/AI_Jasonyu/status/123"
author:
  - "@AI_Jasonyu"
author_name: "鱼总聊AI"
published: 2026-02-26
created: 2026-02-26
cover_image: "https://pbs.twimg.com/media/xxx.jpg"
tweet_count: 3
has_thread: true
likes: 1234
retweets: 567
replies: 89
bookmarks: 234
views: 45678
tags:
  - "clippings"
  - "twitter"
---
```

### 状态：已完成 ✅

---
## 2026-02-26 · v0.2.1 按平台分目录保存内容

背景                                                                                                                              │
│                                                                                                                                   │
│ 当前所有平台的抓取内容都追加到同一个 output/content_hub.md 文件，随着使用量增加会变得混乱且难以管理。内容还被截断到 2000          │
│ 字符。需要改为按平台分目录、每条内容独立一个文件。                                                                                │
│                                                                                                                                   │
│ 改动范围                                                                                                                          │
│                                                                                                                                   │
│ 只改 1 个文件：feedgrab/utils/storage.py（reader.py 调用签名不变，无需修改）                                                      │
│                                                                                                                                   │
│ 目录结构                                                                                                                          │
│                                                                                                                                   │
│ output/                  (由 OUTPUT_DIR 环境变量控制)                                                                             │
│ ├── X/                   # Twitter/X                                                                                              │
│ │   ├── OpenClaw新手完整学习路径.md                                                                                               │
│ │   └── When people ask me about AI agents.md                                                                                     │
│ ├── XHS/                 # 小红书                                                                                                 │
│ ├── Bilibili/            # B站                                                                                                    │
│ ├── WeChat/              # 微信                                                                                                   │
│ ├── YouTube/             # YouTube                                                                                                │
│ ├── Telegram/            # Telegram                                                                                               │
│ ├── RSS/                 # RSS                                                                                                    │
│ └── Manual/              # 手动输入                                                                                               │
│                                                                                                                                   │
│ SourceType → 目录名映射：                                                                                                         │
│ - TWITTER → X                                                                                                                     │
│ - XIAOHONGSHU → XHS                                                                                                               │
│ - BILIBILI → Bilibili                                                                                                             │
│ - WECHAT → WeChat                                                                                                                 │
│ - YOUTUBE → YouTube                                                                                                               │
│ - TELEGRAM → Telegram                                                                                                             │
│ - RSS → RSS                                                                                                                       │
│ - MANUAL → Manual                                                                                                                 │
│                                                                                                                                   │
│ 文件命名规则                                                                                                                      │
│                                                                                                                                   │
│ 1. 优先用 item.title，无标题则取 item.content 前 150 字符，都没有则用 item.id                                                     │
│ 2. 清理非法字符（\ / : * ? " < > |）、控制字符、Windows 保留名                                                                    │
│ 3. 最长 100 字符，截断时不切断单词                                                                                                │
│ 4. 同名冲突：追加 _itemid 后缀（如 My Article_a3f2b9c1d4e5.md）                                                                   │
│ 5. 相同 URL 重复抓取：同一 item.id 产生相同文件名，直接覆盖（更新内容）                                                           │
│                                                                                                                                   │
│ 单文件 Markdown 格式                                                                                                              │
│                                                                                                                                   │
│ ---                                                                                                                               │
│ source: twitter                                                                                                                   │
│ author: "@username"                                                                                                               │
│ url: https://x.com/username/status/123                                                                                            │
│ fetched_at: 2026-02-26T19:32                                                                                                      │
│ tweet_count: 5                                                                                                                    │
│ has_thread: true                                                                                                                  │
│ ---                                                                                                                               │
│                                                                                                                                   │
│ （完整内容，不再截断）                                                                                                            │
│                                                                                                                                   │
│ - Twitter 线程：from_twitter() 已将主贴+作者回帖拼合为 [1/N] 格式，直接保存完整内容                                               │
│ - 非 Twitter 平台：加 # {title} 一级标题 + 完整内容                                                                               │
│ - B站额外字段：bvid、duration                                                                                                     │
│ - 移除 2000 字符截断限制                                                                                                          │
│                                                                                                                                   │
│ storage.py 具体改动                                                                                                               │
│                                                                                                                                   │
│ 1. 新增 PLATFORM_FOLDER_MAP 常量                                                                                                  │
│ 2. 新增 _sanitize_filename() — 文件名清理                                                                                         │
│ 3. 新增 _generate_filename() — 文件名生成                                                                                         │
│ 4. 新增 _resolve_filepath() — 冲突处理                                                                                            │
│ 5. 新增 _format_markdown() — 生成完整 Markdown 内容（YAML front matter + body）                                                   │
│ 6. 重写 save_to_markdown() — 改为写单独文件到平台子目录                                                                           │
│ 7. save_to_json 完全不动                                                                                                          │
│                                                                                                                                   │
│ 验证方式                                                                                                                          │
│                                                                                                                                   │
│ # 测试 Twitter                                                                                                                    │
│ feedgrab https://x.com/AI_Jasonyu/status/2026455606970954087                                                                      │
│ # 预期：output/X/鱼总聊AI on X OpenClaw新手完整学习路径....md 生成                                                                │
│                                                                                                                                   │
│ # 测试列表                                                                                                                        │
│ ls output/X/  


## 2026-02-26 · v0.2.0 · X/Twitter GraphQL 融合升级

### 背景
feedgrab（原 x-reader）的 Twitter 模块只有三级兜底（oEmbed → Jina → Playwright），只能获取单条推文的粗糙文本，无法抓取线程、图片、视频和引用推文。[baoyu-danger-x-to-markdown](https://github.com/anthropics/claude-code) 技能通过逆向工程 X 的私有 GraphQL API 实现了深度抓取，但它是 TypeScript/Bun 运行时的独立工具。

### 方案决策
- **方案选择**：Python 完整重写（而非 TypeScript 子进程调用），保持技术栈统一
- **架构设计**：新增 GraphQL 作为 Tier 0，保留原有三级作为兜底
- **安全措施**：请求间隔 1.5 秒（原版无限制）、最大分页 20 次（原版 1000）、Cookie 日志脱敏、`X_GRAPHQL_ENABLED` 开关

### 实施步骤
| 步骤 | PR | 文件 | 说明 |
|------|-----|------|------|
| 1 | #1 | `twitter_cookies.py` | Cookie 四源合并管理 |
| 2 | #2 | `twitter_graphql.py` | GraphQL API 客户端 + 动态 queryId |
| 3 | #3 | `twitter_thread.py` | 线程重建（6 阶段算法） |
| 4 | #4 | `twitter_markdown.py` | Markdown 渲染 |
| 5 | #5 | `twitter.py` | 四级兜底调度器 |
| 6 | #6 | `schema.py` | Schema 扩展支持线程数据 |

### 参考文档
- 详细技术对比分析：`融合升级方案.md`
- 原始 baoyu 源码：`skills/baoyu-danger-x-to-markdown/`

### 状态：已完成 ✅

---

## 初始版本 · v0.1.0 · 来自 x-reader 的基础功能

- 7+ 平台统一抓取（YouTube、B站、X/Twitter、微信、小红书、Telegram、RSS）
- oEmbed → Jina → Playwright 三级兜底
- CLI / Python 库 / MCP 服务器三层架构
- UnifiedContent 统一数据结构
- Claude Code Skills（视频转录 + AI 分析）

### 状态：已完成 ✅
