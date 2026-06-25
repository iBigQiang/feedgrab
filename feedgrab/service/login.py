# -*- coding: utf-8 -*-
"""Login service wrappers and safe session status helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from feedgrab.config import get_session_dir
from feedgrab.login import login
from feedgrab.service.models import redact_value


@dataclass
class LoginStatus:
    platform: str
    has_session: bool
    session_path: str = ""
    cookie_count: int = 0
    modified_at: str = ""
    age_seconds: float | None = None
    status: str = "missing"
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "has_session": self.has_session,
            "session_path": redact_value(self.session_path),
            "cookie_count": self.cookie_count,
            "modified_at": self.modified_at,
            "age_seconds": self.age_seconds,
            "status": self.status,
            "message": redact_value(self.message),
        }


class LoginService:
    """Run existing login flow and inspect session presence safely."""

    def __init__(self, session_dir: str | Path | None = None):
        self.session_dir = Path(session_dir) if session_dir is not None else get_session_dir()

    def login(self, platform: str, headless: bool = False) -> None:
        login(platform, headless=headless)

    def status(self, platform: str) -> LoginStatus:
        session_path = self.session_dir / f"{platform}.json"
        if not session_path.exists():
            return LoginStatus(
                platform=platform,
                has_session=False,
                session_path=str(session_path),
                status="missing",
            )

        stat = session_path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        age = (datetime.now(timezone.utc) - modified).total_seconds()
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
            cookies = data.get("cookies", []) if isinstance(data, dict) else []
            cookie_count = len(cookies) if isinstance(cookies, list) else 0
            return LoginStatus(
                platform=platform,
                has_session=True,
                session_path=str(session_path),
                cookie_count=cookie_count,
                modified_at=modified.isoformat(),
                age_seconds=age,
                status="ok",
            )
        except Exception as exc:
            return LoginStatus(
                platform=platform,
                has_session=True,
                session_path=str(session_path),
                modified_at=modified.isoformat(),
                age_seconds=age,
                status="warning",
                message=f"session metadata unreadable: {exc}",
            )
