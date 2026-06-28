# -*- coding: utf-8 -*-
"""
Xiaohongshu (RED) Search Notes batch fetcher — fetch notes from search results.

Supports:
    feedgrab "https://www.xiaohongshu.com/search_result?keyword=开学第一课&source=..."

Strategy (tiered, with fallback):
    Tier API — Pure HTTP via xhshow signing (fastest, sort/type filters)
    Tier 0  — Extract notes from __INITIAL_STATE__.search.feeds (~40 notes)
    Tier 1  — Inject XHR interceptor + auto-scroll to capture search/notes API
    Tier 2  — Navigate to each note detail page for full content extraction
"""

import csv
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple
from urllib.parse import urlparse, parse_qs, unquote

from loguru import logger

from feedgrab.config import (
    xhs_search_max_scrolls,
    xhs_search_delay,
)
from feedgrab.utils.dedup import (
    load_index,
    save_index,
    has_item,
    add_item,
    item_id_from_url,
    get_index_path,
)


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def _parse_search_url(url: str) -> str:
    """Extract decoded keyword from XHS search URL.

    Examples:
        https://www.xiaohongshu.com/search_result?keyword=%E5%BC%80%E5%AD%A6%E7%AC%AC%E4%B8%80%E8%AF%BE
        → "开学第一课"
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    keyword_list = params.get("keyword", [])
    if not keyword_list:
        raise ValueError(f"搜索 URL 中未找到 keyword 参数: {url}")
    # URL may be double-encoded (%25xx), decode until stable
    keyword = keyword_list[0]
    for _ in range(3):
        decoded = unquote(keyword)
        if decoded == keyword:
            break
        keyword = decoded
    return keyword.strip()


def _build_note_url(note_id: str, xsec_token: str) -> str:
    """Construct a full XHS note URL with xsec_token for access."""
    base = f"https://www.xiaohongshu.com/explore/{note_id}"
    if xsec_token:
        return f"{base}?xsec_token={xsec_token}&xsec_source=pc_search"
    return base


# ---------------------------------------------------------------------------
# Tier 0 — Extract from __INITIAL_STATE__ (search.feeds)
# ---------------------------------------------------------------------------

XHS_SEARCH_INITIAL_STATE_JS = """() => {
    const state = window.__INITIAL_STATE__;
    if (!state || !state.search) return { found: false };

    // Keyword from searchContext
    let keyword = '';
    try {
        const ctx = state.search.searchContext;
        const raw = ctx._rawValue || ctx._value || ctx;
        keyword = raw.keyword || '';
    } catch(e) {}

    // Notes from search.feeds
    const feedsRef = state.search.feeds;
    if (!feedsRef) return { found: true, keyword, notes: [] };

    const rawFeeds = feedsRef._rawValue || feedsRef._value || feedsRef;
    if (!Array.isArray(rawFeeds)) return { found: true, keyword, notes: [] };

    const notes = [];
    for (const item of rawFeeds) {
        if (item.modelType !== 'note') continue;
        const nc = item.noteCard || {};
        const user = nc.user || {};
        const interact = nc.interactInfo || {};

        notes.push({
            noteId: item.id || '',
            xsecToken: item.xsecToken || '',
            displayTitle: nc.displayTitle || '',
            type: nc.type || '',
            nickname: user.nickname || '',
            userId: user.userId || '',
            likedCount: interact.likedCount || 0,
        });
    }

    return { found: true, keyword, notes };
}"""


async def _extract_search_initial_state(page) -> Tuple[str, List[Dict]]:
    """Tier 0: Extract notes from search page __INITIAL_STATE__.

    Returns:
        (keyword, note_items) where each note_item has
        noteId, xsecToken, displayTitle, type, nickname, userId, likedCount.
    """
    data = await page.evaluate(XHS_SEARCH_INITIAL_STATE_JS)
    if not data.get("found"):
        return "", []

    keyword = data.get("keyword", "")
    notes = data.get("notes", [])
    notes = [n for n in notes if n.get("noteId")]
    return keyword, notes


# ---------------------------------------------------------------------------
# Tier 1 — XHR interceptor + auto-scroll for search pagination
# ---------------------------------------------------------------------------

XHS_SEARCH_XHR_INTERCEPTOR_JS = """() => {
    if (window.__xhs_search_intercepted) return;
    window.__xhs_search_intercepted = true;
    window.__xhs_search_intercepted_notes = [];

    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function() {
        const url = arguments[1] || '';
        this.addEventListener('readystatechange', function() {
            if (this.readyState === 4 && url.includes('/api/sns/web/v1/search/notes')) {
                try {
                    const resp = JSON.parse(this.responseText);
                    if (resp.code === 0 && resp.data && resp.data.items) {
                        for (const item of resp.data.items) {
                            if (item.model_type !== 'note') continue;
                            const nc = item.note_card || {};
                            const user = nc.user || {};
                            const interact = nc.interact_info || {};

                            window.__xhs_search_intercepted_notes.push({
                                noteId: item.id || '',
                                xsecToken: item.xsec_token || '',
                                displayTitle: nc.display_title || '',
                                type: nc.type || '',
                                nickname: user.nickname || '',
                                userId: user.user_id || '',
                                likedCount: interact.liked_count || 0,
                            });
                        }
                    }
                } catch(e) {}
            }
        });
        return origOpen.apply(this, arguments);
    };
}"""

XHS_SEARCH_COLLECT_INTERCEPTED_JS = """() => {
    const notes = window.__xhs_search_intercepted_notes || [];
    window.__xhs_search_intercepted_notes = [];
    return notes;
}"""


async def _scroll_and_collect_search(
    page, initial_notes: List[Dict], max_scrolls: int
) -> List[Dict]:
    """Tier 1: Auto-scroll search results with XHR interception.

    Returns:
        Combined list of note items (deduped by noteId).
    """
    await page.evaluate(XHS_SEARCH_XHR_INTERCEPTOR_JS)

    all_notes: Dict[str, Dict] = {}
    for n in initial_notes:
        all_notes[n["noteId"]] = n

    no_new_count = 0

    for scroll_idx in range(max_scrolls):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        await page.wait_for_timeout(2000)

        new_notes = await page.evaluate(XHS_SEARCH_COLLECT_INTERCEPTED_JS)
        added = 0
        for n in new_notes:
            nid = n.get("noteId", "")
            if nid and nid not in all_notes:
                all_notes[nid] = n
                added += 1

        if added == 0:
            no_new_count += 1
            if no_new_count >= 3:
                logger.info(
                    f"[XHS-Search] 连续 {no_new_count} 次滚动无新笔记，停止"
                )
                break
        else:
            no_new_count = 0
            logger.info(
                f"[XHS-Search] 滚动 {scroll_idx + 1}: "
                f"新增 {added} 篇，累计 {len(all_notes)} 篇"
            )

    # Preserve order: initial first, then scrolled
    ordered = []
    seen = set()
    for n in initial_notes:
        nid = n["noteId"]
        if nid not in seen:
            seen.add(nid)
            ordered.append(all_notes.get(nid, n))
    for nid, n in all_notes.items():
        if nid not in seen:
            seen.add(nid)
            ordered.append(n)

    return ordered


# ---------------------------------------------------------------------------
# Batch record persistence
# ---------------------------------------------------------------------------

def _get_record_dir() -> Path:
    """Return the XHS index directory for batch records."""
    index_dir = get_index_path(platform="XHS").parent
    index_dir.mkdir(parents=True, exist_ok=True)
    return index_dir


def _save_batch_record(note_list: list, keyword: str) -> Path:
    """Save batch record to a JSON file in the XHS index/ directory."""
    out_dir = _get_record_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    safe_keyword = re.sub(r'[\\/:*?"<>|]', '_', keyword or "unknown")
    filename = f"search_{safe_keyword}_all_{ts}.json"

    path = out_dir / filename
    payload = {
        "fetched_at": datetime.now().isoformat(),
        "keyword": keyword,
        "total": len(note_list),
        "notes": note_list,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"[XHS-Search] 批量记录已保存: {path}")
    return path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fetch_search_notes(search_url: str) -> dict:
    """Batch-fetch notes from XHS search results and save each as Markdown.

    Args:
        search_url: XHS search URL with keyword parameter.

    Returns:
        dict with keys: total, fetched, skipped, failed, list_path, keyword
    """
    from feedgrab.schema import from_xiaohongshu
    from feedgrab.utils.storage import save_to_markdown, _parse_xhs_date

    # 1. Parse keyword from URL
    keyword = _parse_search_url(search_url)
    logger.info(f"[XHS-Search] 搜索关键词: {keyword}")

    # 2. Config
    delay = xhs_search_delay()

    # 3. Load dedup index
    saved_ids = load_index(platform="XHS")
    initial_count = len(saved_ids)
    logger.info(f"[XHS-Search] 已有 {initial_count} 条笔记索引")

    # === Tier API: Pure HTTP via xhshow signing ===
    try:
        from feedgrab.config import xhs_api_enabled

        if xhs_api_enabled():
            result = await _fetch_search_notes_via_api(
                keyword, saved_ids, initial_count, delay,
                from_xiaohongshu, save_to_markdown, _parse_xhs_date,
            )
            if result is not None:
                return result
    except Exception as e:
        logger.warning(f"[XHS-Search] API 模式失败 ({e})，降级到 Pinia/浏览器模式")

    # === Tier 0.5: Pinia Store search (browser-native fallback) ===
    try:
        from feedgrab.config import xhs_pinia_enabled

        if xhs_pinia_enabled():
            logger.info(f"[XHS-Search] Tier 0.5 — Pinia Store 搜索: {keyword}")
            from feedgrab.fetchers.xhs_pinia import pinia_search_notes as _pinia_search

            pinia_items = await _pinia_search(keyword)
            if pinia_items:
                logger.info(f"[XHS-Search] Pinia 搜索到 {len(pinia_items)} 条结果")
                # Process items using the same logic as API path
                result = await _process_pinia_search_results(
                    pinia_items, keyword, saved_ids, initial_count, delay,
                    from_xiaohongshu, save_to_markdown, _parse_xhs_date,
                )
                if result is not None:
                    return result
    except Exception as e:
        logger.warning(f"[XHS-Search] Pinia 搜索失败 ({e})，降级到浏览器模式")

    # === Browser fallback (original Tier 0/1/2) ===
    return await _fetch_search_notes_via_browser(
        search_url, keyword, saved_ids, initial_count, delay,
        from_xiaohongshu, save_to_markdown, _parse_xhs_date,
    )


async def _process_pinia_search_results(
    pinia_items, keyword, saved_ids, initial_count, delay,
    from_xiaohongshu, save_to_markdown, _parse_xhs_date,
) -> dict | None:
    """Process search results from Pinia Store injection.

    Pinia items are already normalized (same format as normalize_search_item).
    Saves each note with search-level data (no per-note feed_note call).

    Returns result dict on success, None if no valid items.
    """
    total = len(pinia_items)
    safe_keyword = re.sub(r'[\\/:*?"<>|]', '_', keyword)
    subfolder = f"search_{safe_keyword}"

    fetched = 0
    skipped = 0
    failed = 0
    note_list = []

    for idx, data in enumerate(pinia_items):
        note_url = data.get("url", "")
        if not note_url:
            continue
        item_id = item_id_from_url(note_url.split("?")[0])

        # Dedup check
        if has_item(item_id, saved_ids):
            skipped += 1
            note_list.append({
                "url": note_url, "item_id": item_id,
                "title": data.get("title", ""),
                "status": "skipped", "error": "已存在",
            })
            continue

        try:
            data["platform"] = "xhs"
            if not data.get("date"):
                data["date"] = datetime.now().strftime("%Y-%m-%d")

            note_date = _parse_xhs_date(data.get("date", ""))

            content = from_xiaohongshu(data)
            content.category = subfolder
            saved_path = save_to_markdown(content)

            # Media download
            if saved_path:
                from feedgrab.config import xhs_download_media
                if xhs_download_media():
                    from feedgrab.utils.media import download_media
                    download_media(
                        saved_path,
                        content.extra.get("images", []),
                        content.extra.get("videos", []),
                        content.id,
                        platform="xhs",
                    )

            add_item(item_id, note_url.split("?")[0], saved_ids)
            fetched += 1

            note_title = data.get("title") or data.get("content", "")[:30]
            note_list.append({
                "url": note_url.split("?")[0], "item_id": item_id,
                "author": data.get("author", ""),
                "date": note_date,
                "title": note_title[:80],
                "status": "fetched", "error": "",
            })

            if (idx + 1) % 10 == 0 or idx + 1 == total:
                logger.info(
                    f"[XHS-Search] Pinia 进度 [{idx+1}/{total}] "
                    f"成功:{fetched} 跳过:{skipped} 失败:{failed}"
                )

        except Exception as e:
            logger.warning(
                f"[XHS-Search] [{idx+1}/{total}] Pinia 保存失败: "
                f"{data.get('title', '')[:30]} - {str(e)[:80]}"
            )
            failed += 1
            note_list.append({
                "url": note_url, "item_id": item_id,
                "title": data.get("title", ""),
                "status": "failed", "error": str(e)[:200],
            })

    if fetched == 0 and skipped == 0:
        return None

    # Persist index
    save_index(saved_ids, platform="XHS")
    logger.info(f"[XHS-Search] 索引更新: {initial_count} -> {len(saved_ids)} 条")

    list_path = _save_batch_record(note_list, keyword)

    logger.info(
        f"[XHS-Search] Pinia 搜索批量完成: "
        f"总计 {total}, 成功 {fetched}, 跳过 {skipped}, 失败 {failed}"
    )

    return {
        "total": total,
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "list_path": str(list_path),
        "keyword": keyword,
    }


async def _fetch_search_notes_via_api(
    keyword, saved_ids, initial_count, delay,
    from_xiaohongshu, save_to_markdown, _parse_xhs_date,
) -> dict | None:
    """Try fetching search results via pure API (no browser).

    Returns result dict on success, None to signal browser fallback.
    """
    from feedgrab.fetchers.xhs_api import (
        is_api_available, get_client, normalize_api_note, normalize_search_item,
    )
    from feedgrab.config import xhs_search_sort, xhs_search_note_type, xhs_search_max_pages

    if not is_api_available():
        logger.debug("[XHS-Search] API 不可用，跳过")
        return None

    # Map note_type string to API integer
    type_map = {"all": 0, "video": 1, "image": 2}
    note_type_int = type_map.get(xhs_search_note_type(), 0)
    sort = xhs_search_sort()
    max_pages = xhs_search_max_pages()

    logger.info(
        f"[XHS-Search] Tier API — 纯 HTTP 模式 "
        f"(sort={sort}, type={xhs_search_note_type()}, max_pages={max_pages})"
    )

    with get_client() as client:
        # Step 1: Get search results via API pagination
        all_items = client.get_all_search_notes(
            keyword, sort=sort, note_type=note_type_int, max_pages=max_pages,
        )

        if not all_items:
            logger.warning("[XHS-Search] API 搜索返回空结果，降级到浏览器")
            return None

        total = len(all_items)
        logger.info(f"[XHS-Search] API 收集 {total} 篇搜索结果")

        # Subfolder
        safe_keyword = re.sub(r'[\\/:*?"<>|]', '_', keyword)
        subfolder = f"search_{safe_keyword}"

        # Step 2: Fetch each note via Feed API for full content
        fetched = 0
        skipped = 0
        failed = 0
        note_list = []

        for idx, item in enumerate(all_items):
            search_data = normalize_search_item(item)
            note_id = (
                item.get("id")
                or (item.get("note_card") or {}).get("note_id", "")
                or item.get("note_id", "")
            )
            xsec_token = item.get("xsec_token", "")
            note_url = _build_note_url(note_id, xsec_token)
            item_id = item_id_from_url(note_url.split("?")[0])

            # Dedup check
            if has_item(item_id, saved_ids):
                skipped += 1
                note_list.append({
                    "url": note_url, "item_id": item_id,
                    "title": search_data.get("title", ""),
                    "status": "skipped", "error": "已存在",
                })
                continue

            try:
                # Fetch full note via Feed API
                data = client.feed_note(
                    note_id, xsec_token=xsec_token, xsec_source="pc_search"
                )

                if not data or not data.get("content"):
                    # Use search-level data as fallback
                    data = search_data

                data["platform"] = "xhs"
                data["url"] = note_url

                # Date fallback
                if not data.get("date"):
                    data["date"] = datetime.now().strftime("%Y-%m-%d")

                note_date = _parse_xhs_date(data.get("date", ""))

                # Convert and save
                content = from_xiaohongshu(data)
                content.category = subfolder
                saved_path = save_to_markdown(content)

                # Media download
                if saved_path:
                    from feedgrab.config import xhs_download_media
                    if xhs_download_media():
                        from feedgrab.utils.media import download_media
                        download_media(
                            saved_path,
                            content.extra.get("images", []),
                            content.extra.get("videos", []),
                            content.id,
                            platform="xhs",
                        )

                add_item(item_id, note_url.split("?")[0], saved_ids)
                fetched += 1

                note_title = data.get("title") or data.get("content", "")[:30]
                note_list.append({
                    "url": note_url.split("?")[0], "item_id": item_id,
                    "author": data.get("author", ""),
                    "date": note_date,
                    "title": note_title[:80],
                    "status": "fetched", "error": "",
                })

                if (idx + 1) % 10 == 0 or idx + 1 == total:
                    logger.info(
                        f"[XHS-Search] API 进度 [{idx+1}/{total}] "
                        f"成功:{fetched} 跳过:{skipped} 失败:{failed}"
                    )

            except Exception as e:
                logger.warning(
                    f"[XHS-Search] [{idx+1}/{total}] API 失败: "
                    f"{search_data.get('title', '')[:30]} - {str(e)[:80]}"
                )
                failed += 1
                note_list.append({
                    "url": note_url, "item_id": item_id,
                    "title": search_data.get("title", ""),
                    "status": "failed", "error": str(e)[:200],
                })

    # Persist index
    save_index(saved_ids, platform="XHS")
    logger.info(f"[XHS-Search] 索引更新: {initial_count} -> {len(saved_ids)} 条")

    list_path = _save_batch_record(note_list, keyword)

    logger.info(
        f"[XHS-Search] API 搜索批量完成: "
        f"总计 {total}, 成功 {fetched}, 跳过 {skipped}, 失败 {failed}"
    )

    return {
        "total": total,
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "list_path": str(list_path),
        "keyword": keyword,
    }


async def _fetch_search_notes_via_browser(
    search_url, keyword, saved_ids, initial_count, delay,
    from_xiaohongshu, save_to_markdown, _parse_xhs_date,
) -> dict:
    """Original browser-based Tier 0/1/2 path."""
    from feedgrab.fetchers.browser import (
        evaluate_xhs_note, get_session_path,
        get_async_playwright, stealth_launch, get_stealth_context_options,
        get_stealth_engine_name, setup_resource_blocking, generate_referer,
    )
    from feedgrab.fetchers.xhs_user_notes import _handle_captcha_or_login

    max_scrolls = xhs_search_max_scrolls()

    # Verify session exists
    session_path = get_session_path("xhs")
    if not Path(session_path).exists():
        raise RuntimeError(
            "XHS 搜索批量抓取需要登录 session。请先运行: feedgrab login xhs"
        )

    # Launch browser
    logger.info("[XHS-Search] 浏览器模式 (Tier 0/1/2)")
    async_pw = get_async_playwright()
    logger.info(f"[XHS-Search] Stealth engine: {get_stealth_engine_name()}")
    async with async_pw() as p:
        browser = await stealth_launch(p, headless=False)
        context = await browser.new_context(
            **get_stealth_context_options(storage_state=session_path)
        )
        page = await context.new_page()
        await setup_resource_blocking(context)

        try:
            # Navigate to search results page
            await page.goto(
                search_url, wait_until="domcontentloaded", timeout=30_000,
                referer=generate_referer(search_url),
            )
            await page.wait_for_timeout(5000)

            # Captcha / login check
            current_url = page.url
            if "captcha" in current_url or "login" in current_url:
                await _handle_captcha_or_login(
                    page, search_url, session_path, context
                )

            # Tier 0: Extract from __INITIAL_STATE__
            logger.info(
                "[XHS-Search] Tier 0: 从 __INITIAL_STATE__ 提取搜索结果..."
            )
            state_keyword, initial_notes = await _extract_search_initial_state(
                page
            )

            # Use keyword from state if URL parse failed
            if state_keyword and not keyword:
                keyword = state_keyword

            logger.info(
                f"[XHS-Search] Tier 0: 关键词 '{keyword}', "
                f"首页 {len(initial_notes)} 篇笔记"
            )

            # Tier 1: Scroll for more
            if max_scrolls > 0 and initial_notes:
                logger.info("[XHS-Search] Tier 1: 滚动加载更多搜索结果...")
                all_note_items = await _scroll_and_collect_search(
                    page, initial_notes, max_scrolls
                )
            else:
                all_note_items = initial_notes

            total = len(all_note_items)
            logger.info(
                f"[XHS-Search] 共收集 {total} 篇笔记 "
                f"(Tier 0: {len(initial_notes)}, "
                f"Tier 1 追加: {total - len(initial_notes)})"
            )

            if total == 0:
                return {
                    "total": 0,
                    "fetched": 0,
                    "skipped": 0,
                    "failed": 0,
                    "list_path": "",
                    "keyword": keyword,
                }

            # Determine subfolder
            safe_keyword = re.sub(r'[\\/:*?"<>|]', '_', keyword)
            subfolder = f"search_{safe_keyword}"

            # Tier 2: Process each note
            fetched = 0
            skipped = 0
            failed = 0
            note_list = []

            for idx, note_item in enumerate(all_note_items):
                note_id = note_item["noteId"]
                xsec_token = note_item.get("xsecToken", "")
                note_url = _build_note_url(note_id, xsec_token)
                item_id = item_id_from_url(note_url.split("?")[0])

                # Dedup check
                if has_item(item_id, saved_ids):
                    logger.debug(
                        f"[XHS-Search] [{idx+1}/{total}] 已存在，跳过: "
                        f"{note_item.get('displayTitle', '')[:30]}"
                    )
                    skipped += 1
                    note_list.append({
                        "url": note_url,
                        "item_id": item_id,
                        "title": note_item.get("displayTitle", ""),
                        "status": "skipped",
                        "error": "已存在",
                    })
                    continue

                try:
                    # Navigate to note detail page
                    await page.goto(
                        note_url,
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )

                    # Extract full note data
                    data = await evaluate_xhs_note(page)
                    data["platform"] = "xhs"
                    data["url"] = note_url  # keep xsec_token

                    # Fallback fields from Tier 0
                    if not data.get("title") and note_item.get("displayTitle"):
                        data["title"] = note_item["displayTitle"]
                    if not data.get("author") and note_item.get("nickname"):
                        data["author"] = note_item["nickname"]

                    # Date fallback
                    if not data.get("date"):
                        data["date"] = datetime.now().strftime("%Y-%m-%d")

                    note_date = _parse_xhs_date(data.get("date", ""))

                    # Convert to UnifiedContent and save
                    content = from_xiaohongshu(data)
                    content.category = subfolder
                    saved_path = save_to_markdown(content)

                    # Media download
                    if saved_path:
                        from feedgrab.config import xhs_download_media
                        if xhs_download_media():
                            from feedgrab.utils.media import download_media
                            download_media(
                                saved_path,
                                content.extra.get("images", []),
                                content.extra.get("videos", []),
                                content.id,
                                platform="xhs",
                            )

                    # Update dedup index
                    add_item(item_id, note_url.split("?")[0], saved_ids)
                    fetched += 1

                    note_title = (
                        data.get("title") or data.get("content", "")[:30]
                    )
                    note_list.append({
                        "url": note_url.split("?")[0],
                        "item_id": item_id,
                        "author": data.get("author", ""),
                        "date": note_date,
                        "title": note_title[:80],
                        "status": "fetched",
                        "error": "",
                    })

                    # Progress log
                    if (idx + 1) % 5 == 0 or idx + 1 == total:
                        logger.info(
                            f"[XHS-Search] 进度 [{idx+1}/{total}] "
                            f"成功:{fetched} 跳过:{skipped} 失败:{failed}"
                        )

                    # Rate limit delay
                    time.sleep(delay)

                except Exception as e:
                    error_msg = str(e)
                    logger.warning(
                        f"[XHS-Search] [{idx+1}/{total}] "
                        f"失败: {note_item.get('displayTitle', '')[:30]} "
                        f"- {error_msg[:80]}"
                    )
                    failed += 1
                    note_list.append({
                        "url": note_url,
                        "item_id": item_id,
                        "title": note_item.get("displayTitle", ""),
                        "status": "failed",
                        "error": error_msg[:200],
                    })

        finally:
            await context.close()
            await browser.close()

    # Persist dedup index
    save_index(saved_ids, platform="XHS")
    logger.info(f"[XHS-Search] 索引更新: {initial_count} -> {len(saved_ids)} 条")

    # Save batch record
    list_path = _save_batch_record(note_list, keyword)

    logger.info(
        f"[XHS-Search] 搜索批量抓取完成: "
        f"总计 {total}, 成功 {fetched}, 跳过 {skipped}, 失败 {failed}"
    )

    return {
        "total": total,
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "list_path": str(list_path),
        "keyword": keyword,
    }


# ---------------------------------------------------------------------------
# xhs-so: Keyword search with engagement summary table
# ---------------------------------------------------------------------------

_SORT_MAP = {"general": "general", "popular": "popularity_descending", "latest": "time_descending"}
_SORT_ZH = {"general": "综合", "popular": "热门", "latest": "最新"}
_TYPE_MAP = {"all": 0, "video": 1, "image": 2}


def _resolve_output_base() -> Path:
    """Resolve the base output directory (OBSIDIAN_VAULT > OUTPUT_DIR > output)."""
    vault = os.getenv("OBSIDIAN_VAULT", "").strip()
    output_dir = os.getenv("OUTPUT_DIR", "").strip()
    return Path(vault or output_dir or "output")


def _clean_summary(text: str, max_len: int = 40) -> str:
    """Clean text for summary table display."""
    text = re.sub(r'[\r\n\t]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= max_len:
        return text
    # Prefer cutting at CJK sentence-ending punctuation
    for p in ("。", "！", "？", ".", "!", "?"):
        idx = text.rfind(p, 0, max_len)
        if idx > max_len // 3:
            return text[: idx + 1]
    return text[:max_len] + "…"


def _generate_xhs_summary_table(
    keyword: str,
    sort: str,
    note_type: str,
    notes: List[dict],
    output_path: Path,
    show_keyword: bool = False,
) -> None:
    """Generate XHS summary Markdown table + CSV, sorted by likes descending.

    Args:
        show_keyword: If True, add a "关键词" column (used in merged multi-keyword mode).
    """
    sort_label = _SORT_ZH.get(sort, sort)
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Sort by likes descending
    notes.sort(key=lambda n: int(n.get("likes", 0) or 0), reverse=True)

    # Filter out empty rows (no title and no author = non-note junk)
    valid_notes = [
        nd for nd in notes
        if nd.get("title") or nd.get("content") or nd.get("author")
    ]

    # --- Markdown ---
    lines = [
        "---",
        f'title: "小红书搜索：{keyword}"',
        f'search_sort: "{sort_label}"',
        f'note_type: "{note_type}"',
        f"total: {len(valid_notes)}",
        f"created: {date_str}",
        "cssclasses: wide",
        "---",
        "",
    ]

    if not valid_notes:
        lines.append("*未找到结果。*")
    else:
        if show_keyword:
            lines.append(
                "| # | 关键词 | 作者 | 内容摘要 | 类型 | 日期 | 点赞 | 收藏 | 评论 |"
            )
            lines.append(
                "|:---:|------|------|----------|:---:|:---:|:---:|:---:|:---:|"
            )
        else:
            lines.append(
                "| # | 作者 | 内容摘要 | 类型 | 日期 | 点赞 | 收藏 | 评论 |"
            )
            lines.append(
                "|:---:|------|----------|:---:|:---:|:---:|:---:|:---:|"
            )

        for i, nd in enumerate(valid_notes, 1):
            author = nd.get("author", "").replace("|", "\\|")
            title = nd.get("title", "")
            content = nd.get("content", "")
            summary_text = _clean_summary(title or content, max_len=40)
            summary_text = summary_text.replace("|", "\\|")
            summary_text = summary_text.replace("[", "\\[").replace("]", "\\]")

            note_url = nd.get("url", "")
            summary_link = f"[{summary_text}]({note_url})" if note_url else summary_text

            ntype = "视频" if nd.get("note_type") == "video" else "图文"
            likes = int(nd.get("likes", 0) or 0)
            collects = int(nd.get("collects", 0) or 0)
            comments = int(nd.get("comments", 0) or 0)
            date_raw = nd.get("date", "")
            # Extract just the date part (strip location like "福建")
            date_short = date_raw[:10] if len(date_raw) >= 10 else (date_raw or "—")

            if show_keyword:
                kw = nd.get("_keyword", "").replace("|", "\\|")
                lines.append(
                    f"| {i} | {kw} | {author} | {summary_link} "
                    f"| {ntype} | {date_short} | {likes} | {collects} | {comments} |"
                )
            else:
                lines.append(
                    f"| {i} | {author} | {summary_link} "
                    f"| {ntype} | {date_short} | {likes} | {collects} | {comments} |"
                )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"[XHS-SO] Summary table saved: {output_path}")

    # --- CSV ---
    csv_path = output_path.with_suffix(".csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if show_keyword:
            writer.writerow([
                "#", "关键词", "作者", "内容摘要", "类型", "日期", "点赞", "收藏",
                "评论", "链接",
            ])
        else:
            writer.writerow([
                "#", "作者", "内容摘要", "类型", "日期", "点赞", "收藏",
                "评论", "链接",
            ])
        for i, nd in enumerate(valid_notes, 1):
            author = nd.get("author", "")
            title = nd.get("title", "")
            content = nd.get("content", "")
            summary_text = _clean_summary(title or content, max_len=80)
            ntype = "视频" if nd.get("note_type") == "video" else "图文"
            likes = int(nd.get("likes", 0) or 0)
            collects = int(nd.get("collects", 0) or 0)
            comments = int(nd.get("comments", 0) or 0)
            date_raw = nd.get("date", "")
            date_short = date_raw[:10] if len(date_raw) >= 10 else date_raw
            note_url = nd.get("url", "")
            row = [i, author, summary_text, ntype, date_short,
                   likes, collects, comments, note_url]
            if show_keyword:
                row.insert(1, nd.get("_keyword", ""))
            writer.writerow(row)
    logger.info(f"[XHS-SO] CSV table saved: {csv_path}")


def search_xhs_keyword(
    keyword: str,
    sort: str = "general",
    note_type: str = "all",
    max_results: int = 200,
    save_notes: bool = False,
    skip_summary: bool = False,
) -> dict:
    """Search XHS for notes matching a keyword and generate engagement-ranked output.

    Uses XHS API directly (no browser needed, requires xhshow + session cookies).

    Args:
        keyword: Search keyword.
        sort: Sort mode — general / popular / latest.
        note_type: Note type filter — all / video / image.
        max_results: Maximum number of results.
        save_notes: Whether to save individual note .md files.
        skip_summary: If True, skip generating summary table (used in merge mode).

    Returns:
        dict with: total, saved, query, output_path, csv_path
    """
    from feedgrab.fetchers.xhs_api import is_api_available, get_client, normalize_search_item

    if not is_api_available():
        # Try Pinia Store fallback before giving up
        try:
            from feedgrab.config import xhs_pinia_enabled

            if xhs_pinia_enabled():
                import asyncio
                logger.info(f"[XHS-SO] API 不可用，尝试 Pinia Store 搜索")
                from feedgrab.fetchers.xhs_pinia import pinia_search_notes as _pinia_search

                loop = asyncio.get_event_loop()
                pinia_items = loop.run_until_complete(_pinia_search(keyword))
                if pinia_items:
                    logger.info(f"[XHS-SO] Pinia 搜索到 {len(pinia_items)} 条结果")
                    all_notes = pinia_items[:max_results]

                    # Optionally save individual notes
                    saved_count = 0
                    if save_notes:
                        from feedgrab.schema import from_xiaohongshu
                        from feedgrab.utils.storage import save_to_markdown

                        saved_ids_local = load_index(platform="XHS")
                        safe_kw = re.sub(r'[\\/:*?"<>|]', '_', keyword)
                        subfolder_local = f"search_{safe_kw}"

                        for idx_local, nd in enumerate(all_notes):
                            base_url = nd.get("url", "").split("?")[0]
                            if not base_url:
                                continue
                            iid = item_id_from_url(base_url)
                            if has_item(iid, saved_ids_local):
                                continue
                            try:
                                nd["platform"] = "xhs"
                                if not nd.get("date"):
                                    nd["date"] = datetime.now().strftime("%Y-%m-%d")
                                c = from_xiaohongshu(nd)
                                c.category = subfolder_local
                                sp = save_to_markdown(c)
                                if sp:
                                    from feedgrab.config import xhs_download_media
                                    if xhs_download_media():
                                        from feedgrab.utils.media import download_media
                                        download_media(
                                            sp, c.extra.get("images", []),
                                            c.extra.get("videos", []),
                                            c.id, platform="xhs",
                                        )
                                add_item(iid, base_url, saved_ids_local)
                                saved_count += 1
                            except Exception as e_local:
                                logger.warning(f"[XHS-SO] Pinia 保存失败 [{idx_local+1}]: {str(e_local)[:80]}")

                        save_index(saved_ids_local, platform="XHS")

                    # Generate summary table
                    summary_path = Path("")
                    csv_path = Path("")
                    if not skip_summary:
                        base_dir = _resolve_output_base()
                        sort_label = _SORT_ZH.get(sort, sort)
                        safe_kw2 = re.sub(r'[\\/:*?"<>|]', '_', keyword)
                        date_str = datetime.now().strftime("%Y-%m-%d")
                        summary_dir = base_dir / "XHS" / "search" / f"{sort_label}"
                        summary_path = summary_dir / f"{safe_kw2}_{date_str}.md"

                        _generate_xhs_summary_table(
                            keyword=keyword, sort=sort, note_type=note_type,
                            notes=all_notes, output_path=summary_path,
                        )
                        csv_path = summary_path.with_suffix(".csv")

                    logger.info(f"[XHS-SO] Pinia 搜索完成: {len(all_notes)} 篇笔记")
                    return {
                        "total": len(all_notes),
                        "saved": saved_count,
                        "query": keyword,
                        "output_path": str(summary_path),
                        "csv_path": str(csv_path),
                        "notes": all_notes,
                    }
        except Exception as e:
            logger.warning(f"[XHS-SO] Pinia 搜索也失败: {e}")

        raise RuntimeError(
            "XHS API 不可用。请确保:\n"
            "  1. pip install xhshow\n"
            "  2. feedgrab login xhs (获取 session cookies)"
        )

    # Map sort/type to API parameters
    api_sort = _SORT_MAP.get(sort, "general")
    api_note_type = _TYPE_MAP.get(note_type, 0)
    max_pages = max_results // 20 + 2  # ~20 items per page

    logger.info(f"[XHS-SO] 关键词: {keyword}")
    logger.info(f"[XHS-SO] 排序: {_SORT_ZH.get(sort, sort)}, 类型: {note_type}, 最大: {max_results}")

    # API pagination loop
    all_notes = []
    with get_client() as client:
        raw_items = client.get_all_search_notes(
            keyword, sort=api_sort, note_type=api_note_type, max_pages=max_pages,
        )

        if not raw_items:
            logger.warning("[XHS-SO] 搜索返回空结果")
            return {"total": 0, "saved": 0, "query": keyword, "output_path": "", "csv_path": ""}

        # Normalize all items
        for item in raw_items[:max_results]:
            nd = normalize_search_item(item)
            # Ensure URL has xsec_token for accessibility
            note_id = (
                item.get("id")
                or (item.get("note_card") or {}).get("note_id", "")
                or item.get("note_id", "")
            )
            xsec_token = item.get("xsec_token", "")
            nd["url"] = _build_note_url(note_id, xsec_token)
            all_notes.append(nd)

        # Optionally fetch full content and save individual .md files
        saved_count = 0
        if save_notes:
            from feedgrab.schema import from_xiaohongshu
            from feedgrab.utils.storage import save_to_markdown
            from feedgrab.utils.dedup import has_item, add_item, item_id_from_url

            saved_ids = load_index(platform="XHS")
            safe_keyword = re.sub(r'[\\/:*?"<>|]', '_', keyword)
            subfolder = f"search_{safe_keyword}"

            for idx, nd in enumerate(all_notes):
                note_id_raw = nd.get("url", "").split("/explore/")[-1].split("?")[0]
                base_url = nd["url"].split("?")[0]
                item_id = item_id_from_url(base_url)

                if has_item(item_id, saved_ids):
                    continue

                try:
                    xsec_token = parse_qs(urlparse(nd["url"]).query).get("xsec_token", [""])[0]
                    data = client.feed_note(note_id_raw, xsec_token=xsec_token, xsec_source="pc_search")
                    if not data or not data.get("content"):
                        data = nd.copy()
                    data["platform"] = "xhs"
                    data["url"] = nd["url"]
                    if not data.get("date"):
                        data["date"] = datetime.now().strftime("%Y-%m-%d")

                    content = from_xiaohongshu(data)
                    content.category = subfolder
                    saved_path = save_to_markdown(content)

                    # Media download
                    if saved_path:
                        from feedgrab.config import xhs_download_media
                        if xhs_download_media():
                            from feedgrab.utils.media import download_media
                            download_media(
                                saved_path,
                                content.extra.get("images", []),
                                content.extra.get("videos", []),
                                content.id,
                                platform="xhs",
                            )

                    add_item(item_id, base_url, saved_ids)
                    saved_count += 1

                    # Update summary data with full content data
                    all_notes[idx] = data

                except Exception as e:
                    logger.warning(f"[XHS-SO] 保存失败 [{idx+1}]: {str(e)[:80]}")

            save_index(saved_ids, platform="XHS")

    # Generate summary table (skip in merge mode)
    summary_path = Path("")
    csv_path = Path("")
    if not skip_summary:
        base_dir = _resolve_output_base()
        sort_label = _SORT_ZH.get(sort, sort)
        safe_keyword = re.sub(r'[\\/:*?"<>|]', '_', keyword)
        date_str = datetime.now().strftime("%Y-%m-%d")
        summary_dir = base_dir / "XHS" / "search" / f"{sort_label}"
        summary_path = summary_dir / f"{safe_keyword}_{date_str}.md"

        _generate_xhs_summary_table(
            keyword=keyword,
            sort=sort,
            note_type=note_type,
            notes=all_notes,
            output_path=summary_path,
        )
        csv_path = summary_path.with_suffix(".csv")

    logger.info(f"[XHS-SO] 搜索完成: {len(all_notes)} 篇笔记")

    return {
        "total": len(all_notes),
        "saved": saved_count,
        "query": keyword,
        "output_path": str(summary_path),
        "csv_path": str(csv_path),
        "notes": all_notes,
    }
