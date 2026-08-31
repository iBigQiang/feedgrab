# -*- coding: utf-8 -*-
"""Twitter/X tweet-level user-list fetcher (v0.23.0).

Borrowed from prinsss/twitter-web-exporter (modules/retweeters/api.ts).

Supports two modes via a single unified fetcher:
    - retweeters  (x.com/<user>/status/<id>/retweets)
    - favoriters  (x.com/<user>/status/<id>/likes)

Output:
    - {OUTPUT_DIR}/X/users/{mode}/{tweet_id}_{date}.md  — table of users
    - {OUTPUT_DIR}/X/users/{mode}/{tweet_id}_{date}.csv — same data as CSV
"""

import csv
import os
import re
import time as _time
from datetime import datetime
from pathlib import Path
from loguru import logger
from typing import Optional, List, Dict, Any

from feedgrab.fetchers.twitter_graphql import (
    fetch_retweeters_page, fetch_favoriters_page,
    parse_retweeters_users, parse_favoriters_users,
    extract_user_data,
)


# primary_only marks the modes X restricts to the tweet's own author.
# Measured 2026-08-31 against a 12817-like tweet: Favoriters returns
# TimelineTerminateTimeline for every account that did not write the tweet,
# while Retweeters returns 20 entries for any logged-in account. So rotating
# helps Retweeters and can only lose the one account that might work for
# Favoriters.
_MODE_CONFIG = {
    "retweeters": {
        "fetcher": fetch_retweeters_page,
        "parser": parse_retweeters_users,
        "label": "转推者",
        "primary_only": False,
    },
    "favoriters": {
        "fetcher": fetch_favoriters_page,
        "parser": parse_favoriters_users,
        "label": "点赞者",
        "primary_only": True,
    },
}


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def parse_tweet_user_list_url(url: str) -> tuple:
    """Detect mode + tweet_id from a Twitter URL.

    Returns:
        (mode, tweet_id) or (None, None) if URL doesn't match.
    """
    # Subdomains must be tolerated: reader._detect_platform routes on
    # `"x.com" in domain`, so www./mobile. URLs reach us. Without the optional
    # subdomain group they parsed as (None, None) and the caller's primary-only
    # gate below silently fell through to rotation.
    # /status/<id>/retweets → retweeters
    m = re.search(
        r'https?://(?:[\w-]+\.)?(?:x|twitter)\.com/[^/]+/status/(\d+)/retweets/?',
        url,
    )
    if m:
        return ("retweeters", m.group(1))

    # /status/<id>/likes → favoriters (tweet-level likes, not user-level Likes)
    m = re.search(
        r'https?://(?:[\w-]+\.)?(?:x|twitter)\.com/[^/]+/status/(\d+)/likes/?',
        url,
    )
    if m:
        return ("favoriters", m.group(1))

    return (None, None)


def mode_requires_primary(mode: Optional[str]) -> bool:
    """Whether a tweet-user-list mode is restricted to the tweet's author.

    Single source of truth for callers (reader, CLI) so the policy lives with
    _MODE_CONFIG instead of being restated as `if mode == "favoriters"`.

    None means the URL did not parse as a tweet-user-list at all; that path
    ends in fetch_tweet_user_list's ValueError, so answer False and let the
    caller's generic cookie check run. A mode string we don't know errs toward
    the primary account: better to ask for the one login that might work than
    to fan a possibly-private read across the spares.
    """
    if mode is None:
        return False
    config = _MODE_CONFIG.get(mode)
    if config is None:
        return True
    return bool(config["primary_only"])


def extract_tweet_id(value: str) -> Optional[str]:
    """Accept either a numeric tweet_id or a Twitter URL containing /status/<id>."""
    if not value:
        return None
    if value.isdigit():
        return value
    m = re.search(r'/status/(\d+)', value)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _max_pages() -> int:
    try:
        return int(os.getenv("X_TWEET_USER_LIST_MAX_PAGES", "5"))
    except ValueError:
        return 5


def _delay_between_pages() -> float:
    try:
        return float(os.getenv("X_TWEET_USER_LIST_DELAY", "2.0"))
    except ValueError:
        return 2.0


def _per_page_count() -> int:
    try:
        return int(os.getenv("X_TWEET_USER_LIST_PER_PAGE", "40"))
    except ValueError:
        return 40


def _output_dir() -> Path:
    base = os.getenv("OUTPUT_DIR", "output").strip() or "output"
    return Path(base) / "X" / "users"


def _sanitize(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:80] or "unknown"


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------

async def fetch_tweet_user_list(url_or_id: str, cookies: dict) -> Dict[str, Any]:
    """Batch-fetch users who retweeted / liked a tweet.

    Args:
        url_or_id: Twitter URL (with /retweets or /likes suffix) OR pre-parsed
                   "<mode>:<tweet_id>" string for CLI use.
        cookies: Twitter session cookies.

    Returns:
        dict with mode, tweet_id, total, summary_path, csv_path.
    """
    # CLI may pass "<mode>:<tweet_id>"
    if ":" in url_or_id and not url_or_id.startswith("http"):
        mode, tweet_id = url_or_id.split(":", 1)
        if mode not in _MODE_CONFIG:
            raise ValueError(f"未知模式: {mode}（应为 retweeters/favoriters）")
    else:
        mode, tweet_id = parse_tweet_user_list_url(url_or_id)
        if not mode or not tweet_id:
            raise ValueError(f"无法识别推文用户列表 URL: {url_or_id}")

    cfg = _MODE_CONFIG[mode]
    fetcher = cfg["fetcher"]
    parser = cfg["parser"]
    label = cfg["label"]
    primary_only = cfg.get("primary_only", False)

    logger.info(f"[TweetUserList:{mode}] 开始抓取 {label}: tweet_id={tweet_id}")

    # --- Pagination loop ---
    all_users: List[Dict[str, Any]] = []
    seen_ids: set = set()
    cursor: Optional[str] = None
    max_pages = _max_pages()
    delay = _delay_between_pages()
    per_page = _per_page_count()

    for page in range(max_pages):
        from feedgrab.fetchers.twitter_cookies import (
            fetch_with_cookie_rotation,
            count_total_accounts,
        )
        response, rotated_cookies = fetch_with_cookie_rotation(
            fetcher, tweet_id,
            label=f"TweetUserList:{mode}",
            cursor=cursor, count=per_page,
            primary_only=primary_only,
        )
        if rotated_cookies:
            cookies = rotated_cookies
        if not response:
            total_accounts = count_total_accounts()
            logger.warning(
                f"[TweetUserList:{mode}] >>> 第 {page + 1} 页所有 {total_accounts} 个账号均失败 <<< "
                f"累计 {len(all_users)} 个，终止"
            )
            break

        entries, cursors = parser(response)
        if not entries:
            if mode == "favoriters" and page == 0:
                logger.warning(
                    f"[TweetUserList:{mode}] >>> 点赞者列表为空 <<< "
                    f"X 只允许推文作者本人查看谁点赞了自己的推文，"
                    f"抓别人的推文时任何账号都返回空。"
                    f"若 tweet={tweet_id} 是你自己发的，请确认 "
                    f"sessions/twitter.json 是该账号的登录态：feedgrab login twitter"
                )
            else:
                logger.info(
                    f"[TweetUserList:{mode}] 第 {page + 1} 页无更多条目，"
                    f"累计 {len(all_users)} 个"
                )
            break

        page_users = 0
        for entry in entries:
            ud = extract_user_data(entry)
            if not ud:
                continue
            uid = ud.get("user_id", "")
            if uid in seen_ids:
                continue
            seen_ids.add(uid)
            all_users.append(ud)
            page_users += 1

        logger.info(
            f"[TweetUserList:{mode}] 第 {page + 1} 页新增 {page_users} 个，"
            f"累计 {len(all_users)}"
        )

        cursor = cursors.get("bottom")
        if not cursor:
            logger.info(f"[TweetUserList:{mode}] 无下一页 cursor，分页结束")
            break

        if page < max_pages - 1:
            _time.sleep(delay)

    # Favoriters is author-only, not a per-tweet privacy toggle: X shows the
    # liker list to the tweet's author and to nobody else (measured against a
    # 12817-like third-party tweet — every account got an empty timeline).
    if not all_users and mode == "favoriters":
        logger.warning(
            f"[TweetUserList:{mode}] tweet={tweet_id} 未抓到点赞用户 — "
            f"X 只把点赞者列表展示给推文作者本人，抓别人的推文时任何账号都为空"
        )

    # --- Output ---
    summary_path, csv_path = _save_outputs(mode, label, tweet_id, all_users)

    logger.info(
        f"[TweetUserList:{mode}] 完成: tweet_id={tweet_id} — "
        f"{len(all_users)} 个用户"
    )

    return {
        "mode": mode,
        "tweet_id": tweet_id,
        "total": len(all_users),
        "fetched": len(all_users),
        "summary_path": str(summary_path),
        "csv_path": str(csv_path),
    }


# ---------------------------------------------------------------------------
# Output generation (MD + CSV)
# ---------------------------------------------------------------------------

def _save_outputs(
    mode: str, label: str, tweet_id: str,
    users: List[Dict[str, Any]],
) -> tuple:
    """Generate {OUTPUT_DIR}/X/users/{mode}/{tweet_id}_{date}.{md,csv}."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = _output_dir() / mode
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{_sanitize(tweet_id)}_{date_str}"
    md_path = out_dir / f"{stem}.md"
    csv_path = out_dir / f"{stem}.csv"

    # --- Markdown ---
    lines = [
        "---",
        f'title: "{label} — Tweet {tweet_id}"',
        f'mode: "{mode}"',
        f'tweet_id: "{tweet_id}"',
        f"total: {len(users)}",
        f"fetched_at: {date_str}",
        "cssclasses: wide",
        "---",
        "",
    ]

    if not users:
        lines.append(f"*未找到{label}。*")
    else:
        # Sort by followers_count desc (most influential first)
        users_sorted = sorted(
            users, key=lambda u: int(u.get("followers_count", 0) or 0),
            reverse=True,
        )

        lines.append(
            "| # | 用户 | 显示名 | 简介 | 关注者 | 关注 | 推文 | 蓝V | 链接 |"
        )
        lines.append(
            "|:---:|------|--------|------|:---:|:---:|:---:|:---:|:---:|"
        )

        for i, u in enumerate(users_sorted, 1):
            screen_name = u.get("screen_name", "")
            name = (u.get("name", "") or "").replace("|", "\\|").replace("\n", " ")
            bio = (u.get("description", "") or "").replace("|", "\\|").replace("\n", " ")
            bio = bio[:60] + "…" if len(bio) > 60 else bio
            bio = bio.replace("[", "\\[").replace("]", "\\]")
            blue = "✅" if u.get("is_blue_verified") else ""
            followers = int(u.get("followers_count", 0) or 0)
            friends = int(u.get("friends_count", 0) or 0)
            statuses = int(u.get("statuses_count", 0) or 0)
            user_url = u.get("url", "") or (
                f"https://x.com/{screen_name}" if screen_name else ""
            )
            link = f"[查看]({user_url})" if user_url else ""

            lines.append(
                f"| {i} | @{screen_name} | {name} | {bio} | "
                f"{followers} | {friends} | {statuses} | {blue} | {link} |"
            )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"[TweetUserList:{mode}] 汇总表保存: {md_path}")

    # --- CSV ---
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "user_id", "screen_name", "name", "description", "location",
            "followers_count", "friends_count", "statuses_count",
            "favourites_count", "listed_count",
            "verified", "is_blue_verified", "protected",
            "created_at", "url", "profile_image_url",
        ])
        for u in users:
            writer.writerow([
                u.get("user_id", ""),
                u.get("screen_name", ""),
                u.get("name", ""),
                u.get("description", ""),
                u.get("location", ""),
                u.get("followers_count", 0),
                u.get("friends_count", 0),
                u.get("statuses_count", 0),
                u.get("favourites_count", 0),
                u.get("listed_count", 0),
                u.get("verified", False),
                u.get("is_blue_verified", False),
                u.get("protected", False),
                u.get("created_at", ""),
                u.get("url", ""),
                u.get("profile_image_url", ""),
            ])
    logger.info(f"[TweetUserList:{mode}] CSV 保存: {csv_path}")

    return md_path, csv_path
