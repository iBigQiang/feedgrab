# -*- coding: utf-8 -*-
"""Sidecar worker JSON Lines protocol tests."""

from __future__ import annotations

import asyncio
import io
import json
import os
import threading

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
    assert "settings_schema" in events[0]["methods"]
    assert "settings_update" in events[0]["methods"]
    assert "import_login_sessions" in events[0]["methods"]
    assert events[-1] == {
        "id": "req_ping",
        "event": "done",
        "method": "ping",
        "result": {"pong": True},
    }


def test_worker_main_forces_stdio_utf8(monkeypatch):
    import feedgrab.worker as worker

    class FakeStream:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    stdin = FakeStream()
    stdout = FakeStream()
    stderr = FakeStream()
    monkeypatch.setattr(worker.sys, "argv", ["worker"])
    monkeypatch.setattr(worker.sys, "stdin", stdin)
    monkeypatch.setattr(worker.sys, "stdout", stdout)
    monkeypatch.setattr(worker.sys, "stderr", stderr)

    async def fake_run_stdio(input_stream, output_stream):
        assert input_stream is stdin
        assert output_stream is stdout

    monkeypatch.setattr(worker, "_run_stdio", fake_run_stdio)

    worker.main()

    assert stdin.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]


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
    monkeypatch.setenv("OBSIDIAN_VAULT", "D:/existing-vault")
    service = OutputDirCaptureFetchService()

    events = asyncio.run(_run_lines(SidecarWorker(fetch_service=service), [
        _request("job_output", "fetch", {"urls": ["https://example.test/a"], "output_dir": "D:/gui-output"}),
    ]))

    assert service.seen_output_dirs == [("D:/gui-output", "D:/gui-output")]
    assert os.environ["OUTPUT_DIR"] == "D:/existing"
    assert os.environ["OBSIDIAN_VAULT"] == "D:/existing-vault"
    assert events[-1]["result"] == {"fetched": 1, "errors": 0}


def test_worker_structured_search_task_maps_to_command_runner_without_shell(monkeypatch):
    import sys

    from feedgrab.worker import SidecarWorker

    calls = []

    def fake_command_runner(command, args):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        calls.append((command, args, os.environ.get("OUTPUT_DIR"), os.environ.get("OBSIDIAN_VAULT")))
        print("Summary: D:/gui-output/X/search/openclaw.md")
        print("CSV: D:/gui-output/X/search/openclaw.csv")

    events = asyncio.run(
        _run_lines(
            SidecarWorker(fetch_service=FakeFetchService(), command_runner=fake_command_runner),
            [
                _request(
                    "job_search",
                    "fetch",
                    {
                        "platform": "twitter",
                        "mode": "search",
                        "targets": ["claude code,openclaw"],
                        "output_dir": "D:/gui-output",
                    },
                )
            ],
        )
    )

    assert calls == [("x-so", ["claude code,openclaw"], "D:/gui-output", "D:/gui-output")]
    assert any(
        event.get("event") == "log"
        and event.get("id") == "job_search"
        and event.get("message") == "Summary: D:/gui-output/X/search/openclaw.md"
        for event in events
    )
    artifact_paths = [
        event.get("artifact", {}).get("path")
        for event in events
        if event.get("event") == "artifact"
    ]
    assert artifact_paths == [
        "D:/gui-output/X/search/openclaw.md",
        "D:/gui-output/X/search/openclaw.csv",
    ]
    assert events[-1]["result"] == {"fetched": 1, "errors": 0, "command": 'feedgrab x-so "claude code,openclaw"'}


def test_worker_structured_task_emits_queued_log_while_waiting_for_fetch_lock():
    from feedgrab.worker import SidecarWorker

    async def run_scenario():
        service = BlockingFetchService()

        def fake_command_runner(command, args):
            print("Summary: D:/gui-output/mpweixin/account/老码小张.md")

        worker = SidecarWorker(fetch_service=service, command_runner=fake_command_runner)
        await worker.start()
        await worker.handle_line(_request("job_blocking", "fetch", {"urls": ["https://example.test/block"]}))
        await asyncio.wait_for(service.started.wait(), timeout=1)
        await worker.handle_line(
            _request(
                "job_wechat",
                "fetch",
                {
                    "platform": "wechat",
                    "mode": "account",
                    "targets": ["老码小张"],
                    "output_dir": "D:/gui-output",
                },
            )
        )
        await asyncio.sleep(0.05)
        queued_logs = [
            event
            for event in worker.events
            if event.get("id") == "job_wechat"
            and event.get("event") == "log"
            and "等待前一个抓取任务完成" in event.get("message", "")
        ]
        service.release.set()
        await worker.wait_idle()
        return queued_logs

    assert asyncio.run(run_scenario())


def test_worker_structured_task_emits_executing_log_before_command_finishes():
    from feedgrab.worker import SidecarWorker

    def fake_command_runner(command, args):
        print("Summary: D:/gui-output/mpweixin/account/老码小张.md")

    events = asyncio.run(
        _run_lines(
            SidecarWorker(fetch_service=FakeFetchService(), command_runner=fake_command_runner),
            [
                _request(
                    "job_wechat",
                    "fetch",
                    {
                        "platform": "wechat",
                        "mode": "account",
                        "targets": ["老码小张"],
                        "output_dir": "D:/gui-output",
                    },
                )
            ],
        )
    )

    job_events = [event for event in events if event.get("id") == "job_wechat"]
    executing_index = next(
        index
        for index, event in enumerate(job_events)
        if event.get("event") == "log" and event.get("message") == "正在执行：feedgrab mpweixin-id 老码小张"
    )
    artifact_index = next(index for index, event in enumerate(job_events) if event.get("event") == "artifact")
    assert executing_index < artifact_index


def test_worker_structured_command_streams_logs_and_artifacts_before_command_finishes():
    from feedgrab.worker import SidecarWorker

    printed = threading.Event()
    release = threading.Event()

    def fake_command_runner(command, args):
        print("[mpweixin-id] [1] 第一篇文章")
        print("Saved to Markdown: D:/gui-output/mpweixin/account/老码小张/first.md")
        printed.set()
        release.wait(timeout=2)

    async def run_scenario():
        worker = SidecarWorker(fetch_service=FakeFetchService(), command_runner=fake_command_runner)
        await worker.start()
        await worker.handle_line(
            _request(
                "job_stream",
                "fetch",
                {
                    "platform": "wechat",
                    "mode": "account",
                    "targets": ["老码小张"],
                    "output_dir": "D:/gui-output",
                },
            )
        )
        saw_artifact_before_release = False
        for _ in range(40):
            await asyncio.sleep(0.025)
            if any(event.get("event") == "artifact" and event.get("id") == "job_stream" for event in worker.events):
                saw_artifact_before_release = True
                break
        running_before_release = "job_stream" in worker._tasks
        release.set()
        await worker.wait_idle()
        return worker.events, running_before_release, saw_artifact_before_release

    events, running_before_release, saw_artifact_before_release = asyncio.run(run_scenario())

    assert printed.is_set()
    assert running_before_release
    assert saw_artifact_before_release
    assert any(
        event.get("event") == "log"
        and event.get("id") == "job_stream"
        and event.get("message") == "[mpweixin-id] [1] 第一篇文章"
        for event in events
    )
    assert any(
        event.get("event") == "artifact"
        and event.get("id") == "job_stream"
        and event.get("artifact", {}).get("path") == "D:/gui-output/mpweixin/account/老码小张/first.md"
        for event in events
    )


def test_worker_structured_account_zero_output_is_failure(monkeypatch):
    from feedgrab.worker import SidecarWorker

    def fake_command_runner(command, args):
        print("✅ WeChat account fetch complete: '强子手记'")
        print("   Total: 0, Fetched: 0, Skipped: 0, Failed: 0")

    events = asyncio.run(
        _run_lines(
            SidecarWorker(fetch_service=FakeFetchService(), command_runner=fake_command_runner),
            [
                _request(
                    "job_account_empty",
                    "fetch",
                    {
                        "platform": "wechat",
                        "mode": "account",
                        "targets": ["强子手记"],
                        "output_dir": "D:/gui-output",
                    },
                )
            ],
        )
    )

    assert not any(event.get("event") == "artifact" for event in events)
    error = next(event for event in events if event.get("event") == "error")
    assert "未生成任何文章" in error["error"]["message"]
    assert events[-1]["result"]["errors"] == 1
    assert "未生成任何文章" in events[-1]["result"]["error"]


def test_worker_structured_command_failure_keeps_cli_output(monkeypatch):
    from feedgrab.worker import SidecarWorker

    def fake_command_runner(command, args):
        print("searching twitter")
        print("❌ [openclaw] missing Twitter login")
        raise SystemExit(1)

    events = asyncio.run(
        _run_lines(
            SidecarWorker(fetch_service=FakeFetchService(), command_runner=fake_command_runner),
            [
                _request(
                    "job_search_fail",
                    "fetch",
                    {
                        "platform": "twitter",
                        "mode": "search",
                        "targets": ["openclaw"],
                        "output_dir": "D:/gui-output",
                    },
                )
            ],
        )
    )

    assert any(
        event.get("event") == "log"
        and event.get("id") == "job_search_fail"
        and event.get("message") == "searching twitter"
        for event in events
    )
    assert any(
        event.get("event") == "log"
        and event.get("id") == "job_search_fail"
        and event.get("level") == "error"
        and event.get("message") == "❌ [openclaw] missing Twitter login"
        for event in events
    )
    error = next(event for event in events if event.get("event") == "error")
    assert error["error"]["message"] == "[openclaw] missing Twitter login"
    assert events[-1]["result"] == {
        "fetched": 0,
        "errors": 1,
        "command": "feedgrab x-so openclaw",
        "error": "[openclaw] missing Twitter login",
    }


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


def test_worker_handles_settings_schema_update_and_import_login_sessions():
    from feedgrab.worker import SidecarWorker

    settings_service = FakeSettingsService()
    login_service = FakeLoginService()
    worker = SidecarWorker(
        fetch_service=FakeFetchService(),
        settings_service=settings_service,
        login_service=login_service,
    )

    events = asyncio.run(
        _run_lines(
            worker,
            [
                _request("schema", "settings_schema"),
                _request("update", "settings_update", {"values": {"X_SEARCH_DAYS": 3}}),
                _request("import", "import_login_sessions", {"source_dir": "D:/installer/sessions", "sync": True}),
                _request("import_twitter", "import_login_sessions", {"source_dir": "D:/installer/sessions", "platform": "twitter", "sync": True}),
            ],
        )
    )

    done = {event["id"]: event for event in events if event.get("event") == "done"}
    assert done["schema"]["result"]["platforms"][0]["id"] == "x"
    schema_fields = done["schema"]["result"]["platforms"][0]["fields"]
    assert schema_fields[0]["value"] == 7
    assert schema_fields[1]["value"] == "Mozilla/5.0 Chrome/142.0.7444.265"
    assert schema_fields[2]["value"] is False
    assert done["update"]["result"]["updated"] == [{"name": "X_SEARCH_DAYS", "value": "3"}]
    assert done["import"]["result"]["imported"] == [
        {"source": "D:/installer/sessions/x.json", "target": "D:/main/sessions/x.json"}
    ]
    assert done["import_twitter"]["result"]["imported"] == [
        {"source": "D:/installer/sessions/x.json", "target": "D:/main/sessions/x.json"}
    ]
    assert settings_service.updated_values == {"X_SEARCH_DAYS": 3}
    assert login_service.imported_source_dir == "D:/installer/sessions"
    assert login_service.imported_platform == "twitter"
    assert login_service.imported_sync is True


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
        self.release = asyncio.Event()

    async def fetch_url(self, url: str) -> FetchResult:
        self.started.set()
        await self.release.wait()
        return await super().fetch_url(url)


class OutputDirCaptureFetchService(FakeFetchService):
    def __init__(self):
        self.seen_output_dirs = []

    async def fetch_url(self, url: str) -> FetchResult:
        self.seen_output_dirs.append((os.environ.get("OUTPUT_DIR"), os.environ.get("OBSIDIAN_VAULT")))
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
    def __init__(self):
        self.updated_values = None

    def snapshot(self):
        return FakePayload(
            {
                "items": [
                    {
                        "name": "OUTPUT_DIR",
                        "value": "D:/out",
                        "type": "path",
                        "secret": False,
                    },
                    {
                        "name": "user_agent",
                        "value": "Mozilla/5.0 Chrome/142.0.7444.265",
                        "type": "text",
                        "secret": False,
                    },
                    {
                        "name": "BROWSER_USER_AGENT",
                        "value": "",
                        "type": "text",
                        "secret": False,
                    },
                    {
                        "name": "X_SEARCH_DAYS",
                        "value": "7",
                        "type": "integer",
                        "secret": False,
                    },
                    {
                        "name": "FEEDGRAB_PROXY_ENABLED",
                        "value": "false",
                        "type": "boolean",
                        "secret": False,
                    },
                ]
            }
        )

    def schema(self):
        return FakePayload(
            {
                "platforms": [
                    {
                        "id": "x",
                        "fields": [
                            {"name": "X_SEARCH_DAYS", "type": "integer", "secret": False},
                            {"name": "BROWSER_USER_AGENT", "type": "string", "secret": False},
                            {"name": "FEEDGRAB_PROXY_ENABLED", "type": "boolean", "secret": False},
                        ],
                    }
                ]
            }
        )

    def update(self, values):
        self.updated_values = values
        return FakePayload({"updated": [{"name": "X_SEARCH_DAYS", "value": "3"}]})


class FakeLoginService:
    def __init__(self):
        self.imported_source_dir = None
        self.imported_platform = None
        self.imported_sync = None

    def status(self, platform):
        return FakePayload(
            {
                "platform": platform,
                "has_session": platform == "twitter",
                "status": "ok" if platform == "twitter" else "missing",
            }
        )

    def import_sessions(self, source_dir, *, overwrite=False, platform=None, sync=False):
        self.imported_source_dir = source_dir
        self.imported_platform = platform
        self.imported_sync = sync
        return FakePayload(
            {
                "source_dir": source_dir,
                "target_dir": "D:/main/sessions",
                "overwrite": overwrite,
                "imported": [
                    {"source": f"{source_dir}/x.json", "target": "D:/main/sessions/x.json"}
                ],
                "skipped": [],
                "disabled": [],
                "ignored": [],
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
