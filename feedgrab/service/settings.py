# -*- coding: utf-8 -*-
"""Settings service exposing typed runtime configuration snapshots."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from feedgrab.config import get_cookie_dir, get_data_dir, get_session_dir, get_user_agent
from feedgrab.service.models import redact_value
from feedgrab.service.platform_settings import (
    PlatformSettingField,
    PlatformSettingsSchema,
    get_platform_settings_schema,
)
from feedgrab.service.proxy import apply_proxy_environment


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


@dataclass
class SettingsUpdateResult:
    settings_path: str
    updated: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "settings_path": redact_value(self.settings_path),
            "updated": redact_value(self.updated),
            "skipped": redact_value(self.skipped),
        }


class SettingsService:
    """Read core feedgrab runtime settings without exposing secret values."""

    def __init__(self, settings_path: str | Path | None = None):
        self.settings_path = Path(settings_path) if settings_path is not None else self._default_settings_path()
        self._schema = get_platform_settings_schema()
        self._field_map = self._schema.field_map()
        self._saved_settings = self._load_saved_settings()
        self._project_saved_settings()
        self._project_proxy_settings()

    def data_dir(self) -> Path:
        return get_data_dir()

    def cookie_dir(self) -> Path:
        return get_cookie_dir()

    def session_dir(self) -> Path:
        return get_session_dir()

    def user_agent(self) -> str:
        return get_user_agent()

    def schema(self) -> PlatformSettingsSchema:
        return self._schema

    def update(self, values: dict[str, Any]) -> SettingsUpdateResult:
        if not isinstance(values, dict):
            raise TypeError("settings values must be a dict")

        updated: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for name, value in values.items():
            field = self._field_map.get(str(name))
            if field is None:
                skipped.append({"name": str(name), "reason": "unknown_setting"})
                continue

            stored_value = self._coerce_for_storage(value, field)
            self._saved_settings[field.name] = stored_value
            os.environ[field.name] = self._coerce_for_env(stored_value, field)
            updated.append(
                {
                    "name": field.name,
                    "value": "[redacted]" if field.secret and stored_value else os.environ[field.name],
                }
            )

        if updated:
            self._save_settings()
            self._project_proxy_settings()

        return SettingsUpdateResult(
            settings_path=str(self.settings_path),
            updated=updated,
            skipped=skipped,
        )

    def snapshot(self) -> SettingsSnapshot:
        items = [
            self._path_item("OUTPUT_DIR", "Markdown output directory"),
            self._path_item("OBSIDIAN_VAULT", "Obsidian Vault（高优先级）"),
            self._path_item("FEEDGRAB_DATA_DIR", "feedgrab data/session directory"),
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
        known_names = {item.name for item in items}
        for field in self._schema.fields():
            if field.name in known_names:
                continue
            items.append(self._setting_field_item(field))
            known_names.add(field.name)
        return SettingsSnapshot(items=items)

    def _path_item(self, name: str, description: str) -> ConfigItem:
        field = self._field_map.get(name)
        if field is not None:
            item = self._setting_field_item(field)
            item.description = description
            return item
        value = os.getenv(name, "")
        return ConfigItem(
            name=name,
            value=value,
            value_type="path",
            source="env" if value else "unset",
            description=description,
        )

    def _setting_field_item(self, field: PlatformSettingField) -> ConfigItem:
        has_saved_value = field.name in self._saved_settings
        env_value = os.getenv(field.name)
        if has_saved_value:
            value = self._coerce_for_env(self._saved_settings[field.name], field)
            source = "settings_file"
        elif env_value is not None:
            value = env_value
            source = "env"
        else:
            value = self._coerce_for_env(field.default, field) if field.default != "" else ""
            source = "default" if field.default != "" else "unset"
        return ConfigItem(
            name=field.name,
            value=value,
            value_type=field.value_type,
            source=source,
            secret=field.secret,
            description=field.description,
        )

    def _load_saved_settings(self) -> dict[str, Any]:
        if not self.settings_path.exists():
            return {}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        values = data.get("values", data)
        if not isinstance(values, dict):
            return {}
        return {
            name: value
            for name, value in values.items()
            if isinstance(name, str) and name in self._field_map
        }

    def _project_saved_settings(self) -> None:
        for name, value in self._saved_settings.items():
            field = self._field_map.get(name)
            if field is not None:
                os.environ[name] = self._coerce_for_env(value, field)

    def _project_proxy_settings(self) -> None:
        proxy_setting_names = {"FEEDGRAB_PROXY_ENABLED", "FEEDGRAB_PROXY_URL", "FEEDGRAB_NO_PROXY"}
        if not any(name in self._saved_settings or os.getenv(name) is not None for name in proxy_setting_names):
            return
        apply_proxy_environment(
            enabled=os.getenv("FEEDGRAB_PROXY_ENABLED", ""),
            proxy_url=os.getenv("FEEDGRAB_PROXY_URL", ""),
            no_proxy=os.getenv("FEEDGRAB_NO_PROXY", ""),
        )

    def _save_settings(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "values": self._saved_settings,
        }
        self.settings_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _default_settings_path(self) -> Path:
        raw = os.getenv("FEEDGRAB_SETTINGS_PATH", "").strip()
        if raw:
            return Path(raw)
        return get_data_dir() / "settings.json"

    @staticmethod
    def _coerce_for_storage(value: Any, field: PlatformSettingField) -> Any:
        if field.value_type == "boolean":
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if field.value_type == "integer":
            return int(value)
        return "" if value is None else value

    @staticmethod
    def _coerce_for_env(value: Any, field: PlatformSettingField) -> str:
        if value is None:
            return ""
        if field.value_type == "boolean":
            return "true" if bool(value) else "false"
        return str(value)
