# -*- coding: utf-8 -*-
"""Shared proxy settings helpers for desktop and fetcher runtimes."""

from __future__ import annotations

import os
from urllib.parse import quote, unquote, urlsplit, urlunsplit

DEFAULT_NO_PROXY = "127.0.0.1,localhost"
_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_NO_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")
_SYSTEM_PROXY_ENV_KEYS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
)


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def is_proxy_enabled(value: object | None = None) -> bool:
    """Return whether proxy support is enabled by setting/env value."""
    if isinstance(value, bool):
        return value
    if value is not None:
        return _as_bool(value)
    raw = os.getenv("FEEDGRAB_PROXY_ENABLED")
    if raw is not None:
        return _as_bool(raw)
    return bool(get_proxy_url())


def get_proxy_url() -> str:
    legacy_enabled = os.getenv("FEEDGRAB_PROXY_ENABLED")
    if legacy_enabled is not None and not _as_bool(legacy_enabled):
        return ""

    shorthand = os.getenv("FEEDGRAB_PROXY", "").strip()
    if shorthand:
        return shorthand

    if legacy_enabled is not None:
        return os.getenv("FEEDGRAB_PROXY_URL", "").strip()

    legacy_url = os.getenv("FEEDGRAB_PROXY_URL", "").strip()
    if legacy_url:
        return legacy_url
    for key in _SYSTEM_PROXY_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def get_proxy_source() -> str:
    """Return the environment variable supplying the active proxy URL."""
    if os.getenv("FEEDGRAB_PROXY", "").strip():
        return "FEEDGRAB_PROXY"
    if os.getenv("FEEDGRAB_PROXY_URL", "").strip() and is_proxy_enabled():
        return "FEEDGRAB_PROXY_URL"
    for key in _SYSTEM_PROXY_ENV_KEYS:
        if os.getenv(key, "").strip() and is_proxy_enabled():
            return key
    return ""


def get_no_proxy() -> str:
    return (
        os.getenv("FEEDGRAB_NO_PROXY", "").strip()
        or os.getenv("NO_PROXY", "").strip()
        or os.getenv("no_proxy", "").strip()
        or DEFAULT_NO_PROXY
    )


def redact_proxy_url(url: str) -> str:
    """Hide proxy passwords while keeping scheme, username, host and port visible."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc or parts.password is None:
        return url

    hostname = parts.hostname or ""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    username = quote(unquote(parts.username or ""), safe="")
    netloc = f"{username}:[redacted]@{host}" if username else f"[redacted]@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def apply_proxy_environment(
    *,
    enabled: object | None = None,
    proxy_url: str | None = None,
    no_proxy: str | None = None,
) -> None:
    """Project feedgrab proxy settings into common HTTP environment variables."""
    active = is_proxy_enabled(enabled)
    url = (proxy_url if proxy_url is not None else get_proxy_url()).strip()
    bypass = (no_proxy if no_proxy is not None else get_no_proxy()).strip() or DEFAULT_NO_PROXY

    if active and url:
        for key in _PROXY_ENV_KEYS:
            os.environ[key] = url
        for key in _NO_PROXY_ENV_KEYS:
            os.environ[key] = bypass
        return

    for key in _PROXY_ENV_KEYS:
        if url and os.environ.get(key) == url:
            os.environ.pop(key, None)
    for key in _NO_PROXY_ENV_KEYS:
        if os.environ.get(key) == "":
            os.environ[key] = bypass


def get_requests_proxy_config() -> dict[str, str]:
    """Return requests/curl_cffi proxy kwargs for explicit callers."""
    if not is_proxy_enabled():
        return {}
    url = get_proxy_url()
    if not url:
        return {}
    return {"http": url, "https": url}


def get_playwright_proxy_options() -> dict[str, str]:
    """Return Playwright proxy config with credentials split out of the server URL."""
    if not is_proxy_enabled():
        return {}
    url = get_proxy_url()
    if not url:
        return {}
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return {}

    hostname = parts.hostname or ""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    options: dict[str, str] = {"server": f"{parts.scheme}://{host}"}
    if parts.username:
        options["username"] = unquote(parts.username)
    if parts.password:
        options["password"] = unquote(parts.password)
    bypass = get_no_proxy()
    if bypass:
        options["bypass"] = bypass
    return options


def get_ytdlp_proxy_args() -> list[str]:
    """Return yt-dlp command-line proxy arguments for the active proxy."""
    if not is_proxy_enabled():
        return []
    url = get_proxy_url()
    return ["--proxy", url] if url else []
