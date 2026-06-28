# -*- coding: utf-8 -*-
"""
KDocs (金山文档) fetcher — extract content from WPS cloud documents.

Tier 0: Playwright JS evaluate (ProseMirror DOM extraction)
  - CDP direct connect (reuse running Chrome for auth-required docs)
  - Launch new browser (public docs)
Tier 1: Jina Reader (zero-config fallback)

URL format: https://www.kdocs.cn/l/{token}
"""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from loguru import logger


# ---------------------------------------------------------------------------
# URL detection & parsing
# ---------------------------------------------------------------------------

_KDOCS_DOMAINS = (".kdocs.cn",)
_DOC_URL_PATTERN = re.compile(r"/l/([A-Za-z0-9_-]+)")


def is_kdocs_url(url: str) -> bool:
    """Check whether *url* belongs to a KDocs domain."""
    netloc = urlparse(url).netloc.lower()
    for d in _KDOCS_DOMAINS:
        if netloc.endswith(d) or netloc == d.lstrip("."):
            return True
    return False


def parse_kdocs_url(url: str) -> Optional[str]:
    """Extract doc token from a KDocs share URL. Returns token or None."""
    m = _DOC_URL_PATTERN.search(urlparse(url).path)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# JS evaluate: extract metadata + content from ProseMirror DOM
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Blocks → Markdown conversion
# ---------------------------------------------------------------------------

def _blocks_to_markdown(
    blocks: list, img_subdir: str = "", localize_images: bool = False,
) -> tuple:
    """Convert extracted block list to Markdown string.

    Image blocks should have their blob: URLs already resolved to real CDN
    URLs by _resolve_image_urls() before calling this function.

    Args:
        blocks: Structured block list from JS extraction.
        img_subdir: Subdirectory name for local image storage (e.g. item_id).
        localize_images: If True, replace CDN URLs with local relative paths
            and collect images_info for later download.

    Returns:
        (markdown_string, images_info_list)
    """
    lines = []
    images_info = []
    ordered_counter = {}  # track ordered list counters per level
    img_idx = 0

    for block in blocks:
        btype = block.get("type", "")

        if btype == "heading":
            level = block.get("level", 1)
            prefix = "#" * min(level, 6)
            lines.append(f"\n{prefix} {block['text']}\n")

        elif btype == "paragraph":
            lines.append(block["text"])
            lines.append("")

        elif btype == "bullet":
            indent = "  " * block.get("level", 0)
            lines.append(f"{indent}- {block['text']}")

        elif btype == "ordered":
            level = block.get("level", 0)
            counter = ordered_counter.get(level, 0) + 1
            ordered_counter[level] = counter
            indent = "  " * level
            lines.append(f"{indent}{counter}. {block['text']}")

        elif btype == "todo":
            mark = "x" if block.get("checked") else " "
            lines.append(f"- [{mark}] {block['text']}")

        elif btype == "hr":
            lines.append("\n---\n")

        elif btype == "code":
            lang = block.get("lang", "").lower()
            # Normalize language name for Markdown fence
            if lang in ("plain text", "plaintext", "text"):
                lang = ""
            code = block.get("text", "")
            lines.append(f"\n````{lang}")
            lines.append(code)
            lines.append("````\n")

        elif btype == "image":
            src = block.get("src", "")
            alt = block.get("alt", "")
            # Skip unresolved blob: URLs and loading placeholders
            if not src or src.startswith("blob:") or src.startswith("data:"):
                continue
            if "loading." in src or "weboffice-static" in src:
                continue

            if localize_images:
                # Determine file extension from URL
                ext = _guess_image_ext(src)
                fname = f"{img_idx:03d}_image{ext}"
                img_idx += 1
                images_info.append({"url": src, "filename": fname})
                rel_path = f"attachments/{img_subdir}/{fname}" if img_subdir else f"attachments/{fname}"
                lines.append(f"![{alt}]({rel_path})")
            else:
                lines.append(f"![{alt}]({src})")
            lines.append("")

        # Reset ordered counter when non-ordered block appears
        if btype != "ordered":
            ordered_counter.clear()

    result = "\n".join(lines).strip()
    # 合并连续的分割线（虚拟滚动提取时空 block_tile 会产生大量重复 hr）
    result = re.sub(r"(\n---\n)(\s*\n---\n)+", r"\n---\n", result)
    # 清理分割线前后多余空行
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result, images_info


def _guess_image_ext(url: str) -> str:
    """Guess image file extension from URL path."""
    path = urlparse(url).path.lower()
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
        if ext in path:
            return ext
    return ".png"


# PLACEHOLDER_BROWSER_FUNCTIONS


async def _connect_kdocs_cdp() -> Optional[tuple]:
    """Connect to running Chrome via CDP, find a context with KDocs cookies.

    Returns (browser, context, page) or None.
    Also saves cookies to sessions/kdocs.json for future Launch-mode reuse.
    """
    from feedgrab.config import chrome_cdp_port, get_session_dir

    port = chrome_cdp_port()
    ws_url = f"ws://127.0.0.1:{port}/devtools/browser"

    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(ws_url)
        logger.debug(f"[kdocs] CDP connected: {ws_url}")

        # Find context with kdocs/wps cookies
        target_domains = (".kdocs.cn", ".wps.cn")
        for ctx in browser.contexts:
            cookies = await ctx.cookies()
            for c in cookies:
                domain = c.get("domain", "")
                if any(domain.endswith(d) for d in target_domains):
                    # Save KDocs-relevant cookies as Playwright storage_state
                    try:
                        session_path = get_session_dir() / "kdocs.json"
                        session_path.parent.mkdir(parents=True, exist_ok=True)
                        # Only keep KDocs/WPS cookies (not entire Chrome profile)
                        kdocs_domains = (".kdocs.cn", ".wps.cn", ".wps.com")
                        kdocs_cookies = [
                            c for c in cookies
                            if any(c.get("domain", "").endswith(d) for d in kdocs_domains)
                        ]
                        # Playwright storage_state format
                        storage = {
                            "cookies": kdocs_cookies,
                            "origins": [],
                        }
                        import json
                        session_path.write_text(
                            json.dumps(storage, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        logger.info(f"[kdocs] CDP: saved {len(kdocs_cookies)} cookies to {session_path}")
                    except Exception as e:
                        logger.debug(f"[kdocs] CDP: cookie save failed: {e}")

                    page = await ctx.new_page()
                    logger.info("[kdocs] CDP: reusing context with KDocs cookies")
                    return (browser, ctx, page)

        # No matching context — close and return None
        await browser.close()
        await pw.stop()
        logger.debug("[kdocs] CDP: no context with KDocs cookies found")
    except Exception as e:
        logger.debug(f"[kdocs] CDP connect failed: {e}")

    return None


async def _launch_browser_for_kdocs(url: str):
    """Launch a new browser for KDocs. Returns (pw, browser, context, page)."""
    from feedgrab.config import get_user_agent, get_session_dir

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError(
            "Playwright is required for KDocs. Install:\n"
            '  pip install "feedgrab[browser]"\n'
            "  playwright install chromium"
        )

    pw = await async_playwright().start()

    # Try loading saved session
    session_path = get_session_dir() / "kdocs.json"
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


async def _scroll_and_collect_blocks(page) -> list:
    """Scroll .otl-scroll-container step by step, extracting blocks at each
    position to defeat virtual-scroll DOM recycling.

    Returns a deduplicated, ordered list of block dicts.
    """
    EXTRACT_VISIBLE_JS = """
    () => {
      const pm = document.querySelector('.ProseMirror.otl-main-editor');
      if (!pm) return [];
      const blocks = [];
      const tiles = pm.querySelectorAll('.block_tile');
      for (const tile of tiles) {
        const heading = tile.querySelector('.otl-heading');
        if (heading) {
          const level = parseInt(heading.getAttribute('level') || '1');
          const text = (heading.querySelector('.otl-heading-content')?.textContent || '').trim();
          if (text) blocks.push({ type: 'heading', level, text });
          continue;
        }
        if (tile.querySelector('hr')) { blocks.push({ type: 'hr' }); continue; }
        const nv = tile.querySelector('nodeview');
        if (nv) {
          // Code block: <nodeview data-node-type="code_block">
          if (nv.getAttribute('data-node-type') === 'code_block') {
            const pre = nv.querySelector('pre');
            if (pre) {
              const lang = (pre.getAttribute('lang') || '').trim();
              // Code content is in <code class="code-block-content">
              const codeEl = pre.querySelector('code.code-block-content');
              const code = codeEl ? (codeEl.textContent || '').trim() : '';
              if (code) blocks.push({ type: 'code', lang: lang, text: code });
            }
            continue;
          }
          // Image: <nodeview> with <img>
          const img = nv.querySelector('img');
          if (img) {
            let src = img.getAttribute('data-src')
                     || img.getAttribute('data-origin')
                     || img.getAttribute('data-url')
                     || '';
            if (!src || src.startsWith('blob:')) {
              const bgEl = nv.querySelector('[style*="background-image"]');
              if (bgEl) {
                const m = (bgEl.style.backgroundImage || '').match(/url\\(["']?(https?:\\/\\/[^"')]+)["']?\\)/);
                if (m) src = m[1];
              }
            }
            if (!src) src = img.src || '';
            blocks.push({ type: 'image', src, alt: img.alt || '', sourcekey: img.getAttribute('sourcekey') || '' });
          }
          continue;
        }
        const para = tile.querySelector('.otl-paragraph');
        if (!para) continue;
        const lt = para.getAttribute('listtype') || '';
        const ll = parseInt(para.getAttribute('listlevel') || '0');
        const ce = para.querySelector('.otl-paragraph-content');
        if (!ce) continue;
        let text = '';
        const walk = (n) => {
          if (n.nodeType === 3) { text += n.textContent; return; }
          if (n.nodeType !== 1) return;
          const t = n.tagName, c = (typeof n.className === 'string') ? n.className : '';
          if (c.indexOf('otl-word-gap') >= 0) return;
          if (t === 'A') { text += '[' + (n.textContent||'').trim() + '](' + (n.getAttribute('href')||'') + ')'; return; }
          if (t === 'STRONG' || t === 'B') { text += '**' + (n.textContent||'') + '**'; return; }
          if (t === 'EM') { text += '*' + (n.textContent||'') + '*'; return; }
          for (const ch of n.childNodes) walk(ch);
        };
        for (const ch of ce.childNodes) walk(ch);
        text = text.trim();
        if (!text) continue;
        if (lt === 'bullet') blocks.push({ type: 'bullet', level: ll, text });
        else if (lt === 'ordered') blocks.push({ type: 'ordered', level: ll, text });
        else blocks.push({ type: 'paragraph', text });
      }
      return blocks;
    }
    """

    all_blocks = []
    seen_keys = set()

    def _block_key(b):
        if b.get("type") == "hr":
            return f"hr_{len(all_blocks)}"
        if b.get("type") == "image":
            return f"image:{b.get('sourcekey', '') or b.get('src', '')[:80]}"
        return f"{b.get('type')}:{b.get('text', '')[:80]}"

    def _merge_new(new_blocks):
        for b in new_blocks:
            key = _block_key(b)
            if key not in seen_keys:
                seen_keys.add(key)
                all_blocks.append(b)

    # Check scroll container exists
    has_container = await page.evaluate(
        "() => !!document.querySelector('.otl-scroll-container')"
    )
    if not has_container:
        logger.warning("[kdocs] No scroll container found — extracting visible content only")
        _merge_new(await page.evaluate(EXTRACT_VISIBLE_JS) or [])
        return all_blocks

    # Scroll to top first to ensure clean start
    await page.evaluate(
        "() => { const s = document.querySelector('.otl-scroll-container'); if(s) s.scrollTop = 0; }"
    )
    await page.wait_for_timeout(500)

    # Extract at top
    _merge_new(await page.evaluate(EXTRACT_VISIBLE_JS) or [])

    # Scroll step by step, extract at each position
    stall_count = 0
    for _ in range(200):
        prev = await page.evaluate(
            "() => document.querySelector('.otl-scroll-container')?.scrollTop ?? -1"
        )
        await page.evaluate(
            "() => { const s = document.querySelector('.otl-scroll-container'); if(s) s.scrollTop += 600; }"
        )
        await page.wait_for_timeout(250)
        cur = await page.evaluate(
            "() => document.querySelector('.otl-scroll-container')?.scrollTop ?? -1"
        )
        _merge_new(await page.evaluate(EXTRACT_VISIBLE_JS) or [])
        if cur == prev:
            # Virtual scroll may need more time to render — retry once
            stall_count += 1
            if stall_count >= 2:
                break
            await page.wait_for_timeout(500)
        else:
            stall_count = 0

    return all_blocks


async def _setup_shapes_interceptor(page) -> dict:
    """Register response interceptor to capture the attachment/shapes API
    response, which maps sourcekey → CDN URL for all document images.

    Returns a mutable dict: {sourcekey: url_info} populated when the
    shapes API response arrives.
    """
    shapes_map = {}

    async def _on_response(response):
        if "attachment/shapes" not in response.url:
            return
        try:
            data = await response.json()
            entries = data.get("data", {})
            for sourcekey, info in entries.items():
                # Prefer 'raw' (original size), fallback to 'url'
                url = info.get("raw") or info.get("url") or info.get("thumbnail") or ""
                if url:
                    shapes_map[sourcekey] = url
            if shapes_map:
                logger.debug(f"[kdocs] Shapes API: {len(shapes_map)} images")
        except Exception as e:
            logger.debug(f"[kdocs] Shapes API parse failed: {e}")

    page.on("response", _on_response)
    return shapes_map


async def _resolve_image_urls(page, blocks: list, shapes_map: dict) -> None:
    """Resolve blob: image URLs in blocks to real CDN URLs.

    KDocs images have a `sourcekey` attribute on the <img> element.
    The attachment/shapes API maps sourcekey → CDN URL.
    """
    if not shapes_map:
        # Shapes API might not have fired yet, try fetching manually
        doc_token = await page.evaluate(
            "() => (window.__WPSENV__?.file_info?.file?.id || '')"
        )
        if not doc_token:
            # Try extracting from URL
            current_url = page.url
            m = _DOC_URL_PATTERN.search(urlparse(current_url).path)
            doc_token = m.group(1) if m else ""

        if doc_token:
            try:
                shapes_data = await page.evaluate("""
                    async (token) => {
                        try {
                            const r = await fetch(
                                `/api/v3/office/file/${token}/attachment/shapes`,
                                { credentials: 'include' }
                            );
                            if (!r.ok) return {};
                            const d = await r.json();
                            return d.data || {};
                        } catch { return {}; }
                    }
                """, doc_token)
                for sourcekey, info in (shapes_data or {}).items():
                    url = info.get("raw") or info.get("url") or info.get("thumbnail") or ""
                    if url:
                        shapes_map[sourcekey] = url
                if shapes_map:
                    logger.debug(f"[kdocs] Shapes API (manual): {len(shapes_map)} images")
            except Exception as e:
                logger.debug(f"[kdocs] Manual shapes fetch failed: {e}")

    if not shapes_map:
        logger.debug("[kdocs] No image shapes data available")
        return

    # Resolve sourcekey in image blocks
    resolved = 0
    for block in blocks:
        if block.get("type") != "image":
            continue
        src = block.get("src", "")
        if not src.startswith("blob:"):
            continue
        # Get sourcekey from block (stored during JS extraction)
        sourcekey = block.get("sourcekey", "")
        if sourcekey and sourcekey in shapes_map:
            block["src"] = shapes_map[sourcekey]
            resolved += 1

    if resolved:
        logger.info(f"[kdocs] Resolved {resolved} image URLs via shapes API")


async def _extract_via_playwright(url: str) -> Optional[Dict[str, Any]]:
    """Tier 0: Extract KDocs content via Playwright JS evaluate.

    Priority: saved session (Launch) > CDP auto-cookie > Launch (no session).
    CDP only triggers when no session file exists, and auto-saves cookies
    so subsequent fetches skip CDP entirely.
    """
    from feedgrab.config import kdocs_cdp_enabled, kdocs_page_load_timeout, get_session_dir

    timeout = kdocs_page_load_timeout()
    cdp_conn = None
    pw_instance = None
    session_path = get_session_dir() / "kdocs.json"

    try:
        if session_path.exists():
            # Has saved session → Launch mode, no CDP needed
            pw_instance, browser, _ctx, page = await _launch_browser_for_kdocs(url)
        elif kdocs_cdp_enabled():
            # No session → try CDP to auto-extract cookies
            cdp_conn = await _connect_kdocs_cdp()
            if cdp_conn:
                browser, _ctx, page = cdp_conn
                await page.set_viewport_size({"width": 1920, "height": 1080})
            else:
                # CDP failed → Launch without session (public docs)
                pw_instance, browser, _ctx, page = await _launch_browser_for_kdocs(url)
        else:
            pw_instance, browser, _ctx, page = await _launch_browser_for_kdocs(url)

        # Register shapes API interceptor BEFORE navigation
        shapes_map = await _setup_shapes_interceptor(page)

        # Navigate
        logger.info(f"[kdocs] Navigating: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Wait for ProseMirror editor to appear
        editor_found = False
        try:
            await page.wait_for_selector(
                ".ProseMirror.otl-main-editor",
                timeout=timeout,
            )
            editor_found = True
        except Exception:
            # SSO redirect may happen — wait longer
            logger.debug("[kdocs] Waiting for SSO redirect...")
            try:
                await page.wait_for_selector(
                    ".ProseMirror.otl-main-editor",
                    timeout=timeout * 2,
                )
                editor_found = True
            except Exception:
                pass

        if not editor_found:
            # Session might be expired — prompt user
            if session_path.exists() and not cdp_conn:
                logger.warning(
                    "[kdocs] Cookie 可能已失效，页面未正常加载。"
                    "请删除 sessions/kdocs.json 后重试（将自动通过 CDP 刷新 Cookie），"
                    "或运行: feedgrab login kdocs"
                )
            raise Exception("ProseMirror editor not found — page did not load")

        # Wait for scroll container to be ready (virtual scroll needs init time)
        try:
            await page.wait_for_selector(
                ".otl-scroll-container",
                timeout=5000,
            )
        except Exception:
            logger.debug("[kdocs] Scroll container not found, content may be incomplete")

        # Stabilization delay — CDP mode needs longer for virtual scroll init
        await page.wait_for_timeout(2000 if cdp_conn else 1500)

        # Scroll and collect blocks (defeats virtual-scroll recycling)
        blocks = await _scroll_and_collect_blocks(page)

        # Resolve blob: image URLs via shapes API (sourcekey → CDN URL)
        await _resolve_image_urls(page, blocks, shapes_map)

        # Extract metadata via JS
        meta = await page.evaluate("""
            () => {
                const env = window.__WPSENV__ || {};
                const fi = env.file_info?.file || {};
                return {
                    title: (fi.name || document.title || '').trim(),
                    author: fi.creator?.name || fi.modifier?.name || '',
                    creator_id: fi.creator?.id || '',
                    create_time: fi.create_time || 0,
                    modify_time: fi.modify_time || 0,
                    file_id: fi.id || '',
                };
            }
        """)

        if blocks:
            logger.info(f"[kdocs] Extracted {len(blocks)} blocks")
            return {**(meta or {}), "blocks": blocks}
        else:
            logger.warning("[kdocs] No blocks extracted")
            return meta

    except Exception as e:
        logger.warning(f"[kdocs] Playwright extraction failed: {e}")
        return None
    finally:
        try:
            if cdp_conn:
                await cdp_conn[2].close()  # close page only
                await cdp_conn[0].close()  # disconnect CDP
            elif pw_instance:
                await browser.close()
                await pw_instance.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fetch_kdocs(url: str) -> Dict[str, Any]:
    """Fetch a KDocs document with multi-tier fallback.

    Tier 0: Playwright JS evaluate (CDP or launch)
    Tier 1: Jina Reader (zero-config)
    """
    from feedgrab.config import kdocs_download_images

    doc_token = parse_kdocs_url(url) or ""
    item_id = hashlib.md5(url.encode()).hexdigest()[:12]
    localize = kdocs_download_images()

    # ── Tier 0: Playwright ──
    raw = await _extract_via_playwright(url)

    if raw and raw.get("blocks"):
        blocks = raw["blocks"]

        # Deduplicate title: if first block is H1 matching __WPSENV__ title, skip it
        title_from_env = re.sub(r"[\u200b-\u206f\ufeff]", "", raw.get("title", "")).strip()
        if (blocks and blocks[0].get("type") == "heading"
                and blocks[0].get("level") == 1):
            first_h1 = blocks[0].get("text", "").strip()
            if first_h1 == title_from_env or first_h1 in title_from_env or title_from_env in first_h1:
                blocks = blocks[1:]

        md_content, images_info = _blocks_to_markdown(
            blocks, img_subdir=item_id, localize_images=localize,
        )

        # Timestamp conversion
        create_ts = raw.get("create_time", 0)
        modify_ts = raw.get("modify_time", 0)
        create_time = (
            datetime.fromtimestamp(create_ts).strftime("%Y-%m-%d %H:%M")
            if create_ts else ""
        )
        edit_time = (
            datetime.fromtimestamp(modify_ts).strftime("%Y-%m-%d %H:%M")
            if modify_ts else ""
        )

        title = raw.get("title", "")
        # Clean zero-width characters from title
        title = re.sub(r"[\u200b-\u206f\ufeff]", "", title).strip()

        return {
            "title": title,
            "content": md_content,
            "url": url,
            "author": raw.get("author", ""),
            "doc_token": doc_token,
            "word_count": len(md_content),
            "create_time": create_time,
            "edit_time": edit_time,
            "creator_id": raw.get("creator_id", ""),
            "images_info": images_info,
            "img_subdir": item_id,
            "tags": [],
        }

    # ── Tier 1: Jina Reader ──
    logger.info("[kdocs] Falling back to Jina Reader")
    from feedgrab.fetchers.jina import fetch_via_jina
    jina_data = await fetch_via_jina(url)

    return {
        "title": jina_data.get("title", ""),
        "content": jina_data.get("content", ""),
        "url": url,
        "author": "",
        "doc_token": doc_token,
        "word_count": len(jina_data.get("content", "")),
        "create_time": "",
        "edit_time": "",
        "creator_id": "",
        "images_info": [],
        "img_subdir": item_id,
        "tags": [],
    }


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

def download_kdocs_images(
    md_path: str, images_info: List[dict], img_subdir: str = "",
) -> None:
    """Download KDocs images to ``{md_dir}/attachments/{img_subdir}/``.

    Called after Markdown is saved. Content already contains relative paths
    like ``![alt](attachments/{img_subdir}/001_image.jpeg)``.
    """
    if not images_info:
        return

    md_dir = Path(md_path).parent
    att_dir = md_dir / "attachments"
    if img_subdir:
        att_dir = att_dir / img_subdir
    att_dir.mkdir(parents=True, exist_ok=True)

    from feedgrab.utils.http_client import get as http_get

    for info in images_info:
        url = info.get("url", "")
        fname = info.get("filename", "")
        if not url or not fname:
            continue

        fpath = att_dir / fname
        if fpath.exists() and fpath.stat().st_size > 0:
            logger.debug(f"[kdocs] 图片已存在：{fname}")
            continue

        try:
            resp = http_get(url, timeout=30)
            if resp.status_code == 200:
                fpath.write_bytes(resp.content)
                logger.info(f"[kdocs] 已下载：{fname}（{len(resp.content)} bytes）")
            else:
                logger.warning(f"[kdocs] 图片下载失败（{resp.status_code}）：{fname}")
        except Exception as e:
            logger.warning(f"[kdocs] 图片下载异常：{fname} — {e}")
