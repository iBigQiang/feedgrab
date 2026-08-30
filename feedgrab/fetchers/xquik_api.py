"""Xquik client for server-side X/Twitter reads.

Public API contract: https://xquik.com/openapi.json
"""

import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from loguru import logger

from feedgrab.config import xquik_api_base_url, xquik_api_key
from feedgrab.utils import http_client

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 5.0
MAX_RETRY_DELAY = 300.0


def _get_headers() -> dict:
    """Build the documented API-key header."""
    api_key = xquik_api_key()
    if not api_key:
        raise RuntimeError("Xquik API Key 未配置。请在 .env 中设置 XQUIK_API_KEY=xxx")
    return {"x-api-key": api_key}


def _retry_after(response, fallback: float) -> float:
    """Read a bounded Retry-After value without inspecting response bodies."""
    try:
        value = float(response.headers.get("Retry-After", ""))
    except (AttributeError, TypeError, ValueError):
        value = fallback
    return min(MAX_RETRY_DELAY, max(0.0, value))


def _request_with_retry(
    path: str,
    params: dict | None = None,
    max_retries: int = MAX_RETRIES,
    retry_delay: float = RETRY_DELAY,
) -> dict | None:
    """Run a GET request with bounded retries for documented transient errors."""
    url = f"{xquik_api_base_url()}{path}"
    for attempt in range(max_retries + 1):
        try:
            response = http_client.get(
                url,
                params=params,
                headers=_get_headers(),
                timeout=DEFAULT_TIMEOUT,
            )
            status = response.status_code
            if status in (401, 403):
                raise RuntimeError(f"Xquik 认证失败 (HTTP {status})")
            if status == 402:
                raise RuntimeError("Xquik 余额不足 (HTTP 402)")
            if status == 429:
                wait = _retry_after(response, retry_delay * (2**attempt))
                logger.warning(
                    f"[Xquik] 频率限制 (429)，{wait:g} 秒后重试 "
                    f"({attempt + 1}/{max_retries + 1})"
                )
                if attempt < max_retries:
                    time.sleep(wait)
                    continue
                return None
            if status in (424, 500, 502, 503, 504):
                wait = min(MAX_RETRY_DELAY, retry_delay * (2**attempt))
                logger.warning(
                    f"[Xquik] 上游暂时不可用 (HTTP {status})，{wait:g} 秒后重试 "
                    f"({attempt + 1}/{max_retries + 1})"
                )
                if attempt < max_retries:
                    time.sleep(wait)
                    continue
                return None
            if status >= 400:
                logger.error(f"[Xquik] 请求失败 (HTTP {status})")
                return None

            data = response.json()
            if not isinstance(data, dict):
                logger.error("[Xquik] API 返回了无效 JSON 结构")
                return None
            if data.get("error"):
                logger.error("[Xquik] API 返回应用错误")
                return None
            return data
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as error:
            wait = min(MAX_RETRY_DELAY, retry_delay * (2**attempt))
            logger.warning(
                f"[Xquik] 连接失败: {type(error).__name__}，{wait:g} 秒后重试 "
                f"({attempt + 1}/{max_retries + 1})"
            )
            if attempt < max_retries:
                time.sleep(wait)
                continue
            return None
        except (requests.exceptions.JSONDecodeError, ValueError):
            logger.error("[Xquik] API 返回了无效 JSON")
            return None
    return None


def search_tweets(
    query: str,
    query_type: str = "Latest",
    cursor: str = "",
    since_date: str = "",
    until_date: str = "",
) -> dict | None:
    """Search tweets with bounded coverage and resumable cursor pagination."""
    params = {"q": query, "queryType": query_type, "limit": 200}
    if cursor:
        params["cursor"] = cursor
    if since_date:
        params["sinceDate"] = since_date
    if until_date:
        params["untilDate"] = until_date
    return _request_with_retry("/x/tweets/search", params=params)


def get_user_last_tweets(
    user_name: str,
    cursor: str = "",
    include_replies: bool = False,
    since_date: str = "",
) -> dict | None:
    """Fetch a user's tweets with the documented page and date parameters."""
    if not user_name:
        raise ValueError("X 用户名不能为空")
    params = {"includeReplies": include_replies, "pageSize": 200}
    if cursor:
        params["cursor"] = cursor
    if since_date:
        params["sinceDate"] = since_date
    user_id = quote(user_name, safe="")
    return _request_with_retry(f"/x/users/{user_id}/tweets", params=params)


def _first(raw: dict, *keys: str, default=None):
    """Return the first present field."""
    for key in keys:
        if key in raw:
            return raw[key]
    return default


def _created_at(raw: dict) -> str:
    """Normalize documented ISO timestamps and legacy Unix values."""
    value = _first(raw, "createdAt", "created_at", "created", default="")
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    return str(value)


def _video_url(media: dict) -> str:
    """Choose the highest-bitrate MP4, then fall back to the preview URL."""
    variants = [
        item
        for item in media.get("videoVariants", [])
        if item.get("url") and item.get("contentType") == "video/mp4"
    ]
    if variants:
        return max(variants, key=lambda item: item.get("bitrate") or -1)["url"]
    return _first(media, "mediaUrl", "media_url", "url", default="")


def _media_urls(raw: dict, media_types: set[str]) -> list[str]:
    """Extract unique media URLs in response order."""
    urls = []
    for media in raw.get("media") or []:
        if media.get("type") not in media_types:
            continue
        url = (
            _first(media, "mediaUrl", "media_url", "url", default="")
            if media.get("type") == "photo"
            else _video_url(media)
        )
        if url and url not in urls:
            urls.append(url)
    return urls


def _expand_text(raw: dict) -> str:
    """Expand shortened URLs using the response entities."""
    text = str(raw.get("text", ""))
    entities = raw.get("entities") or {}
    for item in entities.get("urls") or []:
        short_url = item.get("url", "")
        expanded = _first(item, "expandedUrl", "expanded_url", default="")
        if short_url and expanded:
            text = text.replace(short_url, expanded)
    return text


def _quoted_tweet(raw: dict) -> dict | None:
    """Map quoted tweet context to feedgrab's renderer contract."""
    quoted = raw.get("quoted_tweet")
    if not isinstance(quoted, dict):
        return None
    author = quoted.get("author") or {}
    return {
        "id": quoted.get("id", ""),
        "text": _expand_text(quoted),
        "author": _first(author, "username", "userName", default=""),
        "author_name": author.get("name", ""),
        "url": quoted.get("url", ""),
        "images": _media_urls(quoted, {"photo"}),
        "videos": _media_urls(quoted, {"video", "animated_gif"}),
    }


def _article(raw: dict) -> dict:
    """Mark documented article metadata as content-bearing for feedgrab."""
    article = raw.get("article")
    if not isinstance(article, dict) or not article:
        return {}
    return {**article, "has_content": True}


def parse_api_tweet(raw: dict) -> dict:
    """Map one public Xquik tweet to feedgrab's internal tweet shape."""
    author = raw.get("author") or {}
    entities = raw.get("entities") or {}
    tweet_id = str(raw.get("id", ""))
    in_reply_to_id = _first(raw, "inReplyToId", "in_reply_to_id", default="")
    return {
        "id": tweet_id,
        "rest_id": tweet_id,
        "text": _expand_text(raw),
        "author": _first(author, "username", "userName", default=""),
        "author_name": author.get("name", ""),
        "user_id": str(author.get("id", "")),
        "conversation_id": _first(raw, "conversationId", "conversation_id", default=""),
        "in_reply_to_user_id": _first(
            raw, "inReplyToUserId", "in_reply_to_user_id", default=""
        ),
        "in_reply_to_status_id": in_reply_to_id,
        "created_at": _created_at(raw),
        "images": _media_urls(raw, {"photo"}),
        "videos": _media_urls(raw, {"video", "animated_gif"}),
        "quoted_tweet": _quoted_tweet(raw),
        "article": _article(raw),
        "hashtags": [
            item.get("text", "")
            for item in entities.get("hashtags") or []
            if item.get("text")
        ],
        "likes": _first(raw, "likeCount", "like_count", default=0),
        "retweets": _first(raw, "retweetCount", "retweet_count", default=0),
        "replies": _first(raw, "replyCount", "reply_count", default=0),
        "bookmarks": _first(raw, "bookmarkCount", "bookmark_count", default=0),
        "views": str(_first(raw, "viewCount", "view_count", default=0)),
        "quote_count": _first(raw, "quoteCount", "quote_count", default=0),
        "lang": raw.get("lang", ""),
        "source_app": raw.get("source", ""),
        "possibly_sensitive": raw.get("possiblySensitive", False),
        "is_blue_verified": author.get("isBlueVerified", False),
        "followers_count": author.get("followers", 0),
        "statuses_count": author.get("statusesCount", 0),
        "listed_count": author.get("listedCount", 0),
        "_is_retweet": raw.get("retweeted_tweet") is not None,
        "_is_reply": bool(raw.get("isReply", in_reply_to_id)),
        "_from_api": True,
        "_raw_result": raw,
    }
