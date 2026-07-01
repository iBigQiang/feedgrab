# -*- coding: utf-8 -*-
"""
Reddit fetcher.

Strategy (single post):
    Tier 0  GET https://old.reddit.com/<path>.json   — primary, with feedgrab UA
    Tier 1  CDP via running Chrome (REDDIT_CDP_ENABLED=true)
    Tier 2  Stealth Playwright + saved session
    Tier 3  Jina Reader markdown fallback (no comments)

Strategy (subreddit listing):
    GET https://old.reddit.com/r/<sub>/<sort>.json?limit=N
    Each child is rendered through the single-post tier chain so comments
    are included in each saved Markdown file.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

from loguru import logger

from feedgrab.config import (
    get_session_dir,
    reddit_cdp_enabled,
    reddit_max_comments,
    reddit_max_pages,
    reddit_morechildren_batch_size,
    reddit_morechildren_rounds,
    reddit_reply_mode,
    reddit_retry_attempts,
    reddit_user_agent,
    reddit_sub_delay,
)
from feedgrab.utils import http_client


_VALID_SORTS = {"hot", "new", "top", "best", "rising", "controversial"}
_VALID_SEARCH_SORTS = {"relevance", "hot", "top", "new", "comments"}
_VALID_SEARCH_TIME_RANGES = {"all", "year", "month", "week", "day", "hour"}
_SEARCH_SORTS_WITH_TIME = {"relevance", "top", "comments"}
_REDDIT_COOKIE_ORDER = (
    "reddit_session",
    "csrf_token",
    "token_v2",
    "session_tracker",
    "edgebucket",
    "loid",
    "over18",
    "redesign_optout",
)
_REDDIT_COOKIE_NAMES = set(_REDDIT_COOKIE_ORDER)
_REDDIT_HTTP_ONLY_COOKIES = {"reddit_session", "token_v2"}
_COOKIE_PAIR_RE = re.compile(r'"([^"\\]+)"\s*:\s*"((?:\\.|[^"\\])*)"')
_COOKIE_HEADER_PAIR_RE = re.compile(r"(^|[;\s])([A-Za-z0-9_.-]+)=([^;\r\n]+)")
_JINA_BLOCK_PATTERNS = (
    "target url returned error 403",
    "403: forbidden",
    "you've been blocked by network security",
    "blocked by network security",
    "request has been blocked due to a network policy",
)
_OPENCLI_EXTENSION_ID = "ildkmabpimmkaediidaifkhjpohdnifk"


def _reddit_session_path() -> Path:
    return get_session_dir() / "reddit.json"


def _ensure_reddit_storage_state(session_path: str | Path | None = None) -> Optional[str]:
    """Normalize supported Reddit session shapes into Playwright storage_state."""
    path = Path(session_path) if session_path is not None else _reddit_session_path()
    state = _load_reddit_storage_state(path)
    if not state:
        return None

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[Reddit] session 规范化写入失败: {path} ({exc})")
        return None

    return str(path)


def _load_reddit_storage_state(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[Reddit] session 读取失败: {path} ({exc})")
        return None

    payload: Any
    try:
        payload = json.loads(raw)
    except Exception:
        payload = _extract_cookie_pairs(raw)

    state = _normalize_reddit_storage_payload(payload)
    if state and state.get("cookies"):
        return state
    return None


def _extract_cookie_pairs(raw: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for key, value in _COOKIE_PAIR_RE.findall(raw):
        try:
            value = json.loads(f'"{value}"')
        except Exception:
            pass
        pairs[key] = str(value)

    for _prefix, key, value in _COOKIE_HEADER_PAIR_RE.findall(raw):
        pairs.setdefault(key, value.strip())

    return pairs


def _normalize_reddit_storage_payload(payload: Any) -> Optional[dict[str, Any]]:
    origins: list[Any] = []
    raw_cookies: list[Any]

    if isinstance(payload, dict) and isinstance(payload.get("cookies"), list):
        raw_cookies = list(payload.get("cookies") or [])
        if isinstance(payload.get("origins"), list):
            origins = list(payload.get("origins") or [])
    elif isinstance(payload, list):
        raw_cookies = list(payload)
    elif isinstance(payload, dict):
        raw_cookies = _flat_cookie_map_to_list(payload)
    else:
        return None

    cookies: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_cookie in raw_cookies:
        cookie = _normalize_reddit_cookie(raw_cookie)
        if not cookie:
            continue
        key = (cookie["name"], cookie["domain"], cookie["path"])
        if key in seen:
            continue
        seen.add(key)
        cookies.append(cookie)

    if not cookies:
        return None
    return {"cookies": cookies, "origins": origins}


def _flat_cookie_map_to_list(payload: dict[str, Any]) -> list[dict[str, str]]:
    names = [name for name in _REDDIT_COOKIE_ORDER if isinstance(payload.get(name), str)]
    names.extend(
        sorted(
            name
            for name, value in payload.items()
            if name not in names and isinstance(value, str) and _looks_like_reddit_cookie_name(name)
        )
    )
    return [{"name": name, "value": str(payload[name])} for name in names]


def _normalize_reddit_cookie(raw_cookie: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw_cookie, dict):
        return None
    name = str(raw_cookie.get("name") or "").strip()
    value = raw_cookie.get("value")
    if not name or value is None:
        return None
    value = str(value)
    if value == "":
        return None

    raw_domain = str(raw_cookie.get("domain") or "").strip()
    raw_domain_lower = raw_domain.lower()
    if raw_domain and "reddit.com" not in raw_domain_lower:
        return None
    if not raw_domain and not _looks_like_reddit_cookie_name(name):
        return None

    same_site = _normalize_same_site(raw_cookie.get("sameSite") or raw_cookie.get("same_site"))
    expires = raw_cookie.get("expires", raw_cookie.get("expirationDate", -1))
    try:
        expires_value = float(expires)
    except (TypeError, ValueError):
        expires_value = -1

    return {
        "name": name,
        "value": value,
        "domain": ".reddit.com",
        "path": str(raw_cookie.get("path") or "/"),
        "expires": expires_value,
        "httpOnly": bool(raw_cookie.get("httpOnly", name in _REDDIT_HTTP_ONLY_COOKIES)),
        "secure": bool(raw_cookie.get("secure", True)),
        "sameSite": same_site,
    }


def _looks_like_reddit_cookie_name(name: str) -> bool:
    normalized = name.strip()
    return normalized in _REDDIT_COOKIE_NAMES or normalized.startswith("reddit_")


def _normalize_same_site(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("_", "")
    if normalized == "strict":
        return "Strict"
    if normalized in {"none", "norestriction", "no_restriction"}:
        return "None"
    return "Lax"


def _cookie_header_from_session(session_path: str | Path | None = None) -> str:
    path = Path(session_path) if session_path is not None else _reddit_session_path()
    normalized_path = _ensure_reddit_storage_state(path)
    if not normalized_path:
        return ""

    try:
        state = json.loads(Path(normalized_path).read_text(encoding="utf-8"))
    except Exception:
        return ""

    pairs: list[str] = []
    seen: set[str] = set()
    for cookie in state.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "").lower()
        if not name or not value or name in seen:
            continue
        if "reddit.com" not in domain and not _looks_like_reddit_cookie_name(name):
            continue
        seen.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _cookie_header_from_state(state: dict[str, Any]) -> str:
    pairs: list[str] = []
    seen: set[str] = set()
    for cookie in state.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        if not name or not value or name in seen:
            continue
        if not _looks_like_reddit_cookie_name(name):
            continue
        seen.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _is_jina_reddit_blocked(title: str, content: str) -> bool:
    text = f"{title}\n{content}".lower()
    return any(pattern in text for pattern in _JINA_BLOCK_PATTERNS)


def validate_reddit_session(
    *,
    session_path: str | Path | None = None,
    timeout: int = 8,
) -> dict[str, Any]:
    """Validate saved Reddit cookies with Reddit's own /api/me.json endpoint."""
    path = Path(session_path) if session_path is not None else _reddit_session_path()
    state = _load_reddit_storage_state(path)
    if not state:
        return {
            "name": "direct_json_session",
            "status": "missing",
            "authenticated": False,
            "message": "未找到可用 reddit.json Cookie",
            "session_path": str(path),
            "cookie_count": 0,
        }

    cookie_header = _cookie_header_from_state(state)
    cookie_count = len(state.get("cookies", []))
    if not cookie_header:
        return {
            "name": "direct_json_session",
            "status": "missing",
            "authenticated": False,
            "message": "reddit.json 不含 Reddit 认证 Cookie",
            "session_path": str(path),
            "cookie_count": cookie_count,
        }

    headers = {
        "User-Agent": reddit_user_agent(),
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": cookie_header,
    }
    try:
        resp = http_client.get("https://www.reddit.com/api/me.json", headers=headers, timeout=timeout)
    except Exception as exc:
        return {
            "name": "direct_json_session",
            "status": "warning",
            "authenticated": False,
            "message": f"/api/me.json 校验失败：{exc}",
            "session_path": str(path),
            "cookie_count": cookie_count,
        }

    status_code = int(getattr(resp, "status_code", 0) or 0)
    if status_code != 200:
        status = "invalid" if status_code in {401, 403} else "warning"
        return {
            "name": "direct_json_session",
            "status": status,
            "authenticated": False,
            "message": f"/api/me.json 返回 HTTP {status_code}",
            "session_path": str(path),
            "cookie_count": cookie_count,
            "http_status": status_code,
        }

    try:
        data = resp.json()
    except Exception as exc:
        return {
            "name": "direct_json_session",
            "status": "warning",
            "authenticated": False,
            "message": f"/api/me.json JSON 解析失败：{exc}",
            "session_path": str(path),
            "cookie_count": cookie_count,
            "http_status": status_code,
        }

    username = _reddit_me_username(data)
    if not username:
        return {
            "name": "direct_json_session",
            "status": "invalid",
            "authenticated": False,
            "message": "/api/me.json 未返回用户名，登录态不可用",
            "session_path": str(path),
            "cookie_count": cookie_count,
            "http_status": status_code,
        }

    return {
        "name": "direct_json_session",
        "status": "ok",
        "authenticated": True,
        "username": username,
        "message": f"Reddit session 可用：u/{username}",
        "session_path": str(path),
        "cookie_count": cookie_count,
        "http_status": status_code,
    }


def _reddit_me_username(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    account = data.get("data") if isinstance(data.get("data"), dict) else data
    subreddit = account.get("subreddit") if isinstance(account, dict) else {}
    if not isinstance(subreddit, dict):
        subreddit = {}
    prefixed = str(subreddit.get("display_name_prefixed") or "")
    return str(
        account.get("name")
        or subreddit.get("display_name")
        or prefixed.removeprefix("u/")
        or ""
    ).strip()


def probe_reddit_backends() -> dict[str, Any]:
    """Return Reddit backend health, in feedgrab's preferred execution order."""
    checks = [
        validate_reddit_session(),
        _probe_reddit_cdp_cookie(),
        _probe_reddit_playwright_session(),
        _probe_opencli_reddit(),
        _probe_rdt_cli(),
    ]
    default_names = ["direct_json_session", "cdp_cookie", "playwright_session", "opencli", "rdt-cli"]
    for idx, item in enumerate(checks):
        item.setdefault("name", default_names[idx])
    active = ""
    for item in checks:
        if item.get("status") == "ok":
            active = str(item.get("name") or "")
            break
    return {
        "platform": "reddit",
        "active_backend": active or "none",
        "status": "ok" if active else ("warning" if any(c.get("status") == "warning" for c in checks) else "error"),
        "checks": checks,
    }


def _probe_reddit_cdp_cookie() -> dict[str, Any]:
    from feedgrab.config import chrome_cdp_port

    if not reddit_cdp_enabled():
        return {"name": "cdp_cookie", "status": "off", "message": "REDDIT_CDP_ENABLED=false"}
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {"name": "cdp_cookie", "status": "off", "message": "Playwright 未安装，无法探测 CDP"}

    endpoint = f"ws://127.0.0.1:{chrome_cdp_port()}/devtools/browser"
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(endpoint)
            cookies: list[dict[str, Any]] = []
            for ctx in browser.contexts:
                cookies.extend(ctx.cookies())
            browser.close()
    except Exception as exc:
        return {"name": "cdp_cookie", "status": "off", "message": f"Chrome CDP 不可用：{exc}"}

    reddit_cookies = [
        c for c in cookies
        if "reddit.com" in str(c.get("domain") or "").lower()
    ]
    names = {str(c.get("name") or "") for c in reddit_cookies}
    if names.intersection({"reddit_session", "token_v2"}):
        return {"name": "cdp_cookie", "status": "ok", "message": f"Chrome CDP 含 Reddit 认证 Cookie（{len(reddit_cookies)} 个）"}
    if reddit_cookies:
        return {"name": "cdp_cookie", "status": "warning", "message": f"Chrome CDP 仅发现 Reddit 游客 Cookie（{len(reddit_cookies)} 个）"}
    return {"name": "cdp_cookie", "status": "off", "message": "Chrome CDP 未发现 Reddit Cookie"}


def _probe_reddit_playwright_session() -> dict[str, Any]:
    path = _reddit_session_path()
    state = _load_reddit_storage_state(path)
    if not state:
        return {"name": "playwright_session", "status": "off", "message": "未找到 reddit.json"}
    names = {
        str(cookie.get("name") or "")
        for cookie in state.get("cookies", [])
        if isinstance(cookie, dict)
    }
    if names.intersection({"reddit_session", "token_v2"}):
        return {"name": "playwright_session", "status": "ok", "message": f"reddit.json 含认证 Cookie（{len(names)} 个）"}
    return {"name": "playwright_session", "status": "warning", "message": "reddit.json 存在，但缺少 reddit_session/token_v2"}


def _probe_opencli_reddit() -> dict[str, Any]:
    executable = shutil.which("opencli")
    if not executable:
        return {"name": "opencli", "status": "off", "message": "未安装 opencli"}
    try:
        version = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception as exc:
        return {"name": "opencli", "status": "error", "message": f"opencli 无法执行：{exc}"}
    if version.returncode != 0:
        return {"name": "opencli", "status": "error", "message": (version.stderr or version.stdout or "opencli --version failed").strip()}

    try:
        help_result = subprocess.run(
            [executable, "reddit", "--help"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception as exc:
        return {"name": "opencli", "status": "warning", "message": f"opencli 已安装，但 reddit 子命令探测失败：{exc}"}
    if help_result.returncode != 0:
        detail = (help_result.stderr or help_result.stdout or "").strip().splitlines()
        return {"name": "opencli", "status": "warning", "message": f"opencli reddit 子命令不可用：{detail[-1] if detail else '无输出'}"}

    version_text = (version.stdout or "").strip()
    if _opencli_extension_installed():
        return {
            "name": "opencli",
            "status": "warning",
            "message": f"OpenCLI 已安装（v{version_text}）且发现扩展目录；仍需实际 Browser Bridge/登录态探测后才可作为 active backend",
        }
    return {
        "name": "opencli",
        "status": "warning",
        "message": f"OpenCLI 已安装（v{version_text}），但未确认 Chrome 扩展/Browser Bridge；需浏览器登录态才能读取 Reddit",
    }


def _opencli_extension_installed() -> bool:
    roots: list[Path] = []
    local_app_data = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else None
    if local_app_data:
        roots.append(local_app_data / "Google" / "Chrome" / "User Data")
        roots.append(local_app_data / "Microsoft" / "Edge" / "User Data")
    roots.extend([
        Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
        Path.home() / ".config" / "google-chrome",
        Path.home() / ".config" / "chromium",
    ])
    for root in roots:
        if not root.exists():
            continue
        try:
            for profile in root.iterdir():
                if (profile / "Extensions" / _OPENCLI_EXTENSION_ID).exists():
                    return True
        except Exception:
            continue
    return False


def _probe_rdt_cli() -> dict[str, Any]:
    executable = shutil.which("rdt")
    if not executable:
        return {"name": "rdt-cli", "status": "off", "message": "未安装 rdt-cli"}
    try:
        result = subprocess.run(
            [executable, "status", "--json"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"name": "rdt-cli", "status": "error", "message": "rdt status 超时"}
    except Exception as exc:
        return {"name": "rdt-cli", "status": "error", "message": f"rdt 无法执行：{exc}"}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        return {"name": "rdt-cli", "status": "error", "message": f"rdt 异常退出：{detail[-1] if detail else result.returncode}"}
    try:
        data = json.loads(result.stdout or "{}")
    except Exception:
        return {"name": "rdt-cli", "status": "warning", "message": "rdt status 输出不是 JSON"}
    info = data.get("data") if isinstance(data, dict) else {}
    if not isinstance(info, dict):
        info = {}
    if info.get("authenticated"):
        username = info.get("username") or ""
        suffix = f"：u/{username}" if username else ""
        return {"name": "rdt-cli", "status": "ok", "message": f"rdt-cli 已登录{suffix}"}
    return {"name": "rdt-cli", "status": "warning", "message": "rdt-cli 已安装但未登录，请运行 rdt login"}


# =============================================================================
# URL helpers
# =============================================================================

def is_reddit_url(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www.") or netloc.startswith("old."):
        netloc = netloc.split(".", 1)[1]
    return netloc == "reddit.com" or netloc == "redd.it" or netloc.endswith(".reddit.com")


def parse_reddit_url(url: str) -> Tuple[str, Dict[str, str]]:
    """Classify a Reddit URL.

    Returns:
        (kind, info)
          kind = "post" | "subreddit" | "user"
          info dict carries fields such as id / subreddit / sort
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    query = parse_qs(parsed.query)

    # /search/?q=... or /r/<sub>/search/?q=...
    if parts[:1] == ["search"] or (len(parts) >= 3 and parts[0] == "r" and parts[2] == "search"):
        subreddit = parts[1] if len(parts) >= 3 and parts[0] == "r" else ""
        keyword = (query.get("q") or [""])[0].strip()
        sort = _normalize_search_sort((query.get("sort") or ["relevance"])[0])
        time_range = _normalize_search_time_range((query.get("t") or ["all"])[0])
        return "search", {
            "keyword": keyword,
            "sort": sort,
            "time_range": time_range,
            "subreddit": subreddit,
        }

    # redd.it/<id> short link
    if netloc == "redd.it" or netloc.endswith(".redd.it"):
        if parts:
            return "post", {"id": parts[0]}
        raise ValueError(f"无效的 redd.it 短链: {url}")

    # /r/<sub>/comments/<id>/...
    if len(parts) >= 4 and parts[0] == "r" and parts[2] == "comments":
        return "post", {
            "id": parts[3],
            "subreddit": parts[1],
            "slug": parts[4] if len(parts) > 4 else "",
        }

    # /comments/<id>/<slug>?
    if len(parts) >= 2 and parts[0] == "comments":
        return "post", {"id": parts[1]}

    # /r/<sub> or /r/<sub>/<sort>
    if len(parts) >= 1 and parts[0] == "r":
        sub = parts[1] if len(parts) > 1 else ""
        sort = parts[2] if len(parts) > 2 and parts[2] in _VALID_SORTS else "hot"
        return "subreddit", {"subreddit": sub, "sort": sort}

    # /user/<name> or /u/<name>
    if parts and parts[0] in ("user", "u") and len(parts) >= 2:
        return "user", {"username": parts[1]}

    raise ValueError(f"不支持的 Reddit 链接格式: {url}")


def _canonicalize_post_url(info: Dict[str, str]) -> str:
    """Canonicalise to old.reddit.com .json endpoint."""
    pid = info.get("id", "")
    sub = info.get("subreddit", "")
    if sub:
        return f"https://old.reddit.com/r/{sub}/comments/{pid}/.json?limit=500&raw_json=1"
    # No sub known — use generic comments endpoint that follows redirects
    return f"https://old.reddit.com/comments/{pid}/.json?limit=500&raw_json=1"


def _normalize_search_sort(sort: str) -> str:
    normalized = str(sort or "relevance").strip().lower()
    return normalized if normalized in _VALID_SEARCH_SORTS else "relevance"


def _normalize_search_time_range(time_range: str) -> str:
    normalized = str(time_range or "all").strip().lower()
    return normalized if normalized in _VALID_SEARCH_TIME_RANGES else "all"


def _search_sort_allows_time(sort: str) -> bool:
    return _normalize_search_sort(sort) in _SEARCH_SORTS_WITH_TIME


def _effective_search_time_range(sort: str, time_range: str) -> str:
    return _normalize_search_time_range(time_range) if _search_sort_allows_time(sort) else ""


def _build_reddit_search_json_url(
    keyword: str,
    *,
    sort: str = "relevance",
    time_range: str = "all",
    limit: int = 10,
    subreddit: str = "",
) -> str:
    normalized_sort = _normalize_search_sort(sort)
    effective_time = _effective_search_time_range(normalized_sort, time_range)
    params: dict[str, str | int] = {
        "q": keyword,
        "type": "link",
        "sort": normalized_sort,
        "limit": limit,
        "raw_json": 1,
    }
    if effective_time:
        params["t"] = effective_time
    clean_sub = _normalize_subreddit_name(subreddit)
    if clean_sub:
        params["restrict_sr"] = "on"
        return f"https://old.reddit.com/r/{clean_sub}/search.json?{urlencode(params)}"
    return f"https://old.reddit.com/search.json?{urlencode(params)}"


def _with_after(url: str, after: str) -> str:
    if not after:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["after"] = [after]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def _build_reddit_search_display_url(
    keyword: str,
    *,
    sort: str = "relevance",
    time_range: str = "all",
    subreddit: str = "",
) -> str:
    normalized_sort = _normalize_search_sort(sort)
    effective_time = _effective_search_time_range(normalized_sort, time_range)
    params = {
        "q": keyword,
        "type": "posts",
        "sort": normalized_sort,
    }
    if effective_time:
        params["t"] = effective_time
    clean_sub = _normalize_subreddit_name(subreddit)
    if clean_sub:
        return f"https://www.reddit.com/r/{clean_sub}/search/?{urlencode(params)}"
    return f"https://www.reddit.com/search/?{urlencode(params)}"


def _normalize_subreddit_name(subreddit: str) -> str:
    text = str(subreddit or "").strip().strip("/")
    if text.lower().startswith("r/"):
        text = text[2:]
    return re.sub(r"[^A-Za-z0-9_]+", "", text)


# =============================================================================
# Tier 0 — direct .json with feedgrab UA
# =============================================================================

def _fetch_json_direct(url: str) -> Optional[Any]:
    headers = {
        "User-Agent": reddit_user_agent(),
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    cookie_header = _cookie_header_from_session()
    if cookie_header:
        headers["Cookie"] = cookie_header
    attempts = reddit_retry_attempts()
    for attempt in range(1, attempts + 1):
        try:
            resp = http_client.get(url, headers=headers, timeout=20)
            status_code = int(getattr(resp, "status_code", 0) or 0)
            if status_code == 200:
                try:
                    return resp.json()
                except Exception:
                    return None
            if status_code == 429 and attempt < attempts:
                delay = _retry_after_seconds(getattr(resp, "headers", {}) or {}, attempt)
                logger.warning(f"[Reddit] direct .json HTTP 429，{delay:.1f}s 后重试")
                time.sleep(delay)
                continue
            if 500 <= status_code < 600 and attempt < attempts:
                delay = min(30.0, 2.0 ** attempt)
                logger.warning(f"[Reddit] direct .json HTTP {status_code}，{delay:.1f}s 后重试")
                time.sleep(delay)
                continue
            logger.warning(f"[Reddit] direct .json HTTP {status_code}")
            return None
        except Exception as exc:
            if attempt < attempts:
                delay = min(30.0, 2.0 ** attempt)
                logger.warning(f"[Reddit] direct .json 异常，{delay:.1f}s 后重试: {exc}")
                time.sleep(delay)
                continue
            logger.warning(f"[Reddit] direct .json 异常: {exc}")
            return None
    return None


def _retry_after_seconds(headers: dict[str, Any], attempt: int) -> float:
    raw = ""
    for key, value in headers.items():
        if str(key).lower() == "retry-after":
            raw = str(value)
            break
    if raw:
        try:
            return max(0.0, min(float(raw), 60.0))
        except ValueError:
            pass
    return min(30.0, 2.0 ** attempt)


# =============================================================================
# Tier 1/2 — CDP / Browser fetch (page.evaluate fetch)
# =============================================================================

async def _fetch_json_via_browser(url: str) -> Optional[Any]:
    """Run fetch(<json url>) inside a real browser to bypass 403 IP blocks.

    Strategy:
      1. If REDDIT_CDP_ENABLED — connect to running Chrome and reuse a
         reddit.com cookie context.
      2. Otherwise stealth_launch a fresh patchright browser (with
         sessions/reddit.json storage_state if available).
      3. Navigate to https://old.reddit.com/ first, then page.evaluate fetch.
    """
    from feedgrab.config import reddit_page_load_timeout, chrome_cdp_port
    from feedgrab.fetchers.browser import (
        get_async_playwright,
        stealth_launch,
        get_stealth_context_options,
        setup_resource_blocking,
        generate_referer,
    )

    pw = browser = context = page = None
    used_cdp = False

    try:
        try:
            from playwright.async_api import async_playwright as _pw_factory
        except ImportError:
            _pw_factory = None

        # --- Tier 1: CDP connect ---
        if reddit_cdp_enabled() and _pw_factory:
            try:
                pw = await _pw_factory().start()
                ws_url = f"ws://127.0.0.1:{chrome_cdp_port()}/devtools/browser"
                browser = await pw.chromium.connect_over_cdp(ws_url)
                for ctx in browser.contexts:
                    cookies = await ctx.cookies()
                    if any(c.get("domain", "").endswith("reddit.com") for c in cookies):
                        context = ctx
                        page = await ctx.new_page()
                        used_cdp = True
                        logger.info("[Reddit] CDP: 复用 Chrome reddit.com 会话")
                        break
                if not used_cdp:
                    await browser.close()
                    await pw.stop()
                    pw = browser = None
            except Exception as exc:
                logger.debug(f"[Reddit] CDP 连接失败: {exc}")
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                if pw:
                    try:
                        await pw.stop()
                    except Exception:
                        pass
                pw = browser = None

        # --- Tier 2: stealth launch ---
        if not page:
            async_playwright = get_async_playwright()
            pw = await async_playwright().start()
            browser = await stealth_launch(pw, headless=True)
            storage_state = _ensure_reddit_storage_state()
            context = await browser.new_context(
                **get_stealth_context_options(storage_state=storage_state)
            )
            await setup_resource_blocking(context)
            page = await context.new_page()

        await page.goto(
            "https://old.reddit.com/",
            wait_until="domcontentloaded",
            timeout=reddit_page_load_timeout(),
            referer=generate_referer("https://old.reddit.com/"),
        )
        await page.wait_for_timeout(1000)

        result = await page.evaluate(
            """async (jsonUrl) => {
                try {
                    const r = await fetch(jsonUrl, {
                        credentials: 'include',
                        headers: { 'Accept': 'application/json' },
                    });
                    const text = await r.text();
                    return { status: r.status, body: text };
                } catch (e) {
                    return { status: 0, body: String(e) };
                }
            }""",
            url,
        )

        if not result:
            return None
        if result.get("status") != 200:
            logger.warning(f"[Reddit] browser fetch HTTP {result.get('status')}")
            return None
        body = result.get("body") or ""
        try:
            import json as _json
            return _json.loads(body)
        except Exception:
            return None

    except Exception as exc:
        logger.warning(f"[Reddit] browser fetch 异常: {exc}")
        return None

    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        if not used_cdp:
            try:
                if context:
                    await context.close()
            except Exception:
                pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
        try:
            if pw:
                await pw.stop()
        except Exception:
            pass


# =============================================================================
# Markdown rendering
# =============================================================================

def _format_unix_iso(ts: float) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_unix_display(ts: float) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _strip_html(html: str) -> str:
    """Lightweight HTML → Markdown for Reddit body_html / selftext_html."""
    if not html:
        return ""
    text = html
    import html as _html
    text = _html.unescape(text)
    text = re.sub(r"<p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<em>(.*?)</em>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<pre>(.*?)</pre>", r"\n```\n\1\n```\n", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(
        r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>',
        r"[\2](\1)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Strip any remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _render_post(post: Dict[str, Any], comments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Render a Reddit post + top-level comments into the result dict."""
    title = (post.get("title") or "").strip() or "Untitled"
    author = post.get("author", "[deleted]") or "[deleted]"
    sub = post.get("subreddit", "")
    score = post.get("score", 0)
    upvote_ratio = post.get("upvote_ratio", 0.0)
    num_comments = post.get("num_comments", 0)
    flair = post.get("link_flair_text", "") or ""
    is_self = bool(post.get("is_self", True))
    permalink = post.get("permalink", "")
    if permalink and not permalink.startswith("http"):
        permalink = f"https://www.reddit.com{permalink}"
    linked_url = ""
    if not is_self:
        linked_url = post.get("url_overridden_by_dest") or post.get("url", "") or ""
        if linked_url and linked_url.startswith(f"/r/{sub}"):
            linked_url = ""  # internal link only, not external
    selftext_html = post.get("selftext_html") or ""
    selftext = post.get("selftext") or ""
    body_md = _strip_html(selftext_html) if selftext_html else (selftext or "")

    ts_iso = _format_unix_iso(post.get("created_utc") or post.get("created") or 0)

    # Header line
    header_meta = [f"**作者：** u/{author}", f"**r/{sub}**", f"**得分：** {score}"]
    if num_comments:
        header_meta.append(f"**评论：** {num_comments}")
    if upvote_ratio:
        header_meta.append(f"**好评率：** {int(upvote_ratio * 100)}%")
    if flair:
        header_meta.append(f"**标签：** {flair}")
    header_line = "> " + " · ".join(header_meta)

    body_parts: List[str] = [header_line, ""]
    if body_md:
        body_parts.append(body_md)
        body_parts.append("")
    if linked_url:
        body_parts.append(f"[🔗 原始外链]({linked_url})")
        body_parts.append("")

    media = _extract_media_fields(post, permalink=permalink, linked_url=linked_url)
    media_lines = _render_media_lines(media)
    if media_lines:
        body_parts.append("## 媒体")
        body_parts.append("")
        body_parts.extend(media_lines)
        body_parts.append("")

    # Comments. Default mode remains top-level only; tree/all are opt-in.
    if comments:
        body_parts.append("---")
        body_parts.append("")
        body_parts.append(f"## 💬 评论（Top {len(comments)}，按得分排序）")
        body_parts.append("")
        for idx, c in enumerate(comments, 1):
            c_author = c.get("author", "[deleted]") or "[deleted]"
            c_score = c.get("score", 0)
            c_html = c.get("body_html") or ""
            c_body = _strip_html(c_html) if c_html else (c.get("body") or "")
            if not c_body:
                continue
            depth = int(c.get("_depth") or 0)
            marker = "#" if depth <= 0 else f"{'>' * depth} ↳"
            body_parts.append(f"### {marker}{idx} u/{c_author} · {c_score} 分")
            body_parts.append("")
            body_parts.append(_indent_comment_body(c_body, depth))
            body_parts.append("")

    content = "\n".join(body_parts).rstrip() + "\n"

    return {
        "id": post.get("id", ""),
        "title": title,
        "content": content,
        "url": permalink or post.get("url", ""),
        "author": author,
        "author_name": author,
        "subreddit": sub,
        "flair": flair,
        "score": score,
        "upvote_ratio": upvote_ratio,
        "comment_count": num_comments,
        "is_self": is_self,
        "linked_url": linked_url,
        "created_at": ts_iso,
        "tags": [],
        "category": "search/posts",
        "post_hint": media.get("post_hint", ""),
        "preview_image_url": media.get("preview_image_url", ""),
        "gallery_urls": media.get("gallery_urls", []),
        "media_url": media.get("media_url", ""),
        "images": media.get("images", []),
        "videos": media.get("videos", []),
    }


def _indent_comment_body(body: str, depth: int) -> str:
    if depth <= 0:
        return body
    prefix = "> " * depth
    return "\n".join(f"{prefix}{line}" if line else prefix.rstrip() for line in body.splitlines())


def _extract_media_fields(post: Dict[str, Any], *, permalink: str = "", linked_url: str = "") -> dict[str, Any]:
    preview_url = _extract_preview_image_url(post)
    gallery_urls = _extract_gallery_urls(post)
    post_hint = str(post.get("post_hint") or "")
    media_url = str(post.get("url_overridden_by_dest") or post.get("url") or linked_url or "")
    if media_url == permalink or media_url.startswith("/r/"):
        media_url = ""
    images = []
    videos = []
    for url in [preview_url, *gallery_urls]:
        if url and url not in images:
            images.append(url)
    if post.get("is_video") and media_url:
        videos.append(media_url)
    return {
        "post_hint": post_hint,
        "preview_image_url": preview_url,
        "gallery_urls": gallery_urls,
        "media_url": media_url,
        "images": images,
        "videos": videos,
    }


def _extract_preview_image_url(post: Dict[str, Any]) -> str:
    preview = post.get("preview") or {}
    if not isinstance(preview, dict):
        return ""
    images = preview.get("images") or []
    if not images or not isinstance(images[0], dict):
        return ""
    source = images[0].get("source") or {}
    if not isinstance(source, dict):
        return ""
    return html_lib.unescape(str(source.get("url") or ""))


def _extract_gallery_urls(post: Dict[str, Any]) -> list[str]:
    gallery = post.get("gallery_data") or {}
    metadata = post.get("media_metadata") or {}
    if not isinstance(gallery, dict) or not isinstance(metadata, dict):
        return []
    urls: list[str] = []
    for item in gallery.get("items") or []:
        if not isinstance(item, dict):
            continue
        media_id = str(item.get("media_id") or "")
        meta = metadata.get(media_id) if media_id else None
        if not isinstance(meta, dict):
            continue
        source = meta.get("s") or {}
        if not isinstance(source, dict):
            continue
        url = html_lib.unescape(str(source.get("u") or source.get("gif") or source.get("mp4") or ""))
        if url and url not in urls:
            urls.append(url)
    return urls


def _render_media_lines(media: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    post_hint = media.get("post_hint")
    if post_hint:
        lines.append(f"- 类型：{post_hint}")
    if media.get("media_url"):
        lines.append(f"- 原始媒体：{media['media_url']}")
    if media.get("preview_image_url"):
        lines.append(f"- 预览图：{media['preview_image_url']}")
    for idx, url in enumerate(media.get("gallery_urls") or [], 1):
        lines.append(f"- 图集 {idx}：{url}")
    return lines


def _expand_morechildren(
    json_payload: Any,
    *,
    post_id: str,
    sort: str = "confidence",
) -> tuple[Any, dict[str, int]]:
    """Expand Reddit `kind=more` comment stubs for opt-in all-comments mode."""
    stats = {"expanded_count": 0, "remaining_count": 0}
    if not isinstance(json_payload, list) or len(json_payload) < 2:
        return json_payload, stats
    clean_post_id = str(post_id or "").strip()
    if not clean_post_id:
        return json_payload, stats

    seen = _existing_comment_keys(json_payload)
    rounds = reddit_morechildren_rounds()
    batch_size = reddit_morechildren_batch_size()
    for _round in range(rounds):
        stubs = _collect_morechildren_stubs(json_payload)
        if not stubs:
            break
        expanded_this_round = 0
        for stub in reversed(stubs):
            children = stub.get("children") or []
            if not children:
                continue
            insertable: list[dict[str, Any]] = []
            for start in range(0, len(children), batch_size):
                batch = children[start:start + batch_size]
                things = _fetch_morechildren(clean_post_id, batch, sort=sort)
                for thing in things:
                    if not isinstance(thing, dict):
                        continue
                    kind = thing.get("kind")
                    if kind not in {"t1", "more"}:
                        continue
                    data = thing.get("data") or {}
                    if not isinstance(data, dict):
                        continue
                    parent_id = str(data.get("parent_id") or "")
                    if parent_id and parent_id != stub.get("parent_id"):
                        continue
                    key = _comment_key(thing)
                    if key and kind == "t1":
                        if key in seen:
                            continue
                        seen.add(key)
                    insertable.append(thing)
            if not insertable:
                continue
            container = stub["container"]
            index = int(stub["index"])
            container[index:index + 1] = insertable
            expanded_this_round += sum(1 for thing in insertable if thing.get("kind") == "t1")
        stats["expanded_count"] += expanded_this_round
        if expanded_this_round <= 0:
            break

    stats["remaining_count"] = len(_collect_morechildren_stubs(json_payload))
    return json_payload, stats


def _fetch_morechildren(post_id: str, children: list[str], *, sort: str = "confidence") -> list[dict[str, Any]]:
    if not children:
        return []
    link_id = post_id if post_id.startswith("t3_") else f"t3_{post_id}"
    params = {
        "api_type": "json",
        "link_id": link_id,
        "children": ",".join(children),
        "sort": sort,
        "limit_children": "false",
        "raw_json": 1,
    }
    url = f"https://old.reddit.com/api/morechildren.json?{urlencode(params)}"
    payload = _fetch_json_direct(url)
    if not isinstance(payload, dict):
        return []
    wrapper = payload.get("json") if isinstance(payload.get("json"), dict) else payload
    errors = wrapper.get("errors") if isinstance(wrapper, dict) else []
    if errors:
        logger.warning(f"[Reddit] morechildren 返回错误：{errors}")
        return []
    data = wrapper.get("data") if isinstance(wrapper, dict) else {}
    things = data.get("things") if isinstance(data, dict) else []
    return [thing for thing in things if isinstance(thing, dict)]


def _collect_morechildren_stubs(json_payload: Any) -> list[dict[str, Any]]:
    if not isinstance(json_payload, list) or len(json_payload) < 2:
        return []
    comments_listing = json_payload[1]
    children = (comments_listing.get("data") or {}).get("children") or []
    stubs: list[dict[str, Any]] = []
    _collect_morechildren_from_children(children, stubs)
    return stubs


def _collect_morechildren_from_children(children: list[Any], stubs: list[dict[str, Any]]) -> None:
    if not isinstance(children, list):
        return
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            continue
        data = child.get("data") or {}
        if child.get("kind") == "more" and isinstance(data, dict):
            child_ids = [str(item) for item in data.get("children") or [] if item]
            if child_ids:
                stubs.append({
                    "container": children,
                    "index": index,
                    "parent_id": str(data.get("parent_id") or ""),
                    "children": child_ids,
                })
            continue
        if child.get("kind") != "t1" or not isinstance(data, dict):
            continue
        replies = data.get("replies")
        if isinstance(replies, dict):
            nested = ((replies.get("data") or {}).get("children")) or []
            _collect_morechildren_from_children(nested, stubs)


def _existing_comment_keys(json_payload: Any) -> set[str]:
    keys: set[str] = set()
    if not isinstance(json_payload, list) or len(json_payload) < 2:
        return keys
    children = ((json_payload[1].get("data") or {}).get("children")) or []
    _collect_comment_keys(children, keys)
    return keys


def _collect_comment_keys(children: list[Any], keys: set[str]) -> None:
    if not isinstance(children, list):
        return
    for child in children:
        if not isinstance(child, dict):
            continue
        key = _comment_key(child)
        if key:
            keys.add(key)
        data = child.get("data") or {}
        replies = data.get("replies") if isinstance(data, dict) else None
        if isinstance(replies, dict):
            nested = ((replies.get("data") or {}).get("children")) or []
            _collect_comment_keys(nested, keys)


def _comment_key(child: dict[str, Any]) -> str:
    data = child.get("data") or {}
    if not isinstance(data, dict):
        return ""
    name = str(data.get("name") or "")
    if name:
        return name
    comment_id = str(data.get("id") or "")
    if comment_id:
        return f"{child.get('kind', '')}_{comment_id}"
    return ""


def _extract_comments(json_payload: Any, max_n: int, mode: str = "top") -> List[Dict[str, Any]]:
    """Extract comments in top-level or flattened tree mode."""
    if not isinstance(json_payload, list) or len(json_payload) < 2:
        return []
    comments_listing = json_payload[1]
    children = (comments_listing.get("data") or {}).get("children") or []
    normalized_mode = mode if mode in {"top", "tree", "all"} else "top"
    top_level: List[Dict[str, Any]] = []
    for child in children:
        if child.get("kind") != "t1":
            continue
        data = dict(child.get("data") or {})
        if data.get("stickied"):
            continue
        data["_depth"] = 0
        top_level.append(data)
    top_level.sort(key=lambda c: c.get("score", 0), reverse=True)
    if normalized_mode == "top":
        return top_level[:max_n]

    flattened: list[dict[str, Any]] = []
    for item in top_level:
        _append_comment_tree(flattened, item, max_n=max_n, depth=0)
        if len(flattened) >= max_n:
            break
    return flattened[:max_n]


def _append_comment_tree(out: list[dict[str, Any]], comment: dict[str, Any], *, max_n: int, depth: int) -> None:
    if len(out) >= max_n:
        return
    current = dict(comment)
    current["_depth"] = depth
    out.append(current)
    replies = current.get("replies")
    if not isinstance(replies, dict):
        return
    children = ((replies.get("data") or {}).get("children")) or []
    nested: list[dict[str, Any]] = []
    for child in children:
        if child.get("kind") != "t1":
            continue
        data = dict(child.get("data") or {})
        if data.get("stickied"):
            continue
        nested.append(data)
    nested.sort(key=lambda c: c.get("score", 0), reverse=True)
    for item in nested:
        _append_comment_tree(out, item, max_n=max_n, depth=depth + 1)
        if len(out) >= max_n:
            break


def _extract_top_comments(json_payload: Any, max_n: int) -> List[Dict[str, Any]]:
    """Reddit comments JSON is a 2-element list: [post listing, comments listing]."""
    return _extract_comments(json_payload, max_n, mode="top")


def _extract_post_data(json_payload: Any) -> Optional[Dict[str, Any]]:
    """Get the top-level post data dict from a comments JSON payload."""
    if isinstance(json_payload, list) and json_payload:
        post_listing = json_payload[0]
        children = (post_listing.get("data") or {}).get("children") or []
        if children:
            return children[0].get("data") or {}
    elif isinstance(json_payload, dict):
        # Subreddit listing item
        if json_payload.get("kind") == "t3":
            return json_payload.get("data") or {}
    return None


def _extract_search_items(json_payload: Any) -> list[dict[str, Any]]:
    if not isinstance(json_payload, dict):
        return []
    children = ((json_payload.get("data") or {}).get("children")) or []
    items: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict) or child.get("kind") != "t3":
            continue
        data = child.get("data") or {}
        if not isinstance(data, dict):
            continue
        items.append(_search_item_from_post(data))
    return items


def _search_item_from_post(post: dict[str, Any]) -> dict[str, Any]:
    permalink = str(post.get("permalink") or "")
    if permalink and not permalink.startswith("http"):
        permalink = f"https://www.reddit.com{permalink}"
    external_url = str(post.get("url_overridden_by_dest") or post.get("url") or "")
    if external_url == permalink or external_url.startswith("/r/"):
        external_url = ""
    created_at = _format_unix_iso(post.get("created_utc") or post.get("created") or 0)
    selftext = str(post.get("selftext") or "")
    media = _extract_media_fields(post, permalink=permalink, linked_url=external_url)
    return {
        "kind": "t3",
        "id": str(post.get("id") or ""),
        "title": str(post.get("title") or "Untitled").strip() or "Untitled",
        "subreddit": str(post.get("subreddit") or ""),
        "author": str(post.get("author") or "[deleted]"),
        "score": int(post.get("score") or 0),
        "comment_count": int(post.get("num_comments") or post.get("comment_count") or 0),
        "upvote_ratio": float(post.get("upvote_ratio") or 0.0),
        "created_at": created_at,
        "permalink": permalink,
        "external_url": external_url,
        "selftext_summary": _summarize_text(selftext),
        "post_hint": media.get("post_hint", ""),
        "preview_image_url": media.get("preview_image_url", ""),
        "gallery_urls": media.get("gallery_urls", []),
        "media_url": media.get("media_url", ""),
    }


def _summarize_text(text: str, limit: int = 160) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _render_search_summary(
    *,
    keyword: str,
    sort: str,
    time_range: str,
    subreddit: str,
    items: list[dict[str, Any]],
    request_url: str,
) -> str:
    sort_label = _normalize_search_sort(sort)
    effective_time = _effective_search_time_range(sort_label, time_range) or "不适用"
    scope = f"r/{subreddit}" if subreddit else "全站"
    lines: list[str] = [
        f"# Reddit 搜索：{keyword}",
        "",
        f"- 范围：{scope}",
        f"- 排序：{sort_label}",
        f"- 时间：{effective_time}",
        f"- 请求：{request_url}",
        f"- 结果类型：posts/link",
        "",
        "| 标题 | Subreddit | 作者 | Score | 评论 | Upvote | 时间 | 摘要 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for item in items:
        title = _escape_table_cell(item["title"])
        link = item.get("permalink") or item.get("external_url") or ""
        title_cell = f"[{title}]({link})" if link else title
        sub = _escape_table_cell(f"r/{item.get('subreddit', '')}" if item.get("subreddit") else "")
        author = _escape_table_cell(f"u/{item.get('author', '')}" if item.get("author") else "")
        upvote = f"{int(float(item.get('upvote_ratio') or 0) * 100)}%" if item.get("upvote_ratio") else ""
        summary = _escape_table_cell(str(item.get("selftext_summary") or item.get("external_url") or ""))
        lines.append(
            f"| {title_cell} | {sub} | {author} | {item.get('score', 0)} | "
            f"{item.get('comment_count', 0)} | {upvote} | {item.get('created_at', '')[:10]} | {summary} |"
        )
    if not items:
        lines.append("| 无结果 |  |  | 0 | 0 |  |  |  |")
    lines.append("")
    return "\n".join(lines)


def _escape_table_cell(value: str) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _search_category(keyword: str) -> str:
    clean_keyword = str(keyword or "").strip()
    return f"search/{clean_keyword}" if clean_keyword else "search"


# =============================================================================
# Public API
# =============================================================================

async def fetch_reddit(url: str) -> Dict[str, Any]:
    """Fetch a single Reddit post (with comments) through the tier chain."""
    kind, info = parse_reddit_url(url)

    if kind == "subreddit":
        raise RuntimeError(
            f"Reddit 子版块请使用：feedgrab reddit-sub {info.get('subreddit')} --sort {info.get('sort','hot')}"
        )
    if kind == "user":
        raise RuntimeError("Reddit 用户主页抓取尚未支持（v0.21+）")

    json_url = _canonicalize_post_url(info)

    # Tier 0 — direct
    payload = _fetch_json_direct(json_url)

    # Tier 1/2 — browser
    if not payload:
        logger.info("[Reddit] direct .json 失败，尝试 browser fetch...")
        payload = await _fetch_json_via_browser(json_url)

    # Reject error payloads
    if isinstance(payload, dict) and (payload.get("error") or payload.get("reason")):
        raise RuntimeError(
            f"Reddit 帖子无法访问: {payload.get('reason') or payload.get('error')}"
        )

    if not payload:
        # Tier 3 — Jina fallback (no comments)
        logger.info("[Reddit] browser 失败，使用 Jina 兜底...")
        try:
            from feedgrab.fetchers.jina import fetch_via_jina
            jina_data = fetch_via_jina(url)
            if jina_data and jina_data.get("content"):
                title = jina_data.get("title", "Untitled")
                content = jina_data["content"]
                if _is_jina_reddit_blocked(title, content):
                    logger.warning("[Reddit] Jina 返回 Reddit 拦截页，拒绝保存为成功结果")
                    raise RuntimeError("Jina returned Reddit block page")
                return {
                    "id": info.get("id", ""),
                    "title": title,
                    "content": content,
                    "url": url,
                    "author": "reddit",
                    "author_name": "reddit",
                    "subreddit": info.get("subreddit", ""),
                    "flair": "",
                    "score": 0,
                    "upvote_ratio": 0.0,
                    "comment_count": 0,
                    "is_self": True,
                    "linked_url": "",
                    "created_at": "",
                    "tags": [],
                    "category": "search/posts",
                }
        except Exception as exc:
            logger.warning(f"[Reddit] Jina 兜底失败: {exc}")
        raise RuntimeError(f"Reddit 抓取全部 Tier 失败: {url}")

    post = _extract_post_data(payload)
    if not post:
        raise RuntimeError(f"Reddit 响应不含帖子数据: {url}")

    reply_mode = reddit_reply_mode()
    more_stats = {"expanded_count": 0, "remaining_count": 0}
    if reply_mode == "all":
        payload, more_stats = _expand_morechildren(payload, post_id=str(post.get("id") or info.get("id") or ""))
    comments = _extract_comments(payload, reddit_max_comments(), reply_mode)
    result = _render_post(post, comments)
    result["reply_mode"] = reply_mode
    result["rendered_reply_count"] = len(comments)
    result["more_expanded_count"] = more_stats["expanded_count"]
    result["more_remaining_count"] = more_stats["remaining_count"]
    return result


async def fetch_reddit_search(
    keyword: str,
    *,
    sort: str = "relevance",
    time_range: str = "all",
    limit: int = 10,
    subreddit: str = "",
) -> Dict[str, Any]:
    """Fetch Reddit post search results and return a summary artifact payload."""
    clean_keyword = str(keyword or "").strip()
    if not clean_keyword:
        raise ValueError("Reddit 搜索关键词不能为空")
    normalized_sort = _normalize_search_sort(sort)
    normalized_time = _normalize_search_time_range(time_range)
    try:
        normalized_limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        normalized_limit = 10
    clean_subreddit = _normalize_subreddit_name(subreddit)

    page_limit = min(normalized_limit, 100)
    json_url = _build_reddit_search_json_url(
        clean_keyword,
        sort=normalized_sort,
        time_range=normalized_time,
        limit=page_limit,
        subreddit=clean_subreddit,
    )
    logger.info(f"[Reddit] 搜索 listing: {json_url}")

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    tier = "direct"
    after = ""
    page_count = 0
    max_pages = reddit_max_pages()
    while len(items) < normalized_limit and page_count < max_pages:
        page_url = _with_after(json_url, after) if after else json_url
        payload = _fetch_json_direct(page_url)
        current_tier = "direct"
        if not payload:
            logger.info("[Reddit] 搜索 direct .json 失败，尝试 browser fetch...")
            payload = await _fetch_json_via_browser(page_url)
            current_tier = "browser"
        if isinstance(payload, dict) and (payload.get("error") or payload.get("reason")):
            raise RuntimeError(
                f"Reddit 搜索无法访问: {payload.get('reason') or payload.get('error')}"
            )
        if not payload:
            if page_count == 0:
                raise RuntimeError(f"Reddit 搜索抓取失败: {clean_keyword}")
            break
        if current_tier != "direct":
            tier = current_tier if tier == "direct" else "mixed"

        for item in _extract_search_items(payload):
            item_id = str(item.get("id") or item.get("permalink") or "")
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            items.append(item)
            if len(items) >= normalized_limit:
                break
        data = payload.get("data") if isinstance(payload, dict) else {}
        after = str((data or {}).get("after") or "")
        page_count += 1
        if not after:
            break

    display_url = _build_reddit_search_display_url(
        clean_keyword,
        sort=normalized_sort,
        time_range=normalized_time,
        subreddit=clean_subreddit,
    )
    effective_time = _effective_search_time_range(normalized_sort, normalized_time)
    content = _render_search_summary(
        keyword=clean_keyword,
        sort=normalized_sort,
        time_range=normalized_time,
        subreddit=clean_subreddit,
        items=items,
        request_url=json_url,
    )
    item_key = "|".join([clean_keyword, normalized_sort, effective_time, clean_subreddit])
    item_id = hashlib.md5(item_key.encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"reddit_search_{item_id}",
        "title": f"Reddit 搜索：{clean_keyword}",
        "content": content,
        "url": display_url,
        "author": "reddit",
        "author_name": "reddit",
        "subreddit": clean_subreddit,
        "flair": "",
        "score": 0,
        "upvote_ratio": 0.0,
        "comment_count": len(items),
        "is_self": True,
        "linked_url": "",
        "created_at": "",
        "tags": ["reddit", "search"],
        "category": _search_category(clean_keyword),
        "search_keyword": clean_keyword,
        "search_sort": normalized_sort,
        "search_time_range": effective_time or "",
        "result_type": "posts/link",
        "items": items,
        "tier": tier,
        "request_url": json_url,
        "page_count": page_count,
    }


async def fetch_reddit_subreddit(sub: str, sort: str = "hot", limit: int = 25) -> List[Dict[str, Any]]:
    """Fetch a subreddit listing and re-render each post with comments."""
    if sort not in _VALID_SORTS:
        raise ValueError(f"未知 sort: {sort}（可选 {sorted(_VALID_SORTS)}）")

    try:
        normalized_limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        normalized_limit = 25
    page_limit = min(normalized_limit, 100)
    listing_url = f"https://old.reddit.com/r/{sub}/{sort}.json?limit={page_limit}&raw_json=1"
    logger.info(f"[Reddit] 子版块 listing: {listing_url}")

    children: list[dict[str, Any]] = []
    after = ""
    page_count = 0
    max_pages = reddit_max_pages()
    while len(children) < normalized_limit and page_count < max_pages:
        page_url = _with_after(listing_url, after) if after else listing_url
        payload = _fetch_json_direct(page_url)
        if not payload:
            payload = await _fetch_json_via_browser(page_url)
        if not payload:
            if page_count == 0:
                raise RuntimeError(f"Reddit 子版块 listing 抓取失败: r/{sub}")
            break

        if isinstance(payload, dict) and (payload.get("error") or payload.get("reason")):
            raise RuntimeError(
                f"r/{sub} 不可访问: {payload.get('reason') or payload.get('error')}"
            )

        data = payload.get("data") if isinstance(payload, dict) else {}
        for child in ((data or {}).get("children")) or []:
            if len(children) >= normalized_limit:
                break
            children.append(child)
        after = str((data or {}).get("after") or "")
        page_count += 1
        if not after:
            break

    delay = reddit_sub_delay()
    results: List[Dict[str, Any]] = []
    for idx, child in enumerate(children, 1):
        if child.get("kind") != "t3":
            continue
        post = child.get("data") or {}
        permalink = post.get("permalink") or ""
        if not permalink:
            continue
        full_url = f"https://www.reddit.com{permalink}"
        try:
            data = await fetch_reddit(full_url)
            results.append(data)
        except Exception as exc:
            logger.warning(f"[Reddit] 第 {idx} 条 ({permalink}) 抓取失败: {exc}")
        if idx < len(children) and delay > 0:
            await asyncio.sleep(delay)
    return results
