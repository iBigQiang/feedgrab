# -*- coding: utf-8 -*-
"""
Playwright browser fetcher — headless Chromium fallback for anti-scraping sites.

Used when Jina Reader fails (403/451/timeout). Supports persistent login
sessions via Playwright's storage_state for platforms requiring authentication.

Install: pip install "feedgrab[browser]" && playwright install chromium
"""

from loguru import logger
from pathlib import Path
from urllib.parse import urlparse

from feedgrab.config import get_session_dir, get_user_agent
from feedgrab.service.proxy import get_playwright_proxy_options, redact_proxy_url

SESSION_DIR = get_session_dir()
TIMEOUT_MS = 30_000


# ---------------------------------------------------------------------------
# Stealth browser engine: patchright (Tier 1) → playwright (Tier 3)
#
# patchright patches Chromium's CDP layer to remove automation detection
# (navigator.webdriver, Runtime.enable) at the protocol level — no JS hacks.
# Falls back to standard playwright if patchright is not installed.
# Adapted from: https://github.com/D4Vinci/Scrapling
# ---------------------------------------------------------------------------

_async_pw = None   # cached async_playwright function
_pw_engine = ""    # "patchright" or "playwright"


def get_async_playwright():
    """Return async_playwright: patchright (Tier 1) → playwright (Tier 3)."""
    global _async_pw, _pw_engine
    if _async_pw is not None:
        return _async_pw
    try:
        from patchright.async_api import async_playwright
        _async_pw, _pw_engine = async_playwright, "patchright"
        return _async_pw
    except ImportError:
        pass
    try:
        from playwright.async_api import async_playwright
        _async_pw, _pw_engine = async_playwright, "playwright"
        return _async_pw
    except ImportError:
        raise RuntimeError(
            "Neither patchright nor playwright is installed. Run:\n"
            "  pip install patchright && patchright install chromium\n"
            "OR:\n"
            "  pip install playwright && playwright install chromium"
        )


def get_stealth_engine_name() -> str:
    """Return active engine name ('patchright' or 'playwright')."""
    if not _pw_engine:
        get_async_playwright()
    return _pw_engine


# Chrome launch flags for anti-detection (adapted from Scrapling)
STEALTH_LAUNCH_ARGS = [
    # Core anti-detection
    "--disable-blink-features=AutomationControlled",
    "--test-type",
    # Performance & noise reduction
    "--no-pings",
    "--no-first-run",
    "--disable-infobars",
    "--disable-breakpad",
    "--no-service-autorun",
    "--password-store=basic",
    "--disable-hang-monitor",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
    "--disable-search-engine-choice-screen",
    # Stealth fingerprint
    "--mute-audio",
    "--disable-sync",
    "--hide-scrollbars",
    "--disable-logging",
    "--start-maximized",
    "--enable-async-dns",
    "--use-mock-keychain",
    "--disable-translate",
    "--disable-voice-input",
    "--disable-wake-on-wifi",
    "--ignore-gpu-blocklist",
    "--enable-tcp-fast-open",
    "--disable-cloud-import",
    "--disable-print-preview",
    "--disable-dev-shm-usage",
    "--metrics-recording-only",
    "--disable-crash-reporter",
    "--disable-partial-raster",
    "--disable-gesture-typing",
    "--disable-prompt-on-repost",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--aggressive-cache-discard",
    "--disable-cookie-encryption",
    "--disable-domain-reliability",
    "--disable-threaded-animation",
    "--disable-threaded-scrolling",
    "--enable-simple-cache-backend",
    "--disable-background-networking",
    "--enable-surface-synchronization",
    "--disable-renderer-backgrounding",
    "--disable-ipc-flooding-protection",
    "--safebrowsing-disable-auto-update",
    "--disable-background-timer-throttling",
    "--disable-client-side-phishing-detection",
    "--disable-backgrounding-occluded-windows",
    "--autoplay-policy=user-gesture-required",
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--disable-features=AudioServiceOutOfProcess,TranslateUI,"
    "BlinkGenPropertyTrees",
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,"
    "primaryPointerType=4,availablePointerTypes=4",
]

# Playwright adds these by default — they expose automation fingerprints
HARMFUL_DEFAULT_ARGS = [
    "--enable-automation",
    "--disable-popup-blocking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-extensions",
]


async def stealth_launch(p, *, headless=True):
    """Launch Chromium with full stealth anti-detection settings.

    Uses system Chrome (channel='chrome') for genuine fingerprint;
    patchright's CDP patches still apply at the protocol level.
    """
    engine = get_stealth_engine_name()
    proxy_options = get_playwright_proxy_options()
    proxy_label = redact_proxy_url(proxy_options.get("server", "")) if proxy_options else ""
    logger.debug(f"Launching browser [{engine}] headless={headless} proxy={bool(proxy_options)} {proxy_label}")
    launch_options = {
        "headless": headless,
        "channel": "chrome",
        "args": STEALTH_LAUNCH_ARGS,
        "ignore_default_args": HARMFUL_DEFAULT_ARGS,
    }
    if proxy_options:
        launch_options["proxy"] = proxy_options
    return await p.chromium.launch(
        **launch_options,
    )


def get_stealth_context_options(**overrides) -> dict:
    """Build browser context options with anti-detection defaults.

    Returns dict for ``browser.new_context(**opts)``.
    Caller can override any field via kwargs (e.g. storage_state).
    """
    opts = {
        "user_agent": get_user_agent(),
        "viewport": {"width": 1920, "height": 1080},
        "screen": {"width": 1920, "height": 1080},
        "locale": "zh-CN",
        "color_scheme": "dark",
        "device_scale_factor": 2,
        "is_mobile": False,
        "has_touch": False,
        "ignore_https_errors": True,
    }
    proxy_options = get_playwright_proxy_options()
    if proxy_options and "proxy" not in overrides:
        opts["proxy"] = proxy_options
    opts.update(overrides)
    return opts


# ---------------------------------------------------------------------------
# Referer — convincing search engine origin (adapted from Scrapling)
# ---------------------------------------------------------------------------

_CHINESE_DOMAINS = frozenset({
    "xiaohongshu.com", "xhslink.com", "weixin.sogou.com",
    "mp.weixin.qq.com", "sogou.com", "bilibili.com",
    "weibo.com", "zhihu.com", "douyin.com", "baidu.com",
})


def generate_referer(url: str) -> str:
    """Generate a convincing search engine referer for the target URL.

    Chinese platforms → Baidu search, others → Google search.
    Makes traffic appear as organic search rather than direct bot access.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # Extract meaningful site name (skip short subdomains like en/mp/m/api)
    parts = hostname.replace("www.", "").split(".")
    site_name = parts[0]
    if len(parts) >= 3 and len(parts[0]) <= 3:
        site_name = parts[1]

    if any(d in hostname for d in _CHINESE_DOMAINS):
        return f"https://www.baidu.com/s?wd={site_name}"
    return f"https://www.google.com/search?q={site_name}"


# ---------------------------------------------------------------------------
# Resource interception — block non-essential requests for speed
# ---------------------------------------------------------------------------

# Resource types that are never needed for content extraction
_BLOCKED_RESOURCE_TYPES = frozenset({
    "font",         # Web fonts
    "media",        # Video / audio playback
    "texttrack",    # Subtitle tracks
    "beacon",       # Analytics beacons (navigator.sendBeacon)
    "eventsource",  # Server-sent events
    "manifest",     # Service worker / PWA manifest
    "websocket",    # WebSocket connections
})

# Tracking / analytics domains to block
_BLOCKED_DOMAINS = frozenset({
    "google-analytics.com",
    "googletagmanager.com",
    "connect.facebook.net",
    "doubleclick.net",
    "hotjar.com",
    "clarity.ms",
    "plausible.io",
    "umami.is",
    "cdn.mxpnl.com",          # Mixpanel
    "sentry.io",
    "browser.sentry-cdn.com",
})


async def setup_resource_blocking(target) -> None:
    """Install route handler to block non-essential resources.

    Args:
        target: A Playwright Page or BrowserContext.
                Context-level blocking applies to all pages in that context.
    """
    async def _handle_route(route):
        if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return
        req_url = route.request.url
        if any(d in req_url for d in _BLOCKED_DOMAINS):
            await route.abort()
            return
        await route.continue_()

    await target.route("**/*", _handle_route)


# XHS note page JS evaluate — extracted for reuse by batch fetcher
XHS_NOTE_JS_EVALUATE = """() => {
    const title = document.querySelector('#detail-title');
    const desc = document.querySelector('#detail-desc');
    const author = document.querySelector('.author-wrapper .username')
        || document.querySelector('.author-container .username');

    // 正文（不含标签文本，标签单独提取）
    let content = '';
    if (desc) {
        const clone = desc.cloneNode(true);
        clone.querySelectorAll('a.tag').forEach(a => a.remove());
        content = clone.innerText.trim();
    }

    // 话题标签
    const tags = desc
        ? Array.from(desc.querySelectorAll('a.tag'))
            .map(a => a.innerText.trim().replace(/^#+/, ''))
            .filter(Boolean)
        : [];

    // 笔记图片：只取非 duplicate 的 slide，按 data-index 排序
    const images = Array.from(
        document.querySelectorAll('.swiper-wrapper .swiper-slide:not(.swiper-slide-duplicate)')
    ).sort((a, b) =>
        (parseInt(a.dataset.swiperSlideIndex) || 0) -
        (parseInt(b.dataset.swiperSlideIndex) || 0)
    ).map(slide => {
        const img = slide.querySelector('img');
        return img ? (img.src || '') : '';
    }).filter(src => src.startsWith('http'));

    // 互动数据：点赞、收藏、评论
    const counts = Array.from(
        document.querySelectorAll('.engage-bar .count')
    ).map(el => el.innerText.trim());

    // 日期 + IP属地（如 "02-18 福建" 或 "编辑于 2025-08-16"）
    const dateEl = document.querySelector('.bottom-container .date');

    // 作者主页链接（去掉追踪参数）
    let authorUrl = '';
    const authorLink = document.querySelector('.author-wrapper a[href*="/user/profile/"]');
    if (authorLink) {
        try {
            const u = new URL(authorLink.href);
            authorUrl = u.origin + u.pathname;
        } catch(e) {}
    }

    return {
        title: title ? title.innerText.trim() : '',
        content: content,
        author: author ? author.innerText.trim().split('\\n')[0] : '',
        authorUrl: authorUrl,
        tags: tags,
        images: images,
        likes: parseInt(counts[0]) || 0,
        collects: parseInt(counts[1]) || 0,
        comments: parseInt(counts[2]) || 0,
        date: dateEl ? dateEl.innerText.trim() : '',
    };
}"""


def _build_xhs_result(data: dict, page_url: str) -> dict:
    """Convert raw XHS JS evaluate output to a standardized result dict."""
    return {
        "title": (data["title"] or "").strip()[:200],
        "content": (data["content"] or "").strip(),
        "url": page_url,
        "author": (data["author"] or "").strip(),
        "author_url": data.get("authorUrl", ""),
        "tags": data.get("tags", []),
        "images": data.get("images", []),
        "likes": data.get("likes", 0),
        "collects": data.get("collects", 0),
        "comments": data.get("comments", 0),
        "date": data.get("date", ""),
    }


async def evaluate_xhs_note(page) -> dict:
    """Wait for XHS note to render, then extract structured data via JS evaluate.

    Assumes the page has already navigated to an XHS note URL.
    Returns standardized result dict.
    """
    try:
        await page.wait_for_selector("#noteContainer", timeout=8000)
    except Exception:
        pass
    # Wait for date element (lazy-loaded) — gives more time for full render
    try:
        await page.wait_for_selector(".bottom-container .date", timeout=3000)
    except Exception:
        pass
    await page.wait_for_timeout(500)

    data = await page.evaluate(XHS_NOTE_JS_EVALUATE)
    return _build_xhs_result(data, page.url)


async def fetch_via_browser(url: str, storage_state: str = None) -> dict:
    """
    Fetch a URL using stealth Chromium browser.

    Engine priority: patchright (Tier 1) → playwright (Tier 3).

    Args:
        url: Target URL to fetch.
        storage_state: Path to a storage state JSON file (cookies/localStorage).
                       If provided, the browser context will load this session.

    Returns:
        dict with keys: title, content, url, author
    """
    async_pw = get_async_playwright()
    logger.info(f"Browser fetch [{get_stealth_engine_name()}]: {url}")

    async with async_pw() as p:
        # Use headed mode for XHS (anti-bot detection), headless for others
        is_xhs = "xiaohongshu.com" in url or "xhslink.com" in url
        browser = await stealth_launch(p, headless=not is_xhs)

        ctx_opts = {}
        if storage_state and Path(storage_state).exists():
            ctx_opts["storage_state"] = storage_state
            logger.info(f"Using session: {storage_state}")

        context = await browser.new_context(
            **get_stealth_context_options(**ctx_opts)
        )
        page = await context.new_page()
        await setup_resource_blocking(page)

        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=TIMEOUT_MS,
                referer=generate_referer(url),
            )

            if is_xhs:
                # XHS SPA needs the note container to render
                try:
                    await page.wait_for_selector("#noteContainer", timeout=8000)
                except Exception:
                    logger.warning("[XHS] #noteContainer not found within 8s, proceeding anyway")
                await page.wait_for_timeout(1000)

                data = await page.evaluate(XHS_NOTE_JS_EVALUATE)
                result = _build_xhs_result(data, page.url)
            else:
                # Generic fallback for non-XHS pages
                await page.wait_for_timeout(2000)

                title = await page.title()
                content = await page.evaluate("""() => {
                    const el = document.querySelector('article')
                        || document.querySelector('main')
                        || document.querySelector('.content')
                        || document.body;
                    return el ? el.innerText : '';
                }""")

                result = {
                    "title": (title or "").strip()[:200],
                    "content": (content or "").strip(),
                    "url": page.url,
                    "author": "",
                }

            logger.info(f"Browser fetch OK: {result['title'][:60]}")
            return result

        finally:
            await context.close()
            await browser.close()


# ---------------------------------------------------------------------------
# WeChat article JS evaluate — extract rich content + full metadata
# ---------------------------------------------------------------------------

WECHAT_ARTICLE_JS_EVALUATE = """() => {
    const result = {};

    // 1. DOM elements (normal articles)
    const titleEl = document.querySelector('#activity-name');
    result.title = titleEl ? titleEl.innerText.trim() : '';

    const authorEl = document.querySelector('#js_name');
    result.author = authorEl ? authorEl.innerText.trim() : '';

    const timeEl = document.querySelector('#publish_time');
    result.publishTime = timeEl ? timeEl.innerText.trim() : '';

    // 1b. Fallback for image-posts (小绿书, itemShowType=16):
    //     #activity-name and #js_name don't exist — use og:title and nick_name from JS
    if (!result.title) {
        const ogTitle = document.querySelector('meta[property="og:title"]');
        if (ogTitle && ogTitle.content) result.title = ogTitle.content.trim();
    }
    if (!result.title) {
        const h1 = document.querySelector('.rich_media_title');
        if (h1) result.title = h1.innerText.trim();
    }
    if (!result.author) {
        const scripts = document.querySelectorAll('script');
        for (const s of scripts) {
            const text = s.textContent || '';
            const m = text.match(/nick_name\\s*[:=]\\s*['"]([^'"]+)['"]/);
            if (m) { result.author = m[1].trim(); break; }
        }
    }
    // Fallback: cgiDataNew.nick_name (reliable for both normal articles and image-posts)
    if (!result.author) {
        try {
            if (window.cgiDataNew && window.cgiDataNew.nick_name) {
                result.author = window.cgiDataNew.nick_name.trim();
            }
        } catch(e) {}
    }

    // 2. OG meta tags (reliable, always present)
    const ogImage = document.querySelector('meta[property="og:image"]');
    result.ogImage = ogImage ? ogImage.content || '' : '';

    const ogDesc = document.querySelector('meta[property="og:description"]');
    result.ogDescription = ogDesc ? ogDesc.content || '' : '';

    // 3. Article tags
    const tagEls = document.querySelectorAll('#js_tags .article-tag__item');
    result.tags = Array.from(tagEls).map(el => el.innerText.trim()).filter(Boolean);

    // 4. "Read original" link
    const readOriginal = document.querySelector('#js_view_source');
    result.originalUrl = (readOriginal && readOriginal.href &&
        !readOriginal.href.startsWith('javascript:')) ? readOriginal.href : '';

    // 5. create_time from JS scripts (precise Unix timestamp)
    result.createTime = 0;
    const scripts = document.querySelectorAll('script');
    for (const s of scripts) {
        const text = s.textContent || '';
        // Try multiple formats: JsDecode('N'), 'N', "N", =N
        let m = text.match(/create_time\\s*:\\s*JsDecode\\('(\\d+)'\\)/);
        if (!m) m = text.match(/create_time\\s*:\\s*'(\\d+)'/);
        if (!m) m = text.match(/create_time\\s*[:=]\\s*["']?(\\d{10})["']?/);
        if (m) { result.createTime = parseInt(m[1]); break; }
    }

    // 6. Cover image from JS (higher quality than og:image)
    result.coverImage = '';
    for (const s of scripts) {
        const text = s.textContent || '';
        const m = text.match(/msg_cdn_url\\s*[:=]\\s*['"]([^'"]+)['"]/);
        if (m) { result.coverImage = m[1]; break; }
    }
    // Fallback to og:image
    if (!result.coverImage) result.coverImage = result.ogImage;

    // 7. comment_id + appmsg_token from page scripts (for appmsg_comment API)
    result.commentId = '';
    result.appmsgToken = '';
    for (const s of scripts) {
        const text = s.textContent || '';
        if (!result.commentId) {
            let m = text.match(/comment_id\\s*[:=]\\s*['"]?(\\d+)['"]?/);
            if (m) result.commentId = m[1];
        }
        if (!result.appmsgToken) {
            let m = text.match(/appmsg_token\\s*[:=]\\s*['"]([^'"]+)['"]/);
            if (m) result.appmsgToken = m[1];
        }
    }

    // 8. Videos — extract from <video> tags, data-mpvid containers, and page scripts
    result.videos = [];
    const seenVids = new Set();

    // 8a. Direct <video src> tags (may not be loaded due to resource blocking)
    document.querySelectorAll('video[src]').forEach(v => {
        const src = v.src || '';
        if (src && !seenVids.has(src)) {
            seenVids.add(src);
            result.videos.push({ src, poster: v.getAttribute('poster') || '' });
        }
    });

    // 8b. span.video_iframe[data-mpvid] / mpvideo[data-mpvid] containers
    const mpvidElements = document.querySelectorAll('span.video_iframe[data-mpvid], mpvideo[data-mpvid]');
    const mpvids = [];
    mpvidElements.forEach(el => {
        const vid = el.getAttribute('data-mpvid') || '';
        if (vid) mpvids.push(vid);
    });

    // 8c. Extract video URLs from page scripts (most reliable — <video> often blocked)
    //     Scripts contain: url: ('http://mpvideo.qpic.cn/xxx.f10002.mp4?...')
    if (mpvids.length > 0) {
        for (const s of scripts) {
            const text = s.textContent || '';
            if (!text.includes('mpvideo.qpic.cn')) continue;
            // Match mpvideo URLs, prefer highest quality (f10002 SD -> f10004 HD)
            // Use string matching instead of regex to avoid Python/JS escape issues
            const lines = text.split('mpvideo.qpic.cn');
            const qualityMap = {};  // {base_name: {quality: url}}
            for (let i = 1; i < lines.length; i++) {
                // Reconstruct URL: find the mp4 part
                const after = lines[i];
                const mp4Idx = after.indexOf('.mp4');
                if (mp4Idx === -1) continue;
                // Extract quality code (f10002, f10004, etc.)
                const fIdx = after.lastIndexOf('.f', mp4Idx);
                if (fIdx === -1) continue;
                const qualStr = after.substring(fIdx + 2, mp4Idx);
                const quality = parseInt(qualStr);
                if (isNaN(quality)) continue;
                // Get path up to end of query params
                let endIdx = mp4Idx + 4;  // past ".mp4"
                // Include query params (stop at quote, space, or backslash)
                while (endIdx < after.length && !/['\"\\s<>]/.test(after[endIdx])) endIdx++;
                const pathAndQuery = after.substring(0, endIdx);
                // Find the protocol prefix from before the split
                const before = lines[i-1];
                let proto = 'http://';
                if (before.endsWith('https://')) proto = 'https://';
                else if (before.endsWith('http://')) proto = 'http://';
                let url = proto + 'mpvideo.qpic.cn' + pathAndQuery;
                // Base name for dedup (before quality suffix)
                const slashIdx = pathAndQuery.indexOf('/');
                const dotFIdx = pathAndQuery.indexOf('.f');
                const base = pathAndQuery.substring(slashIdx >= 0 ? slashIdx + 1 : 0, dotFIdx >= 0 ? dotFIdx : undefined);
                if (!qualityMap[base] || quality > qualityMap[base].quality) {
                    qualityMap[base] = { quality, url };
                }
                if (!qualityMap[base] || quality > qualityMap[base].quality) {
                    qualityMap[base] = { quality, url };
                }
            }
            // Add best quality URL for each unique video
            for (const key in qualityMap) {
                const url = qualityMap[key].url;
                if (!seenVids.has(url)) {
                    seenVids.add(url);
                    result.videos.push({ src: url, vid: mpvids[0] || '' });
                }
            }
            break;  // Only need first script with video URLs
        }
    }

    // 8d. Fallback: if we found mpvid elements but no URLs, store vid for reference
    mpvids.forEach(vid => {
        if (!result.videos.some(v => v.vid === vid)) {
            const el = document.querySelector('[data-mpvid="' + vid + '"]');
            const cover = el ? decodeURIComponent(el.getAttribute('data-cover') || '') : '';
            result.videos.push({ vid, cover, src: '' });
        }
    });

    // 9. Rich content HTML from #js_content
    const content = document.querySelector('#js_content');
    result.html = content ? content.innerHTML : '';
    result.hasContent = !!content;

    // 10. cgiDataNew — WeChat embeds article metadata in window.cgiDataNew
    //    user_info.appmsg_bar_data contains read/like/share/comment counts
    //    NOTE: only populated with real data when accessed via authenticated WeChat session;
    //    anonymous browser access returns empty object {} with all counts = 0.
    result.cgiMetrics = null;
    try {
        if (window.cgiDataNew && window.cgiDataNew.user_info) {
            const bar = window.cgiDataNew.user_info.appmsg_bar_data;
            if (bar && Object.keys(bar).length > 0) {
                result.cgiMetrics = {
                    readNum: bar.read_num || 0,
                    oldLikeNum: bar.old_like_count || 0,
                    likeNum: bar.like_count || 0,
                    shareNum: bar.share_count || 0,
                    commentNum: bar.comment_count || 0,
                };
            }
        }
    } catch(e) {}

    return result;
}"""


def _build_wechat_result(data: dict, page_url: str, md_converter=None) -> dict:
    """Convert raw WeChat JS evaluate output to a standardized result dict.

    Args:
        data: Raw JS evaluate output.
        page_url: Final page URL.
        md_converter: Function to convert HTML to Markdown.
                      If None, uses a simple tag-stripping fallback.
    """
    html = data.get("html", "")

    # Inject video URLs into HTML before markdown conversion.
    # JS evaluate extracts MP4 URLs from page scripts, but the DOM only has
    # span.video_iframe[data-mpvid] without <video src> (blocked by resource filter).
    videos = data.get("videos", [])
    if html and videos:
        video_urls_with_src = [v for v in videos if v.get("src")]
        if video_urls_with_src:
            import re as _re
            for v in video_urls_with_src:
                src = _clean_wechat_video_url(v["src"])
                vid = v.get("vid", "")
                # Try to inject <video src> into the matching span.video_iframe container
                if vid:
                    pattern = (
                        r'(<span[^>]*data-mpvid="' + _re.escape(vid) + r'"[^>]*>)'
                        r'(.*?)(</span>)'
                    )
                    # Use lambda to avoid regex escape interpretation in replacement
                    video_html = f'<video src="{src}"></video>'
                    new_html = _re.sub(
                        pattern,
                        lambda m: m.group(1) + video_html + m.group(3),
                        html, flags=_re.DOTALL,
                    )
                    if new_html != html:
                        html = new_html
                        continue
                # Fallback: append video link at end of content
                html += f'\n<p><a href="{src}">\u25b6 视频</a></p>\n'

    if md_converter and html:
        content = md_converter(html)
    elif html:
        # Simple fallback: strip tags
        import re
        content = re.sub(r'<[^>]+>', '', html)
        content = re.sub(r'\s+', ' ', content).strip()
    else:
        content = ""

    # Determine best cover image
    cover = data.get("coverImage", "") or data.get("ogImage", "")

    # Determine best publish date
    publish_date = ""
    create_time = data.get("createTime", 0)
    if create_time:
        from datetime import datetime
        publish_date = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M")
    elif data.get("publishTime"):
        publish_date = data["publishTime"]

    result = {
        "title": (data.get("title") or "").strip()[:200],
        "content": content,
        "author": (data.get("author") or "").strip(),
        "url": page_url,
        "platform": "wechat",
        "cover_image": cover,
        "publish_date": publish_date,
        "summary": (data.get("ogDescription") or "").strip(),
        "tags": data.get("tags", []),
        "original_url": data.get("originalUrl", ""),
    }

    # cgiMetrics: only present when appmsg_bar_data has real data
    cgi = data.get("cgiMetrics")
    if cgi:
        result["reads"] = cgi.get("readNum", 0)
        result["likes"] = cgi.get("oldLikeNum", 0)
        result["wow"] = cgi.get("likeNum", 0)    # 在看
        result["shares"] = cgi.get("shareNum", 0)
        result["comments"] = cgi.get("commentNum", 0)

    # comment_id + appmsg_token for fetching comment content via appmsg_comment API
    result["comment_id"] = data.get("commentId", "")
    result["appmsg_token"] = data.get("appmsgToken", "")

    # videos extracted from <video> tags and data-mpvid containers
    # Clean JS escape sequences from URLs before passing to downstream consumers
    raw_videos = data.get("videos", [])
    for v in raw_videos:
        if v.get("src"):
            v["src"] = _clean_wechat_video_url(v["src"])
    result["videos"] = raw_videos

    return result


def _clean_wechat_video_url(url: str) -> str:
    """Decode JS hex escapes and HTML entities in WeChat video URLs."""
    # \x26amp; → &amp; → &  (JS hex escape of HTML entity)
    url = url.replace("\\x26amp;", "&").replace("\\x26", "&")
    url = url.replace("&amp;", "&")
    # mpvideo.qpic.cn requires HTTPS
    if url.startswith("http://mpvideo"):
        url = "https://" + url[7:]
    return url


async def evaluate_wechat_article(page, md_converter=None) -> dict:
    """Wait for WeChat article to render, then extract structured data.

    Assumes the page has already navigated to a mp.weixin.qq.com URL.
    Returns standardized result dict with rich metadata.
    """
    try:
        await page.wait_for_selector("#js_content", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)

    data = await page.evaluate(WECHAT_ARTICLE_JS_EVALUATE)

    if not data or not data.get("hasContent"):
        # Fallback: grab raw text
        title = await page.title()
        text = await page.evaluate("() => document.body.innerText")
        return {
            "title": (title or "").strip()[:200],
            "content": (text or "").strip(),
            "author": "",
            "url": page.url,
            "platform": "wechat",
            "cover_image": "",
            "publish_date": "",
            "summary": "",
            "tags": [],
            "original_url": "",
        }

    return _build_wechat_result(data, page.url, md_converter)


async def fetch_wechat_comments(page, comment_id: str, appmsg_token: str = "",
                                max_comments: int = 100) -> list:
    """Fetch article comments via appmsg_comment API in page context.

    Uses page.evaluate(fetch(...)) so the request inherits page cookies.
    Returns standardized comment list:
        [{user_nickname, content, like_count, sub_comments: [{user_nickname, content}]}]
    """
    if not comment_id:
        return []

    js = """
    async (args) => {
        try {
            let url = '/mp/appmsg_comment?action=getcomment&f=json'
                + '&comment_id=' + args.commentId
                + '&offset=0&limit=' + args.limit;
            if (args.appmsgToken) {
                url += '&appmsg_token=' + encodeURIComponent(args.appmsgToken);
            }
            const resp = await fetch(url, { credentials: 'include' });
            const data = await resp.json();
            return data;
        } catch(e) {
            return { error: e.message };
        }
    }
    """
    try:
        raw = await page.evaluate(js, {
            "commentId": comment_id,
            "appmsgToken": appmsg_token,
            "limit": max_comments,
        })
    except Exception as e:
        logger.debug(f"[wechat-comment] evaluate failed: {e}")
        return []

    if not raw or raw.get("error"):
        logger.debug(f"[wechat-comment] API error: {raw}")
        return []

    # Check for WeChat auth failure (ret=-3 "no session")
    ret = raw.get("ret", 0)
    if ret != 0:
        errmsg = raw.get("errmsg", "unknown")
        logger.warning(
            f"[wechat-comment] API 拒绝请求（ret={ret}, {errmsg}）。"
            "微信公众号评论需要微信客户端登录态，普通浏览器登录态不可用。"
        )
        return []

    # Parse response: elected_comment is the main comment list
    elected = raw.get("elected_comment", [])
    if not elected:
        logger.debug("[wechat-comment] No elected_comment in response")
        return []

    comments = []
    for c in elected:
        nick = c.get("nick_name", "匿名")
        text = (c.get("content", "") or "").strip()
        likes = c.get("like_num", 0)

        # Sub-comments (replies)
        sub_comments = []
        for r in c.get("reply_new", {}).get("reply_list", []):
            sub_nick = r.get("nick_name", "匿名")
            sub_text = (r.get("content", "") or "").strip()
            if sub_text:
                sub_comments.append({
                    "user_nickname": sub_nick,
                    "content": sub_text,
                })

        if text:
            comments.append({
                "user_nickname": nick,
                "content": text,
                "like_count": likes,
                "sub_comments": sub_comments,
            })

    logger.info(f"[wechat-comment] Fetched {len(comments)} comments")
    return comments


def get_session_path(platform: str) -> str:
    """Get the session file path for a platform."""
    return str(get_session_dir() / f"{platform}.json")


# ---------------------------------------------------------------------------
# Feishu document JS evaluation
# ---------------------------------------------------------------------------

FEISHU_DOC_JS_EVALUATE = r"""
(() => {
  // Clean zero-width chars from title (Feishu injects them for tracking)
  function cleanTitle(raw) {
    return raw
      .replace(/[\u200B-\u200F\u2028-\u202F\u2060-\u206F\uFEFF]/g, '')
      .replace(/ - 飞书云文档$/, '')
      .replace(/ - Feishu Docs$/, '')
      .replace(/ - Lark Docs$/, '')
      .trim();
  }

  // Title selectors (multiple sources, best effort)
  function getDocTitle() {
    const selectors = [
      '.doc-title', '[data-testid="doc-title"]', '.suite-title-input',
      'div[data-doc-title]', 'h1.title', 'div[contenteditable="true"] h1'
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.innerText.trim()) return el.innerText.trim();
    }
    return '';
  }

  // Get document author from DOM (not logged-in user)
  function getDocAuthor() {
    const selectors = [
      '.docs-info-avatar-name-text',       // main author name text
      '.docs-info-avatar-name',            // author name container
      '.doc-creator', '.creator-name',     // alternative selectors
      '.wiki-creator-name'
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.innerText.trim()) return el.innerText.trim();
    }
    return '';
  }

  // Try new docx editor (window.PageMain)
  const pm = window.PageMain;
  if (pm && pm.blockManager) {
    const root = pm.blockManager.rootBlockModel;
    if (!root) return { error: 'rootBlockModel not found' };

    function serializeBlock(block, depth) {
      if (depth > 20) return null;  // safety limit
      const result = {
        type: block.type || 'unknown',
        children: []
      };

      // Extract zoneState text content
      if (block.zoneState) {
        result.zoneState = {
          allText: block.zoneState.allText || '',
          content: null
        };
        // Extract Delta ops for rich text
        if (block.zoneState.content && block.zoneState.content.ops) {
          result.zoneState.content = {
            ops: block.zoneState.content.ops.map(op => ({
              insert: op.insert || '',
              attributes: op.attributes || {}
            }))
          };
        }
      }

      // Extract snapshot data (images, code, tables, etc.)
      if (block.snapshot) {
        try {
          result.snapshot = JSON.parse(JSON.stringify(block.snapshot));
        } catch (e) {
          result.snapshot = {};
        }
      }

      // Recurse children
      if (block.children && block.children.length) {
        result.children = block.children
          .map(c => serializeBlock(c, depth + 1))
          .filter(Boolean);
      }

      return result;
    }

    // Title: DOM > rootBlock snapshot > cleaned document.title
    const domTitle = getDocTitle();
    const rootTitle = root.snapshot && root.snapshot.title ? root.snapshot.title : '';
    const pageTitle = cleanTitle(document.title);
    const title = domTitle || rootTitle || pageTitle || 'Untitled';

    return {
      title: title,
      url: location.href,
      author: getDocAuthor() || (window.User && window.User.displayName) || '',
      blockTree: serializeBlock(root, 0)
    };
  }

  // Fallback: DOM extraction for read-only/public pages
  const titleEl = getDocTitle();
  const contentEl = document.querySelector('div[role="document"]')
    || document.querySelector('div[data-editor-root]')
    || document.querySelector('div[data-testid="doc-content"]')
    || document.querySelector('main');

  if (contentEl) {
    return {
      title: titleEl || cleanTitle(document.title),
      url: location.href,
      author: getDocAuthor(),
      content: contentEl.innerText || '',
      blockTree: null
    };
  }

  return { error: 'No Feishu editor or document content found' };
})()
"""


async def _connect_feishu_cdp():
    """Try CDP connection to a running Chrome with Feishu logged in.

    Returns ``(pw, browser, page, True)`` on success, or
    ``(None, None, None, False)`` on failure.  Caller must
    ``page.close(); browser.close(); pw.stop()`` when done.
    ``browser.close()`` only disconnects the CDP socket — it does
    **not** kill the user's Chrome process.
    """
    from feedgrab.config import feishu_cdp_enabled, chrome_cdp_port

    if not feishu_cdp_enabled():
        return None, None, None, False

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None, None, None, False

    port = chrome_cdp_port()
    pw = await async_playwright().start()
    try:
        ws_url = f"ws://127.0.0.1:{port}/devtools/browser"
        browser = await pw.chromium.connect_over_cdp(ws_url)

        # Find a context with Feishu cookies
        feishu_domains = (".feishu.cn", ".larksuite.com", ".larkoffice.com")
        target_ctx = None
        for ctx in browser.contexts:
            cookies = await ctx.cookies()
            if any(
                any(c.get("domain", "").endswith(d) for d in feishu_domains)
                for c in cookies
            ):
                target_ctx = ctx
                break

        if not target_ctx:
            logger.info("[Feishu CDP] No context with Feishu cookies found")
            await browser.close()
            await pw.stop()
            return None, None, None, False

        page = await target_ctx.new_page()
        logger.info("[Feishu CDP] Connected to Chrome, new tab created")
        return pw, browser, page, True

    except Exception as e:
        logger.info(f"[Feishu CDP] Connection failed: {e}")
        await pw.stop()
        return None, None, None, False


async def _evaluate_feishu_doc_on_page(
    url: str, page, *, skip_warmup: bool = False,
) -> dict:
    """Execute the full Feishu doc extraction on an already-created *page*.

    Registers response interceptors, navigates, waits for PageMain,
    runs JS evaluate, and handles sheet + image pre-download.

    The caller is responsible for creating and closing the page.

    *skip_warmup* — when True, skips the initial main-domain warmup
    navigation (CDP mode already has the session established).
    """
    from urllib.parse import urlparse

    # Intercept sheet client_vars + lazy block responses for embedded sheets
    sheet_cv_data: dict = {}
    sheet_block_data: dict = {}
    # Intercept image responses during page load (most reliable auth)
    image_response_data: dict = {}  # token -> bytes

    async def _capture_sheet_response(response):
        try:
            if response.status != 200:
                return
            if "client_vars" in response.url:
                body = await response.json()
                if (
                    body.get("code") == 0
                    and body.get("data", {}).get("snapshot")
                ):
                    token = body["data"].get("token", "")
                    sheet_id = body["data"].get("sheetId", "")
                    key = f"{token}_{sheet_id}" if sheet_id else token
                    if key:
                        sheet_cv_data[key] = body["data"]
                        logger.info(
                            f"[Feishu] Intercepted sheet data: {key}"
                        )
                return
            if "/space/api/v3/sheet/block" in response.url:
                body = await response.json()
                blocks = body.get("data", {}).get("blocks", {})
                if blocks:
                    sheet_block_data.update(blocks)
                    logger.info(
                        f"[Feishu] Intercepted sheet blocks: +{len(blocks)} "
                        f"(total {len(sheet_block_data)})"
                    )
        except Exception:
            pass  # non-JSON or parsing error, skip

    async def _capture_image_response(response):
        try:
            url_str = response.url
            if "/stream/download/" not in url_str:
                return
            if response.status != 200:
                return
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                return
            body = await response.body()
            if len(body) < 100:
                return
            # Extract token: .../download/preview/{token}/?... or
            #                 .../download/all/{token}/
            parts = url_str.split("/")
            for i, part in enumerate(parts):
                if part in ("preview", "all") and i + 1 < len(parts):
                    tk = parts[i + 1].split("?")[0]
                    if tk and len(tk) > 5:
                        image_response_data[tk] = body
                        break
        except Exception:
            pass

    page.on("response", _capture_sheet_response)
    page.on("response", _capture_image_response)

    # Disable copy restrictions (reference: 游侠飞书剪存)
    await page.add_init_script("""
        if (window.copyControl && window.copyControl.enable) {
            window.copyControl.enable();
        }
    """)

    # Warm up session: navigate to main domain first to set cookies,
    # then redirect to the actual document.
    if not skip_warmup:
        parsed = urlparse(url)
        main_url = f"{parsed.scheme}://{parsed.netloc}"
        try:
            await page.goto(main_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning(f"[Feishu] Main page warmup failed: {e}")

    await page.goto(url, wait_until="domcontentloaded", timeout=45000)

    # Wait for editor to initialize (PageMain) or content to render
    try:
        await page.wait_for_function(
            "() => window.PageMain?.blockManager?.rootBlockModel != null"
            " || document.querySelector('div[role=\"document\"]') != null",
            timeout=15000,
        )
    except Exception:
        logger.warning("[Feishu] Timed out waiting for editor, trying anyway")

    # Small delay for editor state to stabilize
    await page.wait_for_timeout(1000)

    # Wait for author info to render (lazy-loaded component)
    try:
        from feedgrab.config import feishu_page_load_timeout
        await page.wait_for_selector(
            ".docs-info-avatar-name-text, .docs-info-avatar-name",
            timeout=feishu_page_load_timeout(),
        )
    except Exception:
        pass  # author element may not exist on all pages

    data = await page.evaluate(FEISHU_DOC_JS_EVALUATE)
    result = data or {}

    sheet_tokens = _find_sheet_tokens(result.get("blockTree"))
    merged_sheet_data = dict(sheet_cv_data)

    if sheet_tokens:
        logger.info(
            f"[Feishu] Preloading {len(sheet_tokens)} embedded sheet(s) via stepped scroll"
        )
        try:
            await page.evaluate(
                """async () => {
                    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                    const doc = document.documentElement;
                    const total = Math.max(doc?.scrollHeight || 0, document.body?.scrollHeight || 0);
                    const step = Math.max(Math.floor((window.innerHeight || 800) * 0.8), 640);
                    window.scrollTo(0, 0);
                    await sleep(250);
                    for (let y = 0; y <= total; y += step) {
                        window.scrollTo(0, y);
                        await sleep(450);
                    }
                    window.scrollTo(0, 0);
                    await sleep(250);
                    return true;
                }"""
            )
            await page.wait_for_timeout(2000)
        except Exception as e:
            logger.debug(f"[Feishu] Sheet preload scroll failed: {e}")

        merged_sheet_data = dict(sheet_cv_data)
        missing_tokens = sorted(
            tk for tk in sheet_tokens if tk not in merged_sheet_data
        )
        if merged_sheet_data:
            logger.info(
                f"[Feishu] Intercepted {len(merged_sheet_data)}/"
                f"{len(sheet_tokens)} sheet(s); missing {len(missing_tokens)}"
            )
        if missing_tokens:
            logger.info(
                f"[Feishu] Fetching {len(missing_tokens)} missing sheet(s) "
                "via internal API"
            )
            fetched = await page.evaluate(
                FEISHU_SHEET_FETCH_JS, missing_tokens
            )
            if fetched:
                merged_sheet_data.update(fetched)
                still_missing = sorted(
                    tk for tk in sheet_tokens if tk not in merged_sheet_data
                )
                logger.info(
                    f"[Feishu] Active fetch got {len(fetched)} sheet(s); "
                    f"still missing {len(still_missing)}"
                )

    if merged_sheet_data:
        result["sheet_client_vars"] = merged_sheet_data
    if sheet_block_data:
        result["sheet_blocks"] = dict(sheet_block_data)

    # Collect pre-downloaded images from interceptor + JS fetch fallback
    try:
        from feedgrab.config import feishu_download_images
        if feishu_download_images():
            img_tokens = _find_image_tokens_from_tree(
                result.get("blockTree")
            )
            if img_tokens:
                img_bytes: dict[str, bytes] = {}

                # Phase 1: use images captured by response interceptor
                for tk in img_tokens:
                    if tk in image_response_data:
                        img_bytes[tk] = image_response_data[tk]

                intercepted = len(img_bytes)
                missing = [
                    tk for tk in img_tokens if tk not in img_bytes
                ]

                # Phase 2: scroll to trigger lazy-loaded images
                if missing:
                    await page.evaluate(
                        "window.scrollTo(0, "
                        "document.body.scrollHeight)"
                    )
                    await page.wait_for_timeout(3000)
                    for tk in list(missing):
                        if tk in image_response_data:
                            img_bytes[tk] = image_response_data[tk]
                            missing.remove(tk)

                # Phase 3: discover real CDN domain from DOM, then
                # JS fetch with correct URL pattern
                if missing:
                    cdn_info = await page.evaluate(
                        _FEISHU_IMAGE_CDN_DISCOVER_JS
                    )
                    if cdn_info:
                        logger.info(
                            f"[Feishu] CDN: {cdn_info['cdn']}, "
                            f"mount: "
                            f"{cdn_info['mount_token'][:12]}..., "
                            f"fetching {len(missing)} images"
                        )
                        batch_size = 10
                        for s in range(0, len(missing), batch_size):
                            batch = missing[s:s + batch_size]
                            fetched = await page.evaluate(
                                _FEISHU_IMAGE_FETCH_JS,
                                {
                                    "tokens": batch,
                                    "cdn": cdn_info["cdn"],
                                    "mount_token": cdn_info[
                                        "mount_token"
                                    ],
                                },
                            )
                            if not fetched:
                                continue
                            # Log first batch
                            if s == 0:
                                for tk, v in list(
                                    fetched.items()
                                )[:3]:
                                    p = (v[:40]
                                         if isinstance(v, str)
                                         else "bytes")
                                    logger.info(
                                        f"[Feishu] fetch "
                                        f"{tk[:12]}... → {p}"
                                    )
                            import base64
                            for tk, b64 in fetched.items():
                                if (b64 and not b64.startswith(
                                        "error:")):
                                    try:
                                        raw = base64.b64decode(b64)
                                        if len(raw) > 100:
                                            img_bytes[tk] = raw
                                    except Exception:
                                        pass
                    else:
                        logger.info(
                            "[Feishu] No CDN info from DOM"
                        )

                if img_bytes:
                    result["_image_bytes"] = img_bytes
                    logger.info(
                        f"[Feishu] Pre-downloaded "
                        f"{len(img_bytes)}/{len(img_tokens)} "
                        f"images ({intercepted} intercepted, "
                        f"{len(img_bytes) - intercepted} fetched)"
                    )
    except Exception as e:
        logger.debug(f"[Feishu] Image pre-download skipped: {e}")

    return result


async def evaluate_feishu_doc(url: str, session_path: str) -> dict:
    """Extract Feishu document content via browser.

    Tries CDP direct-connect first (zero startup, reuses Chrome login),
    then falls back to launching a new browser instance.

    Uses headed mode with vanilla playwright (not patchright) because Feishu
    refuses connections from patchright's CDP-patched Chromium.

    Also intercepts sheet ``client_vars`` API responses for embedded
    spreadsheet data extraction (Protobuf cell blocks).
    """
    # ── Tier 0.5: CDP direct connect ─────────────────────────
    pw_cdp, browser_cdp, page_cdp, via_cdp = await _connect_feishu_cdp()
    if via_cdp:
        try:
            return await _evaluate_feishu_doc_on_page(
                url, page_cdp, skip_warmup=True,
            )
        except Exception as e:
            logger.warning(
                f"[Feishu CDP] Extraction failed: {e}, falling back to launch"
            )
        finally:
            try:
                await page_cdp.close()
            except Exception:
                pass
            try:
                await browser_cdp.close()
            except Exception:
                pass
            try:
                await pw_cdp.stop()
            except Exception:
                pass

    # ── Tier 1: Launch new browser ────────────────────────────
    # Use vanilla playwright for Feishu — patchright causes ERR_CONNECTION_CLOSED
    try:
        from playwright.async_api import async_playwright as _pw_factory
    except ImportError:
        # Fall back to whatever is available
        _pw_factory = get_async_playwright()

    pw = await _pw_factory().start()
    browser = None
    try:
        browser = await pw.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx_opts = get_stealth_context_options(storage_state=session_path)
        context = await browser.new_context(**ctx_opts)
        # NOTE: skip resource blocking for Feishu — its internal APIs and
        # scripts are essential for window.PageMain to initialise.
        page = await context.new_page()

        return await _evaluate_feishu_doc_on_page(url, page)
    finally:
        if browser:
            await browser.close()
        await pw.stop()


def _find_sheet_tokens(block_tree: dict | None) -> set:
    """Recursively find sheet embed tokens in the extracted block tree."""
    tokens: set = set()
    if not block_tree or not isinstance(block_tree, dict):
        return tokens

    def _walk(node):
        if not isinstance(node, dict):
            return
        snap = node.get("snapshot")
        if isinstance(snap, dict):
            if snap.get("type") == "sheet" and snap.get("token"):
                tokens.add(snap["token"])
        for child in node.get("children", []):
            _walk(child)

    _walk(block_tree)
    return tokens


def _find_image_tokens_from_tree(block_tree: dict | None) -> list:
    """Recursively find image tokens in the extracted block tree.

    Checks both ``block["image"]["token"]`` (API response format) and
    ``block["snapshot"]["image"]["token"]`` (Playwright format), mirroring
    the logic in ``feishu._get_image_token()``.
    """
    tokens: list = []
    if not block_tree or not isinstance(block_tree, dict):
        return tokens

    def _walk(node):
        if not isinstance(node, dict):
            return
        # Path 1: block["image"]["token"] (API response)
        img = node.get("image", {})
        if isinstance(img, dict) and img.get("token"):
            tokens.append(img["token"])
        else:
            # Path 2: block["snapshot"]["image"]["token"] (Playwright)
            snap = node.get("snapshot", {})
            if isinstance(snap, dict):
                simg = snap.get("image", {})
                if isinstance(simg, dict) and simg.get("token"):
                    tokens.append(simg["token"])
        for child in node.get("children", []):
            _walk(child)

    _walk(block_tree)
    return tokens


# JS to discover the actual CDN domain and mount_node_token for Feishu images.
# Images in Feishu docs are loaded from a central CDN (e.g.
# internal-api-drive-stream.feishu.cn), not from the page's enterprise domain.
_FEISHU_IMAGE_CDN_DISCOVER_JS = """
(() => {
    // Find the first loaded <img> that uses the drive-stream CDN
    const imgs = document.querySelectorAll('img');
    for (const img of imgs) {
        const src = img.src || '';
        const match = src.match(
            /(https:\\/\\/[^/]*(?:drive-stream|internal-api)[^/]*)\\/space\\/api\\/box\\/stream\\/download\\/v2\\/cover\\/([^/]+)\\/\\?.*mount_node_token=([^&]+)/
        );
        if (match) {
            return { cdn: match[1], sample_token: match[2], mount_token: match[3] };
        }
    }
    return null;
})()
"""

# JS to download images via the discovered CDN URL.  Runs in the page's
# security context.  Accepts {tokens, cdn, mount_token} as argument.
_FEISHU_IMAGE_FETCH_JS = """
async (args) => {
    const { tokens, cdn, mount_token } = args;
    const results = {};
    for (const token of tokens) {
        try {
            const url = `${cdn}/space/api/box/stream/download/v2/cover/${token}/?fallback_source=1&height=1280&mount_node_token=${mount_token}&mount_point=docx_image&policy=equal&width=1280`;
            const resp = await fetch(url, { credentials: 'include' });
            if (resp.ok) {
                const buf = await resp.arrayBuffer();
                if (buf.byteLength > 100) {
                    const bytes = new Uint8Array(buf);
                    let binary = '';
                    const chunkSize = 8192;
                    for (let i = 0; i < bytes.length; i += chunkSize) {
                        binary += String.fromCharCode.apply(
                            null, bytes.subarray(i, i + chunkSize)
                        );
                    }
                    results[token] = btoa(binary);
                } else {
                    results[token] = 'error:empty';
                }
            } else {
                results[token] = 'error:' + resp.status;
            }
        } catch (e) {
            results[token] = 'error:' + e.message;
        }
    }
    return results;
}
"""

# JS to fetch sheet client_vars via internal API from the page context.
# The request body format was reverse-engineered from Feishu sheet editor
# network traffic. Key: sheetId must be inside sheetRange, and memberId
# is extracted from the page's global User object.
FEISHU_SHEET_FETCH_JS = """
async (tokens) => {
    const results = {};
    const csrfToken = (document.cookie.match(/_csrf_token=([^;]+)/) || [])[1] || '';
    let memberId = 0;
    try {
        memberId = window.User?.id || window.__INITIAL_STATE__?.user?.id || 0;
    } catch(e) {}
    const headers = {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
        'Referer': location.href
    };

    for (const fullToken of tokens) {
        try {
            const idx = fullToken.lastIndexOf('_');
            const hasSheetHint = idx > 0;
            const ssToken = hasSheetHint ? fullToken.substring(0, idx) : fullToken;
            const hintedSheetId = hasSheetHint ? fullToken.substring(idx + 1) : '';

            const body = {
                schemaVersion: 9,
                openType: 1,
                token: ssToken,
                clientVersion: 'v0.0.1'
            };
            if (hintedSheetId) {
                body.sheetRange = { sheetId: hintedSheetId };
            }
            if (memberId) body.memberId = memberId;

            const resp = await fetch('/space/api/v3/sheet/client_vars', {
                method: 'POST',
                credentials: 'include',
                headers: headers,
                body: JSON.stringify(body)
            });
            const data = await resp.json();
            if (data.code === 0 && data.data && data.data.snapshot) {
                const realToken = data.data.token || ssToken;
                const realSheetId = data.data.sheetId || hintedSheetId || '';
                const key = realSheetId ? `${realToken}_${realSheetId}` : realToken;
                results[key] = data.data;
                continue;
            }
            console.info(
                `[Feishu] sheet client_vars miss for ${fullToken}: ` +
                `${data.code ?? 'unknown'} ${data.msg ?? ''}`
            );
        } catch (e) {
            console.info(
                `[Feishu] sheet client_vars error for ${fullToken}: ${String(e)}`
            );
        }
    }
    return Object.keys(results).length > 0 ? results : null;
}
"""
