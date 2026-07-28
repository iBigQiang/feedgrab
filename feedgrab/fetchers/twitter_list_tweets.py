# -*- coding: utf-8 -*-
"""
Twitter/X List tweets batch fetcher — fetch tweets from a List via GraphQL.

Supports:
    feedgrab https://x.com/i/lists/2002743803959300263

Design:
    - Fetch list metadata (name) via ListByRestId
    - Paginate ListLatestTweetsTimeline for tweets
    - Filter by date (X_LIST_TWEETS_DAYS, default 1 day)
    - Stream-save each tweet immediately
    - Reuses classification/processing from twitter_bookmarks
"""

import csv
import json
import re
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger
from typing import Dict, Any, List

from feedgrab.config import (
    x_list_tweet_max_pages,
    x_list_tweet_delay,
    x_list_tweets_days,
    x_list_tweets_summary,
    force_refetch,
    parse_twitter_date_local,
)
from feedgrab.fetchers.twitter_graphql import (
    fetch_list_by_rest_id,
    fetch_list_tweets_page,
    parse_list_tweets_entries,
    extract_tweet_data,
)
from feedgrab.fetchers.twitter_bookmarks import (
    _classify_tweet,
    _build_single_tweet_data,
    _fetch_article_body,
    _sanitize_folder_name,
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

def _parse_list_url(url: str) -> str:
    """Extract list ID from a Twitter list URL.

    Returns:
        List ID string (e.g. '2002743803959300263')

    Raises:
        ValueError if URL doesn't match expected pattern.
    """
    match = re.search(r'/i/lists/(\d+)', url)
    if match:
        return match.group(1)
    raise ValueError(f"无法从 URL 提取 List ID: {url}")


# ---------------------------------------------------------------------------
# Batch record persistence
# ---------------------------------------------------------------------------

def _get_list_index_dir() -> Path:
    """Return the index directory for list batch records."""
    from feedgrab.utils.dedup import get_index_path
    index_dir = get_index_path().parent
    index_dir.mkdir(parents=True, exist_ok=True)
    return index_dir


def _save_list_record(tweet_list: list, list_id: str, list_name: str) -> Path:
    """Save list batch record to a JSON file."""
    out_dir = _get_list_index_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = _sanitize_folder_name(list_name) if list_name else list_id
    filename = f"list_{label}_{ts}.json"
    path = out_dir / filename

    payload = {
        "fetched_at": datetime.now().isoformat(),
        "list_id": list_id,
        "list_name": list_name,
        "total": len(tweet_list),
        "tweets": tweet_list,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"[ListTweets] 批量记录已保存: {path}")
    return path


# ---------------------------------------------------------------------------
# Summary table generation
# ---------------------------------------------------------------------------

def _generate_list_summary(
    list_name: str,
    list_id: str,
    days: int,
    tweets: List[dict],
    saved_paths: Dict[str, str],
    output_dir: Path,
) -> Path:
    """Generate summary MD table + CSV for list tweets, sorted by views.

    MD: 内容摘要 uses Obsidian wikilink to the saved .md; 在线查看 links to x.com.
    CSV: explicit URL column, no wikilinks.

    Args:
        tweets: list of extract_tweet_data() dicts.
        saved_paths: mapping tweet_id -> saved .md file path.
        output_dir: directory for the summary files (same level as tweet .md files).

    Returns:
        Path to the generated .md summary file.
    """
    from feedgrab.fetchers.twitter import _clean_title

    date_str = datetime.now().strftime("%Y-%m-%d")
    tweets.sort(key=lambda td: int(td.get("views", 0) or 0), reverse=True)

    # --- Markdown ---
    lines = [
        "---",
        f'title: "列表推文汇总 — {list_name}"',
        f'list_id: "{list_id}"',
        f"days: {days}",
        f"total: {len(tweets)}",
        f"created: {date_str}",
        "cssclasses: wide",
        "---",
        "",
    ]

    if not tweets:
        lines.append("*No tweets found in this list.*")
    else:
        lines.append(
            "| # | 作者 | 内容摘要 | 日期 | 点赞 | 转帖 | 回复 | 查看 | 收藏 | 打开 |"
        )
        lines.append(
            "|:---:|------|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"
        )

        for i, td in enumerate(tweets, 1):
            author_name = td.get("author_name", "")
            handle = td.get("author", "")
            author = author_name if author_name else (f"@{handle}" if handle else "")
            if td.get("is_blue_verified"):
                author = f"\u2705{author}"
            author = author.replace("|", "\\|").replace("\n", " ").replace("\r", "")

            summary = _clean_title(td.get("text", ""), max_len=40)
            # Escape pipe and brackets for table safety
            summary = summary.replace("|", "\\|")
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

            # Content column: Obsidian wikilink if saved .md exists, else plain text
            saved = saved_paths.get(tweet_id, "")
            if saved:
                stem = Path(saved).stem
                # Strip chars that break [[...]] wikilink or table:
                # [ ] — break wikilink brackets
                # # — Obsidian heading anchor separator
                # ^ — Obsidian block reference separator
                # | — table column separator (already filtered by _sanitize_filename)
                safe_stem = stem.replace("[", "").replace("]", "").replace("#", "").replace("^", "").replace("|", "")
                summary_col = f"[[{safe_stem}]]"
            else:
                summary_col = summary

            # Online link column (rightmost)
            online_col = f"[查看]({tweet_url})"

            lines.append(
                f"| {i} | {author} | {summary_col} "
                f"| {date_short} | {likes} | {retweets} | {replies_count} "
                f"| {views} | {bookmarks} | {online_col} |"
            )

    summary_name = _sanitize_folder_name(list_name) or list_id
    summary_path = output_dir / f"{summary_name}_summary.md"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"[ListTweets] 汇总表格已保存: {summary_path}")

    # --- CSV ---
    csv_path = summary_path.with_suffix(".csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
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
            summary = _clean_title(td.get("text", ""), max_len=80)
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
            writer.writerow([
                i, author, summary, date_short, likes, retweets,
                replies_count, views, bookmarks, tweet_url,
            ])
    logger.info(f"[ListTweets] CSV 表格已保存: {csv_path}")

    return summary_path


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

async def fetch_list_tweets(
    list_url: str, cookies: dict
) -> Dict[str, Any]:
    """
    Batch-fetch tweets from a Twitter List.

    Flow:
        1. Parse list URL → list_id
        2. Fetch list metadata (name) via ListByRestId
        3. Calculate since_date from X_LIST_TWEETS_DAYS
        4. Paginate ListLatestTweetsTimeline, filter by date
        5. Process each tweet: classify → save
        6. Save index + batch record

    Args:
        list_url: Twitter list URL (e.g. https://x.com/i/lists/123)
        cookies: dict with 'auth_token' and 'ct0'

    Returns:
        {"total": N, "fetched": N, "skipped": N, "failed": N,
         "list_name": "...", "list_path": "..."}
    """
    from feedgrab.fetchers.twitter_fxtwitter import reset_circuit_breaker
    reset_circuit_breaker()

    list_id = _parse_list_url(list_url)
    logger.info(f"[ListTweets] List ID: {list_id}")

    # Step 1: Fetch list metadata
    list_info = fetch_list_by_rest_id(list_id, cookies)
    list_name = list_info.get("name", "")
    if list_name:
        logger.info(f"[ListTweets] 列表名称: {list_name} (成员数: {list_info.get('member_count', '?')})")
    else:
        logger.warning(f"[ListTweets] 无法获取列表名称，使用 ID 作为目录名")
        list_name = list_id

    # Step 2: Calculate date filter
    days = x_list_tweets_days()
    since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    logger.info(f"[ListTweets] 抓取最近 {days} 天的推文 (since: {since_date})")

    # Step 3: Determine output subfolder — lists_{N}day/{date}/{list_name}
    today = datetime.now().strftime("%Y%m%d")
    subfolder = f"lists_{days}day/{today}/{_sanitize_folder_name(list_name)}"

    # Step 4: Load dedup index
    saved_ids = load_index()

    # Step 5: Paginate and collect tweet entries
    max_pages = x_list_tweet_max_pages()
    all_tweet_entries = []
    cursor = None
    stop_pagination = False

    for page in range(max_pages):
        logger.info(f"[ListTweets] 分页 {page + 1}/{max_pages}...")
        from feedgrab.fetchers.twitter_cookies import (
            fetch_with_cookie_rotation,
            count_total_accounts,
        )
        response, rotated_cookies = fetch_with_cookie_rotation(
            fetch_list_tweets_page, list_id,
            label="ListTweets", cursor=cursor,
        )
        if rotated_cookies:
            cookies = rotated_cookies

        if not response:
            total_accounts = count_total_accounts()
            logger.warning(
                f"[ListTweets] >>> 第 {page + 1} 页所有 {total_accounts} 个账号均失败 <<< "
                f"已抓取 {len(all_tweet_entries)} 条，停止分页"
            )
            break

        entries, cursors = parse_list_tweets_entries(response)
        if not entries:
            logger.info(f"[ListTweets] 第 {page + 1} 页无推文条目，分页结束")
            break

        # Date filter: check each entry
        page_entries = []
        for entry in entries:
            tweet_data = extract_tweet_data(entry)
            if not tweet_data:
                continue
            created_at = tweet_data.get("created_at", "")
            if created_at:
                tweet_date = parse_twitter_date_local(created_at)
                if tweet_date and tweet_date < since_date:
                    stop_pagination = True
                    continue
            page_entries.append(entry)

        all_tweet_entries.extend(page_entries)
        logger.info(
            f"[ListTweets] 第 {page + 1} 页: {len(page_entries)} 条"
            f" (累计: {len(all_tweet_entries)})"
        )

        if stop_pagination:
            logger.info(f"[ListTweets] 遇到早于 {since_date} 的推文，停止分页")
            break

        cursor = cursors.get("bottom")
        if not cursor:
            logger.info("[ListTweets] 无 bottom cursor，分页结束")
            break

    logger.info(f"[ListTweets] 发现阶段完成: {len(all_tweet_entries)} 条推文")

    # Step 6: Pre-scan conversation groups for thread dedup
    # list-conversation-* modules contain root tweet + self-replies.
    # Without dedup, each self-reply triggers a separate full-thread fetch,
    # producing duplicate content. Only process root tweets of each conversation.
    conversation_counts = {}
    for entry in all_tweet_entries:
        td = extract_tweet_data(entry)
        if not td:
            continue
        conv_id = td.get("conversation_id", "")
        if conv_id:
            conversation_counts[conv_id] = conversation_counts.get(conv_id, 0) + 1
    multi_entry_convs = {cid for cid, cnt in conversation_counts.items() if cnt > 1}
    if multi_entry_convs:
        logger.info(
            f"[ListTweets] 检测到 {len(multi_entry_convs)} 个多条目会话，将只处理根推文"
        )

    # Step 7: Process each tweet
    from feedgrab.fetchers.twitter import _fetch_via_graphql
    from feedgrab.schema import from_twitter
    from feedgrab.utils.storage import save_to_markdown

    delay = x_list_tweet_delay()
    processed_ids = set()
    processed_conv_ids = set()
    fetched = 0
    skipped = 0
    failed = 0
    tweet_list = []
    collected_tweets: List[dict] = []
    saved_paths: Dict[str, str] = {}

    for i, entry in enumerate(all_tweet_entries):
        tweet_data = extract_tweet_data(entry)
        if not tweet_data:
            failed += 1
            continue

        tweet_id = tweet_data.get("id", "")
        author = tweet_data.get("author", "unknown")
        conv_id = tweet_data.get("conversation_id", "")
        tweet_url = f"https://x.com/{author}/status/{tweet_id}"

        # Batch dedup
        if tweet_id in processed_ids:
            skipped += 1
            continue
        processed_ids.add(tweet_id)

        # Conversation dedup: skip non-root tweets in multi-entry conversations
        if conv_id in multi_entry_convs:
            if conv_id in processed_conv_ids:
                # This conversation's root was already processed
                skipped += 1
                continue
            if conv_id != tweet_id:
                # Not the root tweet — skip (root has conv_id == tweet_id)
                skipped += 1
                continue
            # This is the root tweet — mark conversation as processed
            processed_conv_ids.add(conv_id)

        # Global dedup
        item_id = item_id_from_url(tweet_url)
        if has_item(item_id, saved_ids) and not force_refetch():
            skipped += 1
            tweet_list.append({"url": tweet_url, "status": "skipped"})
            continue

        tweet_type = _classify_tweet(tweet_data)
        # Force upgrade to thread if this is a root of multi-entry conversation
        if tweet_type == "single" and conv_id in multi_entry_convs:
            tweet_type = "thread"
        logger.info(
            f"[ListTweets] [{i + 1}/{len(all_tweet_entries)}] "
            f"@{author} ({tweet_type}) — {tweet_url}"
        )

        try:
            if tweet_type == "single":
                data = _build_single_tweet_data(tweet_data, tweet_url)
            elif tweet_type == "thread":
                try:
                    data = await _fetch_via_graphql(tweet_url, tweet_id)
                    if not data or not data.get("text"):
                        data = _build_single_tweet_data(tweet_data, tweet_url)
                except Exception as thread_err:
                    logger.warning(f"[ListTweets] 线程获取失败 ({thread_err})，退化为单条保存")
                    data = _build_single_tweet_data(tweet_data, tweet_url)
                _time.sleep(delay)
            elif tweet_type == "article":
                data = _build_single_tweet_data(tweet_data, tweet_url)
                article_info = tweet_data.get("article") or {}
                article_body = article_info.get("body", "")
                if article_body and len(article_body.strip()) > 200:
                    logger.info(f"[ListTweets] Article — GraphQL content_state: @{author}")
                    data["text"] = article_body
                    if data.get("thread_tweets"):
                        data["thread_tweets"][0]["text"] = article_body
                else:
                    jina_content = _fetch_article_body(
                        tweet_url,
                        article_info,
                        author,
                        "[ListTweets]",
                        source_text=tweet_data.get("text", ""),
                    )
                    if jina_content:
                        data["text"] = jina_content
                        if data.get("thread_tweets"):
                            data["thread_tweets"][0]["text"] = jina_content
                _time.sleep(delay)
            else:
                data = _build_single_tweet_data(tweet_data, tweet_url)

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

            add_item(item_id, tweet_url, saved_ids)
            fetched += 1
            tweet_list.append({"url": tweet_url, "status": "fetched"})
            collected_tweets.append(tweet_data)
            if saved_path:
                saved_paths[tweet_id] = saved_path

        except Exception as e:
            logger.error(f"[ListTweets] 处理失败 {tweet_url}: {e}")
            failed += 1
            tweet_list.append({"url": tweet_url, "status": f"failed: {e}"})

    # Step 8: Save index and batch record
    save_index(saved_ids)
    list_path = _save_list_record(tweet_list, list_id, list_name)

    # Step 9: Generate summary table if enabled
    summary_path = ""
    if x_list_tweets_summary() and collected_tweets:
        import os as _os
        vault = _os.getenv("OBSIDIAN_VAULT", "")
        out = _os.getenv("OUTPUT_DIR", "")
        base = vault or out
        if base:
            output_dir = Path(base) / "X" / subfolder
            summary_path = str(_generate_list_summary(
                list_name, list_id, days, collected_tweets, saved_paths, output_dir,
            ))

    total = fetched + skipped + failed
    logger.info(
        f"[ListTweets] 完成: 总数={total}, 成功={fetched}, "
        f"跳过={skipped}, 失败={failed}"
    )

    return {
        "total": total,
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "list_name": list_name,
        "list_path": str(list_path),
        "summary_path": summary_path,
    }
