# -*- coding: utf-8 -*-
"""JSON Lines sidecar worker for desktop clients."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import sys
from typing import Any, TextIO

from loguru import logger

from feedgrab.service import FetchService, LoginService, OutputService, ServiceError
from feedgrab.service.doctor import DoctorService
from feedgrab.service.models import redact_value

try:
    from feedgrab.service.settings import SettingsService
except ModuleNotFoundError:  # pragma: no cover - protects parallel service edits.
    SettingsService = None


PROTOCOL_VERSION = 1
DEFAULT_FETCH_TIMEOUT_SECONDS = 300.0
SUPPORTED_METHODS = (
    "ping",
    "detect_platform",
    "fetch",
    "cancel",
    "doctor",
    "settings_snapshot",
    "settings_schema",
    "settings_update",
    "login_status",
    "import_login_sessions",
    "output_list",
)
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
_STRUCTURED_COMMANDS = {
    ("twitter", "search"): "x-so",
    ("x", "search"): "x-so",
    ("xhs", "search"): "xhs-so",
    ("youtube", "search"): "ytb-so",
    ("zhihu", "search"): "zhihu-so",
    ("reddit", "search"): "reddit-so",
    ("wechat", "account"): "mpweixin-id",
    ("wechat", "search"): "mpweixin-so",
}
_ARTIFACT_PATH_LINE_RE = re.compile(
    r"(?:Saved to Markdown|Summary table saved|CSV table saved|Merged summary|Summary|CSV|Downloaded|List):\s*"
    r"(?P<path>.+?\.(?:md|csv))\s*$",
    re.IGNORECASE,
)
_ZERO_ACCOUNT_OUTPUT_RE = re.compile(
    r"(?:Total:\s*0,\s*Fetched:\s*0,\s*Skipped:\s*0,\s*Failed:\s*0|"
    r"总数：0，已抓取：0，已跳过：0，失败：0)",
    re.IGNORECASE,
)


class StructuredCommandError(RuntimeError):
    """Structured CLI command failed after producing captured output."""

    def __init__(self, message: str, *, stdout_text: str = "", stderr_text: str = ""):
        super().__init__(message)
        self.stdout_text = stdout_text
        self.stderr_text = stderr_text


class _CaptureTextIO(io.StringIO):
    def reconfigure(self, **_: Any) -> None:
        return None


class _StreamingCaptureTextIO(_CaptureTextIO):
    def __init__(self, on_line):
        super().__init__()
        self._on_line = on_line
        self._pending = ""

    def write(self, text: str) -> int:
        written = super().write(text)
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            line = line.rstrip("\r").strip()
            if line:
                self._on_line(line)
        return written

    def flush_pending(self) -> None:
        line = self._pending.strip()
        self._pending = ""
        if line:
            self._on_line(line)


class SidecarWorker:
    """Handle feedgrab desktop JSONL requests and emit JSON-safe events."""

    def __init__(
        self,
        *,
        fetch_service: Any | None = None,
        doctor_service: Any | None = None,
        settings_service: Any | None = None,
        login_service: Any | None = None,
        output_service: Any | None = None,
        command_runner: Any | None = None,
        output: TextIO | None = None,
    ):
        self.fetch_service = fetch_service or FetchService()
        self.doctor_service = doctor_service or DoctorService()
        self.settings_service = settings_service or (SettingsService() if SettingsService else None)
        self.login_service = login_service or LoginService()
        self.output_service = output_service or OutputService()
        self.command_runner = command_runner or _default_command_runner
        self.output = output
        self.events: list[dict[str, Any]] = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._fetch_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._emit(
            {
                "event": "ready",
                "protocol": PROTOCOL_VERSION,
                "methods": list(SUPPORTED_METHODS),
            }
        )

    async def handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return

        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            self._emit_error(None, "invalid_json", str(exc), recoverable=True)
            return

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            self._emit_error(request_id, "invalid_params", "参数必须是对象")
            return

        if method == "fetch":
            self._start_fetch_job(str(request_id), params)
            return
        if method == "cancel":
            await self._cancel_job(request_id, params)
            return

        try:
            await self._dispatch_request(request_id, method, params)
        except Exception as exc:
            self._emit_exception(request_id, exc)

    async def wait_idle(self) -> None:
        while self._tasks:
            tasks = list(self._tasks.values())
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatch_request(self, request_id: str | None, method: str, params: dict[str, Any]) -> None:
        if method == "ping":
            self._emit_done(request_id, method, {"pong": True})
        elif method == "detect_platform":
            url = params.get("url", "")
            platform = self.fetch_service.detect_platform(url)
            self._emit_done(request_id, method, {"platform": platform})
        elif method == "doctor":
            self._handle_doctor(request_id, params)
        elif method == "settings_snapshot":
            self._emit_done(request_id, method, self._settings_snapshot())
        elif method == "settings_schema":
            self._emit_done(request_id, method, self._settings_schema())
        elif method == "settings_update":
            self._emit_done(request_id, method, self._settings_update(params))
        elif method == "login_status":
            self._emit_done(request_id, method, self._login_status(params))
        elif method == "import_login_sessions":
            self._emit_done(request_id, method, self._import_login_sessions(params))
        elif method == "output_list":
            self._emit_done(request_id, method, self._output_list())
        else:
            self._emit_error(request_id, "unknown_method", f"未知请求方法：{method}")

    def _start_fetch_job(self, request_id: str, params: dict[str, Any]) -> None:
        if not request_id:
            self._emit_error(None, "invalid_request", "抓取请求缺少 id")
            return
        if request_id in self._tasks:
            self._emit_error(request_id, "duplicate_job", "任务 id 已存在")
            return

        urls = params.get("urls")
        if isinstance(urls, str):
            urls = [urls]
        if urls is None:
            urls = []
        if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
            self._emit_error(request_id, "invalid_params", "抓取参数 urls 必须是字符串列表")
            return

        output_dir = params.get("output_dir") or params.get("outputDirectory") or ""
        targets = params.get("targets")
        if isinstance(targets, str):
            targets = [targets]
        if targets is not None:
            if not isinstance(targets, list) or not all(isinstance(target, str) for target in targets):
                self._emit_error(request_id, "invalid_params", "抓取参数 targets 必须是字符串列表")
                return
        options = params.get("options") or {}
        if not isinstance(options, dict):
            self._emit_error(request_id, "invalid_params", "抓取参数 options 必须是对象")
            return
        if targets and not urls:
            platform = str(params.get("platform") or "").strip().lower()
            mode = str(params.get("mode") or "").strip().lower()
            task = asyncio.create_task(
                self._run_structured_fetch_job(request_id, targets, platform, mode, str(output_dir), options)
            )
        else:
            task = asyncio.create_task(self._run_fetch_job(request_id, urls, str(output_dir)))
        self._tasks[request_id] = task

    async def _run_fetch_job(self, request_id: str, urls: list[str], output_dir: str = "") -> None:
        fetched = 0
        errors = 0
        last_error = ""
        try:
            async with self._fetch_lock:
                with _temporary_output_environment(output_dir):
                    self._emit(
                        {
                            "id": request_id,
                            "event": "job_started",
                            "method": "fetch",
                            "result": {"total": len(urls)},
                        }
                    )
                    self._emit(
                        {
                            "id": request_id,
                            "event": "log",
                            "method": "fetch",
                            "level": "info",
                            "message": "抓取任务已启动",
                        }
                    )
                    for index, url in enumerate(urls, start=1):
                        self._emit(
                            {
                                "id": request_id,
                                "event": "progress",
                                "method": "fetch",
                                "stage": "fetch",
                                "message": "正在抓取",
                                "url": url,
                                "result": {"index": index, "total": len(urls)},
                            }
                        )
                        try:
                            result = await _fetch_url_with_timeout(self.fetch_service, url)
                        except asyncio.CancelledError:
                            raise
                        except asyncio.TimeoutError:
                            errors += 1
                            timeout = _worker_fetch_timeout()
                            timeout_text = _format_timeout(timeout)
                            last_error = f"抓取超时（{timeout_text}）：{url}"
                            self._emit_error(
                                request_id,
                                "fetch_timeout",
                                last_error,
                                details={"url": url, "timeout_seconds": timeout},
                                url=url,
                                method="fetch",
                            )
                            continue
                        except Exception as exc:
                            errors += 1
                            last_error = str(exc)
                            self._emit_exception(request_id, exc, url=url, method="fetch")
                            continue

                        fetched += 1
                        for artifact in getattr(result, "artifacts", []) or []:
                            artifact_payload = _to_payload(artifact)
                            self._emit(
                                {
                                    "id": request_id,
                                    "event": "artifact",
                                    "method": "fetch",
                                    "url": url,
                                    "artifact": artifact_payload,
                                }
                            )

            result = {"fetched": fetched, "errors": errors}
            if last_error:
                result["error"] = last_error
            self._emit_done(request_id, "fetch", result)
        except asyncio.CancelledError:
            self._emit(
                {
                    "id": request_id,
                    "event": "cancelled",
                    "method": "fetch",
                    "result": {"cancelled": True},
                }
            )
            raise
        finally:
            self._tasks.pop(request_id, None)

    async def _run_structured_fetch_job(
        self,
        request_id: str,
        targets: list[str],
        platform: str,
        mode: str,
        output_dir: str = "",
        options: dict[str, Any] | None = None,
    ) -> None:
        command_preview = ""
        emitted_artifacts: set[str] = set()

        def emit_stream_line(line: str) -> None:
            self._emit(
                {
                    "id": request_id,
                    "event": "log",
                    "method": "fetch",
                    "level": _log_level_for_line(line),
                    "message": line,
                }
            )
            for artifact_path in _extract_artifact_paths_from_command_output(line):
                if artifact_path in emitted_artifacts:
                    continue
                emitted_artifacts.add(artifact_path)
                self._emit_structured_artifact(request_id, artifact_path)

        try:
            command = _structured_command(platform, mode)
            command_args = _structured_command_args(command, targets, options or {})
            command_preview = _command_preview(command, command_args)
            if self._fetch_lock.locked():
                self._emit(
                    {
                        "id": request_id,
                        "event": "log",
                        "method": "fetch",
                        "level": "info",
                        "message": f"任务已排队，等待前一个抓取任务完成：{command_preview}",
                    }
                )
            async with self._fetch_lock:
                with _temporary_output_environment(output_dir):
                    self._emit(
                        {
                            "id": request_id,
                            "event": "job_started",
                            "method": "fetch",
                            "result": {
                                "total": 1,
                                "platform": platform,
                                "mode": mode,
                                "command": command_preview,
                            },
                        }
                    )
                    self._emit(
                        {
                            "id": request_id,
                            "event": "log",
                            "method": "fetch",
                            "level": "info",
                            "message": f"正在执行：{command_preview}",
                        }
                    )
                    stdout_text, stderr_text = await asyncio.to_thread(
                        self._invoke_command_runner,
                        command,
                        command_args,
                        emit_stream_line,
                    )
                    log_lines = _split_log_lines(stdout_text, stderr_text)
                    artifact_paths = _extract_artifact_paths_from_command_output(stdout_text, stderr_text)
                    for artifact_path in artifact_paths:
                        if artifact_path in emitted_artifacts:
                            continue
                        emitted_artifacts.add(artifact_path)
                        self._emit_structured_artifact(request_id, artifact_path)
                    no_output_error = _structured_no_output_error(command, log_lines, artifact_paths)
                    if no_output_error:
                        self._emit_error(
                            request_id,
                            "structured_no_output",
                            no_output_error,
                            recoverable=True,
                        )
                        self._emit_done(
                            request_id,
                            "fetch",
                            {"fetched": 0, "errors": 1, "command": command_preview, "error": no_output_error},
                        )
                        return
            self._emit_done(request_id, "fetch", {"fetched": 1, "errors": 0, "command": command_preview})
        except asyncio.CancelledError:
            self._emit(
                {
                    "id": request_id,
                    "event": "cancelled",
                    "method": "fetch",
                    "result": {"cancelled": True},
                }
            )
            raise
        except StructuredCommandError as exc:
            for line in _split_log_lines(exc.stdout_text, exc.stderr_text):
                for artifact_path in _extract_artifact_paths_from_command_output(line):
                    if artifact_path in emitted_artifacts:
                        continue
                    emitted_artifacts.add(artifact_path)
                    self._emit_structured_artifact(request_id, artifact_path)
            self._emit_exception(request_id, exc)
            self._emit_done(
                request_id,
                "fetch",
                {"fetched": 0, "errors": 1, "command": command_preview, "error": str(exc)},
            )
        except Exception as exc:
            self._emit_exception(request_id, exc)
            self._emit_done(
                request_id,
                "fetch",
                {"fetched": 0, "errors": 1, "command": command_preview, "error": str(exc)},
            )
        finally:
            self._tasks.pop(request_id, None)

    def _invoke_command_runner(self, command: str, args: list[str], on_line=None) -> tuple[str, str]:
        stdout = _StreamingCaptureTextIO(on_line) if on_line else _CaptureTextIO()
        stderr = _StreamingCaptureTextIO(on_line) if on_line else _CaptureTextIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            _configure_command_logging(stderr)
            try:
                self.command_runner(command, list(args))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                if code != 0:
                    message = _last_error_line(stdout.getvalue(), stderr.getvalue()) or (
                        f"命令执行失败（退出码 {code}）：{command}"
                    )
                    raise StructuredCommandError(
                        message,
                        stdout_text=stdout.getvalue(),
                        stderr_text=stderr.getvalue(),
                    ) from exc
            except Exception as exc:
                raise StructuredCommandError(
                    str(exc),
                    stdout_text=stdout.getvalue(),
                    stderr_text=stderr.getvalue(),
                ) from exc
            finally:
                if isinstance(stdout, _StreamingCaptureTextIO):
                    stdout.flush_pending()
                if isinstance(stderr, _StreamingCaptureTextIO):
                    stderr.flush_pending()
                _configure_worker_logging()
        return stdout.getvalue(), stderr.getvalue()

    async def _cancel_job(self, request_id: str | None, params: dict[str, Any]) -> None:
        target_id = str(params.get("id") or params.get("job_id") or "")
        task = self._tasks.get(target_id)
        if task is None:
            self._emit_done(
                request_id,
                "cancel",
                {"cancelled": False, "target_id": target_id, "reason": "not_found"},
            )
            return

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._emit_done(request_id, "cancel", {"cancelled": True, "target_id": target_id})

    def _handle_doctor(self, request_id: str | None, params: dict[str, Any]) -> None:
        modules = params.get("modules") or ["feedgrab"]
        if isinstance(modules, str):
            modules = [modules]
        summary = getattr(self.doctor_service, "summary", None)
        if callable(summary):
            payload = _to_payload(summary(optional_modules=[str(module) for module in modules]))
            for diagnostic in payload.get("checks", []):
                self._emit(
                    {
                        "id": request_id,
                        "event": "diagnostic",
                        "method": "doctor",
                        "diagnostic": diagnostic,
                    }
                )
            self._emit_done(request_id, "doctor", payload)
            return

        diagnostics = []
        for module_name in modules:
            diagnostic = self.doctor_service.check_import(str(module_name), str(module_name))
            payload = _to_payload(diagnostic)
            diagnostics.append(payload)
            self._emit(
                {
                    "id": request_id,
                    "event": "diagnostic",
                    "method": "doctor",
                    "diagnostic": payload,
                }
            )
        self._emit_done(request_id, "doctor", {"diagnostics": diagnostics})

    def _settings_snapshot(self) -> dict[str, Any]:
        if self.settings_service is None:
            return {"available": False}
        snapshot = getattr(self.settings_service, "snapshot", None)
        if callable(snapshot):
            return _to_payload(snapshot())
        return {
            "data_dir": str(self.settings_service.data_dir()),
            "cookie_dir": str(self.settings_service.cookie_dir()),
            "session_dir": str(self.settings_service.session_dir()),
            "user_agent": self.settings_service.user_agent(),
        }

    def _settings_schema(self) -> dict[str, Any]:
        if self.settings_service is None:
            return {"available": False}
        schema = getattr(self.settings_service, "schema", None)
        if callable(schema):
            payload = _to_payload(schema())
            snapshot = self._settings_snapshot()
            item_values = {
                str(item.get("name")): item.get("value")
                for item in snapshot.get("items", [])
                if isinstance(item, dict) and item.get("name")
            }
            if item_values.get("user_agent") and not item_values.get("BROWSER_USER_AGENT"):
                item_values["BROWSER_USER_AGENT"] = item_values["user_agent"]
            for platform in payload.get("platforms", []):
                if not isinstance(platform, dict):
                    continue
                for field in platform.get("fields", []):
                    if not isinstance(field, dict):
                        continue
                    name = str(field.get("name") or "")
                    if name in item_values:
                        field["value"] = _coerce_settings_schema_value(field, item_values[name])
                    if "value" in field:
                        field["value"] = _coerce_settings_schema_value(field, field.get("value"))
                    if "default" in field:
                        field["default"] = _coerce_settings_schema_value(field, field.get("default"))
                    if "defaultValue" in field:
                        field["defaultValue"] = _coerce_settings_schema_value(field, field.get("defaultValue"))
            return payload
        return {"available": False}

    def _settings_update(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.settings_service is None:
            return {"available": False}
        values = params.get("values")
        if values is None:
            values = params.get("settings")
        if not isinstance(values, dict):
            raise ValueError("设置更新参数 values 必须是对象")
        update = getattr(self.settings_service, "update", None)
        if callable(update):
            payload = _to_payload(update(values))
            self._refresh_path_dependent_services(values, payload)
            return payload
        return {"available": False}

    def _refresh_path_dependent_services(self, values: dict[str, Any], payload: dict[str, Any]) -> None:
        updated_names = {
            str(item.get("name"))
            for item in payload.get("updated", [])
            if isinstance(item, dict) and item.get("name")
        }
        if not updated_names:
            updated_names = {str(name) for name in values}
        if "FEEDGRAB_DATA_DIR" in updated_names and isinstance(self.login_service, LoginService):
            self.login_service = LoginService()
        if "OUTPUT_DIR" in updated_names and isinstance(self.output_service, OutputService):
            self.output_service = OutputService()

    def _login_status(self, params: dict[str, Any]) -> dict[str, Any]:
        platforms = params.get("platforms") or []
        if isinstance(platforms, str):
            platforms = [platforms]
        live = bool(params.get("live") or params.get("validate") or params.get("validate_live"))
        if live:
            return {"platforms": [_to_payload(self.login_service.status(str(platform), live=True)) for platform in platforms]}
        return {"platforms": [_to_payload(self.login_service.status(str(platform))) for platform in platforms]}

    def _import_login_sessions(self, params: dict[str, Any]) -> dict[str, Any]:
        source_dir = params.get("source_dir") or params.get("installer_session_dir") or ""
        if not isinstance(source_dir, str) or not source_dir:
            raise ValueError("导入登录态需要 source_dir 参数")
        overwrite = bool(params.get("overwrite", False))
        sync = bool(params.get("sync", False))
        import_sessions = getattr(self.login_service, "import_sessions", None)
        if not callable(import_sessions):
            return {"available": False}
        platform = params.get("platform")
        if isinstance(platform, str) and platform:
            return _to_payload(import_sessions(source_dir, overwrite=overwrite, platform=platform, sync=sync))
        return _to_payload(import_sessions(source_dir, overwrite=overwrite, sync=sync))

    def _output_list(self) -> dict[str, Any]:
        list_artifacts = getattr(self.output_service, "list_artifacts", None)
        if callable(list_artifacts):
            return {"items": [_to_payload(item) for item in list_artifacts()]}

        items = []
        list_inbox = getattr(self.fetch_service, "list_inbox", None)
        if callable(list_inbox):
            for item in list_inbox():
                items.append(_to_payload(item))
        return {"items": items}

    def _emit_done(self, request_id: str | None, method: str, result: dict[str, Any]) -> None:
        self._emit({"id": request_id, "event": "done", "method": method, "result": result})

    def _emit_error(
        self,
        request_id: str | None,
        code: str,
        message: str,
        *,
        recoverable: bool = True,
        details: dict[str, Any] | None = None,
        url: str = "",
        method: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "id": request_id,
            "event": "error",
            "error": {
                "code": code,
                "message": message,
                "recoverable": recoverable,
                "details": details or {},
            },
        }
        if method:
            event["method"] = method
        if url:
            event["url"] = url
        self._emit(event)

    def _emit_exception(self, request_id: str | None, exc: Exception, *, url: str = "", method: str | None = None) -> None:
        if isinstance(exc, ServiceError):
            payload = exc.to_dict()
            self._emit_error(
                request_id,
                payload["code"],
                payload["message"],
                recoverable=payload["recoverable"],
                details=payload["details"],
                url=url,
                method=method,
            )
            return

        self._emit_error(
            request_id,
            exc.__class__.__name__,
            str(exc),
            recoverable=True,
            details={},
            url=url,
            method=method,
        )

    def _emit_structured_artifact(self, request_id: str, artifact_path: str) -> None:
        suffix = os.path.splitext(artifact_path)[1].lower()
        kind = "markdown" if suffix == ".md" else suffix.lstrip(".") or "file"
        self._emit(
            {
                "id": request_id,
                "event": "artifact",
                "method": "fetch",
                "artifact": {"kind": kind, "path": artifact_path},
            }
        )

    def _emit(self, event: dict[str, Any]) -> None:
        payload = _json_safe(event)
        self.events.append(payload)
        if self.output is not None:
            self.output.write(json.dumps(payload, ensure_ascii=True) + "\n")
            self.output.flush()


def _to_payload(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    return value


def _coerce_settings_schema_value(field: dict[str, Any], value: Any) -> Any:
    field_type = str(field.get("type") or field.get("value_type") or "").lower()
    if field_type in {"boolean", "bool"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
        return bool(value)
    if field_type in {"integer", "int"}:
        if value == "":
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if field_type == "number":
        if value == "":
            return value
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return value
        return int(parsed) if parsed.is_integer() else parsed
    return value


def _structured_command(platform: str, mode: str) -> str:
    command = _STRUCTURED_COMMANDS.get((platform, mode))
    if command is None:
        raise ValueError(f"不支持的结构化抓取任务：platform={platform or 'unset'} mode={mode or 'unset'}")
    return command


def _structured_command_args(command: str, targets: list[str], options: dict[str, Any]) -> list[str]:
    args = list(targets)
    if command != "reddit-so":
        return args

    sort = _option_string(options, "sort")
    if sort:
        args.extend(["--sort", sort])
    time_range = _option_string(options, "time", "time_range", "timeRange")
    if time_range and sort not in {"hot", "new"}:
        args.extend(["--time", time_range])
    limit = options.get("limit")
    if limit not in (None, ""):
        args.extend(["--limit", str(limit)])
    subreddit = _option_string(options, "subreddit")
    if subreddit:
        args.extend(["--subreddit", subreddit])
    save_posts = options.get("save_posts", options.get("savePosts"))
    if save_posts is True or (isinstance(save_posts, str) and save_posts.lower() in {"1", "true", "yes", "on"}):
        args.append("--save-posts")
    return args


def _option_string(options: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = options.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _command_preview(command: str, args: list[str]) -> str:
    return "feedgrab " + " ".join([command, *[_quote_cli_arg(arg) for arg in args]])


def _quote_cli_arg(value: str) -> str:
    if not value:
        return '""'
    if any(ch.isspace() or ch in {",", '"'} for ch in value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


async def _fetch_url_with_timeout(fetch_service: Any, url: str) -> Any:
    timeout = _worker_fetch_timeout()
    fetch = fetch_service.fetch_url(url)
    if timeout is None:
        return await fetch
    return await asyncio.wait_for(fetch, timeout=timeout)


def _worker_fetch_timeout() -> float | None:
    raw = os.getenv("FEEDGRAB_WORKER_FETCH_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_FETCH_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_FETCH_TIMEOUT_SECONDS
    return None if timeout <= 0 else timeout


def _format_timeout(timeout: float | None) -> str:
    if timeout is None:
        return "未启用超时限制"
    if timeout.is_integer():
        return f"{int(timeout)}s"
    return f"{timeout:g}s"


@contextlib.contextmanager
def _temporary_output_environment(output_dir: str):
    if not output_dir:
        yield
        return

    keys = ("OUTPUT_DIR", "OBSIDIAN_VAULT")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        os.environ["OUTPUT_DIR"] = output_dir
        os.environ["OBSIDIAN_VAULT"] = ""
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _split_log_lines(*chunks: str) -> list[str]:
    lines: list[str] = []
    for chunk in chunks:
        for line in str(chunk or "").splitlines():
            item = line.strip()
            if item:
                lines.append(item)
    return lines


def _extract_artifact_paths_from_command_output(*chunks: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for line in _split_log_lines(*chunks):
        match = _ARTIFACT_PATH_LINE_RE.search(line)
        if not match:
            continue
        path = match.group("path").strip().strip("'\"")
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _structured_no_output_error(command: str, log_lines: list[str], artifact_paths: list[str]) -> str:
    if command != "mpweixin-id" or artifact_paths:
        return ""
    if any(_ZERO_ACCOUNT_OUTPUT_RE.search(line) for line in log_lines):
        return (
            "微信公众号账号批量未生成任何文章，请检查公众号名称、MP 后台登录态，"
            "或 MPWEIXIN_ID_SINCE 日期过滤。"
        )
    return ""


def _last_error_line(*chunks: str) -> str:
    for line in reversed(_split_log_lines(*chunks)):
        if line.startswith("❌"):
            return line.lstrip("❌ ").strip()
    lines = _split_log_lines(*chunks)
    return lines[-1] if lines else ""


def _log_level_for_line(line: str) -> str:
    lowered = line.lower()
    if line.startswith("❌") or " error " in f" {lowered} " or lowered.startswith("error"):
        return "error"
    if " warning " in f" {lowered} " or lowered.startswith("warning") or "⚠" in line:
        return "warning"
    if line.startswith("✅") or "saved to markdown" in lowered:
        return "success"
    return "info"


def _default_command_runner(command: str, args: list[str]) -> None:
    from feedgrab import cli

    if command == "x-so":
        cli.cmd_twitter_search(args)
        return
    if command == "xhs-so":
        cli.cmd_xhs_search(args)
        return
    if command == "ytb-so":
        cli.cmd_youtube_search(args)
        return
    if command == "zhihu-so":
        cli.cmd_zhihu_search(args)
        return
    if command == "reddit-so":
        cli.cmd_reddit_search(args)
        return
    if command == "mpweixin-id":
        if not args:
            raise ValueError("mpweixin-id 需要公众号名称")
        cli.cmd_mpweixin_account(args[0])
        return
    if command == "mpweixin-so":
        if not args:
            raise ValueError("mpweixin-so 需要关键词")
        cli.cmd_wechat_search(args[0])
        return
    raise ValueError(f"不支持的命令：{command}")


def _json_safe(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_key(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _json_safe(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, str):
        return redact_value(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict(), key=key)
    return str(value)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


async def _run_stdio(stdin: TextIO, stdout: TextIO) -> None:
    os.environ["FEEDGRAB_WORKER_MODE"] = "true"
    _configure_worker_logging()
    worker = SidecarWorker(output=stdout)
    await worker.start()
    while True:
        line = await asyncio.to_thread(stdin.readline)
        if not line:
            break
        await worker.handle_line(line)
    await worker.wait_idle()
    await asyncio.sleep(2)


def main() -> None:
    _configure_stdio_utf8()
    if len(sys.argv) >= 3 and sys.argv[1] == "login":
        _configure_worker_logging()
        LoginService().login(sys.argv[2], headless=False)
        return
    asyncio.run(_run_stdio(sys.stdin, sys.stdout))


def _configure_stdio_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _configure_worker_logging() -> None:
    logger.remove()
    logger.add(_redacted_log_sink, level="INFO")


def _configure_command_logging(sink) -> None:
    logger.remove()
    logger.add(sink, level=os.getenv("LOG_LEVEL", "INFO"))


def _redacted_log_sink(message) -> None:
    sys.stderr.write(str(redact_value(str(message))))


if __name__ == "__main__":
    main()
