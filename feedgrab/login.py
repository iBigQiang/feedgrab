# -*- coding: utf-8 -*-
"""
Login manager — opens a browser for manual login, saves session.

Usage:
    feedgrab login xhs              # Visible browser login
    feedgrab login xhs --headless   # Headless: saves QR screenshot for scanning

Sessions are saved as Playwright storage_state JSON files.

When CHROME_CDP_LOGIN=true, feedgrab first extracts existing cookies from a
running Chrome instance via CDP (Chrome DevTools Protocol). If no matching
cookies are available, it opens the platform login page in that CDP Chrome,
then falls back to the normal Playwright login window if CDP fails.
"""

import contextlib
import json
import os
import time
from pathlib import Path
from loguru import logger

from feedgrab.config import get_session_dir, get_user_agent

PLATFORM_URLS = {
    "xhs": "https://www.xiaohongshu.com/explore",
    "xiaohongshu": "https://www.xiaohongshu.com/explore",
    "wechat": "https://mp.weixin.qq.com",
    "twitter": "https://x.com/login",
    "x": "https://x.com/login",
    "feishu": "https://my.feishu.cn",
    "lark": "https://my.feishu.cn",
    "kdocs": "https://www.kdocs.cn",
    "zhihu": "https://www.zhihu.com",
    "linuxdo": "https://linux.do/",
    "idcflare": "https://idcflare.com/",
    "zsxq": "https://wx.zsxq.com/",
    "flowus": "https://flowus.cn/login",
}


def _save_session(context, session_path: Path) -> None:
    """Save session and set restrictive permissions."""
    context.storage_state(path=str(session_path))
    os.chmod(str(session_path), 0o600)
    logger.info(f"Session saved: {session_path}")
    print(f"\n✅ 登录态已保存到 {session_path}")


def _session_dir() -> Path:
    return get_session_dir()


def _resolve_canonical(platform: str) -> str:
    if platform in ("xhs", "xiaohongshu"):
        return "xhs"
    if platform in ("twitter", "x"):
        return "twitter"
    if platform in ("feishu", "lark"):
        return "feishu"
    if platform == "kdocs":
        return "kdocs"
    return platform


def login(platform: str, headless: bool = False) -> None:
    """
    Open a browser for the user to log in manually.
    After login, saves cookies/localStorage to a session file.

    When CHROME_CDP_LOGIN=true, prefers a running CDP Chrome for cookie
    extraction/login, then falls back to a new Playwright browser if needed.

    Args:
        platform: Platform key (e.g. 'xhs', 'wechat')
        headless: If True, run headless and save QR screenshot for user to scan
    """
    platform = platform.lower()
    if platform not in PLATFORM_URLS:
        supported = ", ".join(sorted(PLATFORM_URLS.keys()))
        print(f"不支持的平台：{platform}")
        print(f"   支持的平台：{supported}")
        return

    session_dir = _session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)
    canonical = _resolve_canonical(platform)
    session_path = session_dir / f"{canonical}.json"

    # --- CDP mode: extract from running Chrome ---
    from feedgrab.config import chrome_cdp_login
    if chrome_cdp_login():
        ok = _login_via_cdp(canonical, session_path)
        if ok:
            return
        ok = _login_interactive_via_cdp(canonical, PLATFORM_URLS[platform], session_path)
        if ok:
            return
        print("CDP 提取未成功，改用普通浏览器登录...")

    # --- Normal browser login ---
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print(
            "未安装 Playwright。请运行：\n"
            '   pip install "feedgrab[browser]"\n'
            "   playwright install chromium"
        )
        return

    if headless:
        _login_headless(PLATFORM_URLS[platform], session_path, canonical)
    else:
        _login_visible(PLATFORM_URLS[platform], session_path, platform)


def _login_visible(login_url: str, session_path: Path, platform: str) -> None:
    from playwright.sync_api import sync_playwright

    print(f"🌐 正在打开 {platform} 登录页：{login_url}")
    print("   请在浏览器窗口中手动完成登录。")
    print("   登录成功后请关闭浏览器窗口，feedgrab 会保存登录态。\n")

    with sync_playwright() as p:
        # Prefer real Chrome channel over bundled Chromium to reduce login friction.
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=get_user_agent(),
        )
        page = context.new_page()
        page.goto(login_url)

        try:
            page.wait_for_event("close", timeout=0)
        except KeyboardInterrupt:
            pass

        _save_session(context, session_path)
        context.close()
        browser.close()


def _login_headless(login_url: str, session_path: Path, canonical: str) -> None:
    from playwright.sync_api import sync_playwright

    qr_path = session_path.parent / f"{canonical}_qr.png"

    print(f"🔐 无头登录：{login_url}")
    print(f"   二维码截图将保存到：{qr_path}")
    print("   正在等待登录（超时：5 分钟）...\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=get_user_agent(),
        )
        page = context.new_page()
        page.goto(login_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Save QR screenshot
        page.screenshot(path=str(qr_path))
        print(f"📸 二维码截图已保存：{qr_path}")
        print("   请打开这张图片并用手机扫码登录。\n")

        # Poll for cookie change (login detection)
        initial_cookies = len(context.cookies())
        timeout = 300  # 5 min
        start = time.time()
        logged_in = False

        try:
            while time.time() - start < timeout:
                time.sleep(3)
                current_cookies = len(context.cookies())
                if current_cookies > initial_cookies + 2:
                    logger.info(f"Cookie count changed: {initial_cookies} -> {current_cookies}")
                    # Wait a bit more for all cookies to settle
                    page.wait_for_timeout(2000)
                    logged_in = True
                    break
        except KeyboardInterrupt:
            pass

        if logged_in:
            _save_session(context, session_path)
        else:
            print("\n⏹ 登录超时或已取消，未保存登录态。")

        context.close()
        browser.close()


# ── CDP Cookie extraction ────────────────────────────────────

# Domain substrings to filter cookies for each platform
_CDP_COOKIE_DOMAINS = {
    "twitter": [".x.com", ".twitter.com"],
    "xhs": [".xiaohongshu.com"],
    "wechat": [".qq.com"],
    "feishu": [".feishu.cn", ".larksuite.com", ".larkoffice.com"],
    "kdocs": [".kdocs.cn", ".wps.cn"],
    "zhihu": [".zhihu.com"],
    "linuxdo": [".linux.do"],
    "idcflare": [".idcflare.com"],
    "zsxq": [".zsxq.com"],
    "flowus": [".flowus.cn", "flowus.cn"],
}

# URLs to pass to Network.getCookies (legacy WebSocket mode)
_CDP_COOKIE_URLS = {
    "twitter": ["https://x.com", "https://twitter.com"],
    "xhs": ["https://www.xiaohongshu.com"],
    "wechat": ["https://mp.weixin.qq.com"],
    "feishu": ["https://my.feishu.cn", "https://open.feishu.cn"],
    "kdocs": ["https://www.kdocs.cn"],
    "zhihu": ["https://www.zhihu.com"],
    "linuxdo": ["https://linux.do"],
    "idcflare": ["https://idcflare.com"],
    "zsxq": ["https://wx.zsxq.com", "https://articles.zsxq.com", "https://api.zsxq.com"],
    "flowus": ["https://flowus.cn"],
}


def _login_via_cdp(canonical: str, session_path: Path) -> bool:
    """Extract cookies from a running Chrome via CDP and save as Playwright storage_state.

    Two strategies:
      Tier 0: Playwright connect_over_cdp(ws://) — Chrome 146+ Remote Debugging
      Tier 1: HTTP /json/version + WebSocket — traditional --remote-debugging-port

    Returns True on success, False on failure (caller should fall back).
    """
    from feedgrab.config import chrome_cdp_port
    port = chrome_cdp_port()

    cookie_domains = _CDP_COOKIE_DOMAINS.get(canonical)
    if not cookie_domains:
        print(f"当前平台未配置 CDP 登录：{canonical}")
        return False

    # Tier 0: Playwright connect_over_cdp (Chrome 146+ compatible)
    ok = _cdp_via_playwright(canonical, port, cookie_domains, session_path)
    if ok:
        return True

    # Tier 1: legacy HTTP discovery + raw WebSocket
    cookie_urls = _CDP_COOKIE_URLS.get(canonical, [])
    ok = _cdp_via_websocket(canonical, port, cookie_urls, session_path)
    if ok:
        return True

    print(f"无法连接 Chrome CDP：127.0.0.1:{port}")
    print("   请启用远程调试：chrome://inspect/#remote-debugging")
    print(f"   或用参数启动 Chrome：--remote-debugging-port={port}")
    return False


def _login_interactive_via_cdp(canonical: str, login_url: str, session_path: Path) -> bool:
    """Open the platform login page in the running CDP Chrome, then save cookies."""
    from feedgrab.config import chrome_cdp_port

    port = chrome_cdp_port()
    cookie_domains = _CDP_COOKIE_DOMAINS.get(canonical)
    if not cookie_domains:
        print(f"当前平台未配置 CDP 登录：{canonical}")
        return False

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("Playwright not installed, skipping interactive CDP login")
        return False

    timeout = _cdp_login_timeout()
    ws_url = f"ws://127.0.0.1:{port}/devtools/browser"
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(ws_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = _prepare_cdp_login_page(context, login_url)
            page.wait_for_timeout(2000)
            initial_signature = _cookie_signature(
                _filter_cdp_platform_cookies(context.cookies(), cookie_domains)
            )
            print(f"🌐 正在通过 Chrome CDP 打开 {canonical} 登录页：{login_url}")
            print("   请在 Chrome 窗口完成登录，feedgrab 会自动检测并保存登录态。\n")

            start = time.time()
            while time.time() - start < timeout:
                platform_cookies = _filter_cdp_platform_cookies(context.cookies(), cookie_domains)
                current_signature = _cookie_signature(platform_cookies)
                if platform_cookies and current_signature != initial_signature:
                    _write_cdp_storage_state(session_path, platform_cookies, source="interactive CDP")
                    with contextlib.suppress(Exception):
                        page.close()
                    browser.close()
                    return True
                if page.is_closed():
                    if platform_cookies and current_signature != initial_signature:
                        _write_cdp_storage_state(session_path, platform_cookies, source="interactive CDP")
                        browser.close()
                        return True
                    break
                with contextlib.suppress(Exception):
                    page.wait_for_timeout(2000)
            browser.close()
    except Exception as e:
        logger.debug(f"Interactive CDP login failed: {e}")
        return False

    print("\n⏹ CDP 登录超时，或未检测到新的 Cookie，未保存登录态。")
    return False


def _prepare_cdp_login_page(context, login_url: str):
    page = _select_cdp_login_page(context)
    with contextlib.suppress(Exception):
        page.bring_to_front()
    page.goto(login_url, wait_until="domcontentloaded")
    _close_extra_blank_cdp_pages(context, page)
    return page


def _select_cdp_login_page(context):
    for page in list(getattr(context, "pages", []) or []):
        if _page_is_closed(page):
            continue
        if _is_blank_cdp_page_url(_safe_page_url(page)):
            return page
    return context.new_page()


def _safe_page_url(page) -> str:
    with contextlib.suppress(Exception):
        return str(getattr(page, "url", "") or "")
    return ""


def _page_is_closed(page) -> bool:
    with contextlib.suppress(Exception):
        return bool(page.is_closed())
    return False


def _close_extra_blank_cdp_pages(context, keep_page) -> None:
    for page in list(getattr(context, "pages", []) or []):
        if page is keep_page or _page_is_closed(page):
            continue
        if _is_blank_cdp_page_url(_safe_page_url(page)):
            with contextlib.suppress(Exception):
                page.close()


def _is_blank_cdp_page_url(url: str) -> bool:
    text = str(url or "").strip().lower()
    return not text or text == "about:blank" or text.startswith("chrome://newtab") or text.startswith("chrome://new-tab-page")


def _cdp_via_playwright(
    canonical: str, port: int, cookie_domains: list, session_path: Path,
) -> bool:
    """Tier 0: connect via Playwright's connect_over_cdp with ws:// URL.

    Chrome 146's built-in Remote Debugging doesn't expose HTTP /json/* endpoints,
    but Playwright can connect directly via ws://127.0.0.1:{port}/devtools/browser.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("Playwright not installed, skipping Tier 0 CDP")
        return False

    ws_url = f"ws://127.0.0.1:{port}/devtools/browser"
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(ws_url)
            all_cookies = []
            for ctx in browser.contexts:
                all_cookies.extend(ctx.cookies())
            browser.close()
    except Exception as e:
        logger.debug(f"Playwright CDP connection failed: {e}")
        return False

    if not all_cookies:
        logger.debug("CDP connected but no cookies found")
        return False

    platform_cookies = _filter_cdp_platform_cookies(all_cookies, cookie_domains)

    if not platform_cookies:
        print(f"未找到 {canonical} Cookie。请确认已在 Chrome 中登录对应网站。")
        return False

    _write_cdp_storage_state(session_path, platform_cookies, source="Playwright CDP")
    return True


def _cdp_via_websocket(
    canonical: str, port: int, cookie_urls: list, session_path: Path,
) -> bool:
    """Tier 1: legacy HTTP discovery + raw WebSocket for --remote-debugging-port mode."""
    import urllib.request

    base = f"http://127.0.0.1:{port}"

    # Check CDP reachable via HTTP
    try:
        req = urllib.request.Request(f"{base}/json/version", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            version_info = json.loads(resp.read())
        browser_name = version_info.get("Browser", "Chrome")
        logger.info(f"CDP connected (legacy): {browser_name} on port {port}")
    except Exception:
        return False

    # Get browser-level WebSocket URL
    ws_url = version_info.get("webSocketDebuggerUrl")
    if not ws_url:
        logger.debug("No WebSocket URL in /json/version")
        return False

    # Extract cookies via WebSocket
    try:
        import websocket
    except ImportError:
        print("未安装 websocket-client。请运行：pip install websocket-client")
        return False

    try:
        ws = websocket.create_connection(ws_url, timeout=5)
        ws.send(json.dumps({
            "id": 1,
            "method": "Network.getCookies",
            "params": {"urls": cookie_urls},
        }))
        result = json.loads(ws.recv())
        ws.close()

        cookies = result.get("result", {}).get("cookies", [])
    except Exception as e:
        logger.debug(f"CDP WebSocket cookie extraction failed: {e}")
        return False

    if not cookies:
        print(f"未找到 {canonical} Cookie。请确认已登录。")
        return False

    # Convert CDP cookies to Playwright storage_state format
    pw_cookies = []
    for c in cookies:
        expires = c.get("expires", -1)
        if expires == 0:  # CDP session cookie → Playwright -1
            expires = -1
        pw_cookies.append({
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "expires": expires,
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": c.get("sameSite", "None"),
        })

    _write_cdp_storage_state(session_path, pw_cookies, source="WebSocket CDP")
    return True


def _filter_cdp_platform_cookies(cookies: list, cookie_domains: list) -> list:
    return [
        c for c in cookies
        if any(c.get("domain", "").endswith(d) or c.get("domain", "") == d.lstrip(".")
               for d in cookie_domains)
    ]


def _cookie_signature(cookies: list) -> set[tuple[str, str, str]]:
    return {
        (str(cookie.get("domain", "")), str(cookie.get("name", "")), str(cookie.get("value", "")))
        for cookie in cookies
    }


def _write_cdp_storage_state(session_path: Path, cookies: list, *, source: str) -> None:
    session_path.parent.mkdir(parents=True, exist_ok=True)
    state = {"cookies": cookies, "origins": []}
    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.chmod(str(session_path), 0o600)
    logger.info(f"CDP session saved ({source}): {session_path}")
    print(f"\n✅ 已通过 CDP 保存登录态：{session_path}（{len(cookies)} 个 Cookie）")


def _cdp_login_timeout() -> int:
    try:
        return max(10, int(os.getenv("CHROME_CDP_LOGIN_TIMEOUT", "300")))
    except ValueError:
        return 300
