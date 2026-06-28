# -*- coding: utf-8 -*-
"""
Sogou WeChat article search — search WeChat public account articles by keyword.

Uses Sogou's WeChat search (weixin.sogou.com) to discover articles,
then fetches each article's full content via browser and converts to Markdown.

Provides:
    - feedgrab mpweixin-so <keyword>

Data available from Sogou search results:
    - title, summary, author (公众号名), publish_time, thumbnail
    - NOT available: read count, likes, comments (WeChat internal only)

Sogou pagination: 10 results per page, up to ~10 pages (100 results).
"""

import math
import re
import time
import urllib.parse
from datetime import datetime
from loguru import logger
from typing import Dict, Any, List, Optional

from feedgrab.config import get_stealth_headers
from feedgrab.utils import http_client


# ---------------------------------------------------------------------------
# Sogou search (HTTP)
# ---------------------------------------------------------------------------

def _sogou_search(keyword: str, page: int = 1) -> List[Dict[str, Any]]:
    """Search WeChat articles via Sogou (one page, 10 results max).

    Returns list of dicts with: title, summary, author, timestamp, sogou_url, thumbnail.
    """
    encoded = urllib.parse.quote(keyword)
    url = (
        f"https://weixin.sogou.com/weixin?"
        f"type=2&query={encoded}&ie=utf8&page={page}"
    )

    try:
        resp = http_client.get(url, headers=get_stealth_headers(), timeout=15)
        html = resp.text
    except Exception as e:
        logger.warning(f"[mpweixin-so] Sogou request failed (page {page}): {e}")
        return []

    if "antispider" in html.lower() or "用户您好" in html:
        logger.warning("[mpweixin-so] Sogou triggered anti-bot verification")
        return []

    return _parse_sogou_results(html)


def _sogou_search_multi(keyword: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Fetch multiple pages from Sogou to reach max_results."""
    pages_needed = math.ceil(max_results / 10)
    all_results: List[Dict[str, Any]] = []

    for page_num in range(1, pages_needed + 1):
        if len(all_results) >= max_results:
            break
        logger.info(f"[mpweixin-so] Fetching search page {page_num}/{pages_needed}")
        page_results = _sogou_search(keyword, page=page_num)
        if not page_results:
            break
        all_results.extend(page_results)
        if page_num < pages_needed:
            time.sleep(1.5)  # Rate limit between pages

    return all_results[:max_results]


def _parse_sogou_results(html: str) -> List[Dict[str, Any]]:
    """Parse Sogou WeChat search results from HTML (single page)."""
    results = []

    li_blocks = re.findall(
        r'<li\s+id="sogou_vr_\d+_box_\d+"[^>]*>(.*?)</li>',
        html,
        re.DOTALL,
    )

    for block in li_blocks:
        item = {}

        # Title
        title_m = re.search(r'<h3>\s*<a[^>]*>(.*?)</a>\s*</h3>', block, re.DOTALL)
        if title_m:
            title = title_m.group(1)
            title = re.sub(r'<[^>]+>', '', title)
            title = re.sub(r'<!--.*?-->', '', title)
            title = title.replace("&ldquo;", "\u201c").replace("&rdquo;", "\u201d")
            title = title.replace("&mdash;", "\u2014").replace("&amp;", "&")
            item["title"] = title.strip()

        # Sogou redirect URL
        url_m = re.search(r'<h3>\s*<a[^>]*href="([^"]+)"', block)
        if url_m:
            sogou_url = url_m.group(1)
            if sogou_url.startswith("/"):
                sogou_url = f"https://weixin.sogou.com{sogou_url}"
            item["sogou_url"] = sogou_url

        # Summary
        summary_m = re.search(r'<p\s+class="txt-info"[^>]*>(.*?)</p>', block, re.DOTALL)
        if summary_m:
            summary = summary_m.group(1)
            summary = re.sub(r'<[^>]+>', '', summary)
            summary = re.sub(r'<!--.*?-->', '', summary)
            summary = summary.replace("&ldquo;", "\u201c").replace("&rdquo;", "\u201d")
            summary = summary.replace("&mdash;", "\u2014").replace("&amp;", "&")
            item["summary"] = summary.strip()

        # Author (公众号名)
        author_m = re.search(r'<span\s+class="all-time-y2">(.*?)</span>', block)
        if author_m:
            item["author"] = author_m.group(1).strip()

        # Publish timestamp
        time_m = re.search(r"timeConvert\('(\d+)'\)", block)
        if time_m:
            ts = int(time_m.group(1))
            item["timestamp"] = ts
            item["publish_date"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

        # Thumbnail
        thumb_m = re.search(r'<img\s+src="(//img01\.sogoucdn\.com[^"]+)"', block)
        if thumb_m:
            item["thumbnail"] = f"https:{thumb_m.group(1)}"

        if item.get("title") and item.get("sogou_url"):
            results.append(item)

    return results


# ---------------------------------------------------------------------------
# Sogou redirect resolution (browser)
# ---------------------------------------------------------------------------

async def _resolve_sogou_redirect(page, sogou_url: str) -> Optional[str]:
    """Follow Sogou redirect via a new tab in the existing browser context.

    The context already has Sogou cookies from visiting the search page.
    """
    try:
        new_page = await page.context.new_page()
        try:
            await new_page.goto(sogou_url, wait_until="domcontentloaded", timeout=20000)
            await new_page.wait_for_timeout(4000)
            final_url = new_page.url

            if "mp.weixin.qq.com" in final_url:
                logger.debug(f"[mpweixin-so] Redirect resolved: {final_url[:80]}")
                return final_url

            if "antispider" in final_url:
                logger.debug("[mpweixin-so] Hit antispider, waiting...")
                try:
                    await new_page.wait_for_url("**/mp.weixin.qq.com/**", timeout=10000)
                    final_url = new_page.url
                    if "mp.weixin.qq.com" in final_url:
                        return final_url
                except Exception:
                    pass

            content = await new_page.content()
            wx_m = re.search(r'(https?://mp\.weixin\.qq\.com/s[^\s"\'<>]+)', content)
            if wx_m:
                return wx_m.group(1)

            logger.debug(f"[mpweixin-so] Redirect landed on: {final_url[:80]}")
        finally:
            await new_page.close()
    except Exception as e:
        logger.debug(f"[mpweixin-so] Browser redirect failed: {e}")

    return None


# ---------------------------------------------------------------------------
# WeChat article HTML → Markdown conversion (markdownify + code block handling)
# ---------------------------------------------------------------------------

def _preprocess_wechat_html(html: str) -> tuple:
    """Pre-process WeChat HTML before markdownify conversion.

    Handles:
    - Lazy-loaded images (data-src → src)
    - SVG/tracking pixel filtering
    - WeChat code blocks (.code-snippet__fix) → placeholder
    - Script/style removal

    Returns:
        (cleaned_html, code_blocks) where code_blocks is a list of
        (language, code_text) tuples for placeholder restoration.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Remove script/style
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()

    # Remove WeChat noise elements
    for sel in [".qr_code_pc", ".reward_area", "#js_pc_qr_code"]:
        for el in soup.select(sel):
            el.decompose()

    # Fix lazy-loaded images: data-src → src, filter SVG/tiny
    for img in soup.find_all("img"):
        data_src = img.get("data-src")
        if data_src:
            img["src"] = data_src

        src = img.get("src", "")
        # Skip SVG icons and base64 images
        if "wx_fmt=svg" in src or src.startswith("data:image"):
            img.decompose()
            continue
        # Skip tiny tracking pixels
        data_w = img.get("data-w")
        if data_w and data_w.isdigit() and int(data_w) < 20:
            img.decompose()

    # Handle WeChat code blocks (.code-snippet__fix) — placeholder strategy
    code_blocks = []
    for snippet in soup.select(".code-snippet__fix"):
        # Remove line number elements
        for line_idx in snippet.select(".code-snippet__line-index"):
            line_idx.decompose()

        # Extract language from pre[data-lang]
        pre = snippet.find("pre", attrs={"data-lang": True})
        lang = pre.get("data-lang", "") if pre else ""

        # Extract code text from all <code> tags
        code_parts = []
        for code_tag in snippet.find_all("code"):
            # Convert <br> tags to newlines before extracting text
            for br in code_tag.find_all("br"):
                br.replace_with("\n")
            text = code_tag.get_text()
            # Filter CSS counter leak lines
            if re.match(r'^[ce]?ounter\(line', text):
                continue
            code_parts.append(text)

        code_text = "\n".join(code_parts)
        placeholder = f"WECHAT-CODEBLOCK-{len(code_blocks)}"
        code_blocks.append((lang, code_text))

        # Replace snippet with placeholder paragraph
        snippet.replace_with(soup.new_string(f"\n\n{placeholder}\n\n"))

    # Also handle standard <pre> code blocks not inside .code-snippet__fix
    for pre in soup.find_all("pre"):
        lang = pre.get("data-lang", "")
        code_tag = pre.find("code") or pre
        # Convert <br> tags to newlines before extracting text
        for br in code_tag.find_all("br"):
            br.replace_with("\n")
        code_text = code_tag.get_text()

        placeholder = f"WECHAT-CODEBLOCK-{len(code_blocks)}"
        code_blocks.append((lang, code_text))
        pre.replace_with(soup.new_string(f"\n\n{placeholder}\n\n"))

    # Handle video elements: span.video_iframe / mpvideo / video containers
    # Replace with [▶ 视频](mp4_url) links before markdownify
    for container in soup.select("span.video_iframe, mpvideo"):
        # Try to find a <video> with direct MP4 src inside or nearby
        video_tag = container.find("video", src=True)
        video_src = video_tag["src"] if video_tag else ""

        if not video_src:
            # Fallback: check parent for <video>
            parent = container.parent
            if parent:
                video_tag = parent.find("video", src=True)
                video_src = video_tag["src"] if video_tag else ""

        if video_src:
            link_tag = soup.new_tag("p")
            a_tag = soup.new_tag("a", href=video_src)
            a_tag.string = "▶ 视频"
            link_tag.append(a_tag)
            container.replace_with(link_tag)
        else:
            # No direct MP4 URL — just remove the broken placeholder
            container.decompose()

    # Remove standalone <video> tags that were already handled above or are orphaned
    for video_tag in soup.find_all("video"):
        src = video_tag.get("src", "")
        if src:
            link_tag = soup.new_tag("p")
            a_tag = soup.new_tag("a", href=src)
            a_tag.string = "▶ 视频"
            link_tag.append(a_tag)
            video_tag.replace_with(link_tag)
        else:
            video_tag.decompose()

    # Remove "视频加载失败" error text from video poster containers
    for el in soup.select(".js_video_poster"):
        el.decompose()
    # Also remove common video error text nodes
    for el in soup.find_all(string=re.compile(r"视频加载失败|请刷新页面再试")):
        if el.parent and el.parent.name in ("p", "div", "span"):
            el.parent.decompose()

    return str(soup), code_blocks


def _html_to_markdown(html: str) -> str:
    """Convert WeChat article HTML to Markdown.

    Uses markdownify for robust conversion with pre-processing for
    WeChat-specific elements (lazy images, code snippets).
    """
    if not html:
        return ""

    import markdownify

    # Pre-process: fix images, extract code blocks as placeholders
    cleaned_html, code_blocks = _preprocess_wechat_html(html)

    # Convert with markdownify
    md = markdownify.markdownify(
        cleaned_html,
        heading_style="ATX",
        bullets="-",
        convert=[
            "p", "h1", "h2", "h3", "h4", "h5", "h6",
            "strong", "em", "b", "i", "a", "img",
            "ul", "ol", "li", "blockquote", "br", "hr",
            "table", "thead", "tbody", "tr", "th", "td",
            "pre", "code", "del", "sub", "sup",
        ],
    )

    # Restore code block placeholders → fenced code blocks
    # Replace in reverse order to avoid prefix collision
    # (e.g. "WECHAT-CODEBLOCK-1" matching inside "WECHAT-CODEBLOCK-10")
    for idx in range(len(code_blocks) - 1, -1, -1):
        lang, code_text = code_blocks[idx]
        placeholder = f"WECHAT-CODEBLOCK-{idx}"
        # Ensure newlines around fences — markdownify may strip placeholder padding
        fence = f"\n\n````{lang}\n{code_text}\n````\n\n"
        md = md.replace(placeholder, fence)

    # Clean up whitespace
    md = md.replace('\u00a0', ' ')
    md = re.sub(r'\n{4,}', '\n\n\n', md)
    md = re.sub(r'[ \t]+$', '', md, flags=re.MULTILINE)
    md = md.strip()

    return md


async def _fetch_wechat_article_via_browser(context, wx_url: str) -> Dict[str, Any]:
    """Fetch WeChat article content using the shared browser context.

    Uses the unified WeChat extraction (rich HTML + full metadata).
    Returns dict with: title, content, author, url, cover_image, publish_date, etc.
    """
    from feedgrab.fetchers.browser import (
        WECHAT_ARTICLE_JS_EVALUATE, _build_wechat_result,
    )

    new_page = await context.new_page()
    try:
        await new_page.goto(wx_url, wait_until="domcontentloaded", timeout=20000)
        await new_page.wait_for_timeout(2000)

        try:
            await new_page.wait_for_selector("#js_content", timeout=5000)
        except Exception:
            pass

        data = await new_page.evaluate(WECHAT_ARTICLE_JS_EVALUATE)

        if not data or not data.get("hasContent"):
            # Fallback: grab raw text
            text = await new_page.evaluate("() => document.body.innerText")
            page_title = await new_page.title()
            return {
                "title": page_title or "",
                "content": (text or "").strip(),
                "author": "",
                "url": wx_url,
                "cover_image": "",
                "publish_date": "",
                "summary": "",
                "tags": [],
                "original_url": "",
            }

        return _build_wechat_result(data, wx_url, md_converter=_html_to_markdown)
    finally:
        await new_page.close()


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def _save_article(article_data: Dict[str, Any], item: Dict[str, Any], keyword: str):
    """Save a WeChat article as Markdown with full metadata."""
    from feedgrab.schema import from_wechat
    from feedgrab.utils.storage import save_to_markdown

    # Fallback title from search results when browser extraction failed
    if not article_data.get("title") and item.get("title"):
        article_data["title"] = item["title"]

    # Enrich with search metadata (fallback only — article page data takes priority)
    if item.get("author") and not article_data.get("author"):
        article_data["author"] = item["author"]
    if item.get("publish_date") and not article_data.get("publish_date"):
        article_data["publish_date"] = item["publish_date"]
    # Sogou thumbnail as fallback when article page cover not available
    if item.get("thumbnail") and not article_data.get("cover_image"):
        article_data["thumbnail"] = item["thumbnail"]
    if item.get("summary") and not article_data.get("summary"):
        article_data["summary"] = item["summary"]
    article_data["search_keyword"] = keyword

    content = from_wechat(article_data)
    content.category = f"search_sogou/{keyword}"
    save_to_markdown(content)


def _save_search_item(item: Dict[str, Any], keyword: str):
    """Save search metadata only (when full content fetch fails)."""
    article_data = {
        "title": item.get("title", ""),
        "author": item.get("author", ""),
        "url": item.get("wechat_url", item.get("sogou_url", "")),
        "content": item.get("summary", ""),
        "publish_date": item.get("publish_date", ""),
        "thumbnail": item.get("thumbnail", ""),
        "summary": item.get("summary", ""),
        "search_keyword": keyword,
    }
    _save_article(article_data, item, keyword)
    logger.info(f"[mpweixin-so] Saved (metadata only): {item.get('title', '')[:50]}")


# ---------------------------------------------------------------------------
# Browser-based Sogou search (unified fingerprint, no HTTP↔browser split)
# ---------------------------------------------------------------------------

async def _sogou_search_browser(
    page, keyword: str, max_results: int = 10
) -> List[Dict[str, Any]]:
    """Search via the browser that already has Sogou cookies.

    The first search page is already loaded (cookies acquired).
    Extracts results from the rendered DOM via page.content(), then
    navigates to subsequent pages if needed.

    Returns list of parsed results (same format as _parse_sogou_results).
    """
    pages_needed = math.ceil(max_results / 10)
    all_results: List[Dict[str, Any]] = []

    for page_num in range(1, pages_needed + 1):
        if len(all_results) >= max_results:
            break

        if page_num > 1:
            # Navigate to next search page
            encoded = urllib.parse.quote(keyword)
            next_url = (
                f"https://weixin.sogou.com/weixin?"
                f"type=2&query={encoded}&ie=utf8&page={page_num}"
            )
            await page.goto(
                next_url, wait_until="domcontentloaded", timeout=15000
            )
            await page.wait_for_timeout(2000)

        html = await page.content()

        if "antispider" in html.lower() or "用户您好" in html:
            logger.warning("[mpweixin-so] Sogou triggered anti-bot verification")
            break

        page_results = _parse_sogou_results(html)
        if not page_results:
            logger.info(
                f"[mpweixin-so] No results on page {page_num}, stopping"
            )
            break

        all_results.extend(page_results)
        logger.info(
            f"[mpweixin-so] Page {page_num}: {len(page_results)} results "
            f"(total {len(all_results)})"
        )

        if page_num < pages_needed and len(all_results) < max_results:
            time.sleep(1.5)  # Rate limit between pages

    return all_results[:max_results]


async def search_wechat_articles(
    keyword: str,
    max_results: int = 10,
    fetch_content: bool = True,
    delay: float = 3.0,
) -> Dict[str, Any]:
    """Search and fetch WeChat articles via Sogou.

    Args:
        keyword: Search keyword
        max_results: Maximum number of results (up to 100, 10 per Sogou page)
        fetch_content: Whether to fetch full article content
        delay: Delay between fetching each article (seconds)

    Returns:
        Dict with: keyword, total, fetched, skipped, failed, articles
    """
    from feedgrab.utils.dedup import load_index, save_index, has_item, add_item, item_id_from_url

    logger.info(f"[mpweixin-so] Searching: {keyword} (max {max_results})")

    # Lightweight mode: HTTP search only (no browser, no content fetch)
    if not fetch_content:
        results = _sogou_search_multi(keyword, max_results=max_results)
        return {
            "keyword": keyword,
            "total": len(results),
            "fetched": len(results),
            "skipped": 0,
            "failed": 0,
            "articles": results,
        }

    # Full mode: browser-based search → redirect → article fetch
    # Single browser context for entire pipeline (unified fingerprint)
    browser = None
    context = None
    page = None
    pw = None
    results: List[Dict[str, Any]] = []
    try:
        from feedgrab.fetchers.browser import (
            get_async_playwright, stealth_launch, get_stealth_context_options,
            get_stealth_engine_name, setup_resource_blocking, generate_referer,
        )

        async_pw = get_async_playwright()
        logger.info(f"[mpweixin-so] Stealth engine: {get_stealth_engine_name()}")
        pw = await async_pw().start()
        browser = await stealth_launch(pw, headless=False)
        context = await browser.new_context(**get_stealth_context_options())
        page = await context.new_page()
        await setup_resource_blocking(context)

        # Navigate to first search page (acquires cookies + extracts results)
        encoded = urllib.parse.quote(keyword)
        search_url = f"https://weixin.sogou.com/weixin?type=2&query={encoded}&ie=utf8&page=1"
        await page.goto(
            search_url, wait_until="domcontentloaded", timeout=15000,
            referer=generate_referer(search_url),
        )
        await page.wait_for_timeout(2000)

        # Extract search results from browser-rendered page (+ pagination)
        results = await _sogou_search_browser(page, keyword, max_results)
        logger.info(f"[mpweixin-so] 浏览器搜索找到 {len(results)} 条结果")
    except ImportError:
        logger.warning("[mpweixin-so] 未安装 Playwright，改用 HTTP 搜索")
        results = _sogou_search_multi(keyword, max_results=max_results)
    except Exception as e:
        logger.warning(f"[mpweixin-so] 浏览器搜索失败：{e}，改用 HTTP 搜索")
        results = _sogou_search_multi(keyword, max_results=max_results)

    if not results:
        logger.warning("[mpweixin-so] 未找到结果")
        # Cleanup browser if launched
        if context:
            await context.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
        return {
            "keyword": keyword,
            "total": 0,
            "fetched": 0,
            "skipped": 0,
            "failed": 0,
            "articles": [],
        }

    index = load_index(platform="mpweixin")
    fetched = 0
    skipped = 0
    failed = 0
    articles = []

    for i, item in enumerate(results):
        title = item.get("title", "untitled")
        sogou_url = item.get("sogou_url", "")

        logger.info(f"[mpweixin-so] [{i+1}/{len(results)}] {title[:50]}")

        # Resolve Sogou redirect → real mp.weixin.qq.com URL
        wx_url = None
        if page:
            wx_url = await _resolve_sogou_redirect(page, sogou_url)

        if wx_url:
            item["wechat_url"] = wx_url

            item_id = item_id_from_url(wx_url)
            if has_item(item_id, index):
                logger.info(f"[mpweixin-so] 已抓取过，跳过：{title[:40]}")
                skipped += 1
                continue

            # Fetch full article directly via our browser (rich Markdown)
            try:
                article_data = await _fetch_wechat_article_via_browser(context, wx_url)
                _save_article(article_data, item, keyword)

                add_item(item_id, wx_url, index)
                articles.append(item)
                fetched += 1
                logger.info(f"[mpweixin-so] 已保存：{title[:50]}")
            except Exception as e:
                logger.warning(f"[mpweixin-so] 抓取失败：{title[:40]} — {e}")
                _save_search_item(item, keyword)
                articles.append(item)
                failed += 1
        else:
            logger.warning(f"[mpweixin-so] 无法解析文章 URL：{title[:40]}")
            _save_search_item(item, keyword)
            articles.append(item)
            failed += 1

        if i < len(results) - 1:
            time.sleep(delay)

    # Cleanup
    if context:
        await context.close()
    if browser:
        await browser.close()
    if pw:
        await pw.stop()

    save_index(index, platform="mpweixin")

    return {
        "keyword": keyword,
        "total": len(results),
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "articles": articles,
    }
