# -*- coding: utf-8 -*-
"""
Login manager — opens a browser for manual login, saves session.

Usage:
    feedgrab login xhs              # Visible browser login
    feedgrab login xhs --headless   # Headless: saves QR screenshot for scanning

Sessions are saved as Playwright storage_state JSON files.

When CHROME_CDP_LOGIN=true, cookies are extracted directly from a running
Chrome instance via CDP (Chrome DevTools Protocol), skipping the browser
login flow entirely.
"""

import json
import os
import time
from pathlib import Path
from loguru import logger

from feedgrab.config import get_session_dir, get_user_agent

SESSION_DIR = get_session_dir()

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
    print(f"\n✅ Session saved to {session_path}")


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

    When CHROME_CDP_LOGIN=true, extracts cookies from a running Chrome
    instance via CDP instead of opening a new browser.

    Args:
        platform: Platform key (e.g. 'xhs', 'wechat')
        headless: If True, run headless and save QR screenshot for user to scan
    """
    platform = platform.lower()
    if platform not in PLATFORM_URLS:
        supported = ", ".join(sorted(PLATFORM_URLS.keys()))
        print(f"Unknown platform: {platform}")
        print(f"   Supported: {supported}")
        return

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    canonical = _resolve_canonical(platform)
    session_path = SESSION_DIR / f"{canonical}.json"

    # --- CDP mode: extract from running Chrome ---
    from feedgrab.config import chrome_cdp_login
    if chrome_cdp_login():
        ok = _login_via_cdp(canonical, session_path)
        if ok:
            return
        print("CDP extraction failed, falling back to browser login...")

    # --- Normal browser login ---
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print(
            "Playwright is not installed. Run:\n"
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

    print(f"🌐 Opening {platform} login page: {login_url}")
    print("   Please log in manually in the browser window.")
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

    qr_path = SESSION_DIR / f"{canonical}_qr.png"

    print(f"🔐 Headless login: {login_url}")
    print(f"   QR code will be saved to: {qr_path}")
    print("   Waiting for login (timeout: 5 min)...\n")

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
        print(f"📸 QR screenshot saved: {qr_path}")
        print("   Open this image and scan the QR code with your phone.\n")

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
            print("\n⏹ Login timed out or cancelled. No session saved.")

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
        print(f"CDP login not configured for platform: {canonical}")
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

    print(f"Chrome CDP not reachable on 127.0.0.1:{port}")
    print("   Enable Remote Debugging: chrome://inspect/#remote-debugging")
    print(f"   Or launch Chrome with: --remote-debugging-port={port}")
    return False


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

    # Filter cookies for target platform
    platform_cookies = [
        c for c in all_cookies
        if any(c.get("domain", "").endswith(d) or c.get("domain", "") == d.lstrip(".")
               for d in cookie_domains)
    ]

    if not platform_cookies:
        print(f"No {canonical} cookies found. Are you logged in to the site in Chrome?")
        return False

    # Playwright cookies are already in storage_state format
    state = {"cookies": platform_cookies, "origins": []}
    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.chmod(str(session_path), 0o600)

    logger.info(f"CDP session saved (Playwright): {session_path}")
    print(f"\n✅ Session saved via CDP: {session_path} ({len(platform_cookies)} cookies)")
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
        print("websocket-client not installed. Run: pip install websocket-client")
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
        print(f"No cookies found for {canonical}. Are you logged in?")
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

    state = {"cookies": pw_cookies, "origins": []}
    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.chmod(str(session_path), 0o600)

    logger.info(f"CDP session saved (WebSocket): {session_path}")
    print(f"\n✅ Session saved via CDP: {session_path} ({len(pw_cookies)} cookies)")
    return True
