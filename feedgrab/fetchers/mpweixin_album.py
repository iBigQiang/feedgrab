# -*- coding: utf-8 -*-
"""
WeChat MP album (合集/专辑) batch fetch — enumerate all articles from a
public album via the getalbum API.

No MP backend session required (public albums are accessible without login).
Falls back to wechat.json session if the album page needs authentication.

Usage:
    feedgrab mpweixin-zhuanji "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=xxx&album_id=xxx"

Data flow:
    1. Parse album URL → extract __biz + album_id
    2. Launch stealth browser → navigate to album page
    3. JS evaluate → call action=getalbum API → paginate via begin_msgid/begin_itemidx
    4. For each article: open in new tab → evaluate_wechat_article → save
    5. Dedup via mpweixin/index/ + progress cache for resume
"""

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from loguru import logger
from typing import Dict, Any, Optional

from feedgrab.config import (
    get_session_dir, mpweixin_zhuanji_since, mpweixin_zhuanji_delay,
)
from feedgrab.utils.dedup import (
    load_index, save_index, has_item, add_item, item_id_from_url,
    get_index_path,
)


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def parse_album_url(url: str) -> dict:
    """Extract __biz and album_id from a WeChat album URL.

    Supported formats:
        mp.weixin.qq.com/mp/appmsgalbum?__biz=xxx&album_id=xxx
        mp.weixin.qq.com/mp/appmsgalbum?action=getalbum&album_id=xxx&__biz=xxx
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    album_id = qs.get("album_id", [""])[0]
    biz = qs.get("__biz", [""])[0]

    if not album_id:
        raise ValueError(f"Missing album_id in URL: {url}")

    return {"biz": biz, "album_id": album_id}


# ---------------------------------------------------------------------------
# Progress cache — resume after interruption
# ---------------------------------------------------------------------------

def _progress_path(album_id: str) -> Path:
    index_dir = get_index_path("mpweixin").parent
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in album_id)
    return index_dir / f"_progress_mpweixin_zhuanji_{safe}.json"


def _load_progress(album_id: str) -> dict:
    path = _progress_path(album_id)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_progress(album_id: str, data: dict):
    path = _progress_path(album_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _clear_progress(album_id: str):
    path = _progress_path(album_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Album API via JS evaluate
# ---------------------------------------------------------------------------

_ALBUM_LIST_JS = """async (params) => {
    const url = new URL('https://mp.weixin.qq.com/mp/appmsgalbum');
    url.searchParams.set('action', 'getalbum');
    url.searchParams.set('album_id', params.album_id);
    url.searchParams.set('count', String(params.count));
    if (params.biz) url.searchParams.set('__biz', params.biz);
    if (params.begin_msgid) {
        url.searchParams.set('begin_msgid', params.begin_msgid);
        url.searchParams.set('begin_itemidx', String(params.begin_itemidx));
    }
    url.searchParams.set('f', 'json');
    try {
        const resp = await fetch(url.toString());
        return resp.json();
    } catch (e) {
        return {error: e.message};
    }
}"""


async def _fetch_album_page(
    page, album_id: str, biz: str = "",
    begin_msgid: str = "", begin_itemidx: int = 1,
    count: int = 10,
) -> tuple:
    """Fetch one page of album articles.

    Returns (articles, is_complete, next_msgid, next_itemidx, album_name).
    Each article dict: {title, url, create_time, pos_num, cover_img_wxid, ...}
    """
    data = await page.evaluate(_ALBUM_LIST_JS, {
        "album_id": album_id,
        "biz": biz,
        "begin_msgid": begin_msgid,
        "begin_itemidx": begin_itemidx,
        "count": count,
    })

    if not data or data.get("error"):
        err = data.get("error", "no response") if data else "no response"
        logger.error(f"[mpweixin-zhuanji] getalbum failed: {err}")
        return [], True, "", 1, ""

    # getalbum returns base_resp.ret = 0 on success
    ret = data.get("base_resp", {}).get("ret", -1)
    if ret != 0:
        logger.error(f"[mpweixin-zhuanji] getalbum ret={ret}")
        return [], True, "", 1, ""

    album_name = data.get("album_resp_info", {}).get("title", "")
    article_list = data.get("getalbum_resp", {}).get("article_list", [])
    is_continue = data.get("getalbum_resp", {}).get("continue_flag", 0)

    articles = []
    next_msgid = ""
    next_itemidx = 1

    for art in article_list:
        articles.append({
            "title": art.get("title", ""),
            "url": art.get("url", ""),
            "create_time": art.get("create_time", 0),
            "cover": art.get("cover_img_1_1", "") or art.get("cover_img_wxid", ""),
            "pos_num": art.get("pos_num", 0),
        })
        # Track last item for next page cursor
        next_msgid = str(art.get("msgid", ""))
        next_itemidx = art.get("itemidx", 1)

    is_complete = is_continue == 0 or len(article_list) == 0
    return articles, is_complete, next_msgid, next_itemidx, album_name


# ---------------------------------------------------------------------------
# Main batch fetch
# ---------------------------------------------------------------------------

async def fetch_album_articles(
    url: str,
    since: str = "",
    delay: float = 3.0,
) -> Dict[str, Any]:
    """Fetch all articles from a WeChat album.

    Args:
        url: Album URL (mp.weixin.qq.com/mp/appmsgalbum?...)
        since: Only fetch articles after this date (YYYY-MM-DD), empty = all
        delay: Seconds between article fetches

    Returns:
        Dict with album_name, total, fetched, skipped, failed counts.
    """
    from feedgrab.fetchers.browser import (
        get_async_playwright, stealth_launch, get_stealth_context_options,
        setup_resource_blocking, generate_referer,
        evaluate_wechat_article, fetch_wechat_comments,
    )
    from feedgrab.fetchers.wechat_search import _html_to_markdown
    from feedgrab.schema import from_wechat
    from feedgrab.utils.storage import save_to_markdown
    from feedgrab.config import mpweixin_fetch_comments, mpweixin_max_comments

    _fetch_comments = mpweixin_fetch_comments()
    _max_comments = mpweixin_max_comments()

    album_info = parse_album_url(url)
    album_id = album_info["album_id"]
    biz = album_info["biz"]

    since_ts = 0
    if since:
        try:
            since_ts = int(datetime.strptime(since, "%Y-%m-%d").timestamp())
        except ValueError:
            logger.warning(f"[mpweixin-zhuanji] Invalid since date: {since}, ignoring")

    # Load dedup + progress
    dedup_index = load_index("mpweixin")
    progress = _load_progress(album_id)
    resume_msgid = progress.get("next_msgid", "")
    resume_itemidx = progress.get("next_itemidx", 1)
    if resume_msgid:
        logger.info(f"[mpweixin-zhuanji] Resuming from msgid={resume_msgid}")

    result = {
        "album_name": progress.get("album_name", ""),
        "total": 0,
        "fetched": progress.get("fetched", 0),
        "skipped": progress.get("skipped", 0),
        "failed": progress.get("failed", 0),
        "articles": [],
    }

    # Try without session first; if the page needs auth, use wechat.json
    session_path = get_session_dir() / "wechat.json"
    ctx_opts = get_stealth_context_options()
    if session_path.exists():
        ctx_opts["storage_state"] = str(session_path)

    async_pw = get_async_playwright()
    async with async_pw() as p:
        browser = await stealth_launch(p, headless=True)
        context = await browser.new_context(**ctx_opts)
        page = await context.new_page()
        await setup_resource_blocking(page)

        try:
            # Navigate to album page (sets cookies/referer context)
            logger.info(f"[mpweixin-zhuanji] Loading album page...")
            await page.goto(
                url, wait_until="domcontentloaded", timeout=30_000,
            )
            await page.wait_for_timeout(2000)

            # Paginate album articles
            begin_msgid = resume_msgid
            begin_itemidx = resume_itemidx
            page_size = 10
            date_cutoff_reached = False
            album_name = result["album_name"]

            while not date_cutoff_reached:
                logger.info(
                    f"[mpweixin-zhuanji] Fetching page (msgid={begin_msgid or 'start'})"
                )
                articles, is_complete, next_msgid, next_itemidx, resp_album_name = (
                    await _fetch_album_page(
                        page, album_id, biz=biz,
                        begin_msgid=begin_msgid, begin_itemidx=begin_itemidx,
                        count=page_size,
                    )
                )

                if resp_album_name and not album_name:
                    album_name = resp_album_name
                    result["album_name"] = album_name
                    logger.info(f"[mpweixin-zhuanji] Album: {album_name}")

                result["total"] += len(articles)

                if not articles:
                    break

                for art in articles:
                    create_time = art.get("create_time", 0)
                    title = art.get("title", "untitled")
                    link = art.get("url", "")

                    # Date filter
                    if since_ts and create_time and create_time < since_ts:
                        logger.info(
                            f"[mpweixin-zhuanji] Reached date cutoff at "
                            f"{datetime.fromtimestamp(create_time).strftime('%Y-%m-%d')}"
                        )
                        date_cutoff_reached = True
                        break

                    if not link:
                        logger.warning(f"[mpweixin-zhuanji] No URL for: {title[:40]}")
                        result["failed"] += 1
                        continue

                    # Dedup
                    item_id = item_id_from_url(link)
                    if item_id and has_item(item_id, dedup_index):
                        logger.debug(f"[mpweixin-zhuanji] Skip (dedup): {title[:40]}")
                        result["skipped"] += 1
                        continue

                    # Fetch full article
                    seq = result["fetched"] + result["skipped"] + result["failed"] + 1
                    logger.info(f"[mpweixin-zhuanji] [{seq}] {title[:50]}")

                    try:
                        art_page = await context.new_page()
                        await setup_resource_blocking(art_page)
                        await art_page.goto(
                            link, wait_until="domcontentloaded", timeout=30_000,
                            referer=generate_referer(link),
                        )
                        art_data = await evaluate_wechat_article(
                            art_page, md_converter=_html_to_markdown,
                        )

                        # Placeholder page (deleted / violation / privacy / risk
                        # control) — account for it instead of saving a shell.
                        reason = art_data.get("unavailable_reason", "")
                        if reason:
                            await art_page.close()
                            from feedgrab.fetchers.mpweixin_account import _record_unavailable
                            _record_unavailable(
                                reason, title, link, item_id, result, dedup_index,
                                log_prefix="mpweixin-zhuanji",
                            )
                        else:
                            # Fetch comments before closing page
                            if _fetch_comments and art_data.get("comment_id"):
                                cmt = await fetch_wechat_comments(
                                    art_page, art_data["comment_id"],
                                    appmsg_token=art_data.get("appmsg_token", ""),
                                    max_comments=_max_comments,
                                )
                                if cmt:
                                    art_data["comment_list"] = cmt

                            await art_page.close()

                            # Fallback metadata from album API
                            if not art_data.get("title"):
                                art_data["title"] = title
                            if not art_data.get("cover_image"):
                                art_data["cover_image"] = art.get("cover", "")

                            # Save
                            safe_album = album_name or album_id
                            item = from_wechat(art_data)
                            item.category = f"zhuanji/{safe_album}"
                            saved_path = save_to_markdown(item)

                            # Download media if enabled
                            if saved_path and (item.extra.get("videos") or item.extra.get("images")):
                                from feedgrab.config import mpweixin_download_media
                                if mpweixin_download_media():
                                    from feedgrab.utils.media import download_media
                                    download_media(
                                        saved_path,
                                        item.extra.get("images", []),
                                        item.extra.get("videos", []),
                                        item.id,
                                        platform="wechat",
                                    )

                            if item_id:
                                add_item(item_id, link, dedup_index)

                            result["fetched"] += 1
                            result["articles"].append({
                                "title": art_data.get("title", ""),
                                "publish_date": art_data.get("publish_date", ""),
                                "url": link,
                            })
                    except Exception as e:
                        logger.error(f"[mpweixin-zhuanji] Failed: {title[:40]} — {e}")
                        result["failed"] += 1

                    # Save progress after each article
                    _save_progress(album_id, {
                        "album_name": album_name,
                        "next_msgid": next_msgid,
                        "next_itemidx": next_itemidx,
                        "fetched": result["fetched"],
                        "skipped": result["skipped"],
                        "failed": result["failed"],
                    })

                    if delay > 0:
                        await asyncio.sleep(delay)

                if is_complete or date_cutoff_reached:
                    break

                begin_msgid = next_msgid
                begin_itemidx = next_itemidx
                await asyncio.sleep(1)

        finally:
            save_index(dedup_index, "mpweixin")
            if result["fetched"] > 0 or not resume_msgid:
                _clear_progress(album_id)
            await context.close()
            await browser.close()

    return result
