# -*- coding: utf-8 -*-
"""
Twitter/X keyword search — discover tweets by keyword with engagement ranking.

Usage:
    feedgrab x-so "openclaw"
    feedgrab x-so openclaw --days 3 --lang en --min-faves 100 --sort top
    feedgrab x-so '"openclaw" lang:zh since:2026-03-06 min_faves:50' --raw

Architecture:
    1. Build Twitter search query from keyword + config defaults + CLI overrides
    2. Tier 0: SearchTimeline GraphQL endpoint (no browser needed)
    3. Tier 1: CDP direct connect (reuse running Chrome with Twitter session)
    4. Tier 2: Playwright launch (stealth browser with saved session)
    5. extract_tweet_data() on each entry → views-ranked summary table
    6. Optionally save individual tweet .md files (X_SEARCH_SAVE_TWEETS=true)

Output:
    X/search/{days}day_{sort_label}/{keyword}_{date}.md    ← summary table (always)
    X/search/{days}day_{sort_label}/{keyword}_{date}.csv   ← CSV table (always)
    X/search/{days}day_{sort_label}/{keyword}/{tweet}.md   ← individual tweets (optional)
"""

import asyncio
import csv
import os
import random
import re
import time
import urllib.parse
from datetime import date, timedelta, datetime
from pathlib import Path
from loguru import logger
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable

from feedgrab.config import (
    parse_twitter_date_local,
    get_session_dir,
    chrome_cdp_port,
    x_search_browser_fallback,
)
from feedgrab.fetchers.twitter_graphql import (
    extract_tweet_data,
    fetch_search_timeline_page,
    parse_search_entries,
)
from feedgrab.fetchers.twitter_cookies import load_twitter_cookies
from feedgrab.fetchers.twitter import _clean_title


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------

def _strip_wrapping_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1].strip()
    return text


def _quote_search_term(text: str) -> str:
    escaped = text.strip().replace('"', '\\"')
    return f'"{escaped}"'


def _case_variants(keyword: str) -> List[str]:
    """Return conservative case variants for ASCII keywords."""
    base = _strip_wrapping_quotes(keyword)
    if not re.search(r"[A-Za-z]", base):
        return [base]

    variants = [base, base.capitalize(), base.lower()]
    result = []
    seen = set()
    for variant in variants:
        if variant and variant not in seen:
            seen.add(variant)
            result.append(variant)
    return result


def _keyword_expression(keyword: str) -> str:
    variants = [_quote_search_term(variant) for variant in _case_variants(keyword)]
    if len(variants) == 1:
        return variants[0]
    return f"({' OR '.join(variants)})"


def _expand_search_langs(lang: str, raw: bool = False) -> List[str]:
    if raw:
        return [""]
    normalized = (lang or "").strip().lower()
    if normalized in {"zh+zxx", "zh_zxx", "zh,zxx"}:
        return ["zh", "zxx"]
    return [normalized]


def _expand_search_sorts(sort: str) -> List[str]:
    normalized = (sort or "live").strip().lower()
    if normalized == "all":
        return ["live", "top"]
    return [normalized if normalized in {"live", "top"} else "live"]


def _sort_label(sort: str) -> str:
    return {"live": "new", "top": "hot", "all": "all"}.get(sort, "new")


def _sort_label_zh(sort: str) -> str:
    return {"live": "最新", "top": "热门", "all": "全量"}.get(sort, "最新")


def build_search_query(
    keyword: str,
    lang: str = "",
    days: int = 1,
    min_faves: int = 0,
    min_retweets: int = 0,
    exclude_retweets: bool = True,
    raw: bool = False,
) -> str:
    """Build Twitter search query string from keyword and filter parameters.

    When raw=False, auto-wraps keyword in quotes for exact phrase match
    and appends configured operators (lang, since, min_faves, etc.).
    When raw=True, uses keyword as-is (user controls full query).
    """
    if raw:
        return keyword

    parts = [_keyword_expression(keyword)]
    if lang:
        parts.append(f"lang:{lang}")
    if days > 0:
        since_date = (date.today() - timedelta(days=days)).isoformat()
        parts.append(f"since:{since_date}")
    if min_faves > 0:
        parts.append(f"min_faves:{min_faves}")
    if min_retweets > 0:
        parts.append(f"min_retweets:{min_retweets}")
    if exclude_retweets:
        parts.append("-is:retweet")

    return " ".join(parts)


def build_search_url(query: str, sort: str = "live") -> str:
    """Build Twitter search URL with sort parameter.

    sort='live' → Latest tab (&f=live)
    sort='top'  → Top tab (no &f= parameter, X.com default)
    """
    encoded = urllib.parse.quote(query)
    url = f"https://x.com/search?q={encoded}&src=typed_query"
    if sort == "live":
        url += "&f=live"
    return url


# ---------------------------------------------------------------------------
# Browser fallback: CDP direct connect → Playwright launch
# ---------------------------------------------------------------------------

async def _connect_twitter_cdp_for_search() -> Tuple[Any, Optional[Callable[[], Awaitable[None]]]]:
    """Try CDP connection to a running Chrome with Twitter logged in.

    Returns (page, cleanup_fn) on success, or (None, None) on failure.
    cleanup_fn disconnects CDP without killing the user's Chrome.
    """
    port = chrome_cdp_port()
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None, None

    pw = await async_playwright().start()
    try:
        ws_url = f"ws://127.0.0.1:{port}/devtools/browser"
        browser = await pw.chromium.connect_over_cdp(ws_url)

        twitter_domains = (".x.com", ".twitter.com")
        target_ctx = None
        for ctx in browser.contexts:
            cookies = await ctx.cookies()
            if any(
                any(c.get("domain", "").endswith(d) for d in twitter_domains)
                for c in cookies
            ):
                target_ctx = ctx
                break

        if not target_ctx:
            logger.info("[X-SO CDP] No context with Twitter cookies found")
            await browser.close()
            await pw.stop()
            return None, None

        page = await target_ctx.new_page()
        logger.info("[X-SO CDP] Connected to Chrome, new tab created")

        async def _cleanup():
            try:
                await page.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await pw.stop()
            except Exception:
                pass

        return page, _cleanup

    except Exception as e:
        logger.info(f"[X-SO CDP] Connection failed: {e}")
        try:
            await pw.stop()
        except Exception:
            pass
        return None, None


async def _launch_browser_for_search(
    session_path: str,
) -> Tuple[Any, Optional[Callable[[], Awaitable[None]]]]:
    """Launch a stealth browser with saved Twitter session.

    Returns (page, cleanup_fn) on success, or (None, None) on failure.
    """
    if not Path(session_path).exists():
        logger.warning(f"[X-SO Browser] Session not found: {session_path}")
        return None, None

    try:
        from feedgrab.fetchers.browser import (
            get_async_playwright,
            stealth_launch,
            get_stealth_context_options,
            setup_resource_blocking,
        )
    except ImportError:
        return None, None

    try:
        _pw_cm = get_async_playwright()
        pw = await _pw_cm.__aenter__()
        browser = await stealth_launch(pw, headless=False)
        ctx_opts = get_stealth_context_options(storage_state=session_path)
        context = await browser.new_context(**ctx_opts)
        await setup_resource_blocking(context)
        page = await context.new_page()

        # Warm up session (activate cookies)
        logger.info("[X-SO Browser] Warming up session...")
        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)
        logger.info("[X-SO Browser] Session ready")

        async def _cleanup():
            try:
                await page.close()
            except Exception:
                pass
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await _pw_cm.__aexit__(None, None, None)
            except Exception:
                pass

        return page, _cleanup

    except Exception as e:
        logger.warning(f"[X-SO Browser] Launch failed: {e}")
        return None, None


async def _search_via_browser(
    query: str, sort: str, max_results: int, session_path: str,
) -> List[dict]:
    """Browser-based search fallback: Tier 1 CDP → Tier 2 Playwright launch.

    Reuses SearchResponseCollector + _scroll_and_collect_search from
    twitter_search_tweets.py. Data format is fully compatible (same
    extract_tweet_data on same GraphQL response structure).
    """
    from feedgrab.fetchers.twitter_search_tweets import (
        SearchResponseCollector, _scroll_and_collect_search,
    )

    search_url = build_search_url(query, sort=sort)

    # Tier 1: CDP direct connect
    page, cleanup = await _connect_twitter_cdp_for_search()
    tier = "CDP"

    if not page:
        # Tier 2: Playwright launch
        page, cleanup = await _launch_browser_for_search(session_path)
        tier = "Browser"

    if not page:
        logger.warning("[X-SO] 所有浏览器兜底层都失败")
        return []

    collector = SearchResponseCollector()
    try:
        page.on("response", collector.handle_response)
        logger.info(f"[X-SO {tier}] Navigating to search page...")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(3)  # Wait for initial GraphQL responses

        max_scrolls = min(max_results // 3 + 10, 60)
        await _scroll_and_collect_search(
            page, collector, max_scrolls=max_scrolls,
            scroll_delay_min=1.5, scroll_delay_max=3.0,
        )

        logger.info(f"[X-SO {tier}] Collected {len(collector.entries)} raw entries")

        # Parse entries (same extract_tweet_data, fully compatible)
        tweets: List[dict] = []
        seen: set = set()
        for entry in collector.entries:
            td = extract_tweet_data(entry)
            if td and td.get("id") not in seen:
                seen.add(td["id"])
                tweets.append(td)

        return tweets[:max_results]

    except Exception as e:
        logger.warning(f"[X-SO {tier}] Search failed: {e}")
        return []
    finally:
        if cleanup:
            await cleanup()


# ---------------------------------------------------------------------------
# Engagement scoring
# ---------------------------------------------------------------------------

def _engagement_score(tweet_data: dict) -> int:
    """Calculate engagement score for sorting.

    Formula: likes*3 + retweets*2 + bookmarks*2 + replies
    """
    likes = int(tweet_data.get("likes", 0) or 0)
    retweets = int(tweet_data.get("retweets", 0) or 0)
    bookmarks = int(tweet_data.get("bookmarks", 0) or 0)
    replies = int(tweet_data.get("replies", 0) or 0)
    return likes * 3 + retweets * 2 + bookmarks * 2 + replies


# ---------------------------------------------------------------------------
# Summary table generation
# ---------------------------------------------------------------------------

def _sanitize_for_dirname(name: str) -> str:
    """Clean a string for use as a directory/file name."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name)
    return name.strip('. ')[:50]


def _resolve_output_base() -> Path:
    """Resolve the base output directory (OBSIDIAN_VAULT > OUTPUT_DIR > output)."""
    vault = os.getenv("OBSIDIAN_VAULT", "").strip()
    output_dir = os.getenv("OUTPUT_DIR", "").strip()
    return Path(vault or output_dir or "output")


def _summary_text(tweet_data: dict, max_len: int) -> str:
    """Choose display text for x-so summary tables."""
    article = tweet_data.get("article") or {}
    source = ""
    if isinstance(article, dict):
        source = (article.get("title") or "").strip()
        if not source:
            source = (article.get("body") or "").strip()
    if not source:
        source = tweet_data.get("text", "") or ""
    return _clean_title(str(source), max_len=max_len)


def _generate_summary_table(
    keyword: str,
    query: str,
    sort: str,
    days: int,
    tweets: List[dict],
    output_path: Path,
    show_keyword: bool = False,
) -> None:
    """Generate summary Markdown table + CSV, sorted by views.

    MD: 内容摘要 is a hyperlink (no separate link column).
    CSV: has explicit link column with plain URL.

    Args:
        show_keyword: If True, add a "关键词" column (used in merged multi-keyword mode).
    """
    sort_label_zh = _sort_label_zh(sort)
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Sort by views descending (ensures correct order in merge mode)
    tweets.sort(key=lambda td: int(td.get("views", 0) or 0), reverse=True)

    # --- Markdown ---
    lines = [
        "---",
        f'title: "X 搜索：{keyword}"',
        f"query: '{query}'",
        f'search_tab: "{sort_label_zh}"',
        f"total: {len(tweets)}",
        f"created: {date_str}",
        "cssclasses: wide",
        "---",
        "",
    ]

    if not tweets:
        lines.append("*未找到结果。*")
    else:
        if show_keyword:
            lines.append(
                "| # | 关键词 | 作者 | 内容摘要 | 日期 | 点赞 | 转帖 | 回复 | 查看 | 收藏 |"
            )
            lines.append(
                "|:---:|------|------|----------|:---:|:---:|:---:|:---:|:---:|:------:|"
            )
        else:
            lines.append(
                "| # | 作者👨🏻‍💻 | 内容摘要💻 | 日期🗓 | 点赞👍 | 转帖🔄 | 回复💬 | 查看👁 | 收藏📌 |"
            )
            lines.append(
                "|:---:|------|----------|:---:|:---:|:---:|:---:|:---:|:------:|"
            )

        for i, td in enumerate(tweets, 1):
            # Prefer display name; fall back to @handle
            author_name = td.get("author_name", "")
            handle = td.get("author", "")
            author = author_name if author_name else (f"@{handle}" if handle else "")
            if td.get("is_blue_verified"):
                author = f"\u2705{author}"
            # Escape pipe and newlines — pipe breaks table columns
            author = author.replace("|", "\\|").replace("\n", " ").replace("\r", "")
            summary = _summary_text(td, max_len=40)
            summary = summary.replace("|", "\\|")
            # Escape brackets for markdown link
            summary = summary.replace("[", "\\[").replace("]", "\\]")
            likes = int(td.get("likes", 0) or 0)
            retweets = int(td.get("retweets", 0) or 0)
            replies_count = int(td.get("replies", 0) or 0)
            views = int(td.get("views", 0) or 0)
            bookmarks = int(td.get("bookmarks", 0) or 0)
            created_at = td.get("created_at", "")
            date_short = parse_twitter_date_local(created_at, "%m-%d")

            tweet_id = td.get("id", "")
            tweet_author = td.get("author", "")
            tweet_url = f"https://x.com/{tweet_author}/status/{tweet_id}"

            # Summary as hyperlink (no separate link column)
            summary_link = f"[{summary}]({tweet_url})"

            if show_keyword:
                kw = td.get("_keyword", "").replace("|", "\\|")
                lines.append(
                    f"| {i} | {kw} | {author} | {summary_link} "
                    f"| {date_short} | {likes} | {retweets} | {replies_count} "
                    f"| {views} | {bookmarks} |"
                )
            else:
                lines.append(
                    f"| {i} | {author} | {summary_link} "
                    f"| {date_short} | {likes} | {retweets} | {replies_count} "
                    f"| {views} | {bookmarks} |"
                )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"[X-SO] Summary table saved: {output_path}")

    # --- CSV ---
    csv_path = output_path.with_suffix(".csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if show_keyword:
            writer.writerow([
                "#", "关键词", "作者", "内容摘要", "日期", "点赞", "转帖",
                "回复", "查看", "收藏", "链接",
            ])
        else:
            writer.writerow([
                "#", "作者", "内容摘要", "日期", "点赞", "转帖",
                "回复", "查看", "收藏", "链接",
            ])
        for i, td in enumerate(tweets, 1):
            author_name = td.get("author_name", "")
            handle = td.get("author", "")
            author = author_name if author_name else (f"@{handle}" if handle else "")
            if td.get("is_blue_verified"):
                author = f"\u2705{author}"
            summary = _summary_text(td, max_len=80)
            likes = int(td.get("likes", 0) or 0)
            retweets = int(td.get("retweets", 0) or 0)
            replies_count = int(td.get("replies", 0) or 0)
            views = int(td.get("views", 0) or 0)
            bookmarks = int(td.get("bookmarks", 0) or 0)
            created_at = td.get("created_at", "")
            date_short = parse_twitter_date_local(created_at, "%m-%d")
            tweet_id = td.get("id", "")
            tweet_author = td.get("author", "")
            tweet_url = f"https://x.com/{tweet_author}/status/{tweet_id}"
            row = [i, author, summary, date_short, likes, retweets,
                   replies_count, views, bookmarks, tweet_url]
            if show_keyword:
                row.insert(1, td.get("_keyword", ""))
            writer.writerow(row)
    logger.info(f"[X-SO] CSV table saved: {csv_path}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def search_twitter_keyword(
    keyword: str,
    lang: str = "",
    days: int = 1,
    min_faves: int = 0,
    min_retweets: int = 0,
    sort: str = "live",
    exclude_retweets: bool = True,
    max_results: int = 100,
    scroll_delay: float = 2.0,
    save_tweets: bool = False,
    raw: bool = False,
    skip_summary: bool = False,
) -> dict:
    """Search Twitter for tweets matching a keyword and generate engagement-ranked output.

    Tier 0: SearchTimeline GraphQL (fastest, <2s/page)
    Tier 1: CDP direct connect (reuse running Chrome)
    Tier 2: Playwright launch (stealth browser with saved session)

    Returns:
        dict with: total, saved, query, output_path
    """
    search_specs: List[Tuple[str, str]] = []
    for sort_variant in _expand_search_sorts(sort):
        for lang_variant in _expand_search_langs(lang, raw=raw):
            query = build_search_query(
                keyword, lang=lang_variant, days=days, min_faves=min_faves,
                min_retweets=min_retweets, exclude_retweets=exclude_retweets,
                raw=raw,
            )
            if (sort_variant, query) not in search_specs:
                search_specs.append((sort_variant, query))

    query_label = " | ".join(dict.fromkeys(query for _, query in search_specs))
    logger.info(f"[X-SO] Query: {query_label}")
    for sort_variant, query in search_specs:
        logger.info(f"[X-SO] URL: {build_search_url(query, sort=sort_variant)}")

    # Load cookies (supports multi-account rotation)
    cookies = load_twitter_cookies()
    if not cookies:
        raise RuntimeError(
            "未找到 Twitter Cookie，请先运行: feedgrab login twitter"
        )

    # Resolve output paths
    base_dir = _resolve_output_base()
    sort_label = _sort_label(sort)
    effective_days = days if not raw else 0
    subdir = f"search/{effective_days}day_{sort_label}"
    safe_keyword = _sanitize_for_dirname(keyword)
    date_str = datetime.now().strftime("%Y-%m-%d")
    summary_dir = base_dir / "X" / subdir
    summary_path = summary_dir / f"{safe_keyword}_{date_str}.md"

    # Session path for browser fallback
    session_path = str(get_session_dir() / "twitter.json")

    # --- Tier 0: GraphQL pagination loop ---
    max_pages = max_results // 20 + 5  # ~20 entries per page
    tweets = []
    seen_ids: set[str] = set()
    total_raw_entries = 0

    for sort_variant, query in search_specs:
        product = "Latest" if sort_variant == "live" else "Top"
        all_entries = []
        cursor = None
        graphql_failed = False

        for page_num in range(max_pages):
            from feedgrab.fetchers.twitter_cookies import (
                fetch_with_cookie_rotation,
                count_total_accounts,
            )
            response, rotated_cookies = fetch_with_cookie_rotation(
                fetch_search_timeline_page,
                label="X-SO",
                raw_query=query,
                cursor=cursor,
                count=20,
                product=product,
            )
            if rotated_cookies:
                cookies = rotated_cookies
            if not response:
                total_accounts = count_total_accounts()
                logger.warning(
                    f"[X-SO] >>> {product} 第 {page_num + 1} 页所有 {total_accounts} 个账号均失败 <<< "
                    f"累计 {len(all_entries)} 条，停止分页"
                )
                graphql_failed = True
                break

            entries, cursors = parse_search_entries(response)
            if not entries:
                logger.info(f"[X-SO] {product} no more entries at page {page_num + 1}")
                break

            all_entries.extend(entries)
            logger.info(
                f"[X-SO] {product} Page {page_num + 1}: +{len(entries)} entries "
                f"(total: {len(all_entries)})"
            )

            if len(all_entries) >= max_results:
                break

            cursor = cursors.get("bottom")
            if not cursor:
                break

        total_raw_entries += len(all_entries)

        if not all_entries and graphql_failed and x_search_browser_fallback():
            logger.info(f"[X-SO] {product} GraphQL failed, trying browser fallback...")
            browser_tweets = await _search_via_browser(query, sort_variant, max_results, session_path)
            if browser_tweets:
                logger.info(f"[X-SO] {product} browser fallback: {len(browser_tweets)} tweets")
            for td in browser_tweets:
                tid = td.get("id", "")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    tweets.append(td)
            continue

        for entry in all_entries:
            td = extract_tweet_data(entry)
            if not td:
                continue
            tid = td.get("id", "")
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            tweets.append(td)

    logger.info(f"[X-SO] Total collected: {total_raw_entries} raw entries")

    # Sort by views (descending)
    tweets.sort(key=lambda td: int(td.get("views", 0) or 0), reverse=True)

    # Truncate after merging and ranking, so Top/Article supplements can surface.
    tweets = tweets[:max_results]
    logger.info(f"[X-SO] Extracted {len(tweets)} tweets")

    # Generate summary table (skip in merge mode)
    if not skip_summary:
        _generate_summary_table(keyword, query_label, sort, effective_days, tweets, summary_path)

    # Optional: save individual tweets
    saved = 0
    if save_tweets and tweets:
        from feedgrab.fetchers.twitter_bookmarks import (
            _classify_tweet,
            _build_single_tweet_data,
        )
        from feedgrab.schema import from_twitter
        from feedgrab.utils.storage import save_to_markdown
        from feedgrab.utils.dedup import (
            load_index, save_index, has_item, add_item, item_id_from_url,
        )

        dedup_index = load_index(platform="X")
        tweet_subdir = f"{subdir}/{safe_keyword}"

        for td in tweets:
            tweet_id = td.get("id", "")
            author = td.get("author", "")
            tweet_url = f"https://x.com/{author}/status/{tweet_id}"
            item_id = item_id_from_url(tweet_url)

            if has_item(item_id, dedup_index):
                continue

            try:
                data = _build_single_tweet_data(td, tweet_url)
                content = from_twitter(data)
                content.category = tweet_subdir
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

                add_item(item_id, tweet_url, dedup_index)
                saved += 1
            except Exception as e:
                logger.warning(f"[X-SO] Failed to save tweet {tweet_id}: {e}")

        save_index(dedup_index, platform="X")
        logger.info(f"[X-SO] Saved {saved} individual tweet files")

    return {
        "total": len(tweets),
        "saved": saved,
        "query": query_label,
        "output_path": str(summary_path),
        "csv_path": str(summary_path.with_suffix(".csv")),
        "tweets": tweets,
    }
