# -*- coding: utf-8 -*-
"""
Zhihu (知乎) fetcher — single post (question/answer + article).

Tier 0: API v4 + Cookie (fastest, <1s, needs z_c0 + x-zse-96 — often 403)
Tier 1: Browser XHR API (CDP page.evaluate XHR, reuses frontend signing, <1s)
Tier 2: Playwright + DOM extraction (structured data, engagement metrics)
Tier 3: Jina Reader (last resort, may lose engagement data)
Tier 1: Playwright + __INITIAL_STATE__ (structured data, engagement metrics)
Tier 2: Jina Reader (last resort, may lose engagement data)
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from loguru import logger


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

_ZHIHU_DOMAINS = ("zhihu.com", "zhuanlan.zhihu.com")

# API v4 endpoints
_API_ANSWER = "https://www.zhihu.com/api/v4/answers/{aid}"
_API_ANSWER_INCLUDE = (
    "content,voteup_count,comment_count,created_time,updated_time,"
    "author,question,thanks_count,favlists_count"
)
_API_ARTICLE = "https://www.zhihu.com/api/v4/articles/{pid}"
_API_QUESTION = "https://www.zhihu.com/api/v4/questions/{qid}"
_API_QUESTION_INCLUDE = "detail,answer_count,follower_count,visit_count"
_API_QUESTION_ANSWERS = "https://www.zhihu.com/api/v4/questions/{qid}/answers"
_API_ANSWERS_INCLUDE = (
    "content,voteup_count,comment_count,created_time,updated_time,"
    "author,thanks_count,favlists_count"
)

# Default number of top answers to fetch for question pages
_DEFAULT_TOP_ANSWERS = 3

# Jina login-page detection keywords
_ZHIHU_LOGIN_KEYWORDS = (
    "知乎 - 有问题，就会有答案",
    "看看知乎上的讨论",
    "登录即可查看",
)


def is_zhihu_url(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    return any(d in netloc for d in _ZHIHU_DOMAINS)


def parse_zhihu_url(url: str) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
    """Parse Zhihu URL into (content_type, question_id, answer_id, article_id).

    Supported formats:
    - zhihu.com/question/{qid}/answer/{aid}  → ("answer", qid, aid, None)
    - zhihu.com/question/{qid}               → ("question", qid, None, None)
    - zhuanlan.zhihu.com/p/{pid}             → ("article", None, None, pid)
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    # Article: zhuanlan.zhihu.com/p/{pid}
    m = re.match(r"/p/(\d+)", path)
    if m:
        return ("article", None, None, m.group(1))

    # Answer: /question/{qid}/answer/{aid}
    m = re.match(r"/question/(\d+)/answer/(\d+)", path)
    if m:
        return ("answer", m.group(1), m.group(2), None)

    # Question only: /question/{qid}
    m = re.match(r"/question/(\d+)", path)
    if m:
        return ("question", m.group(1), None, None)

    return ("unknown", None, None, None)


def clean_zhihu_url(url: str) -> str:
    """Normalize Zhihu URL — strip tracking params, keep essential path."""
    parsed = urlparse(url)
    # Strip all query params and fragments
    clean = parsed._replace(query="", fragment="")
    return clean.geturl().rstrip("/")


def _make_item_id(url: str) -> str:
    return hashlib.md5(clean_zhihu_url(url).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Cookie loading
# ---------------------------------------------------------------------------

def _load_zhihu_cookies() -> Optional[Dict[str, str]]:
    """Load Zhihu cookies from session file."""
    from feedgrab.config import get_session_dir
    session_path = get_session_dir() / "zhihu.json"
    if not session_path.exists():
        return None
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
        cookies = {}
        for c in data.get("cookies", []):
            cookies[c["name"]] = c["value"]
        if "z_c0" not in cookies:
            logger.warning("[zhihu] 登录态文件缺少 z_c0 Cookie")
            return None
        return cookies
    except Exception as e:
        logger.warning(f"[zhihu] 登录态加载失败：{e}")
        return None


def _build_cookie_header(cookies: Dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


# ---------------------------------------------------------------------------
# HTML → Markdown conversion
# ---------------------------------------------------------------------------

def _html_to_markdown(html: str) -> str:
    """Convert Zhihu rich HTML content to Markdown."""
    try:
        from markdownify import markdownify as md
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # Remove noscript tags
        for tag in soup.find_all("noscript"):
            tag.decompose()

        # Convert lazy-loaded images
        for img in soup.find_all("img"):
            # Zhihu uses data-original or data-actualsrc for real image URLs
            real_src = (
                img.get("data-original")
                or img.get("data-actualsrc")
                or img.get("src", "")
            )
            if real_src:
                img["src"] = real_src

        # Convert figure + figcaption
        for fig in soup.find_all("figure"):
            img = fig.find("img")
            cap = fig.find("figcaption")
            if img and img.get("src"):
                alt = cap.get_text(strip=True) if cap else ""
                fig.replace_with(f'\n![{alt}]({img["src"]})\n')

        result = md(str(soup), heading_style="ATX", bullets="-")
        # Clean up excessive blank lines
        result = re.sub(r"\n{3,}", "\n\n", result).strip()
        return result
    except ImportError:
        # Fallback: strip HTML tags
        text = re.sub(r"<[^>]+>", "", html)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# Tier 0 — API v4
# ---------------------------------------------------------------------------

def _api_headers(cookies: Dict[str, str]) -> dict:
    from feedgrab.config import get_user_agent
    return {
        "User-Agent": get_user_agent(),
        "Referer": "https://www.zhihu.com/",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*",
        "Cookie": _build_cookie_header(cookies),
    }


def _parse_api_answer(ans: dict) -> Dict[str, Any]:
    """Parse a single API answer response into a normalized dict."""
    author = ans.get("author", {})
    return {
        "answer_id": str(ans.get("id", "")),
        "content": _html_to_markdown(ans.get("content", "")),
        "author": author.get("name", "") if isinstance(author, dict) else "",
        "author_url": author.get("url", "") if isinstance(author, dict) else "",
        "author_headline": author.get("headline", "") if isinstance(author, dict) else "",
        "upvotes": ans.get("voteup_count", 0),
        "comments": ans.get("comment_count", 0),
        "thanks": ans.get("thanks_count", 0),
        "collected": ans.get("favlists_count", 0),
        "publish_date": _ts_to_str(ans.get("created_time", 0)),
        "updated_date": _ts_to_str(ans.get("updated_time", 0)),
    }


def _fetch_answer_via_api(
    qid: str, aid: str, cookies: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """Fetch target answer + top N answers for the question via API v4."""
    from feedgrab.utils.http_client import get as http_get

    headers = _api_headers(cookies)

    # 1. Fetch the target answer
    ans_url = _API_ANSWER.format(aid=aid)
    try:
        resp = http_get(
            ans_url, headers=headers,
            params={"include": _API_ANSWER_INCLUDE}, timeout=15,
        )
        if resp.status_code != 200:
            logger.info(f"[zhihu] API answer {resp.status_code}")
            return None
        ans = resp.json()
    except Exception as e:
        logger.info(f"[zhihu] API answer request failed: {e}")
        return None

    question = ans.get("question", {})
    target_answer = _parse_api_answer(ans)

    # 2. Fetch top N answers for the question
    top_answers = _fetch_top_answers_via_api(qid, cookies, limit=_DEFAULT_TOP_ANSWERS)

    # 3. Build answers list: target answer first, then fill from top answers
    answers_list = [target_answer]
    seen_ids = {target_answer["answer_id"]}
    for ta in top_answers:
        if ta["answer_id"] not in seen_ids and len(answers_list) < _DEFAULT_TOP_ANSWERS:
            answers_list.append(ta)
            seen_ids.add(ta["answer_id"])

    # If target answer is already in top answers, just use top answers order
    if target_answer["answer_id"] in {a["answer_id"] for a in top_answers}:
        answers_list = []
        for ta in top_answers[:_DEFAULT_TOP_ANSWERS]:
            answers_list.append(ta)

    # Pick the answer with highest engagement for front matter
    best = max(answers_list, key=lambda a: a.get("upvotes", 0))

    return {
        "content_type": "answer",
        "title": question.get("title", ""),
        "question_id": str(question.get("id", qid)),
        "answer_id": best["answer_id"],
        "question_title": question.get("title", ""),
        "question_detail": _html_to_markdown(question.get("detail", "")),
        "content": "",  # Will be built from answers_list in storage
        "author": best["author"],
        "author_url": best["author_url"],
        "upvotes": best["upvotes"],
        "comments": best["comments"],
        "thanks": best["thanks"],
        "collected": best["collected"],
        "views": question.get("visit_count", 0),
        "publish_date": best["publish_date"],
        "tags": [t.get("name", "") for t in question.get("topics", [])],
        "answers_list": answers_list,
    }


def _fetch_top_answers_via_api(
    qid: str, cookies: Dict[str, str], limit: int = 3,
) -> List[Dict[str, Any]]:
    """Fetch top N answers for a question via API v4."""
    from feedgrab.utils.http_client import get as http_get

    headers = _api_headers(cookies)
    url = _API_QUESTION_ANSWERS.format(qid=qid)
    try:
        resp = http_get(
            url, headers=headers,
            params={
                "include": _API_ANSWERS_INCLUDE,
                "limit": str(limit),
                "offset": "0",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.info(f"[zhihu] API question answers {resp.status_code}")
            return []
        data = resp.json()
    except Exception as e:
        logger.info(f"[zhihu] API question answers failed: {e}")
        return []

    return [_parse_api_answer(a) for a in data.get("data", [])]


def _fetch_article_via_api(
    pid: str, cookies: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """Fetch article via API v4."""
    from feedgrab.utils.http_client import get as http_get

    headers = _api_headers(cookies)
    art_url = _API_ARTICLE.format(pid=pid)
    try:
        resp = http_get(art_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.info(f"[zhihu] API article {resp.status_code}")
            return None
        art = resp.json()
    except Exception as e:
        logger.info(f"[zhihu] API article request failed: {e}")
        return None

    author = art.get("author", {})
    return {
        "content_type": "article",
        "title": art.get("title", ""),
        "article_id": str(art.get("id", pid)),
        "content": _html_to_markdown(art.get("content", "")),
        "author": author.get("name", ""),
        "author_url": author.get("url", ""),
        "upvotes": art.get("voteup_count", 0),
        "comments": art.get("comment_count", 0),
        "views": 0,  # articles don't expose view count in API
        "publish_date": _ts_to_str(art.get("created", 0)),
        "tags": [t.get("name", "") for t in art.get("topics", [])],
    }


def _fetch_question_via_api(
    qid: str, cookies: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """Fetch question (first answer) via API v4."""
    from feedgrab.utils.http_client import get as http_get

    headers = _api_headers(cookies)

    # Fetch question detail
    q_url = _API_QUESTION.format(qid=qid)
    try:
        resp = http_get(
            q_url, headers=headers,
            params={"include": _API_QUESTION_INCLUDE}, timeout=15,
        )
        if resp.status_code != 200:
            logger.info(f"[zhihu] API question {resp.status_code}")
            return None
        q = resp.json()
    except Exception as e:
        logger.info(f"[zhihu] API question request failed: {e}")
        return None

    return {
        "content_type": "question",
        "title": q.get("title", ""),
        "question_id": str(q.get("id", qid)),
        "question_title": q.get("title", ""),
        "question_detail": _html_to_markdown(q.get("detail", "")),
        "content": _html_to_markdown(q.get("detail", "")),
        "author": "",
        "author_url": "",
        "upvotes": 0,
        "comments": q.get("answer_count", 0),
        "views": q.get("visit_count", 0),
        "publish_date": _ts_to_str(q.get("created", 0)),
        "tags": [t.get("name", "") for t in q.get("topics", [])],
    }


def _ts_to_str(ts: int) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# Tier 1 — Browser XHR API (reuses frontend x-zse-96 signing)
# ---------------------------------------------------------------------------

# XHR helper JS — injected into browser page context.
# Uses XMLHttpRequest (not fetch) because Zhihu's frontend JS monkey-patches
# XHR.prototype to auto-inject x-zse-96 signature headers.
_XHR_GET_JS = """(url) => new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open('GET', url);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.onload = () => {
        if (xhr.status === 200) {
            try { resolve({ok: true, data: JSON.parse(xhr.responseText)}); }
            catch(e) { resolve({ok: false, status: xhr.status, error: 'parse_error'}); }
        } else {
            resolve({ok: false, status: xhr.status, error: xhr.responseText.substring(0, 200)});
        }
    };
    xhr.onerror = () => resolve({ok: false, status: 0, error: 'network_error'});
    xhr.send();
})"""

# Module-level cached CDP page for batch operations
_xhr_page = None
_xhr_pw = None
_xhr_browser = None


async def _get_xhr_page():
    """Get or create a persistent CDP page for XHR API calls."""
    global _xhr_page, _xhr_pw, _xhr_browser

    if _xhr_page and not _xhr_page.is_closed():
        return _xhr_page

    from feedgrab.config import chrome_cdp_port
    try:
        from playwright.async_api import async_playwright
        _xhr_pw = await async_playwright().start()
        port = chrome_cdp_port()
        _xhr_browser = await _xhr_pw.chromium.connect_over_cdp(
            f"ws://127.0.0.1:{port}/devtools/browser"
        )
        for ctx in _xhr_browser.contexts:
            cookies = await ctx.cookies()
            if any(c.get("domain", "").endswith(".zhihu.com") for c in cookies):
                _xhr_page = await ctx.new_page()
                # Navigate to zhihu to establish same-origin context
                await _xhr_page.goto(
                    "https://www.zhihu.com/", wait_until="domcontentloaded", timeout=20000
                )
                await _xhr_page.wait_for_timeout(2000)
                logger.info("[zhihu] Tier 1: XHR page ready (CDP)")
                return _xhr_page

        logger.info("[zhihu] CDP: no Zhihu context found")
        await _xhr_browser.close()
        await _xhr_pw.stop()
        _xhr_browser = _xhr_pw = _xhr_page = None
    except Exception as e:
        logger.info(f"[zhihu] XHR page init failed: {e}")
        if _xhr_pw:
            await _xhr_pw.stop()
        _xhr_browser = _xhr_pw = _xhr_page = None
    return None


async def _close_xhr_page():
    """Close the cached XHR page."""
    global _xhr_page, _xhr_pw, _xhr_browser
    try:
        if _xhr_page and not _xhr_page.is_closed():
            await _xhr_page.close()
        if _xhr_browser:
            await _xhr_browser.close()
        if _xhr_pw:
            await _xhr_pw.stop()
    except Exception:
        pass
    _xhr_page = _xhr_pw = _xhr_browser = None


async def _xhr_api_get(path: str) -> Optional[dict]:
    """Execute an API GET via browser XHR (auto-signed by frontend JS)."""
    page = await _get_xhr_page()
    if not page:
        return None
    try:
        result = await page.evaluate(_XHR_GET_JS, path)
        if result and result.get("ok"):
            return result["data"]
        status = result.get("status", 0) if result else 0
        logger.info(f"[zhihu] XHR API {status}: {path[:80]}")
        return None
    except Exception as e:
        logger.info(f"[zhihu] XHR API error: {e}")
        return None


async def _fetch_answer_via_xhr(
    qid: str, aid: str,
) -> Optional[Dict[str, Any]]:
    """Tier 0.5: Fetch answer + top N answers via browser XHR."""
    include = _API_ANSWER_INCLUDE

    # 1. Fetch target answer
    ans = await _xhr_api_get(
        f"/api/v4/answers/{aid}?include={include}"
    )
    if not ans or not ans.get("content"):
        return None

    question = ans.get("question", {})
    target_answer = _parse_api_answer(ans)

    # 2. Fetch top N answers
    list_data = await _xhr_api_get(
        f"/api/v4/questions/{qid}/answers?include={_API_ANSWERS_INCLUDE}"
        f"&limit={_DEFAULT_TOP_ANSWERS}&offset=0"
    )
    top_answers = []
    if list_data and list_data.get("data"):
        top_answers = [_parse_api_answer(a) for a in list_data["data"]]

    # 3. Build answers list: target first, fill from top
    answers_list = [target_answer]
    seen_ids = {target_answer["answer_id"]}
    for ta in top_answers:
        if ta["answer_id"] not in seen_ids and len(answers_list) < _DEFAULT_TOP_ANSWERS:
            answers_list.append(ta)
            seen_ids.add(ta["answer_id"])

    if target_answer["answer_id"] in {a["answer_id"] for a in top_answers}:
        answers_list = top_answers[:_DEFAULT_TOP_ANSWERS]

    best = max(answers_list, key=lambda a: a.get("upvotes", 0))

    return {
        "content_type": "answer",
        "title": question.get("title", ""),
        "question_id": str(question.get("id", qid)),
        "answer_id": best["answer_id"],
        "question_title": question.get("title", ""),
        "question_detail": _html_to_markdown(question.get("detail", "")),
        "content": "",
        "author": best["author"],
        "author_url": best["author_url"],
        "upvotes": best["upvotes"],
        "comments": best["comments"],
        "thanks": best["thanks"],
        "collected": best["collected"],
        "views": question.get("visit_count", 0),
        "publish_date": best["publish_date"],
        "tags": [t.get("name", "") for t in question.get("topics", [])],
        "answers_list": answers_list,
    }


async def _fetch_article_via_xhr(pid: str) -> Optional[Dict[str, Any]]:
    """Tier 0.5: Fetch article via browser XHR."""
    art = await _xhr_api_get(f"/api/v4/articles/{pid}")
    if not art or not art.get("content"):
        return None

    author = art.get("author", {})
    return {
        "content_type": "article",
        "title": art.get("title", ""),
        "article_id": str(art.get("id", pid)),
        "content": _html_to_markdown(art.get("content", "")),
        "author": author.get("name", "") if isinstance(author, dict) else "",
        "author_url": author.get("url", "") if isinstance(author, dict) else "",
        "upvotes": art.get("voteup_count", 0),
        "comments": art.get("comment_count", 0),
        "thanks": art.get("thanks_count", 0),
        "collected": art.get("favlists_count", 0),
        "views": 0,
        "publish_date": _ts_to_str(art.get("created", 0)),
        "tags": [t.get("name", "") for t in art.get("topics", [])],
    }


# ---------------------------------------------------------------------------
# Tier 2 — Playwright + __INITIAL_STATE__
# ---------------------------------------------------------------------------

# JS to extract __INITIAL_STATE__ from Zhihu page
_EXTRACT_STATE_JS = """() => {
    try {
        const s = window.__INITIAL_STATE__;
        if (!s) return null;
        // Return a serializable copy (Proxy objects can't be JSON.stringify'd)
        return JSON.parse(JSON.stringify(s));
    } catch(e) {
        return null;
    }
}"""

# JS to extract multiple answers from DOM with engagement data
_EXTRACT_ANSWERS_DOM_JS = """() => {
    const result = {};
    // Question title
    const qTitle = document.querySelector('.QuestionHeader-title');
    result.question_title = qTitle ? qTitle.textContent.trim() : '';
    // Question detail
    const qDetail = document.querySelector('.QuestionHeader-detail .RichText');
    result.question_detail = qDetail ? qDetail.innerHTML : '';

    // Extract top N answer items (deduplicate by content + author)
    const allItems = document.querySelectorAll('.AnswerItem, .List-item');
    const seen = new Set();
    const answers = [];

    for (const el of allItems) {
        // Use data-zop for dedup
        const zop = el.getAttribute('data-zop');
        let itemId = '';
        if (zop) {
            try { itemId = JSON.parse(zop).itemId || ''; } catch(e) {}
        }

        const content = el.querySelector('.RichContent-inner');
        if (!content || content.textContent.trim().length < 10) continue;

        // Dedup by author + content hash (handles missing data-zop)
        const author = el.querySelector('.AuthorInfo-name a, .AuthorInfo-name');
        const authorName = author ? author.textContent.trim().replace(/\\u200b/g, '') : '';
        const contentSnippet = content.textContent.trim().substring(0, 80);
        const dedupKey = authorName + '|' + contentSnippet;
        if (seen.has(dedupKey)) continue;
        seen.add(dedupKey);
        const btns = el.querySelectorAll('.ContentItem-actions button, .ContentItem-action');
        const btnTexts = Array.from(btns).map(b => b.textContent.trim().replace(/\\u200b/g, ''));

        // Parse engagement from button texts
        let upvotes = 0, comments = 0, collected = 0, thanks = 0;
        for (const t of btnTexts) {
            const num = parseInt(t.replace(/[^0-9]/g, ''), 10) || 0;
            if (t.includes('赞同')) upvotes = num;
            else if (t.includes('评论')) comments = num;
        }
        // Buttons without label text: [赞同N, ?, N条评论, 收藏数, 喜欢数, ...]
        const numBtns = btnTexts.filter(t => /^\\d+$/.test(t));
        if (numBtns.length >= 2) {
            collected = parseInt(numBtns[0], 10) || 0;
            thanks = parseInt(numBtns[1], 10) || 0;
        }

        answers.push({
            answer_id: itemId,
            author: authorName,
            content_html: content.innerHTML,
            upvotes: upvotes,
            comments: comments,
            collected: collected,
            thanks: thanks,
        });

        if (answers.length >= 3) break;
    }

    result.answers = answers;
    return result;
}"""

# JS to extract article content from DOM
_EXTRACT_ARTICLE_DOM_JS = """() => {
    const result = {};
    result.title = (document.querySelector('.Post-Title') || {}).textContent || '';
    result.content = (document.querySelector('.Post-RichTextContainer .RichText, .RichText--color') || {}).innerHTML || '';
    const author = document.querySelector('.AuthorInfo-name, .Post-Author .UserLink-link');
    result.author = author ? author.textContent.trim().replace(/\\u200b/g, '') : '';

    // Engagement data from action buttons
    const btns = document.querySelectorAll('.ContentItem-actions button, .ContentItem-action, .Post-SocialActions button');
    const btnTexts = Array.from(btns).map(b => b.textContent.trim().replace(/\\u200b/g, ''));
    let upvotes = 0, comments = 0, collected = 0, thanks = 0;
    for (const t of btnTexts) {
        const num = parseInt(t.replace(/[^0-9]/g, ''), 10) || 0;
        if (t.includes('赞同') || t.includes('赞')) upvotes = num;
        else if (t.includes('评论')) comments = num;
    }
    const numBtns = btnTexts.filter(t => /^\\d+$/.test(t));
    if (numBtns.length >= 2) {
        collected = parseInt(numBtns[0], 10) || 0;
        thanks = parseInt(numBtns[1], 10) || 0;
    }
    result.upvotes = upvotes;
    result.comments = comments;
    result.collected = collected;
    result.thanks = thanks;
    return result;
}"""


async def _connect_zhihu_cdp():
    """Connect to running Chrome via CDP, find Zhihu context."""
    from feedgrab.config import zhihu_cdp_enabled, chrome_cdp_port

    if not zhihu_cdp_enabled():
        return None, None, None, False

    port = chrome_cdp_port()
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        ws_url = f"ws://127.0.0.1:{port}/devtools/browser"
        browser = await pw.chromium.connect_over_cdp(ws_url)

        target_domains = (".zhihu.com",)
        for ctx in browser.contexts:
            cookies = await ctx.cookies()
            if any(
                any(c.get("domain", "").endswith(d) for d in target_domains)
                for c in cookies
            ):
                page = await ctx.new_page()
                logger.info("[zhihu] CDP connected, new tab created")
                return pw, browser, page, True

        logger.info("[zhihu] CDP: no context with Zhihu cookies")
        await browser.close()
        await pw.stop()
    except Exception as e:
        logger.info(f"[zhihu] CDP connection failed: {e}")
    return None, None, None, False


# Module-level cached Playwright CDP page for Tier 2 DOM extraction
_pw_page = None
_pw_pw = None
_pw_browser = None
_pw_is_cdp = False


async def _get_playwright_page():
    """Get or create a persistent CDP page for Playwright DOM extraction."""
    global _pw_page, _pw_pw, _pw_browser, _pw_is_cdp

    if _pw_page and not _pw_page.is_closed():
        return _pw_pw, _pw_browser, _pw_page, _pw_is_cdp

    # Try CDP
    pw, browser, page, is_cdp = await _connect_zhihu_cdp()
    if page:
        _pw_pw, _pw_browser, _pw_page, _pw_is_cdp = pw, browser, page, True
        return pw, browser, page, True

    # Launch mode fallback
    try:
        from feedgrab.fetchers.browser import get_async_playwright, stealth_launch, get_stealth_context_options, setup_resource_blocking
        pw_mod = get_async_playwright()
        pw = await pw_mod().start()
        browser = await stealth_launch(pw, headless=False)

        from feedgrab.config import get_session_dir
        session_path = get_session_dir() / "zhihu.json"
        ctx_opts = get_stealth_context_options()
        if session_path.exists():
            ctx_opts["storage_state"] = str(session_path)
        context = await browser.new_context(**ctx_opts)
        await setup_resource_blocking(context)
        page = await context.new_page()
        _pw_pw, _pw_browser, _pw_page, _pw_is_cdp = pw, browser, page, False
        return pw, browser, page, False
    except Exception as e:
        logger.warning(f"[zhihu] Playwright launch failed: {e}")
        if pw:
            await pw.stop()
        return None, None, None, False


async def _fetch_via_playwright(url: str) -> Optional[Dict[str, Any]]:
    """Tier 2: Playwright browser extraction (reuses CDP page across calls)."""
    from feedgrab.config import zhihu_page_load_timeout

    timeout = zhihu_page_load_timeout()
    content_type, qid, aid, pid = parse_zhihu_url(url)

    pw, browser, page, is_cdp = await _get_playwright_page()
    if not page:
        return None

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)  # Wait for Vue hydration

        # Try __INITIAL_STATE__ first
        state = await page.evaluate(_EXTRACT_STATE_JS)
        if state:
            data = _parse_initial_state(state, content_type, qid, aid, pid)
            if data and (data.get("content") or data.get("answers_list")):
                logger.info("[zhihu] Tier 2: __INITIAL_STATE__ extraction success")
                return data

        # Fallback: DOM extraction
        logger.info("[zhihu] __INITIAL_STATE__ empty, trying DOM extraction")
        if content_type == "article":
            dom = await page.evaluate(_EXTRACT_ARTICLE_DOM_JS)
            if dom and dom.get("content"):
                return {
                    "content_type": "article",
                    "title": dom.get("title", ""),
                    "article_id": pid or "",
                    "content": _html_to_markdown(dom["content"]),
                    "author": dom.get("author", ""),
                    "upvotes": dom.get("upvotes", 0),
                    "comments": dom.get("comments", 0),
                    "thanks": dom.get("thanks", 0),
                    "collected": dom.get("collected", 0),
                    "views": 0,
                    "publish_date": "",
                    "tags": [],
                }
        else:
            dom = await page.evaluate(_EXTRACT_ANSWERS_DOM_JS)
            dom_answers = dom.get("answers", []) if dom else []
            if dom_answers:
                answers_list = []
                for da in dom_answers:
                    answers_list.append({
                        "answer_id": da.get("answer_id", ""),
                        "content": _html_to_markdown(da.get("content_html", "")),
                        "author": da.get("author", ""),
                        "author_url": "",
                        "author_headline": "",
                        "upvotes": da.get("upvotes", 0),
                        "comments": da.get("comments", 0),
                        "thanks": da.get("thanks", 0),
                        "collected": da.get("collected", 0),
                        "publish_date": "",
                        "updated_date": "",
                    })
                best = max(answers_list, key=lambda a: a.get("upvotes", 0))
                q_detail = _html_to_markdown(dom.get("question_detail", ""))
                return {
                    "content_type": content_type or "answer",
                    "title": dom.get("question_title", ""),
                    "question_id": qid or "",
                    "answer_id": best["answer_id"],
                    "question_title": dom.get("question_title", ""),
                    "question_detail": q_detail,
                    "content": "",
                    "author": best["author"],
                    "author_url": "",
                    "upvotes": best["upvotes"],
                    "comments": best["comments"],
                    "thanks": best["thanks"],
                    "collected": best["collected"],
                    "views": 0,
                    "publish_date": "",
                    "tags": [],
                    "answers_list": answers_list,
                }

        return None
    except Exception as e:
        logger.warning(f"[zhihu] Playwright extraction failed: {e}")
        return None


def _parse_initial_state(
    state: dict, content_type: str,
    qid: Optional[str], aid: Optional[str], pid: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Extract structured data from __INITIAL_STATE__."""
    try:
        if content_type == "article":
            return _parse_article_state(state, pid)
        else:
            return _parse_answer_state(state, qid, aid)
    except Exception as e:
        logger.warning(f"[zhihu] __INITIAL_STATE__ parse error: {e}")
        return None


def _parse_answer_state(
    state: dict, qid: Optional[str], aid: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Parse answer data from __INITIAL_STATE__."""
    # Try entities.answers first
    entities = state.get("entities", {})
    answers = entities.get("answers", {})
    questions = entities.get("questions", {})

    ans_data = answers.get(aid, {}) if aid else {}
    q_data = questions.get(qid, {}) if qid else {}

    # If no entities, try initialQuestion / initialAnswers
    if not ans_data:
        initial = state.get("initialAnswers", {})
        if isinstance(initial, dict):
            for k, v in initial.items():
                if isinstance(v, dict) and v.get("content"):
                    ans_data = v
                    break

    if not q_data:
        q_data = state.get("initialQuestion", {}) or {}

    content_html = ans_data.get("content", "")
    if not content_html:
        return None

    author = ans_data.get("author", {})
    question = ans_data.get("question", q_data)
    if isinstance(question, (int, str)):
        question = q_data

    return {
        "content_type": "answer",
        "title": question.get("title", "") if isinstance(question, dict) else "",
        "question_id": str(question.get("id", qid or "")) if isinstance(question, dict) else str(qid or ""),
        "answer_id": str(ans_data.get("id", aid or "")),
        "question_title": question.get("title", "") if isinstance(question, dict) else "",
        "question_detail": _html_to_markdown(
            question.get("detail", "") if isinstance(question, dict) else ""
        ),
        "content": _html_to_markdown(content_html),
        "author": author.get("name", "") if isinstance(author, dict) else "",
        "author_url": author.get("url", "") if isinstance(author, dict) else "",
        "upvotes": ans_data.get("voteupCount", 0) or ans_data.get("voteup_count", 0),
        "comments": ans_data.get("commentCount", 0) or ans_data.get("comment_count", 0),
        "views": question.get("visitCount", 0) if isinstance(question, dict) else 0,
        "publish_date": _ts_to_str(ans_data.get("createdTime", 0) or ans_data.get("created_time", 0)),
        "tags": [],
    }


def _parse_article_state(
    state: dict, pid: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Parse article data from __INITIAL_STATE__."""
    entities = state.get("entities", {})
    articles = entities.get("articles", {})
    art_data = articles.get(pid, {}) if pid else {}

    # Fallback: try initialProps or article key
    if not art_data:
        art_data = state.get("article", {}) or {}
    if not art_data:
        props = state.get("initialProps", {})
        if isinstance(props, dict):
            art_data = props.get("article", {}) or {}

    content_html = art_data.get("content", "")
    if not content_html:
        return None

    author = art_data.get("author", {})
    return {
        "content_type": "article",
        "title": art_data.get("title", ""),
        "article_id": str(art_data.get("id", pid or "")),
        "content": _html_to_markdown(content_html),
        "author": author.get("name", "") if isinstance(author, dict) else "",
        "author_url": author.get("url", "") if isinstance(author, dict) else "",
        "upvotes": art_data.get("voteupCount", 0) or art_data.get("voteup_count", 0),
        "comments": art_data.get("commentCount", 0) or art_data.get("comment_count", 0),
        "views": 0,
        "publish_date": _ts_to_str(art_data.get("created", 0)),
        "tags": [t.get("name", "") for t in art_data.get("topics", []) if isinstance(t, dict)],
    }


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

async def fetch_zhihu(url: str) -> Dict[str, Any]:
    """Fetch Zhihu content with multi-tier fallback.

    Tier 0: API v4 + Cookie  →  Tier 1: Browser XHR  →  Tier 2: Playwright DOM  →  Tier 3: Jina
    """
    url = clean_zhihu_url(url)
    content_type, qid, aid, pid = parse_zhihu_url(url)
    item_id = _make_item_id(url)

    empty_result: Dict[str, Any] = {
        "title": "", "content": "", "url": url, "author": "",
        "content_type": content_type, "question_id": qid or "",
        "answer_id": aid or "", "article_id": pid or "",
        "question_title": "", "question_detail": "",
        "upvotes": 0, "comments": 0, "thanks": 0, "collected": 0, "views": 0,
        "author_url": "", "publish_date": "", "tags": [],
        "img_subdir": item_id, "answers_list": [],
    }

    # Tier 0: API v4
    cookies = _load_zhihu_cookies()
    if cookies:
        logger.info(f"[zhihu] Tier 0: API v4 for {content_type}")
        try:
            if content_type == "answer" and aid:
                data = _fetch_answer_via_api(qid or "", aid, cookies)
            elif content_type == "article" and pid:
                data = _fetch_article_via_api(pid, cookies)
            elif content_type == "question" and qid:
                data = _fetch_question_via_api(qid, cookies)
            else:
                data = None

            if data and (data.get("content") or data.get("answers_list")):
                logger.info("[zhihu] Tier 0: API success")
                data["url"] = url
                data["img_subdir"] = item_id
                return data
        except Exception as e:
            logger.warning(f"[zhihu] Tier 0 failed: {e}")
    else:
        logger.info("[zhihu] No session file, skipping Tier 0 API")

    # Tier 1: Browser XHR API (CDP + frontend signing)
    logger.info(f"[zhihu] Tier 1: Browser XHR for {content_type}")
    try:
        if content_type == "answer" and aid:
            data = await _fetch_answer_via_xhr(qid or "", aid)
        elif content_type == "article" and pid:
            data = await _fetch_article_via_xhr(pid)
        else:
            data = None

        if data and (data.get("content") or data.get("answers_list")):
            logger.info("[zhihu] Tier 1: Browser XHR success")
            data["url"] = url
            data["img_subdir"] = item_id
            return data
    except Exception as e:
        logger.warning(f"[zhihu] Tier 1 failed: {e}")

    # Tier 2: Playwright DOM
    logger.info(f"[zhihu] Tier 2: Playwright for {url}")
    try:
        pw_data = await _fetch_via_playwright(url)
        if pw_data and (pw_data.get("content") or pw_data.get("answers_list")):
            pw_data["url"] = url
            pw_data.setdefault("img_subdir", item_id)
            return pw_data
    except Exception as e:
        logger.warning(f"[zhihu] Tier 2 failed: {e}")

    # Tier 3: Jina Reader
    logger.info(f"[zhihu] Tier 3: Jina Reader for {url}")
    from feedgrab.fetchers.jina import fetch_via_jina
    jina_data = fetch_via_jina(url)
    jina_content = jina_data.get("content", "")

    # Detect login page
    if any(kw in jina_content for kw in _ZHIHU_LOGIN_KEYWORDS):
        logger.warning("[zhihu] Jina 返回的是登录页。请运行：feedgrab login zhihu")
        jina_content = ""

    if jina_content:
        return {
            **empty_result,
            "title": jina_data.get("title", ""),
            "content": jina_content,
        }

    # All tiers failed
    logger.error(f"[zhihu] 所有抓取层都失败：{url}")
    logger.error("   提示：请运行 'feedgrab login zhihu' 保存登录态后重试。")
    return empty_result
