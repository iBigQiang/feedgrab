# feedgrab 项目指令

## 项目概述

feedgrab 是一个万能内容抓取器，从任意平台抓取内容并输出为 Obsidian 兼容的结构化 Markdown。

- **仓库**：https://github.com/iBigQiang/feedgrab
- **作者**：[@iBigQiang](https://github.com/iBigQiang)（强子手记）
- **当前版本**：v0.25.2
- **Python**：≥3.10
- **许可证**：MIT

### 项目来源

feedgrab 由两个项目融合升级而来：
- **[x-reader](https://github.com/runesleo/x-reader)**（@runes_leo）— 提供多平台架构、CLI、MCP 服务器
- **[baoyu-danger-x-to-markdown](https://github.com/JimLiu/baoyu-skills)**（@dotey 宝玉）— 提供逆向工程的 X/Twitter GraphQL 深度抓取能力

### 三层架构

| 层级 | 功能 | 入口 |
|------|------|------|
| Python CLI/库 | 基础内容抓取 + 统一数据结构 | `feedgrab <url>` |
| Codex 技能 | 视频转录 + AI 分析 | `skills/video/` `skills/analyzer/` |
| MCP 服务器 | 将抓取能力暴露为 MCP 工具 | `mcp_server.py` |

### 支持的平台

| 平台 | 抓取方式 |
|------|---------|
| X/Twitter | GraphQL → FxTwitter → Syndication → oEmbed → Jina → Playwright（六级兜底） |
| 小红书 | API (xhshow) → Pinia Store 注入 → Jina → Playwright 深度抓取（单篇 + 作者批量 + 搜索批量 + xhs-so 搜索） |
| YouTube | InnerTube API 字幕（零依赖零 quota）→ yt-dlp 字幕 → Groq Whisper 转录 + YouTube Data API v3 搜索 + yt-dlp 下载 |
| B站 | view API 元数据 + 字幕 3 级兜底（`player/v2` → `player/wbi/v2` WBI → Whisper 可选） |
| 微信公众号 | Playwright WeChat JS 提取 → Jina 兜底（单篇 + markdownify 富文本）/ 搜狗搜索（关键词搜索）/ MP 后台 API（按账号批量）/ 专辑批量（`mpweixin-zhuanji`） |
| GitHub | REST API（仓库元数据 + 中文 README 优先 + 摘要提取） |
| LinuxDo / IDCFlare / Discourse | Discourse Topic JSON API → CDP 复用 Chrome → Playwright 页面内 fetch → Jina（默认主贴 + 楼主自回，可切换完整楼层） |
| 飞书/Lark | Open API → CDP 直连 → Playwright PageMain Block 树 → Jina（单篇 + 知识库批量 + 嵌入表格 + 图片下载；修复虚拟目录树与表格错位） |
| 金山文档/KDocs | Playwright ProseMirror DOM 提取（虚拟滚动 + 代码块 + 图片 shapes API + CDP 直连） |
| FlowUs 息流 | Tier 0 纯 HTTP `/api/docs/{uuid}`（公开零 cookie / 付费需 `next_auth`+`next_auth.sig` 双 cookie）→ CDP → Launch+saved session → Jina；Notion 风格 block-tree 渲染；默认在线签名图 URL，可开启本地图片附件 |
| 有道云笔记 | JSON API（零依赖）→ Playwright iframe DOM → Jina（单篇 + 图片下载） |
| 知乎 | API v4 → Playwright CDP/DOM → Jina（单篇问答前 3 楼 + 专栏文章 + 关键词搜索 `zhihu-so`） |
| Telegram | Telethon |
| 小宇宙 | SSR `__NEXT_DATA__` + Groq Whisper 转录 |
| 喜马拉雅 | Web Revision API + canPlay 降级 + Groq Whisper（免费节目） |
| RSS | feedparser |
| 知识星球 | Tier 0 HTTP cookie（articles SSR HTML / topic API JSON）→ Tier 1 CDP 复用 → Tier 2 Stealth Browser → Tier 3 Jina；短链 t.zsxq.com 302 解析；topic 五形态：talk/question+answer/article/solution；评论三态 |
| 付费新闻（300+） | 7 级 Tier 绕过（JSON-LD → Googlebot/Bingbot UA → AMP → EU IP → archive.today → Google Cache → Jina） |
| 任意网页 | JSON-LD 前置探测 → Jina 兜底 |

## 语言规范

- 所有对话、文档内容一律使用**中文**
- 代码注释可以用英文
- Git commit message 使用英文前缀（feat/fix/docs/chore）+ 英文描述

## 开发工作流

完成功能开发并测试通过后，执行以下收尾流程：

1. **更新 DEVLOG.md** — 在文件顶部（第一个 `---` 分隔线之前）新增版本条目
2. **更新 README.md / README_en.md** — 同步新功能的使用说明、配置项、架构图等
3. **提交代码** — 使用 conventional commit 格式
4. **推送到 GitHub** — `git push origin main`

> 提示：可以使用 `/ship` 命令一键完成上述收尾流程。

## Codex 快捷命令约定

为兼容 Codex，本项目将 Claude Code 的命令文件映射为“触发词 + 脚本”模式：

- 当用户输入 `/newup` 时，视为执行项目启动预热：
  - 优先使用 Git Bash 运行 `scripts/newup.sh`
  - 若 Git Bash 不可用，再回退运行 `scripts/newup.ps1`
  - 同时读取 `CLAUDE.md`、`AGENTS.md`、`DEVLOG.md`、`README.md`
  - 输出当前版本、最近一次迭代标题、项目上下文摘要和 AGENTS 规则预览，然后再继续当前任务
- 当用户输入 `/ship` 时，视为执行收尾检查流程：
  - 优先使用 Git Bash 运行 `scripts/ship.sh`
  - 若 Git Bash 不可用，再回退运行 `scripts/ship.ps1`
  - 查看 `git status --short`、`git diff --stat`
  - 检查 `DEVLOG.md`、`README.md`、`README_en.md`、`AGENTS.md` 是否存在
  - 基于检查结果继续执行文档更新、提交、推送等后续动作
- `.claude/commands/newup.md` 与 `.claude/commands/ship.md` 保留给 Claude Code 使用，Codex 不直接读取它们作为 slash 命令入口

## 版本号规范

- 主要新功能：递增次版本号（如 v0.2.9 → v0.3.0）
- 小功能/修复：递增补丁号（如 v0.3.0 → v0.3.1）
- 版本号记录在 DEVLOG.md 的条目标题中

## 核心架构

```
feedgrab/
├── feedgrab/                  # Python 包
│   ├── cli.py                 # CLI 入口（命令路由 + feedgrab setup 引导 + feedgrab clip 剪贴板抓取）
│   ├── config.py              # 集中配置（路径、开关、get_user_agent() + get_stealth_headers()）
│   ├── reader.py              # URL 调度器（UniversalReader — 平台检测 + 路由 + URL 规范化）
│   ├── schema.py              # 统一数据模型（UnifiedContent）
│   ├── login.py               # 浏览器登录管理器（+ CDP Cookie 提取）
│   ├── service/               # 服务层 API（FetchService / Output / Login / Settings / Doctor / Job）
│   ├── fetchers/
│   │   ├── jina.py            # Jina Reader（万能兜底）
│   │   ├── browser.py         # 隐身浏览器引擎（patchright Tier 1 → playwright Tier 3 + stealth flags）
│   │   ├── bilibili.py        # B站 API
│   │   ├── youtube.py         # yt-dlp 字幕提取 + API 优先元数据
│   │   ├── youtube_search.py  # YouTube Data API v3 搜索 + yt-dlp 下载（视频/音频/字幕）
│   │   ├── github.py          # GitHub REST API（仓库元数据 + 中文 README 优先）
│   │   ├── rss.py             # RSS 解析
│   │   ├── telegram.py        # Telegram 频道
│   │   ├── twitter.py         # X/Twitter 六级兜底调度器
│   │   ├── twitter_cookies.py # Cookie 多源管理 + 多账号轮换（429 自动切换）
│   │   ├── twitter_fxtwitter.py # FxTwitter API 客户端（Tier 0.3 兜底 + circuit breaker）
│   │   ├── twitter_graphql.py # GraphQL API（TweetDetail/UserTweets/Bookmarks/SearchTimeline/动态queryId + x-client-transaction-id）
│   │   ├── twitter_thread.py  # 线程重建 + 评论分类
│   │   ├── twitter_bookmarks.py  # 书签批量抓取
│   │   ├── twitter_user_tweets.py # 用户推文批量抓取
│   │   ├── twitter_list_tweets.py # List 列表批量抓取（按天数过滤+会话去重）
│   │   ├── twitter_search_tweets.py # 浏览器搜索补充抓取（突破 UserTweets 800 条限制）
│   │   ├── twitter_keyword_search.py # 关键词搜索（x-so 命令，纯 GraphQL + 互动排序表格）
│   │   ├── twitter_api.py     # TwitterAPI.io 付费 API 客户端
│   │   ├── twitter_api_user_tweets.py # 付费 API 批量抓取（max_id 分页+断点续传+智能直保）
│   │   ├── twitter_markdown.py   # Markdown 渲染器
│   │   ├── wechat.py          # Playwright → Jina（Browser 优先）
│   │   ├── wechat_search.py   # 搜狗微信搜索（关键词 → 文章发现 + 抓取）
│   │   ├── mpweixin_account.py # 微信公众号按账号批量（MP 后台 API + 断点续传）
│   │   ├── mpweixin_album.py  # 微信公众号专辑批量（mpweixin-zhuanji 命令 + 断点续传）
│   │   ├── xhs.py             # 小红书单篇（API → Pinia → Jina → Playwright 四级兜底）
│   │   ├── xhs_api.py         # 小红书 API 客户端（xhshow 签名 + 评论 + xsec_token 缓存）
│   │   ├── xhs_pinia.py       # 小红书 Pinia Store 注入（浏览器原生请求兜底，CDP 优先 + Launch 降级）
│   │   ├── xhs_user_notes.py  # 小红书作者批量（API → Pinia → 浏览器三层策略）
│   │   ├── xhs_search_notes.py # 小红书搜索批量 + xhs-so 关键词搜索（含 Pinia 兜底）
│   │   ├── feishu.py          # 飞书单篇（Open API → Playwright PageMain → Jina + Block→MD + Sheet Protobuf 解码 + 图片下载）
│   │   ├── feishu_wiki.py     # 飞书知识库批量（Open API 递归 + Playwright 虚拟目录树兜底 + 断点续传）
│   │   ├── kdocs.py           # 金山文档单篇（Playwright ProseMirror DOM + 虚拟滚动 + CDP 直连 + 图片 shapes API）
│   │   ├── linuxdo.py         # LinuxDo / Discourse 论坛帖子（topic JSON → CDP → 浏览器 → Jina）
│   │   ├── idcflare.py        # IDCFlare / Discourse 论坛帖子（topic JSON → CDP → 浏览器 → Jina）
│   │   ├── youdao.py          # 有道云笔记单篇（JSON API + Playwright iframe DOM + Jina 三级兜底）
│   │   ├── zhihu.py           # 知乎单篇（API v4 → Playwright CDP/DOM → Jina + 前 3 楼多回答）
│   │   └── zhihu_search.py    # 知乎关键词搜索（API/Playwright 双层 + 汇总表格 + CSV）
│   └── utils/
│       ├── storage.py         # 按平台分目录 Markdown 输出 + YAML front matter
│       ├── dedup.py           # 全局去重索引
│       ├── http_client.py     # 统一 HTTP 客户端（curl_cffi TLS 指纹 → requests fallback）
│       └── media.py           # 媒体文件下载（Twitter/XHS 图片视频本地化 + MD URL 替换）
├── skills/                    # Codex 技能
├── mcp_server.py              # MCP 服务器入口
├── DEVLOG.md                  # 开发日志（迭代方案、决策、状态）
├── README.md                  # 用户文档（中文）
├── README_en.md               # 用户文档（英文）
├── .env.example               # 配置模板
└── pyproject.toml             # 包定义
```

## 关键设计决策

### Service 层（`feedgrab/service/`）

`feedgrab/service/` 是 CLI、MCP 和未来 GUI 复用的结构化后端 API 层。`FetchService.fetch_url()` 内部继续复用 `UniversalReader.read()`，保留既有 Markdown 保存、媒体本地化、去重索引和 session 语义。Service 通过 `FetchResult.artifacts` 暴露产物路径，不把 artifact 写入 `UnifiedContent.extra`，避免改变 Markdown/front matter 或 MCP `to_dict()` 兼容输出。

### GitHub 仓库 README 抓取（`fetchers/github.py`）

3 次 API 调用完成抓取：`/repos/{owner}/{repo}`（元数据）→ `/repos/{owner}/{repo}/contents/`（根目录列表）→ README 内容。中文 README 优先：从根目录列表匹配 8 种变体（`README_CN.md`、`README.zh-CN.md` 等），命中则获取中文版，否则用默认 README。**子目录中文 README 搜索**：根目录未命中时，`_find_chinese_readme_from_content()` 解析默认 README 中的语言导航链接，同时支持 Markdown `[中文](url)` 和 HTML `<a href="url">🇨🇳 中文</a>` 两种格式，6 种中文标记模式（中文/简体中文/繁體中文/Chinese/ZH-CN/zh-CN），允许 emoji/bold/italic 等任意前缀后缀，支持相对路径和完整 GitHub URL，零额外 API 调用。**直接文件 URL 支持**：`parse_github_url()` 返回 `(owner, repo, file_path)` 三元组，从 `/blob/{branch}/path/to/file` URL 提取文件路径，`fetch_github()` 优先直接获取指定文件。**相对图片链接补全**：`_resolve_relative_urls()` 将 Markdown `![](path)` 和 HTML `<img src="path">` 的相对路径转为 `raw.githubusercontent.com` 绝对 URL，基于 README 所在目录计算 base_dir，`../` 路径规范化。`_extract_readme_summary()` 从 README 提取第一行有意义的描述文本作为标题（跳过 heading、badge、HTML、blockquote、短于 15 字符的行、翻译声明行）。`item_id = MD5("{owner}/{repo}")[:12]` 保证仓库级别去重。无 Token 60 次/小时，有 Token 5000 次/小时。

### Twitter 关键词搜索（`twitter_keyword_search.py`）

`feedgrab x-so <keyword>` 通过三级兜底策略搜索 Twitter，输出按查看数降序排列的 Markdown 汇总表格 + CSV。**Tier 0 GraphQL**（SearchTimeline 端点，<2s/页）→ **Tier 1 CDP 直连**（复用已打开 Chrome，Cookie 域名匹配 `.x.com`/`.twitter.com`，秒级启动）→ **Tier 2 Playwright launch**（隐身浏览器 + session 预热）。浏览器路径复用 `SearchResponseCollector` + `_scroll_and_collect_search`（来自 `twitter_search_tweets.py`），数据格式完全兼容（同一 `extract_tweet_data()`）。`X_SEARCH_BROWSER_FALLBACK=true`（默认）控制是否启用浏览器兜底。支持逗号分隔多关键词批量搜索（`feedgrab x-so "k1,k2,k3"`），`--merge` 合并结果到一个文件（含关键词列），默认分开生成。`build_search_query()` 自动拼接高级搜索运算符（lang/since/min_faves/-is:retweet 等），关键词自动包引号。MD 表格中内容摘要为超链接（无独立链接列），CSV 保留明文链接列。`_generate_summary_table()` 同时输出 `.md`（Obsidian `cssclasses: wide`）和 `.csv`（UTF-8 BOM），合并模式下按查看数全局排序 + 添加关键词列。12 个 `X_SEARCH_*` 配置函数提供默认值。`--raw` 模式让用户完全控制查询语法。`X_SEARCH_SAVE_TWEETS=true` 可选保存单篇推文 .md 到子目录。输出路径：`X/search/{days}day_{new|hot}/{keyword}_{date}.{md,csv}`。

### x-client-transaction-id 反检测（`twitter_graphql.py`）

Twitter 对 SearchTimeline 等 GraphQL 端点强制要求 `x-client-transaction-id` 签名头（缺失返回 404）。`_get_transaction_id()` 使用 `XClientTransaction` 库纯 Python 生成此值：从 x.com 主页提取 SVG 动画帧数据 + `twitter-site-verification` 密钥 + `ondemand.s` JS 索引，经 Cubic Bezier 插值 + SHA-256 签名 + XOR 混淆 + Base64 编码。生成器实例内存缓存 30 分钟复用（`_TRANSACTION_TTL = 1800`），**源数据磁盘缓存 1 小时**（`{data_dir}/cache/twitter_transaction_cache.json`），进程重启时零 HTTP 冷启动。在 `_execute_graphql()` 中自动注入所有 GraphQL 请求。未安装 `XClientTransaction` 时优雅降级并输出安装提示。依赖 `pip install XClientTransaction beautifulsoup4`。

### queryId 四级解析（`twitter_graphql.py`）

`resolve_query_ids()` 从四个来源按优先级解析 GraphQL 操作的 queryId：Tier 0 磁盘缓存（`{data_dir}/cache/twitter_queryid_cache.json`，1 小时 TTL）→ Tier 1 社区源（`fa0311/twitter-openapi` GitHub 仓库，单次 HTTP 获取 87 个 queryId）→ Tier 2 JS bundle 扫描（x.com 首页 HTML → JS chunk 下载 → 正则提取）→ Tier 3 硬编码回退常量。社区源命中时跳过 JS bundle 扫描（省多次 HTTP）。磁盘缓存命中时零 HTTP 请求。硬编码回退值定期从社区源同步更新。

### X/Twitter 六级兜底

Tier 0 GraphQL（需Cookie）→ Tier 0.3 FxTwitter（免费无认证，第三方公共API）→ Tier 0.5 Syndication（免费无认证）→ Tier 1 oEmbed → Tier 2 Jina → Tier 3 Playwright

FxTwitter 数据完整度接近 GraphQL（有 views/bookmarks/Article Draft.js），缺少 blue_verified/listed_count/线程展开。批量模式连续 3 次失败触发 circuit breaker 临时跳过。

Article 长文在 Tier 0 命中后优先用 GraphQL `content_state` 原生渲染，仅当渲染结果不足 200 字时回退 Jina。

### 小红书 API 层 + 多层兜底（`fetchers/xhs_api.py` + `xhs.py`）

**单篇**：Tier 0 API Feed (xhshow) → Tier 0.5 Pinia Store 注入 → Tier 1 Jina → Tier 2 Playwright。`xhshow` 签名库生成 `x-s`/`x-s-common`/`x-t` 等反爬头，纯 HTTP 调用 `edith.xiaohongshu.com` API，单篇 <1s。签名配置使用真实系统平台和 UA（`platform.system()` + `get_user_agent()`），避免 Windows 环境用 macOS UA 被反爬识别。Cookie 从 `sessions/xhs.json` Playwright storage_state 提取（`a1`/`web_session` 等）。

**Pinia Store 注入**（Tier 0.5 兜底）：当 xhshow 签名失效时，通过 Playwright 在小红书页面上下文注入 JS，调用 Vue Pinia Store Action 触发浏览器原生 XHR 请求，XHR monkey-patch 拦截原始 JSON 响应。签名/Cookie/TLS 全部天然真实，不依赖第三方签名库。CDP 优先（复用运行中 Chrome，零启动开销）→ Launch 降级（加载 session + headed 模式）。支持单篇 Feed、搜索、用户笔记三种场景。`XHS_PINIA_ENABLED` 默认 true。

**作者批量**：API cursor 自动分页（30条/页）→ Pinia Store 兜底 → 浏览器三层策略兜底。

**搜索批量**：API page 分页（20条/页）+ 排序/类型筛选 → Pinia Store 兜底 → 浏览器 Tier 0/1/2 兜底。

**浏览器批量策略**（降级后）：Tier 0 `__INITIAL_STATE__`（SSR数据，~40篇）→ Tier 1 XHR拦截+滚动加载 → Tier 2 逐篇深度抓取。

**xhs-so 搜索命令**：`feedgrab xhs-so <keyword>` 纯 API 搜索 + 互动排序汇总表（MD + CSV），仿 `x-so` 模式。支持逗号分隔多关键词批量搜索（`feedgrab xhs-so "k1,k2,k3"`），`--merge` 合并结果到一个文件（含关键词列），默认分开生成。支持 `--sort popular/latest`、`--type video/image`、`--save` 保存单篇。合并模式下按点赞数全局排序 + 添加关键词列。

**评论抓取**：`XHS_FETCH_COMMENTS=true` 时调用评论 API（cursor 分页），提取评论全文 + 子评论，渲染为 Markdown blockquote。

**xsec_token 缓存**：LRU 磁盘缓存 500 条（`sessions/cache/xhs_token_cache.json`），搜索/作者批量结果自动缓存 token。

**反检测**：Gaussian 抖动（base ± gauss(0.3, 0.15)）+ 5% 长暂停 + 验证码冷却（461/471 → 5→10→20→30s 递增）+ 指数退避重试。

`xhshow` 为可选依赖（`pip install xhshow`），未安装时跳过 API 层，完全不影响现有浏览器模式。

### 隐身浏览器引擎（`fetchers/browser.py`）

所有 Playwright 浏览器抓取统一使用隐身引擎：patchright（Tier 1）→ playwright（Tier 3）。patchright 在 Chromium CDP 协议层移除 `navigator.webdriver` 和 `Runtime.enable` 等自动化检测标记。52 条 Chrome 隐身启动参数（STEALTH_LAUNCH_ARGS）覆盖反检测、指纹伪装、性能优化；5 条有害默认参数（HARMFUL_DEFAULT_ARGS）被屏蔽。Context 配置统一 viewport 1920x1080 + screen + locale zh-CN + dark color_scheme + DPR 2。`generate_referer(url)` 自动生成搜索引擎 referer（中国平台→百度、其他→Google），首次导航时设置。`setup_resource_blocking(context)` 在 context 级别拦截 7 类非必要资源 + 11 个 tracking 域名，加速批量抓取。安装 `pip install patchright` 后自动启用，无需配置变更。技术方案适配自 [Scrapling](https://github.com/D4Vinci/Scrapling)。

### User-Agent 集中管理

`config.py` → `get_user_agent()` + `get_stealth_headers()`，所有浏览器交互统一 UA，避免 session 失效。`feedgrab detect-ua` 自动检测本机 Chrome UA。`get_stealth_headers()` 通过 browserforge 生成全套一致 header（UA + sec-ch-ua + Accept + Sec-Fetch 等），按 Chrome 版本号 pin 生成，会话级缓存。未安装 browserforge 时优雅降级并输出安装提示。

### curl_cffi TLS 指纹统一

`utils/http_client.py` 提供统一 HTTP 客户端：curl_cffi `Session(impersonate="chrome")` 模拟 Chrome TLS 指纹（JA3/JA4 完全匹配），fallback 到标准 requests。所有 fetcher 的 `requests.get()`/`urllib.request.urlopen()` 均已迁移到 `http_client.get()`/`http_client.post()`。异常兼容层确保现有 `except requests.Timeout`/`except requests.RequestException` 代码无需改动。`raise_for_status()` 辅助函数包装 curl_cffi 的状态码异常为 `requests.HTTPError`。

### 全局去重索引

`{OUTPUT_DIR}/{Platform}/index/item_id_url.json`，跨模式统一去重（单篇/书签/用户推文/搜索补充/作者笔记/搜索共享）。

### 浏览器搜索补充抓取

UserTweets API 受服务端限制（~800条），通过 Playwright 浏览器按月分片搜索补充历史推文。使用 `page.on("response")` 在 Python 层面拦截 SearchTimeline GraphQL 响应，跨导航持久有效。两阶段共享去重索引。

### TwitterAPI.io 付费 API

替代浏览器搜索补充的服务器友好方案（$0.15/千条）。配置 `TWITTERAPI_IO_KEY` 后超过 800 条时自动走 API 替代浏览器搜索。`X_API_PROVIDER=api` 可全量走付费 API（无需 Cookie）。使用 max_id 分页（Snowflake ID 递增），断点续传（JSONL 缓存），智能直保（普通推文直接保存，长文/线程走 GraphQL）。注意：TwitterAPI.io 的 `since:`/`until:`/直接 `max_id` 跳转操作符均不可靠，日期过滤在代码层完成。

### Cookie 多账号轮换

`sessions/` 目录支持多个 Cookie 文件（`twitter.json` + `x_2.json` + `x_3.json`...），GraphQL 429 时自动切换到未限流账号，15 分钟冷却后恢复。

### X 渠道权限边界与主账号锁定（`twitter_cookies.py`）

轮换只对**公开**数据有意义。账号私有渠道只有唯一一个登录态读得到，轮换过去只会丢掉那个唯一可能成功的账号。2026-08-31 实测（主号 `@iBigQiang` vs 备用号 `x_2.json`）确认 X 有**三级**权限边界：

| 渠道 | 边界 | 机制 |
|------|------|------|
| 书签（`/i/history`、`/i/history/bookmarks/<id>`） | 账号本人 | `primary_only=True` |
| 用户 likes（`/<user>/likes`） | **账号本人** | `primary_only=True`；备用号抓公开主页的 likes 也是空 timeline |
| favoriters（`/status/<id>/likes`、`x-favoriters`） | **推文作者本人** | `primary_only=True`；对第三方 12817 赞推文实测，非作者账号一律空 timeline |
| retweeters（`/status/<id>/retweets`、`x-retweeters`） | 公开 | 保持轮换 |
| 单贴 / 长文 / 账号批量 / replies / 列表 / 列表成员 / followers / following / 关键词搜索 / 人物搜索 | 公开 | 保持轮换 |

实现三件套：`is_primary_cookie_label()` 判定主账号（`env` / `playwright(twitter.json)` / `cookie_file(x.json)` / `chrome_cdp` 为主号，`x_2`~`x_6` / `twitter_2` 为备用号）；`load_primary_twitter_cookies()` **只**返回主账号，绝不降级到备用号；`fetch_with_cookie_rotation(primary_only=True)` 单发不轮换，无主账号时连请求都不发。

**闸门只有一个判据**：`twitter_retweeters.mode_requires_primary(mode)` 读 `_MODE_CONFIG`，`reader.py` 的 `_read_tweet_user_list` 与 `cli.py` 的 `cmd_twitter_tweet_user_list` **共用**它 —— 两个入口都在缺主账号时前置 `RuntimeError` / 中文指引，不拿备用号白跑一趟拿空结果。未知 mode 保守要主账号。新增 author-only 渠道只改 `_MODE_CONFIG` 一处即可，不要在调用点重写 mode 名。

两个易漏的边界：① `parse_tweet_user_list_url` 必须带 `(?:[\w-]+\.)?` 子域组 —— `_detect_platform` 只看 domain 含 `x.com`，`www.x.com` / `mobile.twitter.com` 会照常路由进来，正则漏了子域就解析成 `(None, None)`，闸门落空且功能整条 `ValueError`。② 钉死单账号后没有可跳的对象，`load_twitter_cookies()` 的"跳过限流账号"逻辑失效，必须显式查 `cookie_rate_limit_remaining()`：冷却期内直接终止不发那个必然 429 的请求，失败归因分「本次触发限流（登录态没问题，等冷却）」与「无响应（请检查登录态是否过期）」两支，别把 15 分钟冷却报成"登录态已过期"。

**空结果的正确归因**：likes / favoriters 为空意味着「你不是本人/作者」，**不是**「对方隐藏了列表」——X 只把这两个列表暴露给唯一一个账号。此前代码文案把平台机制说成对方的隐私设置，会误导用户去调设置，已修正。


### 线程抓取的目标推文校验（`twitter_thread.py`）

`fetch_tweet_thread(tweet_id, ...)` 重建的是**根推作者的自回复链**，不是「以 tweet_id 为中心的会话」。`_filter_same_thread` 先把 `id == conversation_id` 的推文认作 root，再用 `_is_same_thread` 过滤，**首条判据就是 `user_id != root_user_id -> False`**。

危险之处在于入口：`_classify_tweet` 只看「是不是回复」（`conversation_id != id` 或 `in_reply_to` 非空），**任何回复推文都会被送进线程路径**——包括别人在该 thread 下的回复。这类目标必然被同作者过滤掉，而组装阶段又只用 `thread_tweets[0]`（根推），传入的 `tweet_id` 被完全丢弃。结果：抓 A 的回复，落盘的是 B 的根推，正文彻底丢失；标题也变成根推的，多条撞名后触发 `_resolve_filepath` 的 hash 后缀，表面症状是「重复文件」，很容易被误判成去重索引失效（其实 `source` / `item_id` 都是对的）。

**约定**：目标推文没能通过同作者过滤时必须返回 `None`，让调用方退回单条抓取——`twitter.py → _fetch_via_graphql` 的 fallback 会按 `tweet_data["id"] == tweet_id` 精确取目标推文。影响面是全部单条推文抓取，不止书签：`_fetch_via_graphql` 无条件先跑 `fetch_tweet_thread`（"Try thread fetch first"）。

排查同类问题时的经验：**先查目标推文的真实身份再下结论**。本例最初的推断是「转推被存成了转推者的 URL」，直接调 `fetch_tweet_by_rest_id` 一看，三条全都 `是否转推: 否`，实际是回复——推断错了，是实测把方向纠正过来的。
### 输出格式

每条内容 → 独立 `.md` 文件，按平台分目录（`X/`、`XHS/`、`LinuxDo/`、`mpweixin/`、`YouTube/` 等），Obsidian 兼容 YAML front matter（title/source/author/published/likes/tags 等）。

### LinuxDo / Discourse 抓取（`fetchers/linuxdo.py`）

**主路径不是 DOM 抓取，而是 topic JSON**：Tier 0 游客/已保存 session Cookie → Tier 1 CDP 页面内 `fetch(...json)` → Tier 2 Stealth Playwright 页面内 `fetch(...json)` → Tier 3 Jina 最后兜底。明确 404 / 私有帖 / 需登录时提前终止，不再把错误页写入 `Manual/`。

**回复模式三态**：`LINUXDO_REPLY_MODE` / `IDCFLARE_REPLY_MODE` 支持 `author`（默认，仅主贴 + 楼主自回）/ `all`（完整楼层）/ `none`（仅主贴），并把 `reply_mode`、`rendered_reply_count` 写入 front matter。

**会话预热**：`sessions/linuxdo.json` / `sessions/idcflare.json` 缺失时，先尝试 CDP Cookie 同步；若仍无本地会话，再短暂浏览器访问首页 seed 一份可复用 storage state。

**折叠块混合渲染**：简单 `<details>` 输出为 Obsidian collapsible callout（`> [!feedgrab-fold]-`），复杂折叠输出为 `<details class="feedgrab-fold ...">` 纯 HTML，兼顾 Obsidian 折叠效果和通用 Markdown 引用场景。

### 搜狗微信搜索（`mpweixin-so`）

`feedgrab mpweixin-so <keyword>` 通过搜狗微信搜索发现公众号文章。配置开关 `MPWEIXIN_SOGOU_ENABLED`（默认 false）。每页 10 条，最多 100 条（10 页），通过 `MPWEIXIN_SOGOU_MAX_RESULTS` 或 `--limit N` 控制。

流程：HTTP 搜索 → headed 浏览器获取搜狗 Cookie → 新标签页跟随跳转获取 `mp.weixin.qq.com` 真实 URL → 同浏览器提取 `#js_content` HTML → `_html_to_markdown()` 转换富文本（headings/bold/italic/images/links/lists/blockquotes）。输出到 `mpweixin/search_sogou/{keyword}/` 目录。

### 微信公众号按账号批量抓取（`mpweixin-id`）

`feedgrab mpweixin-id "公众号名"` 通过 MP 后台 API 枚举公众号全部历史文章。需要先 `feedgrab login wechat` 获取 MP 后台 session（有效期约 4 天）。

流程：加载 `sessions/wechat.json` → 导航 `mp.weixin.qq.com` 建立会话 → `searchbiz` API 搜索账号获取 fakeid → `appmsgpublish` API 分页枚举文章列表（每页 20 条，`MPWEIXIN_ID_PAGE_SIZE` 可调）→ 逐篇在新标签页打开 → `evaluate_wechat_article()` + `_html_to_markdown()` 提取全文 → 保存到 `mpweixin/account/{公众号名}/`。

配置项：`MPWEIXIN_ID_SINCE`（日期过滤，YYYY-MM-DD）、`MPWEIXIN_ID_DELAY`（单篇间隔秒数，默认 3）、`MPWEIXIN_ID_PAGE_SIZE`（每页条数，默认 20，上限 20）、`MPWEIXIN_ID_PAGE_DELAY`（翻页间隔秒数，默认 8）、`MPWEIXIN_ID_PAGE_JITTER`（翻页抖动比例，默认 0.4）、`MPWEIXIN_ID_MAX_ARTICLES`（单次上限，默认 0=不限）、`MPWEIXIN_ID_FREQ_RETRY`（200013 退避重试次数，默认 0）。去重：复用 `mpweixin` 平台索引。API title fallback：当浏览器 title 为空时（小绿书图片帖），使用 API 返回的 title → digest 作为回退。

**频率限制（`ret=200013 freq control`）**：MP 后台「查询他人公众号文章列表」按**请求次数**设配额。实测边界——换目标公众号、换微信账号（不同 bizuin/token）、换 `appmsg` 老接口、改 `count`（1/5/20）全部同样被拒；而 searchbiz 搜号、查自己的号、单篇正文抓取均正常，说明登录态本身有效。因此无法绕过，只能压低请求数（调大 `MPWEIXIN_ID_PAGE_SIZE`）+ 放慢节奏（`MPWEIXIN_ID_PAGE_DELAY` + 抖动）。触发时抛 `MPWeixinFreqControlError` 并保留进度，CLI 明确报错且退出码非 0，**不得再打印"抓取完成"**。

**断点续传语义**：进度文件 `_progress_mpweixin_id_*.json` 的 `next_begin` 记录**当前正在处理的页**，整页处理完才推进到下一页——页内中断时重读本页（已存的由 dedup 跳过），不会跳过本页剩余文章。清理条件：仅当 `publish_list` 为空（真正读完）或触发日期截止时才清；限流、风控、异常、达到单次上限一律保留。注意「本页没解出图文消息」（`articles` 为空但 `is_complete=False`）不等于列表读完，需继续翻页。

### 微信文章占位页识别（`browser.py` → `detect_wechat_unavailable`）

微信在文章被删除 / 违规 / 受隐私设置限制 / 触发风控时，返回的是**正文非空的占位页**（标题退化为「微信公众平台」，正文如"该内容已被发布者删除"），因此"内容为空"的检查抓不住它，历史上会被当作文章存成空壳 `.md`。

判定只在缺少 `#js_content` 时进行，要求「标题退化」+「正文命中已知文案」双条件（风控 URL `wappoc_appmsgcaptcha` 单条件即可），归一为 4 种 `unavailable_reason`：`deleted` / `violation` / `privacy` / `captcha`。微信改文案时退化为原行为（按普通页面落盘），不会误杀正常文章。

四条链路统一接入，分流策略不同：

| 链路 | 处理 |
|------|------|
| 单篇 `wechat.py` | 抛 `WeChatUnavailableError`，**明确终止不降级 Jina**（否则 Jina 会把同一段占位页文案抓成 Markdown） |
| 账号批量 `mpweixin_account.py` | `_record_unavailable()` 分流计数 |
| 专辑批量 `mpweixin_album.py` | 复用 `_record_unavailable(log_prefix=...)` |
| 搜狗 `wechat_search.py` | 跳过保存（该文件另有一份独立的 fallback 实现，需同步维护） |

分流规则：`deleted` / `violation` / `privacy` → 计 skipped 且**写入 dedup**（内容确实没了，避免反复重试）；`captcha` → 计 failed 且**不写 dedup**（风控是临时的，保持可重试），连续 5 篇风控则中止本轮批量。

### 微信单篇抓取（`fetchers/wechat.py` + `browser.py`）

Tier 1 Playwright WeChat JS 提取 → Tier 2 Jina 兜底 → Tier 3 Browser retry。Browser 优先策略：Jina 对微信 CDN 几乎总超时（30s 浪费），Browser 使用 `WECHAT_ARTICLE_JS_EVALUATE` 在 JS 层面一次性提取 9 类数据（DOM 元素 + OG meta + JS 脚本变量 + `#js_content` HTML），数据完整度最高。`create_time` 通过三层正则从页面 JS 脚本提取（JsDecode 格式 / 单引号 / 双引号），精确到秒。`msg_cdn_url` 比 `og:image` 质量更高。`_html_to_markdown()` 使用 markdownify + BeautifulSoup 预处理（lazy image 转换、SVG 过滤、WeChat 代码块占位符策略）。输出的 Markdown 在 front matter 后插入 `<meta name="referrer" content="no-referrer">`，避免 mmbiz.qpic.cn 图片 Referer 校验 403。小绿书图片帖（`itemShowType=16`）的 `#activity-name` 和 `#js_name` 不存在，标题回退链：`#activity-name` → `og:title` → `.rich_media_title`，作者回退链：`#js_name` → JS 脚本 `nick_name` → `cgiDataNew.nick_name`。代码块预处理：`<br>` → `\n` 转换 + 占位符反向还原（防前缀碰撞）+ fence 前后 `\n\n` 间距。

### 微信公众号评论抓取（实验性）

`MPWEIXIN_FETCH_COMMENTS=true` 开启后，JS evaluate 额外提取 `comment_id` + `appmsg_token`，在页面上下文调用 `appmsg_comment` API 获取精选评论。评论渲染为 Markdown blockquote（用户名 + 点赞 + 内容 + 缩进子评论）。**认证限制**：API 需要微信客户端 session（`uin`/`key`/`pass_ticket`），普通浏览器匿名访问时 `appmsg_token=""`，API 返回 `ret=-3 "no session"`。MP 后台 session 是管理 session，不等于阅读者 session。当前优雅降级：无 session 时输出 WARNING 日志，不影响文章抓取。单篇 + `mpweixin-id` + `mpweixin-zhuanji` 三个入口均支持。配置：`MPWEIXIN_FETCH_COMMENTS`（开关，默认 false）+ `MPWEIXIN_MAX_COMMENTS`（最大条数，默认 100）。

### 微信 URL 规范化（`reader.py` → `_normalize_wechat_url`）

`reader.py` 在平台检测后自动清理微信文章 URL：剥离 `scene`/`click_id`/`sessionid`/`chksm` 等追踪参数，仅保留 `__biz`/`mid`/`idx`/`sn` 四个文章标识参数。解决两个问题：(1) PowerShell 中 `&` 被解析为管道操作符导致命令失败；(2) 同一篇文章因不同追踪参数产生不同 `item_id`（MD5(url)）导致去重索引失效。短链格式（`mp.weixin.qq.com/s/xxx`）自动剥离全部 query 参数和 fragment（短链的 hash 就是文章标识，追踪参数均为垃圾）。PowerShell `&` 报错发生在 shell 解析阶段（程序无法拦截），`feedgrab clip` 命令从系统剪贴板读取 URL 完全绕过 shell 解析。

### Markdown 输出过滤（`utils/storage.py` → `_format_markdown`）

`_format_markdown()` 在最终输出 Markdown 前统一过滤已知的垃圾内容，所有平台生效：
- **Twitter emoji SVG 图片**：`![...](https://abs-0.twimg.com/emoji/...)` → 移除（Obsidian 中显示尺寸过大）
- 如需新增过滤规则，在 `_format_markdown()` 的 `return result` 前添加 `re.sub()` 即可

### 飞书文档抓取（`fetchers/feishu.py` + `feishu_wiki.py`）

**多级兜底**：Tier 0 Open API（`lark-oapi`）→ Tier 1 CDP 直连（复用已打开的 Chrome）→ Tier 2 Playwright `window.PageMain` Block 树 → Tier 3 内部导出 API → Tier 4 Jina。patchright 与飞书不兼容（ERR_CONNECTION_CLOSED），必须用 vanilla `playwright.async_api`。

**CDP 直连**（`FEISHU_CDP_ENABLED=true`）：`_connect_feishu_cdp()` 通过 `connect_over_cdp(ws://127.0.0.1:{port}/devtools/browser)` 连接用户已运行的 Chrome，遍历 `browser.contexts` 找含 `.feishu.cn`/`.larksuite.com`/`.larkoffice.com` cookie 的 context，复用其 localStorage/sessionStorage（飞书 SPA 重度依赖），`context.new_page()` 创建新 tab。`_evaluate_feishu_doc_on_page()` 为 CDP/launch 双路径共享的提取函数，CDP 模式 `skip_warmup=True` 跳过主域名预热导航。`browser.close()` 仅断开 WebSocket 不杀 Chrome。配置：`FEISHU_CDP_ENABLED`（开关，默认 false）+ 复用 `CHROME_CDP_PORT`（端口，默认 9222）。

**Block→Markdown 转换器**（`_block_to_md()`）：支持 20+ 种 block 类型，有序列表 `_calc_ordered_label()` 处理 `seq` 字段（`"1"`/`"auto"`/`"a"`/`"i"` 四种序列类型）。代码块统一使用 4 个反引号围栏；`fallback + snapshot.type=code` 也直接按代码块处理。`_get_elements()` 同时支持 SDK `elements` 格式和 Playwright Delta `zoneState.content.ops` 格式（加粗/斜体等内联样式）。ISV 目录组件（`showCataLogLevel`）通过 `_collect_headings()` 预扫描标题 + `_render_isv_block()` 生成缩进列表。

**嵌入电子表格**：Canvas 渲染无 DOM，通过 `POST /space/api/v3/sheet/client_vars` 内部 API 获取 JSON（Protobuf gzip+base64 编码的单元格数据），5 层 wire format 解码（`L0[1]→L1[2]→L2[12]→L3[2]→L4[repeated 2]`）提取 cell 字符串，reshape 为 2D 数组渲染 GFM 表格。常规 table block 则优先读取 `snapshot.rows_id / columns_id` 判定行列，避免整表被压成单行。`browser.py` 中 `FEISHU_SHEET_FETCH_JS` 和 `_capture_sheet_response()` 拦截器提供多策略数据获取。

**图片下载**（`FEISHU_DOWNLOAD_IMAGES=true`）：Open API `drive/v1/medias/{token}/download`（Tier 0）→ CDN `/space/api/box/stream/download/all/{token}/`（Tier 1）。Playwright 模式三阶段浏览器预下载：Phase 1 network interceptor → Phase 2 scroll 触发懒加载 → Phase 3 从 DOM `<img>` 发现真实 CDN 域名（`internal-api-drive-stream.feishu.cn`）+ `mount_node_token`，JS `fetch()` 批量下载（10 张/批），预下载的 `_bytes` 直接写入磁盘跳过 HTTP。`_sanitize_filename()` 清理文件名中的 `()@#%[]{}|<>!` 和空格，保证 Markdown 图片语法不断裂。每篇文档图片保存到独立子目录 `attachments/{item_id}/`，item_id 与 front matter 一致（MD5(url)[:12]）。

**标题清理**：`_clean_feishu_title()` 过滤零宽字符（U+200B-U+206F, U+FEFF）+ 折叠换行 + 去 ` - 飞书云文档` 等后缀。DOM selectors → rootBlock snapshot.title → document.title 三级回退。

**知识库批量**：`feishu-wiki` CLI 命令，Open API 递归节点树 + 逐篇 blocks API + Playwright 虚拟目录树兜底。Playwright 路径优先抓 `data-node-uid/wikiToken`，支持目录树展开、虚拟滚动去重、旧 anchor 兼容。断点续传缓存文件 + 去重索引。

**配置项**：`FEISHU_APP_ID`/`FEISHU_APP_SECRET`（Open API）、`FEISHU_CDP_ENABLED`（CDP 直连，默认 false）、`FEISHU_DOWNLOAD_IMAGES`（图片下载）、`FEISHU_CUSTOM_DOMAINS`（私有化部署域名）、`FEISHU_PAGE_LOAD_TIMEOUT`（页面等待超时）、`FEISHU_WIKI_*`（批量配置）。

### CDP Cookie 提取（`login.py`）

`CHROME_CDP_LOGIN=true` 时 `feedgrab login <platform>` 优先从运行中的 Chrome 提取 Cookie，失败自动回退浏览器登录。Chrome 146+ 的 `chrome://inspect/#remote-debugging` 不暴露传统 HTTP `/json/*` 端点，两级策略适配：Tier 0 Playwright `connect_over_cdp(ws://127.0.0.1:{port}/devtools/browser)` 直连 WebSocket，从 browser contexts 过滤平台域名 cookies；Tier 1 传统 HTTP 发现 + `websocket-client` 原始 WebSocket `Network.getCookies`。支持 7 个平台：twitter（`.x.com`/`.twitter.com`）、xhs（`.xiaohongshu.com`）、wechat（`.qq.com`）、feishu（`.feishu.cn`/`.larksuite.com`）、kdocs（`.kdocs.cn`/`.wps.cn`）、linuxdo（`.linux.do`）、idcflare（`.idcflare.com`）。配置：`CHROME_CDP_LOGIN`（开关，默认 false）+ `CHROME_CDP_PORT`（端口，默认 9222）。

### 金山文档抓取（`fetchers/kdocs.py`）

**Tier 0 Playwright ProseMirror DOM 提取**：金山文档基于 WPS WebOffice SPA + ProseMirror 编辑器，使用虚拟滚动（`.otl-scroll-container`）只渲染可见区域 DOM。`_scroll_and_collect_blocks()` 每次滚动 600px + 250ms 等待 + 提取可见 `.block_tile`，基于文本前 80 字符去重，支持 7 种块类型：标题（`.otl-heading` + `level` 属性）、段落（`.otl-paragraph`）、列表（`listtype=bullet/ordered` + `listlevel`）、待办（todo checkbox）、分割线（`hr`）、代码块（`<nodeview data-node-type="code_block">` → `<pre lang>` → `<code class="code-block-content">`）、图片（`<nodeview>` + `<img sourcekey>`）。

**图片 shapes API**：DOM 中图片使用 blob: URL，无 `data-src`/`data-origin` 属性。通过 `_setup_shapes_interceptor()` 拦截 `attachment/shapes` 网络响应获取 `sourcekey → CDN URL` 映射（优先 `raw` > `url` > `thumbnail`），`_resolve_image_urls()` 将 blob: URL 替换为 CDN URL。手动 fallback：拦截器未触发时通过 `page.evaluate(fetch())` 主动调用 shapes API。

**CDP 直连**（`KDOCS_CDP_ENABLED=true`）：`_connect_kdocs_cdp()` 查找含 `.kdocs.cn`/`.wps.cn` cookie 的 context，`new_page()` 创建新 tab。CDP 页面强制 `set_viewport_size(1920x1080)` + 滚动前 `scrollTop=0` + 滚动容器就绪检查，确保与 Launch 模式一致的提取量。

**图片下载**（`KDOCS_DOWNLOAD_IMAGES=true`）：`download_kdocs_images()` 将 CDN URL 图片下载到 `attachments/{item_id}/` 子目录，`_blocks_to_markdown()` 将 CDN URL 替换为本地相对路径。`_guess_image_ext()` 从 URL 推断扩展名。

**重复分割线修复**：虚拟滚动提取时空 block_tile 产生重复 `---`，`_blocks_to_markdown()` 尾部正则合并连续分割线 + 清理多余空行。

**Tier 1 Jina Reader** 兜底。配置：`KDOCS_CDP_ENABLED`（CDP 直连，默认 false）+ `KDOCS_PAGE_LOAD_TIMEOUT`（页面等待超时，默认 10000ms）+ `KDOCS_DOWNLOAD_IMAGES`（图片下载，默认 false）。

### 有道云笔记抓取（`fetchers/youdao.py`）

**Tier 0 JSON API**（零依赖，<1s）：`GET /yws/api/note/{shareKey}?sev=j1&editorType=1&editorVersion=new-json-editor&sec=v1`，返回标题/时间戳/浏览量 + `content` 嵌套 JSON 字符串。Content 使用数字键压缩格式：`"5"` 子块数组、`"6"` 特殊标记（`"im"` 图片 / `"l"` 列表）、`"7"` 内联元素、`"8"` 文本、`"9"` 样式（`"b"` 粗体 / `"i"` 斜体 / `"fs"` 字号 / `"c"` 颜色）、`"4"` 配置（`"hf"` 链接 / `"u"` 图片URL / `"lt"` 列表类型 / `"ll"` 列表级别）。标题通过 font-size + bold 样式推断（≥24=H1, ≥20=H2, ≥16=H3）。图片使用直接 CDN URL（`note.youdao.com/yws/public/resource/{shareKey}/xmlnote/WEBRESOURCE{id}/{imageId}`），非 blob:。代码块最外层用 4 个反引号包裹（防嵌套）。

**Tier 1 Playwright**：iframe `#content-body` 内 `.bulb-editor` 容器提取。无虚拟滚动。

**Tier 2 Jina Reader** 兜底。

**URL 清理**：`clean_youdao_url()` 只保留 `id` 参数，去掉 `type`/`_time` 等无用尾巴。`item_id = MD5(clean_url)[:12]`。

**图片下载**（`YOUDAO_DOWNLOAD_IMAGES=true`）：`download_youdao_images()` 将 CDN URL 图片下载到 `attachments/{item_id}/` 子目录。分享页无需登录。输出目录：`{OUTPUT_DIR}/NoteYouDao/`。

### 微信专辑批量抓取（`fetchers/mpweixin_album.py`）

`feedgrab mpweixin-zhuanji "<专辑URL>"` 批量抓取微信公众号专辑页面全部文章。以 `mpweixin_account.py` 为模板，结构完全对齐。差异：不需要 MP 后台 session（公开专辑可直接访问）；分页用 `begin_msgid`/`begin_itemidx`（非偏移量）。`parse_album_url()` 解析专辑 URL 提取 `__biz` + `album_id`。JS evaluate 调用 `action=getalbum` API 获取文章列表。断点续传缓存文件 `_progress_mpweixin_zhuanji_{album_id}.json`，完成后自动清理。去重：复用 `mpweixin` 平台索引。输出目录：`mpweixin/zhuanji/{专辑名}/`。配置：`MPWEIXIN_ZHUANJI_SINCE`（日期过滤）+ `MPWEIXIN_ZHUANJI_DELAY`（间隔秒数，默认 3）。

### 媒体文件本地化下载（`utils/media.py`）

`X_DOWNLOAD_MEDIA=true` / `XHS_DOWNLOAD_MEDIA=true` / `MPWEIXIN_DOWNLOAD_MEDIA=true` 开启后，图片视频下载到 `{md_dir}/attachments/{item_id}/` 子目录，Markdown 中远程 URL 替换为相对路径。仿飞书 `download_feishu_images` 模式：`save_to_markdown()` 返回路径 → `download_media()` 下载 + 替换。URL 优化：Twitter `name=orig` 原图质量；XHS 去 `!nd_*` CDN resize 后缀 + `Referer: https://www.xiaohongshu.com` 防盗链；WeChat `http://` → `https://`（mpvideo CDN 要求）+ `Referer: https://mp.weixin.qq.com/`。覆盖范围：单篇（`reader.py`）+ 全部 8 个 Twitter/XHS 批量 fetcher + 2 个微信批量 fetcher（mpweixin_account/mpweixin_album）。单张失败保留远程 URL，不影响其他下载。

### 微信文章视频提取（`fetchers/browser.py` + `wechat_search.py`）

微信文章嵌入视频使用 `<span class="video_iframe" data-mpvid>` 容器，`<video>` 标签因 `setup_resource_blocking()` 资源拦截通常无 `src`。视频 MP4 URL 存在于页面 `<script>` 文本中（格式：`url: ('http://mpvideo.qpic.cn/xxx.fNNNNN.mp4?auth_params')`）。JS evaluate 用字符串 `split('mpvideo.qpic.cn')` 分割法提取 URL（避免 Python 三引号字符串中的正则转义问题），按质量码（f10002 SD < f10004 HD < f10102 < f10104 最高）去重取最优。URL 清理：JS hex escape `\x26amp;` → `&`，`http://` → `https://`（mpvideo CDN 拒绝 HTTP 连接）。`_build_wechat_result()` 在调用 `md_converter` 前将清理后的视频 URL 注入 `span.video_iframe` 容器（正则替换 + lambda 避免反斜杠解释）。`_preprocess_wechat_html()` 将 `<video src>` / `span.video_iframe` 替换为 `[▶ 视频](url)` Markdown 链接，同时清理"视频加载失败"错误文本。视频签名有时效（`auth_key`/`dis_t`），下载须在抓取后尽快进行。

### Article 误判防护（`fetchers/twitter.py` → `_try_fetch_article_body`）

判定推文是否为 Article stub 的逻辑：去掉所有 `t.co` 链接后，**剩余文字少于 30 字符**才算 article stub，避免正常含链接推文被误判后走 Jina 覆盖正文。

### Twitter Article 原生渲染

GraphQL 返回的 Article 数据包含 `content_state`（Draft.js 格式），`_render_article_body()` 将其原生转换为 Markdown，零额外网络请求。支持的 block 类型：unstyled（段落）、header-two（##）、ordered-list-item（有序列表）、atomic（图片/代码块）、code-block（代码块）。entityMap 是 `[{key, value}]` 列表格式（非 dict）。优先级：GraphQL content_state > Jina Reader 兜底。

### Jina 空洞修补（`fetchers/twitter_bookmarks.py`）

Jina Markdown 模式可能丢失 inline link 元素（cashtag、@mention），产生"空洞"（句尾标点后紧跟空行再接下文）。`_patch_jina_hollows()` 检测空洞后用 Jina text 模式获取完整文本，通过锚点匹配定位缺失内容并回填。

### 引用推文完整提取

`extract_tweet_data()` 从 `quoted_status_result` 中提取完整数据：`note_tweet.text`（不截断）、展开 t.co、提取图片/视频、互动指标。`_render_quoted_tweet()` 渲染为 Markdown blockquote。

### richtext_tags 富文本标记

`_apply_richtext_tags()` 将 note_tweet 中的 Draft.js 索引式 `richtext_tags`（Bold/Italic）转换为 Markdown `**bold**`/`*italic*`。从末尾向前插入避免索引偏移。

### 扩展 front matter 元数据

Twitter 推文输出包含完整作者画像（`is_blue_verified`、`followers_count`、`statuses_count`、`listed_count`）和推文元数据（`quotes`、`lang`、`source_app`、`possibly_sensitive`）。

### YouTube Data API v3 搜索（`youtube_search.py`）

`feedgrab ytb-so <keyword>` 通过 YouTube Data API v3 搜索视频。两阶段查询：`search.list`（100 quota）→ videoId list → `videos.list` 批量详情（1 quota）。支持频道限定（`--channel @handle`）、排序（`--order viewCount/date`）、日期范围、时长过滤。频道搜索跳过 `regionCode`/`relevanceLanguage` 参数（否则返回空结果）。

### YouTube 单视频四级兜底（`youtube.py`）

单视频抓取策略：Tier 0 InnerTube API（零依赖零 quota，ANDROID 客户端身份）→ Tier 1 yt-dlp 字幕（多语言轮询 `[sub_lang, zh-CN, zh-Hans, zh-Hant, zh, en, en-US]`）→ Tier 2 Groq Whisper 转录（verbose_json segment 时间戳，复用断句+章节管线，>100MB 自动 ffmpeg 分片）→ Tier 3 API description / Jina 兜底。YouTube Data API v3 元数据（1 quota）优先获取（可选）。yt-dlp 默认从 Chrome 提取 cookies 绕过 YouTube bot 检测（`YTDLP_COOKIES_BROWSER=chrome`），Cookie 提取失败自动降级。

### InnerTube API 字幕获取（`youtube.py` → `_fetch_innertube_transcript`）

`POST https://www.youtube.com/youtubei/v1/player` + `{clientName: "ANDROID", clientVersion: "20.10.38"}`。ANDROID 客户端绕过部分限制。`INNERTUBE_API_KEY` 从页面 HTML 提取，EU consent 自动处理（`CONSENT=YES+` cookie）。`captionTracks[].baseUrl` 剥离 `fmt=srv3` 参数获取默认 XML（`<text start="" dur="">`），双重 `html.unescape()` 处理 `&amp;#39;` 等实体。多语言匹配：先精确命中 `captionTracks`，未命中用 `&tlang=` 请求翻译。

### YouTube 智能断句（`youtube.py` → `_segment_into_sentences`）

三阶段管线：(1) 按句尾标点（`.?!…。？！⁈⁇‼‽．`）拆分 snippet，时间戳按字符比例分配；(2) 跨 snippet 合并为完整句子（CJK 无空格，拉丁加空格）；(3) `_group_into_paragraphs()` 段落分组（≤5句/段，>2s 间隔强制分段）。**无标点兜底**：自动字幕标点率 <10% 时跳过断句，直接 snippet 级分组。`_parse_chapters()` 从 description 正则解析章节时间戳（≥2 个生效），`_format_transcript_markdown()` 输出带 `## 章节标题 [M:SS]` + `[HH:MM:SS → HH:MM:SS]` 时间戳的结构化 Markdown。技术方案参考 [baoyu-youtube-transcript](https://github.com/jimliu/baoyu-skills)（@dotey 宝玉）。

### yt-dlp JS 运行时（`_js_runtime_args()`）

yt-dlp 默认只启用 deno。`_js_runtime_args()` 自动检测 deno/node/bun 并传入 `--js-runtimes` + `--remote-components ejs:github`。两个文件各有一份（`youtube.py` 和 `youtube_search.py`）。

### YouTube 下载命令（`ytb-dlv`/`ytb-dla`/`ytb-dlz`）

三个 CLI 命令下载视频(MP4)/音频(MP3)/字幕(SRT)到 `{OUTPUT_DIR}/YouTube/` 目录。先通过 API 获取元数据构建统一文件名前缀（`author_date：title`），与 MD 输出保持一致。长链接和 youtu.be 短分享链接都兼容。

### 知识星球抓取（`fetchers/zsxq.py`）

支持 `articles.zsxq.com/id_<hashid>.html` 长文章和 `wx.zsxq.com/group/<gid>/topic/<tid>` 单条 topic 两种主形态。短链 `t.zsxq.com/<code>` 自动 302 解析（参考 Douyin 短链思路）；H5 邀请短链跳转目标 `?topic_id=<digits>` 通过 query 参数识别。

**鉴权**（参考 [yann0917/knowledge](https://github.com/yann0917/knowledge) `service.go` 逆向）：Cookie 模式（核心 `zsxq_access_token`，`.zsxq.com` 域）+ 固定 UA + `X-Timestamp` (Unix 秒) + `X-Version: 2.37.0`。X-Version 过期可由 `ZSXQ_API_VERSION` 覆盖。

**Tier 链路**（对齐 LinuxDo 范式）：

- Tier 0：HTTP GET（带 cookie + 头部）。articles 走 `requests.get` + BeautifulSoup `.ql-editor`；topic 走 `https://api.zsxq.com/v2/topics/<tid>/info` 直接解析 JSON。
- Tier 1：CDP `connect_over_cdp(ws://9222/devtools/browser)` 复用 Chrome `.zsxq.com` cookie context。
- Tier 2：Stealth Playwright launch + `sessions/zsxq.json`，page.goto 取 HTML 或 page.evaluate(fetch(api)) 取 JSON。
- Tier 3：Jina（鉴于 zsxq 强登录态门控基本无效，仅留姿态）。明确 401/404/business-failed 时直接终止不下沉，避免落地登录页 .md。

**article HTML 解析关键点**：作者优先 `<meta name="author">`，回退到 `.author-info .nick-name`；group_id 从 `.group-info a[href]` 中正则匹配 `/group/(\d+)`；group_name 优先 `<meta property="og:site_name">`，回退到 `.group-name`；cover_image 优先 `<meta property="og:image">`，回退到 `.ql-editor` 内第一个 `<img>`；Quill 代码块 `<pre class="ql-syntax" spec-language="X">` 用 4 反引号围栏（与 LinuxDo / Feishu 对齐）。

**topic 五形态**：talk / question+answer / article / **solution**（zsxq 较新的"问答+解决方案"形态，`topic.title` 是用户提问，`topic.solution.text` 是星主/AI 解答）。

**评论三态**：`ZSXQ_COMMENT_MODE=none|all|author`（默认 none），对齐 LinuxDo `LINUXDO_REPLY_MODE`。author 模式仅渲染 `owner.user_id == comment.owner.user_id` 的评论。

**配置**：`ZSXQ_ENABLED` / `ZSXQ_CDP_ENABLED` / `ZSXQ_PAGE_LOAD_TIMEOUT` / `ZSXQ_COMMENT_MODE` / `ZSXQ_MAX_COMMENTS` / `ZSXQ_DOWNLOAD_MEDIA` / `ZSXQ_API_VERSION` 共 7 个环境变量；登录用 `feedgrab login zsxq`（CDP 自动提取 / 弹窗扫码二选一）。

## 迭代历史摘要

> 完整记录见 `DEVLOG.md`

| 版本 | 功能 |
|------|------|
| v0.25.2 | 修复线程抓取把「别人的回复」替换成根推内容：`fetch_tweet_thread` 重建的是**根推作者**的自回复链（`_is_same_thread` 第一条判据即 `user_id != root_user_id -> False`），而 `_classify_tweet` 会把任何回复推文都送进线程路径，于是收藏/抓取别人在某 thread 下的回复时，落盘正文被写成根推、被抓那条的内容彻底丢失（标题也随之撞名，触发 hash 后缀，表面症状是「重复文件」）；影响面含全部单条推文抓取（`_fetch_via_graphql` 无条件先跑 thread）。修法是目标推文未通过同作者过滤时返回 `None`，让调用方落到 `twitter.py` 已有的按 `id` 精确取目标推文的 fallback；新增 6 例并用 `git stash` 撤销修复验证测试确实会失败（排除空转）；真实重抓书签文件夹复验，三份产物的 source/author/正文一一对应，撞名文件 2 → 0；测试 436 → 442 passed |
| v0.25.1 | X 全渠道权限实测 + 账号私有渠道锁主账号：18 项渠道 × 2 账号交叉实测确认三级边界（公开 → 账号本人 → 推文作者本人），用户 likes / favoriters 改走 `primary_only=True`，retweeters / 单贴 / 长文 / 列表 / 搜索等公开渠道保持轮换；缺主账号时 reader 与 CLI 前置硬失败，闸门判据统一到 `mode_requires_primary()`（`_MODE_CONFIG` 单一来源）；`parse_tweet_user_list_url` 兼容 `www.` / `mobile.` 子域；主账号被限流时区分「限流冷却」与「登录态过期」两种归因；修复书签 URL 路由（X 已迁到 `/i/history`，legacy `/i/bookmarks` 仍解析，路由与解析统一用 `[0-9]`）；修正两处把平台机制误说成对方隐私设置的文案；10 项渠道端到端实测全部落地；测试 390 → 436 passed |
| v0.25.0 | 第一阶段 service layer 架构升级：新增 `feedgrab/service/` 的结构化 API（models / FetchService / Output / Login / Settings / Doctor / Job），CLI 单 URL 和 MCP 入口改为共用 `FetchService`，保持终端命令、Markdown 输出、去重索引和 session 格式兼容；修复 MP 后台 session 失效错误被掩盖问题；FlowUs 在线图片模式改为写入可预览的 `cdn2.flowus.cn` 签名 URL，本地模式仍通过 `FLOWUS_DOWNLOAD_IMAGES=true` 下载 `attachments/`；测试 210 passed |
| v0.21.0 | 新增「知识星球」（Zsxq）平台支持（articles.zsxq.com 长文章 + wx.zsxq.com 短帖 + t.zsxq.com 短链 302 解析）；4 级 Tier 链路 + 五形态 topic 渲染（含 solution）+ 三态评论筛选 |
| v0.20.1 | 修复 Twitter 长 thread 被误判为 Article 导致 quoted tweet 丢失（`schema.from_twitter` 改用 `_has_article_body` 看 article_data 实际内容） |
| v0.20.0 | 五平台扩展：HackerNews / Medium / Reddit / Weibo / Douyin |
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
| 新增 CLI 命令 | `cli.py` → `main()` 路由 + `cmd_xxx()` |
| 新增环境变量 | `config.py` + `.env.example` |
| 新增平台 fetcher | `fetchers/xxx.py` + `reader.py` 路由 |
| 修改输出格式 | `utils/storage.py` |
| 修改数据模型 | `schema.py` |
| 去重逻辑 | `utils/dedup.py` |
| X/Twitter 相关 | `twitter*.py`（11个文件） |
| YouTube 相关 | `youtube.py`（字幕/转录）+ `youtube_search.py`（搜索/下载） |
| GitHub 相关 | `github.py`（REST API + 中文 README 优先） |
| LinuxDo / IDCFlare / Discourse | `linuxdo.py` + `idcflare.py`（topic JSON → CDP → 浏览器 → Jina） |
| 小红书相关 | `xhs*.py`（4个文件）+ `browser.py` |
| 飞书相关 | `feishu.py`（单篇 + Block→MD + 图片）+ `feishu_wiki.py`（知识库批量）+ `browser.py` |
| 金山文档相关 | `kdocs.py`（ProseMirror DOM 提取 + shapes API 图片 + CDP 直连） |
| 有道云笔记相关 | `youdao.py`（JSON API + Playwright iframe DOM + 图片下载） |
| 知乎相关 | `zhihu.py`（单篇 + 前 3 楼 + API/Playwright/Jina）+ `zhihu_search.py`（关键词搜索 + 汇总表格） |
