# -*- coding: utf-8 -*-
"""
Zhihu keyword search — discover answers/articles by keyword with engagement ranking.

Usage:
    feedgrab zhihu-so "openclaw"
    feedgrab zhihu-so "AI Agent" --days 7 --sort hot --limit 20
    feedgrab zhihu-so "openclaw,ChatGPT" --merge

Architecture:
    1. Tier 0: API v4 search (needs Cookie)
    2. Tier 1: Playwright browser search (__INITIAL_STATE__ extraction)
    3. Sort by upvotes descending → summary table (MD + CSV)
    4. Optionally save individual answer .md files (--save)

Output:
    Zhihu/search/{sort}/{keyword}_{date}.md    ← summary table (always)
    Zhihu/search/{sort}/{keyword}_{date}.csv   ← CSV table (always)
    Zhihu/search/{sort}/{keyword}/{item}.md    ← individual answers (optional)
"""

import asyncio
import csv
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Output path helpers
# ---------------------------------------------------------------------------

def _resolve_output_base() -> Path:
    vault = os.getenv("OBSIDIAN_VAULT", "").strip()
    output_dir = os.getenv("OUTPUT_DIR", "").strip()
    return Path(vault or output_dir or "output")


def _clean_title(text: str, max_len: int = 40) -> str:
    """Truncate and clean text for table display."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len] + "…"
    return text


# ---------------------------------------------------------------------------
# Tier 0 — API v4 search
# ---------------------------------------------------------------------------

_API_SEARCH = "https://www.zhihu.com/api/v4/search_v3"


def _search_via_api(
    keyword: str,
    cookies: Dict[str, str],
    max_results: int = 50,
    sort: str = "hot",
) -> List[Dict[str, Any]]:
    """Search Zhihu via API v4. Returns list of result dicts."""
    from feedgrab.utils.http_client import get as http_get
    from feedgrab.config import get_user_agent

    headers = {
        "User-Agent": get_user_agent(),
        "Referer": "https://www.zhihu.com/",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*",
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
    }

    results: List[Dict[str, Any]] = []
    offset = 0
    limit = 20  # Zhihu API page size

    while len(results) < max_results:
        params = {
            "q": keyword,
            "t": "content",
            "offset": str(offset),
            "limit": str(limit),
        }
        # sort: default is relevance, "sort" param may not be supported
        # but we try anyway
        if sort == "new":
            params["sort"] = "created_time"

        try:
            resp = http_get(_API_SEARCH, headers=headers, params=params, timeout=15)
            if resp.status_code != 200:
                logger.info(f"[zhihu-so] API search returned {resp.status_code}")
                break
            data = resp.json()
        except Exception as e:
            logger.info(f"[zhihu-so] API search failed: {e}")
            break

        items = data.get("data", [])
        if not items:
            break

        for item in items:
            parsed = _parse_search_item(item)
            if parsed:
                results.append(parsed)

        # Check pagination
        paging = data.get("paging", {})
        if paging.get("is_end", True):
            break
        offset += limit

    return results[:max_results]


def _parse_search_item(item: dict) -> Optional[Dict[str, Any]]:
    """Parse a single search result item into a normalized dict."""
    item_type = item.get("type", "")
    obj = item.get("object", item)

    if item_type in ("answer", "search_result"):
        question = obj.get("question", {})
        author = obj.get("author", {})
        return {
            "type": "answer",
            "id": str(obj.get("id", "")),
            "title": question.get("title", obj.get("title", "")),
            "excerpt": _strip_html(obj.get("excerpt", obj.get("content", ""))),
            "url": f"https://www.zhihu.com/question/{question.get('id', '')}/answer/{obj.get('id', '')}",
            "author": author.get("name", "") if isinstance(author, dict) else "",
            "upvotes": obj.get("voteup_count", 0),
            "comments": obj.get("comment_count", 0),
            "views": question.get("visit_count", 0) if isinstance(question, dict) else 0,
            "created_time": obj.get("created_time", 0),
            "date": _ts_to_date(obj.get("created_time", 0)),
        }
    elif item_type == "article":
        author = obj.get("author", {})
        return {
            "type": "article",
            "id": str(obj.get("id", "")),
            "title": obj.get("title", ""),
            "excerpt": _strip_html(obj.get("excerpt", obj.get("content", ""))),
            "url": f"https://zhuanlan.zhihu.com/p/{obj.get('id', '')}",
            "author": author.get("name", "") if isinstance(author, dict) else "",
            "upvotes": obj.get("voteup_count", 0),
            "comments": obj.get("comment_count", 0),
            "views": 0,
            "created_time": obj.get("created", 0),
            "date": _ts_to_date(obj.get("created", 0)),
        }

    return None


# ---------------------------------------------------------------------------
# Tier 1 — Browser XHR search (reuses frontend x-zse-96 signing)
# ---------------------------------------------------------------------------

async def _search_via_xhr(
    keyword: str,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Search Zhihu via browser XHR (auto-signed by frontend JS)."""
    from feedgrab.fetchers.zhihu import _get_xhr_page, _close_xhr_page, _XHR_GET_JS

    page = await _get_xhr_page()
    if not page:
        return []

    results: List[Dict[str, Any]] = []
    offset = 0
    limit = 20

    try:
        while len(results) < max_results:
            from urllib.parse import quote
            path = (
                f"/api/v4/search_v3?q={quote(keyword)}&t=content"
                f"&offset={offset}&limit={limit}"
            )
            data = await page.evaluate(_XHR_GET_JS, path)
            if not data or not data.get("ok"):
                break

            body = data["data"]
            items = body.get("data", [])
            if not items:
                break

            for item in items:
                parsed = _parse_search_item(item)
                if parsed:
                    results.append(parsed)

            paging = body.get("paging", {})
            if paging.get("is_end", True):
                break
            offset += limit
    except Exception as e:
        logger.warning(f"[zhihu-so] XHR search error: {e}")

    return results[:max_results]


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _ts_to_date(ts: int) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# Tier 2 — Playwright browser search (reuses cached CDP page)
# ---------------------------------------------------------------------------

async def _search_via_playwright(
    keyword: str,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Search Zhihu via Playwright browser, extract from DOM."""
    from feedgrab.fetchers.zhihu import _get_playwright_page
    from urllib.parse import quote

    search_url = f"https://www.zhihu.com/search?type=content&q={quote(keyword)}"

    pw, browser, page, is_cdp = await _get_playwright_page()
    if not page:
        return []

    results: List[Dict[str, Any]] = []
    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Extract __INITIAL_STATE__
        state = await page.evaluate("""() => {
            try {
                return JSON.parse(JSON.stringify(window.__INITIAL_STATE__));
            } catch(e) { return null; }
        }""")

        if state:
            results = _parse_search_state(state, max_results)
            logger.info(f"[zhihu-so] Playwright: extracted {len(results)} results from __INITIAL_STATE__")

        # Fallback: DOM extraction if state is empty
        if not results:
            dom_results = await page.evaluate("""() => {
                const items = document.querySelectorAll('.SearchResult-Card, .List-item');
                const seen = new Set();
                const out = [];
                for (const el of items) {
                    const titleEl = el.querySelector('h2 a, .ContentItem-title a');
                    if (!titleEl) continue;
                    const url = titleEl.href || '';
                    if (!url || seen.has(url)) continue;
                    seen.add(url);
                    const excerptEl = el.querySelector('.RichContent-inner, .content');
                    const authorEl = el.querySelector('.AuthorInfo-name a, .AuthorInfo-name');
                    const voteEl = el.querySelector('.VoteButton--up');
                    const btns = el.querySelectorAll('.ContentItem-actions button, .ContentItem-action');
                    const btnTexts = Array.from(btns).map(b => b.textContent.trim().replace(/\\u200b/g, ''));
                    let upvotes = 0, comments = 0;
                    for (const t of btnTexts) {
                        const num = parseInt(t.replace(/[^0-9]/g, ''), 10) || 0;
                        if (t.includes('赞同')) upvotes = num;
                        else if (t.includes('评论')) comments = num;
                    }
                    if (!upvotes && voteEl) {
                        upvotes = parseInt(voteEl.textContent.replace(/[^0-9]/g, ''), 10) || 0;
                    }
                    out.push({
                        title: titleEl.textContent.trim(),
                        url: url,
                        excerpt: excerptEl ? excerptEl.textContent.trim().substring(0, 200) : '',
                        author: authorEl ? authorEl.textContent.trim().replace(/\\u200b/g, '') : '',
                        upvotes: upvotes,
                        comments: comments,
                    });
                }
                return out;
            }""")

            if dom_results:
                for item in dom_results[:max_results]:
                    results.append({
                        "type": "answer",
                        "id": "",
                        "title": item.get("title", ""),
                        "excerpt": item.get("excerpt", ""),
                        "url": item.get("url", ""),
                        "author": item.get("author", ""),
                        "upvotes": item.get("upvotes", 0),
                        "comments": item.get("comments", 0),
                        "views": 0,
                        "date": "",
                    })
                logger.info(f"[zhihu-so] Playwright DOM: extracted {len(results)} results")

    except Exception as e:
        logger.warning(f"[zhihu-so] Playwright search failed: {e}")

    return results


def _parse_search_state(state: dict, max_results: int) -> List[Dict[str, Any]]:
    """Parse search results from __INITIAL_STATE__."""
    results = []

    # Try search.results or entities
    search_data = state.get("search", {})
    if isinstance(search_data, dict):
        # Try different possible keys
        for key in ("results", "items", "data"):
            items = search_data.get(key, [])
            if isinstance(items, list) and items:
                for item in items:
                    parsed = _parse_state_search_item(item, state)
                    if parsed:
                        results.append(parsed)
                break

    # Also try entities directly
    if not results:
        entities = state.get("entities", {})
        answers = entities.get("answers", {})
        for aid, ans in answers.items():
            question = ans.get("question", {})
            if isinstance(question, (int, str)):
                questions = entities.get("questions", {})
                question = questions.get(str(question), {})
            author = ans.get("author", {})
            results.append({
                "type": "answer",
                "id": str(aid),
                "title": question.get("title", "") if isinstance(question, dict) else "",
                "excerpt": _strip_html(ans.get("excerpt", "")),
                "url": f"https://www.zhihu.com/question/{question.get('id', '')}/answer/{aid}" if isinstance(question, dict) else "",
                "author": author.get("name", "") if isinstance(author, dict) else "",
                "upvotes": ans.get("voteupCount", 0) or ans.get("voteup_count", 0),
                "comments": ans.get("commentCount", 0) or ans.get("comment_count", 0),
                "views": question.get("visitCount", 0) if isinstance(question, dict) else 0,
                "date": _ts_to_date(ans.get("createdTime", 0) or ans.get("created_time", 0)),
            })

    return results[:max_results]


def _parse_state_search_item(item: dict, state: dict) -> Optional[Dict[str, Any]]:
    """Parse a single item from __INITIAL_STATE__ search results."""
    # Items may be IDs referencing entities, or inline objects
    if isinstance(item, (int, str)):
        # Reference to entity
        entities = state.get("entities", {})
        ans = entities.get("answers", {}).get(str(item), {})
        if ans:
            question = ans.get("question", {})
            author = ans.get("author", {})
            return {
                "type": "answer",
                "id": str(item),
                "title": question.get("title", "") if isinstance(question, dict) else "",
                "excerpt": _strip_html(ans.get("excerpt", "")),
                "url": f"https://www.zhihu.com/question/{question.get('id', '')}/answer/{item}" if isinstance(question, dict) else "",
                "author": author.get("name", "") if isinstance(author, dict) else "",
                "upvotes": ans.get("voteupCount", 0) or ans.get("voteup_count", 0),
                "comments": ans.get("commentCount", 0) or ans.get("comment_count", 0),
                "views": 0,
                "date": _ts_to_date(ans.get("createdTime", 0)),
            }
        return None

    if isinstance(item, dict):
        return _parse_search_item(item)

    return None


# ---------------------------------------------------------------------------
# Summary table generation
# ---------------------------------------------------------------------------

def _generate_summary_table(
    keyword: str,
    sort: str,
    items: List[Dict[str, Any]],
    output_path: Path,
    show_keyword: bool = False,
) -> None:
    """Generate summary Markdown table + CSV, sorted by upvotes descending."""
    sort_label_zh = "最新" if sort == "new" else "热门"
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Sort by upvotes descending
    items.sort(key=lambda x: int(x.get("upvotes", 0) or 0), reverse=True)

    lines = [
        "---",
        f'title: "知乎搜索：{keyword}"',
        f'search_sort: "{sort_label_zh}"',
        f"total: {len(items)}",
        f"created: {date_str}",
        "cssclasses: wide",
        "---",
        "",
    ]

    if not items:
        lines.append("*未找到结果。*")
    else:
        if show_keyword:
            lines.append("| # | 关键词 | 作者 | 标题 | 赞数 | 评论 | 日期 |")
            lines.append("|:---:|------|------|------|:---:|:---:|:---:|")
        else:
            lines.append("| # | 作者 | 标题 | 赞数 | 评论 | 日期 |")
            lines.append("|:---:|------|------|:---:|:---:|:---:|")

        for i, item in enumerate(items, 1):
            author = (item.get("author", "") or "").replace("|", "\\|")
            title = _clean_title(item.get("title", ""), max_len=50)
            title = title.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")
            url = item.get("url", "")
            title_link = f"[{title}]({url})" if url else title
            upvotes = int(item.get("upvotes", 0) or 0)
            comments = int(item.get("comments", 0) or 0)
            date = item.get("date", "")[:10]

            if show_keyword:
                kw = item.get("_keyword", "").replace("|", "\\|")
                lines.append(f"| {i} | {kw} | {author} | {title_link} | {upvotes} | {comments} | {date} |")
            else:
                lines.append(f"| {i} | {author} | {title_link} | {upvotes} | {comments} | {date} |")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"[zhihu-so] Summary table saved: {output_path}")

    # CSV
    csv_path = output_path.with_suffix(".csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        header = ["#", "作者", "标题", "赞数", "评论", "日期", "链接"]
        if show_keyword:
            header.insert(1, "关键词")
        writer.writerow(header)
        for i, item in enumerate(items, 1):
            row = [
                i,
                item.get("author", ""),
                _clean_title(item.get("title", ""), max_len=80),
                int(item.get("upvotes", 0) or 0),
                int(item.get("comments", 0) or 0),
                item.get("date", "")[:10],
                item.get("url", ""),
            ]
            if show_keyword:
                row.insert(1, item.get("_keyword", ""))
            writer.writerow(row)
    logger.info(f"[zhihu-so] CSV saved: {csv_path}")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

async def search_zhihu_keyword(
    keyword: str,
    sort: str = "hot",
    max_results: int = 50,
    save_answers: bool = False,
    skip_summary: bool = False,
) -> Dict[str, Any]:
    """Search Zhihu for content matching keyword.

    Returns dict with total, saved, output_path, items.
    """
    from feedgrab.config import zhihu_search_delay
    from feedgrab.fetchers.zhihu import _load_zhihu_cookies

    delay = zhihu_search_delay()
    items: List[Dict[str, Any]] = []

    # Tier 0: API search (needs Cookie + x-zse-96, often 403)
    cookies = _load_zhihu_cookies()
    if cookies:
        logger.info(f"[zhihu-so] Tier 0: API search for '{keyword}'")
        try:
            items = _search_via_api(keyword, cookies, max_results, sort)
            if items:
                logger.info(f"[zhihu-so] Tier 0: API returned {len(items)} results")
        except Exception as e:
            logger.warning(f"[zhihu-so] Tier 0 failed: {e}")

    # Tier 1: Browser XHR search (CDP + frontend signing)
    if not items:
        logger.info(f"[zhihu-so] Tier 1: Browser XHR search for '{keyword}'")
        try:
            items = await _search_via_xhr(keyword, max_results)
            if items:
                logger.info(f"[zhihu-so] Tier 1: XHR returned {len(items)} results")
        except Exception as e:
            logger.warning(f"[zhihu-so] Tier 1 failed: {e}")

    # Tier 2: Playwright browser search
    if not items:
        logger.info(f"[zhihu-so] Tier 2: Playwright search for '{keyword}'")
        try:
            items = await _search_via_playwright(keyword, max_results)
        except Exception as e:
            logger.warning(f"[zhihu-so] Playwright search failed: {e}")

    if not items:
        logger.warning(f"[zhihu-so] 未找到结果：'{keyword}'")
        logger.warning("   提示：请运行 'feedgrab login zhihu' 保存登录态后重试。")
        return {"total": 0, "saved": 0, "query": keyword, "items": []}

    # Generate summary table
    base_dir = _resolve_output_base()
    sort_label = "new" if sort == "new" else "hot"
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_keyword = re.sub(r'[\\/:*?"<>|]', '_', keyword)
    subdir = f"search/{sort_label}"
    summary_path = base_dir / "Zhihu" / subdir / f"{safe_keyword}_{date_str}.md"

    if not skip_summary:
        _generate_summary_table(
            keyword=keyword,
            sort=sort,
            items=items,
            output_path=summary_path,
        )

    # Optionally save individual answer .md files
    saved = 0
    if save_answers:
        import time
        from feedgrab.fetchers.zhihu import fetch_zhihu
        from feedgrab.schema import from_zhihu
        from feedgrab.utils.storage import save_to_markdown
        from feedgrab.utils.dedup import load_index, has_item, add_item, save_index, item_id_from_url

        dedup_index = load_index(platform="Zhihu")
        answer_subdir = f"{subdir}/{safe_keyword}"

        for idx, item in enumerate(items):
            item_url = item.get("url", "")
            if not item_url:
                continue
            item_id = item_id_from_url(item_url)
            if has_item(item_id, dedup_index):
                continue

            try:
                data = await fetch_zhihu(item_url)
                if data and data.get("content"):
                    content = from_zhihu(data)
                    content.category = answer_subdir
                    saved_path = save_to_markdown(content)
                    if saved_path:
                        add_item(item_id, item_url, dedup_index)
                        saved += 1
                        logger.info(f"[zhihu-so] [{idx+1}/{len(items)}] Saved: {item.get('title', '')[:40]}")
            except Exception as e:
                logger.warning(f"[zhihu-so] [{idx+1}] Save failed: {str(e)[:80]}")

            if idx < len(items) - 1:
                time.sleep(delay)

        save_index(dedup_index, platform="Zhihu")
        logger.info(f"[zhihu-so] Saved {saved} individual answer files")

    return {
        "total": len(items),
        "saved": saved,
        "query": keyword,
        "output_path": str(summary_path),
        "csv_path": str(summary_path.with_suffix(".csv")),
        "items": items,
    }
