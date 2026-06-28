# -*- coding: utf-8 -*-
"""
XHS Pinia Store injection — browser-native data fetching.

When xhshow API signing fails (461/471), this module provides a reliable
fallback by calling Xiaohongshu's own Pinia Store actions directly inside
the browser page.  Requests are 100% identical to what a real user's browser
sends — signatures, cookies, TLS fingerprint are all genuine.

Architecture (dual-channel, ported from bb-sites):
  1. Monkey-patch XMLHttpRequest to intercept raw JSON responses
  2. Call Pinia Store Action (triggers Vue → internal XHR)
  3. Capture the raw response from the interceptor
  4. Restore original XHR methods in a finally block

Browser management:
  - CDP first: reuse running Chrome with XHS cookies (zero startup cost)
  - Launch fallback: stealth_launch + sessions/xhs.json + navigate to explore
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# JS snippets
# ---------------------------------------------------------------------------

PINIA_CHECK_JS = """
() => {
    const app = document.querySelector('#app')?.__vue_app__;
    const pinia = app?.config?.globalProperties?.$pinia;
    return {
        available: !!pinia?._s,
        stores: pinia?._s ? Array.from(pinia._s.keys()) : []
    };
}
"""

# --- Single note detail ---
PINIA_FEED_NOTE_JS = """
async (noteId) => {
    const app = document.querySelector('#app')?.__vue_app__;
    const pinia = app?.config?.globalProperties?.$pinia;
    if (!pinia?._s) return {ok: false, error: 'Pinia not ready'};

    const noteStore = pinia._s.get('note');
    if (!noteStore) return {ok: false, error: 'note store not found'};

    let captured = null;
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(m, u) {
        this.__url = u;
        return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function(body) {
        if (this.__url && this.__url.includes('/api/sns/web/v1/feed')
            && body && typeof body === 'string' && body.includes(noteId)) {
            const xhr = this;
            const origCb = xhr.onreadystatechange;
            xhr.onreadystatechange = function() {
                if (xhr.readyState === 4 && !captured) {
                    try { captured = JSON.parse(xhr.responseText); } catch {}
                }
                if (origCb) origCb.apply(this, arguments);
            };
        }
        return origSend.apply(this, arguments);
    };

    try {
        await noteStore.getNoteDetailByNoteId(noteId);
        // Small wait for XHR to complete
        await new Promise(r => setTimeout(r, 800));
    } finally {
        XMLHttpRequest.prototype.open = origOpen;
        XMLHttpRequest.prototype.send = origSend;
    }

    if (!captured) return {ok: false, error: 'XHR not captured'};
    return {ok: true, data: captured};
}
"""

# --- Search notes ---
PINIA_SEARCH_JS = """
async (keyword) => {
    const app = document.querySelector('#app')?.__vue_app__;
    const pinia = app?.config?.globalProperties?.$pinia;
    if (!pinia?._s) return {ok: false, error: 'Pinia not ready'};

    const searchStore = pinia._s.get('search');
    if (!searchStore) return {ok: false, error: 'search store not found'};

    let captured = null;
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(m, u) {
        this.__url = u;
        return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function(body) {
        if (this.__url && this.__url.includes('/api/sns/web/v1/search/notes')) {
            const xhr = this;
            const origCb = xhr.onreadystatechange;
            xhr.onreadystatechange = function() {
                if (xhr.readyState === 4 && !captured) {
                    try { captured = JSON.parse(xhr.responseText); } catch {}
                }
                if (origCb) origCb.apply(this, arguments);
            };
        }
        return origSend.apply(this, arguments);
    };

    try {
        searchStore.mutateSearchValue(keyword);
        await searchStore.loadMore();
        await new Promise(r => setTimeout(r, 800));
    } finally {
        XMLHttpRequest.prototype.open = origOpen;
        XMLHttpRequest.prototype.send = origSend;
    }

    if (!captured) return {ok: false, error: 'XHR not captured'};
    return {ok: true, data: captured};
}
"""

# --- User posted notes ---
PINIA_USER_NOTES_JS = """
async (userId) => {
    const app = document.querySelector('#app')?.__vue_app__;
    const pinia = app?.config?.globalProperties?.$pinia;
    if (!pinia?._s) return {ok: false, error: 'Pinia not ready'};

    const userStore = pinia._s.get('user');
    if (!userStore) return {ok: false, error: 'user store not found'};

    let captured = null;
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(m, u) {
        this.__url = u;
        return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function(body) {
        if (this.__url && this.__url.includes('/api/sns/web/v1/user_posted')) {
            const xhr = this;
            const origCb = xhr.onreadystatechange;
            xhr.onreadystatechange = function() {
                if (xhr.readyState === 4 && !captured) {
                    try { captured = JSON.parse(xhr.responseText); } catch {}
                }
                if (origCb) origCb.apply(this, arguments);
            };
        }
        return origSend.apply(this, arguments);
    };

    try {
        await userStore.fetchNotes({userId});
        await new Promise(r => setTimeout(r, 800));
    } finally {
        XMLHttpRequest.prototype.open = origOpen;
        XMLHttpRequest.prototype.send = origSend;
    }

    if (!captured) return {ok: false, error: 'XHR not captured'};
    return {ok: true, data: captured};
}
"""


# ---------------------------------------------------------------------------
# Browser management
# ---------------------------------------------------------------------------

async def _connect_xhs_cdp():
    """Try CDP connection to a running Chrome with XHS logged in.

    Returns ``(pw, browser, context, True)`` on success, or
    ``(None, None, None, False)`` on failure.

    ``browser.close()`` only disconnects the CDP socket — it does
    **not** kill the user's Chrome process.
    """
    from feedgrab.config import chrome_cdp_port

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None, None, None, False

    port = chrome_cdp_port()
    pw = await async_playwright().start()
    try:
        ws_url = f"ws://127.0.0.1:{port}/devtools/browser"
        browser = await pw.chromium.connect_over_cdp(ws_url)

        xhs_domains = (".xiaohongshu.com",)
        target_ctx = None
        for ctx in browser.contexts:
            cookies = await ctx.cookies()
            if any(
                any(c.get("domain", "").endswith(d) for d in xhs_domains)
                for c in cookies
            ):
                target_ctx = ctx
                break

        if not target_ctx:
            logger.debug("[XHS-Pinia] No context with XHS cookies via CDP")
            await browser.close()
            await pw.stop()
            return None, None, None, False

        logger.info(f"[XHS-Pinia] CDP 已连接，{len(await target_ctx.cookies())} 个 Cookie")
        return pw, browser, target_ctx, True

    except Exception as e:
        logger.debug(f"[XHS-Pinia] CDP 连接失败：{e}")
        try:
            await pw.stop()
        except Exception:
            pass
        return None, None, None, False


async def _get_xhs_pinia_page():
    """Get a page on xiaohongshu.com ready for Pinia injection.

    Returns ``(pw, browser, page, is_cdp)`` — caller must close.

    Strategy:
      1. CDP direct connect (zero startup cost)
      2. Launch new browser with saved session
    """
    from feedgrab.config import xhs_pinia_enabled
    if not xhs_pinia_enabled():
        return None, None, None, False

    # --- CDP first ---
    pw, browser, ctx, ok = await _connect_xhs_cdp()
    if ok:
        page = await ctx.new_page()
        # Navigate to explore to ensure Pinia is loaded
        try:
            await page.goto("https://www.xiaohongshu.com/explore",
                            wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
            return pw, browser, page, True
        except Exception as e:
            logger.warning(f"[XHS-Pinia] CDP 页面导航失败：{e}")
            try:
                await page.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await pw.stop()
            except Exception:
                pass

    # --- Launch fallback ---
    from feedgrab.fetchers.browser import get_session_path
    from pathlib import Path

    session_path = get_session_path("xhs")
    if not Path(session_path).exists():
        logger.debug("[XHS-Pinia] 未找到小红书登录态文件，无法启动浏览器")
        return None, None, None, False

    try:
        from feedgrab.fetchers.browser import (
            get_async_playwright,
            stealth_launch,
            get_stealth_context_options,
            setup_resource_blocking,
        )

        pw = await get_async_playwright()
        browser = await stealth_launch(pw, headless=False)
        ctx_opts = get_stealth_context_options()
        ctx_opts["storage_state"] = session_path
        context = await browser.new_context(**ctx_opts)
        setup_resource_blocking(context)
        page = await context.new_page()

        await page.goto("https://www.xiaohongshu.com/explore",
                        wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)
        logger.info("[XHS-Pinia] 浏览器已启动并进入 explore 页面")
        return pw, browser, page, False

    except Exception as e:
        logger.warning(f"[XHS-Pinia] 浏览器启动失败：{e}")
        try:
            if 'browser' in dir() and browser:
                await browser.close()
        except Exception:
            pass
        try:
            if 'pw' in dir() and pw:
                await pw.stop()
        except Exception:
            pass
        return None, None, None, False


async def _cleanup(pw, browser, page, is_cdp: bool):
    """Clean up browser resources."""
    try:
        if page:
            await page.close()
    except Exception:
        pass
    try:
        if browser:
            await browser.close()
    except Exception:
        pass
    if not is_cdp:
        try:
            if pw:
                await pw.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def pinia_feed_note(note_id: str) -> Optional[dict]:
    """Fetch a single XHS note via Pinia Store injection.

    Returns a normalized dict (same format as xhs_api), or None on failure.
    """
    pw, browser, page, is_cdp = await _get_xhs_pinia_page()
    if not page:
        return None

    try:
        # Check Pinia availability
        check = await page.evaluate(PINIA_CHECK_JS)
        if not check or not check.get("available"):
            logger.warning(f"[XHS-Pinia] Pinia 不可用：{check}")
            return None
        logger.debug(f"[XHS-Pinia] Stores: {check.get('stores', [])}")

        # Execute feed note
        result = await page.evaluate(PINIA_FEED_NOTE_JS, note_id)
        if not result or not result.get("ok"):
            logger.warning(f"[XHS-Pinia] 笔记接口调用失败：{result}")
            return None

        # Extract note_card from API response
        raw = result["data"]
        items = raw.get("data", {}).get("items", [])
        if not items:
            logger.warning("[XHS-Pinia] 笔记接口返回空列表")
            return None

        note_card = items[0].get("note_card")
        if not note_card:
            logger.warning("[XHS-Pinia] 首条结果中没有 note_card")
            return None

        # Normalize using existing xhs_api function
        from feedgrab.fetchers.xhs_api import normalize_api_note
        data = normalize_api_note(note_card, note_id)
        logger.info(f"[XHS-Pinia] 笔记抓取成功：{data.get('title', '')[:40]}")
        return data

    except Exception as e:
        logger.warning(f"[XHS-Pinia] 笔记抓取异常：{e}")
        return None

    finally:
        await _cleanup(pw, browser, page, is_cdp)


async def pinia_search_notes(keyword: str) -> Optional[list[dict]]:
    """Search XHS notes via Pinia Store injection.

    Returns a list of normalized search items, or None on failure.
    """
    pw, browser, page, is_cdp = await _get_xhs_pinia_page()
    if not page:
        return None

    try:
        check = await page.evaluate(PINIA_CHECK_JS)
        if not check or not check.get("available"):
            logger.warning(f"[XHS-Pinia] Pinia 不可用：{check}")
            return None

        result = await page.evaluate(PINIA_SEARCH_JS, keyword)
        if not result or not result.get("ok"):
            logger.warning(f"[XHS-Pinia] 搜索失败：{result}")
            return None

        raw = result["data"]
        items = raw.get("data", {}).get("items", [])
        if not items:
            logger.warning("[XHS-Pinia] 搜索结果为空")
            return None

        from feedgrab.fetchers.xhs_api import normalize_search_item
        results = []
        for item in items:
            try:
                note_card = item.get("note_card") or item.get("model_type") and item
                if note_card:
                    results.append(normalize_search_item(item))
            except Exception:
                continue

        logger.info(f"[XHS-Pinia] 搜索 '{keyword}'：{len(results)} 条结果")
        return results if results else None

    except Exception as e:
        logger.warning(f"[XHS-Pinia] 搜索异常：{e}")
        return None

    finally:
        await _cleanup(pw, browser, page, is_cdp)


async def pinia_user_notes(user_id: str) -> Optional[list[dict]]:
    """Fetch user's note list via Pinia Store injection.

    Returns a list of normalized user note items, or None on failure.
    """
    pw, browser, page, is_cdp = await _get_xhs_pinia_page()
    if not page:
        return None

    try:
        check = await page.evaluate(PINIA_CHECK_JS)
        if not check or not check.get("available"):
            logger.warning(f"[XHS-Pinia] Pinia 不可用：{check}")
            return None

        result = await page.evaluate(PINIA_USER_NOTES_JS, user_id)
        if not result or not result.get("ok"):
            logger.warning(f"[XHS-Pinia] 用户笔记接口调用失败：{result}")
            return None

        raw = result["data"]
        notes = raw.get("data", {}).get("notes", [])
        if not notes:
            logger.warning("[XHS-Pinia] 用户笔记为空")
            return None

        from feedgrab.fetchers.xhs_api import normalize_user_note_item
        results = []
        for item in notes:
            try:
                results.append(normalize_user_note_item(item))
            except Exception:
                continue

        logger.info(f"[XHS-Pinia] 用户 {user_id}：{len(results)} 篇笔记")
        return results if results else None

    except Exception as e:
        logger.warning(f"[XHS-Pinia] 用户笔记抓取异常：{e}")
        return None

    finally:
        await _cleanup(pw, browser, page, is_cdp)
