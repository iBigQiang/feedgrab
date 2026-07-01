# -*- coding: utf-8 -*-
"""FlowUs (息流 flowus.cn) fetcher.

FlowUs 是一个 Notion 风格的协作文档平台。整篇文档由扁平化的 block 字典
（uuid → block）组成，按 root.subNodes 顺序递归遍历即可还原文档树。

抓取链路：
  Tier 0: 纯 HTTP API（已保存 cookie 时）— 直接 GET /api/docs/{uuid}
  Tier 1: Playwright + CDP — 首次复用本机 Chrome 提取 cookie 并保存
  Tier 2: Playwright launch + saved session — 浏览器内 fetch 验证
  Tier 3: Jina Reader 兜底

URL 形态：
  https://flowus.cn/share/{uuid}?code={code}
  https://flowus.cn/{username}/{uuid}
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from loguru import logger


# ---------------------------------------------------------------------------
# URL detection & parsing
# ---------------------------------------------------------------------------

_FLOWUS_DOMAINS = ("flowus.cn",)
_UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def is_flowus_url(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    return any(netloc == d or netloc.endswith("." + d) for d in _FLOWUS_DOMAINS)


def parse_flowus_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (doc_uuid, share_code). share_code is None for non-share URLs."""
    parsed = urlparse(url)
    m = _UUID_RE.search(parsed.path)
    doc_uuid = m.group(1).lower() if m else None

    share_code: Optional[str] = None
    if parsed.query:
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        if qs.get("code"):
            share_code = qs["code"][0]
    return doc_uuid, share_code


# ---------------------------------------------------------------------------
# API constants
# ---------------------------------------------------------------------------

_API_HEADERS = {
    "x-platform": "web-cookie",
    "x-app-origin": "web",
    "x-product": "flowus",
    "app_version_name": "1.146.0",
    "accept": "application/json, text/plain, */*",
}


def _build_headers(referer: str) -> Dict[str, str]:
    from feedgrab.config import get_user_agent
    h = dict(_API_HEADERS)
    h["referer"] = referer
    h["user-agent"] = get_user_agent()
    return h


# ---------------------------------------------------------------------------
# Cookie loading from saved session
# ---------------------------------------------------------------------------

def _load_flowus_cookies() -> Dict[str, str]:
    """Load cookies dict from sessions/flowus.json (Playwright storage_state)."""
    from feedgrab.config import get_session_dir
    session_path = get_session_dir() / "flowus.json"
    if not session_path.exists():
        return {}
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"[flowus] cookie load failed: {e}")
        return {}
    out: Dict[str, str] = {}
    for c in data.get("cookies", []):
        name = c.get("name")
        value = c.get("value")
        if name and value:
            out[name] = value
    return out


# ---------------------------------------------------------------------------
# Tier 0: HTTP API
# ---------------------------------------------------------------------------

def _api_get(path: str, referer: str, cookies: Dict[str, str], timeout: int = 15) -> Optional[dict]:
    """GET https://flowus.cn{path}. Returns parsed JSON or None on failure."""
    from feedgrab.utils.http_client import get as http_get
    url = f"https://flowus.cn{path}"
    try:
        resp = http_get(url, headers=_build_headers(referer), cookies=cookies, timeout=timeout)
    except Exception as e:
        logger.debug(f"[flowus] API request failed {path}: {e}")
        return None
    if resp.status_code != 200:
        logger.debug(f"[flowus] API {path} → HTTP {resp.status_code}")
        return None
    try:
        return resp.json()
    except Exception as e:
        logger.debug(f"[flowus] API {path} JSON parse failed: {e}")
        return None


def _fetch_via_http(url: str, doc_uuid: str, share_code: Optional[str]) -> Optional[Dict[str, Any]]:
    """Tier 0: pure HTTP.

    Tries saved cookies first (needed for private/paid docs); if none,
    still attempts a cookie-less request — public share docs work fine.
    Either way returns None on transport failure so caller falls through
    to the browser tier; returns dict with ``_error`` on API-level rejection
    so caller can pick the right next step.

    FlowUs auth uses **two** cookies together: ``next_auth`` (JWT) and
    ``next_auth.sig`` (HMAC signature). Either alone is rejected by the API.
    """
    cookies = _load_flowus_cookies()
    has_auth = bool(cookies.get("next_auth") and cookies.get("next_auth.sig"))
    if not has_auth:
        if cookies.get("next_auth") and not cookies.get("next_auth.sig"):
            logger.warning(
                "[flowus] HTTP: next_auth present but next_auth.sig missing — "
                "saved session is incomplete. Re-login or refresh CDP cookies."
            )
        else:
            logger.debug("[flowus] HTTP: no auth cookies — trying public-share path")

    doc = _api_get(f"/api/docs/{doc_uuid}", url, cookies)
    if doc is None:
        return None  # transport error → try browser
    if doc.get("code") != 200:
        msg = doc.get("msg", "")
        code = doc.get("code")
        # No-cookie + auth-required code → don't treat as terminal; let browser tier retry
        if not has_auth and code in (1407, 401, 403, 1401, 1402):
            logger.info(f"[flowus] public HTTP rejected (code={code} msg={msg}) — switching to browser")
            return None
        logger.warning(f"[flowus] API code={code} msg={msg}")
        return {"_error": code, "_msg": msg, "_doc_uuid": doc_uuid}

    public = _api_get(f"/api/docs/{doc_uuid}/publicData", url, cookies) or {}
    sharer: Dict[str, Any] = {}
    if share_code:
        sharer_resp = _api_get(
            f"/api/share/users/getUserByCode?code={share_code}", url, cookies,
        )
        if sharer_resp and sharer_resp.get("code") == 200:
            sharer = sharer_resp.get("data", {}) or {}

    return {
        "doc_data": doc.get("data", {}),
        "public_data": public.get("data", {}) if public.get("code") == 200 else {},
        "sharer": sharer,
        "_doc_uuid": doc_uuid,
        "_share_code": share_code,
    }


# ---------------------------------------------------------------------------
# Tier 1: CDP — extract cookies + fetch on the same page
# ---------------------------------------------------------------------------

async def _connect_flowus_cdp(url: str) -> Optional[tuple]:
    """Connect to running Chrome via CDP, find context with flowus.cn cookies.

    Returns (browser, context, page) and saves cookies to sessions/flowus.json.
    """
    from feedgrab.config import chrome_cdp_port, get_session_dir

    port = chrome_cdp_port()
    ws_url = f"ws://127.0.0.1:{port}/devtools/browser"

    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(ws_url)
        logger.debug(f"[flowus] CDP connected: {ws_url}")

        for ctx in browser.contexts:
            cookies = await ctx.cookies()
            has_flowus = any(
                c.get("domain", "").endswith("flowus.cn")
                or c.get("domain", "") == "flowus.cn"
                for c in cookies
            )
            if not has_flowus:
                continue

            # Persist FlowUs cookies for next-time Tier 0
            try:
                flowus_cookies = [
                    c for c in cookies
                    if c.get("domain", "").endswith("flowus.cn")
                    or c.get("domain", "") == "flowus.cn"
                ]
                has_auth = (
                    any(c.get("name") == "next_auth" for c in flowus_cookies)
                    and any(c.get("name") == "next_auth.sig" for c in flowus_cookies)
                )
                if has_auth:
                    session_path = get_session_dir() / "flowus.json"
                    session_path.parent.mkdir(parents=True, exist_ok=True)
                    storage = {"cookies": flowus_cookies, "origins": []}
                    session_path.write_text(
                        json.dumps(storage, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    logger.info(
                        f"[flowus] CDP: saved {len(flowus_cookies)} cookies → {session_path}"
                    )
                else:
                    logger.debug(
                        f"[flowus] CDP: skipping cookie save — auth cookies incomplete "
                        f"(got {len(flowus_cookies)} cookies, need both next_auth + next_auth.sig)"
                    )
            except Exception as e:
                logger.debug(f"[flowus] CDP cookie save failed: {e}")

            page = await ctx.new_page()
            logger.info("[flowus] CDP: reusing context with FlowUs cookies")
            return (browser, ctx, page)

        # Found no usable context
        await browser.close()
        await pw.stop()
        logger.debug("[flowus] CDP: no context with FlowUs cookies found")
    except Exception as e:
        logger.debug(f"[flowus] CDP connect failed: {e}")
    return None


async def _launch_browser_for_flowus():
    """Launch new Playwright browser, optionally with saved storage state."""
    from feedgrab.config import get_user_agent, get_session_dir
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError(
            "Playwright is required for FlowUs. Install:\n"
            '  pip install "feedgrab[browser]"\n'
            "  playwright install chromium"
        )

    pw = await async_playwright().start()
    session_path = get_session_dir() / "flowus.json"
    storage_state = str(session_path) if session_path.exists() else None

    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        user_agent=get_user_agent(),
        storage_state=storage_state,
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
    )
    page = await context.new_page()
    return (pw, browser, context, page)


async def _fetch_via_browser(url: str, doc_uuid: str, share_code: Optional[str]) -> Optional[Dict[str, Any]]:
    """Tier 1/2: browser-mediated. Used when Tier 0 has no cookie.

    Steps:
      1. Try CDP to reuse running Chrome (auto-saves cookies for next time)
      2. Fallback: launch new browser with saved storage_state
      3. Navigate to URL, run /api/docs/{uuid} fetch inside page (cookies auto-attached)
    """
    from feedgrab.config import flowus_cdp_enabled, flowus_page_load_timeout, get_session_dir

    cdp_conn = None
    pw_instance = None
    session_path = get_session_dir() / "flowus.json"
    timeout = flowus_page_load_timeout()

    try:
        if not session_path.exists() and flowus_cdp_enabled():
            cdp_conn = await _connect_flowus_cdp(url)
            if cdp_conn:
                browser, _ctx, page = cdp_conn
                await page.set_viewport_size({"width": 1920, "height": 1080})
            else:
                logger.warning(
                    "[flowus] CDP 复用本机 Chrome 失败：请确认 Chrome 已用 "
                    "`--remote-debugging-port=9222` 启动并登录 flowus.cn，"
                    "或运行 `feedgrab login flowus` 保存 session。"
                    "公开文档仍可继续抓取，付费/私有文档此次将无登录态。"
                )
                pw_instance, browser, _ctx, page = await _launch_browser_for_flowus()
        else:
            pw_instance, browser, _ctx, page = await _launch_browser_for_flowus()

        logger.info(f"[flowus] Navigating: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 2)
        except Exception as e:
            logger.debug(f"[flowus] goto timeout (continuing): {e}")
        await page.wait_for_timeout(1500)

        # Inside the page, fetch /api/docs/{uuid} — cookies auto-attached
        api_data = await page.evaluate(
            """
            async (params) => {
              const {doc_uuid, share_code} = params;
              const headers = {
                'x-platform': 'web-cookie',
                'x-app-origin': 'web',
                'x-product': 'flowus',
                'app_version_name': '1.146.0',
                'accept': 'application/json, text/plain, */*',
              };
              const out = {};
              try {
                const r1 = await fetch(`/api/docs/${doc_uuid}`, {headers, credentials:'include'});
                out.doc = await r1.json();
              } catch(e) { out.doc_err = String(e); }
              try {
                const r2 = await fetch(`/api/docs/${doc_uuid}/publicData`, {headers, credentials:'include'});
                out.public = await r2.json();
              } catch(e) { out.public_err = String(e); }
              if (share_code) {
                try {
                  const r3 = await fetch(`/api/share/users/getUserByCode?code=${share_code}`, {headers, credentials:'include'});
                  out.sharer = await r3.json();
                } catch(e) { out.sharer_err = String(e); }
              }
              return out;
            }
            """,
            {"doc_uuid": doc_uuid, "share_code": share_code or ""},
        )

        # Persist cookies for next-time Tier 0 (launch-mode case),
        # but only if we actually got an auth cookie — otherwise we'd
        # overwrite real CDP-extracted cookies with junk later.
        if not session_path.exists():
            try:
                ctx_state = await page.context.storage_state()
                flowus_cookies = [
                    c for c in ctx_state.get("cookies", [])
                    if c.get("domain", "").endswith("flowus.cn")
                    or c.get("domain", "") == "flowus.cn"
                ]
                has_auth = (
                    any(c.get("name") == "next_auth" for c in flowus_cookies)
                    and any(c.get("name") == "next_auth.sig" for c in flowus_cookies)
                )
                if flowus_cookies and has_auth:
                    session_path.parent.mkdir(parents=True, exist_ok=True)
                    session_path.write_text(
                        json.dumps({"cookies": flowus_cookies, "origins": []},
                                   ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    logger.info(
                        f"[flowus] Browser: saved {len(flowus_cookies)} cookies → {session_path}"
                    )
                elif flowus_cookies:
                    logger.debug(
                        f"[flowus] Browser: skipping cookie save — auth cookies incomplete "
                        f"(got {len(flowus_cookies)} cookies, need both next_auth + next_auth.sig)"
                    )
            except Exception as e:
                logger.debug(f"[flowus] cookie persist failed: {e}")

        doc_json = api_data.get("doc") or {}
        public_json = api_data.get("public") or {}
        sharer_json = api_data.get("sharer") or {}

        if doc_json.get("code") != 200:
            return {
                "_error": doc_json.get("code"),
                "_msg": doc_json.get("msg", ""),
                "_doc_uuid": doc_uuid,
            }

        return {
            "doc_data": doc_json.get("data", {}),
            "public_data": public_json.get("data", {}) if public_json.get("code") == 200 else {},
            "sharer": sharer_json.get("data", {}) if sharer_json.get("code") == 200 else {},
            "_doc_uuid": doc_uuid,
            "_share_code": share_code,
        }
    except Exception as e:
        logger.warning(f"[flowus] Browser extraction failed: {e}")
        return None
    finally:
        try:
            if cdp_conn:
                await cdp_conn[2].close()
                await cdp_conn[0].close()
            elif pw_instance:
                await browser.close()
                await pw_instance.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Blocks → Markdown
# ---------------------------------------------------------------------------

# Block type IDs (verified against real doc samples)
_BT_PAGE = 0
_BT_PARA = 1
_BT_BULLET = 4
_BT_ORDERED = 5
_BT_HEADING = 7
_BT_QUOTE = 12
_BT_MEDIA = 14
_BT_CODE = 25


def _render_segments(segments: list) -> str:
    """Render inline segments to Markdown text."""
    parts: list = []
    for seg in segments or []:
        text = seg.get("text", "")
        if not text:
            continue
        enhancer = seg.get("enhancer", {}) or {}
        # Link segment
        if seg.get("type") == 3 and seg.get("url"):
            text = f"[{text}]({seg['url']})"
        # Order: italic outermost? Markdown convention: ***bold italic***
        if enhancer.get("bold"):
            text = f"**{text}**"
        if enhancer.get("italic"):
            text = f"*{text}*"
        if enhancer.get("code"):
            text = f"`{text}`"
        if enhancer.get("backgroundColor"):
            text = f"=={text}=="
        # textColor: Markdown 无统一表达，保留原文
        parts.append(text)
    return "".join(parts)


def _flowus_remote_image_src(
    link: str,
    oss_name: str,
    online_image_urls: Optional[Dict[str, str]] = None,
) -> str:
    """Choose a previewable remote image URL for FlowUs online mode."""
    normalized_oss = (oss_name or "").lstrip("/")
    if online_image_urls and normalized_oss:
        signed = online_image_urls.get(normalized_oss) or online_image_urls.get(oss_name)
        if signed:
            return signed
    return link or (f"https://cdn2.flowus.cn/{normalized_oss}" if normalized_oss else "")


def _collect_image_oss_names(blocks: Dict[str, dict], root_id: str) -> List[str]:
    """Collect FlowUs image ossName values in document order."""
    names: List[str] = []
    seen_names: set = set()
    visited: set = set()

    def _walk(block_id: str) -> None:
        if block_id in visited:
            return
        visited.add(block_id)
        block = blocks.get(block_id)
        if not block:
            return

        data = block.get("data", {}) or {}
        if block.get("type") == _BT_MEDIA and (data.get("display") or "").lower() == "image":
            oss_name = (data.get("ossName") or "").lstrip("/")
            if oss_name and oss_name not in seen_names:
                seen_names.add(oss_name)
                names.append(oss_name)

        for child_id in block.get("subNodes", []) or []:
            _walk(child_id)

    _walk(root_id)
    return names


def _walk_blocks(
    blocks: Dict[str, dict],
    root_id: str,
    img_subdir: str = "",
    localize_images: bool = False,
    online_image_urls: Optional[Dict[str, str]] = None,
) -> Tuple[str, List[dict]]:
    """Walk the block tree starting from root_id and render to Markdown.

    Returns (markdown, images_info).
    """
    lines: List[str] = []
    images: List[dict] = []
    img_idx = [0]
    visited: set = set()

    def _walk(block_id: str, depth: int, ordered_counter: Dict[int, int]) -> None:
        if block_id in visited:
            return
        visited.add(block_id)
        block = blocks.get(block_id)
        if not block:
            return

        btype = block.get("type")
        data = block.get("data", {}) or {}
        segs = data.get("segments", [])

        # Reset ordered counter on non-ordered siblings
        if btype != _BT_ORDERED:
            ordered_counter.clear()

        if btype == _BT_PAGE:
            # Skip page header; root is rendered as title outside
            pass
        elif btype == _BT_HEADING:
            level = max(1, min(6, int(data.get("level", 1))))
            prefix = "#" * level
            text = _render_segments(segs)
            if text:
                lines.append(f"\n{prefix} {text}\n")
        elif btype == _BT_PARA:
            text = _render_segments(segs)
            if text:
                lines.append(text)
                lines.append("")
        elif btype == _BT_BULLET:
            indent = "  " * depth
            text = _render_segments(segs)
            if text:
                lines.append(f"{indent}- {text}")
        elif btype == _BT_ORDERED:
            counter = ordered_counter.get(depth, 0) + 1
            ordered_counter[depth] = counter
            indent = "  " * depth
            text = _render_segments(segs)
            if text:
                lines.append(f"{indent}{counter}. {text}")
        elif btype == _BT_QUOTE:
            text = _render_segments(segs)
            if text:
                for line in text.split("\n"):
                    lines.append(f"> {line}")
                lines.append("")
        elif btype == _BT_CODE:
            lang = (data.get("format", {}) or {}).get("language", "")
            if lang and lang.lower() in ("plain text", "plaintext", "text"):
                lang = ""
            code_text = "".join(s.get("text", "") for s in segs)
            fence_lang = (lang or "").lower()
            lines.append(f"\n````{fence_lang}")
            lines.append(code_text)
            lines.append("````\n")
        elif btype == _BT_MEDIA:
            display = (data.get("display") or "").lower()
            link = data.get("link") or ""
            oss_name = (data.get("ossName") or "").lstrip("/")
            ext = (data.get("extName") or "").lower()
            alt_text = _render_segments(segs) or (oss_name.split("/")[-1] if oss_name else "media")

            if display == "image":
                # Remote mode should still be previewable, so prefer FlowUs'
                # signed CDN URL when the caller resolved one from the DOM.
                # Local mode keeps attachment paths and downloads separately.
                src = _flowus_remote_image_src(link, oss_name, online_image_urls)
                if localize_images and (oss_name or link):
                    fname = f"{img_idx[0]:03d}_image.{ext or 'png'}"
                    img_idx[0] += 1
                    images.append({
                        "url": link,           # fallback URL (often Feishu, may 400)
                        "oss_name": oss_name,  # primary key for DOM matching
                        "filename": fname,
                    })
                    rel = f"attachments/{img_subdir}/{fname}" if img_subdir else f"attachments/{fname}"
                    lines.append(f"![{alt_text}]({rel})")
                elif src:
                    lines.append(f"![{alt_text}]({src})")
                lines.append("")
            elif display in ("video", "audio") and link:
                lines.append(f"[🎬 {alt_text}]({link})")
                lines.append("")
            elif link:
                lines.append(f"[📎 {alt_text}]({link})")
                lines.append("")
        else:
            # Unknown type → fallback: render segments as plain paragraph
            text = _render_segments(segs)
            if text:
                lines.append(text)
                lines.append("")

        # Recurse into children (for nested lists, etc.)
        sub = block.get("subNodes") or []
        if sub:
            # Bullet / ordered / quote increase depth so nested children indent
            next_depth = depth + 1 if btype in (_BT_BULLET, _BT_ORDERED, _BT_QUOTE) else depth
            child_counter: Dict[int, int] = {}
            for child_id in sub:
                _walk(child_id, next_depth, child_counter)

    root = blocks.get(root_id, {})
    for child_id in root.get("subNodes", []) or []:
        _walk(child_id, 0, {})

    md = "\n".join(lines).strip()
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md, images


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _ts_to_local(ts_ms: int) -> str:
    if not ts_ms:
        return ""
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


_ZW_RE = re.compile(r"[​-⁯﻿]")


def _clean_title(raw: str) -> str:
    raw = _ZW_RE.sub("", raw or "")
    return raw.strip()


async def fetch_flowus(url: str) -> Dict[str, Any]:
    """Fetch a FlowUs document.

    Tier 0: HTTP API (saved cookie)
    Tier 1/2: Playwright (CDP or launch) — also auto-saves cookies
    Tier 3: Jina fallback (only on total failure)
    """
    from feedgrab.config import flowus_download_images

    doc_uuid, share_code = parse_flowus_url(url)
    if not doc_uuid:
        raise ValueError(f"无法从 URL 解析 FlowUs doc UUID: {url}")

    item_id = hashlib.md5(doc_uuid.encode()).hexdigest()[:12]
    localize = flowus_download_images()

    # Tier 0: HTTP
    raw = _fetch_via_http(url, doc_uuid, share_code)

    # Tier 1/2: browser if Tier 0 failed or cookies stale
    if not raw or raw.get("_error"):
        logger.info("[flowus] HTTP failed/needs auth, switching to browser")
        raw = await _fetch_via_browser(url, doc_uuid, share_code)

    if not raw or raw.get("_error"):
        # Tier 3: Jina fallback (only if API was completely unreachable)
        error_code = (raw or {}).get("_error")
        error_msg = (raw or {}).get("_msg", "")
        if error_code in (1407, 1404, 1403, 401, 403):
            # These are definitive — don't fall through to Jina
            return {
                "title": f"[FlowUs 错误 {error_code}] {error_msg or doc_uuid}",
                "content": f"FlowUs 文档抓取失败：code={error_code} msg={error_msg}\n\n该文档可能为付费/私有/不存在内容。",
                "url": url,
                "author": "",
                "doc_token": doc_uuid,
                "share_code": share_code or "",
                "word_count": 0,
                "create_time": "",
                "edit_time": "",
                "space_title": "",
                "images_info": [],
                "img_subdir": item_id,
                "tags": [],
            }

        logger.info("[flowus] Falling back to Jina Reader")
        try:
            from feedgrab.fetchers.jina import fetch_via_jina
            jina_data = await fetch_via_jina(url)
            return {
                "title": jina_data.get("title", ""),
                "content": jina_data.get("content", ""),
                "url": url,
                "author": "",
                "doc_token": doc_uuid,
                "share_code": share_code or "",
                "word_count": len(jina_data.get("content", "")),
                "create_time": "",
                "edit_time": "",
                "space_title": "",
                "images_info": [],
                "img_subdir": item_id,
                "tags": [],
            }
        except Exception as e:
            raise RuntimeError(f"FlowUs 抓取失败且 Jina 兜底也失败：{e}") from e

    # ── Success: render block tree ──
    doc_data = raw.get("doc_data", {}) or {}
    public_data = raw.get("public_data", {}) or {}
    sharer = raw.get("sharer", {}) or {}

    blocks: Dict[str, dict] = doc_data.get("blocks", {}) or {}
    root = blocks.get(doc_uuid, {}) or {}

    # Title: prefer seoTitle > segments > title field
    root_data = root.get("data", {}) or {}
    title_segments = root_data.get("segments", [])
    title_from_segments = "".join(s.get("text", "") for s in title_segments)
    title = (
        _clean_title(root_data.get("seoTitle", ""))
        or _clean_title(title_from_segments)
        or _clean_title(root.get("title", ""))
        or doc_uuid
    )

    # Author: sharer.nickname > publicData.ownerUser.nickname
    author = (
        sharer.get("nickname")
        or (public_data.get("ownerUser") or {}).get("nickname")
        or ""
    )
    space_title = public_data.get("spaceTitle", "") or ""

    create_time = _ts_to_local(root.get("createdAt", 0))
    edit_time = _ts_to_local(root.get("updatedAt", 0))

    online_image_urls: Dict[str, str] = {}
    if not localize:
        oss_names = _collect_image_oss_names(blocks, doc_uuid)
        if oss_names:
            logger.info(f"[flowus] Resolving online image URLs from DOM ({len(oss_names)} images)...")
            online_image_urls = _resolve_image_urls_from_dom(url, oss_names)
            logger.info(f"[flowus] Online image URL resolution: {len(online_image_urls)} signed URLs found")

    md_body, images_info = _walk_blocks(
        blocks,
        doc_uuid,
        img_subdir=item_id,
        localize_images=localize,
        online_image_urls=online_image_urls,
    )

    return {
        "title": title,
        "content": md_body,
        "url": url,
        "author": author,
        "doc_token": doc_uuid,
        "share_code": share_code or "",
        "word_count": len(md_body),
        "create_time": create_time,
        "edit_time": edit_time,
        "space_title": space_title,
        "seo_description": _clean_title(root_data.get("seoDescription", "")),
        "images_info": images_info,
        "img_subdir": item_id,
        "tags": [],
    }


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

def _resolve_image_urls_from_dom(doc_url: str, oss_names: List[str]) -> Dict[str, str]:
    """Open the doc in a headless browser, let the SPA render, and harvest the
    signed ``cdn2.flowus.cn/<ossName>?time=...&token=...&role=...`` URLs.

    FlowUs computes these per-share signed URLs client-side; without source
    bundles we can't reproduce the signing algorithm, but the rendered DOM
    always contains them. Returns ``{ossName: signed_url}``.
    """
    import asyncio
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("[flowus] 未安装 Playwright，无法解析签名图片 URL")
        return {}

    from feedgrab.config import get_user_agent, get_session_dir, flowus_page_load_timeout
    session_path = get_session_dir() / "flowus.json"
    storage_state = str(session_path) if session_path.exists() else None
    timeout = flowus_page_load_timeout()

    async def _run() -> Dict[str, str]:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=get_user_agent(),
            storage_state=storage_state,
            viewport={"width": 1920, "height": 3000},
            locale="zh-CN",
        )
        page = await ctx.new_page()
        try:
            await page.goto(doc_url, wait_until="domcontentloaded", timeout=timeout * 2)
        except Exception as e:
            logger.debug(f"[flowus] image-resolve goto error (continuing): {e}")
        # SPA needs time to render + lazy-load img elements; scroll to bottom
        # to force-load lazy images.
        await page.wait_for_timeout(2000)
        try:
            await page.evaluate(
                """async () => {
                    // Force-eager all lazy-loaded images so they request immediately
                    document.querySelectorAll('img[loading="lazy"]').forEach(i => i.loading = 'eager');
                    // Multi-pass scroll: incremental down → bottom → multiple pause points.
                    // FlowUs uses virtual rendering on long docs; step ~400px each tick
                    // and pause long enough to let IntersectionObserver + image
                    // request kick off.
                    const passes = 3;
                    for (let pass = 0; pass < passes; pass++) {
                        const step = pass === 0 ? 400 : 800;
                        let pos = 0;
                        while (pos < document.body.scrollHeight + 8000) {
                            window.scrollTo(0, pos);
                            await new Promise(r => setTimeout(r, pass === 0 ? 280 : 180));
                            pos += step;
                        }
                        window.scrollTo(0, document.body.scrollHeight);
                        await new Promise(r => setTimeout(r, 1500));
                        // Force-eager any newly-added lazy images
                        document.querySelectorAll('img[loading="lazy"]').forEach(i => i.loading = 'eager');
                    }
                    window.scrollTo(0, 0);
                    await new Promise(r => setTimeout(r, 500));
                }"""
            )
        except Exception:
            pass
        # Final wait for all in-flight image requests to settle
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        mapping = await page.evaluate(
            """() => {
                const out = {};
                const imgs = Array.from(document.querySelectorAll('img'));
                for (const img of imgs) {
                    const src = img.src || '';
                    if (!src.includes('cdn2.flowus.cn/oss/')) continue;
                    try {
                        const u = new URL(src);
                        // path like /oss/<uuid>/<filename>
                        const path = decodeURIComponent(u.pathname).replace(/^\\//, '');
                        if (path.startsWith('oss/')) {
                            // Keep the first occurrence (likely the highest quality variant)
                            if (!out[path]) out[path] = src;
                        }
                    } catch (e) {}
                }
                return out;
            }"""
        )
        await browser.close()
        await pw.stop()
        return mapping or {}

    try:
        # Run in a separate thread to avoid `asyncio.run() cannot be called
        # from a running event loop` when called from inside an async caller
        # (reader.py awaits the fetcher, but image download is post-save sync).
        import threading
        result: Dict[str, str] = {}
        error: List[Exception] = []

        def _thread_main():
            try:
                result.update(asyncio.run(_run()))
            except Exception as e:
                error.append(e)

        t = threading.Thread(target=_thread_main, daemon=True)
        t.start()
        t.join(timeout=120)
        if t.is_alive():
            logger.warning("[flowus] DOM 图片解析线程超时")
            return {}
        if error:
            raise error[0]
        return result
    except Exception as e:
        logger.warning(f"[flowus] DOM 图片 URL 解析失败：{e}")
        return {}


def download_flowus_images(
    md_path: str, images_info: List[dict], img_subdir: str = "",
) -> None:
    """Download FlowUs images to ``{md_dir}/attachments/{img_subdir}/``.

    Strategy:
      1. Open the doc in a headless browser, harvest signed
         ``cdn2.flowus.cn/oss/.../?time=&token=&role=`` URLs by ``ossName``.
      2. Direct HTTP GET each signed URL — they're self-authenticating
         (no cookie / no referer needed).
      3. For images without an ``ossName`` match, try the raw ``link``
         (usually a Feishu CDN URL with hotlink protection — may 400).
    """
    if not images_info:
        return
    md_dir = Path(md_path).parent
    att_dir = md_dir / "attachments"
    if img_subdir:
        att_dir = att_dir / img_subdir
    att_dir.mkdir(parents=True, exist_ok=True)

    # Read doc_url from the saved Markdown's front matter
    doc_url = ""
    try:
        text = Path(md_path).read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'^source:\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            doc_url = m.group(1)
    except Exception:
        pass

    # Phase 1: resolve signed CDN URLs from DOM (only if we have any ossName)
    oss_names = [i.get("oss_name") for i in images_info if i.get("oss_name")]
    oss_to_signed: Dict[str, str] = {}
    if oss_names and doc_url:
        logger.info(f"[flowus] 正在从 DOM 解析签名 CDN URL（{len(oss_names)} 张图片）...")
        oss_to_signed = _resolve_image_urls_from_dom(doc_url, oss_names)
        logger.info(f"[flowus] DOM 解析完成：找到 {len(oss_to_signed)} 个签名 URL")
    elif not oss_names:
        logger.debug("[flowus] images_info 中没有 ossName，跳过 DOM 解析")
    elif not doc_url:
        logger.warning("[flowus] 无法从 Markdown front matter 读取 doc_url")

    # Phase 2: download
    from feedgrab.utils.http_client import get as http_get

    def _try_get(url: str, extra_headers: Optional[dict] = None) -> Optional[bytes]:
        try:
            headers = {"user-agent": _build_headers("https://flowus.cn/")["user-agent"]}
            if extra_headers:
                headers.update(extra_headers)
            resp = http_get(url, headers=headers, timeout=30)
        except Exception:
            return None
        if resp.status_code != 200 or not resp.content:
            return None
        ct = (resp.headers.get("content-type") or "").lower()
        if "html" in ct or "json" in ct:
            return None
        return resp.content

    succeeded = failed = 0
    for info in images_info:
        fname = info.get("filename", "")
        oss_name = info.get("oss_name", "")
        link = info.get("url", "")
        if not fname:
            continue
        fpath = att_dir / fname
        if fpath.exists() and fpath.stat().st_size > 0:
            continue

        # Tier 0: signed CDN URL from DOM (always works)
        signed = oss_to_signed.get(oss_name) if oss_name else None
        content = _try_get(signed) if signed else None
        # Tier 1: fall back to the raw link (Feishu CDN, may 400)
        if not content and link:
            content = _try_get(link, {"referer": "https://flowus.cn/"})

        if content:
            fpath.write_bytes(content)
            succeeded += 1
            logger.debug(f"[flowus] 已下载：{fname}（{len(content)} bytes）")
        else:
            failed += 1
            logger.warning(f"[flowus] 图片下载失败：{fname}")

    if succeeded or failed:
        logger.info(f"[flowus] 图片下载：成功 {succeeded}，失败 {failed}")
