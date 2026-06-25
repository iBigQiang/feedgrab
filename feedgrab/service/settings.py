# -*- coding: utf-8 -*-
"""Settings service exposing typed runtime configuration snapshots."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from feedgrab.config import get_cookie_dir, get_data_dir, get_session_dir, get_user_agent
from feedgrab.service.models import redact_value


@dataclass
class ConfigItem:
    name: str
    value: Any
    value_type: str
    source: str = "derived"
    secret: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": "[redacted]" if self.secret and self.value else redact_value(self.value),
            "type": self.value_type,
            "source": self.source,
            "secret": self.secret,
            "description": self.description,
        }


@dataclass
class SettingsSnapshot:
    items: list[ConfigItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items]}


class SettingsService:
    """Read core feedgrab runtime settings without exposing secret values."""

    def data_dir(self) -> Path:
        return get_data_dir()

    def cookie_dir(self) -> Path:
        return get_cookie_dir()

    def session_dir(self) -> Path:
        return get_session_dir()

    def user_agent(self) -> str:
        return get_user_agent()

    def snapshot(self) -> SettingsSnapshot:
        items = [
            self._env_path("OUTPUT_DIR", "Markdown output directory"),
            self._env_path("OBSIDIAN_VAULT", "Obsidian vault output directory"),
            self._env_path("FEEDGRAB_DATA_DIR", "feedgrab data/session directory"),
            ConfigItem(
                name="session_dir",
                value=str(self.session_dir()),
                value_type="path",
                description="Resolved Playwright storage_state directory",
            ),
            ConfigItem(
                name="cookie_dir",
                value=str(self.cookie_dir()),
                value_type="path",
                description="Resolved cookie directory",
            ),
            ConfigItem(
                name="user_agent",
                value=self.user_agent(),
                value_type="string",
                source="BROWSER_USER_AGENT" if os.getenv("BROWSER_USER_AGENT") else "default",
                description="User-Agent used by browser/HTTP fetchers",
            ),
        ]
        for name in ("GROQ_API_KEY", "TWITTERAPI_IO_KEY", "FEISHU_APP_SECRET"):
            items.append(
                ConfigItem(
                    name=name,
                    value=os.getenv(name, ""),
                    value_type="secret",
                    source="env",
                    secret=True,
                )
            )
        return SettingsSnapshot(items=items)

    @staticmethod
    def _env_path(name: str, description: str) -> ConfigItem:
        value = os.getenv(name, "")
        return ConfigItem(
            name=name,
            value=value,
            value_type="path",
            source="env" if value else "unset",
            description=description,
        )
