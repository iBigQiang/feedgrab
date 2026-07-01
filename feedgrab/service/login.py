# -*- coding: utf-8 -*-
"""Login service wrappers and safe session status helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from feedgrab.config import get_session_dir
from feedgrab.login import _cookies_sufficient_for_login, login
from feedgrab.service.models import redact_value
from feedgrab.service.platform_settings import LOGIN_CAPABILITIES, get_login_capability


@dataclass
class LoginStatus:
    platform: str
    has_session: bool
    session_path: str = ""
    candidate_paths: list[str] | None = None
    cookie_count: int = 0
    account_count: int = 0
    valid_count: int = 0
    expired_count: int = 0
    unreadable_count: int = 0
    validation_mode: str = "structural"
    accounts: list[dict[str, Any]] | None = None
    modified_at: str = ""
    age_seconds: float | None = None
    status: str = "missing"
    message: str = ""
    login_required: bool = True
    capability: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "has_session": self.has_session,
            "session_path": redact_value(self.session_path),
            "candidate_paths": redact_value(self.candidate_paths or []),
            "cookie_count": self.cookie_count,
            "account_count": self.account_count,
            "valid_count": self.valid_count,
            "expired_count": self.expired_count,
            "unreadable_count": self.unreadable_count,
            "validation_mode": self.validation_mode,
            "accounts": redact_value(self.accounts or []),
            "modified_at": self.modified_at,
            "age_seconds": self.age_seconds,
            "status": self.status,
            "message": redact_value(self.message),
            "login_required": self.login_required,
            "capability": dict(self.capability or {}),
        }


@dataclass
class SessionImportResult:
    source_dir: str
    target_dir: str
    overwrite: bool = False
    imported: list[dict[str, str]] | None = None
    skipped: list[dict[str, str]] | None = None
    disabled: list[dict[str, str]] | None = None
    ignored: list[dict[str, str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_dir": redact_value(self.source_dir),
            "target_dir": redact_value(self.target_dir),
            "overwrite": self.overwrite,
            "imported": redact_value(self.imported or []),
            "skipped": redact_value(self.skipped or []),
            "disabled": redact_value(self.disabled or []),
            "ignored": redact_value(self.ignored or []),
        }


@contextlib.contextmanager
def _temporary_environment(values: dict[str, str]):
    previous = {name: os.environ.get(name) for name in values}
    for name, value in values.items():
        os.environ[name] = value
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class LoginService:
    """Run existing login flow and inspect session presence safely."""

    def __init__(self, session_dir: str | Path | None = None):
        self.session_dir = Path(session_dir) if session_dir is not None else get_session_dir()

    def login(self, platform: str, headless: bool = False) -> None:
        session_path = self._next_login_session_path(platform)
        with _temporary_environment({
            "FEEDGRAB_DATA_DIR": str(self.session_dir),
            "FEEDGRAB_LOGIN_SESSION_PATH": str(session_path),
        }):
            login(platform, headless=headless)

    def status(self, platform: str, *, live: bool = False) -> LoginStatus:
        capability = get_login_capability(platform)
        candidate_paths = self._candidate_session_paths(platform)
        if not capability.login_required:
            return LoginStatus(
                platform=platform,
                has_session=False,
                candidate_paths=[str(path) for path in candidate_paths],
                status="not_required",
                login_required=False,
                capability=capability.to_dict(),
            )

        existing_paths = [path for path in candidate_paths if path.exists()]
        if not existing_paths:
            session_path = candidate_paths[0]
            return LoginStatus(
                platform=platform,
                has_session=False,
                session_path=str(session_path),
                candidate_paths=[str(path) for path in candidate_paths],
                status="missing",
                login_required=capability.login_required,
                capability=capability.to_dict(),
            )

        accounts = [self._inspect_session_file(path, platform) for path in existing_paths]
        validation_mode = "structural"
        if live and platform.strip().lower() == "reddit":
            validation_mode = "api_me"
            self._validate_reddit_accounts(accounts)
        valid_count = sum(1 for account in accounts if account["status"] == "valid")
        unreadable_count = sum(1 for account in accounts if account["status"] == "unreadable")
        expired_count = len(accounts) - valid_count
        cookie_count = sum(int(account.get("cookie_count") or 0) for account in accounts)
        primary = next((account for account in accounts if account["status"] == "valid"), accounts[0])
        modified_at = str(primary.get("modified_at") or "")
        age_seconds = primary.get("age_seconds")
        status = "ok" if valid_count else "warning"
        message = f"已发现 {len(accounts)} 个账号，本地有效 {valid_count} 个，过期/异常 {expired_count} 个"

        return LoginStatus(
            platform=platform,
            has_session=True,
            session_path=str(primary.get("session_path") or existing_paths[0]),
            candidate_paths=[str(path) for path in candidate_paths],
            cookie_count=cookie_count,
            account_count=len(accounts),
            valid_count=valid_count,
            expired_count=expired_count,
            unreadable_count=unreadable_count,
            validation_mode=validation_mode,
            accounts=accounts,
            modified_at=modified_at,
            age_seconds=float(age_seconds) if isinstance(age_seconds, (int, float)) else None,
            status=status,
            message=message,
            login_required=capability.login_required,
            capability=capability.to_dict(),
        )

    def import_sessions(
        self,
        source_dir: str | Path,
        *,
        overwrite: bool = False,
        platform: str | None = None,
        sync: bool = False,
    ) -> SessionImportResult:
        source_path = Path(source_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        imported: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        disabled: list[dict[str, str]] = []
        ignored: list[dict[str, str]] = []

        if not source_path.exists() or not source_path.is_dir():
            return SessionImportResult(
                source_dir=str(source_path),
                target_dir=str(self.session_dir),
                overwrite=overwrite,
                imported=imported,
                skipped=skipped,
                disabled=disabled,
                ignored=[{"source": str(source_path), "reason": "source_dir_missing"}],
            )

        source_files: list[Path] = []
        for source_file in sorted(source_path.iterdir(), key=lambda path: path.name.lower()):
            if platform and not self._matches_platform_session(source_file, platform):
                ignored.append({"source": str(source_file), "reason": "platform_mismatch"})
                continue
            if not source_file.is_file() or not self._looks_like_session_json(source_file):
                ignored.append({"source": str(source_file), "reason": "not_session_json"})
                continue
            if self._is_empty_session_template(source_file):
                ignored.append({"source": str(source_file), "reason": "empty_template"})
                continue
            source_files.append(source_file)

        if sync and source_files:
            disabled = self._disable_target_sessions_missing_from_source(source_files, platform=platform)

        for source_file in source_files:
            target_file = self.session_dir / source_file.name
            if target_file.exists() and not overwrite:
                skipped.append(
                    {
                        "source": str(source_file),
                        "target": str(target_file),
                        "reason": "exists",
                    }
                )
                continue

            shutil.copy2(source_file, target_file)
            imported.append({"source": str(source_file), "target": str(target_file)})

        return SessionImportResult(
            source_dir=str(source_path),
            target_dir=str(self.session_dir),
            overwrite=overwrite,
            imported=imported,
            skipped=skipped,
            disabled=disabled,
            ignored=ignored,
        )

    def _disable_target_sessions_missing_from_source(
        self,
        source_files: list[Path],
        *,
        platform: str | None,
    ) -> list[dict[str, str]]:
        source_names = {path.name.lower() for path in source_files}
        prefixes = self._sync_prefixes(source_files, platform=platform)
        if not prefixes:
            return []

        disabled: list[dict[str, str]] = []
        disabled_dir = self.session_dir / ".disabled"
        for target_file in self._target_session_files_for_prefixes(prefixes):
            if target_file.name.lower() in source_names:
                continue
            disabled_dir.mkdir(parents=True, exist_ok=True)
            disabled_file = self._available_disabled_path(disabled_dir / target_file.name)
            target_file.replace(disabled_file)
            disabled.append(
                {
                    "source": str(target_file),
                    "target": str(disabled_file),
                    "reason": "missing_from_source",
                }
            )
        return disabled

    def _sync_prefixes(self, source_files: list[Path], *, platform: str | None) -> set[str]:
        if platform:
            capability = get_login_capability(platform)
            return {prefix.lower() for prefix in (capability.session_prefixes or (platform.strip().lower(),))}

        prefixes: set[str] = set()
        for source_file in source_files:
            for capability in LOGIN_CAPABILITIES.values():
                capability_prefixes = capability.session_prefixes or (capability.platform,)
                if any(self._session_name_matches_prefix(source_file.name, prefix) for prefix in capability_prefixes):
                    prefixes.update(prefix.lower() for prefix in capability_prefixes)
        return prefixes

    def _target_session_files_for_prefixes(self, prefixes: set[str]) -> list[Path]:
        discovered: list[Path] = []
        for prefix in sorted(prefixes):
            discovered.extend(self._matching_session_files(prefix))
        unique: dict[str, Path] = {}
        for path in discovered:
            unique[path.name.lower()] = path
        return [unique[name] for name in sorted(unique)]

    @staticmethod
    def _available_disabled_path(path: Path) -> Path:
        if not path.exists():
            return path
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        candidate = path.with_name(f"{path.stem}.{stamp}{path.suffix}")
        index = 2
        while candidate.exists():
            candidate = path.with_name(f"{path.stem}.{stamp}-{index}{path.suffix}")
            index += 1
        return candidate

    def _candidate_session_paths(self, platform: str) -> list[Path]:
        capability = get_login_capability(platform)
        prefixes = capability.session_prefixes or (platform.strip().lower(),)
        discovered: list[Path] = []

        if self.session_dir.exists():
            for prefix in prefixes:
                discovered.extend(self._matching_session_files(prefix))

        ordered: list[Path] = []
        for prefix in prefixes:
            base = self.session_dir / f"{prefix}.json"
            if base not in ordered:
                ordered.append(base)
            for path in sorted(
                (item for item in discovered if item.stem.startswith(f"{prefix}_")),
                key=lambda item: self._session_suffix_number(prefix, item),
            ):
                if path not in ordered:
                    ordered.append(path)

        for path in discovered:
            if path not in ordered:
                ordered.append(path)
        return ordered or [self.session_dir / f"{platform}.json"]

    def _next_login_session_path(self, platform: str) -> Path:
        capability = get_login_capability(platform)
        prefixes = capability.session_prefixes or (platform.strip().lower(),)
        real_by_prefix: dict[str, list[Path]] = {}

        if self.session_dir.exists():
            for prefix in prefixes:
                real_paths = [
                    path
                    for path in self._matching_session_files(prefix)
                    if not self._is_empty_session_template(path)
                ]
                if real_paths:
                    real_by_prefix[prefix] = real_paths

        for prefix in prefixes:
            if prefix in real_by_prefix:
                return self._available_numbered_session_path(prefix, start=2)

        prefix = capability.platform or platform.strip().lower()
        base = self.session_dir / f"{prefix}.json"
        if not base.exists() or self._is_empty_session_template(base):
            return base
        return self._available_numbered_session_path(prefix, start=2)

    def _available_numbered_session_path(self, prefix: str, *, start: int = 2) -> Path:
        index = max(2, start)
        while True:
            candidate = self.session_dir / f"{prefix}_{index}.json"
            if not candidate.exists() or self._is_empty_session_template(candidate):
                return candidate
            index += 1

    def _matching_session_files(self, prefix: str) -> list[Path]:
        pattern = re.compile(rf"^{re.escape(prefix)}(?:_\d+)?\.json$", re.IGNORECASE)
        return [
            path
            for path in self.session_dir.iterdir()
            if path.is_file() and pattern.match(path.name)
        ]

    @staticmethod
    def _matches_platform_session(path: Path, platform: str) -> bool:
        if not path.is_file():
            return False
        capability = get_login_capability(platform)
        prefixes = capability.session_prefixes or (platform.strip().lower(),)
        return any(LoginService._session_name_matches_prefix(path.name, prefix) for prefix in prefixes)

    @staticmethod
    def _session_name_matches_prefix(name: str, prefix: str) -> bool:
        return bool(re.match(rf"^{re.escape(prefix)}(?:_\d+)?\.json$", name, re.IGNORECASE))

    @staticmethod
    def _session_suffix_number(prefix: str, path: Path) -> int:
        if path.stem == prefix:
            return 1
        match = re.match(rf"^{re.escape(prefix)}_(\d+)$", path.stem, re.IGNORECASE)
        return int(match.group(1)) if match else 999_999

    @staticmethod
    def _looks_like_session_json(path: Path) -> bool:
        if path.suffix.lower() != ".json":
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        if isinstance(data.get("cookies"), list) or isinstance(data.get("origins"), list):
            return True
        return any(isinstance(value, str) for value in data.values())

    @staticmethod
    def _is_empty_session_template(path: Path) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(data, dict) or not data:
            return False

        if isinstance(data.get("cookies"), list) or isinstance(data.get("origins"), list):
            return not any(
                LoginService._has_template_value(value)
                for value in data.values()
            )

        if all(isinstance(value, str) for value in data.values()):
            return not any(value.strip() for value in data.values())

        return False

    @staticmethod
    def _has_template_value(value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return any(LoginService._has_template_value(item) for item in value)
        if isinstance(value, dict):
            return any(LoginService._has_template_value(item) for item in value.values())
        return value is not None

    def _inspect_session_file(self, path: Path, platform: str) -> dict[str, Any]:
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        age = (datetime.now(timezone.utc) - modified).total_seconds()
        account: dict[str, Any] = {
            "id": path.stem,
            "file_name": path.name,
            "session_path": str(path),
            "modified_at": modified.isoformat(),
            "age_seconds": age,
            "status": "invalid",
            "cookie_count": 0,
            "message": "",
        }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            account.update({"status": "unreadable", "message": f"JSON 读取失败: {exc}"})
            return account

        cookies = self._extract_cookies(data)
        account["cookie_count"] = len(cookies)
        if not cookies:
            account.update({"status": "invalid", "message": "未找到 Cookie"})
            return account

        valid_cookies = [cookie for cookie in cookies if not self._cookie_expired(cookie)]
        if not valid_cookies:
            account.update({"status": "expired", "message": "Cookie 已过期"})
            return account

        normalized_platform = self._canonical_login_platform(platform)
        if not _cookies_sufficient_for_login(normalized_platform, valid_cookies):
            account.update({"status": "invalid", "message": "缺少关键 Cookie"})
            return account

        account.update({"status": "valid", "message": "本地结构有效"})
        return account

    @staticmethod
    def _extract_cookies(data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        raw_cookies = data.get("cookies")
        if isinstance(raw_cookies, list):
            return [cookie for cookie in raw_cookies if isinstance(cookie, dict)]
        return [
            {"name": str(name), "value": value}
            for name, value in data.items()
            if isinstance(value, str) and value
        ]

    @staticmethod
    def _cookie_expired(cookie: dict[str, Any]) -> bool:
        expires = cookie.get("expires")
        if not isinstance(expires, (int, float)):
            return False
        if expires <= 0:
            return False
        return expires <= datetime.now(timezone.utc).timestamp()

    @staticmethod
    def _canonical_login_platform(platform: str) -> str:
        normalized = platform.strip().lower()
        if normalized == "x":
            return "twitter"
        if normalized in {"xiaohongshu"}:
            return "xhs"
        if normalized == "lark":
            return "feishu"
        return normalized

    @staticmethod
    def _validate_reddit_accounts(accounts: list[dict[str, Any]]) -> None:
        try:
            from feedgrab.fetchers.reddit import validate_reddit_session
        except Exception as exc:
            for account in accounts:
                if account.get("status") == "valid":
                    account.update({
                        "status": "invalid",
                        "validation_mode": "api_me",
                        "message": f"Reddit live 校验不可用: {exc}",
                    })
            return

        for account in accounts:
            if account.get("status") != "valid":
                account["validation_mode"] = "api_me"
                continue
            result = validate_reddit_session(session_path=Path(str(account.get("session_path") or "")))
            account["validation_mode"] = "api_me"
            account["live_status"] = result.get("status", "")
            account["authenticated"] = bool(result.get("authenticated", False))
            if result.get("username"):
                account["username"] = result["username"]
            if result.get("http_status"):
                account["http_status"] = result["http_status"]
            if result.get("authenticated") and result.get("status") == "ok":
                account.update({"status": "valid", "message": result.get("message", "Reddit session 可用")})
            else:
                account.update({"status": "invalid", "message": result.get("message", "Reddit session 不可用")})
