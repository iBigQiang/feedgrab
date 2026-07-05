# -*- coding: utf-8 -*-
"""Desktop-worker focused service-layer tests."""

import asyncio
import json
import os
from pathlib import Path

import pytest

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


def test_login_status_rejects_weak_wechat_qq_cookies(tmp_path):
    from feedgrab.service.login import LoginService

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "wechat.json").write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "uin", "value": "fake", "domain": ".qq.com", "expires": -1},
                    {"name": "rewardsn", "value": "fake", "domain": ".qq.com", "expires": -1},
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )

    status = LoginService(session_dir=session_dir).status("wechat").to_dict()

    assert status["status"] == "warning"
    assert status["valid_count"] == 0
    assert status["accounts"][0]["status"] == "invalid"
    assert "关键 Cookie" in status["accounts"][0]["message"]


def test_login_status_accepts_wechat_mp_backend_cookie_pair(tmp_path):
    from feedgrab.service.login import LoginService

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "wechat.json").write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "slave_sid", "value": "secret", "domain": ".qq.com", "expires": -1},
                    {"name": "data_bizuin", "value": "123", "domain": ".qq.com", "expires": -1},
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )

    status = LoginService(session_dir=session_dir).status("wechat").to_dict()

    assert status["status"] == "ok"
    assert status["valid_count"] == 1
    assert status["accounts"][0]["status"] == "valid"


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


def test_settings_resolves_installed_sessions_dir_for_legacy_relative_saved_value(monkeypatch, tmp_path):
    from feedgrab.service.settings import SettingsService

    install_sessions = tmp_path / "feedgrab Desktop" / "sessions"
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"values": {"FEEDGRAB_DATA_DIR": "\\sessions"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("FEEDGRAB_INSTALL_SESSIONS_DIR", str(install_sessions))
    monkeypatch.setenv("FEEDGRAB_DATA_DIR", str(tmp_path / "old-sessions"))

    snapshot = SettingsService(settings_path=settings_path).snapshot().to_dict()

    items = {item["name"]: item["value"] for item in snapshot["items"]}
    assert items["FEEDGRAB_DATA_DIR"] == str(install_sessions)
    assert items["session_dir"] == str(install_sessions)
    assert os.environ["FEEDGRAB_DATA_DIR"] == str(install_sessions)


def test_login_cdp_switch_is_global_basic_setting_not_platform_specific():
    from feedgrab.service.platform_settings import (
        LOGIN_CAPABILITIES,
        PLATFORM_SETTINGS_SCHEMA,
    )

    fields = PLATFORM_SETTINGS_SCHEMA.field_map()
    assert "CHROME_CDP_LOGIN" in fields
    field = fields["CHROME_CDP_LOGIN"]
    assert field.value_type == "boolean"
    assert field.default is False
    assert field.platform == "core"
    assert "Chrome CDP" in field.label

    required_platforms = {
        platform
        for platform, capability in LOGIN_CAPABILITIES.items()
        if capability.login_required and platform != "x"
    }

    for platform in sorted(required_platforms):
        assert f"{platform.upper()}_LOGIN_CDP_ENABLED" not in fields


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


def test_login_status_reports_platforms_that_do_not_require_login(tmp_path):
    from feedgrab.service.login import LoginService

    service = LoginService(session_dir=tmp_path)

    for platform in ("github", "web", "youtube", "bilibili"):
        payload = service.status(platform).to_dict()
        assert payload["platform"] == platform
        assert payload["has_session"] is False
        assert payload["status"] == "not_required"
        assert payload["login_required"] is False
        assert payload["capability"]["login"] == "not_required"


def test_login_status_finds_x_and_twitter_session_candidates(tmp_path):
    from feedgrab.service.login import LoginService

    x_session = tmp_path / "x_2.json"
    x_session.write_text('{"cookies":[{"name":"ct0","value":"secret"}],"origins":[]}', encoding="utf-8")
    twitter_session = tmp_path / "twitter_2.json"
    twitter_session.write_text('{"cookies":[{"name":"auth_token","value":"secret"}],"origins":[]}', encoding="utf-8")

    service = LoginService(session_dir=tmp_path)

    twitter_payload = service.status("twitter").to_dict()
    x_payload = service.status("x").to_dict()

    assert twitter_payload["has_session"] is True
    assert twitter_payload["session_path"].endswith("x_2.json")
    assert x_payload["has_session"] is True
    assert x_payload["session_path"].endswith("x_2.json")


def test_login_status_finds_reddit_session_candidate(tmp_path):
    from feedgrab.service.login import LoginService

    reddit_session = tmp_path / "reddit.json"
    reddit_session.write_text(
        '{"cookies":[{"name":"reddit_session","value":"secret"}],"origins":[]}',
        encoding="utf-8",
    )

    payload = LoginService(session_dir=tmp_path).status("reddit").to_dict()

    assert payload["platform"] == "reddit"
    assert payload["has_session"] is True
    assert payload["status"] == "ok"
    assert payload["session_path"].endswith("reddit.json")
    assert payload["account_count"] == 1
    assert payload["valid_count"] == 1
    assert "secret" not in repr(payload)


def test_login_status_rejects_reddit_visitor_token_without_session(tmp_path):
    from feedgrab.service.login import LoginService

    reddit_session = tmp_path / "reddit.json"
    reddit_session.write_text(
        '{"cookies":[{"name":"token_v2","value":"visitor","domain":".reddit.com"}],"origins":[]}',
        encoding="utf-8",
    )

    payload = LoginService(session_dir=tmp_path).status("reddit").to_dict()

    assert payload["platform"] == "reddit"
    assert payload["has_session"] is True
    assert payload["status"] == "warning"
    assert payload["account_count"] == 1
    assert payload["valid_count"] == 0
    assert payload["accounts"][0]["status"] == "invalid"


def test_login_status_reddit_live_validation_uses_api_me(monkeypatch, tmp_path):
    from feedgrab.service.login import LoginService

    reddit_session = tmp_path / "reddit.json"
    reddit_session.write_text(
        '{"cookies":[{"name":"reddit_session","value":"secret","domain":".reddit.com"}],"origins":[]}',
        encoding="utf-8",
    )

    def fake_validate(*, session_path=None, timeout=8):
        assert str(session_path).endswith("reddit.json")
        return {
            "status": "ok",
            "authenticated": True,
            "username": "testuser",
            "message": "Reddit session 可用",
        }

    monkeypatch.setattr("feedgrab.fetchers.reddit.validate_reddit_session", fake_validate)

    payload = LoginService(session_dir=tmp_path).status("reddit", live=True).to_dict()

    assert payload["status"] == "ok"
    assert payload["validation_mode"] == "api_me"
    assert payload["accounts"][0]["username"] == "testuser"
    assert "secret" not in repr(payload)


def test_login_service_login_projects_configured_session_dir(monkeypatch, tmp_path):
    from feedgrab.service.login import LoginService

    seen = {}

    def fake_login(platform, headless=False):
        seen["platform"] = platform
        seen["headless"] = headless
        seen["data_dir"] = os.environ.get("FEEDGRAB_DATA_DIR")

    monkeypatch.setattr("feedgrab.service.login.login", fake_login)
    monkeypatch.setenv("FEEDGRAB_DATA_DIR", str(tmp_path / "old-sessions"))

    LoginService(session_dir=tmp_path / "desktop-sessions").login("reddit")

    assert seen == {
        "platform": "reddit",
        "headless": False,
        "data_dir": str(tmp_path / "desktop-sessions"),
    }
    assert os.environ["FEEDGRAB_DATA_DIR"] == str(tmp_path / "old-sessions")


@pytest.mark.parametrize(
    ("platform", "base_name", "expected_name", "cookie_name"),
    [
        ("twitter", "twitter.json", "twitter_2.json", "auth_token"),
        ("xhs", "xhs.json", "xhs_2.json", "a1"),
        ("wechat", "wechat.json", "wechat_2.json", "slave_sid"),
        ("feishu", "feishu.json", "feishu_2.json", "session"),
        ("kdocs", "kdocs.json", "kdocs_2.json", "wps_sid"),
        ("flowus", "flowus.json", "flowus_2.json", "next_auth"),
        ("zhihu", "zhihu.json", "zhihu_2.json", "z_c0"),
        ("linuxdo", "linuxdo.json", "linuxdo_2.json", "_forum_session"),
        ("idcflare", "idcflare.json", "idcflare_2.json", "_forum_session"),
        ("zsxq", "zsxq.json", "zsxq_2.json", "zsxq_access_token"),
        ("reddit", "reddit.json", "reddit_2.json", "reddit_session"),
    ],
)
def test_login_service_login_uses_next_numbered_session_path(
    monkeypatch,
    tmp_path,
    platform,
    base_name,
    expected_name,
    cookie_name,
):
    from feedgrab.service.login import LoginService

    session_dir = tmp_path / "desktop-sessions"
    session_dir.mkdir()
    (session_dir / base_name).write_text(
        json.dumps({"cookies": [{"name": cookie_name, "value": "secret"}], "origins": []}),
        encoding="utf-8",
    )

    seen = {}

    def fake_login(platform, headless=False):
        seen["platform"] = platform
        seen["session_path"] = os.environ.get("FEEDGRAB_LOGIN_SESSION_PATH")

    monkeypatch.setattr("feedgrab.service.login.login", fake_login)

    LoginService(session_dir=session_dir).login(platform)

    assert seen == {
        "platform": platform,
        "session_path": str(session_dir / expected_name),
    }


def test_login_service_login_can_replace_empty_template_session(monkeypatch, tmp_path):
    from feedgrab.service.login import LoginService

    session_dir = tmp_path / "desktop-sessions"
    session_dir.mkdir()
    (session_dir / "reddit.json").write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    seen = {}

    def fake_login(platform, headless=False):
        seen["session_path"] = os.environ.get("FEEDGRAB_LOGIN_SESSION_PATH")

    monkeypatch.setattr("feedgrab.service.login.login", fake_login)

    LoginService(session_dir=session_dir).login("reddit")

    assert seen["session_path"] == str(session_dir / "reddit.json")


def test_login_service_status_rejects_xhs_visitor_cookie_without_web_session(tmp_path):
    from feedgrab.service.login import LoginService

    session_dir = tmp_path / "desktop-sessions"
    session_dir.mkdir()
    (session_dir / "xhs.json").write_text(
        json.dumps({"cookies": [{"name": "a1", "value": "device-cookie"}], "origins": []}),
        encoding="utf-8",
    )

    payload = LoginService(session_dir=session_dir).status("xhs").to_dict()

    assert payload["status"] == "warning"
    assert payload["valid_count"] == 0
    assert payload["accounts"][0]["status"] == "invalid"


def test_login_service_imports_installer_sessions_without_overwriting_existing(tmp_path):
    from feedgrab.service.login import LoginService

    target_dir = tmp_path / "main"
    installer_dir = tmp_path / "installer"
    target_dir.mkdir()
    installer_dir.mkdir()
    existing = target_dir / "twitter.json"
    existing.write_text('{"cookies":[{"name":"existing","value":"keep"}]}', encoding="utf-8")
    (installer_dir / "twitter.json").write_text('{"cookies":[{"name":"new","value":"skip"}]}', encoding="utf-8")
    (installer_dir / "x.json").write_text('{"cookies":[{"name":"ct0","value":"copy"}]}', encoding="utf-8")
    (installer_dir / "notes.txt").write_text("ignore me", encoding="utf-8")

    result = LoginService(session_dir=target_dir).import_sessions(installer_dir).to_dict()

    assert (target_dir / "x.json").exists()
    assert "existing" in existing.read_text(encoding="utf-8")
    assert result["imported"] == [{"source": str(installer_dir / "x.json"), "target": str(target_dir / "x.json")}]
    assert result["skipped"] == [
        {
            "source": str(installer_dir / "twitter.json"),
            "target": str(target_dir / "twitter.json"),
            "reason": "exists",
        }
    ]
    assert result["ignored"] == [{"source": str(installer_dir / "notes.txt"), "reason": "not_session_json"}]


def test_login_service_imports_only_requested_platform_sessions(tmp_path):
    from feedgrab.service.login import LoginService

    target_dir = tmp_path / "main"
    installer_dir = tmp_path / "installer"
    target_dir.mkdir()
    installer_dir.mkdir()
    (installer_dir / "x.json").write_text('{"cookies":[{"name":"ct0","value":"copy"}]}', encoding="utf-8")
    (installer_dir / "twitter_2.json").write_text(
        '{"cookies":[{"name":"auth_token","value":"copy"}]}',
        encoding="utf-8",
    )
    (installer_dir / "wechat.json").write_text(
        '{"cookies":[{"name":"wx","value":"ignore"}]}',
        encoding="utf-8",
    )

    result = LoginService(session_dir=target_dir).import_sessions(installer_dir, platform="twitter").to_dict()

    assert (target_dir / "x.json").exists()
    assert (target_dir / "twitter_2.json").exists()
    assert not (target_dir / "wechat.json").exists()
    assert [Path(row["target"]).name for row in result["imported"]] == ["twitter_2.json", "x.json"]
    assert result["ignored"] == [{"source": str(installer_dir / "wechat.json"), "reason": "platform_mismatch"}]


def test_login_service_sync_import_disables_target_sessions_missing_from_source(tmp_path):
    from feedgrab.service.login import LoginService

    target_dir = tmp_path / "main"
    installer_dir = tmp_path / "installer"
    target_dir.mkdir()
    installer_dir.mkdir()
    valid_session = '{"cookies":[{"name":"auth_token","value":"token"},{"name":"ct0","value":"csrf"}],"origins":[]}'
    for file_name in ("x.json", "x_2.json", "x_3.json"):
        (target_dir / file_name).write_text(valid_session, encoding="utf-8")
    for file_name in ("x.json", "x_2.json"):
        (installer_dir / file_name).write_text(valid_session, encoding="utf-8")

    service = LoginService(session_dir=target_dir)
    result = service.import_sessions(installer_dir, platform="twitter", sync=True).to_dict()
    payload = service.status("twitter").to_dict()

    assert not (target_dir / "x_3.json").exists()
    assert result["disabled"] == [
        {
            "source": str(target_dir / "x_3.json"),
            "target": str(target_dir / ".disabled" / "x_3.json"),
            "reason": "missing_from_source",
        }
    ]
    assert payload["account_count"] == 2
    assert payload["valid_count"] == 2
    assert payload["expired_count"] == 0


def test_login_service_ignores_blank_session_templates_without_disabling_existing(tmp_path):
    from feedgrab.service.login import LoginService

    target_dir = tmp_path / "main"
    template_dir = tmp_path / "templates"
    target_dir.mkdir()
    template_dir.mkdir()
    valid_session = '{"cookies":[{"name":"auth_token","value":"token"},{"name":"ct0","value":"csrf"}],"origins":[]}'
    (target_dir / "x_2.json").write_text(valid_session, encoding="utf-8")
    (template_dir / "x_2.json").write_text('{"auth_token":"","ct0":""}', encoding="utf-8")
    (template_dir / "xhs.json").write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    service = LoginService(session_dir=target_dir)
    result = service.import_sessions(template_dir, sync=True).to_dict()

    assert (target_dir / "x_2.json").exists()
    assert not (target_dir / ".disabled").exists()
    assert result["imported"] == []
    assert result["disabled"] == []
    assert result["ignored"] == [
        {"source": str(template_dir / "x_2.json"), "reason": "empty_template"},
        {"source": str(template_dir / "xhs.json"), "reason": "empty_template"},
    ]


def test_login_service_imports_filled_session_template_copy(tmp_path):
    from feedgrab.service.login import LoginService

    target_dir = tmp_path / "main"
    installer_dir = tmp_path / "installer"
    target_dir.mkdir()
    installer_dir.mkdir()
    (installer_dir / "x_2.json").write_text('{"auth_token":"token","ct0":"csrf"}', encoding="utf-8")

    result = LoginService(session_dir=target_dir).import_sessions(installer_dir, platform="twitter").to_dict()

    assert (target_dir / "x_2.json").exists()
    assert result["imported"] == [
        {"source": str(installer_dir / "x_2.json"), "target": str(target_dir / "x_2.json")}
    ]
    assert result["ignored"] == []


def test_settings_schema_update_persist_project_and_snapshot_redacts_secret(monkeypatch, tmp_path):
    from feedgrab.service.settings import SettingsService

    settings_path = tmp_path / "settings.json"
    monkeypatch.setenv("FEEDGRAB_SETTINGS_PATH", str(settings_path))
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("X_SEARCH_DAYS", raising=False)
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)

    service = SettingsService()
    schema = service.schema().to_dict()
    field_names = {field["name"] for platform in schema["platforms"] for field in platform["fields"]}
    core_fields = {
        field["name"]: field
        for platform in schema["platforms"]
        if platform["id"] == "core"
        for field in platform["fields"]
    }

    assert "FEISHU_APP_SECRET" in field_names
    assert "X_SEARCH_DAYS" in field_names
    assert "REDDIT_ENABLED" in field_names
    assert "REDDIT_MAX_COMMENTS" in field_names
    assert "REDDIT_SEARCH_SORT" in field_names
    assert "REDDIT_SEARCH_TIME_RANGE" in field_names
    assert core_fields["OBSIDIAN_VAULT"]["label"] == "Obsidian Vault"
    assert core_fields["OBSIDIAN_VAULT"]["description"] == "高优先级"

    result = service.update({"FEISHU_APP_SECRET": "super-secret", "X_SEARCH_DAYS": 7}).to_dict()

    assert result["settings_path"] == str(settings_path)
    assert os.environ["FEISHU_APP_SECRET"] == "super-secret"
    assert os.environ["X_SEARCH_DAYS"] == "7"
    assert '"FEISHU_APP_SECRET": "super-secret"' in settings_path.read_text(encoding="utf-8")

    snapshot = service.snapshot().to_dict()
    output_dir = next(item for item in snapshot["items"] if item["name"] == "OUTPUT_DIR")
    obsidian_vault = next(item for item in snapshot["items"] if item["name"] == "OBSIDIAN_VAULT")
    feishu_secret = next(item for item in snapshot["items"] if item["name"] == "FEISHU_APP_SECRET")
    x_days = next(item for item in snapshot["items"] if item["name"] == "X_SEARCH_DAYS")

    assert output_dir["value"] == "./output"
    assert obsidian_vault["value"] == ""
    assert obsidian_vault["source"] == "unset"
    assert feishu_secret["value"] == "[redacted]"
    assert x_days["value"] == "7"


def test_reddit_platform_settings_schema_includes_search_controls():
    from feedgrab.service.platform_settings import PLATFORM_SETTINGS_SCHEMA

    reddit_group = next(group for group in PLATFORM_SETTINGS_SCHEMA.platforms if group.id == "reddit")
    fields = {field.name: field for field in reddit_group.fields}

    assert fields["REDDIT_SEARCH_ENABLED"].value_type == "boolean"
    assert fields["REDDIT_SEARCH_SORT"].value_type == "select"
    assert [option["value"] for option in fields["REDDIT_SEARCH_SORT"].options] == [
        "relevance",
        "hot",
        "top",
        "new",
        "comments",
    ]
    assert fields["REDDIT_SEARCH_TIME_RANGE"].value_type == "select"
    assert [option["value"] for option in fields["REDDIT_SEARCH_TIME_RANGE"].options] == [
        "all",
        "year",
        "month",
        "week",
        "day",
        "hour",
    ]
    assert fields["REDDIT_SEARCH_LIMIT"].default == 10
    assert fields["REDDIT_SEARCH_SAVE_POSTS"].value_type == "boolean"
    assert fields["REDDIT_SEARCH_SUBREDDIT"].value_type == "string"


def test_desktop_settings_migrates_legacy_default_output_but_preserves_vault(monkeypatch, tmp_path):
    from feedgrab.service.settings import SettingsService

    settings_path = tmp_path / "settings.json"
    install_output = tmp_path / "install" / "output"
    settings_path.write_text(
        json.dumps(
            {
                "values": {
                    "OUTPUT_DIR": "E:\\Obsidian\\Qiang_Obsidian\\inbox",
                    "OBSIDIAN_VAULT": "E:/Obsidian/Qiang_Obsidian/inbox",
                    "X_SEARCH_DAYS": 9,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FEEDGRAB_WORKER_MODE", "true")
    monkeypatch.setenv("OUTPUT_DIR", str(install_output))
    monkeypatch.setenv("OBSIDIAN_VAULT", "")

    service = SettingsService(settings_path=settings_path)
    snapshot = service.snapshot().to_dict()
    output_dir = next(item for item in snapshot["items"] if item["name"] == "OUTPUT_DIR")
    obsidian_vault = next(item for item in snapshot["items"] if item["name"] == "OBSIDIAN_VAULT")
    x_days = next(item for item in snapshot["items"] if item["name"] == "X_SEARCH_DAYS")
    saved = json.loads(settings_path.read_text(encoding="utf-8"))["values"]

    assert output_dir["value"] == str(install_output)
    assert obsidian_vault["value"] == "E:/Obsidian/Qiang_Obsidian/inbox"
    assert x_days["value"] == "9"
    assert saved["OUTPUT_DIR"] == str(install_output)
    assert saved["OBSIDIAN_VAULT"] == "E:/Obsidian/Qiang_Obsidian/inbox"
    assert saved["X_SEARCH_DAYS"] == 9


def test_desktop_settings_migrates_empty_output_and_data_dir_to_install_defaults(monkeypatch, tmp_path):
    from feedgrab.service.settings import SettingsService

    settings_path = tmp_path / "settings.json"
    install_output = tmp_path / "feedgrab Desktop" / "output"
    install_sessions = tmp_path / "feedgrab Desktop" / "sessions"
    settings_path.write_text(
        json.dumps(
            {
                "values": {
                    "OUTPUT_DIR": "",
                    "FEEDGRAB_DATA_DIR": "",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FEEDGRAB_WORKER_MODE", "true")
    monkeypatch.setenv("OUTPUT_DIR", str(install_output))
    monkeypatch.setenv("FEEDGRAB_INSTALL_SESSIONS_DIR", str(install_sessions))
    monkeypatch.setenv("FEEDGRAB_DATA_DIR", str(install_sessions))

    service = SettingsService(settings_path=settings_path)
    snapshot = service.snapshot().to_dict()
    items = {item["name"]: item["value"] for item in snapshot["items"]}
    saved = json.loads(settings_path.read_text(encoding="utf-8"))["values"]

    assert items["OUTPUT_DIR"] == str(install_output)
    assert items["FEEDGRAB_DATA_DIR"] == str(install_sessions)
    assert items["session_dir"] == str(install_sessions)
    assert saved["OUTPUT_DIR"] == str(install_output)
    assert saved["FEEDGRAB_DATA_DIR"] == str(install_sessions)


def test_desktop_settings_keeps_user_custom_output_and_vault(monkeypatch, tmp_path):
    from feedgrab.service.settings import SettingsService

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "values": {
                    "OUTPUT_DIR": "D:\\WebSite\\yrgs.org",
                    "OBSIDIAN_VAULT": "D:\\Notes\\Vault",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FEEDGRAB_WORKER_MODE", "true")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "install" / "output"))
    monkeypatch.setenv("OBSIDIAN_VAULT", "")

    service = SettingsService(settings_path=settings_path)
    snapshot = service.snapshot().to_dict()
    output_dir = next(item for item in snapshot["items"] if item["name"] == "OUTPUT_DIR")
    obsidian_vault = next(item for item in snapshot["items"] if item["name"] == "OBSIDIAN_VAULT")
    saved = json.loads(settings_path.read_text(encoding="utf-8"))["values"]

    assert output_dir["value"] == "D:\\WebSite\\yrgs.org"
    assert obsidian_vault["value"] == "D:\\Notes\\Vault"
    assert saved["OUTPUT_DIR"] == "D:\\WebSite\\yrgs.org"
    assert saved["OBSIDIAN_VAULT"] == "D:\\Notes\\Vault"


def test_proxy_settings_project_standard_environment_and_redact_credentials(monkeypatch, tmp_path):
    from feedgrab.service.proxy import (
        get_playwright_proxy_options,
        get_requests_proxy_config,
        redact_proxy_url,
    )
    from feedgrab.service.settings import SettingsService

    settings_path = tmp_path / "settings.json"
    for key in (
        "FEEDGRAB_PROXY_ENABLED",
        "FEEDGRAB_PROXY_URL",
        "FEEDGRAB_NO_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.lower(), raising=False)
    monkeypatch.setenv("FEEDGRAB_SETTINGS_PATH", str(settings_path))

    proxy_url = "http://user:password@127.0.0.1:7890"
    service = SettingsService()
    result = service.update(
        {
            "FEEDGRAB_PROXY_ENABLED": True,
            "FEEDGRAB_PROXY_URL": proxy_url,
            "FEEDGRAB_NO_PROXY": "127.0.0.1,localhost",
        }
    ).to_dict()

    assert os.environ["HTTP_PROXY"] == proxy_url
    assert os.environ["HTTPS_PROXY"] == proxy_url
    assert os.environ["ALL_PROXY"] == proxy_url
    assert os.environ["NO_PROXY"] == "127.0.0.1,localhost"
    assert os.environ["http_proxy"] == proxy_url
    assert result["updated"][1]["value"] == "http://user:[redacted]@127.0.0.1:7890"
    assert redact_proxy_url(proxy_url) == "http://user:[redacted]@127.0.0.1:7890"
    assert get_requests_proxy_config() == {"http": proxy_url, "https": proxy_url}
    assert get_playwright_proxy_options() == {
        "server": "http://127.0.0.1:7890",
        "username": "user",
        "password": "password",
        "bypass": "127.0.0.1,localhost",
    }

    snapshot = SettingsService(settings_path=settings_path).snapshot().to_dict()
    proxy_item = next(item for item in snapshot["items"] if item["name"] == "FEEDGRAB_PROXY_URL")
    assert proxy_item["value"] == "http://user:[redacted]@127.0.0.1:7890"
    assert "password" not in repr(snapshot)


def test_doctor_summary_reports_proxy_disabled_without_network(monkeypatch, tmp_path):
    from feedgrab.service.doctor import DoctorService

    monkeypatch.delenv("FEEDGRAB_PROXY_ENABLED", raising=False)
    monkeypatch.delenv("FEEDGRAB_PROXY_URL", raising=False)

    payload = DoctorService(output_dir=tmp_path).summary().to_dict()
    checks = {item["name"]: item for item in payload["checks"]}

    assert checks["proxy_connectivity"]["status"] == "warning"
    assert checks["proxy_connectivity"]["message"] == "代理未启用"


def test_proxy_disable_preserves_existing_process_proxy(monkeypatch):
    from feedgrab.service.proxy import apply_proxy_environment

    inherited_proxy = "http://corporate-proxy.local:8080"
    monkeypatch.setenv("HTTP_PROXY", inherited_proxy)
    monkeypatch.setenv("HTTPS_PROXY", inherited_proxy)

    apply_proxy_environment(enabled=False, proxy_url="http://127.0.0.1:7890", no_proxy="127.0.0.1,localhost")

    assert os.environ["HTTP_PROXY"] == inherited_proxy
    assert os.environ["HTTPS_PROXY"] == inherited_proxy


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
