# -*- coding: utf-8 -*-
"""
Twitter/X Cookie management — multi-source authentication for GraphQL API.

Cookie sources (priority order):
    1. Environment variables: X_AUTH_TOKEN + X_CT0
    2. Cookie file: sessions/x.json
    3. Playwright session: sessions/twitter.json
    4. Chrome CDP: auto-extract from running Chrome (requires --remote-debugging-port)

Multi-account rotation:
    Place additional cookie files in sessions/:
      x.json, x_2.json, x_3.json, ...
      OR twitter.json, twitter_2.json, twitter_3.json, ...
    When 429 rate limit is hit, call mark_cookie_rate_limited()
    to rotate to the next available account.

Required cookies for GraphQL API:
    - auth_token: session authentication token
    - ct0: CSRF protection token
"""

import json
import os
import shutil
import time
from pathlib import Path
from loguru import logger

from feedgrab.config import get_cookie_dir, get_session_dir, get_user_agent

# Legacy paths (for backward compatibility migration)
_LEGACY_COOKIE_DIRS = [
    Path.cwd() / ".feedgrab" / "cookies",       # project-local .feedgrab/cookies/
    Path.home() / ".feedgrab" / "cookies",       # user-home ~/.feedgrab/cookies/
]
_LEGACY_SESSION_DIRS = [
    Path.cwd() / ".feedgrab" / "sessions",       # project-local .feedgrab/sessions/
    Path.home() / ".feedgrab" / "sessions",       # user-home ~/.feedgrab/sessions/
]

REQUIRED_COOKIES = ("auth_token", "ct0")

# Public bearer token used by X's web client (not a secret).
BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

DEFAULT_USER_AGENT = get_user_agent()

# ---------------------------------------------------------------------------
# Multi-account cookie rotation state
# ---------------------------------------------------------------------------

# Rate limit tracking: {auth_token_prefix: expiry_timestamp}
_rate_limited_accounts: dict[str, float] = {}

# Cooldown period: 15 minutes (Twitter rate limit window)
RATE_LIMIT_COOLDOWN = 15 * 60

# Currently active account key (for logging)
_current_account_key: str = ""


def load_twitter_cookies() -> dict:
    """
    Load Twitter cookies from multiple sources with priority fallback.
    Supports multi-account rotation: skips 429-rate-limited accounts.

    Returns:
        dict with 'auth_token' and 'ct0' keys, or empty dict if none found.
    """
    global _current_account_key

    # Collect all available cookie sets
    all_cookie_sets = _load_all_cookie_sets()

    if not all_cookie_sets:
        logger.warning("No valid Twitter cookies found from any source")
        return {}

    # Clean expired rate limits
    now = time.time()
    expired = [k for k, v in _rate_limited_accounts.items() if now >= v]
    for k in expired:
        del _rate_limited_accounts[k]
        logger.info(f"[CookieRotation] 账号 {k} 限流已过期，重新可用")

    # Find first non-rate-limited cookie set
    for source_label, cookies in all_cookie_sets:
        account_key = cookies.get("auth_token", "")[:8]
        if account_key in _rate_limited_accounts:
            remaining = int(_rate_limited_accounts[account_key] - now)
            logger.debug(
                f"[CookieRotation] 跳过 {source_label} ({account_key}...) "
                f"限流中，剩余 {remaining}s"
            )
            continue

        _current_account_key = account_key
        if len(all_cookie_sets) > 1:
            logger.info(
                f"Twitter cookies loaded from {source_label} "
                f"(auth_token={account_key}...) "
                f"[{_count_available(len(all_cookie_sets))}/{len(all_cookie_sets)} 可用]"
            )
        else:
            logger.info(
                f"Twitter cookies loaded from {source_label} "
                f"(auth_token={account_key}...)"
            )
        return cookies

    # All accounts are rate limited — use the one expiring soonest
    soonest_key = min(_rate_limited_accounts, key=_rate_limited_accounts.get)
    for source_label, cookies in all_cookie_sets:
        if cookies.get("auth_token", "")[:8] == soonest_key:
            remaining = int(_rate_limited_accounts[soonest_key] - now)
            logger.warning(
                f"[CookieRotation] 所有账号均限流中，使用即将解封的 "
                f"{source_label} ({soonest_key}...) 剩余 {remaining}s"
            )
            _current_account_key = soonest_key
            return cookies

    return all_cookie_sets[0][1]


def load_all_twitter_cookie_sets() -> list[tuple[str, dict]]:
    """Load every usable Twitter cookie set in priority order."""
    return _load_all_cookie_sets()


# Sources that identify the account the user themself logged in as. Numbered
# files (x_2.json, twitter_2.json, ...) are rotation spares by convention.
_PRIMARY_SOURCE_LABELS = (
    "env",
    "playwright(twitter.json)",
    "cookie_file(x.json)",
    "chrome_cdp",
)


def is_primary_cookie_label(label: str) -> bool:
    """Whether a cookie source label denotes the user's own (primary) account."""
    return label in _PRIMARY_SOURCE_LABELS


def load_primary_twitter_cookies() -> dict:
    """Load ONLY the primary account cookies — never fall back to spares.

    Account-private data (bookmarks above all) cannot be read by another
    account: rotating to x_2.json only ever yields that account's own data or
    a flat permission error. Callers handling private data must use this
    instead of :func:`load_twitter_cookies`.

    Returns:
        The primary account's cookies, or {} when no primary login exists.
    """
    for label, cookies in _load_all_cookie_sets():
        if is_primary_cookie_label(label):
            activate_twitter_cookie(cookies)
            logger.info(
                f"Twitter 主账号 cookie: {label} "
                f"(auth_token={cookies.get('auth_token', '')[:8]}...)"
            )
            return cookies

    logger.error(
        ">>> 未找到主账号 Twitter 登录态 <<< "
        "已跳过备用号（x_2.json 等）——账号私有数据换号读不到。"
        "请运行: feedgrab login twitter"
    )
    return {}


def activate_twitter_cookie(cookies: dict) -> None:
    """Record the cookie set currently being used for logging/rotation."""
    global _current_account_key
    _current_account_key = (cookies or {}).get("auth_token", "")[:8]


def is_twitter_cookie_rate_limited(cookies: dict) -> bool:
    """Return whether a cookie set is still in the local rate-limit cooldown."""
    account_key = (cookies or {}).get("auth_token", "")[:8]
    if not account_key:
        return False
    expiry = _rate_limited_accounts.get(account_key, 0)
    return time.time() < expiry


def _count_available(total: int) -> int:
    """Count non-rate-limited accounts."""
    now = time.time()
    limited = sum(1 for k, v in _rate_limited_accounts.items() if now < v)
    return total - limited


def _load_all_cookie_sets() -> list[tuple[str, dict]]:
    """Load all available cookie sets from all sources.

    Returns:
        List of (source_label, cookies_dict) tuples.
    """
    results = []

    # Source 1: Environment variables (highest priority)
    cookies = _load_from_env()
    if has_required_cookies(cookies):
        results.append(("env", cookies))

    # Source 2: Playwright sessions — primary account first (twitter.json, twitter_2.json, ...)
    for label, cookies in _load_all_playwright_sessions():
        results.append((label, cookies))

    # Source 3: Extra cookie files (x.json, x_2.json, x_3.json, ...)
    for label, cookies in _load_all_cookie_files():
        results.append((label, cookies))

    # Source 4: Chrome CDP (fallback, single source)
    if not results:
        cookies = _load_from_chrome_cdp()
        if has_required_cookies(cookies):
            results.append(("chrome_cdp", cookies))

    # Deduplicate by auth_token
    seen_tokens = set()
    unique = []
    for label, cookies in results:
        token = cookies.get("auth_token", "")
        if token not in seen_tokens:
            seen_tokens.add(token)
            unique.append((label, cookies))

    return unique


def mark_cookie_rate_limited(cookies: dict = None):
    """Mark current cookie account as rate-limited (429).

    Called by GraphQL layer when 429 is received. The account will be
    skipped for RATE_LIMIT_COOLDOWN seconds (default 15 min).
    Next call to load_twitter_cookies() will return a different account.
    """
    if cookies:
        account_key = cookies.get("auth_token", "")[:8]
    else:
        account_key = _current_account_key

    if not account_key:
        return

    expiry = time.time() + RATE_LIMIT_COOLDOWN
    _rate_limited_accounts[account_key] = expiry
    logger.warning(
        f"[CookieRotation] 账号 {account_key}... 被标记限流，"
        f"{RATE_LIMIT_COOLDOWN // 60} 分钟后自动恢复"
    )


def count_total_accounts() -> int:
    """返回当前可用的 Twitter 账号总数（包括限流中的）。"""
    return len(_load_all_cookie_sets())


def count_available_accounts() -> int:
    """返回当前未被限流的 Twitter 账号数。"""
    now = time.time()
    total = count_total_accounts()
    limited = sum(1 for _, expiry in _rate_limited_accounts.items() if now < expiry)
    return max(0, total - limited)


def earliest_rate_limit_recovery_seconds() -> int:
    """返回最早一个限流账号距离解封的秒数（无限流则返回 0）。"""
    if not _rate_limited_accounts:
        return 0
    now = time.time()
    soonest = min(_rate_limited_accounts.values())
    return max(0, int(soonest - now))


def cookie_rate_limit_remaining(cookies: dict) -> int:
    """Seconds until this specific account leaves the rate-limit table (0 = free)."""
    account_key = (cookies or {}).get("auth_token", "")[:8]
    if not account_key:
        return 0
    expiry = _rate_limited_accounts.get(account_key)
    if not expiry:
        return 0
    return max(0, int(expiry - time.time()))


def _fetch_with_primary_cookie(fetcher_callable, *args, label: str = "GraphQL", **kwargs):
    """Single-shot fetch pinned to the primary account (no rotation).

    Returns:
        (response, cookies) — same contract as :func:`fetch_with_cookie_rotation`.
    """
    cookies = load_primary_twitter_cookies()
    if not cookies:
        logger.error(
            f"[{label}] >>> 无可用主账号 Cookie <<< "
            f"该场景为账号私有数据，禁止降级到备用号（x_2.json 等）"
        )
        return None, {}

    account_key = cookies.get("auth_token", "")[:8]

    # The rotation path skips limited accounts via load_twitter_cookies(); with
    # a single pinned account there is nothing to skip, so check explicitly.
    # Firing a request we know will 429 would otherwise come back as "no
    # response" and get misreported as an expired login below.
    cooldown = cookie_rate_limit_remaining(cookies)
    if cooldown:
        logger.error(
            f"[{label}] >>> 主账号 {account_key}... 正在限流冷却 <<< "
            f"约 {cooldown}s 后恢复（{cooldown // 60} 分钟）— "
            f"账号私有数据无法用备用号代抓，本次终止"
        )
        return None, cookies

    try:
        response = fetcher_callable(*args, cookies=cookies, **kwargs)
    except Exception as exc:
        logger.error(f"[{label}] 主账号 {account_key}... 调用异常: {exc}")
        return None, cookies

    if not response:
        # The GraphQL layer calls mark_cookie_rate_limited() on a 429, so if the
        # account landed in the table during this very call the cause is a rate
        # limit, not a dead session. Reporting both as "check your login" sent
        # users to re-run `feedgrab login twitter` for a 15-minute cooldown.
        cooldown = cookie_rate_limit_remaining(cookies)
        if cooldown:
            logger.error(
                f"[{label}] >>> 主账号 {account_key}... 本次请求触发限流 <<< "
                f"约 {cooldown}s 后恢复（{cooldown // 60} 分钟）— "
                f"登录态本身没问题，等冷却结束重跑即可"
            )
        else:
            logger.error(
                f"[{label}] >>> 主账号 {account_key}... 请求无响应 <<< "
                f"账号私有数据不轮换备用号，请检查该账号登录态是否过期"
            )
    return response, cookies


def fetch_with_cookie_rotation(
    fetcher_callable,
    *args,
    label: str = "GraphQL",
    network_retry_delay: float = 5.0,
    primary_only: bool = False,
    **kwargs,
):
    """跨 cookie 账号的统一重试 helper。

    每次失败时重新调用 ``load_twitter_cookies()`` 切换到下一个可用账号，
    直到拿到非空响应或所有账号都被限流为止。``cookies`` 不需要由调用方提供，
    helper 内部统一加载和轮换。

    Args:
        fetcher_callable: 接收 ``*args``（含 ``cookies`` 占位位置）+ ``**kwargs`` 的 GraphQL 调用函数。
            **注意：调用方必须按 fetcher 签名传位置参数和关键字参数，
            ``cookies`` 通过 kwargs 注入（fetcher 签名通常为 ``(id, cookies, cursor=...)``）。**
        label: 日志前缀（如 ``UserTweets``/``Bookmarks``）。
        network_retry_delay: 切换账号后等待的秒数。
        primary_only: 只用主账号，禁止轮换到备用号。用于书签这类**账号私有数据**
            —— 换号读到的是别人的数据或直接权限错误，轮换在语义上无意义。

    Returns:
        (response, cookies)
            - response 为 None 表示所有账号都尝试过且失败
            - cookies 为最后一次使用的账号 cookie dict（即便失败也返回，用于日志/上下文）
    """
    if primary_only:
        return _fetch_with_primary_cookie(
            fetcher_callable, *args, label=label, **kwargs
        )

    total = count_total_accounts()
    if total == 0:
        logger.error(f"[{label}] 未找到任何 Twitter 账号 Cookie")
        return None, {}

    last_cookies: dict = {}
    last_account_key = ""

    for attempt in range(1, total + 1):
        cookies = load_twitter_cookies()
        if not cookies:
            # All accounts limited — load_twitter_cookies() picks soonest-to-recover
            wait_sec = earliest_rate_limit_recovery_seconds()
            logger.error(
                f"[{label}] >>> 所有 {total} 个 Twitter 账号均已被限流 <<< "
                f"最早 {wait_sec}s 后自动恢复（约 {wait_sec // 60} 分钟）— 终止本次分页"
            )
            return None, last_cookies

        last_cookies = cookies
        account_key = cookies.get("auth_token", "")[:8]

        if attempt > 1:
            time.sleep(network_retry_delay)
            available = count_available_accounts()
            logger.warning(
                f"[{label}] 切换账号重试 ({attempt}/{total}) — "
                f"新账号: {account_key}... 剩余可用 {available}/{total}"
            )

        # Guard: if the rotator returns the same account twice in a row
        # (single account scenario), avoid an infinite tight loop
        if attempt > 1 and account_key == last_account_key:
            logger.error(
                f"[{label}] >>> 只有 1 个可用账号且仍失败 <<< "
                f"账号 {account_key}... 已尝试 {attempt} 次，终止"
            )
            return None, last_cookies

        last_account_key = account_key

        try:
            response = fetcher_callable(*args, cookies=cookies, **kwargs)
        except Exception as exc:
            logger.error(
                f"[{label}] 账号 {account_key}... 调用异常: {exc}（将尝试下一个账号）"
            )
            response = None

        if response:
            if attempt > 1:
                logger.info(
                    f"[{label}] 切换到账号 {account_key}... 第 {attempt} 次尝试成功"
                )
            return response, cookies

    # Should be unreachable: load_twitter_cookies() falls back to soonest-recovering
    # account once all are limited. This branch handles the edge case where every
    # attempt returned None without triggering the "all limited" early-exit.
    logger.error(
        f"[{label}] >>> 全部 {total} 个账号轮询完毕仍无响应 <<< 终止本次分页"
    )
    return None, last_cookies


def has_required_cookies(cookies: dict) -> bool:
    """Check if cookies contain both auth_token and ct0 with valid values."""
    for k in REQUIRED_COOKIES:
        val = cookies.get(k, "")
        if not val:
            return False
        # Filter out template placeholders (real tokens are 20+ hex chars)
        if len(val) < 20:
            return False
    return True


def save_twitter_cookies(cookies: dict) -> None:
    """Save cookies to cookie directory with restrictive permissions."""
    cookie_dir = get_cookie_dir()
    cookie_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = cookie_dir / "x.json"

    with open(cookie_path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)

    try:
        os.chmod(str(cookie_path), 0o600)
    except OSError:
        pass  # Windows doesn't support Unix permissions

    logger.info(f"Cookies saved to {cookie_path}")


def build_graphql_headers(cookies: dict, user_agent: str = None) -> dict:
    """
    Build HTTP headers for X GraphQL API requests.

    Args:
        cookies: dict with 'auth_token' and 'ct0'.
        user_agent: optional custom User-Agent string.

    Returns:
        dict of HTTP headers ready for requests.get/post.
    """
    ua = user_agent or DEFAULT_USER_AGENT
    return {
        "authorization": f"Bearer {BEARER_TOKEN}",
        "x-csrf-token": cookies.get("ct0", ""),
        "cookie": f"auth_token={cookies.get('auth_token', '')}; ct0={cookies.get('ct0', '')}",
        "content-type": "application/json",
        "user-agent": ua,
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en",
    }


# ---------------------------------------------------------------------------
# Private: cookie source loaders
# ---------------------------------------------------------------------------

def _load_from_env() -> dict:
    """Source 1: Load cookies from environment variables."""
    return {
        "auth_token": os.getenv("X_AUTH_TOKEN", ""),
        "ct0": os.getenv("X_CT0", ""),
    }


def _load_from_cookie_file() -> dict:
    """Source 2: Load cookies from primary cookie file (sessions/x.json)."""
    results = _load_all_cookie_files()
    if results:
        return results[0][1]
    return {}


def _load_all_cookie_files() -> list[tuple[str, dict]]:
    """Load cookies from all cookie files: x.json, x_2.json, x_3.json, ..."""
    results = []
    cookie_dir = get_cookie_dir()

    # Primary file
    cookie_path = cookie_dir / "x.json"
    if not cookie_path.exists():
        # Backward compat: search legacy cookie dirs and migrate
        for legacy_dir in _LEGACY_COOKIE_DIRS:
            for name in ("x.json", "twitter.json"):
                legacy_path = legacy_dir / name
                if legacy_path.exists():
                    cookie_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(legacy_path), str(cookie_path))
                    logger.info(f"Migrated cookie: {legacy_path} -> {cookie_path}")
                    break
            else:
                continue
            break

    # Load x.json and x_{N}.json files
    if cookie_dir.exists():
        cookie_files = sorted(cookie_dir.glob("x*.json"))
        for cf in cookie_files:
            # Match x.json, x_2.json, x_3.json but not xhs.json etc
            name = cf.stem
            if name == "x" or (name.startswith("x_") and name[2:].isdigit()):
                cookies = _read_cookie_json(cf)
                if has_required_cookies(cookies):
                    results.append((f"cookie_file({cf.name})", cookies))

    return results


def _read_cookie_json(path: Path) -> dict:
    """Read a cookie JSON file and return {auth_token, ct0}."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "auth_token": data.get("auth_token", ""),
            "ct0": data.get("ct0", ""),
        }
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to read cookie file {path}: {e}")
        return {}


def _load_from_playwright_session() -> dict:
    """Source 3: Load from primary Playwright session."""
    results = _load_all_playwright_sessions()
    if results:
        return results[0][1]
    return {}


def _load_all_playwright_sessions() -> list[tuple[str, dict]]:
    """Load cookies from all Playwright sessions: twitter.json, twitter_2.json, ..."""
    results = []
    session_dir = get_session_dir()

    # Primary file migration. A guest-mode shell (file present but carrying no
    # auth_token/ct0) must NOT shadow a valid legacy session — that silently
    # dropped the primary account and rotated to spares instead.
    session_path = session_dir / "twitter.json"
    existing = _read_playwright_session(session_path) if session_path.exists() else {}
    if not has_required_cookies(existing):
        for legacy_dir in _LEGACY_SESSION_DIRS:
            legacy_path = legacy_dir / "twitter.json"
            if not legacy_path.exists():
                continue
            if not has_required_cookies(_read_playwright_session(legacy_path)):
                continue
            session_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(legacy_path), str(session_path))
            logger.info(f"Migrated session: {legacy_path} -> {session_path}")
            break

    # Load twitter.json and twitter_{N}.json files
    if session_dir.exists():
        session_files = sorted(session_dir.glob("twitter*.json"))
        for sf in session_files:
            name = sf.stem
            if name == "twitter" or (name.startswith("twitter_") and name[8:].isdigit()):
                cookies = _read_playwright_session(sf)
                if has_required_cookies(cookies):
                    results.append((f"playwright({sf.name})", cookies))
                else:
                    # Name the failure instead of silently rotating past it.
                    missing = [c for c in REQUIRED_COOKIES if not cookies.get(c)]
                    logger.warning(
                        f"跳过 session {sf.name}：缺少 {'/'.join(missing) or '有效'} "
                        f"cookie（游客态空壳，非登录态）"
                        f"—— 如需该账号请重新运行: feedgrab login twitter"
                    )

    return results


def _read_playwright_session(path: Path) -> dict:
    """Extract auth_token and ct0 from a Playwright storage_state JSON."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)

        cookie_map = {}
        for cookie in state.get("cookies", []):
            if cookie.get("name") in REQUIRED_COOKIES:
                domain = cookie.get("domain", "")
                if "x.com" in domain or "twitter.com" in domain:
                    cookie_map[cookie["name"]] = cookie["value"]

        return cookie_map
    except (json.JSONDecodeError, IOError, KeyError) as e:
        logger.warning(f"Failed to extract cookies from {path}: {e}")
        return {}


def _load_from_chrome_cdp() -> dict:
    """
    Source 4: Auto-extract cookies from a running Chrome instance via CDP.

    Requires Chrome launched with: --remote-debugging-port=9222
    Connects via WebSocket, calls Network.getCookies for .x.com domain.
    """
    import urllib.request

    cdp_port = os.getenv("CHROME_CDP_PORT", "9222")
    cdp_url = f"http://127.0.0.1:{cdp_port}"

    try:
        # Check if Chrome CDP is reachable
        req = urllib.request.Request(f"{cdp_url}/json/version", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            version_info = json.loads(resp.read())
            ws_url = version_info.get("webSocketDebuggerUrl")

        if not ws_url:
            return {}

        # Use HTTP endpoint to get cookies (simpler than WebSocket)
        # Send CDP command via /json/protocol isn't straightforward,
        # so we use the REST-like endpoint to list targets and get cookies
        req = urllib.request.Request(f"{cdp_url}/json/list", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            targets = json.loads(resp.read())

        # Find an x.com tab
        x_target = None
        for target in targets:
            if "x.com" in target.get("url", ""):
                x_target = target
                break

        if not x_target:
            return {}

        # Extract cookies via CDP WebSocket
        return _extract_cookies_via_websocket(ws_url)

    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return {}


def _extract_cookies_via_websocket(ws_url: str) -> dict:
    """Connect to Chrome DevTools WebSocket and extract X cookies."""
    try:
        import websocket  # optional dependency
    except ImportError:
        logger.debug("websocket-client not installed, skipping Chrome CDP cookie extraction")
        return {}

    try:
        ws = websocket.create_connection(ws_url, timeout=5)
        ws.send(json.dumps({
            "id": 1,
            "method": "Network.getCookies",
            "params": {"urls": ["https://x.com", "https://twitter.com"]},
        }))
        result = json.loads(ws.recv())
        ws.close()

        cookie_map = {}
        for cookie in result.get("result", {}).get("cookies", []):
            if cookie.get("name") in REQUIRED_COOKIES:
                cookie_map[cookie["name"]] = cookie["value"]

        return cookie_map
    except Exception as e:
        logger.debug(f"Chrome CDP WebSocket extraction failed: {e}")
        return {}
