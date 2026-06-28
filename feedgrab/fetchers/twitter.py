# -*- coding: utf-8 -*-
"""
X/Twitter fetcher — six-tier fallback:

0.    GraphQL API (complete thread + media, requires cookie auth)
0.3   FxTwitter API (rich data, no auth, third-party public service)
0.5   Syndication API (text + media + metrics, no auth, single tweet only)
1.    X oEmbed API (fast, reliable for individual tweets, no login needed)
2.    Jina Reader (handles non-tweet X pages like profiles)
3.    Playwright + saved session (handles login-required content)

Install browser tier: pip install "feedgrab[browser]" && playwright install chromium
Save X session:       feedgrab login twitter
"""

import math
import os
import re
import requests
from loguru import logger
from typing import Dict, Any

from feedgrab.fetchers.jina import fetch_via_jina
from feedgrab.config import get_user_agent
from feedgrab.utils import http_client


OEMBED_URL = "https://publish.twitter.com/oembed"
SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"


def _try_fetch_article_body(data: Dict[str, Any], url: str, tier_label: str) -> None:
    """Detect article stub and fetch full body (mutates data in-place).

    Priority: GraphQL content_state (complete, no network call) → Jina (fallback).
    Used by both Tier 0 (GraphQL) and Tier 0.5 (Syndication) to supplement
    article tweets whose text is just a t.co link.
    """
    article_data = data.get("article_data") or {}
    has_article = article_data.get("has_content", False)
    text = data["text"].strip()
    # True article stubs are almost entirely a t.co link with minimal surrounding text.
    # Strip all t.co URLs and check remaining text length — real articles have <30 chars left.
    import re as _re
    text_without_urls = _re.sub(r'https?://t\.co/\S+', '', text).strip()
    text_is_stub = (
        "https://t.co/" in text or text.startswith("http")
    ) and len(text_without_urls) < 30
    # For multi-tweet threads, check first tweet individually
    if not text_is_stub and data.get("thread_tweets"):
        first_text = (data["thread_tweets"][0].get("text") or "").strip()
        first_without_urls = _re.sub(r'https?://t\.co/\S+', '', first_text).strip()
        text_is_stub = (
            "https://t.co/" in first_text or first_text.startswith("http")
        ) and len(first_without_urls) < 30
    is_article_stub = (has_article or text_is_stub) and not data.get("videos")
    if not is_article_stub:
        return

    # Priority 1: GraphQL content_state body (already parsed, no extra network call)
    article_body = article_data.get("body", "")
    if article_body and len(article_body.strip()) > 200:
        logger.info(f"{tier_label} Article detected — using GraphQL content_state body")
        data["text"] = article_body
        if data.get("thread_tweets"):
            data["thread_tweets"][0]["text"] = article_body
        return

    # Priority 2: Jina Reader fallback (for Syndication tier or missing content_state)
    logger.info(f"{tier_label} Article detected — fetching body via Jina")
    from feedgrab.fetchers.twitter_bookmarks import _fetch_article_body
    article_info = data.get("article_data") or {}
    tweet_author = (data.get("author") or "").lstrip("@")
    jina_content = _fetch_article_body(
        url, article_info, tweet_author, tier_label
    )
    if jina_content:
        data["text"] = jina_content
        if data.get("thread_tweets"):
            data["thread_tweets"][0]["text"] = jina_content

# Sentence-ending punctuation for smart title truncation
_SENTENCE_ENDS = set("。！？.!?")


def _clean_title(text: str, max_len: int = 50) -> str:
    """Clean and smart-truncate text for use as a title.

    - Strip Markdown formatting (bold/italic markers)
    - Strip newlines, tabs, control chars; collapse whitespace
    - If within max_len, return as-is
    - Otherwise prefer cutting at last sentence-ending punctuation
    """
    # Strip Markdown bold/italic markers for clean title
    text = re.sub(r'\*{1,3}', '', text)
    # Remove newlines, tabs, control chars; collapse whitespace
    text = re.sub(r'[\r\n\t]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= max_len:
        return text
    # Look for last sentence-ending punctuation within max_len
    candidate = text[:max_len]
    for i in range(len(candidate) - 1, max_len // 3 - 1, -1):
        if candidate[i] in _SENTENCE_ENDS:
            return candidate[:i + 1]
    return candidate


def _extract_author(url: str) -> str:
    """Extract @username from tweet URL."""
    match = re.search(r'x\.com/(\w+)/status', url)
    return f"@{match.group(1)}" if match else ""


def _clean_jina_twitter_title(raw_title: str) -> tuple[str, str]:
    """Extract clean title and author display name from Jina page title.

    Jina returns page titles like:
      'Title: 鱼总聊AI on X: "OpenClaw新手完整学习路径-更适合新手食用的学习+使用教程" / X'
      '鱼总聊AI on X: "OpenClaw新手完整学习路径" / X'

    Returns:
        (clean_title, author_name) — e.g. ("OpenClaw新手...", "鱼总聊AI")
    """
    title = raw_title.strip()
    # Strip "Title: " prefix
    if title.lower().startswith("title:"):
        title = title[6:].strip()

    # Try to match pattern: {author} on X: "{actual_title}" / X
    m = re.match(r'(.+?)\s+on\s+X[:\s]*["\u201c](.+?)["\u201d]\s*/\s*X$', title)
    if m:
        return m.group(2).strip(), m.group(1).strip()

    # Fallback: strip trailing " / X" or " - X"
    title = re.sub(r'\s*[/\-]\s*X\s*$', '', title)
    # Strip leading "Title: "
    title = re.sub(r'^Title:\s*', '', title, flags=re.IGNORECASE)
    return title.strip(), ""


def _extract_tweet_id(url: str) -> str:
    """Extract numeric tweet ID from URL."""
    match = re.search(r'x\.com/\w+/status/(\d+)', url)
    return match.group(1) if match else ""


def _is_tweet_url(url: str) -> bool:
    """Check if this is a direct tweet/status URL (vs profile or other X page)."""
    return bool(re.search(r'x\.com/\w+/status/\d+', url))


def _is_graphql_enabled() -> bool:
    """Check if GraphQL tier is enabled via env config."""
    return os.getenv("X_GRAPHQL_ENABLED", "true").lower() in ("true", "1", "yes")


def _is_graphql_auth_error(error: str) -> bool:
    """Return whether a GraphQL failure means the current cookie should be skipped."""
    lowered = error.lower()
    return "401" in error or "403" in error or "unauthorized" in lowered


def _is_graphql_rate_limit_error(error: str) -> bool:
    """Return whether a GraphQL failure means the current cookie is rate limited."""
    lowered = error.lower()
    return "429" in error or "rate limit" in lowered or "rate limited" in lowered


# ---------------------------------------------------------------------------
# Tier 0: GraphQL API (new — ported from baoyu)
# ---------------------------------------------------------------------------

async def _fetch_via_graphql(url: str, tweet_id: str, cookies: dict = None) -> Dict[str, Any]:
    """
    Fetch tweet/thread via X's private GraphQL API.

    Returns complete thread data with media, quoted tweets, etc.
    Requires valid auth cookies (passed in or loaded automatically).
    """
    from feedgrab.fetchers.twitter_cookies import load_twitter_cookies, has_required_cookies
    from feedgrab.fetchers.twitter_thread import fetch_tweet_thread
    from feedgrab.fetchers.twitter_graphql import fetch_tweet_detail, extract_tweet_data, parse_tweet_entries

    if cookies is None:
        cookies = load_twitter_cookies()
    if not has_required_cookies(cookies):
        raise RuntimeError("No valid Twitter cookies for GraphQL")

    # Try thread fetch first (gets complete author self-reply chain)
    thread = fetch_tweet_thread(tweet_id, cookies)

    if thread and thread.get("tweets"):
        tweets = thread["tweets"]
        root = thread.get("root_tweet", tweets[0])
        author = thread.get("author", "")

        # Build result with thread data + metrics from root tweet
        # For Twitter Articles, prefer article title over tweet text (which is just a t.co link)
        article = root.get("article") or {}
        title = article.get("title") or root.get("text", "")
        title = _clean_title(title)

        return {
            "text": _join_thread_text(tweets),
            "author": f"@{author}" if author else "",
            "author_name": thread.get("author_name", root.get("author_name", "")),
            "url": url,
            "title": title,
            "platform": "twitter",
            "thread_tweets": tweets,
            "has_thread": len(tweets) > 1,
            "article_data": article,
            "likes": root.get("likes", 0),
            "retweets": root.get("retweets", 0),
            "replies": root.get("replies", 0),
            "bookmarks": root.get("bookmarks", 0),
            "views": root.get("views", "0"),
            "created_at": root.get("created_at", ""),
            "images": [img for t in tweets for img in t.get("images", [])],
            "videos": [v for t in tweets for v in t.get("videos", [])],
            "hashtags": list(dict.fromkeys(
                tag for t in tweets for tag in t.get("hashtags", [])
            )),
            "author_replies": thread.get("author_replies", []),
            "comments": thread.get("comments", []),
            # New metadata from root tweet
            "quote_count": root.get("quote_count", 0),
            "lang": root.get("lang", ""),
            "source_app": root.get("source_app", ""),
            "possibly_sensitive": root.get("possibly_sensitive", False),
            "is_blue_verified": root.get("is_blue_verified", False),
            "followers_count": root.get("followers_count", 0),
            "statuses_count": root.get("statuses_count", 0),
            "listed_count": root.get("listed_count", 0),
            # v0.23.0: ModeratedTimeline (opt-in via X_FETCH_MODERATED_REPLIES)
            "moderated_replies": thread.get("moderated_replies", []),
            "has_moderated_replies": thread.get("has_moderated_replies", False),
        }

    # Fallback: single tweet via TweetDetail
    response = fetch_tweet_detail(tweet_id, cookies)
    if response:
        entries = parse_tweet_entries(response)
        for entry in entries:
            tweet_data = extract_tweet_data(entry)
            if tweet_data and tweet_data.get("id") == tweet_id:
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
                    # New metadata
                    "quote_count": tweet_data.get("quote_count", 0),
                    "lang": tweet_data.get("lang", ""),
                    "source_app": tweet_data.get("source_app", ""),
                    "possibly_sensitive": tweet_data.get("possibly_sensitive", False),
                    "is_blue_verified": tweet_data.get("is_blue_verified", False),
                    "followers_count": tweet_data.get("followers_count", 0),
                    "statuses_count": tweet_data.get("statuses_count", 0),
                    "listed_count": tweet_data.get("listed_count", 0),
                }

    raise RuntimeError("GraphQL returned no usable data")


def _join_thread_text(tweets: list) -> str:
    """Join thread tweets into a single text with numbering.

    First tweet (main post) has no prefix; subsequent tweets numbered [1/N]...[N/N].
    """
    if len(tweets) == 1:
        return tweets[0].get("text", "")

    parts = []
    rest_count = len(tweets) - 1
    for i, tweet in enumerate(tweets):
        text = tweet.get("text", "").strip()
        if not text:
            continue
        if i == 0:
            parts.append(text)
        else:
            parts.append(f"[{i}/{rest_count}] {text}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tier 0.5: Syndication API (free, no auth, richer than oEmbed)
# ---------------------------------------------------------------------------

def _syndication_token(tweet_id: str) -> str:
    """Calculate syndication API token.

    JS original: ((id / 1e15) * Math.PI).toString(36).replace(/(0+|\\.)/g, '')
    Token validation is not strict — server accepts longer strings.
    """
    num = (int(tweet_id) / 1e15) * math.pi
    # Convert integer part to base-36
    n = int(num)
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    int_str = "0" if n == 0 else ""
    while n > 0:
        int_str = digits[n % 36] + int_str
        n //= 36
    # Convert fractional part to base-36 (15 digits for double precision)
    frac = num - int(num)
    frac_chars = []
    for _ in range(15):
        frac *= 36
        d = int(frac)
        frac_chars.append(digits[d])
        frac -= d
    b36 = f"{int_str}.{''.join(frac_chars)}"
    return re.sub(r"[0.]", "", b36)


def _fetch_via_syndication(url: str, tweet_id: str) -> Dict[str, Any]:
    """Fetch tweet via Twitter's Syndication API.

    Free, no auth required. Returns text, metrics, media, user info.
    Richer than oEmbed but no thread/quoted tweet/views/bookmarks/note_tweet.
    """
    token = _syndication_token(tweet_id)
    resp = http_client.get(
        SYNDICATION_URL,
        params={"id": tweet_id, "token": token},
        headers={"User-Agent": get_user_agent()},
        timeout=15,
    )
    if resp.status_code == 404:
        raise RuntimeError(f"Syndication: tweet {tweet_id} not found")
    http_client.raise_for_status(resp)
    if not resp.text.strip():
        raise RuntimeError(f"Syndication: empty response for {tweet_id}")

    data = resp.json()
    if data.get("__typename") == "TweetTombstone":
        raise RuntimeError("Syndication: tweet is tombstoned")
    if not data.get("text"):
        raise RuntimeError("Syndication: no text in response")

    # Parse text and expand t.co URLs
    text = data.get("text", "")
    for url_ent in data.get("entities", {}).get("urls", []):
        short = url_ent.get("url", "")
        expanded = url_ent.get("expanded_url", "")
        if short and expanded and short in text:
            text = text.replace(short, expanded)
    # Remove trailing media t.co URLs
    for media in data.get("entities", {}).get("media", []):
        short = media.get("url", "")
        if short and short in text:
            text = text.replace(short, "").strip()

    # User info
    user = data.get("user", {})
    screen_name = user.get("screen_name", "")
    display_name = user.get("name", "")

    # Media extraction
    images = []
    videos = []
    for media in data.get("mediaDetails", []):
        media_type = media.get("type", "")
        if media_type == "photo":
            images.append(media.get("media_url_https", ""))
        elif media_type in ("video", "animated_gif"):
            variants = media.get("video_info", {}).get("variants", [])
            mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
            if mp4s:
                best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                videos.append(best.get("url", ""))
            images.append(media.get("media_url_https", ""))

    # Hashtags
    hashtags = [
        h.get("text", "")
        for h in data.get("entities", {}).get("hashtags", [])
        if h.get("text")
    ]

    # Cover image: article cover > first photo > first mediaDetail
    cover_image = ""
    article = data.get("article") or {}
    article_cover = (article.get("cover_media") or {}).get("media_info") or {}
    if article_cover.get("original_img_url"):
        cover_image = article_cover["original_img_url"]
    elif images:
        cover_image = images[0]

    # Article data (for downstream article body fetching)
    article_data = {}
    if article:
        article_data = {
            "id": article.get("rest_id", ""),
            "title": article.get("title", ""),
            "cover_image": cover_image,
            "has_content": bool(article.get("preview_text")),
        }

    # Build tweet dict compatible with Markdown renderer
    tweet_data = {
        "id": data.get("id_str", tweet_id),
        "text": text,
        "images": images,
        "videos": videos,
        "quoted_tweet": None,
        "hashtags": hashtags,
    }

    # Use article title if available (article tweets have t.co stub as text)
    display_title = article.get("title") or text
    title = _clean_title(display_title)

    return {
        "text": text,
        "author": f"@{screen_name}" if screen_name else "",
        "author_name": display_name,
        "url": url,
        "title": title,
        "platform": "twitter",
        "thread_tweets": [tweet_data],
        "has_thread": False,
        "article_data": article_data,
        "likes": data.get("favorite_count", 0),
        "retweets": 0,  # not available via Syndication
        "replies": data.get("conversation_count", 0),
        "bookmarks": 0,  # not available via Syndication
        "views": "0",  # not available via Syndication
        "created_at": data.get("created_at", ""),
        "images": images,
        "videos": videos,
        "hashtags": hashtags,
        "cover_image": cover_image,
    }


# ---------------------------------------------------------------------------
# Tier 1: oEmbed API (original)
# ---------------------------------------------------------------------------

def _fetch_via_oembed(url: str) -> Dict[str, Any]:
    """
    Fetch tweet text via X's oEmbed API.
    Free, reliable, no auth needed. Works for public tweets.
    Note: oEmbed requires twitter.com URLs (not x.com).
    """
    oembed_query_url = url.replace("x.com", "twitter.com")
    resp = http_client.get(
        OEMBED_URL,
        params={"url": oembed_query_url, "omit_script": "true"},
        timeout=10,
    )
    http_client.raise_for_status(resp)
    data = resp.json()

    html = data.get("html", "")
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()

    return {
        "text": text,
        "author": data.get("author_name", ""),
        "author_url": data.get("author_url", ""),
        "title": text[:100] if text else "",
    }


# ---------------------------------------------------------------------------
# Tier 3: Playwright (original)
# ---------------------------------------------------------------------------

async def _fetch_via_playwright(url: str) -> Dict[str, Any]:
    """
    Fetch tweet via Playwright with X-specific DOM selectors.
    Uses saved login session if available (~/.feedgrab/sessions/twitter.json).
    """
    from feedgrab.fetchers.browser import (
        get_async_playwright, get_session_path,
        stealth_launch, get_stealth_context_options,
        setup_resource_blocking,
    )
    from pathlib import Path

    session_path = get_session_path("twitter")
    has_session = Path(session_path).exists()
    if has_session:
        logger.info(f"Using saved X session: {session_path}")

    pw_cls = get_async_playwright()
    async with pw_cls() as p:
        browser = await stealth_launch(p, headless=True)

        ctx_opts = get_stealth_context_options()
        if has_session:
            ctx_opts["storage_state"] = session_path

        context = await browser.new_context(**ctx_opts)
        await setup_resource_blocking(context)
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

            try:
                await page.wait_for_selector(
                    '[data-testid="tweetText"]', timeout=10_000
                )
            except Exception:
                pass

            tweet_text = await page.evaluate("""() => {
                const tweetEl = document.querySelector('[data-testid="tweetText"]');
                if (tweetEl) return tweetEl.innerText;
                const article = document.querySelector('article');
                if (article) return article.innerText;
                const main = document.querySelector('main');
                if (main) return main.innerText;
                return '';
            }""")

            title = await page.title()

            return {
                "text": (tweet_text or "").strip(),
                "title": (title or "").strip()[:200],
            }
        finally:
            await context.close()
            await browser.close()


# ---------------------------------------------------------------------------
# Main dispatcher — five-tier fallback
# ---------------------------------------------------------------------------

async def fetch_twitter(url: str) -> Dict[str, Any]:
    """
    Fetch a tweet or X post with six-tier fallback.

    Tier 0:   GraphQL API (needs cookie, most complete — thread + media)
    Tier 0.3: FxTwitter API (no auth, rich data — stats + media + article)
    Tier 0.5: Syndication API (free, no auth, text + metrics + media)
    Tier 1:   oEmbed API (free, no auth, single tweet text only)
    Tier 2:   Jina Reader (no auth, handles profiles/non-tweet pages)
    Tier 3:   Playwright browser (last resort, handles login-required content)

    Logic:
        - Has cookies + is tweet URL → try Tier 0 first (GraphQL)
        - GraphQL fails → try Tier 0.3 (FxTwitter, unless circuit-broken)
        - No cookies → skip Tier 0, try Tier 0.3 then Tier 0.5
        - All fail → auto-degrade to Tier 1/2/3

    Args:
        url: Tweet URL (x.com or twitter.com)

    Returns:
        Dict with: text, author, url, title, platform,
        and optionally: thread_tweets, has_thread (from Tier 0/0.3/0.5)
    """
    url = url.replace("twitter.com", "x.com")
    author = _extract_author(url)
    tweet_id = _extract_tweet_id(url)

    # Tier 0: GraphQL (needs cookie auth, most complete)
    if tweet_id and _is_graphql_enabled():
        try:
            from feedgrab.fetchers.twitter_cookies import (
                activate_twitter_cookie,
                has_required_cookies,
                is_twitter_cookie_rate_limited,
                load_all_twitter_cookie_sets,
            )

            cookie_sets = [
                (source_label, cookies)
                for source_label, cookies in load_all_twitter_cookie_sets()
                if has_required_cookies(cookies)
                and not is_twitter_cookie_rate_limited(cookies)
            ]
            if cookie_sets:
                import time as _time

                max_attempts = 1 if len(cookie_sets) > 1 else 4
                for source_label, cookies in cookie_sets:
                    activate_twitter_cookie(cookies)
                    account_key = cookies.get("auth_token", "")[:8]
                    logger.info(
                        f"[Twitter] Tier 0 — GraphQL ({source_label}, {account_key}...): {url}"
                    )
                    data = None
                    last_error = None
                    for attempt in range(max_attempts):
                        try:
                            data = await _fetch_via_graphql(url, tweet_id, cookies=cookies)
                            if data and data.get("text"):
                                break
                            last_error = "empty response"
                        except Exception as gql_err:
                            last_error = str(gql_err)
                            data = None
                            if (
                                _is_graphql_auth_error(last_error)
                                or _is_graphql_rate_limit_error(last_error)
                            ):
                                logger.warning(
                                    f"[Twitter] GraphQL Cookie {source_label} "
                                    f"不可用({last_error})，尝试下一组 Cookie"
                                )
                                break

                        if attempt < max_attempts - 1:
                            logger.warning(
                                f"[Twitter] GraphQL {source_label} 失败({last_error})，"
                                f"5秒后第 {attempt + 1}/{max_attempts - 1} 次重试..."
                            )
                            _time.sleep(5)

                    if data and data.get("text"):
                        _try_fetch_article_body(data, url, "[Twitter]")
                        return data

                    logger.warning(
                        f"[Twitter] GraphQL {source_label} 未拿到有效数据"
                        f"({last_error or 'unknown'})，尝试下一组 Cookie"
                    )

                logger.warning("[Twitter] 所有 GraphQL Cookie 均失败，降级到 FxTwitter")
            else:
                logger.warning(
                    "\n"
                    "+--------------------------------------------------+\n"
                    "|  Twitter Cookie 未配置 - 无法获取完整数据        |\n"
                    "+--------------------------------------------------+\n"
                    "|  缺少 cookie 将导致:                             |\n"
                    "|  - 无法获取 likes/views/bookmarks 等指标         |\n"
                    "|  - 无法获取作者回帖和评论                        |\n"
                    "|  - 仅能获取基础正文内容                          |\n"
                    "+--------------------------------------------------+\n"
                    "|  配置方法 (任选其一):                            |\n"
                    "|  1. feedgrab login twitter                       |\n"
                    "|  2. .env 设置 X_AUTH_TOKEN + X_CT0               |\n"
                    "|  3. 手动写入 sessions/x.json                     |\n"
                    "+--------------------------------------------------+"
                )
        except Exception as e:
            err_msg = str(e)
            if "401" in err_msg or "403" in err_msg or "unauthorized" in err_msg.lower():
                logger.warning(
                    "[Twitter] Cookie 已过期。请运行：feedgrab login twitter\n"
                    "  正在回退到受限模式（无互动指标）..."
                )
            else:
                logger.warning(f"[Twitter] GraphQL failed ({e}), falling back")

    # Tier 0.3: FxTwitter API (rich data, no auth, third-party service)
    if tweet_id and _is_tweet_url(url):
        from feedgrab.fetchers.twitter_fxtwitter import (
            fetch_via_fxtwitter, is_circuit_broken,
        )
        if not is_circuit_broken():
            try:
                logger.info(f"[Twitter] Tier 0.3 — FxTwitter: {url}")
                data = fetch_via_fxtwitter(url, tweet_id)
                if data and data.get("text"):
                    _try_fetch_article_body(data, url, "[Twitter]")
                    return data
                logger.warning("[Twitter] FxTwitter returned empty data")
            except Exception as e:
                logger.warning(f"[Twitter] FxTwitter failed ({e})")
        else:
            logger.debug("[Twitter] FxTwitter skipped (circuit breaker active)")

    # Tier 0.5: Syndication API (richer than oEmbed, no auth)
    if tweet_id and _is_tweet_url(url):
        try:
            logger.info(f"[Twitter] Tier 0.5 — Syndication: {url}")
            data = _fetch_via_syndication(url, tweet_id)
            if data and data.get("text"):
                _try_fetch_article_body(data, url, "[Twitter]")
                return data
            logger.warning("[Twitter] Syndication returned empty data")
        except Exception as e:
            logger.warning(f"[Twitter] Syndication failed ({e})")

    # Tier 1: oEmbed API (best for individual tweets, no auth)
    if _is_tweet_url(url):
        try:
            logger.info(f"[Twitter] Tier 1 — oEmbed: {url}")
            data = _fetch_via_oembed(url)
            text = (data.get("text") or "").strip()
            thin_oembed = (
                len(text) <= 20
                or text.lower().startswith("https://t.co/")
                or ("&mdash;" in text and text.count("https://t.co/") >= 1)
            )
            if not thin_oembed:
                return {
                    "text": text,
                    "author": author or data.get("author", ""),
                    "url": url,
                    "title": data.get("title", ""),
                    "platform": "twitter",
                }
            logger.warning("[Twitter] oEmbed returned thin content")
        except Exception as e:
            logger.warning(f"[Twitter] oEmbed failed ({e})")

    # Tier 2: Jina Reader (handles profiles, threads, non-tweet pages)
    try:
        logger.info(f"[Twitter] Tier 2 — Jina: {url}")
        data = fetch_via_jina(url)
        content = data.get("content", "")
        title = data.get("title", "")
        jina_ok = (
            content
            and len(content.strip()) > 100
            and "not yet fully loaded" not in content.lower()
            and title.lower() not in ("x", "title: x", "")
        )
        if jina_ok:
            clean_title, jina_author_name = _clean_jina_twitter_title(title)
            return {
                "text": content,
                "author": author,
                "author_name": jina_author_name,
                "url": url,
                "title": clean_title,
                "platform": "twitter",
            }
        logger.warning("[Twitter] Jina returned unusable content")
    except Exception as e:
        logger.warning(f"[Twitter] Jina failed ({e})")

    # Tier 3: Playwright + session with X-specific extraction
    try:
        logger.info(f"[Twitter] Tier 3 — Playwright: {url}")
        data = await _fetch_via_playwright(url)
        content = data.get("text", "")
        if content and len(content.strip()) > 20:
            return {
                "text": content,
                "author": author,
                "url": url,
                "title": data.get("title", ""),
                "platform": "twitter",
            }
        logger.warning("[Twitter] Playwright returned empty content")
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"[Twitter] 所有抓取方式都失败：{e}")

    raise RuntimeError(
        f"❌ Twitter/X 抓取所有方式都失败：{url}\n"
        "   可尝试运行：feedgrab login twitter（保存浏览器兜底登录态）\n"
        f"   然后重试：feedgrab {url}"
    )
