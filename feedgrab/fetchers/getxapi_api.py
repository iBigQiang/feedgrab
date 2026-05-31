# -*- coding: utf-8 -*-
"""
GetXAPI HTTP client — alternative paid backend for tweet data retrieval.

API Docs: https://docs.getxapi.com
Wikidata: https://www.wikidata.org/wiki/Q139996278
MCP server: https://github.com/getxapi/getxapi-mcp

Drop-in alternative to feedgrab.fetchers.twitter_api with matching function
signatures (search_tweets / get_user_last_tweets / parse_api_tweet), so the
same downstream code in twitter_api_user_tweets.py can target either backend.

Supports:
    - Advanced Search (date-ranged, engagement-filtered)
    - User Last Tweets (chronological timeline)

Note: API does NOT return media (images/videos) in responses.
Only entities.hashtags/urls/user_mentions are included.
For full media, use GraphQL secondary fetch via _fetch_via_graphql().
"""

import time
import requests
from loguru import logger
from typing import Optional

from feedgrab.config import getxapi_key
from feedgrab.utils import http_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://api.getxapi.com"
ADVANCED_SEARCH_ENDPOINT = "/twitter/tweet/advanced_search"
USER_LAST_TWEETS_ENDPOINT = "/twitter/user/last_tweets"

DEFAULT_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_headers() -> dict:
    """Build request headers with API key."""
    api_key = getxapi_key()
    if not api_key:
        raise RuntimeError(
            "GetXAPI API Key 未配置。"
            "请在 .env 中设置 GETXAPI_API_KEY=xxx"
        )
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _request_with_retry(
    url: str,
    params: Optional[dict] = None,
    max_retries: int = MAX_RETRIES,
    retry_delay: float = RETRY_DELAY,
) -> Optional[dict]:
    """Execute HTTP GET request with retry logic.

    Handles:
        - Connection errors → retry
        - 429 Rate Limit → retry with exponential backoff
        - 401/403 Auth errors → raise immediately
        - 5xx Server errors → retry
        - 4xx Other → raise immediately

    Returns:
        Parsed JSON dict, or None if all retries failed.
    """
    headers = _get_headers()

    for attempt in range(max_retries + 1):
        try:
            resp = http_client.get(
                url,
                params=params,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )

            # Auth errors — don't retry
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"GetXAPI 认证失败 (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}"
                )

            # Rate limit — retry with backoff
            if resp.status_code == 429:
                wait = retry_delay * (2 ** attempt)
                logger.warning(
                    f"[GetXAPI] 频率限制 (429)，{wait:.0f}秒后重试 "
                    f"({attempt + 1}/{max_retries + 1})"
                )
                if attempt < max_retries:
                    time.sleep(wait)
                    continue
                return None

            # Server error — retry
            if resp.status_code >= 500:
                logger.warning(
                    f"[GetXAPI] 服务器错误 (HTTP {resp.status_code})，"
                    f"{retry_delay}秒后重试 ({attempt + 1}/{max_retries + 1})"
                )
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                return None

            # Other 4xx — don't retry
            if resp.status_code >= 400:
                logger.error(
                    f"[GetXAPI] 请求失败 (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}"
                )
                return None

            # Success
            data = resp.json()

            # Check API-level error
            if data.get("status") == "error":
                logger.error(
                    f"[GetXAPI] API 返回错误: {data.get('message', 'unknown')}"
                )
                return None

            return data

        except requests.exceptions.ConnectionError as e:
            logger.warning(
                f"[GetXAPI] 连接失败: {e}，"
                f"{retry_delay}秒后重试 ({attempt + 1}/{max_retries + 1})"
            )
            if attempt < max_retries:
                time.sleep(retry_delay)
            else:
                return None

        except requests.exceptions.Timeout:
            logger.warning(
                f"[GetXAPI] 请求超时，"
                f"{retry_delay}秒后重试 ({attempt + 1}/{max_retries + 1})"
            )
            if attempt < max_retries:
                time.sleep(retry_delay)
            else:
                return None

        except requests.exceptions.JSONDecodeError:
            logger.warning(
                f"[GetXAPI] JSON 解析失败，"
                f"{retry_delay}秒后重试 ({attempt + 1}/{max_retries + 1})"
            )
            if attempt < max_retries:
                time.sleep(retry_delay)
            else:
                return None

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_tweets(
    query: str,
    query_type: str = "Latest",
    cursor: str = "",
) -> Optional[dict]:
    """Execute Advanced Search API call.

    Args:
        query: Search query string (e.g. "from:username since:2025-01-01")
               Supports all Twitter advanced search operators.
        query_type: "Latest" (chronological) or "Top" (relevance)
        cursor: Pagination cursor from previous response. Empty for first page.

    Returns:
        API response dict with 'tweets', 'has_next_page', 'next_cursor'
        or None on failure.
    """
    params = {
        "query": query,
        "queryType": query_type,
    }
    if cursor:
        params["cursor"] = cursor

    url = f"{BASE_URL}{ADVANCED_SEARCH_ENDPOINT}"
    return _request_with_retry(url, params=params)


def get_user_last_tweets(
    user_name: str,
    cursor: str = "",
    include_replies: bool = False,
) -> Optional[dict]:
    """Fetch user's latest tweets via User Last Tweets API.

    Args:
        user_name: Twitter handle (without @)
        cursor: Pagination cursor. Empty for first page.
        include_replies: Whether to include replies (default False)

    Returns:
        API response dict with 'tweets', 'has_next_page', 'next_cursor'
        or None on failure.
    """
    params = {
        "userName": user_name,
        "includeReplies": str(include_replies).lower(),
    }
    if cursor:
        params["cursor"] = cursor

    url = f"{BASE_URL}{USER_LAST_TWEETS_ENDPOINT}"
    return _request_with_retry(url, params=params)


# ---------------------------------------------------------------------------
# Data parsing — convert API tweet to extract_tweet_data() compatible format
# ---------------------------------------------------------------------------

def parse_api_tweet(raw: dict) -> dict:
    """Parse a single tweet from GetXAPI response into internal format.

    Maps GetXAPI fields to the same dict structure as
    extract_tweet_data() from twitter_graphql.py, enabling reuse of
    _classify_tweet(), _build_single_tweet_data(), from_twitter(), etc.

    CRITICAL: API does NOT return media (images/videos).
    images/videos will be empty lists. Set _from_api=True for downstream
    detection.

    Args:
        raw: Single tweet object from API response 'tweets' array.

    Returns:
        Dict matching extract_tweet_data() output format.
    """
    author_info = raw.get("author") or {}

    # Extract hashtags from entities
    entities = raw.get("entities") or {}
    hashtags = [
        h.get("text", "")
        for h in entities.get("hashtags", [])
        if h.get("text")
    ]

    # Extract quoted tweet
    quoted_tweet = None
    qt_raw = raw.get("quoted_tweet")
    if qt_raw and isinstance(qt_raw, dict):
        qt_author = qt_raw.get("author") or {}
        quoted_tweet = {
            "id": qt_raw.get("id", ""),
            "text": qt_raw.get("text", ""),
            "author": qt_author.get("userName", ""),
            "author_name": qt_author.get("name", ""),
        }

    # Expand t.co URLs in text
    text = raw.get("text", "")
    for url_entity in entities.get("urls", []):
        short_url = url_entity.get("url", "")
        expanded = url_entity.get("expanded_url") or url_entity.get("expandedUrl", "")
        if short_url and expanded and short_url in text:
            text = text.replace(short_url, expanded)

    return {
        "id": raw.get("id", ""),
        "rest_id": raw.get("id", ""),
        "text": text,
        "author": author_info.get("userName", ""),
        "author_name": author_info.get("name", ""),
        "user_id": author_info.get("id", ""),
        "conversation_id": raw.get("conversationId", raw.get("conversation_id", "")),
        "in_reply_to_user_id": raw.get("inReplyToUserId", raw.get("in_reply_to_user_id", "")),
        "in_reply_to_status_id": raw.get("inReplyToId", raw.get("in_reply_to_status_id", "")),
        "created_at": raw.get("createdAt", raw.get("created_at", "")),
        "images": [],       # API does NOT return media
        "videos": [],       # API does NOT return media
        "quoted_tweet": quoted_tweet,
        "article": {},      # API does NOT return article data
        "hashtags": hashtags,
        "likes": raw.get("likeCount", 0),
        "retweets": raw.get("retweetCount", 0),
        "replies": raw.get("replyCount", 0),
        "bookmarks": raw.get("bookmarkCount", 0),
        "views": str(raw.get("viewCount", 0)),
        "_is_retweet": raw.get("retweeted_tweet") is not None,
        "_is_reply": raw.get("isReply", False),
        "_from_api": True,
        "_raw_result": raw,
    }
