# -*- coding: utf-8 -*-
"""
Twitter/X Bookmarks batch fetcher — fetch all bookmarked tweets via GraphQL.

Supports:
    - feedgrab https://x.com/i/history           (all bookmarks, current X URL)
    - feedgrab https://x.com/i/bookmarks          (all bookmarks, legacy URL)
    - feedgrab https://x.com/i/history/bookmarks/{folderId} (folder, current)
    - feedgrab https://x.com/i/bookmarks/{folderId} (folder, legacy)

Design (Approach B — hybrid):
    - Extract tweet data directly from bookmark API response (no extra API call)
    - Only fetch full threads or article bodies when needed (secondary API calls)
    - Stream-save each tweet immediately (don't wait for all to finish)
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from loguru import logger
from typing import Dict, Any, Optional, List

from feedgrab.config import x_bookmark_max_pages, x_bookmark_delay, force_refetch
from feedgrab.fetchers.twitter_graphql import (
    fetch_bookmarks_page,
    fetch_bookmark_folder_page,
    fetch_bookmark_folders,
    parse_bookmark_entries,
    extract_tweet_data,
)
from feedgrab.utils.dedup import (
    load_index,
    save_index,
    has_item,
    add_item,
    item_id_from_url,
)


# ---------------------------------------------------------------------------
# Bookmark URL list persistence
# ---------------------------------------------------------------------------

def _get_bookmark_list_dir() -> Path:
    """Return the index directory for bookmark batch records."""
    from feedgrab.utils.dedup import get_index_path
    index_dir = get_index_path().parent
    index_dir.mkdir(parents=True, exist_ok=True)
    return index_dir


def _save_bookmark_list(bookmark_list: list, folder_id: str = "", folder_name: str = ""):
    """Save bookmark URL list to a JSON file in the bookmarks/ directory."""
    out_dir = _get_bookmark_list_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = _sanitize_folder_name(folder_name) if folder_name else (folder_id if folder_id else "all")
    filename = f"bookmarks_{label}_{ts}.json"
    path = out_dir / filename

    payload = {
        "fetched_at": datetime.now().isoformat(),
        "folder_id": folder_id or "",
        "folder_name": folder_name or "",
        "total": len(bookmark_list),
        "tweets": bookmark_list,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"[Bookmarks] URL 列表已保存: {path}")
    return path


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def _parse_bookmark_url(url: str) -> Dict[str, str]:
    """Parse a bookmark URL and extract folder_id if present.

    Returns:
        dict with 'type' ('all' or 'folder') and optional 'folder_id'
    """
    # https://x.com/i/history/bookmarks/2015311287715340624  (current X form)
    # https://x.com/i/bookmarks/2015311287715340624           (legacy form)
    # A bare /i/history or /i/bookmarks carries no folder id and falls through
    # to type="all", which is exactly the bookmarks overview.
    match = re.search(r'/i/(?:history/)?bookmarks(?:/([0-9]+))?', url)
    if match and match.group(1):
        return {"type": "folder", "folder_id": match.group(1)}
    return {"type": "all", "folder_id": ""}


def _graphql_error_summary(response: Dict[str, Any]) -> str:
    """Join GraphQL error messages from a bookmark response ('' when none).

    Twitter returns HTTP 200 + a non-empty ``errors`` array for permission
    failures, so a truthy response is NOT proof that content was returned.
    """
    errors = (response or {}).get("errors") or []
    msgs = [str(e.get("message", "")).strip() for e in errors]
    return "; ".join(m for m in msgs if m)


def _sanitize_folder_name(name: str) -> str:
    """Sanitize a folder name for use as a filesystem directory."""
    # Strip characters illegal in Windows paths
    name = re.sub(r'[\\/:*?"<>|]', '', name)
    name = name.strip('. ')
    return name or "unnamed"


def _resolve_folder_name(folder_id: str, cookies: dict) -> str:
    """Resolve a bookmark folder name via BookmarkFoldersSlice API.

    Falls back to folder_id itself on any failure.
    """
    try:
        folders = fetch_bookmark_folders(cookies)
        for f in folders:
            if f["id"] == folder_id:
                logger.info(f"[Bookmarks] 文件夹名称: {f['name']}")
                return f["name"]
        logger.warning(f"[Bookmarks] 文件夹列表中未找到 ID={folder_id}，使用 ID 作为目录名")
    except Exception as e:
        logger.warning(f"[Bookmarks] 获取文件夹名称失败: {e}，使用 ID 作为目录名")
    return folder_id


# ---------------------------------------------------------------------------
# Jina content quality helpers (shared by bookmarks / user_tweets / twitter)
# ---------------------------------------------------------------------------

def _is_jina_garbage(content: str) -> bool:
    """Detect if Jina content is Twitter page chrome / login page garbage."""
    garbage_markers = [
        # Login page variant
        "New to X?",
        "Sign up now to get your own personalized timeline",
        "Sign up with Google",
        "Create account",
        "Terms of Service",
        "Cookie Use",
        "This page maybe not yet fully loaded",
        "Trending now",
        "What\u2019s happening",
        # 404 / gate page variant
        "Don\u2019t miss what\u2019s happening",
        "Don't miss what's happening",
        "People on X are the first to know",
        "this page doesn\u2019t exist",
        "this page doesn't exist",
        "Try searching for something else",
    ]
    hit_count = sum(1 for m in garbage_markers if m in content)
    return hit_count >= 2


def _fetch_article_body(
    tweet_url: str,
    article_data: dict,
    author: str,
    log_prefix: str = "[Twitter]",
) -> str:
    """Fetch article body via Jina, trying article URL first.

    Returns cleaned article content, or empty string if all attempts fail.
    """
    from feedgrab.fetchers.jina import fetch_via_jina

    # Build URL candidates: article URL first, then status URL
    # Twitter article URL = tweet URL with /status/ replaced by /article/
    urls_to_try = []
    if "/status/" in tweet_url:
        urls_to_try.append(tweet_url.replace("/status/", "/article/"))
    urls_to_try.append(tweet_url)

    for jina_url in urls_to_try:
        for attempt in range(2):
            try:
                jina_data = fetch_via_jina(jina_url)
                jina_content = jina_data.get("content", "")
                if jina_content and len(jina_content.strip()) > 200:
                    if _is_jina_garbage(jina_content):
                        logger.info(
                            f"{log_prefix} Jina 返回页面垃圾，跳过: {jina_url}"
                        )
                        break  # skip to next URL candidate
                    # Normalize nested image links [![alt](img)](link) → ![image](img)
                    jina_content = re.sub(
                        r'\[!\[[^\]]*\]\(([^)]+)\)\]\([^)]+\)',
                        r'![image](\1)',
                        jina_content,
                    )
                    # Fix hollows: Jina markdown drops cashtag/mention links
                    jina_content = _patch_jina_hollows(jina_content, jina_url, log_prefix)
                    return jina_content
                if attempt == 0:
                    logger.info(f"{log_prefix} Jina 内容过短，重试...")
                    time.sleep(2)
            except Exception as je:
                if attempt == 0:
                    logger.info(f"{log_prefix} Jina 失败 ({je})，重试...")
                    time.sleep(2)
                else:
                    logger.warning(f"{log_prefix} Jina 重试失败: {je}")

    logger.warning(f"{log_prefix} 文章正文获取失败，保留原始内容")
    return ""


# Natural break punctuation at end of line (paragraph boundary, not hollow)
_BREAK_END_CHARS = frozenset('.。！？!?：:）)】」》；;')
# Natural break start chars for next line (heading, list, emoji etc.)
_BREAK_START_CHARS = frozenset('#-*>•□■●◆◇▲△▼○①②③④⑤⑥⑦⑧⑨⑩')


def _detect_hollows(md_content: str) -> bool:
    """Quick check: does the markdown content have hollow patterns?

    A hollow is where Jina dropped an inline link (cashtag/mention),
    leaving a line that ends mid-sentence → empty line → continuation.
    """
    lines = md_content.split('\n')
    in_code = False
    for i in range(len(lines) - 2):
        stripped = lines[i].strip()
        if stripped.startswith('```'):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue
        if (lines[i + 1].strip() == ''
                and lines[i + 2].strip()
                and stripped[-1] not in _BREAK_END_CHARS
                and not stripped.endswith(('```', '---'))
                and lines[i + 2].strip()[0] not in _BREAK_START_CHARS
                and not lines[i + 2].strip()[0].isdigit()):
            return True
    return False


def _patch_jina_hollows(md_content: str, jina_url: str, log_prefix: str) -> str:
    """Fix Jina markdown hollows using text-mode content.

    Jina's markdown renderer drops inline link-only elements (cashtags like
    $MODEL, @mentions) from Twitter Articles. The text mode preserves all
    visible text. This function detects hollow patterns in markdown and
    patches them from the text-mode output.
    """
    if not _detect_hollows(md_content):
        return md_content

    from feedgrab.fetchers.jina import fetch_via_jina_text
    text_content = fetch_via_jina_text(jina_url)
    if not text_content:
        return md_content

    logger.info(f"{log_prefix} 检测到 Jina 内容缺失，正在用纯文本模式修补...")

    # Flatten text content for searching (collapse whitespace)
    text_flat = re.sub(r'\s+', ' ', text_content)

    lines = md_content.split('\n')
    result = []
    in_code = False
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith('```'):
            in_code = not in_code

        if in_code or not stripped:
            result.append(lines[i])
            i += 1
            continue

        # Detect and collect a hollow chain:
        # line_A → empty → line_B → empty → line_C ...
        # where each transition looks like a broken sentence (not a natural break)
        chain_lines = [stripped]
        chain_original = [lines[i]]
        j = i + 1
        while j + 1 < len(lines) and lines[j].strip() == '' and lines[j + 1].strip():
            next_stripped = lines[j + 1].strip()
            cur_end = chain_lines[-1][-1]
            is_natural = (
                cur_end in _BREAK_END_CHARS
                or chain_lines[-1].endswith(('```', '---'))
                or next_stripped[0] in _BREAK_START_CHARS
                or next_stripped[0].isdigit()
            )
            if is_natural:
                break
            chain_lines.append(next_stripped)
            chain_original.append(lines[j + 1])
            j += 2

        if len(chain_lines) > 1:
            # Try to find the complete text from text mode
            anchor_before = chain_lines[0][-20:]
            anchor_after = chain_lines[-1][:20:]

            before_pos = text_flat.find(anchor_before)
            if before_pos >= 0:
                search_start = before_pos + len(anchor_before)
                after_pos = text_flat.find(anchor_after, search_start)
                if 0 <= after_pos - search_start < 200:
                    missing = text_flat[search_start:after_pos]
                    # Stitch: first chain line + missing + last chain line
                    patched = chain_original[0].rstrip() + missing + chain_original[-1].lstrip()
                    result.append(patched)
                    i = j  # Skip past the chain
                    continue

        result.append(lines[i])
        i += 1

    return '\n'.join(result)


# ---------------------------------------------------------------------------
# Tweet classification
# ---------------------------------------------------------------------------

def _classify_tweet(tweet_data: dict) -> str:
    """Classify a bookmark tweet for processing strategy.

    Returns:
        'single' — standalone tweet, use data directly
        'thread' — part of a thread, needs fetch_tweet_thread()
        'article' — long-form article, needs Jina body fetch
    """
    # Article check (highest priority)
    article = tweet_data.get("article") or {}
    if article.get("has_content"):
        return "article"

    # Thread check: tweet is a reply in its own conversation
    # conversation_id == own id means it's a root tweet (could still be thread starter)
    # in_reply_to_status_id being empty means it's not a reply
    conv_id = tweet_data.get("conversation_id", "")
    tweet_id = tweet_data.get("id", "")
    in_reply_to = tweet_data.get("in_reply_to_status_id", "")

    if conv_id and tweet_id and conv_id != tweet_id:
        return "thread"
    if in_reply_to:
        return "thread"

    return "single"


def _build_single_tweet_data(tweet_data: dict, url: str) -> dict:
    """Build the standard data dict for a single (non-thread) tweet.

    Output format matches _fetch_via_graphql() in twitter.py.
    """
    from feedgrab.fetchers.twitter import _clean_title

    article = tweet_data.get("article") or {}
    title = article.get("title") or tweet_data.get("text", "")
    title = _clean_title(title)

    return {
        "text": tweet_data.get("text", ""),
        "author": f"@{tweet_data.get('author', '')}",
        "author_name": tweet_data.get("author_name", ""),
        "url": url,
        "title": title,
        "platform": "twitter",
        "thread_tweets": [tweet_data],
        "has_thread": False,
        "article_data": article,
        "likes": tweet_data.get("likes", 0),
        "retweets": tweet_data.get("retweets", 0),
        "replies": tweet_data.get("replies", 0),
        "bookmarks": tweet_data.get("bookmarks", 0),
        "views": tweet_data.get("views", "0"),
        "created_at": tweet_data.get("created_at", ""),
        "images": tweet_data.get("images", []),
        "videos": tweet_data.get("videos", []),
        "hashtags": tweet_data.get("hashtags", []),
        # Extended metadata (matches _fetch_via_graphql output)
        "quote_count": tweet_data.get("quote_count", 0),
        "lang": tweet_data.get("lang", ""),
        "source_app": tweet_data.get("source_app", ""),
        "possibly_sensitive": tweet_data.get("possibly_sensitive", False),
        "is_blue_verified": tweet_data.get("is_blue_verified", False),
        "followers_count": tweet_data.get("followers_count", 0),
        "statuses_count": tweet_data.get("statuses_count", 0),
        "listed_count": tweet_data.get("listed_count", 0),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fetch_bookmarks(bookmark_url: str, cookies: dict) -> dict:
    """
    Batch-fetch all bookmarked tweets and save each as Markdown.

    Args:
        bookmark_url: Bookmark URL (x.com/i/bookmarks or x.com/i/bookmarks/{id})
        cookies: dict with 'auth_token' and 'ct0'

    Returns:
        dict with: total, fetched, skipped, failed, bookmark_list_path
    """
    from feedgrab.fetchers.twitter import _fetch_via_graphql, _clean_title
    from feedgrab.fetchers.twitter_fxtwitter import reset_circuit_breaker
    from feedgrab.fetchers.jina import fetch_via_jina
    from feedgrab.schema import from_twitter
    from feedgrab.utils.storage import save_to_markdown

    reset_circuit_breaker()

    parsed = _parse_bookmark_url(bookmark_url)
    folder_id = parsed.get("folder_id", "")
    folder_name = ""

    if parsed["type"] == "folder":
        folder_name = _resolve_folder_name(folder_id, cookies)
        logger.info(f"[Bookmarks] 书签文件夹: {folder_name} (ID: {folder_id})")

    logger.info("[Bookmarks] 开始批量抓取书签...")

    # Subfolder path for save_to_markdown (e.g., "bookmarks/OpenClaw")
    subfolder = f"bookmarks/{_sanitize_folder_name(folder_name)}" if folder_name else "bookmarks/all"

    # Load dedup index
    saved_ids = load_index()
    initial_count = len(saved_ids)
    logger.info(f"[Bookmarks] 已有 {initial_count} 条推文索引")

    # Collect all bookmark entries via pagination
    all_tweet_entries = []
    cursor = None
    max_pages = x_bookmark_max_pages()
    delay = x_bookmark_delay()

    for page in range(max_pages):
        logger.info(f"[Bookmarks] 获取第 {page + 1} 页...")

        from feedgrab.fetchers.twitter_cookies import fetch_with_cookie_rotation
        # primary_only: bookmarks are account-private, so a spare account can
        # never serve this request — rotating past the primary only wastes
        # requests and hides the real "please re-login" cause.
        if folder_id:
            response, rotated_cookies = fetch_with_cookie_rotation(
                fetch_bookmark_folder_page, folder_id,
                label="Bookmarks", cursor=cursor, primary_only=True,
            )
        else:
            response, rotated_cookies = fetch_with_cookie_rotation(
                fetch_bookmarks_page,
                label="Bookmarks", cursor=cursor, primary_only=True,
            )
        if rotated_cookies:
            cookies = rotated_cookies
        if not response:
            # A dead first page means nothing was fetched at all — that is a hard
            # failure, not "read to the end". Returning total=0 here is what made
            # an expired login look like a successful empty run.
            if not all_tweet_entries:
                raise RuntimeError(
                    "书签抓取失败：主账号请求无响应（常见原因是登录态过期，"
                    "GraphQL 返回 401/403）。书签是账号私有数据，不会轮换备用号，"
                    "请重新登录该账号：feedgrab login twitter"
                )
            logger.error(
                f"[Bookmarks] >>> 第 {page + 1} 页主账号请求失败 <<< "
                f"已抓取 {len(all_tweet_entries)} 条，停止分页"
                f"（书签为账号私有数据，不会轮换备用号）"
            )
            break

        entries, cursors = parse_bookmark_entries(response)
        if not entries:
            # "拿到响应" != "拿到内容"：权限等错误走 HTTP 200 + errors 返回，
            # 不能与「正常读完」合并，否则静默上报成功（总数 0）
            err_summary = _graphql_error_summary(response)
            if err_summary:
                hint = ""
                if "not authorized" in err_summary.lower():
                    hint = (
                        " 提示：书签是账号私有数据，轮换到其他账号读不到同一份书签；"
                        "请确认当前生效账号正是收藏这些推文的账号"
                        "（书签文件夹还需该账号具备 X Premium 权限）。"
                    )
                if all_tweet_entries:
                    logger.error(
                        f"[Bookmarks] >>> 第 {page + 1} 页返回错误 <<< {err_summary}"
                        f"（已抓取 {len(all_tweet_entries)} 条，停止分页）{hint}"
                    )
                    break
                raise RuntimeError(f"书签抓取失败：{err_summary}{hint}")
            logger.info("[Bookmarks] 没有更多书签条目")
            break

        all_tweet_entries.extend(entries)
        logger.info(f"[Bookmarks] 第 {page + 1} 页获取 {len(entries)} 条，累计 {len(all_tweet_entries)} 条")

        # Next page
        cursor = cursors.get("bottom")
        if not cursor:
            logger.info("[Bookmarks] 没有下一页游标，分页完成")
            break

    total = len(all_tweet_entries)
    logger.info(f"[Bookmarks] 共获取 {total} 条书签条目")

    if total == 0:
        return {
            "total": 0,
            "fetched": 0,
            "skipped": 0,
            "failed": 0,
            "bookmark_list_path": "",
        }

    # Process each bookmark tweet
    fetched = 0
    skipped = 0
    failed = 0
    bookmark_list = []
    processed_ids = set()  # in-batch dedup

    for idx, entry in enumerate(all_tweet_entries):
        tweet_data = extract_tweet_data(entry)
        if not tweet_data:
            logger.debug(f"[Bookmarks] [{idx + 1}/{total}] 无法解析条目，跳过")
            failed += 1
            bookmark_list.append({
                "url": "",
                "tweet_id": "",
                "author": "",
                "author_name": "",
                "title": "",
                "status": "failed",
                "error": "无法解析推文数据",
            })
            continue

        tweet_id = tweet_data.get("id", "")
        author = tweet_data.get("author", "")
        author_name = tweet_data.get("author_name", "")
        tweet_url = f"https://x.com/{author}/status/{tweet_id}"
        item_id = item_id_from_url(tweet_url)
        title_preview = _clean_title(tweet_data.get("text", "")[:80])

        # Parse published date
        published = ""
        if tweet_data.get("created_at"):
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(tweet_data["created_at"])
                published = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        # In-batch dedup
        if tweet_id in processed_ids:
            logger.debug(f"[Bookmarks] [{idx + 1}/{total}] 批内重复: {tweet_id}")
            skipped += 1
            bookmark_list.append({
                "url": tweet_url,
                "tweet_id": tweet_id,
                "item_id": item_id,
                "author": f"@{author}",
                "author_name": author_name,
                "published": published,
                "title": title_preview,
                "status": "skipped",
                "error": "批内重复",
            })
            continue

        processed_ids.add(tweet_id)

        # File-level dedup via index (skip when FORCE_REFETCH=true)
        if has_item(item_id, saved_ids) and not force_refetch():
            logger.debug(f"[Bookmarks] [{idx + 1}/{total}] 已存在: @{author} - {title_preview[:30]}")
            skipped += 1
            bookmark_list.append({
                "url": tweet_url,
                "tweet_id": tweet_id,
                "item_id": item_id,
                "author": f"@{author}",
                "author_name": author_name,
                "published": published,
                "title": title_preview,
                "status": "skipped",
                "error": "",
            })
            continue

        # Classify and process
        tweet_type = _classify_tweet(tweet_data)
        error_msg = ""

        try:
            if tweet_type == "single":
                # Direct: use extracted data as-is
                data = _build_single_tweet_data(tweet_data, tweet_url)
            elif tweet_type == "thread":
                # Need full thread reconstruction via GraphQL
                logger.info(f"[Bookmarks] [{idx + 1}/{total}] 线程推文，获取完整线程: @{author}")
                try:
                    data = await _fetch_via_graphql(tweet_url, tweet_id)
                except Exception as thread_err:
                    logger.warning(f"[Bookmarks] 线程获取失败 ({thread_err})，退化为单条保存")
                    data = _build_single_tweet_data(tweet_data, tweet_url)
                time.sleep(delay)
            elif tweet_type == "article":
                # Article: prefer GraphQL content_state body, fallback to Jina
                data = _build_single_tweet_data(tweet_data, tweet_url)
                article = tweet_data.get("article") or {}
                article_body = article.get("body", "")
                if article_body and len(article_body.strip()) > 200:
                    logger.info(f"[Bookmarks] [{idx + 1}/{total}] Article — GraphQL content_state: @{author}")
                    data["text"] = article_body
                    if data.get("thread_tweets"):
                        data["thread_tweets"][0]["text"] = article_body
                else:
                    logger.info(f"[Bookmarks] [{idx + 1}/{total}] 长文章，Jina 获取正文: @{author}")
                    jina_content = _fetch_article_body(
                        tweet_url, article, author, "[Bookmarks]"
                    )
                    if jina_content:
                        data["text"] = jina_content
                        if data.get("thread_tweets"):
                            data["thread_tweets"][0]["text"] = jina_content
                time.sleep(delay)
            else:
                data = _build_single_tweet_data(tweet_data, tweet_url)

            # Convert to UnifiedContent and save
            content = from_twitter(data)
            if subfolder:
                content.category = subfolder
            saved_path = save_to_markdown(content)

            # Media download
            if saved_path:
                from feedgrab.config import x_download_media
                if x_download_media():
                    from feedgrab.utils.media import download_media
                    download_media(
                        saved_path,
                        content.extra.get("images", []),
                        content.extra.get("videos", []),
                        content.id,
                        platform="twitter",
                        context={
                            "tweet_id": content.id,
                            "url": content.url,
                            "screen_name": (content.source_name or "").lstrip("@"),
                            "user_id": content.extra.get("user_id", ""),
                            "created_at": content.extra.get("created_at", ""),
                        },
                    )

            # Update index
            add_item(item_id, tweet_url, saved_ids)
            fetched += 1

            bookmark_list.append({
                "url": tweet_url,
                "tweet_id": tweet_id,
                "item_id": item_id,
                "author": f"@{author}",
                "author_name": author_name,
                "published": published,
                "title": title_preview,
                "status": "fetched",
                "error": "",
            })

            # Progress log every 10 items
            if (idx + 1) % 10 == 0 or idx + 1 == total:
                logger.info(
                    f"[Bookmarks] 进度 [{idx + 1}/{total}] "
                    f"成功:{fetched} 跳过:{skipped} 失败:{failed}"
                )

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"[Bookmarks] [{idx + 1}/{total}] 失败: @{author} - {error_msg[:80]}")
            failed += 1
            bookmark_list.append({
                "url": tweet_url,
                "tweet_id": tweet_id,
                "item_id": item_id,
                "author": f"@{author}",
                "author_name": author_name,
                "published": published,
                "title": title_preview,
                "status": "failed",
                "error": error_msg[:200],
            })

    # Persist dedup index
    save_index(saved_ids)
    logger.info(f"[Bookmarks] 索引更新: {initial_count} -> {len(saved_ids)} 条")

    # Save bookmark URL list
    list_path = _save_bookmark_list(bookmark_list, folder_id, folder_name)

    logger.info(
        f"[Bookmarks] 批量抓取完成: "
        f"总计 {total}, 成功 {fetched}, 跳过 {skipped}, 失败 {failed}"
    )

    return {
        "total": total,
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "bookmark_list_path": str(list_path),
    }
