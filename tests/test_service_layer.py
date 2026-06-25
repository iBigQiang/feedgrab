# -*- coding: utf-8 -*-
"""Service-layer contract tests."""

import asyncio
import importlib
import json
import sys
import types

import pytest

from feedgrab.schema import SourceType, UnifiedContent


def _sample_content() -> UnifiedContent:
    return UnifiedContent(
        source_type=SourceType.GITHUB,
        source_name="owner",
        title="Sample repo",
        content="Sample body",
        url="https://github.com/owner/repo",
    )


def test_service_models_are_json_ready():
    from feedgrab.service.models import (
        Artifact,
        DiagnosticResult,
        FetchRequest,
        FetchResult,
        ProgressEvent,
        ServiceError,
    )

    artifact = Artifact(kind="markdown", path="D:/out/GitHub/sample.md")
    request = FetchRequest(url="github.com/owner/repo")
    result = FetchResult(
        request=request,
        content=_sample_content(),
        artifacts=[artifact],
        platform="github",
    )
    progress = ProgressEvent(stage="fetch", message="started", url=request.url)
    diagnostic = DiagnosticResult(name="pytest", status="ok", message="available")

    assert artifact.to_dict()["content_type"] == "text/markdown"
    assert result.to_dict()["content"]["title"] == "Sample repo"
    assert result.to_dict()["artifacts"][0]["path"].endswith("sample.md")
    assert progress.to_dict()["stage"] == "fetch"
    assert diagnostic.to_dict()["status"] == "ok"

    err = ServiceError("fetch failed", code="fetch_error", recoverable=False)
    assert err.to_dict() == {
        "code": "fetch_error",
        "message": "fetch failed",
        "recoverable": False,
        "details": {},
    }


def test_fetch_service_wraps_reader_result_and_saved_artifact():
    from feedgrab.service.fetch import FetchService

    class FakeReader:
        def _detect_platform(self, url):
            return "github"

        async def read(self, url):
            content = _sample_content()
            setattr(content, "_feedgrab_saved_path", "D:/out/GitHub/sample.md")
            return content

    result = asyncio.run(
        FetchService(reader=FakeReader()).fetch_url("https://github.com/owner/repo")
    )

    assert result.content.title == "Sample repo"
    assert result.platform == "github"
    assert result.artifacts[0].kind == "markdown"
    assert result.artifacts[0].path == "D:/out/GitHub/sample.md"


def test_fetch_service_raises_service_error_with_context():
    from feedgrab.service.fetch import FetchService
    from feedgrab.service.models import ServiceError

    class FakeReader:
        def _detect_platform(self, url):
            return "generic"

        async def read(self, url):
            raise RuntimeError("network down")

    with pytest.raises(ServiceError) as exc:
        asyncio.run(FetchService(reader=FakeReader()).fetch_url("https://example.com"))

    assert exc.value.code == "fetch_error"
    assert exc.value.details["url"] == "https://example.com"
    assert "network down" in str(exc.value)


def test_cli_fetch_uses_service_and_keeps_single_url_output(monkeypatch, capsys):
    import feedgrab.cli as cli

    class FakeFetchService:
        async def fetch_url(self, url):
            from feedgrab.service.models import FetchRequest, FetchResult

            return FetchResult(
                request=FetchRequest(url=url),
                content=_sample_content(),
                platform="github",
            )

    monkeypatch.setattr(cli, "FetchService", FakeFetchService)

    cli.cmd_fetch(["https://github.com/owner/repo"])

    out = capsys.readouterr().out
    assert "[github] Sample repo" in out
    assert "https://github.com/owner/repo" in out
    assert "Sample body..." in out


def test_cli_fetch_reports_batch_failures_without_crashing(monkeypatch, capsys):
    import feedgrab.cli as cli

    class FakeFetchService:
        async def fetch_urls(self, urls):
            from feedgrab.service.models import FetchRequest, FetchResult, ServiceError

            return [
                FetchResult(
                    request=FetchRequest(url=urls[0]),
                    content=_sample_content(),
                    platform="github",
                ),
                FetchResult(
                    request=FetchRequest(url=urls[1]),
                    content=None,
                    platform="web",
                    success=False,
                    error=ServiceError(
                        "network down",
                        code="fetch_error",
                        details={"url": urls[1]},
                    ).to_dict(),
                ),
            ]

    monkeypatch.setattr(cli, "FetchService", FakeFetchService)

    cli.cmd_fetch(["https://github.com/owner/repo", "https://example.com/fail"])

    out = capsys.readouterr().out
    assert "[github] Sample repo" in out
    assert "Failed [web] https://example.com/fail: network down" in out
    assert "Fetched 1/2 URLs" in out


def test_mcp_read_url_uses_fetch_service(monkeypatch):
    _install_fake_mcp(monkeypatch)

    import mcp_server

    mcp_server = importlib.reload(mcp_server)

    class FakeFetchService:
        async def fetch_url(self, url):
            from feedgrab.service.models import FetchRequest, FetchResult

            return FetchResult(
                request=FetchRequest(url=url),
                content=_sample_content(),
                platform="github",
            )

        async def fetch_urls(self, urls):
            return [await self.fetch_url(url) for url in urls]

        def list_inbox(self):
            return []

        def detect_platform(self, url):
            return "github"

    mcp_server.fetch_service = FakeFetchService()

    raw = asyncio.run(mcp_server.read_url("https://github.com/owner/repo"))
    payload = json.loads(raw)

    assert payload["title"] == "Sample repo"
    assert payload["source_type"] == "github"
    assert asyncio.run(mcp_server.detect_platform("https://github.com/owner/repo")) == "github"


def test_mcp_read_batch_includes_structured_failure_without_none_crash(monkeypatch):
    _install_fake_mcp(monkeypatch)

    import mcp_server

    mcp_server = importlib.reload(mcp_server)

    class FakeFetchService:
        async def fetch_urls(self, urls):
            from feedgrab.service.models import FetchRequest, FetchResult, ServiceError

            return [
                FetchResult(
                    request=FetchRequest(url=urls[0]),
                    content=_sample_content(),
                    platform="github",
                ),
                FetchResult(
                    request=FetchRequest(url=urls[1]),
                    content=None,
                    platform="web",
                    success=False,
                    error=ServiceError(
                        "network down",
                        code="fetch_error",
                        details={"url": urls[1]},
                    ).to_dict(),
                ),
            ]

    mcp_server.fetch_service = FakeFetchService()

    raw = asyncio.run(
        mcp_server.read_batch(["https://github.com/owner/repo", "https://example.com/fail"])
    )
    payload = json.loads(raw)

    assert payload[0]["ok"] is True
    assert payload[0]["title"] == "Sample repo"
    assert payload[1]["ok"] is False
    assert payload[1]["error"]["code"] == "fetch_error"


def _install_fake_mcp(monkeypatch):
    class FakeFastMCP:
        def __init__(self, *args, **kwargs):
            self.tools = []

        def tool(self):
            def decorator(func):
                self.tools.append(func.__name__)
                return func

            return decorator

        def run(self, *args, **kwargs):
            return None

    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)
