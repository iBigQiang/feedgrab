# -*- coding: utf-8 -*-
"""Stable data models for the feedgrab service layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from feedgrab.schema import UnifiedContent


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "app_secret",
    "appmsg_token",
    "auth_token",
    "cookie",
    "ct0",
    "key",
    "next_auth",
    "pass_ticket",
    "secret",
    "session",
    "token",
)

_SENSITIVE_QUERY_KEYS = (
    "access_token",
    "api_key",
    "appmsg_token",
    "auth_token",
    "code",
    "key",
    "next_auth",
    "pass_ticket",
    "secret",
    "session",
    "sig",
    "signature",
    "token",
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in {"cookies", "storage_state"}:
        return False
    if lowered in {"authorization", "proxy-authorization"}:
        return True
    if lowered.endswith("_dir") or lowered.endswith("_path"):
        return False
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.netloc:
        return value
    netloc = parts.netloc
    if parts.password is not None:
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        username = parts.username or ""
        netloc = f"{username}:[redacted]@{host}" if username else f"[redacted]@{host}"
    query = []
    changed = False
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        if any(part in key.lower() for part in _SENSITIVE_QUERY_KEYS):
            query.append((key, "[redacted]"))
            changed = True
        else:
            query.append((key, item))
    if not changed and netloc == parts.netloc:
        return value
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), parts.fragment))


def _redact_string(value: str) -> str:
    import re

    redacted = value
    redacted = re.sub(
        r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+",
        r"\1 [redacted]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b(api_key|key|code|[A-Za-z0-9_-]*(?:token|secret|session|ticket|signature|sig)[A-Za-z0-9_-]*)=([^&\s]+)",
        r"\1=[redacted]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)ws://([^/\s]+)/devtools/browser/[A-Za-z0-9._~:-]+",
        r"ws://\1/devtools/browser/[redacted]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)wss://([^/\s]+)/devtools/browser/[A-Za-z0-9._~:-]+",
        r"wss://\1/devtools/browser/[redacted]",
        redacted,
    )
    return _redact_url(redacted)


def redact_value(value: Any, *, key: str = "") -> Any:
    """Return a JSON-safe copy with common credentials redacted."""
    if _is_sensitive_key(key):
        if isinstance(value, str) and key.lower() in {"authorization", "proxy-authorization"}:
            return _redact_string(value)
        return "[redacted]"
    if key.lower() == "cookies" and isinstance(value, list):
        redacted_cookies = []
        for item in value:
            if isinstance(item, dict):
                redacted_cookies.append(
                    {
                        item_key: "[redacted]" if str(item_key).lower() == "value" else redact_value(item_value, key=str(item_key))
                        for item_key, item_value in item.items()
                    }
                )
            else:
                redacted_cookies.append(redact_value(item))
        return redacted_cookies
    if isinstance(value, dict):
        return {item_key: redact_value(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, set):
        return [redact_value(item) for item in sorted(value, key=repr)]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    return redact_value(data)


@dataclass
class Artifact:
    """A file or external artifact produced by a service operation."""

    kind: str
    path: str
    content_type: str = "text/markdown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": redact_value(self.path),
            "content_type": self.content_type,
            "metadata": redact_value(dict(self.metadata)),
        }


@dataclass
class FetchRequest:
    """Input for fetching one URL."""

    url: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": redact_value(self.url),
            "metadata": redact_value(dict(self.metadata)),
        }


@dataclass
class FetchResult:
    """Structured output for one fetch operation."""

    request: FetchRequest
    content: Optional[UnifiedContent] = None
    artifacts: list[Artifact] = field(default_factory=list)
    platform: str = ""
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat())
    success: bool = True
    error: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "content": self.content.to_dict() if self.content else None,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "platform": self.platform,
            "fetched_at": self.fetched_at,
            "success": self.success,
            "error": redact_value(dict(self.error)) if self.error else None,
        }


@dataclass
class ProgressEvent:
    """Progress signal emitted by future GUI/MCP workers."""

    stage: str
    message: str
    url: str = ""
    platform: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "message": redact_value(self.message),
            "url": redact_value(self.url),
            "platform": self.platform,
            "details": _redact_mapping(dict(self.details)),
            "created_at": self.created_at,
        }


class ServiceError(Exception):
    """Service-layer exception with JSON-safe context."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "service_error",
        recoverable: bool = True,
        details: Optional[dict[str, Any]] = None,
    ):
        safe_message = redact_value(message)
        super().__init__(safe_message)
        self.message = safe_message
        self.code = code
        self.recoverable = recoverable
        self.details = _redact_mapping(dict(details or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
            "details": dict(self.details),
        }


@dataclass
class DiagnosticResult:
    """One diagnostic check result."""

    name: str
    status: str
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": redact_value(self.message),
            "details": _redact_mapping(dict(self.details)),
        }
