<div align="center">

<h1>feedgrab</h1>

<h3>The Universal Content Grabber for 18+ Media Platforms, from URLs and Keywords to Obsidian Markdown</h3>

<p>
  <a href="DEVLOG.md"><img src="https://img.shields.io/badge/version-v0.25.0-0A84FF.svg" alt="feedgrab v0.25.0"></a>
  <a href="#install"><img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-6B7280.svg" alt="Windows, macOS and Linux"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/built%20with-Python%203.10%2B-3776AB.svg?logo=python&amp;logoColor=white" alt="Built with Python 3.10+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-22C55E.svg?logo=opensourceinitiative&amp;logoColor=white" alt="MIT License"></a>
  <a href="https://github.com/iBigQiang/feedgrab/stargazers"><img src="https://img.shields.io/github/stars/iBigQiang/feedgrab?style=flat&amp;logo=github&amp;label=stars" alt="GitHub Stars"></a>
</p>

<p>
  <a href="https://playwright.dev/"><img src="https://img.shields.io/badge/browser-Playwright-2EAD33.svg?logo=playwright&amp;logoColor=white" alt="Playwright browser automation"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/protocol-MCP-7C3AED.svg" alt="Model Context Protocol server"></a>
  <a href="https://code.claude.com/docs/en/features-overview"><img src="https://img.shields.io/badge/skills-Claude%20Code-D97757.svg?logo=anthropic&amp;logoColor=white" alt="Claude Code skills"></a>
  <a href="https://obsidian.md/"><img src="https://img.shields.io/badge/output-Obsidian%20Markdown-7C3AED.svg?logo=obsidian&amp;logoColor=white" alt="Obsidian-compatible Markdown"></a>
</p>

<p><strong>feedgrab</strong> is an open-source, <strong>vibe-coded</strong> collector for mainstream media and content platforms. Give it a supported URL or keyword and it detects the platform, batch-fetches the content, and exports metadata-rich, Obsidian-compatible Markdown. It currently supports 18+ mainstream platforms. Its core fetching paths prioritize free interfaces, so paid APIs are usually unnecessary and API costs stay close to zero.</p>

<h3>🌐 Official Repository &amp; Documentation: <a href="https://github.com/iBigQiang/feedgrab">github.com/iBigQiang/feedgrab</a></h3>

<p><strong>English</strong> | <strong><a href="README.md">中文</a></strong> | <a href="#install">Install</a> | <a href="https://github.com/iBigQiang/feedgrab/tree/feedgrab-desktop">Windows Desktop</a> | <a href="DEVLOG.md">Changelog</a></p>

</div>

## ❤️ Sponsors

> [Want to appear here?](mailto:ibigqiang@gmail.com)

<details open>
<summary>Click to expand or collapse sponsor content</summary>

**👑 Title Sponsor**

[![Hitu Image](https://hitu.me/og.jpeg)](https://hitu.me/zh/)

Hitu Image is the title sponsor of feedgrab. It is an all-in-one ChatGPT image creation tool for text-to-image generation, image editing, style transfer, background removal, old photo restoration, and upscaling. New users receive free credits, with no credit card required and no watermark restrictions. **[Try Hitu Image](https://hitu.me/zh/generate)**, or **[register through this offer](https://hitu.me/zh/notices/order-and-get-20-off-in-points)** and receive 20% bonus credits after purchasing a plan.

---

**Sponsors**

<table>
<tr>
<td width="180"><a href="https://huangqiang.me/"><img src="https://huangqiang.me/og.png" alt="Qiang's Notes · iBigQiang" width="150"></a></td>
<td>Thanks to <strong><a href="https://huangqiang.me/?utm_source=github&amp;utm_medium=link&amp;utm_campaign=feedgrab">Qiang's Notes</a></strong> for supporting feedgrab. Qiang's Notes is the personal IP brand website of BigQiang / iBigQiang, bringing together independent development, open-source projects, products, writing, and ways to collaborate. <strong><a href="https://huangqiang.me/">Visit huangqiang.me</a></strong></td>
</tr>
<tr>
<td width="180"><a href="https://www.atlascloud.ai/?utm_source=github&amp;utm_medium=link&amp;utm_campaign=feedgrab"><img src="docs/Sponsor/ATLAS_CLOUD_LOGO_BLACK.svg" alt="Atlas Cloud" width="150"></a></td>
<td><strong><a href="https://www.atlascloud.ai/?utm_source=github&amp;utm_medium=link&amp;utm_campaign=feedgrab">Atlas Cloud</a></strong> is a full-modal AI inference platform that gives developers a single AI API to access video generation, image generation, and LLM APIs. Instead of managing multiple vendor integrations, you connect once and get unified access to 300+ curated models across all modalities.
Check out Atlas Cloud's new Coding Plan promotion for more budget-friendly API access: <a href="https://www.atlascloud.ai/console/coding-plan/?utm_source=github&amp;utm_medium=link&amp;utm_campaign=feedgrab">https://www.atlascloud.ai/console/coding-plan</a></td>
</tr>
</table>

</details>

## What It Does

```
Any URL → Platform Detection → Fetch Content → Unified Output
              ↓                      ↓                ↓
         auto-detect           text: Jina Reader    → output/X/Author_Date：Title.md
         18+ platforms         video: yt-dlp subs    → output/YouTube/Author_Date：Title.md
                               audio: Whisper transcription
                               API: Bilibili / RSS / Telegram / YouTube Data API v3 / GitHub REST API / Feishu Open API / Discourse Topic JSON
                               X/Twitter: GraphQL → FxTwitter → Syndication → oEmbed → Jina → Playwright
```

The Python layer handles text fetching and YouTube subtitle extraction. The **Claude Code skills** (optional) add full Whisper transcription for video/podcast and AI-powered content analysis.

## Three Layers

feedgrab is composable. Use the layers you need:

| Layer | What | Format | Install |
|-------|------|--------|---------|
| **Python CLI/Library** | Basic content fetching + unified schema | See [Install](#install) | Required |
| **Claude Code Skills** | Video transcription + AI analysis + content fetching | `npx skills add iBigQiang/feedgrab` | Optional |
| **MCP Server** | Expose reading as MCP tools | `python mcp_server.py` | Optional |

### Layer 1: Python CLI

```bash
# Fetch any URL
feedgrab https://mp.weixin.qq.com/s/abc123

# Fetch URL from clipboard (solves PowerShell '&' parsing error)
feedgrab clip

# Fetch a tweet (with GraphQL deep fetch if cookies configured)
feedgrab https://x.com/elonmusk/status/123456

# Batch fetch bookmarks (requires X_BOOKMARKS_ENABLED=true)
feedgrab https://x.com/i/bookmarks
feedgrab https://x.com/i/bookmarks/2015311287715340624  # Specific bookmark folder

# Batch fetch user tweets (requires X_USER_TWEETS_ENABLED=true)
feedgrab https://x.com/iBigQiang                        # All tweets
X_USER_TWEETS_SINCE=2026-02-01 feedgrab https://x.com/iBigQiang  # After date
# ↑ Automatically launches browser search when exceeding ~800 tweets (requires feedgrab login twitter)

# Batch fetch Twitter List tweets (requires X_LIST_TWEETS_ENABLED=true)
feedgrab https://x.com/i/lists/2002743803959300263               # Last 1 day (default)
X_LIST_TWEETS_DAYS=3 feedgrab https://x.com/i/lists/2002743803959300263  # Last 3 days
X_LIST_TWEETS_SUMMARY=true feedgrab https://x.com/i/lists/...    # Generate summary table (MD + CSV)

# v0.22.0: Batch fetch Twitter user lists (followers/following/list members, outputs MD + CSV)
feedgrab https://x.com/ai_xiaomu/followers                       # Followers
feedgrab https://x.com/ai_xiaomu/following                       # Following
feedgrab https://x.com/ai_xiaomu/verified_followers              # Blue-verified followers
feedgrab https://x.com/i/lists/2002743803959300263/members       # List members
feedgrab https://x.com/i/lists/2002743803959300263/subscribers   # List subscribers

# v0.22.0: Batch fetch user replies & likes
feedgrab https://x.com/ai_xiaomu/with_replies                    # User replies tab (incl. self-replies)
feedgrab https://x.com/ai_xiaomu/likes                           # User likes (Twitter default = private)

# v0.23.0: Fetch retweeters / favoriters of a tweet (outputs MD + CSV, sorted by followers)
feedgrab x-retweeters https://x.com/ai_xiaomu/status/2051099012288356592
feedgrab x-favoriters 2051099012288356592                        # Accepts plain tweet_id too
feedgrab https://x.com/ai_xiaomu/status/2051099012288356592/retweets  # URL-based routing
feedgrab https://x.com/ai_xiaomu/status/2051099012288356592/likes     # Likes (author may hide)

# v0.23.0: Twitter People search (SearchTimeline product=People)
feedgrab x-so "AI Agent" --people                                # People search, ranked by followers

# v0.23.0: Author-hidden replies (ModeratedTimeline; only the tweet author can see them)
X_FETCH_MODERATED_REPLIES=true feedgrab https://x.com/me/status/...  # Adds thread Phase 8 automatically

# v0.23.0: Custom media filename pattern (X-only; default keeps CDN stem)
X_DOWNLOAD_MEDIA=true X_MEDIA_FILENAME_PATTERN="{date}_{screen_name}_{tweet_id}_{num}.{ext}" \
  feedgrab https://x.com/ai_xiaomu/status/...
# Output: 20260518_ai_xiaomu_2056173124073525356_1.jpg
# Tokens: {date} {datetime} {screen_name} {user_id} {tweet_id} {num} {type} {ext} {name}

# Batch fetch XHS author notes (requires XHS_USER_NOTES_ENABLED=true + feedgrab login xhs)
feedgrab https://www.xiaohongshu.com/user/profile/5eb416f...
XHS_USER_NOTES_SINCE=2026-02-01 feedgrab https://www.xiaohongshu.com/user/profile/5eb416f...  # Only after date

# Batch fetch XHS search results (requires XHS_SEARCH_ENABLED=true + feedgrab login xhs)
feedgrab "https://www.xiaohongshu.com/search_result?keyword=开学第一课&source=web_explore_feed"

# Search XHS notes via API (xhshow, no login required)
feedgrab xhs-so "AI Agent"                           # Search (general)
feedgrab xhs-so "AI Agent" --sort popular             # Sort by popularity
feedgrab xhs-so "AI Agent" --type video               # Video only
feedgrab xhs-so "AI Agent" --sort latest --limit 50   # Latest 50
feedgrab xhs-so "AI Agent" --save                     # Save individual .md files
feedgrab xhs-so "claude code,openclaw" --merge         # Multi-keyword merged into one table
feedgrab xhs-so "claude code,openclaw"                 # Multi-keyword separate tables

# Search YouTube videos
feedgrab ytb-so "AI Agent"
feedgrab ytb-so "tutorial" --channel @AndrewNg --order viewCount
feedgrab ytb-so "ML" --after 2025-01-01 --limit 5

# Search Twitter tweets (engagement-ranked summary table)
feedgrab x-so openclaw                                        # Default: last 1 day + Chinese + Latest tab
feedgrab x-so "AI Agent" --days 7 --min-faves 50 --sort top   # Custom params
feedgrab x-so WorkBuddy --lang zh+zxx --sort all              # Chinese tweets + Article cards, merged Latest/Top
feedgrab x-so '"openclaw" lang:zh since:2026-03-01' --raw     # Raw query mode
feedgrab x-so openclaw --save                                  # Also save individual tweet .md files
feedgrab x-so "VPN,proxy,v2ray" --merge                        # Multi-keyword merged into one table
feedgrab x-so "claude code,openclaw"                           # Multi-keyword separate tables

# Download YouTube video/audio/subtitles (output to OUTPUT_DIR/YouTube/)
feedgrab ytb-dlv https://www.youtube.com/watch?v=xxx   # Download video (MP4)
feedgrab ytb-dla https://www.youtube.com/watch?v=xxx   # Download audio (MP3)
feedgrab ytb-dlz https://www.youtube.com/watch?v=xxx   # Download subtitles (SRT)
feedgrab ytb-dla https://youtu.be/xxx?si=xxx           # Short share links work too

# Fetch GitHub repo README (auto-detects Chinese README priority)
feedgrab https://github.com/nicepkg/aide                          # Repo homepage
feedgrab https://github.com/nicepkg/aide/blob/main/README.md      # README file page
feedgrab https://github.com/nicepkg/aide/tree/main/src             # Sub-page (auto fallback to repo level)

# Fetch a LinuxDo / Discourse topic
feedgrab https://linux.do/t/topic/2032344
feedgrab login linuxdo                                            # Login first if the topic is private or CF is stricter
LINUXDO_REPLY_MODE=all feedgrab https://linux.do/t/topic/2032344  # Capture the full thread

# Fetch a IDCFlare / Discourse topic
feedgrab https://idcflare.com/t/topic/44294
feedgrab login idcflare                                           # Login first if the topic is private or CF is stricter
IDCFLARE_REPLY_MODE=none feedgrab https://idcflare.com/t/topic/44294  # OP only

# Fetch multiple URLs
feedgrab https://url1.com https://url2.com

# Login to a platform (one-time, for browser fallback)
feedgrab login xhs
feedgrab login linuxdo

# Chrome CDP auto-extract cookies (from already logged-in Chrome, no manual login needed)
# Prerequisite: enable Remote Debugging in Chrome (chrome://inspect/#remote-debugging)
CHROME_CDP_LOGIN=true feedgrab login twitter
CHROME_CDP_LOGIN=true feedgrab login xhs
CHROME_CDP_LOGIN=true feedgrab login kdocs

# Fetch KDocs (WPS) documents (CDP by default, reuses Chrome login session)
feedgrab https://www.kdocs.cn/l/xxxxx                              # Auto CDP → Launch fallback
KDOCS_CDP_ENABLED=false feedgrab https://www.kdocs.cn/l/xxxxx      # Force Launch mode
KDOCS_DOWNLOAD_IMAGES=true feedgrab https://www.kdocs.cn/l/xxxxx   # Download images locally

# Download tweet images/videos to local (saved to attachments/{item_id}/ subdirectory)
X_DOWNLOAD_MEDIA=true feedgrab https://x.com/user/status/123
XHS_DOWNLOAD_MEDIA=true feedgrab https://www.xiaohongshu.com/explore/xxx
MPWEIXIN_DOWNLOAD_MEDIA=true feedgrab https://mp.weixin.qq.com/s/xxx

# === HackerNews / Medium / Reddit / Weibo / Douyin (new in v0.20.0) ===
feedgrab https://news.ycombinator.com/item?id=44627001        # HN single item + comments
feedgrab hn top --limit 30                                     # HN top stories batch
feedgrab hn ask --limit 20                                     # HN Ask HN batch

feedgrab https://medium.com/@dotey/some-article-abc123         # Medium single article
feedgrab medium-user @dotey --limit 20                         # Medium user (RSS + tier chain)
feedgrab medium-pub better-programming --limit 20              # Medium publication

feedgrab https://www.reddit.com/r/MachineLearning/comments/<id>/foo/  # Reddit post + Top 50 comments
feedgrab reddit-sub MachineLearning --sort hot --limit 25      # Reddit subreddit batch
feedgrab reddit-so codex --sort comments --time all --limit 10 # Reddit keyword search
REDDIT_REPLY_MODE=tree feedgrab https://www.reddit.com/r/...   # Preserve nested replies from the initial response

feedgrab https://m.weibo.cn/status/4857881961438732            # Weibo single post
feedgrab weibo-user 1234567890 --limit 20                      # Weibo user profile batch

feedgrab https://www.douyin.com/video/7234567890123456789     # Douyin single video
feedgrab https://v.douyin.com/iL3xpDe/                         # Douyin short link (auto 302 resolve)
# === Zsxq (v0.21.0 new) ===
# First-time setup (pick one):
feedgrab login zsxq                                            # Open Chrome, scan QR
CHROME_CDP_LOGIN=true feedgrab login zsxq                      # Reuse running Chrome session (recommended)
feedgrab https://articles.zsxq.com/id_sz9kew31q6we.html        # Long article
feedgrab https://wx.zsxq.com/group/<gid>/topic/<tid>           # Short topic
feedgrab https://t.zsxq.com/yUX3P                              # Invite short link (auto 302)


# === FlowUs (v0.24.0 new) ===
# Public share links — works zero-cookie
feedgrab https://flowus.cn/baochang/share/1e8b026a-cb5a-41bb-8f2c-61fed1d3cc54

# Paid / private docs — reuse local Chrome login state
CHROME_CDP_LOGIN=true feedgrab login flowus                    # CDP cookie extraction (recommended)
feedgrab login flowus                                          # Or Playwright browser manual login
feedgrab https://flowus.cn/share/<uuid>?code=<paid_code>       # Subsequent fetches reuse sessions/flowus.json

# Image mode: default uses previewable signed online URLs; enable to save local attachments
FLOWUS_DOWNLOAD_IMAGES=true feedgrab https://flowus.cn/share/<uuid>?code=<code>


# Auto-detect local Chrome UA and write to .env (recommended on first setup)
feedgrab detect-ua

# Run diagnostic checks (cookies, deps, queryId, network)
feedgrab doctor             # All platforms
feedgrab doctor x           # Twitter/X only
feedgrab doctor xhs         # Xiaohongshu only
feedgrab doctor mpweixin    # WeChat MP only

# View content stats
feedgrab list

# Reset a subdirectory (delete .md files + clean dedup index, for re-fetching)
feedgrab reset bookmarks/OpenClaw       # Reset a bookmark folder
feedgrab reset status_author/geekbb    # Reset a user tweets folder

# Clean up batch records and cache files from index directories (preserves dedup index)
feedgrab clean-index                  # Interactive confirmation
feedgrab clean-index --yes            # Skip confirmation
```

### Layer 2: Claude Code Skills

Install all skills with one command:

```bash
npx skills add iBigQiang/feedgrab
```

Includes 5 skills:

| Skill | Command | Description |
|-------|---------|-------------|
| `feedgrab` | `/feedgrab <URL>` | Core fetcher — URL to structured Markdown |
| `feedgrab-batch` | `/feedgrab-batch` | Batch fetcher — bookmarks, user tweets, search, etc. |
| `feedgrab-setup` | `/feedgrab-setup` | Setup guide — pip install + config + diagnostics |
| `analyzer` | `/analyze <URL>` | Content analyzer — multi-dimensional analysis report |
| `video` | Auto-triggered | Video/podcast — yt-dlp subtitles + Whisper transcription |

After installation, just send a URL in Claude Code — the corresponding skill auto-triggers.

### Layer 3: MCP Server

> Requires cloning the repo (mcp_server.py is not included in pip install).

```bash
git clone https://github.com/iBigQiang/feedgrab.git
cd feedgrab
pip install -e ".[mcp]"
python mcp_server.py
```

Tools exposed:
- `read_url(url)` — fetch any URL
- `read_batch(urls)` — fetch multiple URLs concurrently
- `list_inbox()` — view previously fetched content
- `detect_platform(url)` — identify platform from URL

Claude Code config (`~/.claude/claude_desktop_config.json`):
```json
{
    "mcpServers": {
        "feedgrab": {
            "command": "python",
            "args": ["/path/to/feedgrab/mcp_server.py"]
        }
    }
}
```

## Supported Platforms

| Platform | Text Fetch | Video/Audio Transcript |
|----------|-----------|----------------------|
| YouTube | **InnerTube API** (zero deps, zero quota) + YouTube Data API v3 search | InnerTube → yt-dlp subtitles → Groq Whisper fallback + smart segmentation + chapters |
| Bilibili (B站) | API metadata | **3-tier subtitle cascade** (`/x/player/v2` → `/x/player/wbi/v2` WBI-signed → Whisper opt-in) + shared Whisper pipeline |
| **Xiaoyuzhou (小宇宙)** | `__NEXT_DATA__` SSR (title/shownotes/podcast/m4a URL) | **Groq Whisper auto-transcribe** + smart segmentation + chapters |
| **Ximalaya (喜马拉雅)** | Web Revision API (audio + simple, free tracks m4a direct link) | **Groq Whisper auto-transcribe** (paid tracks `canPlay=false` gracefully degraded) |
| X / Twitter | **GraphQL** → **FxTwitter** → **Syndication** → oEmbed → Jina → Playwright | — |
| WeChat (微信公众号) | Jina → Playwright WeChat JS extraction (single + markdownify + image anti-hotlink) / Sogou search (`mpweixin-so`) / MP backend API batch by account (`mpweixin-id`) / Album batch (`mpweixin-zhuanji`) | — |
| GitHub | **REST API** (repo metadata + Chinese README priority (incl. subdirectory language link search) + relative image URL resolution + summary extraction) | — |
| LinuxDo / Discourse forum | **Discourse Topic JSON API** → **CDP Chrome reuse** → **Playwright in-page fetch** → Jina (last resort; saves OP + author follow-up replies by default, switchable to full thread) | — |
| IDCFlare / Discourse forum | **Discourse Topic JSON API** → **CDP Chrome reuse** → **Playwright in-page fetch** → Jina (last resort; saves OP + author follow-up replies by default, switchable to full thread) | — |
| Xiaohongshu (小红书) | **API (xhshow)** → **Pinia Store injection** → Jina → **Playwright deep fetch** (single + **author batch** + **search batch** + **keyword search `xhs-so`**) | — |
| Feishu/Lark (飞书) | **Open API** → **CDP direct connect** → **Playwright PageMain** → Jina (single + **wiki batch `feishu-wiki`** + embedded sheets + image download; virtual tree traversal + table-layout fix) | — |
| KDocs (金山文档) | **Playwright ProseMirror DOM** extraction (virtual scroll + code blocks + image shapes API + CDP direct connect) | — |
| Youdao Note (有道云笔记) | **JSON API** (zero dependency) → Playwright iframe DOM → Jina (single doc + image download) | — |
| Zhihu (知乎) | **API v4** → **Playwright CDP/DOM** → Jina (single Q&A top 3 answers + articles + **keyword search `zhihu-so`**) | — |
| Telegram | Telethon | — |
| RSS | feedparser | — |
| 小宇宙 (Xiaoyuzhou) | see Xiaoyuzhou row above | Groq Whisper auto-transcribe |
| Apple Podcasts | — | via Claude Code skill |
| **HackerNews** | **Hacker News Firebase API v0** (0 cookie / 0 anti-bot, single item + first-level comments + `hn top/new/best/ask/show/jobs` list batch) | — |
| **Medium** | **Jina Reader** → JSON-LD articleBody → Stealth Browser; user/publication batch via RSS feed (`medium-user @<handle>` / `medium-pub <slug>`) | — |
| **Reddit** | **old.reddit.com .json + self-identifying UA** → CDP reuse Chrome → Stealth Playwright + saved session → Jina (single posts support `REDDIT_REPLY_MODE=top/tree/all`, plus `reddit-sub` subreddit batch and `reddit-so` keyword search summary) | — |
| **Weibo (微博)** | m.weibo.cn mobile API (show + container/getIndex) + SSR `$render_data` fallback (single + `weibo-user` profile batch; SUB cookie optional) | — |
| **Douyin (抖音)** | **CDP reuse Chrome** → Stealth Playwright + saved session → SSR `RENDER_DATA` parse → Jina (no signature cracking, relies on browser-internal execution; short links auto-resolve via 302) | — |
| **Paywalled news sites** (NYT/WSJ/FT/Economist/Bloomberg/SCMP, 300+) | **7-tier paywall bypass** (JSON-LD probe + Googlebot/Bingbot UA + AMP pages + archive.today + Google Cache) | — |
| Any web page | **JSON-LD probe** → Jina fallback | — |

> \*XHS supports **API fetching** (xhshow, no login required) and **browser fetching** (requires one-time login: `feedgrab login xhs`). Single note fetch prefers API (full metadata + comments), falls back to **Pinia Store injection** (browser-native requests, no third-party signing library needed) → Jina → Playwright when unavailable. **Keyword search** (`feedgrab xhs-so`), **author profile batch**, and **search result batch** also support Pinia fallback. `XHS_PINIA_ENABLED=true` (enabled by default).
>
> YouTube Whisper transcription requires `GROQ_API_KEY` — get a free key from [Groq](https://console.groq.com/keys)

### X/Twitter Six-Tier Fallback

feedgrab uses an advanced six-tier strategy for X/Twitter content:

| Tier | Method | Auth Required | Capabilities |
|------|--------|--------------|-------------|
| 0 | **GraphQL API** | Cookie (`auth_token` + `ct0`) | Complete threads, images, videos, quoted tweets, articles |
| 0.3 | **FxTwitter API** | None | Text, images, videos, full engagement (incl. views/bookmarks), Article Draft.js, author profile |
| 0.5 | **Syndication API** | None | Text, images, videos, engagement (likes/replies), article detection |
| 1 | oEmbed API | None | Single tweet text (public tweets only) |
| 2 | Jina Reader | None | Profiles, non-tweet pages |
| 3 | Playwright | Optional session | Login-required content, last resort |

> **Value of the FxTwitter tier**: Third-party public API with near-GraphQL data completeness (views, bookmarks, Article full text) without authentication. Missing blue_verified, listed_count, and thread expansion. Auto circuit-breaker after 3 consecutive failures in batch mode.

> **Value of the Syndication tier**: When cookies are valid, GraphQL handles everything automatically and Syndication is rarely needed. Its real value is when all cookies expire — users can still get 80% of the data (missing only retweets/bookmarks/views) without immediately re-logging in, instead of degrading to text-only oEmbed.

Tier 0 (GraphQL) is ported from the [baoyu-danger-x-to-markdown](https://github.com/JimLiu/baoyu-skills/tree/main/skills/baoyu-danger-x-to-markdown) skill, featuring:
- Dynamic `queryId` resolution from X's frontend JS bundles
- Complete thread reconstruction (author self-reply chains)
- Multi-phase pagination (upward + downward + continuation)
- Full media extraction (images, videos, quoted tweets)
- Engagement metrics (likes / retweets / replies / bookmarks / views)
- Author replies + comments collection (opt-in toggles)
- **Bookmark batch fetch** (all bookmarks / specific folders)
- **User tweets batch fetch** (all / date-filtered, auto-skip RT + conversation dedup)
- **List tweets batch fetch** (day-filtered 1/2/3/7 days, conversation dedup, thread deep fetch)
- **Browser search supplement** (breaks UserTweets ~800 limit, auto month-chunked search)
- **Global dedup index** (unified cross-mode deduplication)

### X/Twitter Cookie Configuration

Tier 0 (GraphQL) requires Twitter cookies for full data access. **Without cookies, it auto-degrades** but you'll lose:
- Engagement metrics (likes / views / bookmarks)
- Author replies and comments
- Bookmark and user tweet batch fetch
- Only basic text via oEmbed + Jina fallback

**Setup (choose one):**

#### Method 1: Browser login (recommended)

```bash
feedgrab login twitter
```

Opens a browser, saves cookies to `sessions/twitter.json` after login.

#### Method 2: `.env` environment variables

Copy cookie values from browser DevTools:

1. Open https://x.com and log in
2. F12 → Application → Cookies → `https://x.com`
3. Find `auth_token` and `ct0` values
4. Add to `.env`:

```env
X_AUTH_TOKEN=your_auth_token
X_CT0=your_ct0
```

> Environment variables take highest priority.

#### Method 3: Manual cookie file

Create `sessions/x.json`:

```json
{
  "auth_token": "your_auth_token",
  "ct0": "your_ct0"
}
```

> Also supports Method 4: Chrome CDP auto-extraction — enable `chrome://inspect/#remote-debugging` in Chrome, then `CHROME_CDP_LOGIN=true feedgrab login twitter` to instantly extract cookies from your logged-in Chrome.

**Cookie priority**: Environment variables > Playwright session (`twitter.json`) > Cookie file (`x.json`) > Chrome CDP

#### Multi-Account Cookie Rotation (Anti 429 Rate Limit)

Batch fetching via GraphQL easily triggers 429 rate limits. Configure multiple X account cookies for automatic rotation:

```
sessions/
├── twitter.json    ← Primary account (auto-generated by feedgrab login twitter)
├── x_2.json        ← Second account (manually created)
├── x_3.json        ← Third account...
```

Additional cookie files use the same format as Method 3. To get cookies:

1. Open https://x.com in Chrome/Edge and log into the target account
2. F12 → **Application** tab → expand **Cookies** → click `https://x.com`
3. Find `auth_token` and `ct0` rows, copy values to `sessions/x_2.json`

> Cookies are not device-bound. They work across machines as long as you don't log out in the browser.
>
> On 429, automatically switches to next available account. Auto-recovers after 15-minute cooldown.

### TwitterAPI.io Paid API (Optional)

Server-friendly alternative to browser search supplement. No tweet count limit, $0.15/1K tweets.

**Use cases**:
- Auto-replaces Playwright browser search when tweets exceed 800 (just configure API Key)
- Server deployment: `X_API_PROVIDER=api` for full API-only path, no cookies or browser needed

```env
# .env configuration
TWITTERAPI_IO_KEY=your_api_key       # Get from https://twitterapi.io
# X_API_PROVIDER=graphql             # graphql(default) | api(full paid API)
# X_API_SAVE_DIRECTLY=false          # true=save directly(fast,no images) | false=GraphQL supplement(recommended)
# X_API_MIN_LIKES=                   # Min likes filter (empty=no filter, OR logic across all three)
# X_API_MIN_RETWEETS=                # Min retweets filter
# X_API_MIN_VIEWS=                   # Min views filter
```

**Smart Direct Save** (`X_API_SAVE_DIRECTLY=true`): Normal tweets save API data directly (fast), articles and threads still use GraphQL for full media.

**Breakpoint Resume**: Discovery phase writes cache in real-time. Resume from where you left off after interruption without re-consuming API quota.

### Output Format

Each fetched item is saved as an individual Markdown file, organized by platform:

```
output/
├── X/                    # Twitter/X
│   ├── index/            #   Dedup index + batch fetch records
│   ├── status/           #   Single tweets
│   ├── status_xxx/       #   User tweets (by display_name)
│   ├── bookmarks/        #   All bookmarks
│   ├── bookmarks_xxx/    #   Bookmark folders (by name)
│   └── search/           #   Keyword search results (x-so command, .md + .csv)
│       └── 1day_new/     #     By days + sort mode
├── XHS/                  # Xiaohongshu
│   ├── index/            #   Dedup index + batch fetch records
│   ├── notes_xxx/        #   Author notes (subdirectory per author)
│   ├── search_xxx/       #   Search notes (subdirectory per keyword)
│   └── search/           #   Keyword search results (xhs-so command, .md + .csv)
├── mpweixin/             # WeChat articles
├── YouTube/
├── GitHub/               # GitHub repos
│   └── index/            #   Dedup index
├── Bilibili/
├── Telegram/
└── RSS/
```

Files use Obsidian-compatible YAML front matter:

**Twitter example:**

```yaml
---
title: "OpenClaw Beginner Guide"
source: "https://x.com/AI_Jasonyu/status/123"
author:
  - "@AI_Jasonyu"
author_name: "鱼总聊AI"
published: 2026-02-25
created: 2026-02-26
cover_image: "https://pbs.twimg.com/media/xxx.jpg"
likes: 1075
retweets: 315
replies: 41
bookmarks: 2180
views: 426321
tags:
  - "clippings"
  - "twitter"
---
```

**Xiaohongshu example:**

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
```

> Set `OBSIDIAN_VAULT` to write directly into your Obsidian vault under platform subdirectories.

## Install

```bash
# From GitHub (recommended)
pip install git+https://github.com/iBigQiang/feedgrab.git

# With Telegram support
pip install "feedgrab[telegram] @ git+https://github.com/iBigQiang/feedgrab.git"

# With browser fallback (Playwright — for XHS/WeChat anti-scraping)
pip install "feedgrab[browser] @ git+https://github.com/iBigQiang/feedgrab.git"
playwright install chromium

# Twitter search enhancement (x-client-transaction-id signing, required for x-so command)
pip install "feedgrab[twitter] @ git+https://github.com/iBigQiang/feedgrab.git"

# XHS API enhancement (xhshow API fetching, required for xhs-so command)
pip install "feedgrab[xhs] @ git+https://github.com/iBigQiang/feedgrab.git"

# With all optional dependencies
pip install "feedgrab[all] @ git+https://github.com/iBigQiang/feedgrab.git"
playwright install chromium
```

Or clone and install locally:
```bash
git clone https://github.com/iBigQiang/feedgrab.git
cd feedgrab
pip install -e ".[all]"
playwright install chromium
```

### Dependencies for video/audio (optional)

```bash
# macOS
brew install yt-dlp ffmpeg

# Linux
pip install yt-dlp
apt install ffmpeg
```

For Whisper transcription, get a free API key from [Groq](https://console.groq.com/keys) and set:
```bash
export GROQ_API_KEY=your_key_here
```

## Use as Library

```python
import asyncio
from feedgrab.reader import UniversalReader

async def main():
    reader = UniversalReader()
    content = await reader.read("https://mp.weixin.qq.com/s/abc123")
    print(content.title)
    print(content.content[:200])

asyncio.run(main())
```

### Structured Service API

New clients should prefer `feedgrab.service.FetchService`. It reuses the existing `UniversalReader`, Markdown persistence, media localization, and dedup logic, while returning a structured `FetchResult`. `content` is the existing `UnifiedContent`, and `artifacts` records generated Markdown paths.

```python
import asyncio
from feedgrab.service import FetchService

async def main():
    service = FetchService()
    result = await service.fetch_url("https://github.com/iBigQiang/feedgrab")
    print(result.content.title)
    for artifact in result.artifacts:
        print(artifact.kind, artifact.path)

asyncio.run(main())
```

CLI commands keep their existing behavior. The MCP server now calls the same service layer for fetch operations. Batch fetches and MCP calls expose structured failure items instead of losing the whole batch on a single URL failure. Global proxy settings are propagated to HTTP, Playwright, the XHS API client, and yt-dlp.

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

The recommended setup uses one variable and the proxy diagnostic command. When unset, feedgrab also checks the standard `HTTPS_PROXY`, `HTTP_PROXY`, and `ALL_PROXY` variables in that order:

```bash
FEEDGRAB_PROXY=socks5://127.0.0.1:8567 feedgrab doctor proxy
```

| Variable | Required | Description |
|----------|----------|-------------|
| `FEEDGRAB_LOG_LEVEL` | No | Log level: `INFO` (default) / `DEBUG` / `WARNING` |
| `FEEDGRAB_PROXY` | No | Recommended global proxy setting; setting it enables the proxy, e.g. `socks5://127.0.0.1:8567` |
| `FEEDGRAB_PROXY_ENABLED` | No | Legacy enable switch; explicitly set `false` to disable proxy use |
| `FEEDGRAB_PROXY_URL` | No | Legacy-compatible HTTP / SOCKS5 proxy URL |
| `FEEDGRAB_NO_PROXY` | No | Comma-separated bypass list, default `127.0.0.1,localhost`, keeping local CDP/internal services direct |
| `X_AUTH_TOKEN` | X GraphQL only | Twitter/X auth cookie |
| `X_CT0` | X GraphQL only | Twitter/X CSRF token cookie |
| `X_GRAPHQL_ENABLED` | No | Enable/disable GraphQL tier (default: `true`) |
| `X_THREAD_MAX_PAGES` | No | Max pagination for threads (default: `20`) |
| `X_REQUEST_DELAY` | No | Delay between GraphQL requests in seconds (default: `1.5`) |
| `X_FETCH_AUTHOR_REPLIES` | No | Collect author's replies to commenters (default: `false`) |
| `X_FETCH_ALL_COMMENTS` | No | Collect all comments under tweet (default: `false`) |
| `X_MAX_COMMENTS` | No | Max comments to collect (default: `50`) |
| `X_BOOKMARKS_ENABLED` | No | Enable bookmark batch fetch (default: `false`) |
| `X_BOOKMARK_MAX_PAGES` | No | Max pagination for bookmarks (default: `50`) |
| `X_BOOKMARK_DELAY` | No | Delay between bookmark fetches in seconds (default: `2.0`) |
| `X_USER_TWEETS_ENABLED` | No | Enable user tweets batch fetch (default: `false`) |
| `X_USER_TWEET_MAX_PAGES` | No | Max pagination for user tweets (default: `200`) |
| `X_USER_TWEET_DELAY` | No | Delay between user tweet fetches in seconds (default: `2.0`) |
| `X_USER_TWEETS_SINCE` | No | Only fetch tweets after this date (e.g. `2025-10-01`, empty=all) |
| `X_LIST_TWEETS_ENABLED` | No | Enable Twitter List batch fetch (default: `false`) |
| `X_LIST_TWEETS_DAYS` | No | Fetch tweets from last N days (default: `1`, supports 1/2/3/7) |
| `X_LIST_TWEET_MAX_PAGES` | No | Max pagination for list tweets (default: `50`) |
| `X_LIST_TWEET_DELAY` | No | Delay between list tweet fetches in seconds (default: `2`) |
| `X_LIST_TWEETS_SUMMARY` | No | Generate summary table (MD + CSV) after list fetch (default: `false`) |
| `X_SEARCH_SUPPLEMENTARY` | No | Search supplement when UserTweets insufficient (default: `true`) |
| `X_SEARCH_MAX_PAGES_PER_CHUNK` | No | Max pages per monthly search chunk (default: `50`) |
| `TWITTERAPI_IO_KEY` | No | TwitterAPI.io paid API key from https://twitterapi.io |
| `X_API_PROVIDER` | No | `graphql` (default) or `api` (full paid API) |
| `X_API_SAVE_DIRECTLY` | No | `true`=save API data directly / `false`=GraphQL supplement (default) |
| `X_API_MIN_LIKES` | No | Min likes filter (empty=no filter, OR logic across all three) |
| `X_API_MIN_RETWEETS` | No | Min retweets filter (empty=no filter) |
| `X_API_MIN_VIEWS` | No | Min views filter (empty=no filter) |
| `FORCE_REFETCH` | No | Force re-fetch, skip dedup and overwrite existing files (default: `false`) |
| `X_SEARCH_ENABLED` | No | Enable Twitter keyword search (default: `true`) |
| `X_SEARCH_LANG` | No | Default search language (default: `zh`; `zh+zxx` covers Chinese tweets plus Article cards; empty=any) |
| `X_SEARCH_DAYS` | No | Default search time range in days (default: `1`) |
| `X_SEARCH_MIN_FAVES` | No | Default min likes filter (default: `0`=no filter) |
| `X_SEARCH_SORT` | No | Search sort: `live`=Latest / `top`=Top / `all`=merged Latest+Top (default: `live`) |
| `X_SEARCH_MAX_RESULTS` | No | Max tweets per search (default: `100`) |
| `X_SEARCH_SAVE_TWEETS` | No | Save individual tweet .md files (default: `false`, summary table only) |
| `X_SEARCH_MERGE_KEYWORDS` | No | Merge multi-keyword search results into one file (default: `false`, also via `--merge` flag) |
| `XHS_USER_NOTES_ENABLED` | No | Enable XHS author batch fetch (default: `false`) |
| `XHS_USER_NOTE_MAX_SCROLLS` | No | Max scroll iterations on author profile (default: `50`) |
| `XHS_USER_NOTE_DELAY` | No | Delay between note fetches in seconds (default: `3.0`) |
| `XHS_USER_NOTES_SINCE` | No | Only fetch notes after this date (e.g. `2026-02-01`, empty=all) |
| `XHS_SEARCH_ENABLED` | No | Enable XHS search batch fetch (default: `false`) |
| `XHS_SEARCH_MAX_SCROLLS` | No | Max scroll iterations on search page (default: `30`) |
| `XHS_SEARCH_DELAY` | No | Delay between search note fetches in seconds (default: `3.0`) |
| `XHS_API_ENABLED` | No | Enable xhshow API fetching (default: `true`, auto-activates when xhshow installed) |
| `XHS_API_DELAY` | No | API request interval in seconds (default: `1.0`, with random jitter) |
| `XHS_SEARCH_SORT` | No | xhs-so search sort: `general` / `popular` / `latest` (default: `general`) |
| `XHS_SEARCH_NOTE_TYPE` | No | xhs-so search type: `all` / `video` / `image` (default: `all`) |
| `XHS_SEARCH_MAX_PAGES` | No | xhs-so max search pages, 20 results per page (default: `5`) |
| `XHS_SEARCH_MERGE_KEYWORDS` | No | Merge multi-keyword search results into one file (default: `false`, also via `--merge` flag) |
| `MPWEIXIN_SOGOU_ENABLED` | No | Enable Sogou WeChat article search (default: `false`) |
| `MPWEIXIN_SOGOU_MAX_RESULTS` | No | Max articles per search (default: `10`, max `100`) |
| `MPWEIXIN_SOGOU_DELAY` | No | Delay between article fetches in seconds (default: `3.0`) |
| `MPWEIXIN_ZHUANJI_SINCE` | No | Album batch: only fetch articles after this date (`YYYY-MM-DD`, empty=all) |
| `MPWEIXIN_ZHUANJI_DELAY` | No | Album batch: delay between article fetches in seconds (default: `3.0`) |
| `MPWEIXIN_FETCH_COMMENTS` | No | Fetch article comments (experimental, default: `false`, requires WeChat client session) |
| `MPWEIXIN_MAX_COMMENTS` | No | Max comments to fetch per article (default: `100`) |
| `LINUXDO_CDP_ENABLED` | No | Prefer reusing a running Chrome session for LinuxDo JSON fetches (default: `true`) |
| `LINUXDO_PAGE_LOAD_TIMEOUT` | No | LinuxDo browser page wait timeout in milliseconds (default: `15000`) |
| `LINUXDO_REPLY_MODE` | No | LinuxDo reply capture mode: `author` (default, OP + topic author replies only) / `all` (full thread) / `none` (OP only) |
| `IDCFLARE_CDP_ENABLED` | No | Prefer reusing a running Chrome session for IDCFlare JSON fetches (default: `true`) |
| `IDCFLARE_PAGE_LOAD_TIMEOUT` | No | IDCFlare browser page wait timeout in milliseconds (default: `15000`) |
| `IDCFLARE_REPLY_MODE` | No | IDCFlare reply capture mode: `author` (default, OP + topic author replies only) / `all` (full thread) / `none` (OP only) |
| `REDDIT_REPLY_MODE` | No | Reddit reply mode: `top` (default, top-level only) / `tree` (initial nested tree) / `all` (extra morechildren expansion) |
| `REDDIT_RETRY_ATTEMPTS` | No | Retry count for Reddit direct `.json` 429/5xx/network errors (default: `3`) |
| `REDDIT_MAX_PAGES` | No | Max `after` cursor pages for `reddit-so` / `reddit-sub` (default: `5`) |
| `REDDIT_MORECHILDREN_ROUNDS` | No | morechildren expansion rounds when `REDDIT_REPLY_MODE=all` (default: `2`) |
| `REDDIT_SEARCH_ENABLED` | No | Enable Reddit keyword search `reddit-so` (default: `true`) |
| `REDDIT_SEARCH_SORT` | No | `reddit-so` sort: `relevance` / `hot` / `top` / `new` / `comments` (default: `relevance`) |
| `REDDIT_SEARCH_TIME_RANGE` | No | `reddit-so` time range: `all` / `year` / `month` / `week` / `day` / `hour` (default: `all`) |
| `REDDIT_SEARCH_LIMIT` | No | Default `reddit-so` result count (default: `10`) |
| `REDDIT_SEARCH_SAVE_POSTS` | No | Deep-fetch each post after `reddit-so` search (default: `false`) |
| `REDDIT_SEARCH_SUBREDDIT` | No | Default subreddit scope for `reddit-so`, empty=sitewide |
| `CHROME_CDP_LOGIN` | No | Enable CDP cookie extraction from running Chrome (default: `false`) |
| `CHROME_CDP_PORT` | No | Chrome CDP port (default: `9222`) |
| `X_DOWNLOAD_MEDIA` | No | Download Twitter images/videos to local `attachments/` subdirectory (default: `false`) |
| `XHS_DOWNLOAD_MEDIA` | No | Download XHS images to local `attachments/` subdirectory (default: `false`) |
| `MPWEIXIN_DOWNLOAD_MEDIA` | No | Download WeChat article videos to local `attachments/` subdirectory (default: `false`) |
| `GITHUB_TOKEN` | No | GitHub personal access token (without: 60 req/h, with: 5000 req/h) |
| `BROWSER_USER_AGENT` | No | Global browser UA (recommend `feedgrab detect-ua` for auto-detection) |
| `TG_API_ID` | Telegram only | From https://my.telegram.org |
| `TG_API_HASH` | Telegram only | From https://my.telegram.org |
| `GROQ_API_KEY` | Whisper only | From https://console.groq.com/keys (free) |
| `GEMINI_API_KEY` | AI analysis only | From Google AI Studio |
| `FEEDGRAB_DATA_DIR` | No | Cookie/session storage directory (default: `sessions`) |
| `OUTPUT_DIR` | No | Directory for Markdown output (default: `./output`) |
| `OBSIDIAN_VAULT` | No | Path to Obsidian vault (writes to platform subdirectories) |

## Architecture

```
feedgrab/
├── feedgrab/                  # Python package
│   ├── cli.py                 # CLI entry point
│   ├── config.py              # Centralized config (paths, feature flags)
│   ├── reader.py              # URL dispatcher (UniversalReader)
│   ├── schema.py              # Unified data model (UnifiedContent + Inbox)
│   ├── login.py               # Browser login manager (+ CDP cookie extraction)
│   ├── fetchers/
│   │   ├── jina.py            # Jina Reader (universal fallback)
│   │   ├── browser.py         # Playwright headless (anti-scraping fallback)
│   │   ├── bilibili.py        # Bilibili API
│   │   ├── youtube.py         # yt-dlp subtitle extraction
│   │   ├── github.py          # GitHub REST API (repo metadata + Chinese README priority + subdirectory search + image URL resolution)
│   │   ├── linuxdo.py         # LinuxDo / Discourse topic fetcher (JSON API → CDP → browser → Jina)
│   │   ├── idcflare.py        # IDCFlare / Discourse topic fetcher (JSON API → CDP → browser → Jina)
│   │   ├── rss.py             # RSS (feedparser)
│   │   ├── telegram.py        # Telegram (Telethon)
│   │   ├── twitter.py         # X/Twitter six-tier dispatcher
│   │   ├── twitter_cookies.py # Cookie multi-source management + rotation
│   │   ├── twitter_graphql.py # X GraphQL API client (TweetDetail, UserTweets, Bookmarks, SearchTimeline + x-client-transaction-id)
│   │   ├── twitter_thread.py  # Thread reconstruction + comment classification
│   │   ├── twitter_bookmarks.py  # Bookmark batch fetch
│   │   ├── twitter_user_tweets.py # User tweets batch fetch
│   │   ├── twitter_list_tweets.py # List tweets batch fetch (day-filtered + conversation dedup)
│   │   ├── twitter_search_tweets.py # Browser search supplement (breaks 800 limit)
│   │   ├── twitter_keyword_search.py # Keyword search (x-so command, pure GraphQL + engagement-ranked table)
│   │   ├── twitter_api.py     # TwitterAPI.io paid API client
│   │   ├── twitter_api_user_tweets.py # Paid API supplement/full fetch
│   │   ├── twitter_markdown.py# Thread Markdown renderer (YAML front matter + media)
│   │   ├── wechat.py          # Jina → Playwright WeChat JS extraction
│   │   ├── mpweixin_account.py # WeChat account batch (MP backend API + resume)
│   │   ├── mpweixin_album.py  # WeChat album batch (mpweixin-zhuanji + resume)
│   │   ├── xhs.py             # API (xhshow) → Pinia Store injection → Jina → Playwright 4-tier fallback
│   │   ├── xhs_api.py         # XHS API client (xhshow signing + comments + xsec_token cache)
│   │   ├── xhs_pinia.py       # XHS Pinia Store injection (browser-native fallback, CDP-first)
│   │   ├── xhs_user_notes.py  # XHS author batch fetch (API → Pinia → browser 3-tier strategy)
│   │   ├── xhs_search_notes.py # XHS search batch fetch (xhs-so API/Pinia search + scroll + deep fetch)
│   │   ├── feishu.py          # Feishu single doc (Open API → Playwright PageMain → Jina + Block→MD + image download)
│   │   ├── feishu_wiki.py     # Feishu wiki batch (Open API recursive + virtual-tree/Playwright fallback + resume)
│   │   ├── kdocs.py           # KDocs (WPS) single doc (Playwright ProseMirror DOM + virtual scroll + CDP)
│   │   └── youdao.py          # Youdao Note single doc (JSON API + Playwright iframe DOM + Jina 3-tier fallback)
│   └── utils/
│       ├── storage.py         # Per-platform Markdown + JSON dual output
│       ├── dedup.py           # Global dedup index (cross-mode unified tracking)
│       ├── http_client.py     # Unified HTTP client (curl_cffi TLS fingerprint → requests fallback)
│       └── media.py           # Media file download (Twitter/XHS image/video localization)
├── sessions/                  # Cookie/session storage (auto-created, git-ignored)
├── skills/                    # Claude Code skills (npx skills add iBigQiang/feedgrab)
│   ├── feedgrab/              # Core fetcher — /feedgrab <URL>
│   ├── feedgrab-batch/        # Batch fetcher — /feedgrab-batch
│   ├── feedgrab-setup/        # Setup guide — /feedgrab-setup
│   ├── video/                 # Video/podcast → transcript + summary
│   └── analyzer/              # Content → structured analysis
├── mcp_server.py              # MCP server entry point
└── pyproject.toml
```

## How the Layers Work Together

```
User sends URL
    │
    ├─ Text content (article, tweet, WeChat)
    │   └─ Python fetcher → UnifiedContent → inbox
    │
    ├─ X/Twitter tweet or thread
    │   └─ GraphQL (full thread + media) → FxTwitter → Syndication → oEmbed → Jina → Playwright
    │
    ├─ Video (YouTube, Bilibili, X video)
    │   ├─ Python fetcher → metadata (title, description)
    │   └─ Video skill → full transcript via subtitles/Whisper
    │
    ├─ Podcast (小宇宙, Apple Podcasts)
    │   └─ Video skill → full transcript via Whisper
    │
    └─ Analysis requested
        └─ Analyzer skill → structured report + action items
```

## Credits

feedgrab is a fusion upgrade built on the following two projects. It inherits x-reader's multi-platform architecture and incorporates the deep X/Twitter GraphQL fetching capabilities developed in baoyu-danger-x-to-markdown:

- **[x-reader](https://github.com/runesleo/x-reader)** by [@runes_leo](https://x.com/runes_leo): the original multi-platform content reader that provided the core architecture, CLI, MCP server, and fetchers for 7+ platforms.
- **[baoyu-danger-x-to-markdown](https://github.com/JimLiu/baoyu-skills/tree/main/skills/baoyu-danger-x-to-markdown)** by [@dotey](https://x.com/dotey) (宝玉): the X/Twitter deep-fetching skill that provided reverse-engineered GraphQL API access, thread reconstruction, and Markdown rendering.

## Author

Maintained by [@iBigQiang](https://github.com/iBigQiang)

## Donate

If feedgrab has been helpful to you, feel free to buy the author a coffee :)

<p align="center">
  <img src="docs/Payment_QR_code.png" alt="Pay QR code" width="600">
</p>

## License

MIT

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=iBigQiang/feedgrab&type=Date)](https://star-history.com/#iBigQiang/feedgrab&Date)
