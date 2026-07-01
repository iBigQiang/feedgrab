# -*- coding: utf-8 -*-
"""
Xiaohongshu API client — pure HTTP with xhshow signing.

Provides a fast API layer (Tier 0) for XHS fetching, falling back
to browser-based approaches when unavailable.

Dependencies (optional — graceful degradation):
  pip install xhshow httpx
"""

from __future__ import annotations

import json
import logging
import os
import platform
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

# ---------------------------------------------------------------------------
# Optional dependency: xhshow signing library
# ---------------------------------------------------------------------------

_XHSHOW_AVAILABLE = False
try:
    from xhshow import CryptoConfig, SessionManager, Xhshow
    from xhshow.utils.url_utils import extract_uri

    _XHSHOW_AVAILABLE = True
except ImportError:
    pass

_HTTPX_AVAILABLE = False
try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    pass


def is_api_available() -> bool:
    """Check if XHS API mode is usable (xhshow + httpx installed + cookies exist)."""
    if not _XHSHOW_AVAILABLE or not _HTTPX_AVAILABLE:
        return False
    from feedgrab.config import get_data_dir

    session_path = get_data_dir() / "xhs.json"
    return session_path.exists()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EDITH_HOST = "https://edith.xiaohongshu.com"
_HOME_URL = "https://www.xiaohongshu.com"
_SDK_VERSION = "4.2.6"
_APP_ID = "xhs-pc-web"

# xsec_token cache
_TOKEN_CACHE_MAX = 500


# ---------------------------------------------------------------------------
# Platform & UA detection — use real system fingerprint
# ---------------------------------------------------------------------------


def _detect_platform() -> str:
    """Detect real OS platform for signing config."""
    sys_name = platform.system().lower()
    return {"windows": "Windows", "darwin": "macOS", "linux": "Linux"}.get(
        sys_name, "Windows"
    )


def _get_chrome_version(ua: str) -> str:
    """Extract Chrome major version from UA string."""
    m = re.search(r"Chrome/(\d+)", ua)
    return m.group(1) if m else "132"


def _get_api_user_agent() -> str:
    """Get UA for API requests — use real detected UA for fingerprint consistency."""
    from feedgrab.config import get_user_agent

    return get_user_agent()


def _build_sec_ch_ua(chrome_ver: str) -> str:
    """Build sec-ch-ua header matching Chrome version."""
    return (
        f'"Not:A-Brand";v="99", "Google Chrome";v="{chrome_ver}", '
        f'"Chromium";v="{chrome_ver}"'
    )


def _build_sec_ch_ua_platform() -> str:
    """Build sec-ch-ua-platform header matching real OS."""
    plat = _detect_platform()
    return f'"{plat}"'


# ---------------------------------------------------------------------------
# Signing configuration — matches real system fingerprint
# ---------------------------------------------------------------------------

_signing_instance = None  # lazy init


def _get_signing():
    """Lazy-initialize xhshow signing with real platform fingerprint."""
    global _signing_instance
    if _signing_instance is not None:
        return _signing_instance

    if not _XHSHOW_AVAILABLE:
        return None

    ua = _get_api_user_agent()
    plat = _detect_platform()

    config = CryptoConfig().with_overrides(
        PUBLIC_USERAGENT=ua,
        SIGNATURE_DATA_TEMPLATE={
            "x0": _SDK_VERSION,
            "x1": _APP_ID,
            "x2": plat,
            "x3": "",
            "x4": "",
        },
        SIGNATURE_XSCOMMON_TEMPLATE={
            "s0": 5,
            "s1": "",
            "x0": "1",
            "x1": _SDK_VERSION,
            "x2": plat,
            "x3": _APP_ID,
            "x4": "4.86.0",
            "x5": "",
            "x6": "",
            "x7": "",
            "x8": "",
            "x9": -596800761,
            "x10": 0,
            "x11": "normal",
        },
    )

    _signing_instance = {
        "xhshow": Xhshow(config),
        "session": SessionManager(config),
        "ua": ua,
        "platform": plat,
        "chrome_ver": _get_chrome_version(ua),
    }
    logger.debug(f"[XHS-API] Signing initialized: platform={plat}, Chrome/{_signing_instance['chrome_ver']}")
    return _signing_instance


# ---------------------------------------------------------------------------
# Cookie extraction from Playwright session
# ---------------------------------------------------------------------------


def _extract_cookies_from_session() -> dict[str, str]:
    """Extract API-usable cookies from sessions/xhs.json (Playwright storage_state)."""
    from feedgrab.config import get_data_dir

    session_path = get_data_dir() / "xhs.json"
    if not session_path.exists():
        raise FileNotFoundError(f"未找到小红书登录态：{session_path}。请运行：feedgrab login xhs")

    data = json.loads(session_path.read_text(encoding="utf-8"))
    cookies: dict[str, str] = {}
    for c in data.get("cookies", []):
        domain = c.get("domain", "")
        if "xiaohongshu.com" in domain:
            cookies[c["name"]] = c["value"]

    if "a1" not in cookies:
        raise ValueError("小红书登录态缺少关键 Cookie a1，请运行：feedgrab login xhs")

    return cookies


def _cookies_to_string(cookies: dict[str, str]) -> str:
    """Format cookies as HTTP Cookie header value."""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


# ---------------------------------------------------------------------------
# xsec_token disk cache
# ---------------------------------------------------------------------------


def _get_token_cache_path() -> Path:
    from feedgrab.config import get_data_dir

    cache_dir = get_data_dir() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "xhs_token_cache.json"


def _load_token_cache() -> dict:
    path = _get_token_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_token_cache(cache: dict) -> None:
    path = _get_token_cache_path()
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def cache_xsec_token(note_id: str, xsec_token: str) -> None:
    """Store a resolved xsec_token for later reuse (LRU, max 500)."""
    if not note_id or not xsec_token:
        return
    cache = _load_token_cache()

    existing = cache.get(note_id)
    if isinstance(existing, dict) and existing.get("token") == xsec_token:
        existing["ts"] = time.time()
        _save_token_cache(cache)
        return

    cache[note_id] = {"token": xsec_token, "ts": time.time()}

    # Evict oldest if over limit
    if len(cache) > _TOKEN_CACHE_MAX:
        sorted_keys = sorted(
            cache.keys(),
            key=lambda k: cache[k].get("ts", 0) if isinstance(cache[k], dict) else 0,
        )
        for key in sorted_keys[: len(cache) - _TOKEN_CACHE_MAX]:
            del cache[key]

    _save_token_cache(cache)


def get_cached_xsec_token(note_id: str) -> str:
    """Retrieve cached xsec_token for a note, or empty string."""
    entry = _load_token_cache().get(note_id, "")
    if isinstance(entry, dict):
        return entry.get("token", "")
    return str(entry) if entry else ""


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


def parse_note_url(url: str) -> tuple[str, str]:
    """Extract (note_id, xsec_token) from a Xiaohongshu note URL.

    Returns:
        (note_id, xsec_token) — token may be empty
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    # /explore/{note_id} or /discovery/item/{note_id}
    note_id = ""
    for prefix in ("/explore/", "/discovery/item/"):
        if prefix in path:
            note_id = path.split(prefix)[-1].split("/")[0].split("?")[0]
            break

    if not note_id:
        # Try last path segment
        segments = [s for s in path.split("/") if s]
        if segments:
            note_id = segments[-1]

    # Extract xsec_token from query params
    qs = parse_qs(parsed.query)
    xsec_token = qs.get("xsec_token", [""])[0]

    # Try cache if no token in URL
    if not xsec_token and note_id:
        xsec_token = get_cached_xsec_token(note_id)

    return note_id, xsec_token


def parse_profile_url(url: str) -> str:
    """Extract user_id from a Xiaohongshu profile URL."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    # /user/profile/{user_id}
    if "/user/profile/" in path:
        return path.split("/user/profile/")[-1].split("/")[0].split("?")[0]
    return ""


# ---------------------------------------------------------------------------
# search_id generator
# ---------------------------------------------------------------------------


def _generate_search_id() -> str:
    """Generate unique search ID: (timestamp_ms << 64) + random in base36."""
    e = int(time.time() * 1000) << 64
    t = random.randint(0, 2147483646)
    num = e + t

    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if num == 0:
        return "0"
    result = ""
    while num > 0:
        result = alphabet[num % 36] + result
        num //= 36
    return result


# ---------------------------------------------------------------------------
# XhsApiClient
# ---------------------------------------------------------------------------


class XhsApiClient:
    """Xiaohongshu API client with signing, rate limiting, and retry."""

    def __init__(
        self,
        cookies: dict[str, str],
        delay: float = 1.0,
        max_retries: int = 3,
    ):
        self.cookies = dict(cookies)
        self._delay = delay
        self._base_delay = delay
        self._max_retries = max_retries
        self._last_request_time = 0.0
        self._verify_count = 0
        self._http = httpx.Client(timeout=30.0, follow_redirects=True)
        self._signing = _get_signing()

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # --- Rate limiting (Gaussian jitter) ---

    def _rate_limit_delay(self) -> None:
        """Enforce delay with Gaussian jitter to mimic human browsing."""
        if self._delay <= 0:
            return
        elapsed = time.time() - self._last_request_time
        if elapsed < self._delay:
            jitter = max(0, random.gauss(0.3, 0.15))
            # 5% chance of longer pause
            if random.random() < 0.05:
                jitter += random.uniform(2.0, 5.0)
            sleep_time = self._delay - elapsed + jitter
            time.sleep(sleep_time)

    def _mark_request(self) -> None:
        self._last_request_time = time.time()

    # --- HTTP headers ---

    def _base_headers(self) -> dict[str, str]:
        s = self._signing
        ua = s["ua"]
        chrome_ver = s["chrome_ver"]
        return {
            "user-agent": ua,
            "content-type": "application/json;charset=UTF-8",
            "cookie": _cookies_to_string(self.cookies),
            "origin": _HOME_URL,
            "referer": f"{_HOME_URL}/",
            "sec-ch-ua": _build_sec_ch_ua(chrome_ver),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": _build_sec_ch_ua_platform(),
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "dnt": "1",
            "priority": "u=1, i",
        }

    # --- Response handling ---

    def _handle_response(self, resp) -> Any:
        """Parse API response, handle errors."""
        if resp.status_code in (461, 471):
            self._verify_count += 1
            cooldown = min(30, 5 * (2 ** (self._verify_count - 1)))
            logger.warning(
                f"[XHS-API] Captcha triggered (count={self._verify_count}), "
                f"cooling down {cooldown}s"
            )
            self._delay = max(self._delay, self._base_delay * 2)
            time.sleep(cooldown)
            raise XhsApiError(f"Captcha required (HTTP {resp.status_code})")

        self._verify_count = 0
        text = resp.text
        if not text:
            return None

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise XhsApiError(f"接口返回非 JSON 内容：{text[:200]}")

        if data.get("success"):
            return data.get("data", data.get("success"))

        code = data.get("code")
        if code == -100:
            raise XhsSessionExpiredError("小红书登录态已过期")
        if code == 300012:
            raise XhsApiError("当前 IP 被小红书限制")
        if code == 300015:
            raise XhsApiError("小红书签名错误")

        raise XhsApiError(f"小红书 API 错误（code={code}）：{json.dumps(data)[:200]}")

    # --- Request with retry ---

    def _request_with_retry(self, method: str, url: str, **kwargs) -> Any:
        """Send HTTP request with exponential backoff retry."""
        self._rate_limit_delay()
        last_exc: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                resp = self._http.request(method, url, **kwargs)
                # Merge response cookies back
                for name, value in resp.cookies.items():
                    if value:
                        self.cookies[name] = value
                self._mark_request()

                if resp.status_code in (429, 500, 502, 503, 504):
                    wait = (2**attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"[XHS-API] HTTP {resp.status_code}, "
                        f"{wait:.1f}s 后重试（{attempt + 1}/{self._max_retries}）"
                    )
                    time.sleep(wait)
                    continue
                return self._handle_response(resp)
            except (XhsApiError, XhsSessionExpiredError):
                raise
            except Exception as exc:
                last_exc = exc
                wait = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    f"[XHS-API] 网络错误：{exc}，"
                    f"{wait:.1f}s 后重试（{attempt + 1}/{self._max_retries}）"
                )
                time.sleep(wait)

        if last_exc:
            raise XhsApiError(
                f"请求重试 {self._max_retries} 次后仍失败：{last_exc}"
            )
        raise XhsApiError(f"请求重试 {self._max_retries} 次后仍失败")

    # --- Signed API calls ---

    def _sign_and_get(
        self,
        uri: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        s = self._signing
        sign_headers = s["xhshow"].sign_headers_get(
            uri, self.cookies, params=params, session=s["session"]
        )
        full_uri = s["xhshow"].build_url(uri, params) if params else uri
        url = f"{_EDITH_HOST}{full_uri}"
        headers = {**self._base_headers(), **sign_headers}
        return self._request_with_retry("GET", url, headers=headers)

    def _sign_and_post(
        self,
        uri: str,
        data: dict[str, Any],
    ) -> Any:
        s = self._signing
        sign_headers = s["xhshow"].sign_headers_post(
            uri, self.cookies, payload=data, session=s["session"]
        )
        url = f"{_EDITH_HOST}{uri}"
        headers = {**self._base_headers(), **sign_headers}
        body = json.dumps(data, separators=(",", ":"))
        return self._request_with_retry("POST", url, headers=headers, content=body)

    # === API Endpoints ===

    def feed_note(
        self,
        note_id: str,
        xsec_token: str = "",
        xsec_source: str = "pc_feed",
    ) -> dict[str, Any] | None:
        """Fetch a single note via Feed API.

        Returns normalized note dict or None if empty.
        """
        if xsec_token:
            cache_xsec_token(note_id, xsec_token)

        data = self._sign_and_post(
            "/api/sns/web/v1/feed",
            {
                "source_note_id": note_id,
                "image_formats": ["jpg", "webp", "avif"],
                "extra": {"need_body_topic": "1"},
                "xsec_source": xsec_source,
                "xsec_token": xsec_token,
            },
        )

        if not data or not isinstance(data, dict):
            return None

        items = data.get("items", [])
        if not items:
            return None

        note_card = items[0].get("note_card")
        if not note_card:
            return None

        # Cache token from response
        resp_token = items[0].get("xsec_token", "")
        if resp_token:
            cache_xsec_token(note_id, resp_token)

        return normalize_api_note(note_card, note_id)

    def get_user_notes_page(
        self, user_id: str, cursor: str = ""
    ) -> dict[str, Any]:
        """Fetch one page of user notes (30 per page, cursor-based)."""
        return self._sign_and_get(
            "/api/sns/web/v1/user_posted",
            {
                "num": 30,
                "cursor": cursor,
                "user_id": user_id,
                "image_scenes": "FD_WM_WEBP",
            },
        )

    def get_all_user_notes(
        self,
        user_id: str,
        since_date: str = "",
        max_pages: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch all user notes via cursor pagination.

        Args:
            user_id: XHS user ID
            since_date: YYYY-MM-DD filter (stop when older notes found)
            max_pages: 0 = unlimited

        Returns:
            List of raw note dicts from API (with note_id and xsec_token),
            deduplicated by note_id.
        """
        all_notes: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        cursor = ""
        pages = 0
        since_ts = 0
        if since_date:
            try:
                since_ts = int(
                    datetime.strptime(since_date, "%Y-%m-%d").timestamp() * 1000
                )
            except ValueError:
                pass

        old_streak = 0

        while True:
            if max_pages > 0 and pages >= max_pages:
                break

            data = self.get_user_notes_page(user_id, cursor=cursor)
            if not data or not isinstance(data, dict):
                break

            notes = data.get("notes", [])
            if not notes:
                break

            for note in notes:
                note_id = note.get("note_id", "")
                xsec_token = note.get("xsec_token", "")
                if note_id and xsec_token:
                    cache_xsec_token(note_id, xsec_token)

                # Dedup by note_id
                if note_id and note_id in seen_ids:
                    continue
                if note_id:
                    seen_ids.add(note_id)

                # Date filter
                if since_ts and note.get("time"):
                    try:
                        note_ts = int(note["time"])
                        if note_ts < since_ts:
                            old_streak += 1
                            if old_streak >= 3:
                                logger.info(
                                    f"[XHS-API] 3 consecutive notes older than {since_date}, stopping"
                                )
                                return all_notes
                            continue
                        else:
                            old_streak = 0
                    except (ValueError, TypeError):
                        old_streak = 0

                all_notes.append(note)

            pages += 1
            has_more = data.get("has_more", False)
            next_cursor = data.get("cursor", "")
            if not has_more or not next_cursor:
                break
            cursor = next_cursor

            logger.info(
                f"[XHS-API] User notes page {pages}: {len(notes)} notes "
                f"(total {len(all_notes)})"
            )

        return all_notes

    def search_notes_page(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        sort: str = "general",
        note_type: int = 0,
    ) -> dict[str, Any]:
        """Search notes — one page."""
        # Map friendly sort names to API values
        sort_map = {
            "general": "general",
            "popular": "popularity_descending",
            "latest": "time_descending",
        }
        api_sort = sort_map.get(sort, sort)

        return self._sign_and_post(
            "/api/sns/web/v1/search/notes",
            {
                "keyword": keyword,
                "page": page,
                "page_size": page_size,
                "search_id": _generate_search_id(),
                "sort": api_sort,
                "note_type": note_type,
                "ext_flags": [],
                "geo": "",
                "image_formats": ["jpg", "webp", "avif"],
            },
        )

    def get_all_search_notes(
        self,
        keyword: str,
        sort: str = "general",
        note_type: int = 0,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """Search notes with auto-pagination.

        Returns:
            List of raw search result items (deduplicated by note_id).
        """
        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for page in range(1, max_pages + 1):
            data = self.search_notes_page(
                keyword, page=page, sort=sort, note_type=note_type
            )
            if not data or not isinstance(data, dict):
                break

            items = data.get("items", [])
            if not items:
                break

            added = 0
            for item in items:
                # Filter: only keep note items (skip ads, rec_query, etc.)
                if item.get("model_type") and item.get("model_type") != "note":
                    continue

                # Cache xsec_token from search results
                note_card = item.get("note_card") or item.get("note", {})
                note_id = (
                    item.get("id")
                    or note_card.get("note_id")
                    or item.get("note_id", "")
                )
                # Skip items without note_id (broken/ad items)
                if not note_id:
                    continue

                xsec_token = item.get("xsec_token", "")
                if note_id and xsec_token:
                    cache_xsec_token(note_id, xsec_token)

                # Dedup by note_id
                if note_id in seen_ids:
                    continue
                seen_ids.add(note_id)
                all_items.append(item)
                added += 1

            has_more = data.get("has_more", True)

            logger.info(
                f"[XHS-API] Search page {page}: {len(items)} results "
                f"(+{added} new, total {len(all_items)})"
            )

            if not has_more or len(items) < 20:
                break

        return all_items

    def get_comments_page(
        self,
        note_id: str,
        cursor: str = "",
        xsec_token: str = "",
    ) -> dict[str, Any]:
        """Fetch one page of comments for a note."""
        if not xsec_token:
            xsec_token = get_cached_xsec_token(note_id)
        if not xsec_token:
            raise XhsApiError(
                f"No xsec_token for note {note_id} — pass a full URL with token"
            )

        return self._sign_and_get(
            "/api/sns/web/v2/comment/page",
            {
                "note_id": note_id,
                "cursor": cursor,
                "top_comment_id": "",
                "image_formats": "jpg,webp,avif",
                "xsec_token": xsec_token,
            },
        )

    def get_all_comments(
        self,
        note_id: str,
        xsec_token: str = "",
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        """Fetch all comments with auto-pagination."""
        all_comments: list[dict[str, Any]] = []
        cursor = ""

        for page in range(max_pages):
            try:
                data = self.get_comments_page(
                    note_id, cursor=cursor, xsec_token=xsec_token
                )
            except XhsApiError:
                break

            if not data or not isinstance(data, dict):
                break

            comments = data.get("comments", [])
            all_comments.extend(comments)

            has_more = data.get("has_more", False)
            next_cursor = data.get("cursor", "")
            if not has_more or not next_cursor:
                break
            cursor = next_cursor

        return all_comments

    def resolve_xsec_token(self, note_id: str) -> str:
        """Resolve xsec_token: cache → HTML extraction."""
        cached = get_cached_xsec_token(note_id)
        if cached:
            return cached

        # Try fetching note HTML and extracting token
        try:
            url = f"{_HOME_URL}/explore/{note_id}"
            resp = self._http.get(
                url,
                headers={
                    "user-agent": self._signing["ua"],
                    "referer": f"{_HOME_URL}/",
                    "cookie": _cookies_to_string(self.cookies),
                },
                follow_redirects=True,
            )
            html = resp.text
            patterns = [
                r'"xsec_token"\s*:\s*"([^"]+)"',
                r"xsec_token=([^&\"']+)",
                r"'xsec_token':'([^']+)'",
            ]
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    token = match.group(1)
                    cache_xsec_token(note_id, token)
                    return token
        except Exception as e:
            logger.debug(f"[XHS-API] xsec_token HTML extraction failed: {e}")

        return ""


# ---------------------------------------------------------------------------
# Data normalization: API response → feedgrab xhs dict
# ---------------------------------------------------------------------------


def normalize_api_note(note_card: dict[str, Any], note_id: str = "") -> dict[str, Any]:
    """Convert XHS API note_card to feedgrab's standard xhs dict format.

    Handles both Feed API responses (title/desc/time) and Search API
    responses (display_title, no desc/time).

    This output is directly consumable by schema.from_xiaohongshu().
    """
    user = note_card.get("user") or {}
    interact = note_card.get("interact_info") or {}

    # Extract images (highest resolution from info_list)
    images = []
    for img in note_card.get("image_list") or []:
        info_list = img.get("info_list") or img.get("url_default") or []
        if isinstance(info_list, list) and info_list:
            # Last item is typically highest resolution
            url = info_list[-1].get("url", "")
            if url:
                images.append(url)
        elif isinstance(info_list, str) and info_list:
            images.append(info_list)
        # Some responses use url_default directly
        url_default = img.get("url_default", "")
        if url_default and url_default not in images:
            images.append(url_default)

    # Cover image fallback (search API uses "cover" instead of image_list details)
    if not images:
        cover = note_card.get("cover") or {}
        cover_url = cover.get("url_default", "") or cover.get("url", "")
        if cover_url:
            images.append(cover_url)

    # Extract tags
    tags = [
        tag.get("name", "")
        for tag in (note_card.get("tag_list") or [])
        if tag.get("name")
    ]

    # Parse timestamp to date string
    date_str = ""
    note_time = note_card.get("time")
    if note_time:
        try:
            ts = int(note_time) / 1000 if int(note_time) > 1e12 else int(note_time)
            date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            pass

    # IP location
    ip_location = note_card.get("ip_location", "")

    # Combine date and location like browser output: "2025-02-18 福建"
    date_with_location = date_str
    if ip_location:
        date_with_location = f"{date_str} {ip_location}" if date_str else ip_location

    def _safe_int(val: Any) -> int:
        if val is None:
            return 0
        try:
            return int(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return 0

    user_id = user.get("user_id", "")
    nid = note_id or note_card.get("note_id", "")

    # Title: Feed API uses "title", Search API uses "display_title"
    title = note_card.get("title", "") or note_card.get("display_title", "")
    # Content: Feed API uses "desc", Search API has no desc
    content = note_card.get("desc", "")

    return {
        "title": title,
        "content": content,
        "author": user.get("nickname", ""),
        "author_url": f"{_HOME_URL}/user/profile/{user_id}" if user_id else "",
        "url": f"{_HOME_URL}/explore/{nid}" if nid else "",
        "platform": "xhs",
        "tags": tags,
        "images": images,
        "likes": _safe_int(interact.get("liked_count")),
        "collects": _safe_int(interact.get("collected_count")),
        "comments": _safe_int(interact.get("comment_count")),
        "share_count": _safe_int(interact.get("share_count")),
        "date": date_with_location,
        "note_type": note_card.get("type", "normal"),
    }


def normalize_search_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a search result item to feedgrab format."""
    note_card = item.get("note_card") or item.get("note") or {}
    note_id = (
        item.get("id")
        or note_card.get("note_id")
        or item.get("note_id", "")
    )
    result = normalize_api_note(note_card, note_id)
    result["xsec_token"] = item.get("xsec_token", "")
    return result


def normalize_user_note_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a user_posted list item (lightweight, fewer fields)."""
    return {
        "note_id": item.get("note_id", ""),
        "xsec_token": item.get("xsec_token", ""),
        "display_title": item.get("display_title", ""),
        "type": item.get("type", "normal"),
        "liked_count": item.get("interact_info", {}).get("liked_count", 0),
        "user": item.get("user", {}),
    }


# ---------------------------------------------------------------------------
# Convenience: get_client()
# ---------------------------------------------------------------------------


def get_client(delay: float | None = None) -> XhsApiClient:
    """Create an XhsApiClient with cookies from session file.

    Args:
        delay: Request interval in seconds (default from config).

    Raises:
        ImportError: if xhshow or httpx not installed
        FileNotFoundError: if no XHS session
    """
    if not _XHSHOW_AVAILABLE:
        raise ImportError(
            "xhshow 未安装，XHS API 模式不可用。\n"
            "  pip install xhshow\n"
            "  将使用浏览器模式作为替代"
        )
    if not _HTTPX_AVAILABLE:
        raise ImportError(
            "httpx 未安装，XHS API 模式不可用。\n"
            "  pip install httpx"
        )

    if delay is None:
        from feedgrab.config import xhs_api_delay

        delay = xhs_api_delay()

    cookies = _extract_cookies_from_session()
    return XhsApiClient(cookies, delay=delay)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class XhsApiError(Exception):
    """General XHS API error."""

    pass


class XhsSessionExpiredError(XhsApiError):
    """XHS session/cookie expired — needs re-login."""

    pass
