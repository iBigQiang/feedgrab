# -*- coding: utf-8 -*-
"""
Xiaohongshu (RED) note fetcher — multi-tier fallback:

0.   XHS API (xhshow signing — fastest, ~0.5s, needs pip install xhshow)
0.5  Pinia Store injection (browser-native, ~1-2s, xhshow failure fallback)
1.   Jina Reader (fast, no deps)
2.   Playwright + saved session (handles 451/403)
3.   Error with login instructions

Install API tier:     pip install xhshow
Install browser tier: pip install "feedgrab[browser]" && playwright install chromium
"""

from loguru import logger
from typing import Dict, Any
from pathlib import Path

from feedgrab.fetchers.jina import fetch_via_jina


async def fetch_xhs(url: str) -> Dict[str, Any]:
    """
    Fetch a Xiaohongshu note with multi-tier fallback.

    Args:
        url: xiaohongshu.com or xhslink.com URL

    Returns:
        Dict with: title, content, author, url, platform, tags, images, etc.
    """
    # Tier 0: XHS API (needs xhshow + session cookies)
    note_id = None  # extracted early for Tier 0.5 Pinia fallback
    try:
        from feedgrab.config import xhs_api_enabled

        if xhs_api_enabled():
            from feedgrab.fetchers.xhs_api import is_api_available, parse_note_url

            if is_api_available():
                note_id, xsec_token = parse_note_url(url)
                if note_id:
                    logger.info(f"[XHS] Tier 0 — API Feed: {url}")
                    from feedgrab.fetchers.xhs_api import get_client

                    with get_client() as client:
                        # Try resolving token if not in URL
                        if not xsec_token:
                            xsec_token = client.resolve_xsec_token(note_id)
                        data = client.feed_note(note_id, xsec_token=xsec_token)

                        # Fetch comments if enabled
                        if data and data.get("content"):
                            from feedgrab.config import xhs_fetch_comments, xhs_max_comments

                            if xhs_fetch_comments():
                                try:
                                    raw_comments = client.get_all_comments(
                                        note_id,
                                        xsec_token=xsec_token,
                                        max_pages=xhs_max_comments(),
                                    )
                                    if raw_comments:
                                        comment_list = []
                                        for c in raw_comments:
                                            user_info = c.get("user_info") or {}
                                            subs = []
                                            for sc in c.get("sub_comments", []):
                                                sc_user = sc.get("user_info") or {}
                                                subs.append({
                                                    "user_nickname": sc_user.get("nickname", ""),
                                                    "content": sc.get("content", ""),
                                                })
                                            comment_list.append({
                                                "user_nickname": user_info.get("nickname", ""),
                                                "content": c.get("content", ""),
                                                "like_count": c.get("like_count", 0),
                                                "sub_comments": subs,
                                            })
                                        data["comment_list"] = comment_list
                                        logger.info(f"[XHS] Fetched {len(comment_list)} comments")
                                except Exception as ce:
                                    logger.warning(f"[XHS] Comment fetch failed: {ce}")

                    if data and data.get("content"):
                        logger.info(f"[XHS] API Feed success: {data.get('title', '')[:40]}")
                        return data
                    logger.warning("[XHS] API Feed returned empty, falling back to Jina")
    except Exception as e:
        logger.warning(f"[XHS] API Feed failed ({e}), falling back")

    # Tier 0.5: Pinia Store injection (browser-native fallback)
    try:
        from feedgrab.config import xhs_pinia_enabled

        if xhs_pinia_enabled() and note_id:
            logger.info(f"[XHS] Tier 0.5 — Pinia Store injection: {note_id}")
            from feedgrab.fetchers.xhs_pinia import pinia_feed_note

            pinia_data = await pinia_feed_note(note_id)
            if pinia_data and pinia_data.get("content"):
                logger.info(f"[XHS] Pinia success: {pinia_data.get('title', '')[:40]}")
                return pinia_data
            logger.warning("[XHS] Pinia returned empty, falling back to Jina")
    except Exception as e:
        logger.warning(f"[XHS] Pinia failed ({e}), falling back to Jina")

    # Tier 1: Jina Reader
    try:
        logger.info(f"[XHS] Tier 1 — Jina: {url}")
        data = fetch_via_jina(url)
        title = data.get("title", "")
        content = data.get("content", "")
        # Detect login page: Jina sometimes returns the login page instead of note
        is_login_page = "小红书 - 你的生活" in title or "登录后推荐" in content
        if content and not is_login_page:
            return {
                "title": title,
                "content": content,
                "author": data.get("author", ""),
                "url": url,
                "platform": "xhs",
            }
        logger.warning("[XHS] Jina returned empty/login page, falling back to browser")
    except Exception as e:
        logger.warning(f"[XHS] Jina failed ({e}), falling back to browser")

    # Tier 2: Playwright with session
    if "xsec_token" not in url and "xiaohongshu.com/explore/" in url:
        logger.warning("[XHS] URL missing xsec_token, likely to get 404")

    from feedgrab.fetchers.browser import get_session_path, SESSION_DIR

    session_path = get_session_path("xhs")
    if not Path(session_path).exists():
        # Tier 3: No session — guide user
        raise RuntimeError(
            "❌ 小红书需要登录态，当前未找到已保存的登录态。\n"
            "   请先运行：feedgrab login xhs\n"
            "   登录后再重新抓取这个 URL。"
        )

    try:
        logger.info(f"[XHS] Tier 2 — Playwright with session: {url}")
        from feedgrab.fetchers.browser import fetch_via_browser

        data = await fetch_via_browser(url, storage_state=session_path)

        # Session expiry detection: XHS redirects to /explore or login page
        final_url = data.get("url", "")
        if final_url and final_url != url:
            if final_url.rstrip("/").endswith("/explore") or "login" in final_url:
                raise RuntimeError(
                    f"❌ 小红书登录态已过期或失效（页面跳转到 {final_url}）。\n"
                    "   请运行：feedgrab login xhs\n"
                    "   登录后再重新抓取这个 URL。"
                )

        return {
            "title": data["title"],
            "content": data["content"],
            "author": data.get("author", ""),
            "author_url": data.get("author_url", ""),
            "url": url,
            "platform": "xhs",
            "tags": data.get("tags", []),
            "images": data.get("images", []),
            "likes": data.get("likes", 0),
            "collects": data.get("collects", 0),
            "comments": data.get("comments", 0),
            "date": data.get("date", ""),
        }
    except RuntimeError:
        # Playwright not installed
        raise
    except Exception as e:
        logger.error(f"[XHS] Browser fetch also failed: {e}")
        raise RuntimeError(
            "❌ 小红书抓取所有兜底方式都失败。\n"
            f"   最后错误：{e}\n"
            "   可尝试运行：feedgrab login xhs（刷新登录态）"
        )
