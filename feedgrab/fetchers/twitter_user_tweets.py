# -*- coding: utf-8 -*-
"""
Twitter/X User Tweets batch fetcher — fetch all tweets from a user profile via GraphQL.

Supports:
    - feedgrab https://x.com/iBigQiang           (all tweets)
    - feedgrab https://x.com/iBigQiang/with_replies  (treated same as above)

Design (mirrors twitter_bookmarks.py):
    - Resolve screen_name → user_id via UserByScreenName API
    - Paginate UserTweets API, extract tweet data directly
    - Only fetch full threads or article bodies when needed (secondary API calls)
    - Stream-save each tweet immediately
    - Optional date filtering via X_USER_TWEETS_SINCE env var
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from loguru import logger
from typing import Dict, Any, Optional

from feedgrab.config import x_user_tweet_max_pages, x_user_tweet_delay, x_user_tweets_since, force_refetch
from feedgrab.fetchers.twitter_graphql import (
    fetch_user_by_screen_name,
    fetch_user_tweets_page,
    parse_user_tweets_entries,
    extract_tweet_data,
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
    """Extract screen_name from a profile URL.

    Examples:
        https://x.com/iBigQiang → "iBigQiang"
        https://x.com/iBigQiang/with_replies → "iBigQiang"
        https://twitter.com/iBigQiang → "iBigQiang"
    """
    match = re.search(r'(?:x\.com|twitter\.com)/([a-zA-Z0-9_]{1,15})', url)
    if match:
        return match.group(1)
    raise ValueError(f"无法从 URL 提取用户名: {url}")


# ---------------------------------------------------------------------------
# Date filtering
# ---------------------------------------------------------------------------

def _parse_tweet_date(created_at: str) -> str:
    """Parse Twitter RFC 2822 date to 'YYYY-MM-DD' for comparison."""
    from feedgrab.config import parse_twitter_date_local
    return parse_twitter_date_local(created_at)


# ---------------------------------------------------------------------------
# Batch record persistence
# ---------------------------------------------------------------------------

def _get_record_dir() -> Path:
    """Return the index directory for batch records."""
    from feedgrab.utils.dedup import get_index_path
    index_dir = get_index_path().parent
    index_dir.mkdir(parents=True, exist_ok=True)
    return index_dir


def _save_batch_record(
    tweet_list: list, screen_name: str, since_date: str = ""
):
    """Save batch record to a JSON file in the index/ directory."""
    out_dir = _get_record_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    today = datetime.now().strftime("%Y-%m-%d")

    if since_date:
        filename = f"status_{screen_name}_{since_date}_{today}_{ts}.json"
    else:
        filename = f"status_{screen_name}_all_{ts}.json"

    path = out_dir / filename

    payload = {
        "fetched_at": datetime.now().isoformat(),
        "screen_name": screen_name,
        "since": since_date or "",
        "total": len(tweet_list),
        "tweets": tweet_list,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"[UserTweets] 批量记录已保存: {path}")
    return path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fetch_user_tweets(
    profile_url: str, cookies: dict, mode: str = "tweets"
) -> dict:
    """
    Batch-fetch all tweets from a user profile and save each as Markdown.

    Args:
        profile_url: Profile URL (e.g. https://x.com/iBigQiang)
        cookies: dict with 'auth_token' and 'ct0'
        mode: "tweets" (default UserTweets) | "likes" | "replies"

    Returns:
        dict with: total, fetched, skipped, failed, list_path
    """
    from feedgrab.fetchers.twitter import _fetch_via_graphql, _clean_title
    from feedgrab.fetchers.twitter_fxtwitter import reset_circuit_breaker
    from feedgrab.fetchers.jina import fetch_via_jina
    from feedgrab.schema import from_twitter
    from feedgrab.utils.storage import save_to_markdown

    # v0.22.0: pick page-fetcher by mode
    from feedgrab.fetchers.twitter_graphql import (
        fetch_user_likes_page,
        fetch_user_tweets_and_replies_page,
    )
    if mode == "likes":
        page_fetcher = fetch_user_likes_page
        mode_label = "Likes"
    elif mode == "replies":
        page_fetcher = fetch_user_tweets_and_replies_page
        mode_label = "Replies"
    else:
        page_fetcher = fetch_user_tweets_page
        mode_label = "UserTweets"

    reset_circuit_breaker()

    # 1. Parse screen_name from URL
    screen_name = _parse_profile_url(profile_url)
    logger.info(f"[UserTweets] 用户: @{screen_name}")

    # 2. Resolve user_id and display_name
    user_info = fetch_user_by_screen_name(screen_name, cookies)
    user_id = user_info.get("user_id", "")
    display_name = user_info.get("name", "")
    screen_name = user_info.get("screen_name", screen_name)  # canonical

    if not user_id:
        raise RuntimeError(f"无法获取用户 ID: @{screen_name}")

    logger.info(
        f"[UserTweets] 用户信息: @{screen_name} ({display_name}), "
        f"ID: {user_id}"
    )

    # 3. Determine subfolder: author_name/{display_name} or author_name/{screen_name}
    folder_label = _sanitize_folder_name(display_name) if display_name else screen_name
    if mode == "likes":
        subfolder = f"likes_author/{folder_label}"
    elif mode == "replies":
        subfolder = f"replies_author/{folder_label}"
    else:
        subfolder = f"status_author/{folder_label}"
    logger.info(f"[{mode_label}] 保存目录: {subfolder}")

    # 4. Load dedup index
    saved_ids = load_index()
    initial_count = len(saved_ids)
    is_force = force_refetch()
    logger.info(
        f"[UserTweets] 已有 {initial_count} 条推文索引"
        + (" (FORCE_REFETCH=true，将覆盖已有文件)" if is_force else "")
    )

    # 5. Date filtering config
    since_date = x_user_tweets_since()
    if since_date:
        logger.info(f"[UserTweets] 日期过滤: 仅抓取 {since_date} 之后的推文")

    # 6. Paginate UserTweets API
    all_tweet_entries = []
    cursor = None
    max_pages = x_user_tweet_max_pages()
    delay = x_user_tweet_delay()

    for page in range(max_pages):
        logger.info(f"[{mode_label}] 获取第 {page + 1} 页...")

        from feedgrab.fetchers.twitter_cookies import (
            fetch_with_cookie_rotation,
            count_total_accounts,
        )
        # Likes are readable by the account itself only: measured 2026-08-31,
        # a spare account gets `user.result.timeline: {}` for someone else's
        # likes, and even the primary account gets it for a third party. So
        # rotating can only ever lose the one account that might work.
        response, rotated_cookies = fetch_with_cookie_rotation(
            page_fetcher,
            user_id,
            label=mode_label,
            network_retry_delay=5.0,
            cursor=cursor,
            primary_only=(mode == "likes"),
        )
        # Update working cookies for subsequent pages (best-effort, rotation persists)
        if rotated_cookies:
            cookies = rotated_cookies
        if not response:
            total_accounts = count_total_accounts()
            logger.error(
                f"[{mode_label}] >>> 第 {page + 1} 页所有 {total_accounts} 个账号均失败 <<< "
                f"已抓取 {len(all_tweet_entries)} 条，停止分页"
            )
            break

        entries, cursors = parse_user_tweets_entries(response)
        if not entries:
            # v0.22.0: friendly message for likes/replies when content is private
            if mode == "likes" and page == 0:
                logger.warning(
                    f"[{mode_label}] >>> @{screen_name} 的喜欢列表为空 <<< "
                    f"X 已将点赞列表私有化：只有 @{screen_name} 本人的登录态能读到"
                    f"自己的 Likes，用别的账号（含本工具的主账号）抓必然返回空。"
                    f"若 @{screen_name} 就是你自己，请确认 sessions/twitter.json "
                    f"是该账号的登录态：feedgrab login twitter"
                )
            else:
                logger.info(
                    f"[{mode_label}] 第 {page + 1} 页返回空条目，分页结束 "
                    f"（API 服务端限制，累计 {len(all_tweet_entries)} 条）"
                )
            break

        # Date filter: check if any tweet on this page is too old
        stop_pagination = False
        page_entries = []

        for entry in entries:
            tweet_data = extract_tweet_data(entry)
            if not tweet_data:
                continue  # 非推文条目（推荐关注等），直接丢弃

            if since_date:
                tweet_date = _parse_tweet_date(tweet_data.get("created_at", ""))
                if tweet_date and tweet_date < since_date:
                    stop_pagination = True
                    continue  # skip this old tweet

            page_entries.append(entry)

        all_tweet_entries.extend(page_entries)
        logger.info(
            f"[UserTweets] 第 {page + 1} 页获取 {len(page_entries)} 条"
            f"（过滤 {len(entries) - len(page_entries)} 条），"
            f"累计 {len(all_tweet_entries)} 条"
        )

        if stop_pagination:
            logger.info("[UserTweets] 已到达日期过滤边界，停止分页")
            break

        # Next page
        cursor = cursors.get("bottom")
        if not cursor:
            logger.info(
                f"[UserTweets] 第 {page + 1} 页无下一页游标，分页结束 "
                f"（API 服务端限制，累计 {len(all_tweet_entries)} 条）"
            )
            break

    total = len(all_tweet_entries)
    logger.info(f"[UserTweets] 共获取 {total} 条推文条目")

    # Detect earliest tweet date + ID for Search API supplementary fetch
    earliest_tweet_date = ""
    earliest_tweet_id = ""
    if total > 0:
        for entry in all_tweet_entries:
            td = extract_tweet_data(entry)
            if td and td.get("created_at"):
                tweet_date = _parse_tweet_date(td["created_at"])
                if tweet_date and (not earliest_tweet_date or tweet_date < earliest_tweet_date):
                    earliest_tweet_date = tweet_date
                    earliest_tweet_id = td.get("rest_id", "")
        if earliest_tweet_date:
            logger.info(
                f"[UserTweets] UserTweets 最早推文日期: {earliest_tweet_date}"
                f" (ID: {earliest_tweet_id})"
            )

    if total == 0:
        return {
            "total": 0,
            "fetched": 0,
            "skipped": 0,
            "failed": 0,
            "list_path": "",
        }

    # 7. Pre-scan: build conversation map to deduplicate self-reply threads
    #    UserTweets returns both root tweets and self-replies as separate entries.
    #    Without this, a root tweet saves as "single" (1 tweet) and its self-reply
    #    triggers a full thread fetch that re-saves with the same title + hash suffix.
    conversation_counts = {}  # conversation_id -> count of entries
    for entry in all_tweet_entries:
        td = extract_tweet_data(entry)
        if td:
            conv_id = td.get("conversation_id", "")
            if conv_id:
                conversation_counts[conv_id] = conversation_counts.get(conv_id, 0) + 1

    # Identify conversation IDs that have multiple entries (i.e., threads)
    multi_entry_convs = {cid for cid, cnt in conversation_counts.items() if cnt > 1}
    if multi_entry_convs:
        logger.info(
            f"[UserTweets] 检测到 {len(multi_entry_convs)} 个多条目会话，"
            f"将跳过非根条目并升级根推文为线程处理"
        )

    # Track processed conversation IDs to avoid duplicate thread fetches
    processed_conv_ids = set()

    # 8. Process each tweet
    fetched = 0
    skipped = 0
    failed = 0
    tweet_list = []
    processed_ids = set()  # in-batch dedup

    for idx, entry in enumerate(all_tweet_entries):
        tweet_data = extract_tweet_data(entry)
        if not tweet_data:
            logger.debug(f"[UserTweets] [{idx + 1}/{total}] 无法解析条目，跳过")
            failed += 1
            tweet_list.append({
                "url": "",
                "tweet_id": "",
                "author": f"@{screen_name}",
                "author_name": display_name,
                "title": "",
                "status": "failed",
                "error": "无法解析推文数据",
            })
            continue

        tweet_id = tweet_data.get("id", "")
        author = tweet_data.get("author", screen_name)
        author_name = tweet_data.get("author_name", display_name)
        tweet_url = f"https://x.com/{author}/status/{tweet_id}"
        item_id = item_id_from_url(tweet_url)
        title_preview = _clean_title(tweet_data.get("text", "")[:80])
        published = _parse_tweet_date(tweet_data.get("created_at", ""))

        # Skip retweets (only original tweets)
        if tweet_data.get("_raw_result", {}).get("legacy", {}).get("retweeted_status_result"):
            logger.debug(f"[UserTweets] [{idx + 1}/{total}] 转推，跳过")
            skipped += 1
            tweet_list.append({
                "url": tweet_url,
                "tweet_id": tweet_id,
                "item_id": item_id,
                "author": f"@{author}",
                "author_name": author_name,
                "published": published,
                "title": title_preview,
                "status": "skipped",
                "error": "转推",
            })
            continue

        # Conversation dedup: skip non-root entries in multi-entry conversations
        conv_id = tweet_data.get("conversation_id", "")
        is_root = (conv_id == tweet_id) or not conv_id
        if conv_id in multi_entry_convs:
            if not is_root:
                # Non-root self-reply — skip, will be included in thread fetch of root
                logger.debug(
                    f"[UserTweets] [{idx + 1}/{total}] "
                    f"自回复（根推文将以线程方式处理），跳过"
                )
                skipped += 1
                tweet_list.append({
                    "url": tweet_url,
                    "tweet_id": tweet_id,
                    "item_id": item_id,
                    "author": f"@{author}",
                    "author_name": author_name,
                    "published": published,
                    "title": title_preview,
                    "status": "skipped",
                    "error": "自回复（线程内）",
                })
                continue
            elif conv_id in processed_conv_ids:
                # Root already processed via earlier entry
                logger.debug(
                    f"[UserTweets] [{idx + 1}/{total}] "
                    f"会话已处理，跳过"
                )
                skipped += 1
                tweet_list.append({
                    "url": tweet_url,
                    "tweet_id": tweet_id,
                    "item_id": item_id,
                    "author": f"@{author}",
                    "author_name": author_name,
                    "published": published,
                    "title": title_preview,
                    "status": "skipped",
                    "error": "会话已处理",
                })
                continue

        # In-batch dedup
        if tweet_id in processed_ids:
            logger.debug(f"[UserTweets] [{idx + 1}/{total}] 批内重复: {tweet_id}")
            skipped += 1
            tweet_list.append({
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
        if has_item(item_id, saved_ids) and not is_force:
            logger.debug(
                f"[UserTweets] [{idx + 1}/{total}] 已存在: "
                f"@{author} - {title_preview[:30]}"
            )
            skipped += 1
            tweet_list.append({
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

        # Upgrade root tweets in multi-entry conversations to "thread"
        if tweet_type == "single" and conv_id in multi_entry_convs:
            tweet_type = "thread"
            logger.debug(
                f"[UserTweets] [{idx + 1}/{total}] "
                f"根推文有自回复，升级为线程处理"
            )

        # Track processed conversation
        if conv_id:
            processed_conv_ids.add(conv_id)

        try:
            if tweet_type == "single":
                data = _build_single_tweet_data(tweet_data, tweet_url)
            elif tweet_type == "thread":
                logger.info(
                    f"[UserTweets] [{idx + 1}/{total}] "
                    f"线程推文，获取完整线程: @{author}"
                )
                try:
                    data = await _fetch_via_graphql(tweet_url, tweet_id)
                except Exception as thread_err:
                    logger.warning(f"[UserTweets] 线程获取失败 ({thread_err})，退化为单条保存")
                    data = _build_single_tweet_data(tweet_data, tweet_url)
                time.sleep(delay)
            elif tweet_type == "article":
                data = _build_single_tweet_data(tweet_data, tweet_url)
                article = tweet_data.get("article") or {}
                article_body = article.get("body", "")
                if article_body and len(article_body.strip()) > 200:
                    logger.info(
                        f"[UserTweets] [{idx + 1}/{total}] "
                        f"Article — GraphQL content_state: @{author}"
                    )
                    data["text"] = article_body
                    if data.get("thread_tweets"):
                        data["thread_tweets"][0]["text"] = article_body
                else:
                    logger.info(
                        f"[UserTweets] [{idx + 1}/{total}] "
                        f"长文章，Jina 获取正文: @{author}"
                    )
                    jina_content = _fetch_article_body(
                        tweet_url, article, author, "[UserTweets]"
                    )
                    if jina_content:
                        data["text"] = jina_content
                        if data.get("thread_tweets"):
                            data["thread_tweets"][0]["text"] = jina_content
                time.sleep(delay)
            else:
                data = _build_single_tweet_data(tweet_data, tweet_url)

            # P0-3: propagate pinned marker into data so from_twitter sees it
            # (thread/article paths re-fetch fresh data that loses _is_pinned)
            if tweet_data.get("is_pinned"):
                data["is_pinned"] = True

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
                    f"[UserTweets] 进度 [{idx + 1}/{total}] "
                    f"成功:{fetched} 跳过:{skipped} 失败:{failed}"
                )

        except Exception as e:
            error_msg = str(e)
            logger.warning(
                f"[UserTweets] [{idx + 1}/{total}] "
                f"失败: @{author} - {error_msg[:80]}"
            )
            failed += 1
            tweet_list.append({
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
    logger.info(f"[UserTweets] 索引更新: {initial_count} -> {len(saved_ids)} 条")

    # Search supplementary: fill historical gap beyond UserTweets limit
    from feedgrab.config import x_search_supplementary_enabled, twitterapi_io_key
    if (since_date
        and earliest_tweet_date
        and earliest_tweet_date > since_date
        and x_search_supplementary_enabled()):

        try:
            if twitterapi_io_key():
                # Paid API supplementary (no browser needed, server-friendly)
                logger.info(
                    f"[UserTweets] UserTweets 最早到 {earliest_tweet_date}，"
                    f"目标日期 {since_date}，启动 TwitterAPI.io 补充抓取"
                )
                from feedgrab.fetchers.twitter_api_user_tweets import fetch_api_supplementary
                search_result = await fetch_api_supplementary(
                    screen_name=screen_name,
                    display_name=display_name,
                    since_date=since_date,
                    earliest_tweet_date=earliest_tweet_date,
                    earliest_tweet_id=earliest_tweet_id,
                    subfolder=subfolder,
                    saved_ids=saved_ids,
                    is_force=is_force,
                )
            else:
                # Browser search supplementary (original behavior)
                logger.info(
                    f"[UserTweets] UserTweets 最早到 {earliest_tweet_date}，"
                    f"目标日期 {since_date}，启动浏览器搜索补充抓取"
                )
                from feedgrab.fetchers.twitter_search_tweets import fetch_search_supplementary
                search_result = await fetch_search_supplementary(
                    screen_name=screen_name,
                    display_name=display_name,
                    cookies=cookies,
                    since_date=since_date,
                    earliest_tweet_date=earliest_tweet_date,
                    subfolder=subfolder,
                    saved_ids=saved_ids,
                    is_force=is_force,
                )

            # Merge stats
            fetched += search_result.get("fetched", 0)
            skipped += search_result.get("skipped", 0)
            failed += search_result.get("failed", 0)
            total += search_result.get("total", 0)
            # Persist updated index
            save_index(saved_ids)
            logger.info(
                f"[UserTweets] 补充抓取完成，索引更新至 {len(saved_ids)} 条"
            )
        except Exception as e:
            logger.warning(f"[UserTweets] 补充抓取失败: {e}")

    # Save batch record
    list_path = _save_batch_record(tweet_list, screen_name, since_date)

    logger.info(
        f"[UserTweets] 批量抓取完成: "
        f"总计 {total}, 成功 {fetched}, 跳过 {skipped}, 失败 {failed}"
    )

    return {
        "total": total,
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "list_path": str(list_path),
    }
