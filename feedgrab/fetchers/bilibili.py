# -*- coding: utf-8 -*-
"""
Bilibili video fetcher — metadata + subtitles + Whisper fallback.

Tier 0: ``x/web-interface/view`` — video metadata (title, cid, author, ...)
Tier 1: ``x/player/v2`` (no signature) — subtitle list, works for legacy videos
Tier 2: ``x/player/wbi/v2`` (WBI-signed) — current subtitle endpoint
Tier 3: Groq Whisper via yt-dlp (optional, off by default)

Subtitle JSON format (same across tiers):
    {"body": [{"from": 0.0, "to": 2.5, "content": "...", "location": 2}, ...]}
"""

import re
from typing import Any, Dict, List, Optional

import requests
from loguru import logger

from feedgrab.config import (
    bilibili_subtitle_enabled,
    bilibili_subtitle_lang,
    bilibili_subtitle_whisper,
    get_stealth_headers,
    get_user_agent,
)
from feedgrab.utils import http_client
from feedgrab.utils.bilibili_wbi import sign_wbi_params
from feedgrab.utils.transcribe import (
    format_transcript,
    subtitle_body_to_snippets,
)


_VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
_PLAYER_V2_URL = "https://api.bilibili.com/x/player/v2"
_PLAYER_WBI_V2_URL = "https://api.bilibili.com/x/player/wbi/v2"
_BILI_REFERER = "https://www.bilibili.com/"
_HTTP_TIMEOUT = 15


def _bili_headers() -> Dict[str, str]:
    return {
        **get_stealth_headers(),
        "Referer": _BILI_REFERER,
        "Origin": "https://www.bilibili.com",
    }


def _extract_bvid(url_or_bv: str) -> str:
    if not url_or_bv:
        raise ValueError("Bilibili URL/BV 为空")
    if url_or_bv.startswith("BV"):
        return url_or_bv
    m = re.search(r"BV\w+", url_or_bv)
    if not m:
        raise ValueError(f"无法从输入中提取 BV ID：{url_or_bv}")
    return m.group()


def _fetch_view(bvid: str) -> Dict[str, Any]:
    """``x/web-interface/view`` — full video metadata incl. cid and aid."""
    resp = http_client.get(
        _VIEW_URL,
        params={"bvid": bvid},
        headers=_bili_headers(),
        timeout=_HTTP_TIMEOUT,
    )
    http_client.raise_for_status(resp)
    data = resp.json()
    if data.get("code") != 0:
        raise ValueError(f"Bilibili view API 错误：{data.get('message')}（code={data.get('code')}）")
    return data["data"]


def _fetch_player_info(aid: int, cid: int, bvid: str, signed: bool) -> Optional[Dict[str, Any]]:
    """Fetch player info (incl. subtitles) with optional WBI signature."""
    if signed:
        try:
            params = sign_wbi_params({"aid": aid, "cid": cid, "bvid": bvid})
            endpoint = _PLAYER_WBI_V2_URL
        except Exception as e:
            logger.warning(f"[Bilibili] WBI 签名失败：{e}")
            return None
    else:
        params = {"aid": aid, "cid": cid, "bvid": bvid}
        endpoint = _PLAYER_V2_URL

    try:
        resp = http_client.get(
            endpoint,
            params=params,
            headers=_bili_headers(),
            timeout=_HTTP_TIMEOUT,
        )
        http_client.raise_for_status(resp)
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"[Bilibili] player API 请求失败（{'wbi' if signed else 'v2'}）：{e}")
        return None

    if data.get("code") != 0:
        logger.debug(f"[Bilibili] player API code={data.get('code')} msg={data.get('message')}")
        return None
    return data.get("data") or {}


def _pick_best_subtitle(
    subtitles: List[Dict[str, Any]], preferred_lang: str
) -> Optional[Dict[str, Any]]:
    """Choose best subtitle track: exact lang match → zh-CN/zh-Hans → first."""
    if not subtitles:
        return None
    prefs = [preferred_lang, "zh-CN", "zh-Hans", "zh-Hant", "zh", "ai-zh", "en", "en-US"]
    seen = set()
    ordered_prefs = [p for p in prefs if p and p not in seen and not seen.add(p)]
    for pref in ordered_prefs:
        for sub in subtitles:
            lan = (sub.get("lan") or "").strip()
            if lan == pref:
                return sub
    return subtitles[0]


def _fetch_subtitle_body(subtitle_url: str) -> List[Dict[str, Any]]:
    """Download and parse the actual subtitle JSON file."""
    if not subtitle_url:
        return []
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    try:
        resp = http_client.get(
            subtitle_url,
            headers={"User-Agent": get_user_agent(), "Referer": _BILI_REFERER},
            timeout=_HTTP_TIMEOUT,
        )
        http_client.raise_for_status(resp)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"[Bilibili] 字幕抓取失败：{e}")
        return []
    body = data.get("body") or []
    if not isinstance(body, list):
        return []
    return body


def _fetch_subtitles_snippets(aid: int, cid: int, bvid: str) -> List[Dict[str, Any]]:
    """3-tier cascade: v2 → wbi/v2. Returns Whisper-style snippets or []."""
    preferred_lang = bilibili_subtitle_lang()

    # Tier 1: unsigned /x/player/v2 (works for legacy videos)
    logger.info(f"[Bilibili] Tier 1 — /x/player/v2: aid={aid} cid={cid}")
    info = _fetch_player_info(aid, cid, bvid, signed=False)
    subtitles = ((info or {}).get("subtitle") or {}).get("subtitles") or []

    if not subtitles:
        # Tier 2: signed /x/player/wbi/v2
        logger.info(f"[Bilibili] Tier 2 — /x/player/wbi/v2 (WBI-signed): aid={aid} cid={cid}")
        info = _fetch_player_info(aid, cid, bvid, signed=True)
        subtitles = ((info or {}).get("subtitle") or {}).get("subtitles") or []

    if not subtitles:
        logger.info("[Bilibili] 两个 player 端点都没有可用字幕")
        return []

    chosen = _pick_best_subtitle(subtitles, preferred_lang)
    if not chosen:
        return []

    sub_url = chosen.get("subtitle_url") or ""
    lan = chosen.get("lan") or "?"
    logger.info(f"[Bilibili] 已选择字幕：lan={lan}, url={sub_url[:80]}")

    body = _fetch_subtitle_body(sub_url)
    snippets = subtitle_body_to_snippets(body)
    logger.info(f"[Bilibili] 已解析 {len(snippets)} 条字幕片段")
    return snippets


def _whisper_fallback_snippets(video_url: str) -> List[Dict[str, Any]]:
    """Tier 3: use youtube.py's Whisper pipeline (yt-dlp supports Bilibili)."""
    try:
        from feedgrab.fetchers.youtube import _transcribe_via_whisper
    except ImportError as e:
        logger.warning(f"[Bilibili] Whisper 兜底导入失败：{e}")
        return []
    logger.info(f"[Bilibili] Tier 3 — Whisper via yt-dlp: {video_url}")
    return _transcribe_via_whisper(video_url)


async def fetch_bilibili(url_or_bv: str) -> Dict[str, Any]:
    """Fetch Bilibili video metadata + subtitles (+ optional Whisper)."""
    logger.info(f"[Bilibili] Tier 0 — view API: {url_or_bv}")
    bvid = _extract_bvid(url_or_bv) if ("bilibili.com" in url_or_bv or "b23.tv" in url_or_bv) else url_or_bv
    if not bvid.startswith("BV"):
        bvid = _extract_bvid(bvid)

    view = _fetch_view(bvid)

    aid = int(view.get("aid") or 0)
    cid = int(view.get("cid") or 0)
    title = view.get("title", "")
    desc = view.get("desc", "")
    author = (view.get("owner") or {}).get("name", "")
    cover = view.get("pic", "")
    duration = int(view.get("duration") or 0)
    pubdate = int(view.get("pubdate") or 0)
    stat = view.get("stat") or {}

    canonical_url = f"https://www.bilibili.com/video/{bvid}"
    logger.info(
        f"[Bilibili] title={title[:40]!r} author={author!r} "
        f"aid={aid} cid={cid} duration={duration}s"
    )

    # Subtitle + Whisper pipeline
    transcript = ""
    has_transcript = False
    if bilibili_subtitle_enabled() and aid and cid:
        snippets = _fetch_subtitles_snippets(aid, cid, bvid)

        if not snippets and bilibili_subtitle_whisper():
            snippets = _whisper_fallback_snippets(canonical_url)

        if snippets:
            transcript = format_transcript(snippets, description=desc)
            has_transcript = bool(transcript)
            logger.info(
                f"[Bilibili] transcript: {len(transcript)} chars, {len(snippets)} snippets"
            )

    return {
        "title": title,
        "description": desc,
        "author": author,
        "url": canonical_url,
        "cover": cover,
        "bvid": bvid,
        "aid": aid,
        "cid": cid,
        "duration": duration,
        "pubdate": pubdate,
        "view_count": stat.get("view", 0),
        "like_count": stat.get("like", 0),
        "coin_count": stat.get("coin", 0),
        "favorite_count": stat.get("favorite", 0),
        "transcript": transcript,
        "has_transcript": has_transcript,
        "platform": "bilibili",
    }
