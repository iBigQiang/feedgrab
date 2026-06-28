# -*- coding: utf-8 -*-
"""
Twitter/X GraphQL API client — fetch tweet data via X's internal GraphQL endpoints.

Ported from baoyu-danger-x-to-markdown (TypeScript) to Python.
Reference files: constants.ts, graphql.ts, http.ts, thread.ts

Core capabilities:
    - Dynamic queryId resolution from X's frontend JS bundle (self-updating)
    - Hardcoded fallback queryIds when dynamic resolution fails
    - TweetDetail / TweetResultByRestId API calls
    - Rate limiting with configurable delays (safety measure, not in original)

This module uses X's private GraphQL API (reverse-engineered from the web client).
Users must acknowledge this via consent mechanism before first use.
"""

import json
import os
import re
import time
import requests
from loguru import logger
from pathlib import Path
from typing import Dict, Any, Optional, List

from feedgrab.config import get_data_dir
from feedgrab.fetchers.twitter_cookies import (
    build_graphql_headers,
    DEFAULT_USER_AGENT,
)
from feedgrab.utils import http_client

# ---------------------------------------------------------------------------
# Fallback queryIds — from baoyu constants.ts
# ---------------------------------------------------------------------------

FALLBACK_TWEET_DETAIL_QUERY_ID = "xd_EMdYvB9hfZsZ6Idri0w"
FALLBACK_TWEET_RESULT_QUERY_ID = "7xflPyRiUxGVbJd4uWmbfg"
FALLBACK_ARTICLE_QUERY_ID = "id8pHQbQi7eZ6P9mA1th1Q"
FALLBACK_BOOKMARKS_QUERY_ID = "2neUNDqrrFzbLui8yallcQ"
FALLBACK_BOOKMARK_FOLDERS_QUERY_ID = "i78YDd0Tza-dV4SYs58kRg"
FALLBACK_BOOKMARK_FOLDER_TIMELINE_QUERY_ID = "8HoabOvl7jl9IC1Aixj-vg"
FALLBACK_USER_BY_SCREEN_NAME_QUERY_ID = "1VOOyvKkiI3FMmkeDNxM9A"
FALLBACK_USER_TWEETS_QUERY_ID = "q6xj5bs0hapm9309hexA_g"
FALLBACK_SEARCH_TIMELINE_QUERY_ID = "VhUd6vHVmLBcw0uX-6jMLA"
FALLBACK_LIST_BY_REST_ID_QUERY_ID = "BpXQqi3VImT8bR7pAf26rg"
FALLBACK_LIST_LATEST_TWEETS_QUERY_ID = "RlZzktZY_9wJynoepm8ZsA"

# v0.22.0: twe-borrowed operations (fa0311/twitter-openapi placeholder.json)
FALLBACK_FOLLOWERS_QUERY_ID = "IOh4aS6UdGWGJUYTqliQ7Q"
FALLBACK_FOLLOWING_QUERY_ID = "zx6e-TLzRkeDO_a7p4b3JQ"
FALLBACK_BLUE_VERIFIED_FOLLOWERS_QUERY_ID = "GQ1yZjbfSiPfi_5gznKMPw"
FALLBACK_LIKES_QUERY_ID = "lIDpu_NWL7_VhimGGt0o6A"
FALLBACK_USER_TWEETS_AND_REPLIES_QUERY_ID = "6hvhmQQ9zPIR8RZWHFAm4w"
FALLBACK_LIST_MEMBERS_QUERY_ID = "EkmM6fQjaFMaQbj2wGFQ9w"
FALLBACK_LIST_SUBSCRIBERS_QUERY_ID = "_av5eJHyhOzx9nTQkQg0iQ"

# v0.23.0: ModeratedTimeline — author-hidden replies under their own thread
# (queryId from fa0311/twitter-openapi; twe modules/tweet-detail/api.ts:48)
FALLBACK_MODERATED_TIMELINE_QUERY_ID = "T2DTQt8XU3-d2EHWRsxOcw"

# v0.23.0: Retweeters / Favoriters — users who retweeted / liked a tweet
# (queryId from fa0311/twitter-openapi placeholder.json)
FALLBACK_RETWEETERS_QUERY_ID = "Mbs-2NiTvy32oHDerWtVhg"
FALLBACK_FAVORITERS_QUERY_ID = "G27_CXbgIP3G9Fod_2RMUA"

# ---------------------------------------------------------------------------
# Feature switches — per-operation, from baoyu constants.ts
# ---------------------------------------------------------------------------

# TweetResultByRestId features (constants.ts lines 25-61)
TWEET_RESULT_FEATURES = {
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": True,
    "creator_subscriptions_quote_tweet_preview_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_enhance_cards_enabled": True,
    "premium_content_api_read_enabled": True,
    "responsive_web_text_conversations_enabled": True,
    "responsive_web_media_download_video_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": True,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": True,
    "rweb_video_screen_enabled": True,
}

# TweetDetail features (constants.ts lines 105-137)
TWEET_DETAIL_FEATURES = {
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_text_conversations_enabled": True,
    "responsive_web_media_download_video_enabled": True,
    "premium_content_api_read_enabled": False,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_jetfuel_frame": False,
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": True,
}

# ---------------------------------------------------------------------------
# Field toggles — per-operation, from baoyu constants.ts
# ---------------------------------------------------------------------------

# TweetResultByRestId field toggles
TWEET_RESULT_FIELD_TOGGLES = {
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
    "withGrokAnalyze": False,
    "withDisallowedReplyControls": False,
    "withPayments": True,
    "withAuxiliaryUserLabels": True,
}

# TweetDetail field toggles
TWEET_DETAIL_FIELD_TOGGLES = {
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
    "withGrokAnalyze": False,
    "withDisallowedReplyControls": False,
}

# ---------------------------------------------------------------------------
# Bookmarks feature switches and field toggles
# ---------------------------------------------------------------------------

BOOKMARK_FEATURES = dict(TWEET_DETAIL_FEATURES)
BOOKMARK_FEATURES["graphql_timeline_v2_bookmark_timeline"] = True

BOOKMARK_FIELD_TOGGLES = {
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
    "withGrokAnalyze": False,
    "withDisallowedReplyControls": False,
}

# ---------------------------------------------------------------------------
# UserByScreenName & UserTweets feature switches
# ---------------------------------------------------------------------------

USER_BY_SCREEN_NAME_FEATURES = {
    "hidden_profile_likes_enabled": True,
    "hidden_profile_subscriptions_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
}

USER_TWEETS_FEATURES = dict(TWEET_DETAIL_FEATURES)
USER_TWEETS_FEATURES["creator_subscriptions_tweet_preview_api_enabled"] = True

# ---------------------------------------------------------------------------
# v0.22.0: Followers / Following / BlueVerifiedFollowers / ListMembers /
# ListSubscribers feature switches (user-list timelines)
# ---------------------------------------------------------------------------

USER_LIST_FEATURES = dict(USER_BY_SCREEN_NAME_FEATURES)
# These ops also accept the same minimal feature set used by UserByScreenName.
# Live feature flags are still updated at runtime via _update_features_from_html.

# Likes & UserTweetsAndReplies — same shape as UserTweets timeline
USER_LIKES_FEATURES = dict(USER_TWEETS_FEATURES)
USER_TWEETS_AND_REPLIES_FEATURES = dict(USER_TWEETS_FEATURES)

# ---------------------------------------------------------------------------
# ListLatestTweetsTimeline feature switches
# ---------------------------------------------------------------------------

LIST_TWEETS_FEATURES = dict(TWEET_DETAIL_FEATURES)
LIST_TWEETS_FEATURES["rweb_lists_timeline_redesign_enabled"] = True

# ---------------------------------------------------------------------------
# SearchTimeline feature switches and field toggles
# ---------------------------------------------------------------------------

SEARCH_TIMELINE_FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_grok_image_annotation_enabled": False,
    "responsive_web_grok_imagine_annotation_enabled": False,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
}

SEARCH_TIMELINE_FIELD_TOGGLES = {
    "withPayments": False,
    "withAuxiliaryUserLabels": False,
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
    "withGrokAnalyze": False,
    "withDisallowedReplyControls": False,
}

# All mutable features dicts for dynamic update (populated after definition)
_ALL_FEATURES_DICTS: list = [
    TWEET_RESULT_FEATURES,
    TWEET_DETAIL_FEATURES,
    BOOKMARK_FEATURES,
    USER_BY_SCREEN_NAME_FEATURES,
    USER_TWEETS_FEATURES,
    LIST_TWEETS_FEATURES,
    SEARCH_TIMELINE_FEATURES,
    # v0.22.0: twe-borrowed ops
    USER_LIST_FEATURES,
    USER_LIKES_FEATURES,
    USER_TWEETS_AND_REPLIES_FEATURES,
]


def _update_features_from_html(html: str) -> int:
    """Extract live feature flags from x.com HTML and update global dicts.

    Twitter embeds feature switch config in inline <script> as JSON like:
        "responsive_web_graphql_timeline_navigation_enabled": {"value": true}

    Only UPDATES existing keys in our features dicts — never adds new ones
    to avoid URL bloat and 414 errors.  Returns number of flags changed.
    """
    if not html:
        return 0
    try:
        pattern = re.compile(
            r'"([a-z][a-z0-9_]+)":\s*\{\s*"value"\s*:\s*(true|false)',
            re.IGNORECASE,
        )
        # Build a lookup: key → new bool value
        live: dict[str, bool] = {}
        for m in pattern.finditer(html):
            live[m.group(1)] = m.group(2).lower() == "true"

        if not live:
            return 0

        changed = 0
        unique_keys: set = set()
        for fdict in _ALL_FEATURES_DICTS:
            for key in fdict:
                if key in live and fdict[key] != live[key]:
                    unique_keys.add(key)
                    fdict[key] = live[key]
                    changed += 1
        if unique_keys:
            logger.info("Dynamic feature update: {} flags synced from x.com", len(unique_keys))
        return changed
    except Exception as exc:
        logger.debug("Feature extraction from HTML failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Rate limiting (safety measure — original baoyu has none)
# ---------------------------------------------------------------------------

DEFAULT_REQUEST_DELAY = float(os.getenv("X_REQUEST_DELAY", "1.5"))
DEFAULT_MAX_PAGES = int(os.getenv("X_THREAD_MAX_PAGES", "20"))

# GraphQL base URL
GRAPHQL_BASE = "https://x.com/i/api/graphql"

# Cache for resolved query info and home HTML
_query_cache: Dict[str, Any] = {}
_cache_timestamp: float = 0
_cached_home_html: str = ""
CACHE_TTL = 3600  # 1 hour

# Cache for x-client-transaction-id generator
_transaction_generator = None
_transaction_generator_timestamp: float = 0
_TRANSACTION_TTL = 1800  # 30 min — homepage/ondemand.s can change

# Disk cache for homepage + ondemand data (avoids cold-start HTTP requests)
_DISK_CACHE_TTL = 3600  # 1 hour (matches twitter-cli)

# Community queryId source (fa0311/twitter-openapi)
_COMMUNITY_QUERYID_URL = (
    "https://raw.githubusercontent.com/fa0311/twitter-openapi/"
    "main/src/config/placeholder.json"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_tweet_detail(
    tweet_id: str, cookies: dict, cursor: str = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch full tweet detail via TweetDetail GraphQL endpoint.

    Returns the tweet plus surrounding conversation context (thread, quoted tweets).
    Supports cursor-based pagination for thread traversal.

    Args:
        tweet_id: The numeric tweet/status ID.
        cookies: dict with 'auth_token' and 'ct0'.
        cursor: Optional pagination cursor from a previous response.

    Returns:
        Raw GraphQL response dict, or None on failure.
    """
    query_id = _get_query_id("TweetDetail")
    headers = build_graphql_headers(cookies)

    # Variables — matches baoyu graphql.ts fetchTweetDetail()
    variables = {
        "focalTweetId": tweet_id,
        "with_rux_injections": False,
        "rankingMode": "Relevance",
        "includePromotedContent": True,
        "withCommunity": True,
        "withQuickPromoteEligibilityTweetFields": True,
        "withBirdwatchNotes": True,
        "withVoice": True,
        "withV2Timeline": True,
        "withDownvotePerspective": False,
        "withReactionsMetadata": False,
        "withReactionsPerspective": False,
        "withSuperFollowsTweetFields": False,
        "withSuperFollowsUserFields": False,
    }

    if cursor:
        variables["cursor"] = cursor
        variables["referrer"] = "tweet"
        _rate_limit_wait()

    return _execute_graphql(
        query_id=query_id,
        operation_name="TweetDetail",
        variables=variables,
        features=dict(TWEET_DETAIL_FEATURES),
        field_toggles=dict(TWEET_DETAIL_FIELD_TOGGLES),
        headers=headers,
    )


def fetch_tweet_by_rest_id(tweet_id: str, cookies: dict) -> Optional[Dict[str, Any]]:
    """
    Fetch a single tweet by REST ID (simpler endpoint, less context).

    Fallback when TweetDetail fails — returns just the tweet without
    surrounding conversation context.

    Args:
        tweet_id: The numeric tweet/status ID.
        cookies: dict with 'auth_token' and 'ct0'.

    Returns:
        Raw GraphQL response dict, or None on failure.
    """
    query_id = _get_query_id("TweetResultByRestId")
    headers = build_graphql_headers(cookies)

    variables = {
        "tweetId": tweet_id,
        "withCommunity": False,
        "includePromotedContent": False,
        "withVoice": False,
    }

    return _execute_graphql(
        query_id=query_id,
        operation_name="TweetResultByRestId",
        variables=variables,
        features=dict(TWEET_RESULT_FEATURES),
        field_toggles=dict(TWEET_RESULT_FIELD_TOGGLES),
        headers=headers,
    )


def fetch_bookmarks_page(
    cookies: dict, cursor: str = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """
    Fetch one page of the authenticated user's bookmarks.

    Args:
        cookies: dict with 'auth_token' and 'ct0'.
        cursor: Optional pagination cursor from a previous response.
        count: Number of bookmarks per page (default 20).

    Returns:
        Raw GraphQL response dict, or None on failure.
    """
    query_id = _get_query_id("Bookmarks")
    headers = build_graphql_headers(cookies)

    variables = {
        "count": count,
        "includePromotedContent": False,
        "withClientEventToken": False,
        "withBirdwatchNotes": False,
        "withVoice": True,
        "withV2Timeline": True,
    }

    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()

    return _execute_graphql(
        query_id=query_id,
        operation_name="Bookmarks",
        variables=variables,
        features=dict(BOOKMARK_FEATURES),
        field_toggles=dict(BOOKMARK_FIELD_TOGGLES),
        headers=headers,
    )


def fetch_bookmark_folders(cookies: dict) -> list:
    """
    Fetch the user's bookmark folder list via BookmarkFoldersSlice.

    Returns:
        [{"id": "...", "name": "OpenClaw"}, ...]
        Empty list on failure.
    """
    query_id = _get_query_id("BookmarkFoldersSlice")
    headers = build_graphql_headers(cookies)

    response = _execute_graphql(
        query_id=query_id,
        operation_name="BookmarkFoldersSlice",
        variables={},
        features={},
        field_toggles={},
        headers=headers,
    )

    if not response or "data" not in response:
        logger.warning("[BookmarkFolders] API returned empty response")
        return []

    data = response["data"]

    # Response path not yet verified by packet capture — try multiple paths
    # Path 0: data.viewer.user_results.result.bookmark_collections_slice.items (actual)
    items = (
        data.get("viewer", {})
        .get("user_results", {})
        .get("result", {})
        .get("bookmark_collections_slice", {})
        .get("items", [])
    )
    # Path 1: data.bookmark_collections_slice.items (from twikit)
    if not items:
        items = data.get("bookmark_collections_slice", {}).get("items", [])
    # Path 2: data.bookmarkFoldersSlice.folders
    if not items:
        items = data.get("bookmarkFoldersSlice", {}).get("folders", [])
    # Path 3: deep scan — find any list of dicts with "name" key
    if not items:
        def _find_folder_items(obj, depth=0):
            if depth > 5:
                return []
            if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "name" in obj[0]:
                return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    found = _find_folder_items(v, depth + 1)
                    if found:
                        return found
            return []
        items = _find_folder_items(data)

    if not items:
        logger.warning(f"[BookmarkFolders] Could not parse folders from response keys: {list(data.keys())}")
        logger.debug(f"[BookmarkFolders] Raw response: {json.dumps(data, ensure_ascii=False)[:500]}")

    folders = []
    for item in items:
        fid = item.get("id", "")
        fname = item.get("name", "")
        if fid and fname:
            folders.append({"id": str(fid), "name": fname})

    logger.info(f"[BookmarkFolders] Found {len(folders)} folders")
    return folders


def fetch_bookmark_folder_page(
    folder_id: str, cookies: dict, cursor: str = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """
    Fetch one page of tweets from a specific bookmark folder.

    Args:
        folder_id: The numeric bookmark folder ID.
        cookies: dict with 'auth_token' and 'ct0'.
        cursor: Optional pagination cursor.
        count: Items per page (default 20).

    Returns:
        Raw GraphQL response dict, or None on failure.
    """
    query_id = _get_query_id("BookmarkFolderTimeline")
    headers = build_graphql_headers(cookies)

    variables = {
        "count": count,
        "includePromotedContent": True,
        "bookmark_collection_id": folder_id,
    }

    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()

    return _execute_graphql(
        query_id=query_id,
        operation_name="BookmarkFolderTimeline",
        variables=variables,
        features=dict(BOOKMARK_FEATURES),
        field_toggles=dict(BOOKMARK_FIELD_TOGGLES),
        headers=headers,
    )


def fetch_user_by_screen_name(screen_name: str, cookies: dict) -> Dict[str, str]:
    """
    Resolve a screen_name to user_id and display name via UserByScreenName API.

    Args:
        screen_name: Twitter handle without '@' (e.g. 'iBigQiang').
        cookies: dict with 'auth_token' and 'ct0'.

    Returns:
        {"user_id": "123", "screen_name": "iBigQiang", "name": "强子手记"}
    """
    query_id = _get_query_id("UserByScreenName")
    headers = build_graphql_headers(cookies)

    variables = {
        "screen_name": screen_name,
        "withSafetyModeUserFields": True,
    }

    response = _execute_graphql(
        query_id=query_id,
        operation_name="UserByScreenName",
        variables=variables,
        features=dict(USER_BY_SCREEN_NAME_FEATURES),
        field_toggles={},
        headers=headers,
    )

    if not response or "data" not in response:
        logger.warning(f"[UserByScreenName] API returned empty for @{screen_name}")
        return {"user_id": "", "screen_name": screen_name, "name": ""}

    result = response.get("data", {}).get("user", {}).get("result", {})
    return {
        "user_id": result.get("rest_id", ""),
        "screen_name": result.get("legacy", {}).get("screen_name", screen_name),
        "name": result.get("legacy", {}).get("name", ""),
    }


def fetch_user_tweets_page(
    user_id: str, cookies: dict, cursor: str = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """
    Fetch one page of a user's tweets via UserTweets GraphQL endpoint.

    Args:
        user_id: The numeric user ID (from fetch_user_by_screen_name).
        cookies: dict with 'auth_token' and 'ct0'.
        cursor: Optional pagination cursor from a previous response.
        count: Number of tweets per page (default 20).

    Returns:
        Raw GraphQL response dict, or None on failure.
    """
    query_id = _get_query_id("UserTweets")
    headers = build_graphql_headers(cookies)

    variables = {
        "userId": user_id,
        "count": count,
        "includePromotedContent": True,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
        "withV2Timeline": True,
    }

    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()

    return _execute_graphql(
        query_id=query_id,
        operation_name="UserTweets",
        variables=variables,
        features=dict(USER_TWEETS_FEATURES),
        field_toggles=dict(BOOKMARK_FIELD_TOGGLES),
        headers=headers,
    )


# ---------------------------------------------------------------------------
# v0.23.0: sortIndex helper (borrowed from twe utils/api.ts:24-29)
# ---------------------------------------------------------------------------

def _entry_sort_index(entry: dict) -> int:
    """Safe BigInt-like extraction of `sortIndex` from a timeline entry.

    Twitter uses snowflake-id strings, so we treat them as big integers.
    Returns 0 on missing/malformed values.
    """
    try:
        return int(entry.get("sortIndex", "0") or "0")
    except (TypeError, ValueError):
        return 0


def _sort_entries_by_sortindex(entries: list) -> list:
    """Sort entries by sortIndex desc in place (newest first).

    Twitter's GraphQL response is usually already sorted, but in batch
    merges across pages the order can drift. Forcing a descending sort
    by snowflake id stabilizes the final timeline.
    """
    entries.sort(key=_entry_sort_index, reverse=True)
    return entries


def parse_user_tweets_entries(response: Dict[str, Any]) -> tuple:
    """
    Extract tweet entries and pagination cursors from a UserTweets GraphQL response.

    Response path: data.user.result.timeline_v2.timeline.instructions

    Returns:
        (entries, cursors) — entries can be passed to extract_tweet_data(),
        cursors is a dict with optional 'top' and 'bottom' keys.
    """
    if not response or "data" not in response:
        return [], {}

    data = response["data"]

    # Primary path: data.user.result.timeline_v2.timeline.instructions
    instructions = (
        data.get("user", {})
        .get("result", {})
        .get("timeline_v2", {})
        .get("timeline", {})
        .get("instructions", [])
    )
    # Fallback: data.user.result.timeline.timeline.instructions
    if not instructions:
        instructions = (
            data.get("user", {})
            .get("result", {})
            .get("timeline", {})
            .get("timeline", {})
            .get("instructions", [])
        )

    entries = []
    cursors = {}
    pin_entry = None  # P0-3: pinned tweet from TimelinePinEntry instruction

    for instruction in instructions:
        inst_type = instruction.get("type", "")

        # P0-3: pinned tweet (top of profile) — twe user-tweets_api.ts L48-57
        if inst_type == "TimelinePinEntry":
            pin = instruction.get("entry", {})
            if pin:
                # Tag for downstream renderers; insert at the front below.
                pin["_is_pinned"] = True
                pin_entry = pin
            continue

        if inst_type == "TimelineAddEntries":
            for entry in instruction.get("entries", []):
                entry_id = entry.get("entryId", "")
                content = entry.get("content", {})

                # Extract cursor entries
                if entry_id.startswith("cursor-"):
                    cursor_type = content.get("cursorType", "")
                    value = content.get("value", "")
                    if cursor_type == "Top" and value:
                        cursors["top"] = value
                    elif cursor_type == "Bottom" and value:
                        cursors["bottom"] = value
                    continue

                # Skip promoted content
                if "promoted" in entry_id.lower():
                    continue

                # UserTweets wraps tweets in TimelineTimelineModule with items[]
                if content.get("entryType") == "TimelineTimelineModule":
                    for item in content.get("items", []):
                        # Filter non-tweet items (e.g. "Who to Follow" cards)
                        item_type = (item.get("item", {})
                                     .get("itemContent", {})
                                     .get("itemType", ""))
                        if item_type == "TimelineTweet":
                            entries.append(item)
                elif content.get("entryType") == "TimelineTimelineItem":
                    # Only keep tweet items, skip "who to follow" / "trends" etc.
                    item_type = content.get("itemContent", {}).get("itemType", "")
                    if item_type == "TimelineTweet":
                        entries.append(entry)
                else:
                    entries.append(entry)

        elif inst_type == "TimelineAddToModule":
            for item in instruction.get("moduleItems", []):
                item_type = (item.get("item", {})
                             .get("itemContent", {})
                             .get("itemType", ""))
                if item_type == "TimelineTweet":
                    entries.append(item)

    # v0.23.0: stable sort by sortIndex desc (twe utils/api.ts:24-29)
    # — pinned entry stays at the head, inserted after sort.
    _sort_entries_by_sortindex(entries)

    # Place pinned tweet first (only on first page — pin entry only appears in cursor=None response)
    if pin_entry is not None:
        entries.insert(0, pin_entry)

    return entries, cursors


# ---------------------------------------------------------------------------
# v0.22.0: User list & timeline ops (twe-borrowed)
#
# Followers / Following / BlueVerifiedFollowers / ListMembers / ListSubscribers
#   → return User entries.   parsed by parse_*_users_entries.
# Likes / UserTweetsAndReplies
#   → return Tweet entries.  parsed by parse_user_tweets_entries (shared).
#
# queryIds bootstrapped from fa0311/twitter-openapi placeholder.json; runtime
# resolution (cache / community / JS-bundle) overrides hardcoded defaults.
# ---------------------------------------------------------------------------


def _parse_user_list_response(
    response: Dict[str, Any], instructions_path: List[str]
) -> tuple:
    """Generic user-timeline parser.

    Args:
        response: full GraphQL JSON dict
        instructions_path: dot-path keys into `data.<...>.timeline.instructions`

    Returns:
        (entries, cursors) where entries are TimelineUser entries.
    """
    if not response or "data" not in response:
        return [], {}

    node: Any = response["data"]
    for key in instructions_path:
        if not isinstance(node, dict):
            return [], {}
        node = node.get(key, {})

    instructions = node.get("instructions", []) if isinstance(node, dict) else []

    entries: List[dict] = []
    cursors: Dict[str, str] = {}

    for instruction in instructions:
        inst_type = instruction.get("type", "")
        if inst_type != "TimelineAddEntries":
            continue
        for entry in instruction.get("entries", []):
            entry_id = entry.get("entryId", "")
            content = entry.get("content", {})

            if entry_id.startswith("cursor-"):
                ct = content.get("cursorType", "")
                value = content.get("value", "")
                if ct == "Top" and value:
                    cursors["top"] = value
                elif ct == "Bottom" and value:
                    cursors["bottom"] = value
                continue

            if content.get("entryType") == "TimelineTimelineItem":
                item_type = content.get("itemContent", {}).get("itemType", "")
                if item_type == "TimelineUser":
                    entries.append(entry)

    # v0.23.0: stable sort by sortIndex desc (twe utils/api.ts:24-29)
    _sort_entries_by_sortindex(entries)

    return entries, cursors


def extract_user_data(entry: dict) -> Optional[Dict[str, Any]]:
    """Extract structured user data from a TimelineUser entry.

    Returns:
        Flat dict with user fields, or None on suspended/deleted/unknown typename.
    """
    content = entry.get("content", entry)
    item_content = content.get("itemContent", {})
    user_results = item_content.get("user_results", {})
    result = user_results.get("result", {})

    typename = result.get("__typename")
    if typename != "User":
        # twe utils/api.ts:98-110 — log + skip
        if typename:
            logger.debug(
                f"[Twitter] Skipping {typename} entry "
                f"(likely suspended/deleted)"
            )
        return None

    legacy = result.get("legacy", {})
    core = result.get("core", {})

    screen_name = legacy.get("screen_name", "") or core.get("screen_name", "")
    name = legacy.get("name", "") or core.get("name", "")

    return {
        "user_id": result.get("rest_id", ""),
        "screen_name": screen_name,
        "name": name,
        "description": legacy.get("description", ""),
        "location": legacy.get("location", ""),
        "url": f"https://x.com/{screen_name}" if screen_name else "",
        "profile_image_url": (
            legacy.get("profile_image_url_https", "")
            or result.get("avatar", {}).get("image_url", "")
        ),
        "profile_banner_url": legacy.get("profile_banner_url", ""),
        "followers_count": legacy.get("followers_count", 0),
        "friends_count": legacy.get("friends_count", 0),
        "statuses_count": legacy.get("statuses_count", 0),
        "listed_count": legacy.get("listed_count", 0),
        "favourites_count": legacy.get("favourites_count", 0),
        "verified": legacy.get("verified", False),
        "is_blue_verified": result.get("is_blue_verified", False),
        "created_at": legacy.get("created_at", ""),
        "protected": legacy.get("protected", False),
    }


# --- User-timeline ops returning User entries -------------------------------

def _fetch_user_timeline_op(
    user_id: str, cookies: dict, cursor: Optional[str], count: int,
    operation_name: str,
) -> Optional[Dict[str, Any]]:
    """Shared helper for Followers / Following / BlueVerifiedFollowers."""
    query_id = _get_query_id(operation_name)
    headers = build_graphql_headers(cookies)
    variables = {
        "userId": user_id,
        "count": count,
        "includePromotedContent": False,
    }
    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()
    return _execute_graphql(
        query_id=query_id,
        operation_name=operation_name,
        variables=variables,
        features=dict(USER_LIST_FEATURES),
        field_toggles={},
        headers=headers,
    )


def fetch_followers_page(
    user_id: str, cookies: dict, cursor: Optional[str] = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """Fetch one page of a user's followers."""
    return _fetch_user_timeline_op(user_id, cookies, cursor, count, "Followers")


def fetch_following_page(
    user_id: str, cookies: dict, cursor: Optional[str] = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """Fetch one page of who a user is following."""
    return _fetch_user_timeline_op(user_id, cookies, cursor, count, "Following")


def fetch_blue_verified_followers_page(
    user_id: str, cookies: dict, cursor: Optional[str] = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """Fetch one page of a user's Blue-verified followers."""
    return _fetch_user_timeline_op(
        user_id, cookies, cursor, count, "BlueVerifiedFollowers"
    )


def parse_user_timeline_users(response: Dict[str, Any]) -> tuple:
    """Parse Followers / Following / BlueVerifiedFollowers response.

    Response path: data.user.result.timeline.timeline.instructions
    """
    return _parse_user_list_response(
        response, ["user", "result", "timeline", "timeline"]
    )


# --- List-user ops returning User entries -----------------------------------

def _fetch_list_user_op(
    list_id: str, cookies: dict, cursor: Optional[str], count: int,
    operation_name: str,
) -> Optional[Dict[str, Any]]:
    """Shared helper for ListMembers / ListSubscribers."""
    query_id = _get_query_id(operation_name)
    headers = build_graphql_headers(cookies)
    variables = {
        "listId": list_id,
        "count": count,
        "withSafetyModeUserFields": True,
    }
    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()
    return _execute_graphql(
        query_id=query_id,
        operation_name=operation_name,
        variables=variables,
        features=dict(USER_LIST_FEATURES),
        field_toggles={},
        headers=headers,
    )


def fetch_list_members_page(
    list_id: str, cookies: dict, cursor: Optional[str] = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """Fetch one page of a Twitter List's members."""
    return _fetch_list_user_op(list_id, cookies, cursor, count, "ListMembers")


def fetch_list_subscribers_page(
    list_id: str, cookies: dict, cursor: Optional[str] = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """Fetch one page of a Twitter List's subscribers."""
    return _fetch_list_user_op(list_id, cookies, cursor, count, "ListSubscribers")


def parse_list_members_users(response: Dict[str, Any]) -> tuple:
    """data.list.members_timeline.timeline.instructions"""
    return _parse_user_list_response(
        response, ["list", "members_timeline", "timeline"]
    )


def parse_list_subscribers_users(response: Dict[str, Any]) -> tuple:
    """data.list.subscribers_timeline.timeline.instructions"""
    return _parse_user_list_response(
        response, ["list", "subscribers_timeline", "timeline"]
    )


# --- Tweet-level user-list ops: Retweeters / Favoriters --------------------
# v0.23.0: who retweeted / liked a given tweet.

def _fetch_tweet_user_list_op(
    tweet_id: str, cookies: dict, cursor: Optional[str], count: int,
    operation_name: str,
) -> Optional[Dict[str, Any]]:
    """Shared helper for Retweeters / Favoriters."""
    query_id = _get_query_id(operation_name)
    headers = build_graphql_headers(cookies)
    variables = {
        "tweetId": tweet_id,
        "count": count,
        "includePromotedContent": False,
    }
    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()
    return _execute_graphql(
        query_id=query_id,
        operation_name=operation_name,
        variables=variables,
        features=dict(USER_LIST_FEATURES),
        field_toggles={},
        headers=headers,
    )


def fetch_retweeters_page(
    tweet_id: str, cookies: dict, cursor: Optional[str] = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """Fetch one page of users who retweeted a tweet."""
    return _fetch_tweet_user_list_op(tweet_id, cookies, cursor, count, "Retweeters")


def fetch_favoriters_page(
    tweet_id: str, cookies: dict, cursor: Optional[str] = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """Fetch one page of users who liked a tweet."""
    return _fetch_tweet_user_list_op(tweet_id, cookies, cursor, count, "Favoriters")


def parse_retweeters_users(response: Dict[str, Any]) -> tuple:
    """data.retweeters_timeline.timeline.instructions"""
    return _parse_user_list_response(
        response, ["retweeters_timeline", "timeline"]
    )


def parse_favoriters_users(response: Dict[str, Any]) -> tuple:
    """data.favoriters_timeline.timeline.instructions"""
    return _parse_user_list_response(
        response, ["favoriters_timeline", "timeline"]
    )


# --- Tweet-timeline ops returning Tweet entries (Likes, UserTweetsAndReplies)

def fetch_user_likes_page(
    user_id: str, cookies: dict, cursor: Optional[str] = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """Fetch one page of a user's liked tweets via Likes endpoint."""
    query_id = _get_query_id("Likes")
    headers = build_graphql_headers(cookies)
    variables = {
        "userId": user_id,
        "count": count,
        "includePromotedContent": False,
        "withClientEventToken": False,
        "withBirdwatchNotes": False,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()
    return _execute_graphql(
        query_id=query_id,
        operation_name="Likes",
        variables=variables,
        features=dict(USER_LIKES_FEATURES),
        field_toggles=dict(BOOKMARK_FIELD_TOGGLES),
        headers=headers,
    )


def fetch_user_tweets_and_replies_page(
    user_id: str, cookies: dict, cursor: Optional[str] = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """Fetch one page of a user's tweets+replies (with_replies tab)."""
    query_id = _get_query_id("UserTweetsAndReplies")
    headers = build_graphql_headers(cookies)
    variables = {
        "userId": user_id,
        "count": count,
        "includePromotedContent": True,
        "withCommunity": True,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()
    return _execute_graphql(
        query_id=query_id,
        operation_name="UserTweetsAndReplies",
        variables=variables,
        features=dict(USER_TWEETS_AND_REPLIES_FEATURES),
        field_toggles=dict(BOOKMARK_FIELD_TOGGLES),
        headers=headers,
    )


# Note: Likes & UserTweetsAndReplies use parse_user_tweets_entries (above) —
# response shape is identical to UserTweets, just different operationName.


# --- v0.23.0: ModeratedTimeline (author-hidden replies under their thread) ---

# ModeratedTimeline reuses the TweetDetail feature set (same scope = thread).
MODERATED_TIMELINE_FEATURES = dict(TWEET_DETAIL_FEATURES)


def fetch_moderated_timeline_page(
    focal_tweet_id: str, cookies: dict, cursor: Optional[str] = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """Fetch one page of author-hidden replies under a thread.

    Only the tweet author themselves can typically query this endpoint;
    others will get an empty timeline. Used as opt-in supplement to
    fetch_tweet_detail when X_FETCH_MODERATED_REPLIES=true.
    """
    query_id = _get_query_id("ModeratedTimeline")
    headers = build_graphql_headers(cookies)
    variables = {
        "rootTweetId": focal_tweet_id,
        "count": count,
        "includePromotedContent": False,
        "withCommunity": False,
        "withVoice": True,
    }
    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()
    return _execute_graphql(
        query_id=query_id,
        operation_name="ModeratedTimeline",
        variables=variables,
        features=dict(MODERATED_TIMELINE_FEATURES),
        field_toggles=dict(BOOKMARK_FIELD_TOGGLES),
        headers=headers,
    )


def parse_moderated_timeline_entries(response: Dict[str, Any]) -> tuple:
    """Parse ModeratedTimeline response.

    Path: data.tweet.result.timeline_response.timeline.instructions (twe L59)

    Returns:
        (entries, cursors) — entries can be passed to extract_tweet_data().
    """
    if not response or "data" not in response:
        return [], {}

    instructions = (
        response.get("data", {})
        .get("tweet", {})
        .get("result", {})
        .get("timeline_response", {})
        .get("timeline", {})
        .get("instructions", [])
    )

    entries: List[dict] = []
    cursors: Dict[str, str] = {}

    for instruction in instructions:
        inst_type = instruction.get("type", "")
        if inst_type == "TimelineAddEntries":
            for entry in instruction.get("entries", []):
                entry_id = entry.get("entryId", "")
                content = entry.get("content", {})

                if entry_id.startswith("cursor-"):
                    ct = content.get("cursorType", "")
                    value = content.get("value", "")
                    if ct == "Bottom" and value:
                        cursors["bottom"] = value
                    continue

                if content.get("entryType") == "TimelineTimelineItem":
                    item_type = content.get("itemContent", {}).get("itemType", "")
                    if item_type == "TimelineTweet":
                        entries.append(entry)

    _sort_entries_by_sortindex(entries)
    return entries, cursors


# ---------------------------------------------------------------------------
# List timeline: fetch metadata + tweets
# ---------------------------------------------------------------------------

def fetch_list_by_rest_id(list_id: str, cookies: dict) -> Dict[str, str]:
    """
    Fetch list metadata (name, description, member count) via ListByRestId.

    Args:
        list_id: Numeric list ID (e.g. '2002743803959300263').
        cookies: dict with 'auth_token' and 'ct0'.

    Returns:
        {"list_id": "...", "name": "...", "description": "...", "member_count": 0}
    """
    query_id = _get_query_id("ListByRestId")
    headers = build_graphql_headers(cookies)

    variables = {
        "listId": list_id,
        "withSuperFollowsUserFields": True,
    }

    response = _execute_graphql(
        query_id=query_id,
        operation_name="ListByRestId",
        variables=variables,
        features=dict(USER_BY_SCREEN_NAME_FEATURES),
        field_toggles={},
        headers=headers,
    )

    if not response or "data" not in response:
        logger.warning(f"[ListByRestId] API returned empty for list {list_id}")
        return {"list_id": list_id, "name": "", "description": "", "member_count": 0}

    lst = response.get("data", {}).get("list", {})
    return {
        "list_id": lst.get("id_str", list_id),
        "name": lst.get("name", ""),
        "description": lst.get("description", ""),
        "member_count": lst.get("member_count", 0),
    }


def fetch_list_tweets_page(
    list_id: str, cookies: dict, cursor: str = None, count: int = 20
) -> Optional[Dict[str, Any]]:
    """
    Fetch one page of tweets from a Twitter List via ListLatestTweetsTimeline.

    Args:
        list_id: Numeric list ID.
        cookies: dict with 'auth_token' and 'ct0'.
        cursor: Optional pagination cursor from a previous response.
        count: Number of tweets per page (default 20).

    Returns:
        Raw GraphQL response dict, or None on failure.
    """
    query_id = _get_query_id("ListLatestTweetsTimeline")
    headers = build_graphql_headers(cookies)

    variables = {
        "listId": list_id,
        "count": count,
        "withDownvotePerspective": False,
        "withReactionsMetadata": False,
        "withReactionsPerspective": False,
        "withSuperFollowsTweetFields": True,
        "withSuperFollowsUserFields": True,
        "withBirdwatchNotes": True,
    }

    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()

    return _execute_graphql(
        query_id=query_id,
        operation_name="ListLatestTweetsTimeline",
        variables=variables,
        features=dict(LIST_TWEETS_FEATURES),
        field_toggles=dict(BOOKMARK_FIELD_TOGGLES),
        headers=headers,
    )


def parse_list_tweets_entries(response: Dict[str, Any]) -> tuple:
    """
    Extract tweet entries and pagination cursors from a ListLatestTweetsTimeline response.

    Response path: data.list.tweets_timeline.timeline.instructions

    Returns:
        (entries, cursors) — entries can be passed to extract_tweet_data(),
        cursors is a dict with optional 'top' and 'bottom' keys.
    """
    if not response or "data" not in response:
        return [], {}

    data = response["data"]

    # Primary path: data.list.tweets_timeline.timeline.instructions
    instructions = (
        data.get("list", {})
        .get("tweets_timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )

    entries = []
    cursors = {}

    for instruction in instructions:
        inst_type = instruction.get("type", "")

        if inst_type == "TimelineAddEntries":
            for entry in instruction.get("entries", []):
                entry_id = entry.get("entryId", "")
                content = entry.get("content", {})

                # Extract cursor entries
                if entry_id.startswith("cursor-"):
                    cursor_type = content.get("cursorType", "")
                    value = content.get("value", "")
                    if cursor_type == "Top" and value:
                        cursors["top"] = value
                    elif cursor_type == "Bottom" and value:
                        cursors["bottom"] = value
                    continue

                # Skip promoted content
                if "promoted" in entry_id.lower():
                    continue

                # Filter to tweet items only
                item_type = content.get("itemContent", {}).get("itemType", "")
                if item_type == "TimelineTweet":
                    entries.append(entry)
                elif content.get("entryType") == "TimelineTimelineModule":
                    for item in content.get("items", []):
                        sub_type = (item.get("item", {})
                                    .get("itemContent", {})
                                    .get("itemType", ""))
                        if sub_type == "TimelineTweet":
                            entries.append(item)

        elif inst_type == "TimelineAddToModule":
            for item in instruction.get("moduleItems", []):
                item_type = (item.get("item", {})
                             .get("itemContent", {})
                             .get("itemType", ""))
                if item_type == "TimelineTweet":
                    entries.append(item)

    return entries, cursors


def parse_bookmark_entries(response: Dict[str, Any]) -> tuple:
    """
    Extract tweet entries and pagination cursors from a Bookmarks GraphQL response.

    Supports both Bookmarks and BookmarkFolderTimeline responses.
    Response path: data.bookmark_timeline_v2.timeline.instructions
    (different from TweetDetail's threaded_conversation_with_injections_v2)

    Returns:
        (entries, cursors) — entries can be passed to extract_tweet_data(),
        cursors is a dict with optional 'top' and 'bottom' keys.
    """
    if not response or "data" not in response:
        return [], {}

    data = response["data"]

    # Try multiple response paths:
    # Path 1: bookmark_timeline_v2 (Bookmarks endpoint)
    instructions = (
        data.get("bookmark_timeline_v2", {})
        .get("timeline", {})
        .get("instructions", [])
    )
    # Path 2: bookmark_folder_timeline (BookmarkFolderTimeline endpoint)
    if not instructions:
        instructions = (
            data.get("bookmark_folder_timeline", {})
            .get("timeline", {})
            .get("instructions", [])
        )
    # Path 3: generic scan for instructions in nested timeline objects
    if not instructions:
        for v in data.values():
            if isinstance(v, dict):
                timeline = v.get("timeline", {})
                if isinstance(timeline, dict):
                    inst = timeline.get("instructions", [])
                    if inst:
                        instructions = inst
                        break

    entries = []
    cursors = {}

    for instruction in instructions:
        inst_type = instruction.get("type", "")

        if inst_type == "TimelineAddEntries":
            for entry in instruction.get("entries", []):
                entry_id = entry.get("entryId", "")
                content = entry.get("content", {})

                # Extract cursor entries
                if entry_id.startswith("cursor-"):
                    cursor_type = content.get("cursorType", "")
                    value = content.get("value", "")
                    if cursor_type == "Top" and value:
                        cursors["top"] = value
                    elif cursor_type == "Bottom" and value:
                        cursors["bottom"] = value
                    continue

                # Skip promoted content
                if "promoted" in entry_id.lower():
                    continue

                entries.append(entry)

        elif inst_type == "TimelineAddToModule":
            for item in instruction.get("moduleItems", []):
                entries.append(item)

    return entries, cursors


def fetch_search_timeline_page(
    raw_query: str,
    cookies: dict,
    cursor: str = None,
    count: int = 20,
    product: str = "Latest",
) -> Optional[Dict[str, Any]]:
    """
    Fetch one page of search results via SearchTimeline GraphQL endpoint.

    Args:
        raw_query: Search query string (e.g. "from:username since:2025-01-01 until:2025-02-01").
        cookies: dict with 'auth_token' and 'ct0'.
        cursor: Optional pagination cursor from a previous response.
        count: Number of results per page (default 20).
        product: Search product type — "Latest" (chronological) or "Top" (relevance).

    Returns:
        Raw GraphQL response dict, or None on failure.
    """
    query_id = _get_query_id("SearchTimeline")
    headers = build_graphql_headers(cookies)

    variables = {
        "rawQuery": raw_query,
        "count": count,
        "querySource": "typed_query",
        "product": product,
        "withGrokTranslatedBio": False,
    }

    if cursor:
        variables["cursor"] = cursor
        _rate_limit_wait()

    return _execute_graphql(
        query_id=query_id,
        operation_name="SearchTimeline",
        variables=variables,
        features=dict(SEARCH_TIMELINE_FEATURES),
        field_toggles=dict(SEARCH_TIMELINE_FIELD_TOGGLES),
        headers=headers,
        use_post=True,
    )


def parse_search_entries(response: Dict[str, Any]) -> tuple:
    """
    Extract tweet entries and pagination cursors from a SearchTimeline GraphQL response.

    Response path: data.search_by_raw_query.search_timeline.timeline.instructions

    Returns:
        (entries, cursors) — entries can be passed to extract_tweet_data(),
        cursors is a dict with optional 'top' and 'bottom' keys.
    """
    if not response or "data" not in response:
        return [], {}

    data = response["data"]

    # Primary path
    instructions = (
        data.get("search_by_raw_query", {})
        .get("search_timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )

    entries = []
    cursors = {}

    for instruction in instructions:
        inst_type = instruction.get("type", "")

        if inst_type == "TimelineAddEntries":
            for entry in instruction.get("entries", []):
                entry_id = entry.get("entryId", "")
                content = entry.get("content", {})

                # Extract cursor entries
                if entry_id.startswith("cursor-"):
                    cursor_type = content.get("cursorType", "")
                    value = content.get("value", "")
                    if cursor_type == "Top" and value:
                        cursors["top"] = value
                    elif cursor_type == "Bottom" and value:
                        cursors["bottom"] = value
                    continue

                # Skip promoted content
                if "promoted" in entry_id.lower():
                    continue

                entries.append(entry)

        elif inst_type == "TimelineAddToModule":
            for item in instruction.get("moduleItems", []):
                entries.append(item)

        elif inst_type == "TimelineReplaceEntry":
            # Page 2+ returns cursors via ReplaceEntry instead of AddEntries
            entry = instruction.get("entry", {})
            entry_id = entry.get("entryId", "")
            content = entry.get("content", {})
            if entry_id.startswith("cursor-"):
                cursor_type = content.get("cursorType", "")
                value = content.get("value", "")
                if cursor_type == "Top" and value:
                    cursors["top"] = value
                elif cursor_type == "Bottom" and value:
                    cursors["bottom"] = value

    return entries, cursors


def parse_search_people_entries(response: Dict[str, Any]) -> tuple:
    """v0.23.0: parse SearchTimeline response for `product=People`.

    Response shape is identical to parse_search_entries, but entries are
    TimelineUser items (consumable by extract_user_data).

    Returns:
        (entries, cursors) — entries can be passed to extract_user_data().
    """
    if not response or "data" not in response:
        return [], {}

    instructions = (
        response["data"]
        .get("search_by_raw_query", {})
        .get("search_timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )

    entries: List[dict] = []
    cursors: Dict[str, str] = {}

    for instruction in instructions:
        inst_type = instruction.get("type", "")
        if inst_type == "TimelineAddEntries":
            for entry in instruction.get("entries", []):
                entry_id = entry.get("entryId", "")
                content = entry.get("content", {})
                if entry_id.startswith("cursor-"):
                    cursor_type = content.get("cursorType", "")
                    value = content.get("value", "")
                    if cursor_type == "Top" and value:
                        cursors["top"] = value
                    elif cursor_type == "Bottom" and value:
                        cursors["bottom"] = value
                    continue
                # Only keep TimelineUser items (drops promoted, modules, etc.)
                if content.get("entryType") == "TimelineTimelineItem":
                    item_type = content.get("itemContent", {}).get("itemType", "")
                    if item_type == "TimelineUser":
                        entries.append(entry)
        elif inst_type == "TimelineReplaceEntry":
            entry = instruction.get("entry", {})
            entry_id = entry.get("entryId", "")
            content = entry.get("content", {})
            if entry_id.startswith("cursor-"):
                cursor_type = content.get("cursorType", "")
                value = content.get("value", "")
                if cursor_type == "Top" and value:
                    cursors["top"] = value
                elif cursor_type == "Bottom" and value:
                    cursors["bottom"] = value

    _sort_entries_by_sortindex(entries)
    return entries, cursors

def resolve_query_ids(user_agent: str = None) -> Dict[str, str]:
    """
    Dynamically resolve queryIds from multiple sources.

    Resolution order (first wins per operation):
        Tier 0: Disk cache (queryids from previous run, < 1h old)
        Tier 1: Community source (fa0311/twitter-openapi, single HTTP request)
        Tier 2: JS bundle scan (x.com HTML → per-bundle JS downloads)
        Tier 3: Hardcoded fallback constants

    Returns:
        Dict mapping operation_name → query_id.
    """
    ua = user_agent or DEFAULT_USER_AGENT
    result = {}
    all_ops = set(_fallback_query_ids())

    # --- Tier 0: Disk cache ---
    cached_ids = _load_queryid_cache()
    if cached_ids:
        for op in all_ops:
            if op in cached_ids:
                result[op] = cached_ids[op]
        if len(result) >= len(all_ops):
            logger.debug(f"All {len(result)} queryIds loaded from disk cache")
            return result

    # --- Tier 1: Community source (fa0311/twitter-openapi) ---
    if set(result) < all_ops:
        community = _resolve_community_query_ids()
        for op in all_ops:
            if op not in result and op in community:
                result[op] = community[op]
        if len(result) >= len(all_ops):
            logger.debug(f"All {len(result)} queryIds resolved (cache + community)")
            _save_queryid_cache(result)
            return result

    # --- Tier 2: JS bundle scan (only for missing operations) ---
    missing = all_ops - set(result)

    if missing:
        try:
            html = _fetch_home_html(ua)
            if html:
                # TweetDetail — from api.<hash>a.js
                if "TweetDetail" in missing:
                    api_match = re.search(r'api:"([a-zA-Z0-9]+)"', html)
                    if api_match:
                        chunk_hash = api_match.group(1)
                        chunk_url = f"https://abs.twimg.com/responsive-web/client-web/api.{chunk_hash}a.js"
                        qid = _fetch_and_extract_query_id(chunk_url, "TweetDetail", ua)
                        if qid:
                            result["TweetDetail"] = qid
                            logger.debug(f"Resolved TweetDetail: queryId={qid}")

                # TweetResultByRestId — from main.<hash>.js (different bundle!)
                main_match = re.search(r'main:"([a-zA-Z0-9]+)"', html)
                if "TweetResultByRestId" in missing and main_match:
                    chunk_hash = main_match.group(1)
                    chunk_url = f"https://abs.twimg.com/responsive-web/client-web/main.{chunk_hash}a.js"
                    qid = _fetch_and_extract_query_id(chunk_url, "TweetResultByRestId", ua)
                    if qid:
                        result["TweetResultByRestId"] = qid
                        logger.debug(f"Resolved TweetResultByRestId: queryId={qid}")

                # ArticleEntityResultByRestId — from bundle.TwitterArticles.<hash>a.js
                if "ArticleEntityResultByRestId" in missing:
                    article_match = re.search(r'bundle\.TwitterArticles:"([a-zA-Z0-9]+)"', html)
                    if article_match:
                        chunk_hash = article_match.group(1)
                        chunk_url = f"https://abs.twimg.com/responsive-web/client-web/bundle.TwitterArticles.{chunk_hash}a.js"
                        qid = _fetch_and_extract_query_id(chunk_url, "ArticleEntityResultByRestId", ua)
                        if qid:
                            result["ArticleEntityResultByRestId"] = qid
                            logger.debug(f"Resolved ArticleEntityResultByRestId: queryId={qid}")

                # Bookmarks — typically in main.<hash>.js
                if "Bookmarks" not in result and main_match:
                    chunk_hash = main_match.group(1)
                    chunk_url = f"https://abs.twimg.com/responsive-web/client-web/main.{chunk_hash}a.js"
                    qid = _fetch_and_extract_query_id(chunk_url, "Bookmarks", ua)
                    if qid:
                        result["Bookmarks"] = qid
                        logger.debug(f"Resolved Bookmarks: queryId={qid}")

                # BookmarkFoldersSlice & BookmarkFolderTimeline
                bkf_ops_missing = {"BookmarkFoldersSlice", "BookmarkFolderTimeline"} - set(result)
                if bkf_ops_missing:
                    bkf_match = re.search(r'bundle\.BookmarkFolders:"([a-zA-Z0-9]+)"', html)
                    if bkf_match:
                        chunk_hash = bkf_match.group(1)
                        chunk_url = f"https://abs.twimg.com/responsive-web/client-web/bundle.BookmarkFolders.{chunk_hash}a.js"
                        for op in bkf_ops_missing.copy():
                            qid = _fetch_and_extract_query_id(chunk_url, op, ua)
                            if qid:
                                result[op] = qid
                                bkf_ops_missing.discard(op)
                                logger.debug(f"Resolved {op}: queryId={qid}")

                    # Also try main bundle for bookmark folder ops
                    if bkf_ops_missing and main_match:
                        chunk_hash = main_match.group(1)
                        chunk_url = f"https://abs.twimg.com/responsive-web/client-web/main.{chunk_hash}a.js"
                        for op in bkf_ops_missing:
                            qid = _fetch_and_extract_query_id(chunk_url, op, ua)
                            if qid:
                                result[op] = qid
                                logger.debug(f"Resolved {op}: queryId={qid}")

                # UserByScreenName & UserTweets & List ops — typically in main bundle
                main_ops_missing = {
                    "UserByScreenName", "UserTweets", "ListByRestId", "ListLatestTweetsTimeline",
                    # v0.22.0: twe-borrowed ops also live in main bundle
                    "Followers", "Following", "BlueVerifiedFollowers", "Likes",
                    "UserTweetsAndReplies", "ListMembers", "ListSubscribers",
                    # v0.23.0
                    "ModeratedTimeline",
                    "Retweeters", "Favoriters",
                } - set(result)
                if main_ops_missing and main_match:
                    chunk_hash = main_match.group(1)
                    chunk_url = f"https://abs.twimg.com/responsive-web/client-web/main.{chunk_hash}a.js"
                    for op in main_ops_missing:
                        qid = _fetch_and_extract_query_id(chunk_url, op, ua)
                        if qid:
                            result[op] = qid
                            logger.debug(f"Resolved {op}: queryId={qid}")

                # SearchTimeline — try main bundle first, then search-related bundles
                if "SearchTimeline" not in result:
                    if main_match:
                        chunk_hash = main_match.group(1)
                        chunk_url = f"https://abs.twimg.com/responsive-web/client-web/main.{chunk_hash}a.js"
                        qid = _fetch_and_extract_query_id(chunk_url, "SearchTimeline", ua)
                        if qid:
                            result["SearchTimeline"] = qid
                            logger.debug(f"Resolved SearchTimeline from main: queryId={qid}")

                    if "SearchTimeline" not in result:
                        for pattern in [
                            r'bundle\.search:"([a-zA-Z0-9]+)"',
                            r'bundle\.Search:"([a-zA-Z0-9]+)"',
                            r'bundle\.SearchTimeline:"([a-zA-Z0-9]+)"',
                            r'bundle\.explore:"([a-zA-Z0-9]+)"',
                            r'bundle\.Explore:"([a-zA-Z0-9]+)"',
                        ]:
                            bm = re.search(pattern, html)
                            if bm:
                                chunk_hash = bm.group(1)
                                bundle_name = pattern.split(r'\.')[1].split(':')[0].rstrip('\\')
                                chunk_url = f"https://abs.twimg.com/responsive-web/client-web/bundle.{bundle_name}.{chunk_hash}a.js"
                                qid = _fetch_and_extract_query_id(chunk_url, "SearchTimeline", ua)
                                if qid:
                                    result["SearchTimeline"] = qid
                                    logger.debug(f"Resolved SearchTimeline from bundle.{bundle_name}: queryId={qid}")
                                    break

                    # Brute-force: scan all bundle.* entries in HTML for SearchTimeline
                    if "SearchTimeline" not in result:
                        for bm in re.finditer(r'bundle\.(\w+):"([a-zA-Z0-9]+)"', html):
                            bundle_name = bm.group(1)
                            chunk_hash = bm.group(2)
                            chunk_url = f"https://abs.twimg.com/responsive-web/client-web/bundle.{bundle_name}.{chunk_hash}a.js"
                            qid = _fetch_and_extract_query_id(chunk_url, "SearchTimeline", ua)
                            if qid:
                                result["SearchTimeline"] = qid
                                logger.debug(f"Resolved SearchTimeline from bundle.{bundle_name}: queryId={qid}")
                                break

                    # Last resort: extract main JS URL from <script src="...main.HASH.js">
                    if "SearchTimeline" not in result:
                        main_src = re.search(
                            r'src="(https://abs\.twimg\.com/responsive-web/client-web/main\.[a-zA-Z0-9]+\.js)"',
                            html,
                        )
                        if main_src:
                            qid = _fetch_and_extract_query_id(main_src.group(1), "SearchTimeline", ua)
                            if qid:
                                result["SearchTimeline"] = qid
                                logger.debug(f"Resolved SearchTimeline from script src: queryId={qid}")

        except Exception as e:
            logger.warning(f"Dynamic queryId resolution failed ({e}), using fallbacks")

    # --- Tier 3: Hardcoded fallbacks ---
    fallbacks = _fallback_query_ids()
    for op, qid in fallbacks.items():
        if op not in result:
            result[op] = qid

    # Save resolved queryIds to disk for next cold start
    _save_queryid_cache(result)

    return result


# ---------------------------------------------------------------------------
# Response parsing helpers — from baoyu thread.ts parseTweetsAndToken
# ---------------------------------------------------------------------------

def parse_tweet_entries(response: Dict[str, Any]) -> List[dict]:
    """
    Extract tweet entries from a TweetDetail GraphQL response.

    Checks both v2 and v1 conversation paths (matches baoyu thread.ts).
    Filters out "you_might_also_like" recommendation entries.

    Returns:
        List of entry dicts from the timeline instructions.
    """
    if not response or "data" not in response:
        return []

    data = response["data"]

    # Try v2 first, then v1 (matches baoyu thread.ts)
    instructions = (
        data.get("threaded_conversation_with_injections_v2", {}).get("instructions")
        or data.get("threaded_conversation_with_injections", {}).get("instructions")
        or []
    )

    entries = []

    for instruction in instructions:
        inst_type = instruction.get("type", "")

        if inst_type == "TimelineAddEntries":
            for entry in instruction.get("entries", []):
                # Skip "you_might_also_like" recommendations
                component = (
                    entry.get("content", {})
                    .get("clientEventInfo", {})
                    .get("component", "")
                )
                if component == "you_might_also_like":
                    continue
                entries.append(entry)

        elif inst_type == "TimelineAddToModule":
            for item in instruction.get("moduleItems", []):
                entries.append(item)

    return entries


def parse_cursors(entries: List[dict]) -> Dict[str, str]:
    """
    Extract pagination cursors from timeline entries.

    Cursor types (matches baoyu thread.ts parseInstruction):
        - top: TimelineTimelineCursor with cursorType "Top"
        - bottom: TimelineTimelineCursor with cursorType "Bottom"
        - more: cursor with cursorType "ShowMore" or "ShowMoreThreads"

    Returns:
        dict with optional keys: 'top', 'bottom', 'more'
    """
    cursors = {}

    for entry in entries:
        entry_id = entry.get("entryId", "")
        content = entry.get("content", {})
        entry_type = content.get("entryType", "")
        cursor_type = content.get("cursorType", "")

        # Top-level cursor entries (cursor-top-xxx, cursor-bottom-xxx)
        if entry_type == "TimelineTimelineCursor":
            value = content.get("value", "")
            if cursor_type == "Top" and value:
                cursors["top"] = value
            elif cursor_type == "Bottom" and value:
                cursors["bottom"] = value

        # Also check itemContent for cursors (nested in conversation modules)
        item_content = content.get("itemContent", {})
        if item_content.get("entryType") == "TimelineTimelineCursor":
            value = item_content.get("value", "")
            ct = item_content.get("cursorType", "")
            if ct == "Top" and value:
                cursors["top"] = value
            elif ct == "Bottom" and value:
                cursors["bottom"] = value

        # ShowMore / ShowMoreThreads cursors inside conversation thread modules
        if "conversationthread" in entry_id or content.get("entryType") == "TimelineTimelineModule":
            items = content.get("items", [])
            for item in items:
                ic = item.get("item", {}).get("itemContent", {})
                ic_type = ic.get("itemType", ic.get("__typename", ""))
                ic_cursor_type = ic.get("cursorType", "")
                if ic_type == "TimelineTimelineCursor" and ic_cursor_type in ("ShowMore", "ShowMoreThreads"):
                    value = ic.get("value", "")
                    if value:
                        cursors["more"] = value

    return cursors


def extract_tweet_data(entry: dict) -> Optional[Dict[str, Any]]:
    """
    Extract structured tweet data from a timeline entry.

    Navigates TweetWithVisibilityResults wrappers and extracts:
    id, text (with note_tweet long text), author, media, quoted tweet, metrics.

    Matches baoyu thread.ts parseTweetsAndToken tweet extraction path.

    Returns:
        Flat dict with tweet fields, or None if entry is not a tweet.
    """
    # P0-3: pinned-tweet marker propagated from parse_user_tweets_entries
    is_pinned = bool(entry.get("_is_pinned", False))

    # Navigate to the tweet result object
    content = entry.get("content", entry)
    item_content = (
        content.get("itemContent")
        or content.get("item", {}).get("itemContent")
        or {}
    )

    tweet_results = item_content.get("tweet_results", {})
    result = tweet_results.get("result", {})

    # Handle TweetWithVisibilityResults wrapper
    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet", {})

    # P0-2: Explicit typename handling for tombstone/unavailable (twe utils/api.ts:222-247)
    typename = result.get("__typename")
    if typename == "TweetTombstone":
        tombstone_text = (
            result.get("tombstone", {}).get("text", {}).get("text", "")
            or "unknown reason"
        )
        logger.warning(f"[Twitter] TweetTombstone skipped: {tombstone_text}")
        return None
    if typename == "TweetUnavailable":
        reason = result.get("reason", "") or "likely deleted/protected"
        logger.warning(f"[Twitter] TweetUnavailable skipped: {reason}")
        return None
    if typename != "Tweet":
        if typename:
            logger.debug(f"[Twitter] Unknown tweet typename {typename}, skipping entry")
        return None

    legacy = result.get("legacy", {})
    core = result.get("core", {})
    user_results = core.get("user_results", {}).get("result", {})
    user_legacy = user_results.get("legacy", {})
    # New API format: screen_name/name moved to user_results.result.core
    user_core = user_results.get("core", {})

    # Extract note_tweet (long text) if available — priority over legacy.full_text
    note_tweet = (
        result.get("note_tweet", {})
        .get("note_tweet_results", {})
        .get("result", {})
    )
    full_text = note_tweet.get("text") or legacy.get("full_text", "")

    # Expand t.co short URLs to original URLs (note_tweet entity_set first, then legacy)
    note_urls = note_tweet.get("entity_set", {}).get("urls", [])
    legacy_urls = legacy.get("entities", {}).get("urls", [])
    for url_entity in (note_urls or legacy_urls):
        short_url = url_entity.get("url", "")
        expanded_url = url_entity.get("expanded_url", "")
        if short_url and expanded_url and short_url in full_text:
            full_text = full_text.replace(short_url, expanded_url)

    # Apply richtext_tags (bold/italic) from note_tweet to produce Markdown formatting
    full_text = _apply_richtext_tags(full_text, note_tweet)

    # Extract media from extended_entities
    media_list = legacy.get("extended_entities", {}).get("media", [])
    images = []
    videos = []
    for media in media_list:
        media_type = media.get("type", "")
        if media_type == "photo":
            images.append(media.get("media_url_https", ""))
        elif media_type in ("video", "animated_gif"):
            # P0-4: don't filter by content_type — HLS m3u8 has no bitrate so
            # it gets excluded naturally; mp4/webm both have bitrate
            variants = media.get("video_info", {}).get("variants", [])
            bitrate_variants = [v for v in variants if v.get("bitrate")]
            if bitrate_variants:
                best = max(bitrate_variants, key=lambda v: v.get("bitrate", 0))
                videos.append(best.get("url", ""))
            # Also keep poster image
            images.append(media.get("media_url_https", ""))

    # Extract quoted tweet (full data: long text, media, metrics, t.co expansion)
    quoted_tweet = None
    quoted_status = result.get("quoted_status_result", {}).get("result", {})
    if quoted_status.get("__typename") == "TweetWithVisibilityResults":
        quoted_status = quoted_status.get("tweet", {})

    # P0-2: Log tombstone/unavailable on quoted tweet
    q_typename = quoted_status.get("__typename")
    if q_typename == "TweetTombstone":
        qt_text = (
            quoted_status.get("tombstone", {}).get("text", {}).get("text", "")
            or "unknown reason"
        )
        logger.warning(f"[Twitter] Quoted TweetTombstone: {qt_text}")
        quoted_tweet = {"is_tombstone": True, "tombstone_text": qt_text}
    elif q_typename == "TweetUnavailable":
        q_reason = quoted_status.get("reason", "") or "likely deleted/protected"
        logger.warning(f"[Twitter] Quoted TweetUnavailable: {q_reason}")
        quoted_tweet = {"is_unavailable": True, "tombstone_text": q_reason}
    elif q_typename == "Tweet":
        q_legacy = quoted_status.get("legacy", {})
        q_user = (
            quoted_status.get("core", {})
            .get("user_results", {})
            .get("result", {})
        )
        q_user_legacy = q_user.get("legacy", {})
        q_user_core = q_user.get("core", {})

        # Use note_tweet for full text (avoids 280-char truncation)
        q_note = (
            quoted_status.get("note_tweet", {})
            .get("note_tweet_results", {})
            .get("result", {})
        )
        q_text = q_note.get("text") or q_legacy.get("full_text", "")

        # Expand t.co URLs in quoted tweet
        q_note_urls = q_note.get("entity_set", {}).get("urls", [])
        q_legacy_urls = q_legacy.get("entities", {}).get("urls", [])
        for q_url_ent in (q_note_urls or q_legacy_urls):
            q_short = q_url_ent.get("url", "")
            q_expanded = q_url_ent.get("expanded_url", "")
            if q_short and q_expanded and q_short in q_text:
                q_text = q_text.replace(q_short, q_expanded)

        # Apply richtext_tags to quoted tweet text
        q_text = _apply_richtext_tags(q_text, q_note)

        # Extract media from quoted tweet
        q_media_list = q_legacy.get("extended_entities", {}).get("media", [])
        q_images = []
        q_videos = []
        for q_media in q_media_list:
            q_mtype = q_media.get("type", "")
            if q_mtype == "photo":
                q_images.append(q_media.get("media_url_https", ""))
            elif q_mtype in ("video", "animated_gif"):
                q_variants = q_media.get("video_info", {}).get("variants", [])
                q_bitrate_variants = [v for v in q_variants if v.get("bitrate")]
                if q_bitrate_variants:
                    q_best = max(q_bitrate_variants, key=lambda v: v.get("bitrate", 0))
                    q_videos.append(q_best.get("url", ""))
                q_images.append(q_media.get("media_url_https", ""))

        # Remove trailing media t.co URL from text
        for q_media in q_legacy.get("entities", {}).get("media", []):
            q_short = q_media.get("url", "")
            if q_short and q_short in q_text:
                q_text = q_text.replace(q_short, "").strip()

        q_screen_name = q_user_legacy.get("screen_name", "") or q_user_core.get("screen_name", "")
        quoted_tweet = {
            "id": q_legacy.get("id_str", ""),
            "text": q_text,
            "author": q_screen_name,
            "author_name": q_user_legacy.get("name", "") or q_user_core.get("name", ""),
            "images": q_images,
            "videos": q_videos,
            "likes": q_legacy.get("favorite_count", 0),
            "retweets": q_legacy.get("retweet_count", 0),
            "views": quoted_status.get("views", {}).get("count", "0"),
            "url": f"https://x.com/{q_screen_name}/status/{q_legacy.get('id_str', '')}",
        }

    # Extract article reference if present
    article = _extract_article_ref(result)

    # Extract hashtags from entities (check note_tweet entity_set first, then legacy)
    note_hashtags = note_tweet.get("entity_set", {}).get("hashtags", [])
    legacy_hashtags = legacy.get("entities", {}).get("hashtags", [])
    raw_hashtags = note_hashtags or legacy_hashtags
    hashtags = [h.get("text", "") for h in raw_hashtags if h.get("text")]

    return {
        "id": legacy.get("id_str", result.get("rest_id", "")),
        "rest_id": result.get("rest_id", ""),
        "text": full_text,
        "author": user_legacy.get("screen_name", "") or user_core.get("screen_name", ""),
        "author_name": user_legacy.get("name", "") or user_core.get("name", ""),
        "user_id": user_legacy.get("id_str", "") or user_results.get("rest_id", "") or legacy.get("user_id_str", ""),
        "conversation_id": legacy.get("conversation_id_str", ""),
        "in_reply_to_user_id": legacy.get("in_reply_to_user_id_str", ""),
        "in_reply_to_status_id": legacy.get("in_reply_to_status_id_str", ""),
        "created_at": legacy.get("created_at", ""),
        "images": images,
        "videos": videos,
        "quoted_tweet": quoted_tweet,
        "article": article,
        "hashtags": hashtags,
        "likes": legacy.get("favorite_count", 0),
        "retweets": legacy.get("retweet_count", 0),
        "replies": legacy.get("reply_count", 0),
        "bookmarks": legacy.get("bookmark_count", 0),
        "views": result.get("views", {}).get("count", "0"),
        # New metadata fields
        "quote_count": legacy.get("quote_count", 0),
        "lang": legacy.get("lang", ""),
        "source_app": _parse_source_app(result.get("source", "")),
        "possibly_sensitive": legacy.get("possibly_sensitive", False),
        # Author profile fields
        "is_blue_verified": user_results.get("is_blue_verified", False),
        "followers_count": user_legacy.get("followers_count", 0),
        "statuses_count": user_legacy.get("statuses_count", 0),
        "listed_count": user_legacy.get("listed_count", 0),
        # P0-3: pinned tweet marker (TimelinePinEntry instruction)
        "is_pinned": is_pinned,
        # Keep raw result for article extraction in later PRs
        "_raw_result": result,
    }


# ---------------------------------------------------------------------------
# Disk cache helpers
# ---------------------------------------------------------------------------

def _disk_cache_path(name: str) -> Path:
    """Return path to a disk cache file under {data_dir}/cache/."""
    return get_data_dir() / "cache" / name


def _load_transaction_cache() -> Optional[dict]:
    """Load cached homepage HTML + ondemand.s text from disk (1h TTL)."""
    path = _disk_cache_path("twitter_transaction_cache.json")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) >= _DISK_CACHE_TTL:
            return None
        if "home_html" not in data or "ondemand_text" not in data:
            return None
        # Validate that ondemand_text is not empty (may happen if cached during failure)
        if not data.get("ondemand_text"):
            return None
        return data
    except Exception:
        return None


def _save_transaction_cache(home_html: str, ondemand_text: str):
    """Save homepage HTML + ondemand.s text to disk cache."""
    path = _disk_cache_path("twitter_transaction_cache.json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "ts": time.time(),
            "home_html": home_html,
            "ondemand_text": ondemand_text,
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        logger.debug(f"Transaction cache saved to {path}")
    except Exception as e:
        logger.debug(f"Failed to save transaction cache: {e}")


def _load_queryid_cache() -> Optional[Dict[str, str]]:
    """Load cached queryIds from disk (1h TTL)."""
    path = _disk_cache_path("twitter_queryid_cache.json")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) >= _DISK_CACHE_TTL:
            return None
        return data.get("ids", {})
    except Exception:
        return None


def _save_queryid_cache(query_ids: Dict[str, str]):
    """Save resolved queryIds to disk cache."""
    path = _disk_cache_path("twitter_queryid_cache.json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"ts": time.time(), "ids": query_ids}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        logger.debug(f"QueryId cache saved to {path} ({len(query_ids)} ops)")
    except Exception as e:
        logger.debug(f"Failed to save queryId cache: {e}")


def _resolve_community_query_ids() -> Dict[str, str]:
    """Fetch queryIds from fa0311/twitter-openapi community source.

    Returns a dict mapping operationName → queryId. On failure returns {}.
    The community source is a single HTTP request to GitHub raw content.
    """
    try:
        resp = http_client.get(
            _COMMUNITY_QUERYID_URL,
            headers={"user-agent": DEFAULT_USER_AGENT},
            timeout=8,
        )
        http_client.raise_for_status(resp)
        data = resp.json()
        result = {}
        for op_name, op_data in data.items():
            if isinstance(op_data, dict) and "queryId" in op_data:
                result[op_name] = op_data["queryId"]
        if result:
            logger.debug(f"[GraphQL] Community source: {len(result)} queryIds fetched")
        return result
    except Exception as e:
        logger.debug(f"[GraphQL] Community queryId source unavailable: {e}")
        return {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_transaction_id(method: str, path: str) -> str:
    """Generate x-client-transaction-id for Twitter anti-bot verification.

    Uses XClientTransaction library to compute a signed header value
    based on x.com homepage SVG animations + ondemand.s JS indices.
    Caches the generator instance in memory (TTL 30 min) and source
    data on disk (TTL 1 hour) to avoid cold-start HTTP requests.
    Returns empty string if generation fails (graceful degradation).
    """
    global _transaction_generator, _transaction_generator_timestamp, _cached_home_html

    now = time.time()

    # Re-initialize if never attempted (timestamp=0) or TTL expired
    if _transaction_generator_timestamp == 0 or (now - _transaction_generator_timestamp) >= _TRANSACTION_TTL:
        try:
            import bs4
            from x_client_transaction import ClientTransaction
            from x_client_transaction.utils import get_ondemand_file_url

            ua = DEFAULT_USER_AGENT
            home_html = None
            ondemand_text = None

            # Try disk cache first (avoids 2 HTTP requests on cold start)
            cached = _load_transaction_cache()
            if cached:
                home_html = cached["home_html"]
                ondemand_text = cached["ondemand_text"]
            else:
                # Fetch from network (share _cached_home_html with queryId resolver)
                home_html = _fetch_home_html(ua)
                if not home_html:
                    _transaction_generator_timestamp = now
                    return ""

                home_soup = bs4.BeautifulSoup(home_html, "html.parser")
                ondemand_url = get_ondemand_file_url(response=home_soup)
                if ondemand_url:
                    try:
                        ondemand_resp = http_client.get(
                            ondemand_url, headers={"user-agent": ua}, timeout=15,
                        )
                        ondemand_text = ondemand_resp.text
                    except Exception as e:
                        logger.warning(f"[GraphQL] Failed to fetch ondemand.s: {e}")
                        ondemand_text = ""
                    # Only cache when ondemand_text is non-empty (avoid polluting cache)
                    if ondemand_text:
                        _save_transaction_cache(home_html, ondemand_text)
                else:
                    ondemand_text = ""
                    logger.warning(
                        "[GraphQL] ondemand.s URL not found in x.com HTML — "
                        "try: pip install xclienttransaction --upgrade"
                    )

            # Dynamic feature flags update from x.com inline scripts
            _update_features_from_html(home_html)

            if not ondemand_text:
                logger.warning(
                    "[GraphQL] ondemand.s unavailable — transaction-id skipped. "
                    "SearchTimeline may return 404. Try: pip install xclienttransaction --upgrade"
                )
                _transaction_generator_timestamp = now
                return ""
            home_soup = bs4.BeautifulSoup(home_html, "html.parser")
            _transaction_generator = ClientTransaction(
                home_page_response=home_soup,
                ondemand_file_response=ondemand_text,
            )
            _transaction_generator_timestamp = now
            logger.debug("x-client-transaction-id generator initialized")
        except ImportError:
            logger.warning(
                "[GraphQL] XClientTransaction not installed. "
                "Some endpoints (SearchTimeline) may return 404.\n"
                "  pip install XClientTransaction"
            )
            return ""
        except Exception as e:
            logger.warning(f"[GraphQL] Failed to init transaction generator: {e}")
            _transaction_generator_timestamp = now
            return ""

    if _transaction_generator is None:
        return ""
    try:
        return _transaction_generator.generate_transaction_id(
            method=method, path=path,
        )
    except Exception as e:
        logger.debug(f"[GraphQL] Failed to generate transaction id: {e}")
        return ""


# Track last cookie refresh prompt to avoid spamming
_last_cookie_prompt_time: float = 0
_COOKIE_PROMPT_COOLDOWN: int = 60  # seconds


def _prompt_cookie_refresh_via_cdp() -> bool:
    """Prompt user to refresh Twitter cookies via CDP when GraphQL returns 401/403.

    Returns True if cookies were successfully refreshed and should retry.
    """
    global _last_cookie_prompt_time

    now = time.time()
    if now - _last_cookie_prompt_time < _COOKIE_PROMPT_COOLDOWN:
        logger.debug("Cookie refresh prompt cooldown, skipping...")
        return False
    _last_cookie_prompt_time = now

    # Check if CDP is available
    from feedgrab.config import chrome_cdp_port
    port = chrome_cdp_port()

    # Try to connect to CDP to check availability
    cdp_available = False
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/version",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                cdp_available = True
    except Exception:
        pass

    if not cdp_available:
        print("\n" + "=" * 60)
        print("⚠️  Twitter/X Cookie 已过期或账号受限")
        print("=" * 60)
        print("\n检测到 GraphQL 401/403 错误，当前 Cookie 可能已失效。")
        print("\n刷新 Cookie 的方法：")
        print("1. 开启 Chrome 远程调试端口，然后使用 CDP 自动提取：")
        print(f"   chrome.exe --remote-debugging-port={port}")
        print("\n2. 然后运行：")
        print("   feedgrab login twitter")
        print("\n3. 或使用环境变量手动设置：")
        print("   X_AUTH_TOKEN=xxx X_CT0=yyy feedgrab <url>")
        print("\n" + "=" * 60)
        return False

    # CDP is available, prompt for auto-refresh
    print("\n" + "=" * 60)
    print("⚠️  Twitter/X Cookie 已过期或账号受限")
    print("=" * 60)
    print("\n检测到 GraphQL 401/403 错误，当前 Cookie 可能已失效。")
    print(f"✓ Chrome 远程调试端口已检测到 (127.0.0.1:{port})")

    if _is_non_interactive_cookie_refresh_runtime():
        from feedgrab.config import chrome_cdp_login

        if not chrome_cdp_login():
            logger.warning("Skipping interactive Twitter cookie refresh in sidecar worker")
            print("\n当前运行在非交互 worker 中，跳过命令行确认并尝试其他抓取方式...")
            return False
        print("\n当前运行在非交互 worker 中，自动通过 CDP 尝试刷新 Cookie...")
        return _refresh_twitter_cookie_via_cdp()

    try:
        response = input("\n是否通过 CDP 自动获取新 Cookie? [Y/n]: ").strip().lower()
        if response and response not in ("y", "yes", ""):
            print("跳过自动刷新，将尝试使用其他方式抓取...")
            return False
    except (EOFError, KeyboardInterrupt):
        print("\n跳过自动刷新...")
        return False

    return _refresh_twitter_cookie_via_cdp()


def _is_non_interactive_cookie_refresh_runtime() -> bool:
    return any(
        os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
        for name in ("FEEDGRAB_WORKER_MODE", "FEEDGRAB_DESKTOP_NONINTERACTIVE")
    )


def _refresh_twitter_cookie_via_cdp() -> bool:
    # Perform CDP cookie extraction
    print("\n正在通过 CDP 提取 Cookie...")
    try:
        from feedgrab.login import _login_via_cdp

        session_path = Path(os.environ.get("FEEDGRAB_DATA_DIR", "sessions")) / "twitter.json"
        ok = _login_via_cdp("twitter", session_path)

        if ok:
            print("✅ Cookie 刷新成功，将重试当前请求...")
            return True
        else:
            print("❌ CDP Cookie 提取失败，请手动登录。")
            return False

    except Exception as e:
        logger.warning(f"CDP cookie refresh failed: {e}")
        print(f"❌ CDP Cookie 提取失败: {e}")
        return False


def _build_cookie_header(cookies: dict) -> str:
    """Build Cookie header string from cookies dict."""
    parts = []
    if cookies.get("auth_token"):
        parts.append(f"auth_token={cookies['auth_token']}")
    if cookies.get("ct0"):
        parts.append(f"ct0={cookies['ct0']}")
    # Include other common Twitter cookies if present
    for key in ["lang", "twid", "gt"]:
        if cookies.get(key):
            parts.append(f"{key}={cookies[key]}")
    return "; ".join(parts)


def _execute_graphql(
    query_id: str,
    operation_name: str,
    variables: dict,
    features: dict,
    field_toggles: dict,
    headers: dict,
    use_post: bool = False,
) -> Optional[Dict[str, Any]]:
    """Execute a GraphQL request against X's API.

    use_post=True sends a POST request (required for SearchTimeline after
    Twitter's migration).  Other endpoints keep using GET by default.
    """
    # Always inject this feature (matches baoyu http.ts buildFeatureMap)
    features["responsive_web_graphql_exclude_directive_enabled"] = True

    url = f"{GRAPHQL_BASE}/{query_id}/{operation_name}"
    path = f"/i/api/graphql/{query_id}/{operation_name}"
    method = "POST" if use_post else "GET"

    # Inject x-client-transaction-id (required by SearchTimeline etc.)
    tid = _get_transaction_id(method, path)
    if tid:
        headers["x-client-transaction-id"] = tid

    if use_post:
        # POST: parameters go in JSON body, features sent in full
        body = {
            "variables": variables,
            "queryId": query_id,
            "features": features,
        }
        if field_toggles:
            body["fieldToggles"] = field_toggles
        headers["Referer"] = "https://x.com/compose/post"
    else:
        # GET: parameters go in URL query string, compact features
        compact_features = {k: v for k, v in features.items() if v}
        params = {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(compact_features, separators=(",", ":")),
            "fieldToggles": json.dumps(field_toggles, separators=(",", ":")),
        }

    try:
        if use_post:
            resp = http_client.post(url, json=body, headers=headers, timeout=30)
        else:
            resp = http_client.get(url, params=params, headers=headers, timeout=30)

        if resp.status_code in (401, 403):
            logger.error(f"GraphQL {resp.status_code} — cookies may have expired or account restricted")
            # Prompt user to refresh cookies via CDP
            if _prompt_cookie_refresh_via_cdp():
                # Retry the request with new cookies
                from feedgrab.fetchers.twitter_cookies import load_twitter_cookies
                new_cookies = load_twitter_cookies()
                if new_cookies:
                    # Update headers with new cookies
                    headers["cookie"] = _build_cookie_header(new_cookies)
                    # Retry the request
                    if use_post:
                        resp = http_client.post(url, json=body, headers=headers, timeout=30)
                    else:
                        resp = http_client.get(url, params=params, headers=headers, timeout=30)
                    if resp.status_code not in (401, 403):
                        # Retry succeeded, continue processing
                        http_client.raise_for_status(resp)
                        data = resp.json()
                        if "errors" in data:
                            for err in data["errors"]:
                                logger.warning(f"GraphQL error: {err.get('message', 'unknown')}")
                        return data
            return None
        if resp.status_code == 429:
            logger.error("GraphQL 429 Rate Limited — too many requests")
            # Notify cookie rotation system
            from feedgrab.fetchers.twitter_cookies import mark_cookie_rate_limited
            mark_cookie_rate_limited()
            return None

        http_client.raise_for_status(resp)
        data = resp.json()

        if "errors" in data:
            for err in data["errors"]:
                msg = err.get("message", "")
                logger.warning(f"GraphQL error: {msg}")
            # Detect account-level restrictions (locked, suspended, etc.)
            # Twitter returns HTTP 200 + errors for these — not 401/403/429
            error_msgs = " ".join(
                e.get("message", "") for e in data.get("errors", [])
            ).lower()
            if any(kw in error_msgs for kw in (
                "temporarily locked", "suspended", "access control",
                "account is locked",
            )):
                logger.warning("[GraphQL] Account restricted — rotating to next cookie")
                from feedgrab.fetchers.twitter_cookies import mark_cookie_rate_limited
                mark_cookie_rate_limited()
                return None

        return data

    except requests.Timeout:
        logger.error(f"GraphQL request timed out: {operation_name}")
        return None
    except requests.RequestException as e:
        logger.error(f"GraphQL request failed: {e}")
        return None
    except json.JSONDecodeError:
        logger.error("GraphQL response is not valid JSON")
        return None


def _get_query_id(operation_name: str) -> str:
    """Get queryId for an operation, with caching and dynamic resolution."""
    global _query_cache, _cache_timestamp

    now = time.time()
    if _query_cache and (now - _cache_timestamp) < CACHE_TTL:
        if operation_name in _query_cache:
            return _query_cache[operation_name]

    resolved = resolve_query_ids()
    _query_cache = resolved
    _cache_timestamp = now

    fallbacks = _fallback_query_ids()
    return resolved.get(operation_name, fallbacks.get(operation_name, ""))


def _fallback_query_ids() -> Dict[str, str]:
    """Hardcoded fallback queryIds from baoyu constants.ts."""
    return {
        "TweetDetail": FALLBACK_TWEET_DETAIL_QUERY_ID,
        "TweetResultByRestId": FALLBACK_TWEET_RESULT_QUERY_ID,
        "ArticleEntityResultByRestId": FALLBACK_ARTICLE_QUERY_ID,
        "Bookmarks": FALLBACK_BOOKMARKS_QUERY_ID,
        "BookmarkFoldersSlice": FALLBACK_BOOKMARK_FOLDERS_QUERY_ID,
        "BookmarkFolderTimeline": FALLBACK_BOOKMARK_FOLDER_TIMELINE_QUERY_ID,
        "UserByScreenName": FALLBACK_USER_BY_SCREEN_NAME_QUERY_ID,
        "UserTweets": FALLBACK_USER_TWEETS_QUERY_ID,
        "SearchTimeline": FALLBACK_SEARCH_TIMELINE_QUERY_ID,
        "ListByRestId": FALLBACK_LIST_BY_REST_ID_QUERY_ID,
        "ListLatestTweetsTimeline": FALLBACK_LIST_LATEST_TWEETS_QUERY_ID,
        # v0.22.0: twe-borrowed
        "Followers": FALLBACK_FOLLOWERS_QUERY_ID,
        "Following": FALLBACK_FOLLOWING_QUERY_ID,
        "BlueVerifiedFollowers": FALLBACK_BLUE_VERIFIED_FOLLOWERS_QUERY_ID,
        "Likes": FALLBACK_LIKES_QUERY_ID,
        "UserTweetsAndReplies": FALLBACK_USER_TWEETS_AND_REPLIES_QUERY_ID,
        "ListMembers": FALLBACK_LIST_MEMBERS_QUERY_ID,
        "ListSubscribers": FALLBACK_LIST_SUBSCRIBERS_QUERY_ID,
        # v0.23.0
        "ModeratedTimeline": FALLBACK_MODERATED_TIMELINE_QUERY_ID,
        "Retweeters": FALLBACK_RETWEETERS_QUERY_ID,
        "Favoriters": FALLBACK_FAVORITERS_QUERY_ID,
    }


def _fetch_home_html(user_agent: str) -> str:
    """Fetch and cache x.com homepage HTML (matches baoyu http.ts caching)."""
    global _cached_home_html

    if _cached_home_html:
        return _cached_home_html

    try:
        resp = http_client.get(
            "https://x.com",
            headers={"user-agent": user_agent},
            timeout=15,
        )
        http_client.raise_for_status(resp)
        _cached_home_html = resp.text
        return _cached_home_html
    except Exception as e:
        logger.warning(f"Failed to fetch x.com homepage: {e}")
        return ""


def _fetch_and_extract_query_id(
    chunk_url: str, operation_name: str, user_agent: str
) -> Optional[str]:
    """Download a JS bundle chunk and extract queryId for an operation."""
    try:
        resp = http_client.get(
            chunk_url,
            headers={"user-agent": user_agent},
            timeout=15,
        )
        http_client.raise_for_status(resp)
        return _extract_query_id(resp.text, operation_name)
    except Exception as e:
        logger.debug(f"Failed to fetch/parse {chunk_url}: {e}")
        return None


def _extract_query_id(js_content: str, operation_name: str) -> Optional[str]:
    """
    Extract queryId for a given operationName from JS bundle content.

    Tries multiple regex patterns since X's bundle format can vary.
    """
    patterns = [
        rf'queryId:"([^"]+)",operationName:"{operation_name}"',
        rf'operationName:"{operation_name}"[^}}]*?queryId:"([^"]+)"',
        rf'\{{[^}}]*queryId:"([^"]+)"[^}}]*operationName:"{operation_name}"[^}}]*\}}',
    ]

    for pattern in patterns:
        match = re.search(pattern, js_content)
        if match:
            return match.group(1)

    return None


def _apply_richtext_tags(text: str, note_tweet: dict) -> str:
    """Apply richtext_tags (Bold/Italic) from note_tweet to produce Markdown formatting.

    Tags are index-based on the original text. We process from end to start
    to avoid index shifting when inserting markers.
    """
    tags = note_tweet.get("richtext", {}).get("richtext_tags", [])
    if not tags or not text:
        return text

    # Sort by from_index descending so insertions don't shift earlier indices
    sorted_tags = sorted(tags, key=lambda t: t.get("from_index", 0), reverse=True)
    chars = list(text)

    for tag in sorted_tags:
        fr = tag.get("from_index", 0)
        to = tag.get("to_index", 0)
        types = tag.get("richtext_types", [])
        if fr >= to or fr >= len(chars):
            continue
        to = min(to, len(chars))
        if "Bold" in types and "Italic" in types:
            chars.insert(to, "***")
            chars.insert(fr, "***")
        elif "Bold" in types:
            chars.insert(to, "**")
            chars.insert(fr, "**")
        elif "Italic" in types:
            chars.insert(to, "*")
            chars.insert(fr, "*")

    return "".join(chars)


def _parse_source_app(source_html: str) -> str:
    """Extract app name from tweet source HTML tag.

    Input: '<a href="https://mobile.twitter.com" rel="nofollow">Twitter Web App</a>'
    Output: 'Twitter Web App'
    """
    if not source_html:
        return ""
    m = re.search(r">([^<]+)<", source_html)
    return m.group(1).strip() if m else ""


def _apply_article_inline(text: str, entity_ranges: list, entity_map: dict, block: dict) -> str:
    """Apply LINK entities and inline styles (Bold/Italic) to block text.

    Process from end to start to preserve character offsets.
    Links first, then styles — so styles can wrap around link syntax.
    """
    # Collect all entities that carry a URL (LINK, MENTION, etc.)
    link_ops = []
    for er in entity_ranges:
        ent_key = str(er.get("key", ""))
        ent = entity_map.get(ent_key, {})
        ent_data = ent.get("data", {})
        # Extract URL from any entity type — url / href / value
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

    # Apply inline styles (Bold/Italic) from end to start
    styles = block.get("inlineStyleRanges", [])
    if styles:
        # After link insertion, original offsets are invalid for styles.
        # Re-map style offsets by tracking how link insertions shifted positions.
        # Build shift map from original link_ops (sorted ascending by offset).
        shifts = []  # (original_offset, chars_added)
        for offset, length, url in sorted(link_ops, key=lambda x: x[0]):
            # [anchor](url) adds len("[") + len("](") + len(url) + len(")") = 4 + len(url)
            added = 4 + len(url)
            shifts.append((offset, added))

        def adjusted_offset(orig_off):
            """Shift an original offset to account for link insertions before it."""
            adj = orig_off
            for lk_off, added in shifts:
                if lk_off < orig_off:
                    adj += added
                elif lk_off == orig_off:
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


def _render_article_body(article: dict) -> str:
    """Render Twitter Article content_state (Draft.js format) to Markdown.

    The content_state contains:
    - blocks: list of {text, type, entityRanges, inlineStyleRanges, depth}
    - entityMap: list of {key, value: {type, data}} entries

    Block types: unstyled, header-two, header-three, ordered-list-item,
    unordered-list-item, blockquote, atomic, code-block.

    Entity types: MEDIA (images), MARKDOWN (code blocks), TWEMOJI (emoji SVG).
    """
    cs = article.get("content_state")
    if not cs:
        return ""
    blocks = cs.get("blocks", [])
    if not blocks:
        return ""

    # Build entityMap lookup: key (str) → {type, data}
    raw_em = cs.get("entityMap", {})
    if isinstance(raw_em, list):
        entity_map = {str(item["key"]): item["value"] for item in raw_em if "key" in item}
    elif isinstance(raw_em, dict):
        entity_map = raw_em
    else:
        entity_map = {}

    # Build mediaId → URL lookup from media_entities
    media_url_map = {}
    for me in article.get("media_entities", []):
        mi = me.get("media_info") or {}
        media_id = str(me.get("media_key", ""))
        url = mi.get("original_img_url", "")
        if url:
            media_url_map[media_id] = url
        # Also index by numeric media_id
        mid = str(mi.get("__rest_id", ""))
        if mid and url:
            media_url_map[mid] = url

    parts = []
    list_counter = 0  # for ordered lists

    for block in blocks:
        btype = block.get("type", "unstyled")
        text = block.get("text", "")
        entity_ranges = block.get("entityRanges", [])

        # atomic blocks: content comes from entityMap
        if btype == "atomic":
            for er in entity_ranges:
                ent_key = str(er.get("key", ""))
                ent = entity_map.get(ent_key, {})
                ent_type = ent.get("type", "")
                ent_data = ent.get("data", {})

                if ent_type == "MEDIA":
                    # Resolve image URL from media_entities
                    for mi in ent_data.get("mediaItems", []):
                        mid = str(mi.get("mediaId", ""))
                        img_url = media_url_map.get(mid, "")
                        if not img_url:
                            # Try matching by suffix in media_url_map keys
                            for mk, mv in media_url_map.items():
                                if mid in mk or mk in mid:
                                    img_url = mv
                                    break
                        if img_url:
                            parts.append(f"\n![image]({img_url})\n")
                elif ent_type == "MARKDOWN":
                    md = ent_data.get("markdown", "")
                    if md:
                        parts.append(f"\n{md}\n")
                # TWEMOJI: skip (emoji SVGs are oversized in Obsidian)
            continue

        # Empty block → blank line
        if not text.strip():
            parts.append("")
            list_counter = 0
            continue

        # Apply inline LINK entities and styles (from end to avoid offset shift)
        text = _apply_article_inline(text, entity_ranges, entity_map, block)

        # Format text based on block type
        if btype == "header-one":
            parts.append(f"# {text}")
            list_counter = 0
        elif btype == "header-two":
            parts.append(f"## {text}")
            list_counter = 0
        elif btype == "header-three":
            parts.append(f"### {text}")
            list_counter = 0
        elif btype == "ordered-list-item":
            list_counter += 1
            parts.append(f"{list_counter}. {text}")
        elif btype == "unordered-list-item":
            parts.append(f"- {text}")
            list_counter = 0
        elif btype == "blockquote":
            for line in text.split("\n"):
                parts.append(f"> {line}")
            list_counter = 0
        elif btype == "code-block":
            parts.append(f"````\n{text}\n````")
            list_counter = 0
        else:
            # unstyled → regular paragraph
            parts.append(text)
            list_counter = 0

    return "\n\n".join(parts).strip()


def _extract_article_ref(result: dict) -> Optional[Dict[str, Any]]:
    """
    Extract article entity reference from a tweet result.

    Matches baoyu tweet-article.ts resolveArticleEntityFromTweet() — checks
    multiple paths where articles can be embedded.
    """
    # Check various paths for embedded article
    for path in [
        lambda r: r.get("article", {}).get("article_results", {}).get("result"),
        lambda r: r.get("article", {}).get("result"),
        lambda r: r.get("legacy", {}).get("article", {}).get("article_results", {}).get("result"),
        lambda r: r.get("legacy", {}).get("article", {}).get("result"),
        lambda r: r.get("article_results", {}).get("result"),
    ]:
        try:
            article = path(result)
            if article and article.get("rest_id"):
                # Extract cover image from cover_media
                cover_media = article.get("cover_media") or {}
                media_info = cover_media.get("media_info") or {}
                cover_image = (
                    media_info.get("original_img_url")
                    or (media_info.get("preview_image") or {}).get("original_img_url")
                    or ""
                )
                # Render article body from content_state (Draft.js format)
                body = _render_article_body(article)
                return {
                    "id": article.get("rest_id", ""),
                    "title": article.get("title", ""),
                    "cover_image": cover_image,
                    "body": body,
                    "has_content": bool(body or article.get("preview_text")),
                }
        except (TypeError, AttributeError):
            continue

    return None


_last_request_time: float = 0


def _rate_limit_wait():
    """
    Enforce minimum delay between GraphQL requests.

    Safety measure added in feedgrab — the original baoyu has no rate limiting,
    which risks account throttling with aggressive thread pagination.
    """
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    delay = DEFAULT_REQUEST_DELAY
    if elapsed < delay:
        wait = delay - elapsed
        logger.debug(f"Rate limiting: waiting {wait:.1f}s")
        time.sleep(wait)
    _last_request_time = time.time()
