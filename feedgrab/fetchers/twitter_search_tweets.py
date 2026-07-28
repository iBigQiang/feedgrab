# -*- coding: utf-8 -*-
"""
Twitter/X Search supplementary fetcher — fill historical gaps via browser search.

When UserTweets API hits its ~800 tweet server-side limit, this module
automatically picks up where it left off, using Playwright browser to perform
monthly date-chunked searches: ``from:username since:YYYY-MM-DD until:YYYY-MM-DD``

Architecture:
    1. Launch Playwright + load twitter session (sessions/twitter.json)
    2. Register page.on("response") handler to capture SearchTimeline GraphQL data
    3. Navigate to x.com/search?q=... for each monthly chunk
    4. Auto-scroll to load more results → triggers more GraphQL requests
    5. Parse captured tweet data → dedup → save Markdown

Key insight: Using Playwright's response event (Python-level) instead of JS
injection means the interceptor survives page navigations and captures ALL
responses including the initial page load.

Design:
    - Monthly chunks from newest (earliest_tweet_date) to oldest (since_date)
    - Shared dedup index with UserTweets (in-place mutation of saved_ids)
    - Skip replies to other users (only save root tweets & self-threads)
    - Early termination after 3 consecutive empty months
    - Failure-safe: exceptions don't affect UserTweets results
"""

import asyncio
import json
import random
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from loguru import logger
from typing import Dict, Any, List, Optional

from feedgrab.config import (
    x_search_max_pages_per_chunk,
    x_user_tweet_delay,
    get_session_dir,
)
from feedgrab.fetchers.twitter_graphql import extract_tweet_data
from feedgrab.fetchers.twitter_bookmarks import (
    _classify_tweet,
    _build_single_tweet_data,
    _fetch_article_body,
)
from feedgrab.utils.dedup import (
    has_item,
    add_item,
    item_id_from_url,
)


# ---------------------------------------------------------------------------
# Monthly chunk generation (pure stdlib, no python-dateutil)
# ---------------------------------------------------------------------------

def _generate_monthly_chunks(earliest_date: str, since_date: str) -> list:
    """Generate monthly (since, until) chunks from newest to oldest.

    Args:
        earliest_date: The earliest date UserTweets reached (e.g. "2025-12-08").
                       Search starts from the 1st of this month.
        since_date:    The target start date from config (e.g. "2025-01-01").

    Returns:
        List of (since_str, until_str) tuples, newest first.
        Example: [("2025-11-01","2025-12-01"), ("2025-10-01","2025-11-01"), ...]
    """
    earliest_dt = datetime.strptime(earliest_date[:10], "%Y-%m-%d")
    since_dt = datetime.strptime(since_date[:10], "%Y-%m-%d")

    until_year, until_month = earliest_dt.year, earliest_dt.month

    chunks = []
    while True:
        since_year, since_month = until_year, until_month
        since_month -= 1
        if since_month < 1:
            since_month = 12
            since_year -= 1

        chunk_since = f"{since_year:04d}-{since_month:02d}-01"
        chunk_until = f"{until_year:04d}-{until_month:02d}-01"

        chunks.append((chunk_since, chunk_until))

        until_year, until_month = since_year, since_month

        since_first = datetime(since_year, since_month, 1)
        if since_first <= since_dt:
            break

    return chunks


# ---------------------------------------------------------------------------
# Response interceptor (Python-level, survives page navigations)
# ---------------------------------------------------------------------------

class SearchResponseCollector:
    """Collects tweet entries from SearchTimeline GraphQL responses.

    Registered via page.on("response", collector.handle_response) — works at
    the Playwright network layer, so it captures responses even during initial
    page load (before any JS injection would be possible).
    """

    def __init__(self):
        self.entries: List[Dict] = []
        self._seen_ids: set = set()

    def clear(self):
        """Clear collected entries for a new search chunk."""
        self.entries.clear()
        self._seen_ids.clear()

    async def handle_response(self, response):
        """Playwright response event handler — intercept SearchTimeline responses."""
        try:
            url = response.url
            if "SearchTimeline" not in url and "SearchAdaptive" not in url:
                return
            if response.status != 200:
                return

            data = await response.json()
            instructions = (
                data.get("data", {})
                .get("search_by_raw_query", {})
                .get("search_timeline", {})
                .get("timeline", {})
                .get("instructions", [])
            )

            added = 0
            for instruction in instructions:
                if instruction.get("type") != "TimelineAddEntries":
                    continue
                for entry in instruction.get("entries", []):
                    entry_id = entry.get("entryId", "")
                    # Skip cursors and non-tweet entries
                    if (entry_id.startswith("cursor-")
                            or "who-to-follow" in entry_id
                            or "search-feedback" in entry_id):
                        continue
                    if entry_id and entry_id not in self._seen_ids:
                        self._seen_ids.add(entry_id)
                        self.entries.append(entry)
                        added += 1

            if added > 0:
                logger.debug(
                    f"[Search] 拦截到 SearchTimeline 响应: "
                    f"+{added} 条（累计 {len(self.entries)}）"
                )

        except Exception as e:
            # Non-critical: log and continue
            logger.debug(f"[Search] 响应解析异常: {e}")


# ---------------------------------------------------------------------------
# Browser search URL builder
# ---------------------------------------------------------------------------

def _build_search_url(
    screen_name: str, since_date: str, until_date: str
) -> str:
    """Build Twitter search URL with date range filter.

    Matches browser manual search behavior: no &f= parameter (defaults to Top tab).
    """
    query = f"from:{screen_name} since:{since_date} until:{until_date}"
    encoded = urllib.parse.quote(query)
    return f"https://x.com/search?q={encoded}&src=typed_query"


# ---------------------------------------------------------------------------
# Browser scroll + collect
# ---------------------------------------------------------------------------

async def _scroll_and_collect_search(
    page,
    collector: SearchResponseCollector,
    max_scrolls: int = 80,
    scroll_delay_min: float = 1.5,
    scroll_delay_max: float = 3.0,
    max_no_new: int = 8,
) -> int:
    """Auto-scroll search results page while collector captures GraphQL responses.

    Returns:
        Number of entries collected during scrolling.
    """
    count_before = len(collector.entries)
    no_new_count = 0

    for scroll_idx in range(max_scrolls):
        entries_before_scroll = len(collector.entries)

        # Scroll down
        await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
        delay = random.uniform(scroll_delay_min, scroll_delay_max)
        await asyncio.sleep(delay)

        new_count = len(collector.entries) - entries_before_scroll
        if new_count > 0:
            no_new_count = 0
        else:
            no_new_count += 1

        # Check if we've hit the bottom
        at_bottom = await page.evaluate("""() => {
            return window.innerHeight + window.scrollY
                   >= document.body.scrollHeight - 200;
        }""")

        if at_bottom and no_new_count >= 3:
            logger.info(
                f"[Search] 已到达页面底部"
                f"（累计 {len(collector.entries)} 条）"
            )
            break

        if no_new_count >= max_no_new:
            logger.info(
                f"[Search] 连续 {max_no_new} 次滚动无新数据，停止"
                f"（累计 {len(collector.entries)} 条）"
            )
            break

        # Progress log every 10 scrolls
        if (scroll_idx + 1) % 10 == 0:
            logger.info(
                f"[Search] 滚动进度: {scroll_idx + 1}/{max_scrolls}, "
                f"累计 {len(collector.entries)} 条"
            )

    return len(collector.entries) - count_before


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fetch_search_supplementary(
    screen_name: str,
    display_name: str,
    cookies: dict,
    since_date: str,
    earliest_tweet_date: str,
    subfolder: str,
    saved_ids: dict,
    is_force: bool,
) -> dict:
    """
    Supplementary fetch via browser search to fill gaps left by UserTweets.

    Uses Playwright browser to navigate to Twitter search pages with monthly
    date-chunked queries, intercept GraphQL responses via page.on("response"),
    and extract tweet data.

    Args:
        screen_name: Twitter handle (e.g. "dontbesilent").
        display_name: User's display name (for logging).
        cookies: dict with 'auth_token' and 'ct0'.
        since_date: Target start date from X_USER_TWEETS_SINCE (e.g. "2025-01-01").
        earliest_tweet_date: Earliest date UserTweets reached (e.g. "2025-12-08").
        subfolder: Output subfolder (e.g. "status_dontbesilent").
        saved_ids: Shared dedup index dict (mutated in place).
        is_force: Whether FORCE_REFETCH is enabled.

    Returns:
        dict with: total, fetched, skipped, failed
    """
    from feedgrab.fetchers.twitter import _fetch_via_graphql, _clean_title
    from feedgrab.schema import from_twitter
    from feedgrab.utils.storage import save_to_markdown

    from feedgrab.fetchers.browser import (
        get_async_playwright, stealth_launch,
        get_stealth_context_options, setup_resource_blocking,
    )

    logger.info(
        f"[Search] 开始浏览器搜索补充抓取: @{screen_name}, "
        f"范围 {since_date} ~ {earliest_tweet_date}"
    )

    chunks = _generate_monthly_chunks(earliest_tweet_date, since_date)
    logger.info(f"[Search] 共 {len(chunks)} 个月度分片")

    delay = x_user_tweet_delay()

    total = 0
    fetched = 0
    skipped = 0
    failed = 0
    consecutive_empty = 0

    # Resolve session path
    session_dir = get_session_dir()
    session_path = str(session_dir / "twitter.json")
    if not Path(session_path).exists():
        logger.warning(
            "[Search] 未找到 Twitter session 文件，"
            "请先运行 feedgrab login twitter"
        )
        return {"total": 0, "fetched": 0, "skipped": 0, "failed": 0}

    # Create response collector
    collector = SearchResponseCollector()

    pw_cls = get_async_playwright()
    async with pw_cls() as p:
        browser = await stealth_launch(p, headless=False)

        ctx_opts = get_stealth_context_options(storage_state=session_path)
        context = await browser.new_context(**ctx_opts)
        await setup_resource_blocking(context)
        page = await context.new_page()

        # Register response interceptor (Python-level, survives navigations)
        page.on("response", collector.handle_response)

        try:
            # Warm up: navigate to x.com first to establish session
            logger.info("[Search] 预热: 先访问 x.com 激活 session...")
            try:
                await page.goto(
                    "https://x.com/home",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                await asyncio.sleep(3)
                logger.info("[Search] Session 预热完成")
            except Exception as e:
                logger.warning(f"[Search] 预热导航异常: {e}, 继续执行")

            for chunk_idx, (chunk_since, chunk_until) in enumerate(chunks):
                search_url = _build_search_url(
                    screen_name, chunk_since, chunk_until
                )
                logger.info(
                    f"[Search] 分片 [{chunk_idx + 1}/{len(chunks)}] "
                    f"from:{screen_name} since:{chunk_since} "
                    f"until:{chunk_until}"
                )

                # Clear collector for this chunk
                collector.clear()

                # Navigate to search page
                try:
                    await page.goto(
                        search_url,
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                except Exception as e:
                    logger.warning(
                        f"[Search] 导航失败: {e}, 跳过此分片"
                    )
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        logger.info(
                            "[Search] 连续 3 个分片失败，提前终止"
                        )
                        break
                    continue

                # Wait for tweets to render (or "No results" indicator)
                try:
                    await page.wait_for_selector(
                        '[data-testid="tweet"], '
                        '[data-testid="empty_state_header_text"]',
                        timeout=15000,
                    )
                except Exception:
                    logger.info(
                        f"[Search] 分片 [{chunk_idx + 1}/{len(chunks)}] "
                        f"页面加载超时，尝试继续"
                    )

                # Check for empty results
                empty_state = await page.query_selector(
                    '[data-testid="empty_state_header_text"]'
                )
                if empty_state:
                    logger.info(
                        f"[Search] 分片 [{chunk_idx + 1}/{len(chunks)}] "
                        f"无搜索结果"
                    )
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        logger.info(
                            "[Search] 连续 3 个月度分片无结果，提前终止"
                        )
                        break
                    continue

                # Wait a moment for initial GraphQL response to arrive
                await asyncio.sleep(2)

                initial_count = len(collector.entries)
                logger.info(
                    f"[Search] 分片 [{chunk_idx + 1}/{len(chunks)}] "
                    f"初始加载捕获 {initial_count} 条"
                )

                # Scroll to load more
                scroll_added = await _scroll_and_collect_search(
                    page, collector
                )

                chunk_entries = list(collector.entries)
                logger.info(
                    f"[Search] 分片 [{chunk_idx + 1}/{len(chunks)}] "
                    f"共捕获 {len(chunk_entries)} 条原始条目"
                    f"（初始 {initial_count} + 滚动 {scroll_added}）"
                )

                # Process each entry
                chunk_fetched = 0
                for entry in chunk_entries:
                    tweet_data = extract_tweet_data(entry)
                    if not tweet_data:
                        continue

                    total += 1
                    tweet_id = tweet_data.get("id", "")
                    author = tweet_data.get("author", "")
                    tweet_url = (
                        f"https://x.com/{author}/status/{tweet_id}"
                    )
                    item_id = item_id_from_url(tweet_url)
                    title_preview = _clean_title(
                        tweet_data.get("text", "")[:80]
                    )

                    # Filter: only keep tweets from the target user
                    if author.lower() != screen_name.lower():
                        skipped += 1
                        continue

                    # Filter: skip replies to other users
                    in_reply_to_user = tweet_data.get(
                        "in_reply_to_user_id", ""
                    )
                    own_user_id = tweet_data.get("user_id", "")
                    if (in_reply_to_user
                            and in_reply_to_user != own_user_id):
                        skipped += 1
                        continue

                    # Skip self-reply non-root tweets
                    conv_id = tweet_data.get("conversation_id", "")
                    if conv_id and conv_id != tweet_id:
                        skipped += 1
                        continue

                    # Dedup
                    if has_item(item_id, saved_ids) and not is_force:
                        skipped += 1
                        continue

                    # Classify and process
                    tweet_type = _classify_tweet(tweet_data)

                    try:
                        if tweet_type == "single":
                            data = _build_single_tweet_data(
                                tweet_data, tweet_url
                            )
                        elif tweet_type == "thread":
                            logger.info(
                                f"[Search] 线程推文: @{author}"
                            )
                            try:
                                data = await _fetch_via_graphql(
                                    tweet_url, tweet_id
                                )
                            except Exception as thread_err:
                                logger.warning(f"[Search] 线程获取失败 ({thread_err})，退化为单条保存")
                                data = _build_single_tweet_data(
                                    tweet_data, tweet_url
                                )
                            time.sleep(delay)
                        elif tweet_type == "article":
                            data = _build_single_tweet_data(
                                tweet_data, tweet_url
                            )
                            article = tweet_data.get("article") or {}
                            article_body = article.get("body", "")
                            if article_body and len(article_body.strip()) > 200:
                                logger.info(f"[Search] Article — GraphQL content_state: @{author}")
                                data["text"] = article_body
                                if data.get("thread_tweets"):
                                    data["thread_tweets"][0]["text"] = article_body
                            else:
                                logger.info(f"[Search] 长文章，Jina 获取正文: @{author}")
                                jina_content = _fetch_article_body(
                                    tweet_url,
                                    article,
                                    author,
                                    "[Search]",
                                    source_text=tweet_data.get("text", ""),
                                )
                                if jina_content:
                                    data["text"] = jina_content
                                    if data.get("thread_tweets"):
                                        data["thread_tweets"][0][
                                            "text"
                                        ] = jina_content
                            time.sleep(delay)
                        else:
                            data = _build_single_tweet_data(
                                tweet_data, tweet_url
                            )

                        # Convert to UnifiedContent and save
                        content = from_twitter(data)
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
                        chunk_fetched += 1

                    except Exception as e:
                        error_msg = str(e)
                        logger.warning(
                            f"[Search] 失败: @{author} - "
                            f"{error_msg[:80]}"
                        )
                        failed += 1

                # Log chunk result
                logger.info(
                    f"[Search] 分片 [{chunk_idx + 1}/{len(chunks)}] "
                    f"完成, 累计: 成功:{fetched} 跳过:{skipped} "
                    f"失败:{failed}"
                )

                # Early termination: 3 consecutive empty chunks
                if chunk_fetched == 0:
                    consecutive_empty += 1
                    logger.info(
                        f"[Search] 分片无新推文"
                        f"（连续空分片: {consecutive_empty}/3）"
                    )
                    if consecutive_empty >= 3:
                        logger.info(
                            "[Search] 连续 3 个月度分片无新推文，"
                            "提前终止"
                        )
                        break
                else:
                    consecutive_empty = 0
                    logger.info(
                        f"[Search] 分片新增 {chunk_fetched} 条"
                    )

                # Brief pause between chunks
                await asyncio.sleep(2)

        finally:
            await context.close()
            await browser.close()

    logger.info(
        f"[Search] 补充抓取完成: "
        f"总处理 {total}, 成功 {fetched}, 跳过 {skipped}, "
        f"失败 {failed}"
    )

    return {
        "total": total,
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
    }
