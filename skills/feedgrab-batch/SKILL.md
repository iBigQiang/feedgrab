---
name: feedgrab-batch
description: Batch content grabber — bulk fetch bookmarks, user tweets, search results, author notes, wiki pages, and more from X/Twitter, Xiaohongshu, WeChat, YouTube, Feishu, Zhihu. Use when user wants to batch/bulk fetch, search keywords, or grab all posts from an account.
---

# feedgrab-batch — Batch Content Grabber

> Bulk fetch content from any platform — bookmarks, user timelines, keyword search, album pages, wiki trees.

## Trigger

Activate when user mentions batch/bulk operations:
- `/feedgrab-batch <command> <args>`
- "Grab all my bookmarks"
- "批量抓取这个用户的推文"
- "搜索关键词 AI Agent"
- "抓取这个公众号的所有文章"
- "下载这个飞书知识库"

## Prerequisites

1. feedgrab installed (`which feedgrab`)
2. Platform-specific cookies/API keys configured (see each command below)

If not ready, suggest `/feedgrab-setup`.

## Command Reference

### X/Twitter

#### Bookmarks
```bash
# Requires: X_BOOKMARKS_ENABLED=true + feedgrab login twitter (primary session only)
feedgrab https://x.com/i/history                            # All bookmarks (current X URL)
feedgrab https://x.com/i/history/bookmarks/2015311287715340624  # Specific folder
feedgrab https://x.com/i/bookmarks                          # Legacy URL, still accepted
```

#### User Tweets
```bash
# Requires: X_USER_TWEETS_ENABLED=true + feedgrab login twitter
feedgrab https://x.com/username                              # All tweets
X_USER_TWEETS_SINCE=2026-01-01 feedgrab https://x.com/username  # Since date
# Auto browser search supplement when >800 tweets
```

#### List Tweets
```bash
# Requires: X_LIST_TWEETS_ENABLED=true + feedgrab login twitter
feedgrab https://x.com/i/lists/LIST_ID                       # Last 1 day
X_LIST_TWEETS_DAYS=7 feedgrab https://x.com/i/lists/LIST_ID  # Last 7 days
X_LIST_TWEETS_SUMMARY=true feedgrab https://x.com/i/lists/LIST_ID  # + summary table
```

#### Keyword Search (x-so)
```bash
feedgrab x-so "AI Agent"                          # Search tweets
feedgrab x-so "AI Agent" --days 7                 # Last 7 days
feedgrab x-so "AI Agent" --min-faves 100          # Min 100 likes
feedgrab x-so "claude,cursor,copilot" --merge     # Multi-keyword merged table
feedgrab x-so "AI Agent" --raw                    # Raw query syntax
```
Output: Markdown table (sorted by views) + CSV at `output/X/search/`

---

### Xiaohongshu (小红书)

#### Author Notes
```bash
# Requires: XHS_USER_NOTES_ENABLED=true + feedgrab login xhs
feedgrab https://www.xiaohongshu.com/user/profile/USER_ID
XHS_USER_NOTES_SINCE=2026-01-01 feedgrab https://www.xiaohongshu.com/user/profile/USER_ID
```

#### Search Results
```bash
# Requires: XHS_SEARCH_ENABLED=true + feedgrab login xhs
feedgrab "https://www.xiaohongshu.com/search_result?keyword=开学第一课&source=web_explore_feed"
```

#### Keyword Search (xhs-so)
```bash
feedgrab xhs-so "AI Agent"                          # Search notes
feedgrab xhs-so "AI Agent" --sort popular            # By popularity
feedgrab xhs-so "AI Agent" --type video              # Videos only
feedgrab xhs-so "AI Agent" --save                    # Also save individual .md
feedgrab xhs-so "claude,cursor" --merge              # Multi-keyword merged
```
Output: Markdown table (sorted by likes) + CSV at `output/XHS/search/`

---

### WeChat (微信公众号)

#### By Account (mpweixin-id)
```bash
# Requires: feedgrab login wechat (MP backend session, ~4 day validity)
feedgrab mpweixin-id "公众号名称"
MPWEIXIN_ID_SINCE=2026-01-01 feedgrab mpweixin-id "公众号名称"  # Since date
```

#### Album / Collection (mpweixin-zhuanji)
```bash
feedgrab mpweixin-zhuanji "ALBUM_URL"                # No login needed
MPWEIXIN_ZHUANJI_SINCE=2026-01-01 feedgrab mpweixin-zhuanji "ALBUM_URL"
```

#### Sogou Search (mpweixin-so)
```bash
feedgrab mpweixin-so "关键词"                         # Search via Sogou
feedgrab mpweixin-so "关键词" --limit 30              # Max 30 results
```

---

### YouTube

#### Search (ytb-so)
```bash
# Requires: YOUTUBE_API_KEY
feedgrab ytb-so "machine learning"                    # Search videos
feedgrab ytb-so "AI" --order viewCount               # Sort by views
feedgrab ytb-so "AI" --channel @3blue1brown          # Channel-specific
feedgrab ytb-so "AI" --days 30                       # Last 30 days
```

#### Download
```bash
feedgrab ytb-dlv "VIDEO_URL"    # Download video (MP4)
feedgrab ytb-dla "VIDEO_URL"    # Download audio (MP3)
feedgrab ytb-dlz "VIDEO_URL"    # Download subtitles (SRT)
```

---

### Feishu/Lark (飞书)

#### Wiki Batch
```bash
# Requires: FEISHU_APP_ID + FEISHU_APP_SECRET
feedgrab feishu-wiki "WIKI_SPACE_URL"
```

---

### Zhihu (知乎)

#### Keyword Search (zhihu-so)
```bash
# Requires: feedgrab login zhihu (for full content)
feedgrab zhihu-so "AI Agent"                          # Search Q&A + articles
feedgrab zhihu-so "AI Agent" --sort hot               # By popularity
feedgrab zhihu-so "AI Agent" --limit 20               # Max 20 results
feedgrab zhihu-so "AI Agent" --days 30                # Last 30 days
feedgrab zhihu-so "claude,cursor" --merge             # Multi-keyword merged
```
Output: Markdown table (sorted by upvotes) + CSV at `output/Zhihu/search/`

---

## Environment Variables Quick Reference

| Platform | Required Variables | How to Get |
|----------|-------------------|------------|
| Twitter (basic) | Cookie via `feedgrab login twitter` | Login in browser |
| Twitter (paid API) | `TWITTERAPI_IO_KEY` | twitterapi.io |
| Xiaohongshu | Cookie via `feedgrab login xhs` | Login in browser |
| WeChat batch | Cookie via `feedgrab login wechat` | Login MP backend |
| YouTube | `YOUTUBE_API_KEY` | Google Cloud Console |
| GitHub | `GITHUB_TOKEN` (optional) | GitHub Settings |
| Feishu | `FEISHU_APP_ID` + `FEISHU_APP_SECRET` | Feishu Open Platform |
| Zhihu | Cookie via `feedgrab login zhihu` | Login in browser |

## Resume After Interruption

All batch commands support **checkpoint resume** — if interrupted, just re-run the same command. Already fetched items are skipped via dedup index.

## Tips

- Use `feedgrab doctor <platform>` to diagnose configuration issues
- Media download: set `X_DOWNLOAD_MEDIA=true` / `XHS_DOWNLOAD_MEDIA=true` / `MPWEIXIN_DOWNLOAD_MEDIA=true`
- Multi-account cookie rotation for Twitter: put extra cookies in `sessions/x_2.json`, `x_3.json`
