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
import shutil
import subprocess
import tempfile
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
    "reddit": "https://www.reddit.com/login/",
}


def _save_session(context, session_path: Path) -> None:
    """Save session and set restrictive permissions."""
    context.storage_state(path=str(session_path))
    os.chmod(str(session_path), 0o600)
    logger.info(f"Session saved: {session_path}")
    print(f"\n✅ 登录态已保存到 {session_path}")


def _session_dir() -> Path:
    return get_session_dir()


def _login_session_path(session_dir: Path, canonical: str) -> Path:
    configured = os.getenv("FEEDGRAB_LOGIN_SESSION_PATH", "").strip()
    if not configured:
        return session_dir / f"{canonical}.json"

    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = session_dir / path
    return path


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
    session_path = _login_session_path(session_dir, canonical)
    if canonical == "reddit" and headless:
        print("Reddit 不支持 headless 自动登录。请使用 feedgrab login reddit 走普通 Chrome/CDP 手动登录，避免触发异常登录拦截。")
        return
    force_interactive_login = os.getenv("FEEDGRAB_FORCE_INTERACTIVE_LOGIN", "").strip().lower() in {
        "true",
        "1",
        "yes",
        "on",
    }

    # --- CDP mode: extract from running Chrome ---
    from feedgrab.config import chrome_cdp_login
    cdp_login_enabled = chrome_cdp_login()
    prefer_managed_cdp_login = canonical == "reddit" and not headless
    if cdp_login_enabled:
        if not force_interactive_login:
            ok = _login_via_cdp(canonical, session_path)
            if ok:
                return
        ok = _login_interactive_via_cdp(canonical, PLATFORM_URLS[platform], session_path)
        if ok:
            return
        if prefer_managed_cdp_login and _start_managed_chrome_cdp(canonical, session_path):
            ok = _login_interactive_via_cdp(canonical, PLATFORM_URLS[platform], session_path)
            if ok:
                return
            print("Reddit CDP 登录未检测到新的 Cookie，已停止本次登录流程。")
            return
        if prefer_managed_cdp_login:
            print("Reddit 登录未能启动普通 Chrome CDP，未使用 Playwright 受控浏览器。")
            return
        print("CDP 提取未成功，改用普通浏览器登录...")

    if prefer_managed_cdp_login:
        if _start_managed_chrome_cdp(canonical, session_path):
            ok = _login_interactive_via_cdp(canonical, PLATFORM_URLS[platform], session_path)
            if ok:
                return
            print("Reddit CDP 登录未检测到新的 Cookie，已停止本次登录流程。")
            return
        print("Reddit 登录未能启动普通 Chrome CDP，未使用 Playwright 受控浏览器。")
        return

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
    print("   登录成功后请保持窗口打开，feedgrab 检测到登录态后会自动保存并关闭窗口。\n")

    with sync_playwright() as p:
        canonical = _resolve_canonical(platform)
        profile_parent = _visible_login_profile_parent(session_path)
        profile_parent.mkdir(parents=True, exist_ok=True)
        profile_key = _login_profile_key(session_path, canonical)

        with tempfile.TemporaryDirectory(
            prefix=f"{profile_key}-",
            dir=str(profile_parent),
            ignore_cleanup_errors=True,
        ) as profile_dir:
            # Persistent Chrome keeps login fingerprint closer to a normal browser,
            # while the temporary profile guarantees each manual login starts clean.
            context = p.chromium.launch_persistent_context(
                profile_dir,
                headless=False,
                channel="chrome",
                user_agent=get_user_agent(),
                args=_visible_login_args(),
                ignore_default_args=["--enable-automation", "--no-sandbox"],
            )
            page = _prepare_login_page(context, login_url)

            try:
                saved = _wait_for_visible_login_and_save(context, page, session_path, canonical)
                if not saved:
                    print("\n⏹ 未检测到有效登录态，未保存登录信息。请保持窗口打开直到提示已保存。")
            except KeyboardInterrupt:
                pass
            except Exception as exc:
                if _is_playwright_target_closed_error(exc):
                    print(
                        "\n⚠️ 登录窗口已关闭，未能在关闭前读取登录态。"
                        "请重新点击登录，扫码成功后等待“登录态已保存”提示再关闭窗口。"
                    )
                else:
                    raise
            finally:
                with contextlib.suppress(Exception):
                    context.close()


def _wait_for_visible_login_and_save(context, page, session_path: Path, canonical: str) -> bool:
    initial_signature = _visible_cookie_signature(context, canonical)
    requires_cookie_change = _visible_login_requires_cookie_change(canonical)
    requires_page_success = _visible_login_requires_page_success(canonical)
    warned_insufficient = False

    while True:
        if _safe_page_is_closed(page):
            return False

        platform_cookies = _visible_platform_cookies(context, canonical)
        current_signature = _cookie_signature(platform_cookies)
        login_cookie_changed = current_signature != initial_signature
        login_page_ready = _visible_page_sufficient_for_login(canonical, page)
        if _cookies_sufficient_for_login(canonical, platform_cookies) and (
            not requires_cookie_change or login_cookie_changed
        ) and (not requires_page_success or login_page_ready):
            _save_session(context, session_path)
            return True

        if platform_cookies and login_cookie_changed:
            if not warned_insufficient:
                _print_insufficient_cdp_cookies(canonical, len(platform_cookies))
                warned_insufficient = True

        try:
            page.wait_for_timeout(1000)
        except Exception as exc:
            if _is_playwright_target_closed_error(exc):
                return False
            raise


def _visible_login_requires_cookie_change(canonical: str) -> bool:
    return canonical in {"xhs", "flowus"}


def _visible_login_requires_page_success(canonical: str) -> bool:
    return True


def _visible_page_sufficient_for_login(canonical: str, page) -> bool:
    if canonical == "xhs":
        return _xhs_visible_page_sufficient_for_login(page)
    if canonical == "flowus":
        return _flowus_visible_page_sufficient_for_login(page)
    if canonical == "reddit":
        return _reddit_visible_page_sufficient_for_login(page)
    return _generic_visible_page_sufficient_for_login(page)


def _xhs_visible_page_sufficient_for_login(page) -> bool:
    state = _safe_page_evaluate(page, _XHS_VISIBLE_LOGIN_STATE_JS)
    if not isinstance(state, dict):
        return False
    return bool(state.get("ok"))


def _flowus_visible_page_sufficient_for_login(page) -> bool:
    state = _safe_page_evaluate(page, _FLOWUS_VISIBLE_LOGIN_STATE_JS)
    if not isinstance(state, dict):
        return False
    return bool(state.get("ok"))


def _reddit_visible_page_sufficient_for_login(page) -> bool:
    state = _safe_page_evaluate(page, _REDDIT_VISIBLE_LOGIN_STATE_JS)
    if not isinstance(state, dict):
        return False
    return bool(state.get("ok"))


def _generic_visible_page_sufficient_for_login(page) -> bool:
    state = _safe_page_evaluate(page, _GENERIC_VISIBLE_LOGIN_STATE_JS)
    if not isinstance(state, dict):
        return False
    return bool(state.get("ok"))


def _safe_page_evaluate(page, script: str):
    try:
        return page.evaluate(script)
    except Exception as exc:
        if _is_playwright_target_closed_error(exc):
            return None
        logger.debug(f"Visible login page readiness probe failed: {exc}")
        return None


def _visible_cookie_signature(context, canonical: str) -> set[tuple[str, str, str]]:
    return _cookie_signature(_visible_platform_cookies(context, canonical))


def _visible_platform_cookies(context, canonical: str) -> list:
    try:
        cookies = context.cookies()
    except Exception as exc:
        if _is_playwright_target_closed_error(exc):
            return []
        raise
    cookie_domains = _CDP_COOKIE_DOMAINS.get(canonical)
    if not cookie_domains:
        return cookies
    return _filter_cdp_platform_cookies(cookies, cookie_domains)


def _visible_cookies_sufficient_for_login(canonical: str, cookies: list) -> bool:
    return _cookies_sufficient_for_login(canonical, cookies)


def _cookies_sufficient_for_login(canonical: str, cookies: list) -> bool:
    if not cookies:
        return False
    if canonical == "wechat":
        return wechat_cookie_names_sufficient(_cookie_names(cookies))
    names = _cookie_names(cookies)
    required_all = _required_all_cookie_names(canonical)
    if required_all:
        return required_all.issubset(names)
    required_any = _required_any_cookie_names(canonical)
    if required_any:
        return bool(names.intersection(required_any))
    return True


def _required_all_cookie_names(canonical: str) -> set[str]:
    return {
        "twitter": {"auth_token", "ct0"},
        "xhs": {"web_session"},
        "flowus": {"next_auth", "next_auth.sig"},
    }.get(canonical, set())


def _required_any_cookie_names(canonical: str) -> set[str]:
    return {
        "feishu": {"session", "session_list", "passport_app_access_token"},
        "kdocs": {"wps_sid", "wps_sid_c"},
        "zhihu": {"z_c0"},
        "linuxdo": {"_t"},
        "idcflare": {"_t"},
        "zsxq": {"zsxq_access_token"},
        "reddit": {"reddit_session"},
    }.get(canonical, set())


_XHS_VISIBLE_LOGIN_STATE_JS = r"""
async () => {
    const text = document.body?.innerText || "";
    const hasLoginText = /扫码登录|验证码登录|手机号登录|登录后推荐|登录小红书|请输入手机号|请先登录/.test(text);
    const hasLoginInput = !!document.querySelector(
        'input[placeholder*="手机号"],input[placeholder*="验证码"],input[placeholder*="密码"]'
    );

    const app = document.querySelector("#app")?.__vue_app__;
    const pinia = app?.config?.globalProperties?.$pinia;
    const userStore = pinia?._s?.get?.("user");
    const userState = userStore?.$state || userStore || {};
    const candidates = [
        userState.userInfo,
        userState.user_info,
        userState.selfInfo,
        userState.profile,
        userStore?.userInfo,
        userStore?.user_info,
        userStore?.selfInfo,
        userStore?.profile,
    ].filter(Boolean);
    const hasCurrentUser = candidates.some((item) => {
        const user = item || {};
        return Boolean(user.user_id || user.userId || user.id || user.nickname || user.nickName);
    });

    let apiOk = false;
    try {
        const response = await fetch("https://edith.xiaohongshu.com/api/sns/web/v1/user/selfinfo", {
            credentials: "include",
            headers: { accept: "application/json" },
        });
        const data = await response.json().catch(() => null);
        const payload = data?.data || {};
        apiOk = Boolean(
            response.ok && (
                payload.user_id ||
                payload.userId ||
                payload.id ||
                payload.nickname ||
                payload?.user_info?.user_id ||
                payload?.userInfo?.userId
            )
        );
    } catch (_) {}

    return {
        ok: Boolean((apiOk || hasCurrentUser) && !hasLoginText && !hasLoginInput),
        apiOk,
        hasCurrentUser,
        hasLoginDialog: Boolean(hasLoginText || hasLoginInput),
        url: location.href,
    };
}
"""


_FLOWUS_VISIBLE_LOGIN_STATE_JS = r"""
async () => {
    const text = document.body?.innerText || "";
    const path = location.pathname.toLowerCase();
    const isLoginRoute = /\/login|\/signup|\/register/.test(path);
    const hasLoginText = /欢迎来到\s*FlowUs|请输入手机号|请输入手机|请输入邮箱|请输入密码|请输入验证码|使用验证码登录|注册账号/.test(text);
    const hasLoginInput = !!document.querySelector(
        'input[placeholder*="手机号"],input[placeholder*="手机"],input[placeholder*="邮箱"],input[placeholder*="密码"],input[placeholder*="验证码"]'
    );

    let apiOk = false;
    for (const endpoint of ["/api/users/me", "/api/user/current", "/api/auth/currentUser"]) {
        try {
            const response = await fetch(endpoint, {
                credentials: "include",
                headers: { accept: "application/json" },
            });
            const data = await response.json().catch(() => null);
            const payload = data?.data || data?.user || data || {};
            if (
                response.ok &&
                (payload.uuid || payload.id || payload.userId || payload.email || payload.phone || payload.nickname)
            ) {
                apiOk = true;
                break;
            }
        } catch (_) {}
    }

    return {
        ok: Boolean(apiOk || (!isLoginRoute && !hasLoginText && !hasLoginInput)),
        apiOk,
        isLoginRoute,
        hasLoginForm: Boolean(hasLoginText || hasLoginInput),
        url: location.href,
    };
}
"""


_REDDIT_VISIBLE_LOGIN_STATE_JS = r"""
() => {
    const text = document.body?.innerText || "";
    const url = location.href;
    const blocked = /blocked by network security|whoa there|network policy|you've been blocked/i.test(text);
    return {
        ok: !blocked,
        blocked,
        url,
    };
}
"""


_GENERIC_VISIBLE_LOGIN_STATE_JS = r"""
() => {
    const text = document.body?.innerText || "";
    const url = location.href;
    const path = location.pathname.toLowerCase();
    const loginRoute = /\/login|\/signin|\/sign-in|\/auth|\/passport|\/account/i.test(path);
    const hasPasswordInput = !!document.querySelector('input[type="password"]');
    const hasLoginInput = Boolean(
        hasPasswordInput ||
        document.querySelector(
            'input[type="tel"],input[type="email"],input[autocomplete*="one-time-code"],input[placeholder*="验证码"],input[placeholder*="密码"],input[placeholder*="手机"],input[placeholder*="邮箱"]'
        )
    );
    const hasLoginText = /扫码|二维码|验证码|请输入.*(密码|手机|手机号|邮箱)|sign in|log in|login|password/i.test(text);
    const hasBlockedText = /blocked by network security|network policy|you've been blocked|captcha|人机验证|安全验证/i.test(text);
    const hasLoginForm = Boolean(hasLoginInput && (loginRoute || hasLoginText));
    return {
        ok: Boolean(!hasBlockedText && !hasLoginForm),
        url,
        loginRoute,
        hasLoginForm,
        hasLoginInput,
        hasLoginText,
        hasBlockedText,
    };
}
"""


def _safe_page_is_closed(page) -> bool:
    try:
        return bool(page.is_closed())
    except Exception as exc:
        if _is_playwright_target_closed_error(exc):
            return True
        raise


def _is_playwright_target_closed_error(exc: Exception) -> bool:
    return (
        exc.__class__.__name__ == "TargetClosedError"
        or "Target page, context or browser has been closed" in str(exc)
    )


def _visible_login_profile_parent(session_path: Path) -> Path:
    base = os.getenv("FEEDGRAB_LOGIN_PROFILE_DIR", "").strip()
    if base:
        return Path(base).expanduser()
    return session_path.parent / ".browser-profiles"


def _managed_cdp_profile_dir(session_path: Path, canonical: str) -> Path:
    base = os.getenv("FEEDGRAB_CDP_PROFILE_DIR", "").strip()
    profile_key = _login_profile_key(session_path, canonical)
    if base:
        return Path(base).expanduser() / f"{profile_key}-cdp"
    return session_path.parent / ".browser-profiles" / f"{profile_key}-cdp"


def _login_profile_key(session_path: Path, canonical: str) -> str:
    return session_path.stem or canonical


def _start_managed_chrome_cdp(canonical: str, session_path: Path) -> bool:
    from feedgrab.config import chrome_cdp_port

    port = chrome_cdp_port()
    if _cdp_browser_endpoint(port):
        return True

    chrome_path = _find_chrome_executable()
    if not chrome_path:
        print("未找到 Chrome/Edge 可执行文件。请通过 CHROME_PATH 指定浏览器路径。")
        return False

    profile_dir = _managed_cdp_profile_dir(session_path, canonical)
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]

    print(f"正在启动普通 Chrome CDP：{chrome_path}")
    print(f"   CDP 端口：{port}")
    print(f"   登录浏览器资料目录：{profile_dir}")

    popen_kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    try:
        subprocess.Popen(args, **popen_kwargs)
    except Exception as exc:
        logger.debug(f"Managed Chrome CDP startup failed: {exc}")
        print(f"Chrome CDP 启动失败：{exc}")
        return False

    for _attempt in range(30):
        time.sleep(0.5)
        if _cdp_browser_endpoint(port):
            return True

    print(f"Chrome 已启动，但 CDP 端口 {port} 未在预期时间内响应。")
    return False


def _find_chrome_executable() -> str | None:
    candidates = [
        os.getenv("CHROME_PATH"),
        _join_env_path("PROGRAMFILES", "Google", "Chrome", "Application", "chrome.exe"),
        _join_env_path("PROGRAMFILES(X86)", "Google", "Chrome", "Application", "chrome.exe"),
        _join_env_path("LOCALAPPDATA", "Google", "Chrome", "Application", "chrome.exe"),
        _join_env_path("PROGRAMFILES", "Microsoft", "Edge", "Application", "msedge.exe"),
        _join_env_path("PROGRAMFILES(X86)", "Microsoft", "Edge", "Application", "msedge.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("msedge"),
        shutil.which("msedge.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _join_env_path(name: str, *parts: str) -> str | None:
    base = os.getenv(name)
    if not base:
        return None
    return str(Path(base).joinpath(*parts))


def _visible_login_args() -> list[str]:
    return [
        "--no-first-run",
        "--no-default-browser-check",
    ]


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
    "reddit": [".reddit.com", "reddit.com"],
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
    "reddit": ["https://www.reddit.com", "https://old.reddit.com", "https://reddit.com"],
}


def wechat_cookie_names_sufficient(cookie_names: set[str]) -> bool:
    """Return True when cookie names look like a usable WeChat/MP session."""
    names = {str(name or "").strip().lower() for name in cookie_names if str(name or "").strip()}

    mp_backend_names = {"slave_sid", "slave_user", "data_bizuin", "bizuin"}
    if len(names.intersection(mp_backend_names)) >= 2:
        return True

    reader_session_names = {"key", "pass_ticket", "appmsg_token"}
    return "wxuin" in names and bool(names.intersection(reader_session_names))


def _cdp_cookies_sufficient_for_login(canonical: str, cookies: list) -> bool:
    return _cookies_sufficient_for_login(canonical, cookies)


def _cookie_names(cookies: list) -> set[str]:
    return {
        str(cookie.get("name", "")).strip().lower()
        for cookie in cookies
        if (
            isinstance(cookie, dict)
            and str(cookie.get("name", "")).strip()
            and str(cookie.get("value", "")).strip()
        )
    }


def _print_insufficient_cdp_cookies(canonical: str, cookie_count: int) -> None:
    if canonical == "wechat":
        print(
            f"检测到 {cookie_count} 个 QQ 域 Cookie，但缺少微信公众号后台关键 Cookie，"
            "未保存登录态。请在登录页完成扫码登录后再等待保存。"
        )


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
    endpoint = _cdp_playwright_endpoint(port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(endpoint)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            warmup_url = _login_warmup_url(canonical)
            page = _prepare_cdp_login_page(context, login_url, warmup_url=warmup_url)
            page.wait_for_timeout(2000)
            initial_signature = _cookie_signature(
                _filter_cdp_platform_cookies(context.cookies(), cookie_domains)
            )
            print(f"🌐 正在通过 Chrome CDP 打开 {canonical} 登录页：{login_url}")
            print("   请在 Chrome 窗口完成登录，feedgrab 会自动检测并保存登录态。\n")

            start = time.time()
            warned_insufficient = False
            while time.time() - start < timeout:
                platform_cookies = _filter_cdp_platform_cookies(context.cookies(), cookie_domains)
                current_signature = _cookie_signature(platform_cookies)
                login_cookie_changed = current_signature != initial_signature
                login_page_ready = _cdp_page_sufficient_for_login(canonical, page)
                if platform_cookies and (login_cookie_changed or login_page_ready):
                    if _cdp_cookies_sufficient_for_login(canonical, platform_cookies):
                        _write_cdp_storage_state(session_path, platform_cookies, source="interactive CDP")
                        with contextlib.suppress(Exception):
                            page.close()
                        browser.close()
                        return True
                    if login_cookie_changed and not warned_insufficient:
                        _print_insufficient_cdp_cookies(canonical, len(platform_cookies))
                        warned_insufficient = True
                if page.is_closed():
                    if (
                        platform_cookies
                        and (login_cookie_changed or login_page_ready)
                        and _cdp_cookies_sufficient_for_login(canonical, platform_cookies)
                    ):
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


def _prepare_cdp_login_page(context, login_url: str, warmup_url: str | None = None):
    return _prepare_login_page(context, login_url, warmup_url=warmup_url)


def _cdp_page_sufficient_for_login(canonical: str, page) -> bool:
    if canonical != "wechat":
        return False
    return _wechat_mp_backend_url_has_token(_safe_page_url(page))


def _wechat_mp_backend_url_has_token(url: str) -> bool:
    text = str(url or "").strip()
    lowered = text.lower()
    if "mp.weixin.qq.com" not in lowered or "token=" not in lowered:
        return False
    try:
        from urllib.parse import parse_qs, urlparse

        values = parse_qs(urlparse(text).query).get("token") or []
        return any(str(value).strip() for value in values)
    except Exception:
        return True


def _prepare_login_page(context, login_url: str, warmup_url: str | None = None):
    page = _select_login_page(context)
    with contextlib.suppress(Exception):
        page.bring_to_front()
    if warmup_url:
        page.goto(warmup_url, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            page.wait_for_timeout(1500)
    page.goto(login_url, wait_until="domcontentloaded")
    _close_extra_blank_pages(context, page)
    return page


def _login_warmup_url(canonical: str) -> str | None:
    if canonical == "reddit":
        return "https://www.reddit.com/"
    return None


def _select_cdp_login_page(context):
    return _select_login_page(context)


def _select_login_page(context):
    for page in list(getattr(context, "pages", []) or []):
        if _page_is_closed(page):
            continue
        if _is_blank_page_url(_safe_page_url(page)):
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
    _close_extra_blank_pages(context, keep_page)


def _close_extra_blank_pages(context, keep_page) -> None:
    for page in list(getattr(context, "pages", []) or []):
        if page is keep_page or _page_is_closed(page):
            continue
        if _is_blank_page_url(_safe_page_url(page)):
            with contextlib.suppress(Exception):
                page.close()


def _is_blank_cdp_page_url(url: str) -> bool:
    return _is_blank_page_url(url)


def _is_blank_page_url(url: str) -> bool:
    text = str(url or "").strip().lower()
    return not text or text == "about:blank" or text.startswith("chrome://newtab") or text.startswith("chrome://new-tab-page")


def _cdp_playwright_endpoint(port: int) -> str:
    return _cdp_browser_endpoint(port) or f"ws://127.0.0.1:{port}/devtools/browser"


def _cdp_browser_endpoint(port: int) -> str | None:
    import urllib.request

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version", method="GET")
        with urllib.request.urlopen(req, timeout=1) as resp:
            payload = json.loads(resp.read())
    except Exception:
        return None
    endpoint = payload.get("webSocketDebuggerUrl") if isinstance(payload, dict) else None
    return endpoint if isinstance(endpoint, str) and endpoint.strip() else None


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

    endpoint = _cdp_playwright_endpoint(port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(endpoint)
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

    if not _cdp_cookies_sufficient_for_login(canonical, platform_cookies):
        _print_insufficient_cdp_cookies(canonical, len(platform_cookies))
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

    if not _cdp_cookies_sufficient_for_login(canonical, pw_cookies):
        _print_insufficient_cdp_cookies(canonical, len(pw_cookies))
        return False

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
