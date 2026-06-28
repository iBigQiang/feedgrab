# -*- coding: utf-8 -*-
"""
FxTwitter API fetcher — free, no auth, rich data.

Uses the FxTwitter public API (api.fxtwitter.com) as a fallback tier.
Returns structured JSON with full text, stats, media, author profile,
quoted tweets, and Article content (Draft.js blocks).

Limitations:
    - No thread expansion (only returns root tweet)
    - No blue_verified / listed_count
    - Third-party service dependency (may go down)
"""

import re
import json
import requests
from loguru import logger
from typing import Dict, Any, Optional

from feedgrab.config import get_user_agent
from feedgrab.utils import http_client

# Module-level circuit breaker for batch mode
_consecutive_failures: int = 0
_circuit_broken: bool = False
_CIRCUIT_BREAK_THRESHOLD = 3


def reset_circuit_breaker():
    """Reset the FxTwitter circuit breaker (call at task start)."""
    global _consecutive_failures, _circuit_broken
    _consecutive_failures = 0
    _circuit_broken = False


def is_circuit_broken() -> bool:
    """Check if FxTwitter has been disabled due to consecutive failures."""
    return _circuit_broken


def _record_success():
    """Record a successful FxTwitter call."""
    global _consecutive_failures
    _consecutive_failures = 0


def _record_failure():
    """Record a failed FxTwitter call, trigger circuit break if threshold reached."""
    global _consecutive_failures, _circuit_broken
    _consecutive_failures += 1
    if _consecutive_failures >= _CIRCUIT_BREAK_THRESHOLD:
        _circuit_broken = True
        logger.warning(
            f"[FxTwitter] {_CIRCUIT_BREAK_THRESHOLD} consecutive failures — "
            f"circuit breaker activated, skipping FxTwitter for this task"
        )


def _parse_tweet_url(url: str) -> tuple:
    """Extract (username, tweet_id) from X/Twitter URL."""
    match = re.search(r'(?:x\.com|twitter\.com)/([a-zA-Z0-9_]{1,15})/status/(\d+)', url)
    if match:
        return match.group(1), match.group(2)
    return None, None


def _render_article_body(article: dict) -> str:
    """Render FxTwitter article content blocks to Markdown.

    FxTwitter returns Draft.js-compatible blocks in article.content.blocks.
    This reuses the same rendering logic as our GraphQL content_state renderer.
    """
    content = article.get("content", {})
    blocks = content.get("blocks", [])
    if not blocks:
        return ""

    # Build entityMap lookup (FxTwitter may include it in content)
    raw_em = content.get("entityMap", {})
    if isinstance(raw_em, list):
        entity_map = {str(item["key"]): item["value"] for item in raw_em if "key" in item}
    elif isinstance(raw_em, dict):
        entity_map = raw_em
    else:
        entity_map = {}

    parts = []
    for block in blocks:
        btype = block.get("type", "unstyled")
        text = block.get("text", "")
        entity_ranges = block.get("entityRanges", [])

        if btype == "atomic":
            # Image or embed — check entityRanges
            if entity_ranges and entity_map:
                for er in entity_ranges:
                    ent_key = str(er.get("key", ""))
                    ent = entity_map.get(ent_key, {})
                    ent_type = ent.get("type", "")
                    if ent_type == "LINK":
                        url = ent.get("data", {}).get("url", "")
                        if url and text:
                            parts.append(f"[{text}]({url})")
            elif text:
                parts.append(text)
            continue

        if not text:
            continue

        # Apply LINK entities then inline styles
        text = _apply_fxtwitter_inline(text, entity_ranges, entity_map, block)

        if btype == "header-two":
            parts.append(f"## {text}")
        elif btype == "header-three":
            parts.append(f"### {text}")
        elif btype == "blockquote":
            for line in text.split("\n"):
                parts.append(f"> {line}")
        elif btype == "ordered-list-item":
            parts.append(f"1. {text}")
        elif btype == "unordered-list-item":
            parts.append(f"- {text}")
        elif btype == "code-block":
            parts.append(f"````\n{text}\n````")
        else:
            # unstyled = normal paragraph
            parts.append(text)

    return "\n\n".join(parts)


def _apply_fxtwitter_inline(text: str, entity_ranges: list, entity_map: dict, block: dict) -> str:
    """Apply LINK entities and inline styles for FxTwitter article blocks."""
    # Collect all entities that carry a URL (LINK, MENTION, etc.)
    link_ops = []
    for er in entity_ranges:
        ent_key = str(er.get("key", ""))
        ent = entity_map.get(ent_key, {})
        ent_data = ent.get("data", {})
        url = ent_data.get("url") or ent_data.get("href") or ent_data.get("value") or ""
        if url:
            offset = er.get("offset", 0)
            length = er.get("length", 0)
            link_ops.append((offset, length, url))

    # Apply links from end to start
    for offset, length, url in sorted(link_ops, key=lambda x: x[0], reverse=True):
        end = offset + length
        anchor = text[offset:end]
        text = text[:offset] + f"[{anchor}]({url})" + text[end:]

    # Apply inline styles (Bold/Italic) with offset adjustment for inserted links
    styles = block.get("inlineStyleRanges", [])
    if styles:
        shifts = []
        for offset, length, url in sorted(link_ops, key=lambda x: x[0]):
            shifts.append((offset, 4 + len(url)))

        def adjusted_offset(orig_off):
            adj = orig_off
            for lk_off, added in shifts:
                if lk_off <= orig_off:
                    adj += added
            return adj

        for style in sorted(styles, key=lambda s: s.get("offset", 0), reverse=True):
            orig_off = style.get("offset", 0)
            length = style.get("length", 0)
            stype = style.get("style", "")
            off = adjusted_offset(orig_off)
            end = adjusted_offset(orig_off + length)
            if stype == "Bold":
                text = text[:off] + "**" + text[off:end] + "**" + text[end:]
            elif stype == "Italic":
                text = text[:off] + "*" + text[off:end] + "*" + text[end:]

    return text


def fetch_via_fxtwitter(url: str, tweet_id: str) -> Dict[str, Any]:
    """Fetch a single tweet via FxTwitter API.

    Returns a dict compatible with feedgrab's twitter.py data format.
    Raises RuntimeError on failure.
    """
    username, _ = _parse_tweet_url(url)
    if not username:
        # Fallback: use tweet_id directly
        username = "i"

    api_url = f"https://api.fxtwitter.com/{username}/status/{tweet_id}"

    try:
        resp = http_client.get(
            api_url,
            headers={"User-Agent": get_user_agent(), "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            _record_failure()
            raise RuntimeError(f"FxTwitter HTTP {resp.status_code}")
        data = resp.json()
    except requests.RequestException as e:
        _record_failure()
        raise RuntimeError(f"FxTwitter request failed: {e}")
    except RuntimeError:
        raise
    except Exception as e:
        _record_failure()
        raise RuntimeError(f"FxTwitter request failed: {e}")

    if data.get("code") != 200:
        _record_failure()
        raise RuntimeError(
            f"FxTwitter error: {data.get('code')} — {data.get('message', 'Unknown')}"
        )

    tweet = data.get("tweet")
    if not tweet:
        _record_failure()
        raise RuntimeError("FxTwitter returned no tweet data")

    _record_success()

    # --- Build result dict compatible with twitter.py format ---

    text = tweet.get("text", "")
    author_obj = tweet.get("author", {})
    screen_name = author_obj.get("screen_name", username)

    # Media extraction
    images = []
    videos = []
    media = tweet.get("media", {})
    for item in media.get("all", []):
        mtype = item.get("type", "")
        if mtype == "photo":
            img_url = item.get("url", "")
            # Strip ?name=orig suffix for consistency
            if "?name=" in img_url:
                img_url = img_url.split("?name=")[0]
            images.append(img_url)
        elif mtype in ("video", "animated_gif"):
            # Get best quality video variant
            videos.append(item.get("url", ""))
            # Also add thumbnail as image
            thumb = item.get("thumbnail_url", "")
            if thumb:
                images.append(thumb)

    # Hashtags from facets
    hashtags = []
    raw_text = tweet.get("raw_text", {})
    for facet in raw_text.get("facets", []):
        if facet.get("type") == "hashtag":
            tag = facet.get("original", "")
            if tag:
                hashtags.append(tag)

    # Quoted tweet
    qt = tweet.get("quote")
    quoted_tweet = None
    if qt:
        qt_author = qt.get("author", {})
        qt_images = []
        qt_media = qt.get("media", {})
        for item in qt_media.get("all", []):
            if item.get("type") == "photo":
                qt_images.append(item.get("url", "").split("?name=")[0])

        quoted_tweet = {
            "text": qt.get("text", ""),
            "author": qt_author.get("screen_name", ""),
            "author_name": qt_author.get("name", ""),
            "url": qt.get("url", ""),
            "images": qt_images,
            "videos": [],
            "likes": qt.get("likes", 0),
            "retweets": qt.get("retweets", 0),
        }

    # Article data
    article = tweet.get("article")
    article_data = {}
    article_body = ""
    if article:
        cover_media = article.get("cover_media", {})
        cover_info = cover_media.get("media_info", {})
        cover_url = cover_info.get("original_img_url", "")

        article_data = {
            "id": article.get("id", ""),
            "title": article.get("title", ""),
            "cover_image": cover_url,
            "has_content": bool(article.get("content", {}).get("blocks")),
        }

        # Render article body from blocks
        article_body = _render_article_body(article)
        if article_body and len(article_body) > 200:
            article_data["body"] = article_body

    # Cover image: article cover > first photo
    cover_image = article_data.get("cover_image", "")
    if not cover_image and images:
        cover_image = images[0]

    # Use article title if available
    display_title = ""
    if article:
        display_title = article.get("title", "")
    if not display_title:
        display_title = text
    from feedgrab.fetchers.twitter import _clean_title
    title = _clean_title(display_title)

    # Build tweet_data for thread_tweets compatibility
    tweet_data = {
        "id": tweet.get("id", tweet_id),
        "text": article_body if article_body and len(article_body) > 200 else text,
        "images": images,
        "videos": videos,
        "quoted_tweet": quoted_tweet,
        "hashtags": hashtags,
    }

    # Views: FxTwitter returns int, normalize to string for compatibility
    views = tweet.get("views", 0)

    return {
        "text": tweet_data["text"],
        "author": f"@{screen_name}" if screen_name else "",
        "author_name": author_obj.get("name", ""),
        "url": url,
        "title": title,
        "platform": "twitter",
        "thread_tweets": [tweet_data],
        "has_thread": False,
        "article_data": article_data,
        "likes": tweet.get("likes", 0),
        "retweets": tweet.get("retweets", 0),
        "replies": tweet.get("replies", 0),
        "bookmarks": tweet.get("bookmarks", 0),
        "views": str(views) if views else "0",
        "created_at": tweet.get("created_at", ""),
        "images": images,
        "videos": videos,
        "hashtags": hashtags,
        "cover_image": cover_image,
        # FxTwitter provides these but not blue_verified/listed_count
        "source_app": tweet.get("source", ""),
        "possibly_sensitive": tweet.get("possibly_sensitive", False),
        "lang": tweet.get("lang", ""),
        "followers_count": author_obj.get("followers", 0),
        "statuses_count": author_obj.get("tweets", 0),
    }
