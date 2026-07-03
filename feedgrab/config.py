# -*- coding: utf-8 -*-
"""
Centralized configuration — paths, feature flags, defaults.

All cookie/session paths and feature toggles should be read from here,
not hardcoded in individual fetcher files.
"""

import os
import platform
import re
from pathlib import Path

from loguru import logger


# ---------------------------------------------------------------------------
# User-Agent — single source of truth
# ---------------------------------------------------------------------------

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/132.0.0.0 Safari/537.36"
)


def get_user_agent() -> str:
    """Return the User-Agent string for all browser/HTTP interactions.

    Priority:
      1. BROWSER_USER_AGENT env var (user-configured or auto-detected)
      2. browserforge generated UA (if installed)
      3. DEFAULT_USER_AGENT fallback
    """
    env_ua = os.getenv("BROWSER_USER_AGENT", "").strip()
    if env_ua:
        return env_ua
    if _stealth_headers is None:
        _init_stealth_headers()
    return _stealth_headers.get("User-Agent", DEFAULT_USER_AGENT)


# ---------------------------------------------------------------------------
# Stealth headers — browserforge-powered consistent fingerprint
# ---------------------------------------------------------------------------

_stealth_headers = None  # session-level cache


def _detect_browser_os() -> str:
    """Map platform.system() to browserforge OS name."""
    return {"windows": "windows", "linux": "linux", "darwin": "macos"}.get(
        platform.system().lower(), "windows"
    )


def _init_stealth_headers():
    """Initialize consistent browser headers (called once, cached).

    Uses browserforge to generate a complete header set where UA, Sec-Ch-Ua,
    Accept, Accept-Language etc. are internally consistent.
    Falls back to basic headers if browserforge is not installed.
    """
    global _stealth_headers

    env_ua = os.getenv("BROWSER_USER_AGENT", "").strip()

    try:
        from browserforge.headers import Browser, HeaderGenerator

        # Match Chrome version from user's real UA if set
        chrome_ver = 0
        if env_ua:
            m = re.search(r"Chrome/(\d+)", env_ua)
            chrome_ver = int(m.group(1)) if m else 0

        if chrome_ver > 0:
            browsers = [Browser(name="chrome", min_version=chrome_ver, max_version=chrome_ver)]
        else:
            # Pin to DEFAULT_USER_AGENT version for consistent "Google Chrome" brand
            m = re.search(r"Chrome/(\d+)", DEFAULT_USER_AGENT)
            fallback_ver = int(m.group(1)) if m else 132
            browsers = [Browser(name="chrome", min_version=fallback_ver, max_version=fallback_ver)]

        gen = HeaderGenerator(
            browser=browsers, os=_detect_browser_os(), device="desktop"
        )
        _stealth_headers = dict(gen.generate())

        # Override with user's real UA if set (keeps Sec-Ch-Ua from browserforge)
        if env_ua:
            _stealth_headers["User-Agent"] = env_ua

        # Let HTTP libraries handle encoding negotiation
        _stealth_headers.pop("Accept-Encoding", None)

        # Chinese locale for feedgrab's primary targets
        _stealth_headers["Accept-Language"] = "zh-CN,zh;q=0.9,en;q=0.8"

    except ImportError:
        logger.warning(
            "[stealth] browserforge 未安装，浏览器指纹一致性降级。"
            "建议安装以提升反检测能力：\n"
            '  pip install "feedgrab[stealth]"   # 含 patchright + browserforge\n'
            "  或单独安装：pip install browserforge"
        )
        ua = env_ua or DEFAULT_USER_AGENT
        _stealth_headers = {
            "User-Agent": ua,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    except Exception as e:
        logger.warning(f"[stealth] browserforge 初始化失败: {e}，使用基础 header")
        ua = env_ua or DEFAULT_USER_AGENT
        _stealth_headers = {
            "User-Agent": ua,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }


def get_stealth_headers(**overrides) -> dict:
    """Return a complete set of consistent browser headers.

    Uses browserforge (if installed) to generate headers where UA, Sec-Ch-Ua,
    Accept, etc. are all internally consistent.  Falls back to basic headers.
    Kwargs override specific headers (e.g. Accept="text/markdown" for Jina).
    Cached per session — all requests share the same fingerprint.
    """
    if _stealth_headers is None:
        _init_stealth_headers()
    h = dict(_stealth_headers)
    h.update(overrides)
    return h


def get_data_dir() -> Path:
    """Return the feedgrab data/session directory (project-local by default).

    Reads FEEDGRAB_DATA_DIR from env; defaults to ``sessions``.
    Cookies and Playwright sessions are stored together in one flat directory.
    Relative paths are resolved against the current working directory.
    """
    raw = os.getenv("FEEDGRAB_DATA_DIR", "sessions")
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def get_cookie_dir() -> Path:
    """Return the cookie storage directory (same as session dir)."""
    return get_data_dir()


def get_session_dir() -> Path:
    """Return the Playwright session storage directory (same as cookie dir)."""
    return get_data_dir()


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

def x_fetch_author_replies() -> bool:
    """Whether to collect the tweet author's own replies."""
    return os.getenv("X_FETCH_AUTHOR_REPLIES", "false").lower() in ("true", "1", "yes")


def x_fetch_all_comments() -> bool:
    """Whether to collect all comments under the main tweet."""
    return os.getenv("X_FETCH_ALL_COMMENTS", "false").lower() in ("true", "1", "yes")


def x_max_comments() -> int:
    """Maximum number of comments to collect (default 50)."""
    try:
        return int(os.getenv("X_MAX_COMMENTS", "50"))
    except ValueError:
        return 50


def x_fetch_moderated_replies() -> bool:
    """v0.23.0: whether to also pull author-hidden replies via ModeratedTimeline.

    Note: API typically only returns data for the tweet author themselves.
    For everyone else, the timeline is empty (graceful no-op).
    """
    return os.getenv("X_FETCH_MODERATED_REPLIES", "false").lower() in ("true", "1", "yes")


def x_moderated_replies_max_pages() -> int:
    """Maximum pagination requests for ModeratedTimeline (default 3)."""
    try:
        return int(os.getenv("X_MODERATED_REPLIES_MAX_PAGES", "3"))
    except ValueError:
        return 3


# ---------------------------------------------------------------------------
# Bookmarks batch fetch
# ---------------------------------------------------------------------------

def x_bookmarks_enabled() -> bool:
    """Whether bookmark batch fetching is enabled."""
    return os.getenv("X_BOOKMARKS_ENABLED", "false").lower() in ("true", "1", "yes")


def x_bookmark_max_pages() -> int:
    """Maximum bookmark pagination pages (default 50, ~1000 tweets)."""
    try:
        return int(os.getenv("X_BOOKMARK_MAX_PAGES", "50"))
    except ValueError:
        return 50


def x_bookmark_delay() -> float:
    """Delay in seconds between processing each bookmark tweet (default 2.0)."""
    try:
        return float(os.getenv("X_BOOKMARK_DELAY", "2.0"))
    except ValueError:
        return 2.0


# ---------------------------------------------------------------------------
# User timeline batch fetch
# ---------------------------------------------------------------------------

def x_user_tweets_enabled() -> bool:
    """Whether user timeline batch fetching is enabled."""
    return os.getenv("X_USER_TWEETS_ENABLED", "false").lower() in ("true", "1", "yes")


def x_user_tweet_max_pages() -> int:
    """Maximum user timeline pagination pages (default 200, ~4000 tweets)."""
    try:
        return int(os.getenv("X_USER_TWEET_MAX_PAGES", "200"))
    except ValueError:
        return 200


def x_user_tweet_delay() -> float:
    """Delay in seconds between processing each user tweet (default 2.0)."""
    try:
        return float(os.getenv("X_USER_TWEET_DELAY", "2.0"))
    except ValueError:
        return 2.0


def x_user_tweets_since() -> str:
    """Date filter for user tweets (e.g. '2025-10-01'). Empty = fetch all."""
    return os.getenv("X_USER_TWEETS_SINCE", "").strip()


# ---------------------------------------------------------------------------
# List tweets batch fetch
# ---------------------------------------------------------------------------

def x_list_tweets_enabled() -> bool:
    """Whether list tweets batch fetching is enabled."""
    return os.getenv("X_LIST_TWEETS_ENABLED", "false").lower() in ("true", "1", "yes")


def x_list_tweet_max_pages() -> int:
    """Maximum list timeline pagination pages (default 50)."""
    try:
        return int(os.getenv("X_LIST_TWEET_MAX_PAGES", "50"))
    except ValueError:
        return 50


def x_list_tweet_delay() -> float:
    """Delay in seconds between processing each list tweet (default 2.0)."""
    try:
        return float(os.getenv("X_LIST_TWEET_DELAY", "2.0"))
    except ValueError:
        return 2.0


def x_list_tweets_days() -> int:
    """Number of days to fetch from list (default: 1 = last 24h)."""
    try:
        return int(os.getenv("X_LIST_TWEETS_DAYS", "1"))
    except ValueError:
        return 1


def x_list_tweets_summary() -> bool:
    """Whether to generate a summary table (MD + CSV) after list tweets batch fetch."""
    return os.getenv("X_LIST_TWEETS_SUMMARY", "false").lower() in ("true", "1", "yes")


def x_search_supplementary_enabled() -> bool:
    """Whether to use Search API to supplement UserTweets for older tweets.

    When enabled (default), after UserTweets finishes, if X_USER_TWEETS_SINCE
    is set and UserTweets didn't reach that far back, Search API will
    automatically fill the gap via monthly date chunking.
    """
    return os.getenv("X_SEARCH_SUPPLEMENTARY", "true").lower() in ("true", "1", "yes")


def x_search_max_pages_per_chunk() -> int:
    """Maximum pages per monthly search chunk (default 50)."""
    try:
        return int(os.getenv("X_SEARCH_MAX_PAGES_PER_CHUNK", "50"))
    except ValueError:
        return 50


# ---------------------------------------------------------------------------
# v0.22.0: User list batch fetch (Followers / Following / List members ...)
# ---------------------------------------------------------------------------

def x_user_list_enabled() -> bool:
    """Whether Twitter user-list batch fetching is enabled.

    Covers followers/following/blue_verified_followers/list_members/list_subscribers.
    """
    return os.getenv("X_USER_LIST_ENABLED", "true").lower() in ("true", "1", "yes")


def x_user_list_max_pages() -> int:
    """Maximum user-list pagination pages (default 20, ~400 users)."""
    try:
        return int(os.getenv("X_USER_LIST_MAX_PAGES", "20"))
    except ValueError:
        return 20


def x_user_list_delay() -> float:
    """Delay in seconds between user-list pagination pages (default 2.0)."""
    try:
        return float(os.getenv("X_USER_LIST_DELAY", "2.0"))
    except ValueError:
        return 2.0


def x_user_list_per_page() -> int:
    """Per-page count for user-list timelines (default 20)."""
    try:
        return int(os.getenv("X_USER_LIST_PER_PAGE", "20"))
    except ValueError:
        return 20


# ---------------------------------------------------------------------------
# v0.22.0: User likes batch fetch (Likes)
# ---------------------------------------------------------------------------

def x_user_likes_enabled() -> bool:
    """Whether Twitter user-likes batch fetching is enabled."""
    return os.getenv("X_USER_LIKES_ENABLED", "true").lower() in ("true", "1", "yes")


def x_user_likes_max_pages() -> int:
    """Maximum pages for user-likes fetcher (default 50)."""
    try:
        return int(os.getenv("X_USER_LIKES_MAX_PAGES", "50"))
    except ValueError:
        return 50


# ---------------------------------------------------------------------------
# v0.22.0: User replies batch fetch (UserTweetsAndReplies)
# ---------------------------------------------------------------------------

def x_user_replies_enabled() -> bool:
    """Whether Twitter user-replies batch fetching is enabled."""
    return os.getenv("X_USER_REPLIES_ENABLED", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# v0.23.0: Tweet-level user lists (Retweeters / Favoriters)
# ---------------------------------------------------------------------------

def x_tweet_user_list_enabled() -> bool:
    """Whether tweet-level user-list batch fetching is enabled."""
    return os.getenv("X_TWEET_USER_LIST_ENABLED", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Twitter/X keyword search (x-so)
# ---------------------------------------------------------------------------

def x_search_enabled() -> bool:
    """Whether Twitter keyword search is enabled."""
    return os.getenv("X_SEARCH_ENABLED", "true").lower() in ("true", "1", "yes")


def x_search_lang() -> str:
    """Default language filter for keyword search (e.g. 'zh', 'en'). Empty = no filter."""
    return os.getenv("X_SEARCH_LANG", "zh").strip()


def x_search_days() -> int:
    """Default time range in days for keyword search (default 1)."""
    try:
        return int(os.getenv("X_SEARCH_DAYS", "1"))
    except ValueError:
        return 1


def x_search_min_faves() -> int:
    """Default minimum likes filter (default 0 = no filter)."""
    try:
        return int(os.getenv("X_SEARCH_MIN_FAVES", "0"))
    except ValueError:
        return 0


def x_search_min_retweets() -> int:
    """Default minimum retweets filter (default 0 = no filter)."""
    try:
        return int(os.getenv("X_SEARCH_MIN_RETWEETS", "0"))
    except ValueError:
        return 0


def x_search_sort() -> str:
    """Default search sort mode: 'live', 'top', or 'all'. Default: live."""
    val = os.getenv("X_SEARCH_SORT", "live").strip().lower()
    return val if val in ("live", "top", "all") else "live"


def x_search_exclude_retweets() -> bool:
    """Whether to exclude retweets from search results. Default: true."""
    return os.getenv("X_SEARCH_EXCLUDE_RETWEETS", "true").lower() in ("true", "1", "yes")


def x_search_delay() -> float:
    """Scroll delay in seconds between search result pages (default 2.0)."""
    try:
        return float(os.getenv("X_SEARCH_DELAY", "2.0"))
    except ValueError:
        return 2.0


def x_search_max_results() -> int:
    """Maximum tweets to collect per search (default 100)."""
    try:
        return int(os.getenv("X_SEARCH_MAX_RESULTS", "100"))
    except ValueError:
        return 100


def x_search_save_tweets() -> bool:
    """Whether to save individual tweet .md files in addition to summary table."""
    return os.getenv("X_SEARCH_SAVE_TWEETS", "false").lower() in ("true", "1", "yes")


def x_search_merge_keywords() -> bool:
    """Whether to merge multi-keyword search results into one summary table (default false)."""
    return os.getenv("X_SEARCH_MERGE_KEYWORDS", "false").lower() in ("true", "1", "yes")


def x_search_browser_fallback() -> bool:
    """Whether to fall back to browser search when GraphQL fails (default true)."""
    return os.getenv("X_SEARCH_BROWSER_FALLBACK", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# TwitterAPI.io paid API (supplementary / standalone)
# ---------------------------------------------------------------------------

def twitterapi_io_key() -> str:
    """TwitterAPI.io API Key. Empty = not configured.

    When configured, used as supplementary for UserTweets (replacing browser search).
    Get your key at https://twitterapi.io
    """
    return os.getenv("TWITTERAPI_IO_KEY", "").strip()


def x_api_provider() -> str:
    """API provider for user tweet batch fetch.

    'graphql' (default) — free GraphQL + optional API supplementary
    'api' — full TwitterAPI.io paid API path (no cookie needed, server-friendly)
    """
    val = os.getenv("X_API_PROVIDER", "graphql").strip().lower()
    if val not in ("graphql", "api"):
        return "graphql"
    return val


def x_api_save_directly() -> bool:
    """Whether to save API data directly without GraphQL secondary fetch.

    false (default) — use tweet_id to call GraphQL for full data (images/videos/thread)
    true — directly convert API data and save (faster, but no media)
    """
    return os.getenv("X_API_SAVE_DIRECTLY", "false").lower() in ("true", "1", "yes")


def x_api_min_likes() -> int:
    """Minimum likes filter for API fetch (OR logic). 0 = no filter."""
    try:
        val = os.getenv("X_API_MIN_LIKES", "").strip()
        return int(val) if val else 0
    except ValueError:
        return 0


def x_api_min_retweets() -> int:
    """Minimum retweets filter for API fetch (OR logic). 0 = no filter."""
    try:
        val = os.getenv("X_API_MIN_RETWEETS", "").strip()
        return int(val) if val else 0
    except ValueError:
        return 0


def x_api_min_views() -> int:
    """Minimum views filter for API fetch (OR logic). 0 = no filter."""
    try:
        val = os.getenv("X_API_MIN_VIEWS", "").strip()
        return int(val) if val else 0
    except ValueError:
        return 0


def force_refetch() -> bool:
    """Skip dedup check and re-fetch/overwrite existing files.

    Set FORCE_REFETCH=true to re-fetch all items even if already saved.
    Useful after code fixes or to update metadata (likes/views).
    """
    return os.getenv("FORCE_REFETCH", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# XHS API mode (needs: pip install xhshow)
# ---------------------------------------------------------------------------

def xhs_api_enabled() -> bool:
    """Whether to prefer XHS API mode (faster, no browser needed).

    When true: API → Jina → Playwright fallback chain.
    When false: skip API, use Jina → Playwright directly.
    Requires xhshow library + valid session cookies.
    """
    return os.getenv("XHS_API_ENABLED", "true").lower() in ("true", "1", "yes")


def xhs_pinia_enabled() -> bool:
    """Use Pinia Store injection as fallback when xhshow API fails.

    When true: xhshow fails → try Pinia Store in browser → Jina → Playwright.
    Default true — transparent fallback, no impact on normal xhshow path.
    """
    return os.getenv("XHS_PINIA_ENABLED", "true").lower() in ("true", "1", "yes")


def xhs_api_delay() -> float:
    """API request interval in seconds (default 1.0, with Gaussian jitter)."""
    try:
        return float(os.getenv("XHS_API_DELAY", "1.0"))
    except ValueError:
        return 1.0


def xhs_fetch_comments() -> bool:
    """Whether to fetch full comment text when fetching a single note."""
    return os.getenv("XHS_FETCH_COMMENTS", "false").lower() in ("true", "1", "yes")


def xhs_max_comments() -> int:
    """Maximum comment pages to fetch (default 5, ~20 comments per page)."""
    try:
        return int(os.getenv("XHS_MAX_COMMENTS", "5"))
    except ValueError:
        return 5


def xhs_search_sort() -> str:
    """Default search sort: general / popular / latest."""
    val = os.getenv("XHS_SEARCH_SORT", "general").strip().lower()
    return val if val in ("general", "popular", "latest") else "general"


def xhs_search_note_type() -> str:
    """Default search note type: all / video / image."""
    val = os.getenv("XHS_SEARCH_NOTE_TYPE", "all").strip().lower()
    return val if val in ("all", "video", "image") else "all"


def xhs_search_max_pages() -> int:
    """Maximum search pagination pages (default 10, 20 per page = 200 max)."""
    try:
        return int(os.getenv("XHS_SEARCH_MAX_PAGES", "10"))
    except ValueError:
        return 10


def xhs_search_save_notes() -> bool:
    """Whether to save individual note .md files during xhs-so search (default false)."""
    return os.getenv("XHS_SEARCH_SAVE_NOTES", "false").strip().lower() in ("true", "1", "yes")


def xhs_search_merge_keywords() -> bool:
    """Whether to merge multi-keyword search results into one summary table (default false)."""
    return os.getenv("XHS_SEARCH_MERGE_KEYWORDS", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# XHS user notes batch fetch
# ---------------------------------------------------------------------------

def xhs_user_notes_enabled() -> bool:
    """Whether XHS user notes batch fetching is enabled."""
    return os.getenv("XHS_USER_NOTES_ENABLED", "false").lower() in ("true", "1", "yes")


def xhs_user_note_max_scrolls() -> int:
    """Maximum scroll iterations on XHS profile page (default 50)."""
    try:
        return int(os.getenv("XHS_USER_NOTE_MAX_SCROLLS", "50"))
    except ValueError:
        return 50


def xhs_user_note_delay() -> float:
    """Delay in seconds between processing each XHS note (default 3.0)."""
    try:
        return float(os.getenv("XHS_USER_NOTE_DELAY", "3.0"))
    except ValueError:
        return 3.0


def xhs_user_notes_since() -> str:
    """Date filter for XHS user notes (e.g. '2025-10-01'). Empty = fetch all."""
    return os.getenv("XHS_USER_NOTES_SINCE", "").strip()


# ---------------------------------------------------------------------------
# XHS search notes batch fetch
# ---------------------------------------------------------------------------

def xhs_search_enabled() -> bool:
    """Whether XHS search notes batch fetching is enabled."""
    return os.getenv("XHS_SEARCH_ENABLED", "false").lower() in ("true", "1", "yes")


def xhs_search_max_scrolls() -> int:
    """Maximum scroll iterations on XHS search page (default 30)."""
    try:
        return int(os.getenv("XHS_SEARCH_MAX_SCROLLS", "30"))
    except ValueError:
        return 30


def xhs_search_delay() -> float:
    """Delay in seconds between processing each XHS search note (default 3.0)."""
    try:
        return float(os.getenv("XHS_SEARCH_DELAY", "3.0"))
    except ValueError:
        return 3.0


# ---------------------------------------------------------------------------
# WeChat Sogou search (mpweixin-so)
# ---------------------------------------------------------------------------

def mpweixin_sogou_enabled() -> bool:
    """Whether Sogou WeChat article search is enabled."""
    return os.getenv("MPWEIXIN_SOGOU_ENABLED", "false").lower() in ("true", "1", "yes")


def mpweixin_sogou_max_results() -> int:
    """Maximum articles per search (default 10, max ~100, 10 per page)."""
    try:
        val = int(os.getenv("MPWEIXIN_SOGOU_MAX_RESULTS", "10"))
        return min(val, 100)  # Sogou caps at ~10 pages
    except ValueError:
        return 10


def mpweixin_sogou_delay() -> float:
    """Delay in seconds between fetching each article (default 3.0)."""
    try:
        return float(os.getenv("MPWEIXIN_SOGOU_DELAY", "3.0"))
    except ValueError:
        return 3.0


# ---------------------------------------------------------------------------
# WeChat MP account article fetch (mpweixin-id)
# ---------------------------------------------------------------------------

def mpweixin_id_since() -> str:
    """Date filter for MP account articles (e.g. '2025-10-01').

    Only fetch articles published after this date.
    Empty = fetch all articles.
    """
    return os.getenv("MPWEIXIN_ID_SINCE", "").strip()


def mpweixin_id_delay() -> float:
    """Delay in seconds between fetching each article (default 3.0)."""
    try:
        return float(os.getenv("MPWEIXIN_ID_DELAY", "3.0"))
    except ValueError:
        return 3.0


# ---------------------------------------------------------------------------
# WeChat MP album batch fetch (mpweixin-zhuanji)
# ---------------------------------------------------------------------------

def mpweixin_zhuanji_since() -> str:
    """Date filter for album articles (e.g. '2025-10-01'). Empty = all."""
    return os.getenv("MPWEIXIN_ZHUANJI_SINCE", "").strip()


def mpweixin_zhuanji_delay() -> float:
    """Delay in seconds between fetching each album article (default 3.0)."""
    try:
        return float(os.getenv("MPWEIXIN_ZHUANJI_DELAY", "3.0"))
    except ValueError:
        return 3.0


def mpweixin_fetch_comments() -> bool:
    """Whether to fetch article comments via appmsg_comment API (default false)."""
    return os.getenv("MPWEIXIN_FETCH_COMMENTS", "false").lower() in ("true", "1", "yes")


def mpweixin_max_comments() -> int:
    """Maximum number of comments to fetch per article (default 100)."""
    try:
        return int(os.getenv("MPWEIXIN_MAX_COMMENTS", "100"))
    except ValueError:
        return 100


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def parse_twitter_date_local(created_at: str, fmt: str = "%Y-%m-%d") -> str:
    """Parse Twitter created_at to local timezone string.

    Supports both:
    - RFC 2822 from GraphQL: "Thu Oct 28 03:49:11 +0000 2022"
    - ISO 8601 from Syndication API: "2022-10-28T03:49:11.000Z"

    Converts UTC to system local timezone so dates match the Twitter web UI.
    """
    if not created_at:
        return ""
    try:
        # Try ISO 8601 first (Syndication API format)
        # Use regex to avoid matching "T" in weekday names like "Tue", "Thu"
        if re.search(r"\d{4}-\d{2}-\d{2}T", created_at):
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            dt = dt.astimezone()  # UTC → system local timezone
            return dt.strftime(fmt)
        # Fallback to RFC 2822 (GraphQL format)
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(created_at)
        dt = dt.astimezone()  # UTC → system local timezone
        return dt.strftime(fmt)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def github_token() -> str:
    """GitHub personal access token. Without: 60 req/h, with: 5000 req/h."""
    return os.getenv("GITHUB_TOKEN", "").strip()


# ---------------------------------------------------------------------------
# Feishu / Lark
# ---------------------------------------------------------------------------

def feishu_app_id() -> str:
    """Feishu Open Platform App ID for API access."""
    return os.getenv("FEISHU_APP_ID", "").strip()


def feishu_app_secret() -> str:
    """Feishu Open Platform App Secret for API access."""
    return os.getenv("FEISHU_APP_SECRET", "").strip()


def feishu_wiki_batch_enabled() -> bool:
    """Enable wiki batch download via feishu-wiki command. Default false."""
    return os.getenv("FEISHU_WIKI_BATCH_ENABLED", "false").lower() in ("true", "1", "yes")


def feishu_wiki_delay() -> float:
    """Delay between requests in wiki batch mode (seconds). Default 1.0."""
    try:
        return float(os.getenv("FEISHU_WIKI_DELAY", "1.0"))
    except ValueError:
        return 1.0


def feishu_wiki_since() -> str:
    """Only fetch wiki docs modified after this date (YYYY-MM-DD). Empty = all."""
    return os.getenv("FEISHU_WIKI_SINCE", "").strip()


def feishu_download_images() -> bool:
    """Download images locally instead of linking to Feishu CDN. Default false."""
    return os.getenv("FEISHU_DOWNLOAD_IMAGES", "false").lower() in ("true", "1", "yes")


def feishu_page_load_timeout() -> int:
    """Playwright page element wait timeout in ms. Default 5000."""
    try:
        return int(os.getenv("FEISHU_PAGE_LOAD_TIMEOUT", "5000"))
    except ValueError:
        return 5000


def feishu_cdp_enabled() -> bool:
    """Connect to running Chrome via CDP for Feishu docs. Default false.

    Requires Chrome launched with --remote-debugging-port (see CHROME_CDP_PORT).
    """
    return os.getenv("FEISHU_CDP_ENABLED", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# KDocs (WPS 金山文档)
# ---------------------------------------------------------------------------

def kdocs_cdp_enabled() -> bool:
    """Connect to running Chrome via CDP for KDocs. Default true.

    KDocs documents often require login. CDP reuses the running Chrome's
    session cookies automatically, falling back to launch mode if CDP
    is unavailable.
    """
    return os.getenv("KDOCS_CDP_ENABLED", "true").lower() in ("true", "1", "yes")


def kdocs_page_load_timeout() -> int:
    """Playwright page element wait timeout in ms. Default 10000."""
    try:
        return int(os.getenv("KDOCS_PAGE_LOAD_TIMEOUT", "10000"))
    except ValueError:
        return 10000


def kdocs_download_images() -> bool:
    """Download KDocs images locally instead of linking to CDN. Default false."""
    return os.getenv("KDOCS_DOWNLOAD_IMAGES", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# FlowUs (息流 flowus.cn)
# ---------------------------------------------------------------------------

def flowus_cdp_enabled() -> bool:
    """Connect to running Chrome via CDP for FlowUs. Default true.

    Required for private / paid docs needing the next_auth JWT cookie.
    Public share docs work without cookies — CDP is skipped automatically
    when the no-cookie HTTP attempt succeeds.
    """
    return os.getenv("FLOWUS_CDP_ENABLED", "true").lower() in ("true", "1", "yes")


def flowus_page_load_timeout() -> int:
    """Playwright page element wait timeout in ms. Default 10000."""
    try:
        return int(os.getenv("FLOWUS_PAGE_LOAD_TIMEOUT", "10000"))
    except ValueError:
        return 10000


def flowus_download_images() -> bool:
    """Download FlowUs images locally. Default false (links may be time-limited)."""
    return os.getenv("FLOWUS_DOWNLOAD_IMAGES", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Zhihu (知乎)
# ---------------------------------------------------------------------------

def zhihu_cdp_enabled() -> bool:
    """Connect to running Chrome via CDP for Zhihu. Default false."""
    return os.getenv("ZHIHU_CDP_ENABLED", "false").lower() in ("true", "1", "yes")


def zhihu_page_load_timeout() -> int:
    """Playwright page element wait timeout in ms. Default 10000."""
    try:
        return int(os.getenv("ZHIHU_PAGE_LOAD_TIMEOUT", "10000"))
    except ValueError:
        return 10000


def zhihu_download_images() -> bool:
    """Download Zhihu images locally. Default false."""
    return os.getenv("ZHIHU_DOWNLOAD_IMAGES", "false").lower() in ("true", "1", "yes")


def linuxdo_cdp_enabled() -> bool:
    """Connect to running Chrome via CDP for LinuxDo. Default true."""
    return os.getenv("LINUXDO_CDP_ENABLED", "true").lower() in ("true", "1", "yes")


def linuxdo_page_load_timeout() -> int:
    """Playwright page element wait timeout in ms. Default 15000."""
    try:
        return int(os.getenv("LINUXDO_PAGE_LOAD_TIMEOUT", "15000"))
    except ValueError:
        return 15000


def linuxdo_reply_mode() -> str:
    """Reply rendering mode for LinuxDo: author | all | none. Default author."""
    raw = os.getenv("LINUXDO_REPLY_MODE", "author").strip().lower()
    return raw if raw in {"author", "all", "none"} else "author"


def idcflare_cdp_enabled() -> bool:
    """Connect to running Chrome via CDP for IDCFlare. Default true."""
    return os.getenv("IDCFLARE_CDP_ENABLED", "true").lower() in ("true", "1", "yes")


def idcflare_page_load_timeout() -> int:
    """Playwright page element wait timeout in ms. Default 15000."""
    try:
        return int(os.getenv("IDCFLARE_PAGE_LOAD_TIMEOUT", "15000"))
    except ValueError:
        return 15000


def idcflare_reply_mode() -> str:
    """Reply rendering mode for IDCFlare: author | all | none. Default author."""
    raw = os.getenv("IDCFLARE_REPLY_MODE", "author").strip().lower()
    return raw if raw in {"author", "all", "none"} else "author"


# ----- HackerNews -----

def hn_enabled() -> bool:
    """HackerNews fetcher master switch. Default true (no dependencies)."""
    return os.getenv("HN_ENABLED", "true").lower() in ("true", "1", "yes")


def hn_max_comments() -> int:
    """Max top-level comments rendered per HN item. Default 50."""
    try:
        return int(os.getenv("HN_MAX_COMMENTS", "50"))
    except ValueError:
        return 50


def hn_fetch_all_comments() -> bool:
    """Recursively fetch the full comment tree (v0.21+). Default false."""
    return os.getenv("HN_FETCH_ALL_COMMENTS", "false").lower() in ("true", "1", "yes")


def hn_list_limit() -> int:
    """Default item count for hn top/best/... list commands. Default 30."""
    try:
        return int(os.getenv("HN_LIST_LIMIT", "30"))
    except ValueError:
        return 30


# ----- Medium -----

def medium_enabled() -> bool:
    return os.getenv("MEDIUM_ENABLED", "true").lower() in ("true", "1", "yes")


def medium_use_jina() -> bool:
    return os.getenv("MEDIUM_USE_JINA", "true").lower() in ("true", "1", "yes")


def medium_use_browser_fallback() -> bool:
    return os.getenv("MEDIUM_USE_BROWSER_FALLBACK", "true").lower() in ("true", "1", "yes")


def medium_user_limit() -> int:
    try:
        return int(os.getenv("MEDIUM_USER_LIMIT", "20"))
    except ValueError:
        return 20


def medium_user_delay() -> float:
    try:
        return float(os.getenv("MEDIUM_USER_DELAY", "2.0"))
    except ValueError:
        return 2.0


# ----- Reddit -----

def reddit_enabled() -> bool:
    return os.getenv("REDDIT_ENABLED", "true").lower() in ("true", "1", "yes")


def reddit_cdp_enabled() -> bool:
    return os.getenv("REDDIT_CDP_ENABLED", "true").lower() in ("true", "1", "yes")


def reddit_page_load_timeout() -> int:
    try:
        return int(os.getenv("REDDIT_PAGE_LOAD_TIMEOUT", "15000"))
    except ValueError:
        return 15000


def reddit_max_comments() -> int:
    try:
        return int(os.getenv("REDDIT_MAX_COMMENTS", "50"))
    except ValueError:
        return 50


def reddit_fetch_all_comments() -> bool:
    return os.getenv("REDDIT_FETCH_ALL_COMMENTS", "false").lower() in ("true", "1", "yes")


def reddit_reply_mode() -> str:
    value = os.getenv("REDDIT_REPLY_MODE", "top").strip().lower()
    return value if value in ("top", "tree", "all") else "top"


def reddit_retry_attempts() -> int:
    try:
        value = int(os.getenv("REDDIT_RETRY_ATTEMPTS", "3"))
    except ValueError:
        return 3
    return max(1, min(value, 5))


def reddit_max_pages() -> int:
    try:
        value = int(os.getenv("REDDIT_MAX_PAGES", "5"))
    except ValueError:
        return 5
    return max(1, min(value, 20))


def reddit_morechildren_rounds() -> int:
    try:
        value = int(os.getenv("REDDIT_MORECHILDREN_ROUNDS", "2"))
    except ValueError:
        return 2
    return max(0, min(value, 5))


def reddit_morechildren_batch_size() -> int:
    try:
        value = int(os.getenv("REDDIT_MORECHILDREN_BATCH_SIZE", "100"))
    except ValueError:
        return 100
    return max(1, min(value, 100))


def reddit_user_agent() -> str:
    raw = os.getenv("REDDIT_USER_AGENT", "").strip()
    if raw:
        return raw
    return "feedgrab/0.20.0 (+https://github.com/iBigQiang/feedgrab)"


def reddit_sub_limit() -> int:
    try:
        return int(os.getenv("REDDIT_SUB_LIMIT", "25"))
    except ValueError:
        return 25


def reddit_sub_delay() -> float:
    try:
        return float(os.getenv("REDDIT_SUB_DELAY", "2.0"))
    except ValueError:
        return 2.0


def reddit_search_enabled() -> bool:
    return os.getenv("REDDIT_SEARCH_ENABLED", "true").lower() in ("true", "1", "yes")


def reddit_search_sort() -> str:
    value = os.getenv("REDDIT_SEARCH_SORT", "relevance").strip().lower()
    return value if value in ("relevance", "hot", "top", "new", "comments") else "relevance"


def reddit_search_time_range() -> str:
    value = os.getenv("REDDIT_SEARCH_TIME_RANGE", "all").strip().lower()
    return value if value in ("all", "year", "month", "week", "day", "hour") else "all"


def reddit_search_limit() -> int:
    try:
        value = int(os.getenv("REDDIT_SEARCH_LIMIT", "10"))
    except ValueError:
        return 10
    return max(1, min(value, 100))


def reddit_search_save_posts() -> bool:
    return os.getenv("REDDIT_SEARCH_SAVE_POSTS", "false").lower() in ("true", "1", "yes")


def reddit_search_subreddit() -> str:
    return os.getenv("REDDIT_SEARCH_SUBREDDIT", "").strip().strip("/")


# ----- Weibo -----

def weibo_enabled() -> bool:
    return os.getenv("WEIBO_ENABLED", "true").lower() in ("true", "1", "yes")


def weibo_cookie() -> str:
    return os.getenv("WEIBO_COOKIE", "").strip()


def weibo_use_visitor() -> bool:
    return os.getenv("WEIBO_USE_VISITOR", "true").lower() in ("true", "1", "yes")


def weibo_user_limit() -> int:
    try:
        return int(os.getenv("WEIBO_USER_LIMIT", "20"))
    except ValueError:
        return 20


def weibo_user_delay() -> float:
    try:
        return float(os.getenv("WEIBO_USER_DELAY", "2.0"))
    except ValueError:
        return 2.0


def weibo_download_media() -> bool:
    """Download Weibo images/videos to attachments/. Default false.

    Note: weibocdn URLs are signed (Expires/ssig) and typically expire within
    hours. Set WEIBO_DOWNLOAD_MEDIA=true to keep a local copy.
    """
    return os.getenv("WEIBO_DOWNLOAD_MEDIA", "false").lower() in ("true", "1", "yes")


def weibo_fetch_comments() -> bool:
    return os.getenv("WEIBO_FETCH_COMMENTS", "false").lower() in ("true", "1", "yes")


# ----- Douyin -----

def douyin_enabled() -> bool:
    return os.getenv("DOUYIN_ENABLED", "true").lower() in ("true", "1", "yes")


def douyin_cdp_enabled() -> bool:
    return os.getenv("DOUYIN_CDP_ENABLED", "true").lower() in ("true", "1", "yes")


def douyin_page_load_timeout() -> int:
    try:
        return int(os.getenv("DOUYIN_PAGE_LOAD_TIMEOUT", "15000"))
    except ValueError:
        return 15000


def douyin_download_media() -> bool:
    return os.getenv("DOUYIN_DOWNLOAD_MEDIA", "false").lower() in ("true", "1", "yes")


def douyin_fetch_comments() -> bool:
    return os.getenv("DOUYIN_FETCH_COMMENTS", "false").lower() in ("true", "1", "yes")


def zhihu_search_days() -> int:
    """Search within recent N days. Default 30."""
    try:
        return int(os.getenv("ZHIHU_SEARCH_DAYS", "30"))
    except ValueError:
        return 30


def zhihu_search_limit() -> int:
    """Maximum search results. Default 50."""
    try:
        return int(os.getenv("ZHIHU_SEARCH_LIMIT", "50"))
    except ValueError:
        return 50


def zhihu_search_save_answers() -> bool:
    """Save individual answer .md files during search. Default false."""
    return os.getenv("ZHIHU_SEARCH_SAVE_ANSWERS", "false").lower() in ("true", "1", "yes")


def zhihu_search_delay() -> float:
    """Delay between search requests in seconds. Default 2.0."""
    try:
        return float(os.getenv("ZHIHU_SEARCH_DELAY", "2.0"))
    except ValueError:
        return 2.0


# ---------------------------------------------------------------------------
# Youdao Note (有道云笔记)
# ---------------------------------------------------------------------------

def youdao_download_images() -> bool:
    """Download Youdao Note images locally instead of linking to CDN. Default false."""
    return os.getenv("YOUDAO_DOWNLOAD_IMAGES", "false").lower() in ("true", "1", "yes")


# ── Media download ───────────────────────────────────────────

def x_download_media() -> bool:
    """Download Twitter images/videos to local attachments directory. Default false."""
    return os.getenv("X_DOWNLOAD_MEDIA", "false").lower() in ("true", "1", "yes")


def xhs_download_media() -> bool:
    """Download XHS images to local attachments directory. Default false."""
    return os.getenv("XHS_DOWNLOAD_MEDIA", "false").lower() in ("true", "1", "yes")


def mpweixin_download_media() -> bool:
    """Download WeChat article videos/images to local attachments directory. Default false."""
    return os.getenv("MPWEIXIN_DOWNLOAD_MEDIA", "false").lower() in ("true", "1", "yes")


# ── Chrome CDP ───────────────────────────────────────────────

def chrome_cdp_login() -> bool:
    """Extract cookies from a running Chrome via CDP instead of browser login.

    Requires Chrome with Remote Debugging enabled (chrome://inspect/#remote-debugging
    or --remote-debugging-port=9222). Default false.
    """
    return os.getenv("CHROME_CDP_LOGIN", "false").lower() in ("true", "1", "yes")


def chrome_cdp_port() -> int:
    """CDP debugging port. Default 9222."""
    try:
        return int(os.getenv("CHROME_CDP_PORT", "9222"))
    except ValueError:
        return 9222


# ---------------------------------------------------------------------------
# YouTube Whisper (Groq)
# ---------------------------------------------------------------------------

def groq_whisper_model() -> str:
    """Groq Whisper model. Default whisper-large-v3. Alt: whisper-large-v3-turbo (faster, cheaper)."""
    return os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3").strip()


def youtube_whisper_lang() -> str:
    """Language hint for Whisper transcription. Default zh (Chinese)."""
    return os.getenv("YOUTUBE_WHISPER_LANG", "zh").strip()


# ---------------------------------------------------------------------------
# Paywall bypass (generic news sites)
# ---------------------------------------------------------------------------

def paywall_enabled() -> bool:
    """Master switch for paywall bypass pre-Jina fallback. Default true."""
    return os.getenv("PAYWALL_ENABLED", "true").lower() in ("true", "1", "yes")


def paywall_timeout() -> int:
    """HTTP timeout per paywall tier request, in seconds. Default 15."""
    try:
        return int(os.getenv("PAYWALL_TIMEOUT", "15"))
    except ValueError:
        return 15


def paywall_use_amp() -> bool:
    """Try AMP page variants during bypass. Default true."""
    return os.getenv("PAYWALL_USE_AMP", "true").lower() in ("true", "1", "yes")


def paywall_use_archive() -> bool:
    """Try archive.today snapshot during bypass. Default true."""
    return os.getenv("PAYWALL_USE_ARCHIVE", "true").lower() in ("true", "1", "yes")


def paywall_use_google_cache() -> bool:
    """Try Google web cache during bypass. Default true."""
    return os.getenv("PAYWALL_USE_GOOGLE_CACHE", "true").lower() in ("true", "1", "yes")


def paywall_domains_extra() -> str:
    """User-supplied extra paywall domains, pipe-separated (e.g. a.com|b.com).

    Appended to the hardcoded PAYWALL_DOMAINS list. Default empty.
    """
    return os.getenv("PAYWALL_DOMAINS_EXTRA", "").strip()


def paywall_jsonld_for_all() -> bool:
    """Probe JSON-LD articleBody on non-paywall URLs before Jina fallback.

    Much faster than Jina on SEO-heavy sites (<1s vs 5-15s). Default true.
    Disable if you want generic URLs to go straight to Jina.
    """
    return os.getenv("PAYWALL_JSONLD_FOR_ALL", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Xiaoyuzhou (小宇宙) podcast
# ---------------------------------------------------------------------------

def xiaoyuzhou_enabled() -> bool:
    """Master switch for Xiaoyuzhou fetcher. Default true."""
    return os.getenv("XIAOYUZHOU_ENABLED", "true").lower() in ("true", "1", "yes")


def xiaoyuzhou_whisper() -> bool:
    """Transcribe Xiaoyuzhou audio via Groq Whisper. Default true.

    Disable if you only want metadata + shownotes (saves GROQ quota).
    """
    return os.getenv("XIAOYUZHOU_WHISPER", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Ximalaya (喜马拉雅)
# ---------------------------------------------------------------------------

def ximalaya_enabled() -> bool:
    """Master switch for Ximalaya fetcher. Default true."""
    return os.getenv("XIMALAYA_ENABLED", "true").lower() in ("true", "1", "yes")


def ximalaya_whisper() -> bool:
    """Transcribe Ximalaya audio via Groq Whisper. Default true.

    Disable if you only want metadata (saves GROQ quota). Paid tracks
    (``canPlay=false``) are always skipped regardless of this flag.
    """
    return os.getenv("XIMALAYA_WHISPER", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Bilibili subtitle / transcript
# ---------------------------------------------------------------------------

def bilibili_subtitle_enabled() -> bool:
    """Fetch subtitles for Bilibili videos. Default true.

    Subtitles are typically free (AI-generated or user-uploaded), so we
    always try them. Use /x/player/v2 first, /x/player/wbi/v2 (WBI-signed)
    as second tier.
    """
    return os.getenv("BILIBILI_SUBTITLE_ENABLED", "true").lower() in ("true", "1", "yes")


def bilibili_subtitle_lang() -> str:
    """Preferred subtitle language. Default ``zh-CN``.

    Falls back to zh-Hans/zh/ai-zh/en if exact match unavailable.
    """
    return os.getenv("BILIBILI_SUBTITLE_LANG", "zh-CN").strip() or "zh-CN"


def bilibili_subtitle_whisper() -> bool:
    """Whisper fallback when Bilibili has no subtitles. Default false.

    Transcribing Bilibili audio consumes GROQ quota + time, so opt-in only.
    When enabled, yt-dlp downloads audio → Groq Whisper transcribes.
    """
    return os.getenv("BILIBILI_SUBTITLE_WHISPER", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Zsxq (知识星球)
# ---------------------------------------------------------------------------

def zsxq_enabled() -> bool:
    """Master switch for Zsxq fetcher. Default true."""
    return os.getenv("ZSXQ_ENABLED", "true").lower() in ("true", "1", "yes")


def zsxq_cdp_enabled() -> bool:
    """Connect to running Chrome via CDP for Zsxq. Default true.

    When true, the fetcher reuses the cookies/session of an already-logged-in
    Chrome instance (via ``CHROME_CDP_PORT``, default 9222) instead of relying
    only on ``sessions/zsxq.json``.
    """
    return os.getenv("ZSXQ_CDP_ENABLED", "true").lower() in ("true", "1", "yes")


def zsxq_page_load_timeout() -> int:
    """Stealth Playwright page-load timeout for Zsxq, in milliseconds."""
    try:
        return int(os.getenv("ZSXQ_PAGE_LOAD_TIMEOUT", "20000"))
    except ValueError:
        return 20000


def zsxq_comment_mode() -> str:
    """Comment rendering mode for Zsxq topics: ``none`` | ``all`` | ``author``.

    Default ``none`` — articles seldom benefit from comments and the comment
    API requires extra HTTP calls. ``author`` keeps replies from the topic
    owner only (mirrors LinuxDo's ``LINUXDO_REPLY_MODE``).
    """
    raw = os.getenv("ZSXQ_COMMENT_MODE", "none").strip().lower()
    if raw not in ("none", "all", "author"):
        return "none"
    return raw


def zsxq_max_comments() -> int:
    """Maximum number of comments rendered when comment_mode != none."""
    try:
        return int(os.getenv("ZSXQ_MAX_COMMENTS", "50"))
    except ValueError:
        return 50


def zsxq_download_media() -> bool:
    """Download Zsxq images locally to ``attachments/<id>/``. Default false.

    When false, images keep their original ``images.zsxq.com`` URLs which may
    eventually expire. Enable for archival use cases.
    """
    return os.getenv("ZSXQ_DOWNLOAD_MEDIA", "false").lower() in ("true", "1", "yes")


def zsxq_api_version() -> str:
    """``X-Version`` header sent to api.zsxq.com. Bump if 401s persist after login.

    The value reflects the live Zsxq web client and rotates roughly quarterly.
    Override with ``ZSXQ_API_VERSION`` env if Zsxq tightens the check.
    """
    return os.getenv("ZSXQ_API_VERSION", "2.37.0").strip() or "2.37.0"
