# -*- coding: utf-8 -*-
"""
Unified HTTP client — curl_cffi TLS fingerprint → requests fallback.

Uses curl_cffi (if installed) for Chrome TLS fingerprint impersonation
(JA3/JA4 match), making HTTP requests indistinguishable from real Chrome
at the TLS layer.  Falls back to standard requests library.

Connection reuse via persistent session (domain-scoped cookie jar).
Raises requests-compatible exceptions for backward compatibility.
"""

import requests as _requests_lib  # always available (core dep)
from loguru import logger

from feedgrab.service.proxy import get_requests_proxy_config

_engine = None   # "curl_cffi" or "requests"
_session = None  # persistent session


def _ensure_session():
    """Initialize the session once (cached)."""
    global _engine, _session
    if _session is not None:
        return
    try:
        from curl_cffi.requests import Session
        _session = Session(impersonate="chrome")
        _engine = "curl_cffi"
    except ImportError:
        _session = _requests_lib.Session()
        _engine = "requests"
        logger.warning(
            "[stealth] curl_cffi 未安装，HTTP 请求使用 Python 默认 TLS 指纹。"
            "建议安装以模拟 Chrome TLS 指纹：\n"
            "  pip install curl_cffi"
        )


def _raise_compat(e):
    """Re-raise curl_cffi exceptions as requests-compatible."""
    msg = str(e).lower()
    if "timeout" in msg or "timed out" in msg:
        raise _requests_lib.Timeout(str(e)) from e
    if "connect" in msg or "resolve" in msg or "dns" in msg:
        raise _requests_lib.ConnectionError(str(e)) from e
    raise _requests_lib.RequestException(str(e)) from e


def _with_proxy_kwargs(kwargs):
    if "proxies" in kwargs:
        return kwargs
    proxies = get_requests_proxy_config()
    if proxies:
        kwargs = dict(kwargs)
        kwargs["proxies"] = proxies
    return kwargs


def get(url, **kwargs):
    """HTTP GET with TLS fingerprint impersonation.

    Accepts the same kwargs as requests.get() (headers, params, timeout, etc.).
    Returns a requests-compatible Response object.
    """
    _ensure_session()
    kwargs = _with_proxy_kwargs(kwargs)
    try:
        return _session.get(url, **kwargs)
    except _requests_lib.RequestException:
        raise
    except Exception as e:
        _raise_compat(e)


def post(url, **kwargs):
    """HTTP POST with TLS fingerprint impersonation."""
    _ensure_session()
    kwargs = _with_proxy_kwargs(kwargs)
    try:
        return _session.post(url, **kwargs)
    except _requests_lib.RequestException:
        raise
    except Exception as e:
        _raise_compat(e)


def raise_for_status(resp):
    """Call raise_for_status() with requests-compatible exception wrapping.

    curl_cffi's Response.raise_for_status() raises its own exception type,
    not requests.HTTPError.  This wrapper ensures callers that catch
    requests.HTTPError / requests.RequestException still work correctly.
    """
    try:
        resp.raise_for_status()
    except _requests_lib.HTTPError:
        raise
    except Exception as e:
        # curl_cffi raises its own RequestsError — wrap as requests.HTTPError
        raise _requests_lib.HTTPError(str(e), response=resp) from e


def get_engine_name() -> str:
    """Return active HTTP engine name ('curl_cffi' or 'requests')."""
    _ensure_session()
    return _engine
