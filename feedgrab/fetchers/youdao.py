# -*- coding: utf-8 -*-
"""
Youdao Note (有道云笔记) fetcher.

Tier 0: JSON API (zero dependency, <1s)
Tier 1: Playwright iframe DOM extraction (fallback)
Tier 2: Jina Reader (last resort)
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from loguru import logger


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

_YOUDAO_DOMAINS = ("note.youdao.com",)

_API_BASE = "https://share.note.youdao.com/yws/api/note/{share_key}"
_API_PARAMS = {
    "sev": "j1",
    "editorType": "1",
    "editorVersion": "new-json-editor",
    "sec": "v1",
}


def is_youdao_url(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    return any(netloc.endswith(d) or netloc == d for d in _YOUDAO_DOMAINS)


def parse_youdao_url(url: str) -> Optional[str]:
    """Extract share key (id param) from Youdao Note URL."""
    qs = parse_qs(urlparse(url).query)
    ids = qs.get("id", [])
    return ids[0] if ids else None


def clean_youdao_url(url: str) -> str:
    """Keep only the ``id`` param, strip ``type``, ``_time``, etc."""
    parsed = urlparse(url)
    share_key = parse_youdao_url(url)
    if share_key:
        clean_q = urlencode({"id": share_key})
        return urlunparse(parsed._replace(query=clean_q, fragment=""))
    return urlunparse(parsed._replace(fragment=""))


# ---------------------------------------------------------------------------
# JSON content parser  (compressed numeric-key format → Markdown)
# ---------------------------------------------------------------------------

# Font-size thresholds for heading detection
_FS_H1 = 24
_FS_H2 = 20
_FS_H3 = 16


def _get_styles(elements: list) -> dict:
    """Collect style map from inline element style array (key "9")."""
    m: Dict[str, Any] = {}
    for s in elements:
        st = s.get("2", "")
        val = s.get("0", True)  # "b"/"i"/"u" have no "0", use True
        if st:
            m[st] = val
    return m


def _parse_inline(elements: list) -> str:
    """Convert inline element array (key "7") to Markdown text."""
    parts: List[str] = []
    for elem in elements:
        text = elem.get("8", "")
        if not text:
            continue
        styles = _get_styles(elem.get("9", []))

        # inline code
        if "code" in styles:
            text = f"`{text}`"
        # bold
        if "b" in styles:
            text = f"**{text}**"
        # italic
        if "i" in styles:
            text = f"*{text}*"
        # strikethrough
        if "s" in styles:
            text = f"~~{text}~~"

        parts.append(text)
    return "".join(parts)


def _detect_heading_level(elements: list) -> int:
    """Infer heading level from font-size in styles. Returns 0 if not a heading."""
    for elem in elements:
        styles = _get_styles(elem.get("9", []))
        fs = styles.get("fs")
        if isinstance(fs, (int, float)):
            if fs >= _FS_H1:
                return 1
            if fs >= _FS_H2:
                return 2
            if fs >= _FS_H3:
                return 3
    return 0


def _is_bold_heading(elements: list) -> bool:
    """Check if all non-empty elements are bold (common heading pattern)."""
    has_text = False
    for elem in elements:
        text = elem.get("8", "").strip()
        if not text:
            continue
        has_text = True
        styles = _get_styles(elem.get("9", []))
        if "b" not in styles:
            return False
    return has_text


def _make_code_fence(code_text: str) -> str:
    """Build code fence — minimum 4 backticks, longer if content requires."""
    longest = max((len(m.group()) for m in re.finditer(r"`+", code_text)), default=0)
    return "`" * max(4, longest + 1)


def _guess_image_ext(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
        if path.endswith(ext):
            return ext
    return ".png"


def _parse_sub_block(sub: dict, link_url: str = "") -> str:
    """Parse a type-2 sub-block (paragraph/text) into Markdown."""
    elements = sub.get("7", [])
    if not elements:
        return ""
    text = _parse_inline(elements)
    if link_url and text:
        text = f"[{text}]({link_url})"
    return text


def _parse_youdao_content(
    content_obj: dict,
    share_key: str,
    localize_images: bool = False,
    img_subdir: str = "",
) -> Tuple[str, List[dict]]:
    """Parse Youdao Note compressed JSON into Markdown.

    Returns (markdown_string, images_info_list).
    """
    top_blocks = content_obj.get("5", [])
    lines: List[str] = []
    images_info: List[dict] = []
    img_idx = 0
    prev_was_list = False

    for block in top_blocks:
        marker = block.get("6", "")
        cfg = block.get("4", {}) if isinstance(block.get("4"), dict) else {}
        subs = block.get("5", [])

        # --- Image block ---
        if marker == "im":
            prev_was_list = False
            img_url = cfg.get("u", "")
            if not img_url:
                continue
            if localize_images:
                ext = _guess_image_ext(img_url)
                fname = f"{img_idx:03d}_image{ext}"
                img_idx += 1
                images_info.append({"url": img_url, "filename": fname})
                rel = f"attachments/{img_subdir}/{fname}" if img_subdir else f"attachments/{fname}"
                lines.append(f"\n![image]({rel})\n")
            else:
                lines.append(f"\n![image]({img_url})\n")
            continue

        # --- List item ---
        if marker == "l":
            cfg_lt = cfg.get("lt", "unordered")
            cfg_ll = cfg.get("ll", 1)
            indent = "  " * max(0, cfg_ll - 1)
            bullet = "-" if cfg_lt == "unordered" else "1."

            # Collect text from sub-blocks (may contain links)
            parts: List[str] = []
            for s in subs:
                stype = s.get("2", "")
                if stype == "3":
                    # Link sub-block
                    link_cfg = s.get("4", {}) if isinstance(s.get("4"), dict) else {}
                    href = link_cfg.get("hf", "")
                    for ss in s.get("5", []):
                        t = _parse_sub_block(ss, link_url=href)
                        if t:
                            parts.append(t)
                elif stype == "2":
                    t = _parse_sub_block(s)
                    if t:
                        parts.append(t)

            text = "".join(parts).strip()
            if text:
                if not prev_was_list:
                    lines.append("")  # blank line before list start
                lines.append(f"{indent}{bullet} {text}")
                prev_was_list = True
            continue

        # --- Normal paragraph / heading / link ---
        prev_was_list = False
        parts_p: List[str] = []
        for s in subs:
            stype = s.get("2", "")
            if stype == "3":
                link_cfg = s.get("4", {}) if isinstance(s.get("4"), dict) else {}
                href = link_cfg.get("hf", "")
                for ss in s.get("5", []):
                    t = _parse_sub_block(ss, link_url=href)
                    if t:
                        parts_p.append(t)
            elif stype == "2":
                t = _parse_sub_block(s)
                if t:
                    parts_p.append(t)

        text = "".join(parts_p).strip()
        if not text:
            lines.append("")
            continue

        # Detect heading via font-size in first sub-block
        first_sub = subs[0] if subs else {}
        elems = first_sub.get("7", [])
        hlevel = _detect_heading_level(elems)
        if hlevel > 0 and _is_bold_heading(elems):
            prefix = "#" * hlevel
            # Strip bold markers from heading text
            clean = text.replace("**", "")
            lines.append(f"\n{prefix} {clean}\n")
        else:
            lines.append(text)
            lines.append("")

    result = "\n".join(lines).strip()
    # Clean up excessive blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result, images_info


# ---------------------------------------------------------------------------
# Tier 0 — JSON API
# ---------------------------------------------------------------------------

def _fetch_via_api(share_key: str) -> Optional[dict]:
    """Fetch note content via Youdao public JSON API."""
    from feedgrab.utils.http_client import get as http_get

    url = _API_BASE.format(share_key=share_key)
    try:
        resp = http_get(url, params=_API_PARAMS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"[youdao] API returned {resp.status_code}")
            return None
        data = resp.json()
        content_raw = data.get("content", "")
        content_obj = json.loads(content_raw) if content_raw else {}
        return {
            "title": data.get("tl", ""),
            "create_time": data.get("ct", 0),
            "modify_time": data.get("mt", 0),
            "page_views": data.get("pv", 0),
            "size": data.get("sz", 0),
            "content": content_obj,
        }
    except Exception as e:
        logger.warning(f"[youdao] API 请求失败：{e}")
        return None


# ---------------------------------------------------------------------------
# Tier 1 — Playwright iframe DOM extraction
# ---------------------------------------------------------------------------

async def _extract_via_playwright(url: str) -> Optional[dict]:
    """Fallback: extract content from Youdao share page via Playwright."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("[youdao] 未安装 Playwright，跳过 Tier 1")
        return None

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    try:
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # Wait for iframe
        iframe_el = await page.wait_for_selector(
            "iframe#content-body", timeout=10000
        )
        frame = await iframe_el.content_frame()
        if not frame:
            return None

        await frame.wait_for_selector(
            ".bulb-editor", timeout=10000
        )
        await page.wait_for_timeout(2000)

        title = await page.evaluate(
            "() => document.title.replace(/\\.note$/, '').trim()"
        )

        # Extract text from all divs inside the editor
        content = await frame.evaluate("""
            () => {
                const editor = document.querySelector('.bulb-editor');
                if (!editor) return '';
                const lines = [];
                for (const div of editor.children) {
                    const imgs = div.querySelectorAll('img');
                    for (const img of imgs) {
                        if (img.src) lines.push('![image](' + img.src + ')');
                    }
                    const text = div.textContent?.trim();
                    if (text) lines.push(text);
                    else lines.push('');
                }
                return lines.join('\\n');
            }
        """)

        return {"title": title, "content": content} if content else None
    except Exception as e:
        logger.warning(f"[youdao] Playwright 提取失败：{e}")
        return None
    finally:
        await browser.close()
        await pw.stop()


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

async def fetch_youdao(url: str) -> Dict[str, Any]:
    """Fetch Youdao Note with multi-tier fallback.

    Tier 0: JSON API  →  Tier 1: Playwright  →  Tier 2: Jina Reader
    """
    from feedgrab.config import youdao_download_images

    url = clean_youdao_url(url)
    share_key = parse_youdao_url(url)
    item_id = hashlib.md5(url.encode()).hexdigest()[:12]
    localize = youdao_download_images()

    if not share_key:
        raise ValueError(f"Cannot extract share key from URL: {url}")

    empty_result = {
        "title": "", "content": "", "url": url, "author": "",
        "share_key": share_key, "page_views": 0,
        "create_time": "", "edit_time": "",
        "images_info": [], "img_subdir": item_id, "tags": [],
    }

    # Tier 0: JSON API
    logger.info(f"[youdao] Tier 0: API for {share_key}")
    raw = _fetch_via_api(share_key)
    if raw and raw.get("content"):
        md_content, images_info = _parse_youdao_content(
            raw["content"], share_key, localize, item_id,
        )
        if md_content:
            ct = raw.get("create_time", 0)
            mt = raw.get("modify_time", 0)
            create_str = datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M") if ct else ""
            edit_str = datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M") if mt else ""
            return {
                "title": raw.get("title", ""),
                "content": md_content,
                "url": url,
                "author": "",
                "share_key": share_key,
                "page_views": raw.get("page_views", 0),
                "create_time": create_str,
                "edit_time": edit_str,
                "images_info": images_info,
                "img_subdir": item_id,
                "tags": [],
            }

    # Tier 1: Playwright
    logger.info("[youdao] Tier 1: Playwright fallback")
    pw_data = await _extract_via_playwright(url)
    if pw_data and pw_data.get("content"):
        return {**empty_result, "title": pw_data.get("title", ""), "content": pw_data["content"]}

    # Tier 2: Jina Reader
    logger.info("[youdao] Tier 2: Jina Reader fallback")
    from feedgrab.fetchers.jina import fetch_via_jina
    jina_data = fetch_via_jina(url)
    return {**empty_result, "title": jina_data.get("title", ""), "content": jina_data.get("content", "")}


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

def download_youdao_images(
    md_path: str, images_info: List[dict], img_subdir: str = "",
) -> None:
    """Download Youdao Note images to {md_dir}/attachments/{img_subdir}/."""
    if not images_info:
        return
    md_dir = Path(md_path).parent
    att_dir = md_dir / "attachments" / img_subdir if img_subdir else md_dir / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)

    from feedgrab.utils.http_client import get as http_get

    for info in images_info:
        url = info.get("url", "")
        fname = info.get("filename", "")
        if not url or not fname:
            continue
        fpath = att_dir / fname
        if fpath.exists() and fpath.stat().st_size > 0:
            continue
        try:
            resp = http_get(url, timeout=30)
            if resp.status_code == 200:
                fpath.write_bytes(resp.content)
                logger.info(f"[youdao] 已下载：{fname}（{len(resp.content)} bytes）")
            else:
                logger.warning(f"[youdao] 图片下载状态码 {resp.status_code}：{fname}")
        except Exception as e:
            logger.warning(f"[youdao] 图片下载异常：{fname} — {e}")
