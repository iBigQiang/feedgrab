# feedgrab 项目指令

## 项目概述

feedgrab 是一个万能内容抓取器，从任意平台抓取内容并输出为 Obsidian 兼容的结构化 Markdown。

- **仓库**：https://github.com/iBigQiang/feedgrab
- **作者**：[@iBigQiang](https://github.com/iBigQiang)（强子手记）
- **当前版本**：v0.25.0
- **Python**：≥3.10
- **许可证**：MIT

### 项目来源

- **[x-reader](https://github.com/runesleo/x-reader)**（@runes_leo）— 多平台架构、CLI、MCP 服务器
- **[baoyu-danger-x-to-markdown](https://github.com/JimLiu/baoyu-skills)**（@dotey 宝玉）— X/Twitter GraphQL 逆向工程深度抓取

### 三层架构

| 层级 | 功能 | 入口 |
|------|------|------|
| Python CLI/库 | 基础内容抓取 + 统一数据结构 | `feedgrab <url>` |
| Claude Code 技能 | 视频转录 + AI 分析 | `skills/video/` `skills/analyzer/` |
| MCP 服务器 | 将抓取能力暴露为 MCP 工具 | `mcp_server.py` |

### 支持的平台（抓取方式一览）

| 平台 | 抓取方式 |
|------|---------|
| X/Twitter | GraphQL → FxTwitter → Syndication → oEmbed → Jina → Playwright（六级兜底） |
| 小红书 | API (xhshow) → Pinia Store 注入 → Jina → Playwright（+ 作者/搜索批量 + `xhs-so`） |
| YouTube | InnerTube API → yt-dlp 字幕 → Groq Whisper + Data API v3 搜索 + yt-dlp 下载 |
| B站 | view API 元数据 + 字幕 3 级兜底（`player/v2` → `player/wbi/v2` WBI → Whisper 可选） |
| 微信公众号 | Playwright JS 提取 → Jina 兜底（单篇 + 搜狗搜索 + MP 账号批量 + 专辑批量） |
| GitHub | REST API（仓库元数据 + 中文 README 优先 + 摘要提取） |
| LinuxDo / IDCFlare / Discourse | Discourse Topic JSON API → CDP 复用 Chrome → Playwright 页面内 fetch → Jina（默认主贴 + 楼主自回，可切换完整楼层） |
| 飞书/Lark | Open API → CDP 直连 → Playwright PageMain → Jina（+ 知识库批量 + 嵌入表格 + 图片；修复虚拟目录树与表格错位） |
| 金山文档 | Playwright ProseMirror DOM（虚拟滚动 + 代码块 + shapes API 图片 + CDP 直连） |
| FlowUs 息流 | Tier 0 纯 HTTP `/api/docs/{uuid}`（公开零 cookie / 付费需 `next_auth`+`next_auth.sig` 双 cookie）→ CDP → Launch+saved session → Jina；Notion 风格 block-tree 渲染；默认在线签名图 URL，可开启本地图片附件 |
| 有道云笔记 | JSON API → Playwright iframe DOM → Jina（+ 图片下载） |
| 知乎 | API v4 → Playwright CDP/DOM → Jina（+ 问答前 3 楼 + 专栏 + `zhihu-so`） |
| Telegram | Telethon |
| 小宇宙 | SSR `__NEXT_DATA__` + Groq Whisper 转录 |
| 喜马拉雅 | Web Revision API + canPlay 降级 + Groq Whisper（免费节目） |
| RSS | feedparser |
| HackerNews | Firebase API v0（item.json + 一层评论；hn top/new/best/ask/show/jobs 列表批量） |
| Medium | Jina Reader → JSON-LD articleBody → Stealth Browser；medium-user / medium-pub 走 RSS feed |
| Reddit | old.reddit.com .json + 自报 UA → CDP 复用 Chrome → Stealth Playwright + saved session → Jina（首屏 Top 50 评论 + reddit-sub 五种排序） |
| Weibo | m.weibo.cn `/statuses/show` + `/api/container/getIndex`（SUB Cookie 可选 + Visitor 占位 + SSR `$render_data` 兜底） |
| Douyin | CDP 复用 Chrome → Stealth Playwright + saved session → SSR RENDER_DATA → Jina（不破解签名，浏览器内自动签名；v.douyin.com 短链 302 解析） |
| 知识星球 | Tier 0 HTTP cookie（articles SSR HTML / topic API JSON）→ Tier 1 CDP 复用 → Tier 2 Stealth Browser → Tier 3 Jina（强登录态门控，仅姿态保留）；短链 t.zsxq.com 302 解析；topic 五形态：talk/question+answer/article/solution；评论三态 |
| 付费新闻（300+） | 7 级 Tier 绕过（JSON-LD → Googlebot/Bingbot UA → AMP → EU IP → archive.today → Google Cache → Jina） |
| 任意网页 | JSON-LD 前置探测 → Jina 兜底 |

## 语言规范

- 对话、文档一律使用**中文**
- 代码注释可英文
- Git commit：英文前缀（feat/fix/docs/chore）+ 英文描述

## 开发工作流

完成功能开发并测试通过后：

1. 更新 DEVLOG.md（顶部新增版本条目）
2. 更新 README.md / README_en.md（同步新功能使用说明）
3. `git commit` + `git push origin main`

> 使用 `/ship` 命令一键完成上述流程。

## 版本号规范

- 主要新功能：递增次版本号（v0.17.0 → v0.18.0）
- 小功能/修复：递增补丁号（v0.18.0 → v0.18.1）

## 核心架构

```
feedgrab/
├── feedgrab/
│   ├── cli.py                 # CLI 入口（命令路由 + setup + clip）
│   ├── config.py              # 集中配置（get_user_agent/get_stealth_headers）
│   ├── reader.py              # URL 调度器（UniversalReader 平台检测 + 路由 + URL 规范化）
│   ├── schema.py              # 统一数据模型（UnifiedContent）
│   ├── login.py               # 浏览器登录管理 + CDP Cookie 提取
│   ├── service/               # 服务层 API（FetchService / Output / Login / Settings / Doctor / Job）
│   ├── fetchers/              # 各平台 fetcher（见"关键文件速查"）
│   └── utils/                 # storage / dedup / http_client / jsonld / transcribe / bilibili_wbi / media
├── skills/                    # Claude Code 技能
├── mcp_server.py              # MCP 服务器入口
├── DEVLOG.md                  # 开发日志（迭代方案、决策、状态）— 详细实现历史见此
├── README.md / README_en.md   # 用户文档
├── .env.example               # 配置模板
└── pyproject.toml
```

## 跨模块核心约定

> 详细实现细节、每个平台的抓取逻辑见 **DEVLOG.md** 对应版本条目。
> 本节只记录架构性的、跨模块的、易忘记的核心约定。

### Service 层

- `feedgrab/service/` 是 CLI、MCP 和未来 GUI 复用的结构化后端 API 层。
- `FetchService.fetch_url()` 内部继续复用 `UniversalReader.read()`，保留既有 Markdown 保存、媒体本地化、去重索引和 session 语义。
- Service 结果通过 `FetchResult.artifacts` 暴露 Markdown 产物路径；不把 artifact 写入 `UnifiedContent.extra`，避免改变 Markdown/front matter 或 MCP `to_dict()` 兼容输出。

### 输出格式

- 每条内容 → 独立 `.md`，按平台分目录（`X/`、`XHS/`、`LinuxDo/`、`mpweixin/`、`Web/` 等）
- Obsidian 兼容 YAML front matter（title/source/author/published/likes/tags 等）
- `utils/storage.py → _format_markdown()` 统一过滤垃圾内容（如 Twitter emoji SVG），新增规则加 `re.sub()` 即可
- Discourse front matter 额外记录 `reply_mode` + `rendered_reply_count`，保证“只抓楼主回复”模式可追溯

### 去重索引

- `{OUTPUT_DIR}/{Platform}/index/item_id_url.json`
- 跨模式统一（单篇/书签/用户推文/搜索补充/作者批量共享）

### HTTP 层

- `utils/http_client.py`：curl_cffi `Session(impersonate="chrome")` TLS 指纹 → requests fallback
- 所有 fetcher 的 `requests.get()`/`urllib` 均走 `http_client.get/post`
- 异常兼容：`except requests.Timeout`/`except requests.RequestException` 无需改动

### User-Agent 与指纹

- `config.py → get_user_agent() + get_stealth_headers()` 集中管理（UA + sec-ch-ua + Accept + Sec-Fetch 全套一致）
- `feedgrab detect-ua` 自动检测本机 Chrome UA
- 依赖 browserforge（未装优雅降级）

### 隐身浏览器引擎（`fetchers/browser.py`）

- 所有 Playwright 抓取统一入口：patchright Tier 1 → playwright Tier 3
- 52 条 Chrome stealth launch args + context 级资源拦截（7 类资源 + 11 tracking 域名）
- 统一 viewport 1920x1080 + locale zh-CN + DPR 2
- `generate_referer(url)` 中国平台→百度，其他→Google
- **例外**：飞书必须用 vanilla `playwright.async_api`（patchright 触发 ERR_CONNECTION_CLOSED）
- 技术方案参考 [Scrapling](https://github.com/D4Vinci/Scrapling)

### CDP 直连（复用运行中 Chrome）

- Cookie 提取：`CHROME_CDP_LOGIN=true` + `CHROME_CDP_PORT=9222`（login.py）
- 飞书：`FEISHU_CDP_ENABLED=true`（复用 Feishu cookie context + localStorage）
- LinuxDo：`LINUXDO_CDP_ENABLED=true`（复用 Chrome Cookie / Cloudflare 会话抓论坛 JSON）
- IDCFlare：`IDCFLARE_CDP_ENABLED=true`（复用 Chrome Cookie / Cloudflare 会话抓论坛 JSON）
- 金山：`KDOCS_CDP_ENABLED=true`
- FlowUs：CDP 复用本机 Chrome（`flowus.cn` cookie context），认证靠 `next_auth` JWT + `next_auth.sig` HMAC **两个 cookie 联合验证**（缺任意一个 API 返回 `code:1407`）
- 关键：`browser.close()` 在 CDP 模式下只断 WebSocket 不杀 Chrome

### 微信 URL 规范化

- `reader.py → _normalize_wechat_url` 剥离追踪参数（`scene`/`click_id`/`sessionid`/`chksm`）
- 只保留 `__biz`/`mid`/`idx`/`sn`
- 短链格式剥离全部 query + fragment
- `feedgrab clip` 从剪贴板读 URL 绕过 PowerShell `&` 报错

### 媒体文件本地化

- `X_DOWNLOAD_MEDIA` / `XHS_DOWNLOAD_MEDIA` / `MPWEIXIN_DOWNLOAD_MEDIA` / `FEISHU_DOWNLOAD_IMAGES` / `KDOCS_DOWNLOAD_IMAGES` / `YOUDAO_DOWNLOAD_IMAGES`
- 下载到 `{md_dir}/attachments/{item_id}/`，MD 中替换为相对路径
- Referer 防盗链：Twitter `name=orig` / XHS 去 `!nd_*` + xiaohongshu referer / WeChat http→https + mp.weixin referer / Feishu 浏览器三阶段预下载

### 分平台关键约定

| 平台 | 关键点 |
|------|--------|
| X/Twitter | `x-client-transaction-id` 签名头必需（SearchTimeline）；queryId 四级解析（disk→community→JS→hardcoded）；feature flags 动态更新 + 紧凑编码；Cookie 多账号轮换（`sessions/twitter.json + x_2.json + ...`）；Article 优先 GraphQL `content_state` 原生渲染 |
| 小红书 | xhshow 签名配置用真实 `platform.system()` + `get_user_agent()`；Cookie 从 `sessions/xhs.json`；xsec_token LRU 缓存 500 条；Pinia Store 注入作为 Tier 0.5 兜底（`XHS_PINIA_ENABLED` 默认 true） |
| LinuxDo / IDCFlare / Discourse | 主路径不是 DOM 抓取，而是 `topic.json`：Tier 0 游客/已保存 session Cookie → Tier 1 CDP 页面内 `fetch(...json)` → Tier 2 Stealth Playwright 页面内 `fetch(...json)` → Tier 3 Jina；明确 404 / 私有 / 需登录时提前终止，不再把错误页写入 Markdown；默认 `reply_mode=author` 只抓主贴 + 楼主自回，可切换 `all` / `none`；本地 `sessions/{platform}.json` 缺失时先做 CDP / browser warmup |
| 飞书 | 必用 vanilla playwright；标题清理零宽字符（U+200B-U+206F, U+FEFF）；Block→MD 支持 20+ 类型；知识库批量优先抓虚拟目录树 `wikiToken`；代码块统一使用 4 个反引号；表格优先读取 `snapshot.rows_id / columns_id` 修复单行错位；图片数据在 `snapshot.image.token`（非 `image.token`） |
| 微信 | Browser 优先（Jina 对 mmbiz 几乎总超时）；Markdown 插入 `<meta name="referrer" content="no-referrer">` 防 mmbiz.qpic.cn 403；评论 API 需微信客户端 session（普通浏览器 "no session" 优雅降级）；**占位页识别**：`browser.py → detect_wechat_unavailable()` 把删除/违规/隐私/风控验证页归一为 `unavailable_reason`，被单篇/账号批量/专辑/搜狗四条链路共用——删除类计 skipped 且写 dedup，风控类计 failed 且**不写 dedup**保持可重试，单篇路径命中后明确终止不降级 Jina；**`mpweixin-id` 限流**：MP 后台"查他人号文章列表"按请求次数配额，超限返回 `ret=200013 freq control`，实测换号/换接口/改 count 均无效，只能靠 `MPWEIXIN_ID_PAGE_SIZE`(默认 20) 压请求数 + `MPWEIXIN_ID_PAGE_DELAY` 放慢节奏；限流/异常中断一律保留分页进度，仅 `publish_list` 为空（真读完）或触发日期截止才清 |
| B站 WBI | `img_key + sub_key`（64 char）按 `MIXIN_KEY_ENC_TAB` 置换取前 32 char = `mixin_key`；值过滤 `!()*'`；`w_rid = md5(query + mixin_key)`；`(img_key, sub_key)` 磁盘缓存 5 分钟 |
| YouTube | InnerTube ANDROID 客户端绕过部分限制；yt-dlp 默认 `YTDLP_COOKIES_BROWSER=chrome` 绕 bot 检测；智能断句：标点拆分→跨 snippet 合并（CJK 无空格/拉丁加空格）→段落分组；标点率 <10% 跳过断句 |
| HackerNews | Firebase API v0（`https://hacker-news.firebaseio.com/v0/...`），0 Cookie / 0 反爬；评论默认抓首屏一层 50 条，递归全树留 v0.21+；HN created_at 输出 ISO 8601（让 `parse_twitter_date_local` 直接消费） |
| Medium | Tier 0 Jina → Tier 1 JSON-LD articleBody → Tier 2 Stealth Browser；用户/出版物批量直接 RSS（`medium.com/feed/<@user|publication>`）+ 单篇 Tier 链补全；member-only 文案匹配 → front matter `is_member_only=true` 优雅降级 |
| Reddit | Tier 0 `old.reddit.com .json` + 自报 UA（`feedgrab/0.20.0`）→ Tier 1 CDP `reddit.com` cookie context → Tier 2 Stealth Playwright + `sessions/reddit.json` → Tier 3 Jina；浏览器内 `fetch()` 复用 Cloudflare 状态；评论按 score 排序 + 过滤 stickied，不展开 "load more comments" |
| Weibo | m.weibo.cn 移动端 API（show/container/getIndex），SUB Cookie 走 `WEIBO_COOKIE` env 或 `sessions/weibo.json`；SSR 兜底解析 `var $render_data=[{...}][0]||`；created_at 用 `email.utils.parsedate_to_datetime` 解析 RFC 2822；转发用 Markdown 引用块嵌套 |
| Douyin | CDP/Launch 共用同一 page 内 `fetch('/aweme/v1/web/aweme/detail/?aweme_id=...')`，浏览器自动签名 a_bogus / X-Bogus / msToken；明确**不破解**签名算法（每月变化）；SSR 兜底解析 `<script id="RENDER_DATA">` URL-encoded JSON；短链 `v.douyin.com/<code>` → 302 → `iesdouyin.com/share/video/<aweme_id>/` |
| 付费墙 | 7 级 Tier 级联（JSON-LD/Googlebot/Bingbot/Generic/AMP/EU IP/archive.today/Google Cache）；`PAYWALL_JSONLD_FOR_ALL=true` 让 Tier 0 对 generic URL 都跑；Googlebot/Bingbot 每次覆盖 UA + Referer + `X-Forwarded-For` + `cookies={}` |
| 知识星球 | Cookie 鉴权（`zsxq_access_token` + 固定 UA + `X-Timestamp` + `X-Version: 2.37.0`，从 [yann0917/knowledge](https://github.com/yann0917/knowledge) 逆向）；articles.zsxq.com SSR HTML 正文在 `.ql-editor`，作者从 `.author-info .nick-name` 取，group_id 从 `.group-info a[href]` 正则；topic API 返回五形态（talk/question+answer/article/solution，solution 是 zsxq 较新的“问答+解决方案”形态，`topic.title` 是提问，`topic.solution.text` 是解答）；短链 `t.zsxq.com/<code>` 跳转 H5 形态 `?topic_id=<digits>` 用 query 解析；明确终止 401/404/business-failed 不下沉 Jina（避免落地登录页 .md） |
| Whisper 共享 | `utils/transcribe.py` 4 个公开函数（`groq_transcribe_file`/`groq_transcribe_url`/`format_transcript`/`subtitle_body_to_snippets`）委托 youtube.py 内部函数，不重构 youtube.py |

### 诊断命令

- `feedgrab doctor` — 所有平台
- `feedgrab doctor x` / `xhs` / `mpweixin` / `feishu` — 按平台分区检查 Cookie/依赖/网络
- 别名：`twitter`→`x`，`wechat`→`mpweixin`

## 迭代历史摘要

> 完整记录见 `DEVLOG.md`。以下仅列最近版本。

| 版本 | 功能 |
|------|------|
| v0.25.0 | 第一阶段 service layer 架构升级：新增 `feedgrab/service/` 的结构化 API（models / FetchService / Output / Login / Settings / Doctor / Job），CLI 单 URL 和 MCP 入口改为共用 `FetchService`，保持终端命令、Markdown 输出、去重索引和 session 格式兼容；修复 MP 后台 session 失效错误被掩盖问题；FlowUs 在线图片模式改为写入可预览的 `cdn2.flowus.cn` 签名 URL，本地模式仍通过 `FLOWUS_DOWNLOAD_IMAGES=true` 下载 `attachments/`；测试 210 passed |
| v0.24.1 | 修复 Twitter 多账号 429 轮换：抽出 `fetch_with_cookie_rotation()` helper（`twitter_cookies.py`），统一 7 个批量 fetcher（user_tweets / bookmarks / list / user_lists / retweeters / search_people / keyword_search）的"账号限流后跨账号重试"逻辑；之前重试仅复用同一被限流账号 3 次就停（user_tweets）或直接 break（其余 6 个），现在改为**每个账号都试一遍**才真正终止；关键日志统一加 `>>> ... <<<` 高亮 + 剩余可用账号数 + 最早解封倒计时；测试 193 → 201；实测 `feedgrab https://x.com/AdrianPunk115` 抓取量 557 → 632（+13.4%） |
| v0.24.0 | 新增「FlowUs 息流」（flowus.cn）平台支持（公开分享 + 付费/私有分享 + 个人空间链接）；Notion 风格 block-tree 扁平 JSON 渲染（8 类 block：page/paragraph/bullet/ordered/heading/quote/media/code + 5 种 enhancer + 链接片段）；纯 HTTP 链路（公开零 cookie / 付费需 `next_auth`+`next_auth.sig` 双 cookie）+ CDP/Launch 浏览器兜底；图片本地化：headless 浏览器渲染 + 多 pass 滚动 + lazy→eager 抓 `cdn2.flowus.cn` 签名 URL（59/59 全成功） |
| v0.23.0 | twitter-web-exporter 融合 Phase 2 五项功能：P2-1 头像原图替换（`_normal/_bigger/_mini/_400x400` → 原图）+ P2-3 Retweeters/Favoriters（`/status/<id>/retweets` `/status/<id>/likes` URL 路由 + `x-retweeters` `x-favoriters` CLI）+ P2-4 SearchTimeline `product=People` 人物搜索（`x-so --people`）+ P1-3 ModeratedTimeline thread Phase 8 接入（`X_FETCH_MODERATED_REPLIES` opt-in，404 优雅降级）+ P2-2 X 媒体文件名 pattern 系统（`X_MEDIA_FILENAME_PATTERN` opt-in 9 token + path traversal 安全化）；测试 153 → 193；P1-1 通用 instruction helper 重构推迟 v0.23.1 |
| v0.22.0 | 融合 [twitter-web-exporter](https://github.com/prinsss/twitter-web-exporter)：补 5 个高价值 GraphQL operation（Followers / Following / BlueVerifiedFollowers / ListMembers / ListSubscribers / Likes / UserTweetsAndReplies，queryId 取自 fa0311/twitter-openapi）+ 3 个解析鲁棒性增强（TweetTombstone/TweetUnavailable 显式日志 + TimelinePinEntry 置顶提取 + 视频 variant 不过滤 content_type）；新 URL pattern：`/followers` `/following` `/verified_followers` `/likes` `/with_replies` `/i/lists/<id>/members(subscribers)` |
| v0.21.0 | 新增「知识星球」（Zsxq）平台支持（articles.zsxq.com 长文章 + wx.zsxq.com 短帖 + t.zsxq.com 短链 302 解析）；4 级 Tier 链路（HTTP cookie / CDP / Stealth Browser / Jina）+ 五形态 topic 渲染（含 solution）+ 三态评论筛选 |
| v0.20.1 | 修复 Twitter 长 thread 被误判为 Article 导致 quoted tweet 丢失（`schema.from_twitter` 改用 `_has_article_body` 看 article_data 实际内容，统一 thread/article 渲染路径） |
| v0.20.0 | 五平台扩展：HackerNews（Firebase API + 列表批量）/ Medium（Jina + RSS）/ Reddit（.json + CDP 兜底 + reddit-sub）/ Weibo（m.weibo.cn API + weibo-user）/ Douyin（CDP/Launch/SSR 三级 Tier）|
| v0.19.0 | IDCFlare 平台支持 + Discourse 回复模式/会话预热 + 飞书知识库目录/表格/代码块修复 |
| v0.18.0 | LinuxDo / Discourse 平台支持 + JSON-first 多级兜底 + 折叠块混合渲染（Obsidian callout / 原生 details） |
| v0.17.0 | 小宇宙 / 喜马拉雅 / B 站字幕三平台 + WBI 签名自研 + Whisper 共享薄层 |
| v0.16.0 | 付费墙 7 级绕过 + JSON-LD articleBody 提取 + `SourceType.WEB` |
| v0.15.x | 飞书嵌入表格错位修复 + YouTube Whisper 时间戳 + 知乎平台 |
| v0.14.x | 金山文档 + 有道云笔记 平台支持 |
| v0.13.x | x-so 三级兜底 + Article 超链接完整保存 + GraphQL 热路径优化 + XHS Pinia 注入 + 飞书 CDP |
| v0.12.x | CDP Cookie 提取 + 微信专辑批量 + 媒体文件本地化 + 微信视频提取 + GitHub 中文 README 增强 |
| v0.11.x | 飞书平台支持（+ 知识库批量 + 嵌入表格 + 图片下载） |
| v0.10.x | 小红书 API 层（xhshow）+ `xhs-so` 搜索 + 多关键词批量 |
| v0.9.x | GraphQL 冷启动加速 + 微信 MP 账号批量 + curl_cffi 统一 + YouTube 搜索/下载 + GitHub README |
| v0.8.x | 隐身浏览器引擎 + browserforge + FxTwitter + 搜狗微信搜索 |
| v0.7.x | GraphQL 数据完整提取 + 引用推文 + richtext + 扩展 front matter |
| v0.6.x | Twitter Article 原生渲染 + List 批量 + Syndication |
| v0.5.x | TwitterAPI.io + Cookie 多账号轮换 + 断点续传 |
| v0.1-0.4 | 初始版本 + 书签/用户推文批量 + XHS 深度抓取 + Article 检测 |

## 关键文件速查

| 需求 | 看哪个文件 |
|------|-----------|
| 结构化 service API | `feedgrab/service/`，重点 `fetch.py` / `models.py` |
| 新增 CLI 命令 | `cli.py → main()` 路由 + `cmd_xxx()` |
| 新增环境变量 | `config.py` + `.env.example` |
| 新增平台 fetcher | `fetchers/xxx.py` + `reader.py` 路由 |
| 修改输出格式 | `utils/storage.py` |
| 修改数据模型 | `schema.py` |
| 去重逻辑 | `utils/dedup.py` |
| X/Twitter | `twitter*.py`（11 个文件） |
| YouTube | `youtube.py`（字幕/转录）+ `youtube_search.py`（搜索/下载） |
| GitHub | `github.py`（REST API + 中文 README 优先） |
| LinuxDo / IDCFlare / Discourse | `linuxdo.py` + `idcflare.py`（topic JSON → CDP → 浏览器 → Jina） |
| HackerNews | `hackernews.py`（Firebase API v0 + 列表批量 + 一层评论） |
| Medium | `medium.py`（Jina + JSON-LD + Browser + RSS feed 批量） |
| Reddit | `reddit.py`（.json + CDP/Browser fetch + reddit-sub） |
| Weibo | `weibo.py`（m.weibo.cn show + container/getIndex + SSR 兜底） |
| Douyin | `douyin.py`（CDP/Launch + SSR RENDER_DATA + 短链 302 解析） |
| 知识星球 | `zsxq.py`（articles SSR HTML + topic API JSON + 五形态 + 短链 302） |
| 小红书 | `xhs*.py`（5 个文件）+ `browser.py` |
| 飞书 | `feishu.py` + `feishu_wiki.py` + `browser.py` |
| 金山文档 | `kdocs.py` |
| FlowUs 息流 | `flowus.py`（HTTP API → CDP → Launch browser → Jina；block-tree 渲染 + DOM 抓 cdn2.flowus.cn 签名 URL 图片下载） |
| 有道云笔记 | `youdao.py` |
| 知乎 | `zhihu.py` + `zhihu_search.py` |
| 小宇宙 / 喜马拉雅 | `xiaoyuzhou.py` + `ximalaya.py` + `utils/transcribe.py` |
| B站字幕 / WBI | `bilibili.py` + `utils/bilibili_wbi.py` |
| 付费墙 / 通用网页 | `paywall.py` + `utils/jsonld.py` |
| 隐身浏览器 | `fetchers/browser.py`（52 条 stealth args + 资源拦截） |
| CDP Cookie | `login.py`（7 平台支持，含 LinuxDo / Zsxq） |
