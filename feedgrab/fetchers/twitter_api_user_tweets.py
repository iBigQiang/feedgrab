# -*- coding: utf-8 -*-
"""
Twitter/X User Tweets batch fetcher via TwitterAPI.io paid API.

Two entry points:
    1. fetch_api_supplementary() — replaces Playwright browser search supplementary
       Called from twitter_user_tweets.py when UserTweets hits ~800 limit.
    2. fetch_api_user_tweets() — standalone full API path
       Called from reader.py when X_API_PROVIDER=api (server deployment).

Design:
    - "API discover + GraphQL download" hybrid strategy
    - API discovers all tweet IDs via Advanced Search (no count limit)
    - GraphQL fetches full data per tweet (images, videos, threads)
    - OR: direct save mode skips GraphQL (faster, no media)
    - Shares dedup index with GraphQL mode (seamless switching)
    - Supports engagement filtering (likes/retweets/views, OR logic)

Cost: ~$0.15 per 1,000 tweets discovered via API.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from loguru import logger
from typing import Dict, Any, Optional, List

from feedgrab.config import (
    x_user_tweet_delay,
    x_user_tweets_since,
    force_refetch,
    x_api_save_directly,
    x_api_min_likes,
    x_api_min_retweets,
    x_api_min_views,
    parse_twitter_date_local,
)
from feedgrab.fetchers.twitter_api import (
    search_tweets,
    get_user_last_tweets,
    parse_api_tweet,
)
from feedgrab.fetchers.twitter_bookmarks import (
    _classify_tweet,
    _build_single_tweet_data,
    _sanitize_folder_name,
    _fetch_article_body,
)
from feedgrab.utils.dedup import (
    load_index,
    save_index,
    has_item,
    add_item,
    item_id_from_url,
)


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def _parse_profile_url(url: str) -> str:
    """Extract screen_name from a profile URL."""
    match = re.search(r'(?:x\.com|twitter\.com)/([a-zA-Z0-9_]{1,15})', url)
    if match:
        return match.group(1)
    raise ValueError(f"无法从 URL 提取用户名: {url}")


# ---------------------------------------------------------------------------
# Engagement filter
# ---------------------------------------------------------------------------

def _passes_engagement_filter(tweet_data: dict) -> bool:
    """Check if a tweet passes the engagement filter (OR logic).

    If no filter is configured (all thresholds are 0), all tweets pass.
    If any filter is configured, tweet must meet AT LEAST ONE threshold.
    """
    min_likes = x_api_min_likes()
    min_retweets = x_api_min_retweets()
    min_views = x_api_min_views()

    # No filter configured → pass all
    if not min_likes and not min_retweets and not min_views:
        return True

    # OR logic: pass if ANY threshold is met
    if min_likes and tweet_data.get("likes", 0) >= min_likes:
        return True
    if min_retweets and tweet_data.get("retweets", 0) >= min_retweets:
        return True
    if min_views:
        try:
            if int(tweet_data.get("views", "0")) >= min_views:
                return True
        except (ValueError, TypeError):
            pass

    return False


# ---------------------------------------------------------------------------
# Discovery cache — breakpoint resume support
# ---------------------------------------------------------------------------

def _get_cache_path(screen_name: str, since_date: str = "") -> Path:
    """Return path to the discovery cache JSONL file."""
    from feedgrab.utils.dedup import get_index_path
    index_dir = get_index_path().parent
    index_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_since_{since_date}" if since_date else ""
    return index_dir / f".api_discovery_{screen_name.lower()}{suffix}.jsonl"


def _load_discovery_cache(cache_path: Path) -> tuple[List[dict], set, bool]:
    """Load previously discovered tweets from cache file.

    Returns:
        (cached_tweets, seen_ids, is_complete)
        - cached_tweets: list of parsed tweet dicts
        - seen_ids: set of tweet IDs already discovered
        - is_complete: True if discovery was fully completed last time
    """
    if not cache_path.exists():
        return [], set(), False

    cached_tweets = []
    seen_ids = set()
    is_complete = False

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("_status") == "complete":
                    is_complete = True
                    continue

                tweet_id = entry.get("id", "")
                if tweet_id and tweet_id not in seen_ids:
                    seen_ids.add(tweet_id)
                    cached_tweets.append(entry)
    except Exception as e:
        logger.warning(f"[API-Cache] 缓存文件读取失败: {e}")
        return [], set(), False

    return cached_tweets, seen_ids, is_complete


def _append_to_cache(cache_path: Path, tweets: List[dict]):
    """Append discovered tweets to cache file (JSONL format)."""
    with open(cache_path, "a", encoding="utf-8") as f:
        for t in tweets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")


def _mark_cache_complete(cache_path: Path):
    """Mark the discovery cache as complete."""
    with open(cache_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"_status": "complete",
                             "completed_at": datetime.now().isoformat()},
                            ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# API discovery — paginate to collect all tweets
# ---------------------------------------------------------------------------

def _discover_tweets_via_search(
    screen_name: str,
    since_date: str = "",
    initial_max_id: int = None,
    max_pages: int = 5000,
) -> List[dict]:
    """Discover all tweets via Advanced Search API with max_id pagination.

    Strategy: **ID-based pagination** using ``max_id:{id}`` in the query string.

    Each page returns ~20 tweets. We take the smallest tweet ID on each page,
    subtract 1, and inject ``max_id:{smallest_id - 1}`` into the next query.
    This leverages Twitter's Snowflake IDs (monotonically increasing) for
    precise, gap-free pagination.

    **Important**: TwitterAPI.io's ``since:``, ``until:``, and direct ``max_id``
    jump operators are unreliable for historical dates. Therefore:
    - The query uses only ``from:{screen_name}`` (no date operators)
    - Date filtering (since_date) is done in-code after receiving results
    - ``max_id`` is only used incrementally (from previous page's smallest ID)

    **Breakpoint resume**: Discovered tweets are written to a JSONL cache file
    in real-time. If interrupted, the next run loads the cache and resumes
    from the last known position (smallest cached ID).

    Args:
        screen_name: Twitter handle (without @)
        since_date: Start date filter (inclusive), format YYYY-MM-DD.
            Applied in-code, not via search operator.
        initial_max_id: Ignored (kept for API compatibility). TwitterAPI.io
            does not support jumping to arbitrary historical IDs.
        max_pages: Safety limit on total pages (default 5000 = ~100k tweets)

    Returns:
        List of parsed tweet dicts (from parse_api_tweet).
    """
    # --- Check discovery cache for resume ---
    cache_path = _get_cache_path(screen_name, since_date)
    cached_tweets, seen_ids, is_complete = _load_discovery_cache(cache_path)

    if is_complete and cached_tweets:
        logger.info(
            f"[API-Search] 发现缓存已完成，直接加载 {len(cached_tweets)} 条推文 "
            f"(缓存: {cache_path.name})"
        )
        return cached_tweets

    # Resume from cache: find smallest ID to continue from
    all_tweets = list(cached_tweets)
    max_id = None  # Will be set from cache if resuming

    if seen_ids:
        # Find smallest ID in cache → resume from there
        min_cached_id = None
        for tid_str in seen_ids:
            try:
                tid_int = int(tid_str)
                if min_cached_id is None or tid_int < min_cached_id:
                    min_cached_id = tid_int
            except ValueError:
                pass
        if min_cached_id is not None:
            max_id = min_cached_id - 1
        logger.info(
            f"[API-Search] 从缓存续传: 已有 {len(all_tweets)} 条，"
            f"从 max_id={max_id} 继续 (缓存: {cache_path.name})"
        )
    else:
        logger.info(
            f"[API-Search] max_id 分页: from:{screen_name} "
            f"(since_date={since_date or '全部'} 代码层过滤)"
        )

    # Track consecutive empty-after-filter pages to detect search index gap
    consecutive_empty = 0

    for page in range(1, max_pages + 1):
        # Build query: only from: + max_id (no since:/until: — unreliable)
        query = f"from:{screen_name}"
        if max_id is not None:
            query += f" max_id:{max_id}"

        response = search_tweets(query, query_type="Latest")
        if not response:
            logger.warning(f"[API-Search] P{page}: API 请求失败，停止")
            break

        raw_tweets = response.get("tweets", [])
        if not raw_tweets:
            logger.info(
                f"[API-Search] P{page}: 空页，发现结束 "
                f"(累计 {len(all_tweets)})"
            )
            # Discovery complete — mark cache
            _mark_cache_complete(cache_path)
            break

        page_new = 0
        min_id_on_page = None
        page_parsed = []
        page_too_old = 0  # tweets before since_date on this page

        for raw in raw_tweets:
            tweet_id = raw.get("id", "")
            if not tweet_id:
                continue

            # Track smallest ID for pagination (even for filtered tweets)
            try:
                tid_int = int(tweet_id)
                if min_id_on_page is None or tid_int < min_id_on_page:
                    min_id_on_page = tid_int
            except ValueError:
                pass

            if tweet_id in seen_ids:
                continue
            seen_ids.add(tweet_id)

            parsed = parse_api_tweet(raw)

            # In-code date filtering: skip tweets before since_date
            if since_date:
                tweet_date = parse_twitter_date_local(
                    parsed.get("created_at", "")
                )
                if tweet_date and tweet_date < since_date:
                    page_too_old += 1
                    continue

            all_tweets.append(parsed)
            page_parsed.append(parsed)
            page_new += 1

        # Write this page to cache immediately
        if page_parsed:
            _append_to_cache(cache_path, page_parsed)

        # Progress log every 50 pages + first 3 pages
        if page % 50 == 0 or page <= 3:
            extra = f" 过早:{page_too_old}" if page_too_old else ""
            logger.info(
                f"[API-Search] P{page}: +{page_new} 新 "
                f"(累计 {len(all_tweets)}{extra})"
            )

        # Pagination end conditions
        if page_new == 0:
            if page_too_old > 0:
                # All new tweets on this page are before since_date
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    logger.info(
                        f"[API-Search] P{page}: 连续 {consecutive_empty} 页"
                        f"全部早于 {since_date}，发现结束 "
                        f"(累计 {len(all_tweets)})"
                    )
                    _mark_cache_complete(cache_path)
                    break
            else:
                # Genuine exhaustion (all duplicates or empty)
                logger.info(
                    f"[API-Search] P{page}: 无新推文，发现结束 "
                    f"(累计 {len(all_tweets)})"
                )
                _mark_cache_complete(cache_path)
                break
        else:
            consecutive_empty = 0

        # Advance: next page starts below smallest ID on this page
        if min_id_on_page is not None:
            max_id = min_id_on_page - 1
        else:
            logger.info(f"[API-Search] P{page}: 无法提取 ID，停止")
            break

        time.sleep(0.3)

    logger.info(
        f"[API-Search] 发现完成: {len(all_tweets)} 条去重推文, "
        f"{page} 页查询"
    )
    return all_tweets


# ---------------------------------------------------------------------------
# Batch record persistence
# ---------------------------------------------------------------------------

def _save_batch_record(
    tweet_list: list, screen_name: str, since_date: str = "", prefix: str = "api"
) -> str:
    """Save batch record to a JSON file in the index/ directory."""
    from feedgrab.utils.dedup import get_index_path

    out_dir = get_index_path().parent
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    today = datetime.now().strftime("%Y-%m-%d")

    if since_date:
        filename = f"{prefix}_status_{screen_name}_{since_date}_{today}_{ts}.json"
    else:
        filename = f"{prefix}_status_{screen_name}_all_{ts}.json"

    path = out_dir / filename

    payload = {
        "fetched_at": datetime.now().isoformat(),
        "screen_name": screen_name,
        "provider": "twitterapi.io",
        "since": since_date or "",
        "total": len(tweet_list),
        "tweets": tweet_list,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"[API-UserTweets] 批量记录已保存: {path}")
    return str(path)


# ---------------------------------------------------------------------------
# Shared processing logic
# ---------------------------------------------------------------------------

async def _process_tweets(
    all_tweets: List[dict],
    screen_name: str,
    display_name: str,
    subfolder: str,
    saved_ids: dict,
    is_force: bool,
    delay: float,
    log_prefix: str = "[API-UserTweets]",
) -> dict:
    """Process discovered tweets: filter, dedup, fetch full data, save.

    This is the shared Phase 2 logic used by both fetch_api_supplementary()
    and fetch_api_user_tweets().

    Returns:
        dict with: total, fetched, skipped, failed, filtered, tweet_list
    """
    from feedgrab.fetchers.twitter import _fetch_via_graphql, _clean_title
    from feedgrab.schema import from_twitter
    from feedgrab.utils.storage import save_to_markdown

    save_directly = x_api_save_directly()

    # Build conversation map for thread detection
    conversation_counts: Dict[str, int] = {}
    for t in all_tweets:
        if t.get("_is_retweet"):
            continue
        conv_id = t.get("conversation_id", "")
        if conv_id:
            conversation_counts[conv_id] = conversation_counts.get(conv_id, 0) + 1

    multi_entry_convs = {cid for cid, cnt in conversation_counts.items() if cnt > 1}
    if multi_entry_convs:
        logger.info(
            f"{log_prefix} 检测到 {len(multi_entry_convs)} 个多条目会话"
        )

    processed_conv_ids: set = set()
    processed_ids: set = set()
    tweet_list: list = []
    fetched = 0
    skipped = 0
    failed = 0
    filtered_count = 0
    total_count = len(all_tweets)

    for idx, tweet_data in enumerate(all_tweets):
        tweet_id = tweet_data.get("id", "")
        author = tweet_data.get("author", screen_name)
        author_name = tweet_data.get("author_name", display_name)
        tweet_url = f"https://x.com/{author}/status/{tweet_id}"
        item_id = item_id_from_url(tweet_url)
        published = parse_twitter_date_local(tweet_data.get("created_at", ""))
        title_preview = _clean_title(tweet_data.get("text", "")[:80])

        # --- Filter: retweets ---
        if tweet_data.get("_is_retweet"):
            skipped += 1
            continue

        # --- Filter: replies to other users ---
        in_reply_to_user = tweet_data.get("in_reply_to_user_id", "")
        own_user_id = tweet_data.get("user_id", "")
        if in_reply_to_user and in_reply_to_user != own_user_id:
            skipped += 1
            continue

        # --- Filter: non-target user (from search results) ---
        if author.lower() != screen_name.lower():
            skipped += 1
            continue

        # --- Filter: engagement ---
        if not _passes_engagement_filter(tweet_data):
            filtered_count += 1
            skipped += 1
            tweet_list.append({
                "url": tweet_url, "tweet_id": tweet_id, "item_id": item_id,
                "author": f"@{author}", "author_name": author_name,
                "published": published, "title": title_preview,
                "status": "skipped", "error": "未达互动阈值",
            })
            continue

        # --- Conversation dedup ---
        conv_id = tweet_data.get("conversation_id", "")
        is_root = (conv_id == tweet_id) or not conv_id
        if conv_id in multi_entry_convs:
            if not is_root:
                skipped += 1
                continue
            if conv_id in processed_conv_ids:
                skipped += 1
                continue

        # --- In-batch dedup ---
        if tweet_id in processed_ids:
            skipped += 1
            continue
        processed_ids.add(tweet_id)

        # --- File-level dedup ---
        if has_item(item_id, saved_ids) and not is_force:
            skipped += 1
            tweet_list.append({
                "url": tweet_url, "tweet_id": tweet_id, "item_id": item_id,
                "author": f"@{author}", "author_name": author_name,
                "published": published, "title": title_preview,
                "status": "skipped", "error": "",
            })
            continue

        # --- Classify ---
        tweet_type = _classify_tweet(tweet_data)

        # Upgrade root tweets in multi-entry conversations to "thread"
        if tweet_type == "single" and conv_id in multi_entry_convs:
            tweet_type = "thread"

        # Smart article detection from API data (API doesn't return article field,
        # but article URLs in text reveal them)
        if tweet_type == "single":
            text = tweet_data.get("text", "")
            if "x.com/i/article/" in text or "twitter.com/i/article/" in text:
                tweet_type = "article"

        # Track processed conversation
        if conv_id:
            processed_conv_ids.add(conv_id)

        # --- Process ---
        try:
            # Smart direct save: normal tweets → API data (fast),
            # articles & threads → GraphQL for full content (images/videos/body)
            needs_graphql = (
                not save_directly
                or tweet_type in ("article", "thread")
            )

            if needs_graphql:
                try:
                    data = await _fetch_via_graphql(tweet_url, tweet_id)
                except Exception as gql_err:
                    logger.warning(
                        f"{log_prefix} [{idx + 1}/{total_count}] "
                        f"GraphQL 失败 ({gql_err})，使用 API 数据"
                    )
                    data = _build_single_tweet_data(tweet_data, tweet_url)

                # For articles, prefer GraphQL content_state, fallback to Jina
                if tweet_type == "article":
                    article = data.get("article_data") or tweet_data.get("article") or {}
                    article_body = article.get("body", "")
                    if article_body and len(article_body.strip()) > 200:
                        logger.info(f"{log_prefix} Article — GraphQL content_state: @{author}")
                        data["text"] = article_body
                        if data.get("thread_tweets"):
                            data["thread_tweets"][0]["text"] = article_body
                    else:
                        jina_content = _fetch_article_body(
                            tweet_url,
                            article,
                            author,
                            log_prefix,
                            source_text=tweet_data.get("text", ""),
                        )
                        if jina_content:
                            data["text"] = jina_content
                            if data.get("thread_tweets"):
                                data["thread_tweets"][0]["text"] = jina_content

                time.sleep(delay)
            else:
                # Direct save: use API data as-is (fast, no GraphQL call)
                data = _build_single_tweet_data(tweet_data, tweet_url)

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

            tweet_list.append({
                "url": tweet_url, "tweet_id": tweet_id, "item_id": item_id,
                "author": f"@{author}", "author_name": author_name,
                "published": published, "title": title_preview,
                "status": "fetched", "error": "",
            })

            # Progress log every 10 items
            if (idx + 1) % 10 == 0:
                logger.info(
                    f"{log_prefix} 进度 [{idx + 1}/{total_count}] "
                    f"成功:{fetched} 跳过:{skipped} 失败:{failed} "
                    f"过滤:{filtered_count}"
                )

            # Persist dedup index every 50 successful saves (crash protection)
            if fetched > 0 and fetched % 50 == 0:
                save_index(saved_ids)
                logger.debug(f"{log_prefix} 索引自动保存 ({len(saved_ids)} 条)")

        except Exception as e:
            error_msg = str(e)
            logger.warning(
                f"{log_prefix} [{idx + 1}/{total_count}] "
                f"失败: @{author} - {error_msg[:80]}"
            )
            failed += 1
            tweet_list.append({
                "url": tweet_url, "tweet_id": tweet_id, "item_id": item_id,
                "author": f"@{author}", "author_name": author_name,
                "published": published, "title": title_preview,
                "status": "failed", "error": error_msg[:200],
            })

    return {
        "total": total_count,
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "filtered": filtered_count,
        "tweet_list": tweet_list,
    }


# ---------------------------------------------------------------------------
# Entry point 1: Supplementary (replaces browser search)
# ---------------------------------------------------------------------------

async def fetch_api_supplementary(
    screen_name: str,
    display_name: str,
    since_date: str,
    earliest_tweet_date: str,
    subfolder: str,
    saved_ids: dict,
    is_force: bool,
    earliest_tweet_id: str = "",
) -> dict:
    """Fetch historical tweets via paid API to supplement UserTweets.

    Replaces twitter_search_tweets.fetch_search_supplementary().
    Called from twitter_user_tweets.py when UserTweets hits ~800 limit
    and TWITTERAPI_IO_KEY is configured.

    Args:
        screen_name: Twitter handle (without @)
        display_name: User display name (for logging)
        since_date: Target start date (YYYY-MM-DD)
        earliest_tweet_date: Earliest date from UserTweets phase (YYYY-MM-DD)
        subfolder: Save subdirectory (e.g. "status_强子手记")
        saved_ids: Shared dedup index dict
        is_force: Whether FORCE_REFETCH is enabled
        earliest_tweet_id: Tweet ID of the earliest UserTweets entry (used as
            initial max_id upper bound). Required because TwitterAPI.io's
            ``until:`` operator does not work for historical dates.

    Returns:
        dict with: total, fetched, skipped, failed
    """
    log_prefix = "[API-Supplementary]"
    logger.info(
        f"{log_prefix} 补充抓取 @{screen_name}，"
        f"范围: {since_date} → {earliest_tweet_date}"
        f" (boundary ID: {earliest_tweet_id})"
    )

    delay = x_user_tweet_delay()
    save_directly = x_api_save_directly()
    logger.info(
        f"{log_prefix} 配置: save_directly={save_directly}, delay={delay}s, "
        f"min_likes={x_api_min_likes()}, min_retweets={x_api_min_retweets()}, "
        f"min_views={x_api_min_views()}"
    )

    # Phase 1: Discover tweets via API
    # Use earliest_tweet_id as initial max_id (TwitterAPI.io's until: operator
    # does not work for historical dates, so we use real tweet ID as upper bound)
    initial_max_id = None
    if earliest_tweet_id:
        try:
            initial_max_id = int(earliest_tweet_id) - 1
        except ValueError:
            pass

    all_tweets = _discover_tweets_via_search(
        screen_name,
        since_date=since_date,
        initial_max_id=initial_max_id,
    )

    if not all_tweets:
        logger.info(f"{log_prefix} API 未发现补充推文")
        return {"total": 0, "fetched": 0, "skipped": 0, "failed": 0}

    logger.info(f"{log_prefix} 发现 {len(all_tweets)} 条补充推文")

    # Phase 2: Process tweets
    result = await _process_tweets(
        all_tweets=all_tweets,
        screen_name=screen_name,
        display_name=display_name,
        subfolder=subfolder,
        saved_ids=saved_ids,
        is_force=is_force,
        delay=delay,
        log_prefix=log_prefix,
    )

    logger.info(
        f"{log_prefix} 补充抓取完成: "
        f"总计 {result['total']}, 成功 {result['fetched']}, "
        f"跳过 {result['skipped']}, 失败 {result['failed']}, "
        f"过滤 {result['filtered']}"
    )

    return {
        "total": result["total"],
        "fetched": result["fetched"],
        "skipped": result["skipped"],
        "failed": result["failed"],
    }


# ---------------------------------------------------------------------------
# Entry point 2: Standalone full API path
# ---------------------------------------------------------------------------

async def fetch_api_user_tweets(profile_url: str) -> dict:
    """Batch-fetch ALL tweets from a user via TwitterAPI.io paid API.

    Standalone full API path — does not depend on GraphQL UserTweets.
    Used when X_API_PROVIDER=api (server deployment, no cookies needed).

    Two-phase "API discover + GraphQL download" strategy:
    Phase 1: API discovers all tweet IDs (no count limit)
    Phase 2: Process each tweet (GraphQL for full data, or direct save)

    Args:
        profile_url: Profile URL (e.g. https://x.com/iBigQiang)

    Returns:
        dict with: total, fetched, skipped, failed, filtered, list_path
    """
    log_prefix = "[API-UserTweets]"

    from feedgrab.fetchers.twitter_fxtwitter import reset_circuit_breaker
    reset_circuit_breaker()

    # 1. Parse screen_name
    screen_name = _parse_profile_url(profile_url)
    logger.info(f"{log_prefix} 用户: @{screen_name}")

    # 2. Load config
    since_date = x_user_tweets_since()
    delay = x_user_tweet_delay()
    is_force = force_refetch()

    logger.info(
        f"{log_prefix} 配置: provider=api, "
        f"save_directly={x_api_save_directly()}, "
        f"since={since_date or '全部'}, "
        f"min_likes={x_api_min_likes()}, "
        f"min_retweets={x_api_min_retweets()}, "
        f"min_views={x_api_min_views()}"
    )

    # 3. Phase 1 — Discover all tweets via API
    logger.info(f"{log_prefix} === 第一阶段：API 发现推文 ===")
    all_tweets = _discover_tweets_via_search(screen_name, since_date=since_date)

    if not all_tweets:
        logger.warning(f"{log_prefix} Advanced Search 无结果，尝试 UserTimeline 接口")
        # Fallback to User Last Tweets API
        # Note: response structure is {data: {tweets: [...]}} not {tweets: [...]}
        all_tweets = []
        seen_ids = set()
        cursor = ""
        page = 0
        while page < 500:
            page += 1
            resp = get_user_last_tweets(screen_name, cursor=cursor)
            if not resp:
                break
            # User Last Tweets wraps data in resp.data.tweets
            inner = resp.get("data", resp)
            raw_tweets = (
                inner.get("tweets", [])
                if isinstance(inner, dict)
                else resp.get("tweets", [])
            )
            if not raw_tweets:
                break
            new_count = 0
            for raw in raw_tweets:
                tid = raw.get("id", "")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    all_tweets.append(parse_api_tweet(raw))
                    new_count += 1
            logger.info(
                f"{log_prefix} UserTimeline 第 {page} 页: "
                f"+{new_count} 新 (累计 {len(all_tweets)})"
            )
            if new_count == 0:
                break
            has_next = resp.get("has_next_page", False)
            next_cursor = resp.get("next_cursor", "")
            if not has_next or not next_cursor:
                break
            cursor = next_cursor
            time.sleep(0.5)

    if not all_tweets:
        logger.error(f"{log_prefix} 两个 API 接口均未返回数据")
        return {
            "total": 0, "fetched": 0, "skipped": 0,
            "failed": 0, "filtered": 0, "list_path": "",
        }

    logger.info(f"{log_prefix} 发现阶段完成，原始推文 {len(all_tweets)} 条")

    # 4. Determine display name and subfolder
    first_author_name = ""
    for t in all_tweets:
        if t.get("author", "").lower() == screen_name.lower():
            first_author_name = t.get("author_name", "")
            if first_author_name:
                break
    display_name = first_author_name or screen_name
    folder_label = _sanitize_folder_name(display_name)
    subfolder = f"status_author/{folder_label}"
    logger.info(f"{log_prefix} 保存目录: {subfolder}")

    # 5. Load dedup index
    saved_ids = load_index()
    initial_count = len(saved_ids)
    logger.info(
        f"{log_prefix} 已有 {initial_count} 条推文索引"
        + (" (FORCE_REFETCH=true)" if is_force else "")
    )

    # 6. Phase 2 — Process tweets
    logger.info(f"{log_prefix} === 第二阶段：逐条处理 ===")
    result = await _process_tweets(
        all_tweets=all_tweets,
        screen_name=screen_name,
        display_name=display_name,
        subfolder=subfolder,
        saved_ids=saved_ids,
        is_force=is_force,
        delay=delay,
        log_prefix=log_prefix,
    )

    # 7. Persist index
    save_index(saved_ids)
    logger.info(f"{log_prefix} 索引更新: {initial_count} -> {len(saved_ids)} 条")

    # 8. Save batch record
    list_path = _save_batch_record(
        result["tweet_list"], screen_name, since_date, prefix="api"
    )

    logger.info(
        f"{log_prefix} 批量抓取完成: "
        f"总计 {result['total']}, 成功 {result['fetched']}, "
        f"跳过 {result['skipped']}, 失败 {result['failed']}, "
        f"过滤 {result['filtered']}"
    )

    return {
        "total": result["total"],
        "fetched": result["fetched"],
        "skipped": result["skipped"],
        "failed": result["failed"],
        "filtered": result["filtered"],
        "list_path": list_path,
    }
