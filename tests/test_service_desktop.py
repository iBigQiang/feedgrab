# -*- coding: utf-8 -*-
"""Desktop-worker focused service-layer tests."""

import asyncio
from pathlib import Path

from feedgrab.schema import SourceType, UnifiedContent


def _content(url: str = "https://example.com/ok") -> UnifiedContent:
    item = UnifiedContent(
        source_type=SourceType.WEB,
        source_name="example.com",
        title="OK",
        content="body",
        url=url,
    )
    setattr(item, "_feedgrab_saved_path", "D:/out/Web/ok.md")
    return item


def test_fetch_urls_returns_success_and_failure_items_without_skipping():
    from feedgrab.service.fetch import FetchService

    class FakeReader:
        def _detect_platform(self, url):
            return "web"

        async def read(self, url):
            if "bad" in url:
                raise RuntimeError("boom token=secret")
            return _content(url)

    results = asyncio.run(
        FetchService(reader=FakeReader()).fetch_urls(
            ["https://example.com/ok", "https://example.com/bad?token=secret"]
        )
    )

    assert len(results) == 2
    assert results[0].success is True
    assert results[0].content.title == "OK"
    assert results[1].success is False
    assert results[1].content is None
    assert results[1].error["code"] == "fetch_error"
    assert results[1].to_dict()["request"]["url"].endswith("bad?token=%5Bredacted%5D")


def test_service_redaction_handles_nested_lists_urls_headers_and_cdp_endpoints():
    from feedgrab.service.models import DiagnosticResult, ProgressEvent, ServiceError

    details = {
        "Authorization": "Bearer abc123",
        "url": "https://x.test/path?xsec_token=abc&normal=1",
        "items": [
            {"storage_state": {"cookies": [{"name": "auth_token", "value": "abc"}]}},
            "ws://127.0.0.1:9222/devtools/browser/secret-id",
        ],
    }

    service_error = ServiceError("failed token=abc123", details=details)
    err = service_error.to_dict()
    event = ProgressEvent(stage="fetch", message="Bearer abc123", details=details).to_dict()
    diagnostic = DiagnosticResult(
        name="cdp",
        status="warning",
        message="ws://127.0.0.1:9222/devtools/browser/secret-id",
        details=details,
    ).to_dict()
    cookie = err["details"]["items"][0]["storage_state"]["cookies"][0]

    rendered = repr([err, event, diagnostic])
    assert cookie["value"] == "[redacted]"
    assert "abc123" not in rendered
    assert "xsec_token=abc" not in rendered
    assert "abc123" not in str(service_error)
    assert "secret-id" not in rendered
    assert "auth_token" in rendered
    assert "[redacted]" in rendered
    assert "normal=1" in rendered


def test_job_service_runs_serial_jobs_and_records_artifacts_errors_history_and_cancel():
    from feedgrab.service.jobs import JobService
    from feedgrab.service.models import Artifact, FetchRequest, FetchResult, ServiceError

    order = []

    class FakeFetchService:
        async def fetch_urls(self, urls):
            order.append(list(urls))
            if "fail" in urls[0]:
                err = ServiceError("nope", code="fetch_error", details={"url": urls[0]})
                return [
                    FetchResult(
                        request=FetchRequest(url=urls[0]),
                        success=False,
                        error=err.to_dict(),
                        platform="web",
                    )
                ]
            return [
                FetchResult(
                    request=FetchRequest(url=urls[0]),
                    content=_content(urls[0]),
                    artifacts=[Artifact(kind="markdown", path="D:/out/Web/ok.md")],
                    platform="web",
                )
            ]

    async def run_jobs():
        service = JobService(fetch_service=FakeFetchService())
        first = service.enqueue_fetch(["https://example.com/ok"])
        second = service.enqueue_fetch(["https://example.com/fail"])
        cancelled = service.enqueue_fetch(["https://example.com/cancel"])
        assert service.cancel(cancelled.job_id) is True
        await service.run_until_idle()
        return service, first.job_id, second.job_id, cancelled.job_id

    service, first_id, second_id, cancelled_id = asyncio.run(run_jobs())

    assert order == [["https://example.com/ok"], ["https://example.com/fail"]]
    assert service.get_job(first_id).status == "succeeded"
    assert service.get_job(first_id).artifacts[0]["path"].endswith("ok.md")
    assert service.get_job(second_id).status == "failed"
    assert service.get_job(second_id).error["code"] == "fetch_error"
    assert service.get_job(cancelled_id).status == "cancelled"
    assert [job.job_id for job in service.history()] == [first_id, second_id, cancelled_id]
    assert any(event.stage == "job_done" for event in service.events(first_id))


def test_job_service_preserves_running_cancel_status():
    from feedgrab.service.jobs import JobService
    from feedgrab.service.models import Artifact, FetchRequest, FetchResult

    class SlowFetchService:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def fetch_urls(self, urls):
            self.started.set()
            await self.release.wait()
            return [
                FetchResult(
                    request=FetchRequest(url=urls[0]),
                    content=_content(urls[0]),
                    artifacts=[Artifact(kind="markdown", path="D:/out/Web/ok.md")],
                    platform="web",
                )
            ]

    async def run_job():
        fetch_service = SlowFetchService()
        service = JobService(fetch_service=fetch_service)
        job = service.enqueue_fetch(["https://example.com/slow"])
        runner = asyncio.create_task(service.run_until_idle())
        await fetch_service.started.wait()
        assert service.cancel(job.job_id) is True
        fetch_service.release.set()
        await runner
        return service.get_job(job.job_id)

    job = asyncio.run(run_job())

    assert job.status == "cancelled"
    assert job.artifacts == []


def test_job_service_exposes_retry_and_concurrency_limit():
    from feedgrab.service.jobs import JobService
    from feedgrab.service.models import Artifact, FetchRequest, FetchResult, ServiceError

    class FlakyFetchService:
        def __init__(self):
            self.calls = 0

        async def fetch_urls(self, urls):
            self.calls += 1
            if self.calls == 1:
                err = ServiceError("temporary", code="fetch_error", details={"url": urls[0]})
                return [
                    FetchResult(
                        request=FetchRequest(url=urls[0]),
                        success=False,
                        error=err.to_dict(),
                        platform="web",
                    )
                ]
            return [
                FetchResult(
                    request=FetchRequest(url=urls[0]),
                    content=_content(urls[0]),
                    artifacts=[Artifact(kind="markdown", path="D:/out/Web/retry.md")],
                    platform="web",
                )
            ]

    async def run_job():
        service = JobService(fetch_service=FlakyFetchService(), concurrency_limit=2)
        job = service.enqueue_fetch(["https://example.com/retry"], retry_limit=1)
        await service.run_until_idle()
        assert service.get_job(job.job_id).status == "failed"
        assert service.retry(job.job_id) is True
        await service.run_until_idle()
        return service, service.get_job(job.job_id)

    service, job = asyncio.run(run_job())

    assert service.concurrency_limit == 2
    assert job.status == "succeeded"
    assert job.attempts == 2
    assert job.retry_limit == 1
    assert job.artifacts[0]["path"].endswith("retry.md")


def test_settings_snapshot_schema_redacts_secret_fields(monkeypatch, tmp_path):
    from feedgrab.service.settings import SettingsService

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("FEEDGRAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BROWSER_USER_AGENT", "Agent/1.0")
    monkeypatch.setenv("GROQ_API_KEY", "sk-secret")

    snapshot = SettingsService().snapshot().to_dict()
    names = {item["name"] for item in snapshot["items"]}

    assert {"OUTPUT_DIR", "OBSIDIAN_VAULT", "FEEDGRAB_DATA_DIR", "session_dir", "cookie_dir", "user_agent"} <= names
    assert next(item for item in snapshot["items"] if item["name"] == "GROQ_API_KEY")["value"] == "[redacted]"


def test_doctor_summary_reports_python_imports_and_output_dir_writability(tmp_path):
    from feedgrab.service.doctor import DoctorService

    summary = DoctorService(output_dir=tmp_path).summary(
        optional_modules=["json", "module_that_should_not_exist_feedgrab"]
    )
    payload = summary.to_dict()
    checks = {item["name"]: item for item in payload["checks"]}

    assert payload["status"] == "warning"
    assert checks["python"]["status"] == "ok"
    assert checks["import:json"]["status"] == "ok"
    assert checks["import:module_that_should_not_exist_feedgrab"]["status"] == "warning"
    assert checks["output_dir"]["status"] == "ok"


def test_login_status_reports_session_metadata_without_cookie_values(tmp_path):
    from feedgrab.service.login import LoginService

    session = tmp_path / "twitter.json"
    session.write_text(
        '{"cookies":[{"name":"auth_token","value":"secret"}],"origins":[]}',
        encoding="utf-8",
    )

    payload = LoginService(session_dir=tmp_path).status("twitter").to_dict()

    assert payload["platform"] == "twitter"
    assert payload["has_session"] is True
    assert payload["cookie_count"] == 1
    assert "secret" not in repr(payload)


def test_output_service_lists_metadata_and_returns_open_plan_without_opening(tmp_path):
    from feedgrab.service.output import OutputService

    root = tmp_path / "out"
    platform_dir = root / "Web"
    platform_dir.mkdir(parents=True)
    artifact_path = platform_dir / "item.md"
    artifact_path.write_text("# item\n", encoding="utf-8")

    service = OutputService(output_dir=root)
    artifact = service.artifact_info(artifact_path).to_dict()
    listing = service.list_artifacts("Web")
    open_plan = service.open_path(artifact_path).to_dict()

    assert artifact["exists"] is True
    assert artifact["size"] > 0
    assert listing[0].path == str(artifact_path)
    assert open_plan["action"] == "open_path"
    assert open_plan["executed"] is False
    assert Path(open_plan["path"]) == artifact_path


def test_service_package_exports_desktop_services():
    import feedgrab.service as service

    assert service.JobService.__name__ == "JobService"
    assert service.SettingsService.__name__ == "SettingsService"
    assert service.DoctorService.__name__ == "DoctorService"
    assert service.LoginService.__name__ == "LoginService"
    assert service.OutputService.__name__ == "OutputService"
