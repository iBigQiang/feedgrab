# -*- coding: utf-8 -*-
"""Sidecar worker JSON Lines protocol tests."""

from __future__ import annotations

import asyncio
import io
import json
import os

from feedgrab.service.models import Artifact, FetchRequest, FetchResult, ServiceError


def _request(request_id: str, method: str, params: dict | None = None) -> str:
    return json.dumps({"id": request_id, "method": method, "params": params or {}})


async def _run_lines(worker, lines: list[str]) -> list[dict]:
    await worker.start()
    for line in lines:
        await worker.handle_line(line)
    await worker.wait_idle()
    return worker.events


def test_worker_emits_ready_and_handles_ping():
    from feedgrab.worker import SidecarWorker

    events = asyncio.run(_run_lines(SidecarWorker(fetch_service=FakeFetchService()), [
        _request("req_ping", "ping"),
    ]))

    assert events[0]["event"] == "ready"
    assert events[-1] == {
        "id": "req_ping",
        "event": "done",
        "method": "ping",
        "result": {"pong": True},
    }


def test_worker_detects_platform_through_fetch_service():
    from feedgrab.worker import SidecarWorker

    events = asyncio.run(_run_lines(SidecarWorker(fetch_service=FakeFetchService()), [
        _request("req_detect", "detect_platform", {"url": "https://github.com/iBigQiang/feedgrab"}),
    ]))

    assert events[-1]["event"] == "done"
    assert events[-1]["result"] == {"platform": "github"}


def test_worker_fetch_emits_job_progress_artifacts_and_done_without_real_network():
    from feedgrab.worker import SidecarWorker

    events = asyncio.run(_run_lines(SidecarWorker(fetch_service=FakeFetchService()), [
        _request("job_1", "fetch", {"urls": ["https://example.test/a", "https://example.test/b"]}),
    ]))

    names = [event["event"] for event in events]
    assert names == [
        "ready",
        "job_started",
        "log",
        "progress",
        "artifact",
        "progress",
        "artifact",
        "done",
    ]
    assert events[1]["id"] == "job_1"
    assert events[1]["result"]["total"] == 2
    assert events[3]["url"] == "https://example.test/a"
    assert events[4]["artifact"]["path"].endswith("a.md")
    assert events[-1]["result"]["fetched"] == 2


def test_worker_fetch_emits_log_event():
    from feedgrab.worker import SidecarWorker

    events = asyncio.run(_run_lines(SidecarWorker(fetch_service=FakeFetchService()), [
        _request("job_log", "fetch", {"urls": ["https://example.test/a"]}),
    ]))

    log = next(event for event in events if event["event"] == "log")
    assert log["id"] == "job_log"
    assert log["level"] == "info"


def test_worker_fetch_applies_output_dir_only_during_job(monkeypatch):
    from feedgrab.worker import SidecarWorker

    monkeypatch.setenv("OUTPUT_DIR", "D:/existing")
    service = OutputDirCaptureFetchService()

    events = asyncio.run(_run_lines(SidecarWorker(fetch_service=service), [
        _request("job_output", "fetch", {"urls": ["https://example.test/a"], "output_dir": "D:/gui-output"}),
    ]))

    assert service.seen_output_dirs == ["D:/gui-output"]
    assert os.environ["OUTPUT_DIR"] == "D:/existing"
    assert events[-1]["result"] == {"fetched": 1, "errors": 0}


def test_worker_can_cancel_running_fetch_job():
    from feedgrab.worker import SidecarWorker

    async def scenario():
        service = BlockingFetchService()
        worker = SidecarWorker(fetch_service=service)
        await worker.start()
        await worker.handle_line(_request("job_long", "fetch", {"urls": ["https://example.test/slow"]}))
        await service.started.wait()
        await worker.handle_line(_request("cancel_req", "cancel", {"id": "job_long"}))
        await worker.wait_idle()
        return worker.events

    events = asyncio.run(scenario())

    assert any(event["event"] == "cancelled" and event["id"] == "job_long" for event in events)
    assert events[-1] == {
        "id": "cancel_req",
        "event": "done",
        "method": "cancel",
        "result": {"cancelled": True, "target_id": "job_long"},
    }


def test_worker_errors_are_json_safe_and_redacted():
    from feedgrab.worker import SidecarWorker

    events = asyncio.run(_run_lines(SidecarWorker(fetch_service=FailingFetchService()), [
        _request("job_secret", "fetch", {"urls": ["https://example.test/secret"], "api_key": "sk-test-secret"}),
    ]))

    error = next(event for event in events if event["event"] == "error")
    json.dumps(error)
    assert error["id"] == "job_secret"
    assert error["error"]["code"] == "fetch_error"
    assert error["error"]["details"]["api_key"] == "[redacted]"
    assert error["error"]["details"]["nested"]["cookie"] == "[redacted]"


def test_worker_progress_and_errors_redact_url_query_tokens():
    from feedgrab.worker import SidecarWorker

    events = asyncio.run(_run_lines(SidecarWorker(fetch_service=FailingFetchService()), [
        _request("job_url_secret", "fetch", {"urls": ["https://example.test/secret?token=url-secret&normal=1"]}),
    ]))

    rendered = json.dumps(events)
    assert "url-secret" not in rendered
    assert "token=%5Bredacted%5D" in rendered
    assert "normal=1" in rendered


def test_worker_stdout_json_is_ascii_safe_for_windows_codepages():
    from feedgrab.worker import SidecarWorker

    buffer = io.BytesIO()
    output = io.TextIOWrapper(buffer, encoding="gbk", errors="strict")
    worker = SidecarWorker(fetch_service=FakeFetchService(), output=output)

    worker._emit({"event": "artifact", "artifact": {"path": "D:/out/蜗牛King 👑.md"}})
    output.flush()

    raw = buffer.getvalue().decode("gbk")
    assert "\\u8717\\u725bKing" in raw
    assert "\\ud83d\\udc51" in raw


def test_worker_log_sink_redacts_token_strings(monkeypatch):
    import feedgrab.worker as worker_module

    written = []
    monkeypatch.setattr(worker_module.sys.stderr, "write", written.append)

    worker_module._redacted_log_sink("xsec_token=abc123 auth_token=def456 Bearer secret")

    rendered = "".join(written)
    assert "abc123" not in rendered
    assert "def456" not in rendered
    assert "secret" not in rendered
    assert "xsec_token=[redacted]" in rendered
    assert "auth_token=[redacted]" in rendered


def test_worker_returns_settings_login_output_and_diagnostics_from_services():
    from feedgrab.worker import SidecarWorker

    worker = SidecarWorker(
        fetch_service=FakeFetchService(),
        doctor_service=FakeDoctorService(),
        settings_service=FakeSettingsService(),
        login_service=FakeLoginService(),
        output_service=FakeOutputService(),
    )

    events = asyncio.run(
        _run_lines(
            worker,
            [
                _request("settings", "settings_snapshot"),
                _request("login", "login_status", {"platforms": ["twitter", "github"]}),
                _request("outputs", "output_list"),
                _request("doctor", "doctor"),
            ],
        )
    )

    done = {event["id"]: event for event in events if event.get("event") == "done"}
    assert done["settings"]["result"]["items"][0]["name"] == "OUTPUT_DIR"
    assert done["login"]["result"]["platforms"][0]["platform"] == "twitter"
    assert done["outputs"]["result"]["items"][0]["path"].endswith("item.md")
    assert done["doctor"]["result"]["status"] == "ok"
    assert any(event.get("event") == "diagnostic" for event in events)


class FakeFetchService:
    def detect_platform(self, url: str) -> str:
        if "github.com" in url:
            return "github"
        return "generic"

    async def fetch_url(self, url: str) -> FetchResult:
        slug = url.rstrip("/").split("/")[-1]
        return FetchResult(
            request=FetchRequest(url=url),
            artifacts=[Artifact(kind="markdown", path=f"D:/fake-output/{slug}.md")],
            platform=self.detect_platform(url),
        )

    def list_inbox(self):
        return []


class BlockingFetchService(FakeFetchService):
    def __init__(self):
        self.started = asyncio.Event()

    async def fetch_url(self, url: str) -> FetchResult:
        self.started.set()
        await asyncio.sleep(60)
        return await super().fetch_url(url)


class OutputDirCaptureFetchService(FakeFetchService):
    def __init__(self):
        self.seen_output_dirs = []

    async def fetch_url(self, url: str) -> FetchResult:
        self.seen_output_dirs.append(os.environ.get("OUTPUT_DIR"))
        return await super().fetch_url(url)


class FailingFetchService(FakeFetchService):
    async def fetch_url(self, url: str) -> FetchResult:
        raise ServiceError(
            "boom",
            code="fetch_error",
            details={
                "url": url,
                "api_key": "sk-test-secret",
                "nested": {"cookie": "sessionid=secret"},
            },
        )


class FakePayload:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return self.payload


class FakeSettingsService:
    def snapshot(self):
        return FakePayload(
            {
                "items": [
                    {
                        "name": "OUTPUT_DIR",
                        "value": "D:/out",
                        "type": "path",
                        "secret": False,
                    }
                ]
            }
        )


class FakeLoginService:
    def status(self, platform):
        return FakePayload(
            {
                "platform": platform,
                "has_session": platform == "twitter",
                "status": "ok" if platform == "twitter" else "missing",
            }
        )


class FakeOutputService:
    def list_artifacts(self, platform=None):
        return [
            FakePayload(
                {
                    "path": "D:/out/Web/item.md",
                    "exists": True,
                    "content_type": "text/markdown",
                }
            )
        ]


class FakeDoctorService:
    def summary(self, optional_modules=None):
        return FakePayload(
            {
                "status": "ok",
                "checks": [
                    {
                        "name": "python",
                        "status": "ok",
                        "message": "3.12",
                        "details": {},
                    }
                ],
            }
        )

    def check_import(self, module_name, label=""):
        return FakePayload(
            {
                "name": label or module_name,
                "status": "ok",
                "message": "available",
                "details": {},
            }
        )
