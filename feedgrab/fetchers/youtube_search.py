# -*- coding: utf-8 -*-
"""
YouTube Data API v3 — search, channel browse, video metadata.

Requires: YOUTUBE_API_KEY env var (free, 10,000 quota units/day).
Quota costs: search.list=100, videos.list=1, channels.list=1.
"""

import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional
from urllib.parse import quote_plus

from loguru import logger


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_BASE = "https://www.googleapis.com/youtube/v3"


def _get_api_key() -> str:
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "未设置 YOUTUBE_API_KEY。"
            "请在 Google Cloud Console 开通 YouTube Data API v3 后写入该配置。"
        )
    return key


def _default_region() -> str:
    return os.getenv("YOUTUBE_REGION", "US").strip()


def _default_lang() -> str:
    return os.getenv("YOUTUBE_LANG", "zh-CN").strip()


def _default_max_results() -> int:
    return int(os.getenv("YOUTUBE_MAX_RESULTS", "10"))


# ---------------------------------------------------------------------------
# HTTP helpers (reuse feedgrab's http_client)
# ---------------------------------------------------------------------------

def _api_get(endpoint: str, params: dict) -> dict:
    """Call YouTube API and return JSON, raise on error."""
    from feedgrab.utils import http_client

    params["key"] = _get_api_key()
    url = f"{API_BASE}/{endpoint}"
    resp = http_client.get(url, params=params, timeout=15)

    if resp.status_code != 200:
        error_msg = resp.text[:300]
        raise RuntimeError(f"YouTube API {resp.status_code}: {error_msg}")

    return resp.json()


# ---------------------------------------------------------------------------
# Duration parsing (ISO 8601 PT → readable + seconds)
# ---------------------------------------------------------------------------

def _parse_duration(iso: str) -> tuple:
    """Parse ISO 8601 duration 'PT1H2M10S' → ('1:02:10', 3730)."""
    if not iso:
        return ("0:00", 0)
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return ("0:00", 0)
    h, mi, s = int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0)
    total = h * 3600 + mi * 60 + s
    if h > 0:
        readable = f"{h}:{mi:02d}:{s:02d}"
    else:
        readable = f"{mi}:{s:02d}"
    return (readable, total)


def _parse_duration_input(text: str) -> int:
    """Parse user input like '30m', '1h', '1h30m', '90' → seconds."""
    if not text:
        return 0
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    total = 0
    for val, unit in re.findall(r"(\d+)(h|m|s)?", text):
        val = int(val)
        if unit == "h":
            total += val * 3600
        elif unit == "m":
            total += val * 60
        else:
            total += val
    return total


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------

def _resolve_channel(channel_input: str) -> str:
    """Resolve @handle / URL / channel ID → channelId.

    Tries channels.list?forHandle first, falls back to search.
    """
    # Already a channel ID
    if channel_input.startswith("UC") and len(channel_input) == 24:
        return channel_input

    # Extract handle from URL: youtube.com/@handle or youtube.com/c/name
    handle = channel_input
    url_match = re.search(r"youtube\.com/(?:@|c/)([^/?&]+)", channel_input)
    if url_match:
        handle = url_match.group(1)

    # Ensure @ prefix for API
    if not handle.startswith("@"):
        handle = f"@{handle}"

    # Tier 1: forHandle API (1 quota unit)
    try:
        data = _api_get("channels", {"part": "id", "forHandle": handle})
        items = data.get("items", [])
        if items:
            return items[0]["id"]
    except Exception:
        pass

    # Tier 2: search fallback (100 quota units)
    clean_name = handle.lstrip("@")
    data = _api_get("search", {
        "part": "snippet",
        "q": clean_name,
        "type": "channel",
        "maxResults": 1,
    })
    items = data.get("items", [])
    if items:
        return items[0]["snippet"]["channelId"]

    raise RuntimeError(f"Cannot resolve YouTube channel: {channel_input}")


# ---------------------------------------------------------------------------
# Video details (batch)
# ---------------------------------------------------------------------------

def _get_video_details(video_ids: List[str]) -> List[Dict[str, Any]]:
    """Fetch full details for up to 50 video IDs (1 quota unit total)."""
    if not video_ids:
        return []

    data = _api_get("videos", {
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(video_ids),
    })

    results = []
    for item in data.get("items", []):
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        duration_readable, duration_seconds = _parse_duration(
            content.get("duration", "")
        )

        # Best thumbnail
        thumbs = snippet.get("thumbnails", {})
        thumb_url = ""
        for key in ("maxres", "high", "medium", "default"):
            if key in thumbs:
                thumb_url = thumbs[key]["url"]
                break

        results.append({
            "video_id": item["id"],
            "title": snippet.get("title", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "channel_id": snippet.get("channelId", ""),
            "published_at": snippet.get("publishedAt", ""),
            "description": snippet.get("description", ""),
            "tags": snippet.get("tags", []),
            "category_id": snippet.get("categoryId", ""),
            "thumbnail": thumb_url,
            "duration": duration_readable,
            "duration_seconds": duration_seconds,
            "definition": content.get("definition", ""),
            "has_caption": content.get("caption") == "true",
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
            "url": f"https://www.youtube.com/watch?v={item['id']}",
        })

    return results


def get_single_video(video_id: str) -> Optional[Dict[str, Any]]:
    """Get details for a single video by ID. Returns None if not found."""
    results = _get_video_details([video_id])
    return results[0] if results else None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def youtube_search(
    keyword: str,
    *,
    channel: str = "",
    max_results: int = 0,
    order: str = "relevance",
    after: str = "",
    before: str = "",
    min_duration: str = "",
    max_duration: str = "",
    region: str = "",
    lang: str = "",
) -> List[Dict[str, Any]]:
    """Search YouTube videos and return detailed results.

    Args:
        keyword: Search query
        channel: Channel handle/@name/URL/ID to restrict search
        max_results: Number of results (default from config, max 50)
        order: Sort order — relevance/date/viewCount/rating
        after: Only videos published after this date (YYYY-MM-DD)
        before: Only videos published before this date (YYYY-MM-DD)
        min_duration: Minimum duration (e.g. '10m', '1h', '90')
        max_duration: Maximum duration (e.g. '30m', '2h')
        region: Region code (default from config)
        lang: Language preference (default from config)

    Returns:
        List of video detail dicts.
    """
    if not max_results:
        max_results = _default_max_results()
    max_results = min(max_results, 50)

    params: dict = {
        "part": "snippet",
        "q": keyword,
        "type": "video",
        "maxResults": max_results,
        "order": order,
    }

    # Channel filter
    if channel:
        channel_id = _resolve_channel(channel)
        params["channelId"] = channel_id
        logger.info(f"[YouTube] Channel resolved: {channel} → {channel_id}")

    # Date filters
    if after:
        params["publishedAfter"] = f"{after}T00:00:00Z"
    if before:
        params["publishedBefore"] = f"{before}T23:59:59Z"

    # Region / language (skip for channel-scoped search — can cause empty results)
    if not channel:
        params["regionCode"] = region or _default_region()
        params["relevanceLanguage"] = lang or _default_lang()

    logger.info(
        f"[YouTube] Searching: '{keyword}'"
        f"{f' channel={channel}' if channel else ''}"
        f" order={order} limit={max_results}"
    )

    # Phase 1: Search → video IDs (100 quota units)
    search_data = _api_get("search", params)
    video_ids = [
        item["id"]["videoId"]
        for item in search_data.get("items", [])
        if item["id"].get("videoId")
    ]

    if not video_ids:
        logger.warning("[YouTube] 未找到结果")
        return []

    # Phase 2: Video details (1 quota unit)
    results = _get_video_details(video_ids)

    # Client-side duration filter (API only supports short/medium/long)
    min_sec = _parse_duration_input(min_duration)
    max_sec = _parse_duration_input(max_duration)
    if min_sec or max_sec:
        before_count = len(results)
        results = [
            v for v in results
            if (not min_sec or v["duration_seconds"] >= min_sec)
            and (not max_sec or v["duration_seconds"] <= max_sec)
        ]
        logger.info(
            f"[YouTube] Duration filter: {before_count} → {len(results)}"
            f" ({min_duration or '0'}~{max_duration or '∞'})"
        )

    # Enforce max_results limit (API may return more than requested)
    if len(results) > max_results:
        results = results[:max_results]

    logger.info(f"[YouTube] Found {len(results)} videos")
    return results


# ---------------------------------------------------------------------------
# Download helpers (yt-dlp integration)
# ---------------------------------------------------------------------------

def _js_runtime_args() -> List[str]:
    """Detect available JS runtime for yt-dlp."""
    for name, cmd in [("deno", "deno"), ("node", "node"), ("bun", "bun")]:
        if shutil.which(cmd):
            return ["--js-runtimes", name, "--remote-components", "ejs:github"]
    return []


def _detect_browser_cookie() -> List[str]:
    """Detect available browser for yt-dlp cookie extraction.

    Returns empty list by default — cookie extraction often fails on Windows
    (Chrome DB locked) and most public videos don't need auth.
    """
    # Skip by default — cookie extraction is unreliable
    # Users can set YT_COOKIES_BROWSER env var to enable
    browser = os.getenv("YT_COOKIES_BROWSER", "").strip()
    if browser:
        return ["--cookies-from-browser", browser]
    return []


def download_video(
    url: str,
    output_dir: str = "",
    quality: str = "best",
    audio_only: bool = False,
    filename_prefix: str = "",
) -> Optional[str]:
    """Download video/audio via yt-dlp. Returns output file path or None."""
    if not output_dir:
        output_dir = os.path.expanduser("~/Downloads")
    os.makedirs(output_dir, exist_ok=True)

    if filename_prefix:
        output_template = os.path.join(output_dir, f"{filename_prefix}.%(ext)s")
    else:
        output_template = os.path.join(output_dir, "%(title).80s.%(ext)s")
    cookie_args = _detect_browser_cookie()

    if audio_only:
        cmd = [
            "yt-dlp", *_js_runtime_args(),
            "-x", "--audio-format", "mp3",
            "-o", output_template, "--no-playlist",
            *cookie_args, url,
        ]
    else:
        fmt_map = {
            "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
            "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
            "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
        }
        cmd = [
            "yt-dlp", *_js_runtime_args(),
            "-f", fmt_map.get(quality, fmt_map["best"]),
            "-o", output_template, "--no-playlist",
            *cookie_args, url,
        ]

    logger.info(f"[YouTube] Downloading: {url} ({'audio' if audio_only else quality})")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            # Extract output filename from yt-dlp output
            for line in result.stdout.splitlines():
                if "Destination:" in line or "has already been downloaded" in line:
                    path = line.split("Destination:", 1)[-1].strip() if "Destination:" in line else ""
                    if path and os.path.exists(path):
                        return path
            # Fallback: find most recent file in output_dir
            logger.info("[YouTube] Download completed")
            return output_dir
        else:
            logger.warning(f"[YouTube] yt-dlp error: {result.stderr[:200]}")
            return None
    except FileNotFoundError:
        logger.error("[YouTube] 未找到 yt-dlp。请安装：pip install yt-dlp")
        return None
    except subprocess.TimeoutExpired:
        logger.error("[YouTube] 下载超时（10 分钟限制）")
        return None


def download_subtitles(
    url: str,
    output_dir: str = "",
    lang: str = "zh-CN",
    filename_prefix: str = "",
) -> Optional[str]:
    """Download subtitles via yt-dlp as SRT file. Returns output path or None."""
    if not output_dir:
        output_dir = os.path.expanduser("~/Downloads")
    os.makedirs(output_dir, exist_ok=True)

    if filename_prefix:
        output_template = os.path.join(output_dir, filename_prefix)
    else:
        output_template = os.path.join(output_dir, "%(title).80s")

    # Try multiple languages in priority order
    langs_to_try = [lang, "zh-CN", "zh-Hans", "zh-Hant", "zh", "en", "en-US"]
    seen = set()
    langs_to_try = [l for l in langs_to_try if l not in seen and not seen.add(l)]

    for try_lang in langs_to_try:
        cmd = [
            "yt-dlp", *_js_runtime_args(),
            "--write-auto-sub", "--write-sub",
            "--sub-lang", try_lang,
            "--sub-format", "srt",
            "--skip-download",
            "-o", output_template,
            "--no-playlist",
            url,
        ]

        logger.info(f"[YouTube] Downloading subtitles ({try_lang}): {url}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            # Check if SRT file was created
            for ext in [f".{try_lang}.srt", f".{try_lang}.vtt"]:
                import glob
                pattern = os.path.join(output_dir, f"*{ext}")
                matches = glob.glob(pattern)
                if matches:
                    logger.info(f"[YouTube] Subtitle saved: {matches[-1]}")
                    return matches[-1]
        except FileNotFoundError:
            logger.error("[YouTube] 未找到 yt-dlp。请安装：pip install yt-dlp")
            return None
        except subprocess.TimeoutExpired:
            logger.error("[YouTube] 字幕下载超时")
            return None

    logger.warning("[YouTube] 未找到任何语言的字幕")
    return None
