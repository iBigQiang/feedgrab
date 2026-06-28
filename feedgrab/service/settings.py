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

    _LEGACY_DESKTOP_DEFAULT_PATHS = {
        "e:\\obsidian\\qiang_obsidian\\inbox",
    }

    def __init__(self, settings_path: str | Path | None = None):
        self.settings_path = Path(settings_path) if settings_path is not None else self._default_settings_path()
        self._schema = get_platform_settings_schema()
        self._field_map = self._schema.field_map()
        self._saved_settings = self._load_saved_settings()
        self._migrate_legacy_desktop_defaults()
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
            self._path_item("OUTPUT_DIR", "Markdown 输出目录"),
            self._path_item("OBSIDIAN_VAULT", "Obsidian Vault"),
            self._path_item("FEEDGRAB_DATA_DIR", "feedgrab 数据/登录态目录"),
            ConfigItem(
                name="session_dir",
                value=str(self.session_dir()),
                value_type="path",
                description="已解析的 Playwright 登录态目录",
            ),
            ConfigItem(
                name="cookie_dir",
                value=str(self.cookie_dir()),
                value_type="path",
                description="已解析的 Cookie 目录",
            ),
            ConfigItem(
                name="user_agent",
                value=self.user_agent(),
                value_type="string",
                source="BROWSER_USER_AGENT" if os.getenv("BROWSER_USER_AGENT") else "default",
                description="浏览器/HTTP 抓取使用的 User-Agent",
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

    def _migrate_legacy_desktop_defaults(self) -> None:
        if not self._is_desktop_worker_runtime():
            return
        changed = False

        output_dir_value = self._saved_settings.get("OUTPUT_DIR")
        if self._is_legacy_desktop_default_path(output_dir_value) or self._is_desktop_default_output_dir_value(output_dir_value):
            install_output_dir = os.getenv("OUTPUT_DIR", "").strip()
            if install_output_dir:
                self._saved_settings["OUTPUT_DIR"] = install_output_dir
            else:
                self._saved_settings.pop("OUTPUT_DIR", None)
            changed = True

        if self._is_legacy_desktop_default_path(self._saved_settings.get("OBSIDIAN_VAULT")):
            self._saved_settings["OBSIDIAN_VAULT"] = ""
            changed = True

        if self._is_desktop_default_data_dir_value(self._saved_settings.get("FEEDGRAB_DATA_DIR")):
            install_sessions_dir = os.getenv("FEEDGRAB_INSTALL_SESSIONS_DIR", "").strip()
            if install_sessions_dir:
                self._saved_settings["FEEDGRAB_DATA_DIR"] = install_sessions_dir
                changed = True

        if changed:
            self._save_settings()

    @classmethod
    def _is_legacy_desktop_default_path(cls, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        return cls._normalize_path_for_match(value) in cls._LEGACY_DESKTOP_DEFAULT_PATHS

    @staticmethod
    def _normalize_path_for_match(value: str) -> str:
        return value.strip().strip('"').replace("/", "\\").rstrip("\\").lower()

    @staticmethod
    def _is_desktop_default_data_dir_value(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        normalized = value.strip().replace("/", "\\").rstrip("\\").lower()
        return normalized in {"", "sessions", ".", ".\\sessions", "\\sessions"}

    @staticmethod
    def _is_desktop_default_output_dir_value(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        normalized = value.strip().replace("/", "\\").rstrip("\\").lower()
        return normalized in {"", "output", ".", ".\\output", "\\output", "./output"}

    @staticmethod
    def _is_desktop_worker_runtime() -> bool:
        worker_mode = os.getenv("FEEDGRAB_WORKER_MODE", "").strip().lower()
        return worker_mode in {"1", "true", "yes", "on"} or bool(os.getenv("FEEDGRAB_DESKTOP_RUNTIME_ROOT", "").strip())

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
        if field.name == "OUTPUT_DIR":
            return SettingsService._normalize_desktop_output_dir("" if value is None else str(value))
        if field.name == "FEEDGRAB_DATA_DIR":
            return SettingsService._normalize_desktop_data_dir("" if value is None else str(value))
        return "" if value is None else value

    @staticmethod
    def _coerce_for_env(value: Any, field: PlatformSettingField) -> str:
        if value is None:
            return ""
        if field.value_type == "boolean":
            return "true" if bool(value) else "false"
        if field.name == "OUTPUT_DIR":
            return SettingsService._normalize_desktop_output_dir(str(value))
        if field.name == "FEEDGRAB_DATA_DIR":
            return SettingsService._normalize_desktop_data_dir(str(value))
        return str(value)

    @staticmethod
    def _normalize_desktop_output_dir(value: str) -> str:
        text = str(value)
        normalized = text.strip().replace("/", "\\").rstrip("\\").lower()
        if SettingsService._is_desktop_worker_runtime() and normalized in {"", "output", ".", ".\\output", "\\output", "./output"}:
            install_output_dir = os.getenv("OUTPUT_DIR", "").strip()
            if install_output_dir:
                return install_output_dir
        return text

    @staticmethod
    def _normalize_desktop_data_dir(value: str) -> str:
        text = str(value)
        normalized = text.strip().replace("/", "\\").rstrip("\\").lower()
        if normalized in {"", "sessions", ".", ".\\sessions", "\\sessions"}:
            install_sessions_dir = os.getenv("FEEDGRAB_INSTALL_SESSIONS_DIR", "").strip()
            if install_sessions_dir:
                return install_sessions_dir
        return text
