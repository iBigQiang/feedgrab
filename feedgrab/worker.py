# -*- coding: utf-8 -*-
"""JSON Lines sidecar worker for desktop clients."""

from __future__ import annotations

import asyncio
import json
import os
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
SUPPORTED_METHODS = (
    "ping",
    "detect_platform",
    "fetch",
    "cancel",
    "doctor",
    "settings_snapshot",
    "login_status",
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
        output: TextIO | None = None,
    ):
        self.fetch_service = fetch_service or FetchService()
        self.doctor_service = doctor_service or DoctorService()
        self.settings_service = settings_service or (SettingsService() if SettingsService else None)
        self.login_service = login_service or LoginService()
        self.output_service = output_service or OutputService()
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
            self._emit_error(request_id, "invalid_params", "params must be an object")
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
        elif method == "login_status":
            self._emit_done(request_id, method, self._login_status(params))
        elif method == "output_list":
            self._emit_done(request_id, method, self._output_list())
        else:
            self._emit_error(request_id, "unknown_method", f"unknown method: {method}")

    def _start_fetch_job(self, request_id: str, params: dict[str, Any]) -> None:
        if not request_id:
            self._emit_error(None, "invalid_request", "fetch request requires id")
            return
        if request_id in self._tasks:
            self._emit_error(request_id, "duplicate_job", "job id already exists")
            return

        urls = params.get("urls")
        if isinstance(urls, str):
            urls = [urls]
        if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
            self._emit_error(request_id, "invalid_params", "fetch params.urls must be a list of strings")
            return

        output_dir = params.get("output_dir") or params.get("outputDirectory") or ""
        task = asyncio.create_task(self._run_fetch_job(request_id, urls, str(output_dir)))
        self._tasks[request_id] = task

    async def _run_fetch_job(self, request_id: str, urls: list[str], output_dir: str = "") -> None:
        fetched = 0
        errors = 0
        try:
            async with self._fetch_lock:
                previous_output_dir = os.environ.get("OUTPUT_DIR")
                if output_dir:
                    os.environ["OUTPUT_DIR"] = output_dir
                try:
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
                            "message": "fetch job started",
                        }
                    )
                    for index, url in enumerate(urls, start=1):
                        self._emit(
                            {
                                "id": request_id,
                                "event": "progress",
                                "method": "fetch",
                                "stage": "fetch",
                                "message": "fetching",
                                "url": url,
                                "result": {"index": index, "total": len(urls)},
                            }
                        )
                        try:
                            result = await self.fetch_service.fetch_url(url)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            errors += 1
                            self._emit_exception(request_id, exc, url=url)
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
                finally:
                    if output_dir:
                        if previous_output_dir is None:
                            os.environ.pop("OUTPUT_DIR", None)
                        else:
                            os.environ["OUTPUT_DIR"] = previous_output_dir

            self._emit_done(request_id, "fetch", {"fetched": fetched, "errors": errors})
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

    def _login_status(self, params: dict[str, Any]) -> dict[str, Any]:
        platforms = params.get("platforms") or []
        if isinstance(platforms, str):
            platforms = [platforms]
        return {"platforms": [_to_payload(self.login_service.status(str(platform))) for platform in platforms]}

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
        if url:
            event["url"] = url
        self._emit(event)

    def _emit_exception(self, request_id: str | None, exc: Exception, *, url: str = "") -> None:
        if isinstance(exc, ServiceError):
            payload = exc.to_dict()
            self._emit_error(
                request_id,
                payload["code"],
                payload["message"],
                recoverable=payload["recoverable"],
                details=payload["details"],
                url=url,
            )
            return

        self._emit_error(
            request_id,
            exc.__class__.__name__,
            str(exc),
            recoverable=True,
            details={},
            url=url,
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
    asyncio.run(_run_stdio(sys.stdin, sys.stdout))


def _configure_worker_logging() -> None:
    logger.remove()
    logger.add(_redacted_log_sink, level="INFO")


def _redacted_log_sink(message) -> None:
    sys.stderr.write(str(redact_value(str(message))))


if __name__ == "__main__":
    main()
