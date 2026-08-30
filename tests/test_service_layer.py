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
    assert "失败 [web] https://example.com/fail: network down" in out
    assert "已抓取 1/2 个 URL" in out


def test_cli_twitter_search_exits_when_all_batch_keywords_fail(monkeypatch, capsys):
    import feedgrab.cli as cli
    import feedgrab.config as config
    import feedgrab.fetchers.twitter_keyword_search as search_module

    monkeypatch.setattr(config, "x_search_enabled", lambda: True)
    monkeypatch.setattr(config, "x_search_lang", lambda: "")
    monkeypatch.setattr(config, "x_search_days", lambda: 1)
    monkeypatch.setattr(config, "x_search_min_faves", lambda: 0)
    monkeypatch.setattr(config, "x_search_min_retweets", lambda: 0)
    monkeypatch.setattr(config, "x_search_sort", lambda: "live")
    monkeypatch.setattr(config, "x_search_exclude_retweets", lambda: True)
    monkeypatch.setattr(config, "x_search_delay", lambda: 0)
    monkeypatch.setattr(config, "x_search_max_results", lambda: 10)
    monkeypatch.setattr(config, "x_search_save_tweets", lambda: False)
    monkeypatch.setattr(config, "x_search_merge_keywords", lambda: True)

    async def failing_search(**_kwargs):
        raise RuntimeError("missing Twitter login")

    monkeypatch.setattr(search_module, "search_twitter_keyword", failing_search)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_twitter_search(["alpha,beta"])

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "❌ [alpha] missing Twitter login" in out
    assert "❌ [beta] missing Twitter login" in out
    assert "❌ 所有 X 关键词搜索都失败：alpha, beta" in out


def test_cli_twitter_search_merge_writes_empty_summary(monkeypatch, tmp_path, capsys):
    import feedgrab.cli as cli
    import feedgrab.config as config
    import feedgrab.fetchers.twitter_keyword_search as search_module

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    monkeypatch.setattr(config, "x_search_enabled", lambda: True)
    monkeypatch.setattr(config, "x_search_lang", lambda: "")
    monkeypatch.setattr(config, "x_search_days", lambda: 1)
    monkeypatch.setattr(config, "x_search_min_faves", lambda: 0)
    monkeypatch.setattr(config, "x_search_min_retweets", lambda: 0)
    monkeypatch.setattr(config, "x_search_sort", lambda: "live")
    monkeypatch.setattr(config, "x_search_exclude_retweets", lambda: True)
    monkeypatch.setattr(config, "x_search_delay", lambda: 0)
    monkeypatch.setattr(config, "x_search_max_results", lambda: 10)
    monkeypatch.setattr(config, "x_search_save_tweets", lambda: False)
    monkeypatch.setattr(config, "x_search_merge_keywords", lambda: True)

    async def empty_search(**kwargs):
        return {
            "total": 0,
            "saved": 0,
            "query": kwargs["keyword"],
            "output_path": "",
            "csv_path": "",
            "tweets": [],
        }

    monkeypatch.setattr(search_module, "search_twitter_keyword", empty_search)

    cli.cmd_twitter_search(["alpha,beta"])

    out = capsys.readouterr().out
    assert "合并汇总：" in out
    merged_files = list(tmp_path.glob("X/search/1day_new/alpha+beta_*.md"))
    assert len(merged_files) == 1
    assert "*未找到结果。*" in merged_files[0].read_text(encoding="utf-8")


def test_cli_twitter_search_merge_uses_all_sort_output_dir(monkeypatch, tmp_path, capsys):
    import feedgrab.cli as cli
    import feedgrab.config as config
    import feedgrab.fetchers.twitter_keyword_search as search_module

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    monkeypatch.setattr(config, "x_search_enabled", lambda: True)
    monkeypatch.setattr(config, "x_search_lang", lambda: "zh+zxx")
    monkeypatch.setattr(config, "x_search_days", lambda: 3)
    monkeypatch.setattr(config, "x_search_min_faves", lambda: 0)
    monkeypatch.setattr(config, "x_search_min_retweets", lambda: 0)
    monkeypatch.setattr(config, "x_search_sort", lambda: "all")
    monkeypatch.setattr(config, "x_search_exclude_retweets", lambda: True)
    monkeypatch.setattr(config, "x_search_delay", lambda: 0)
    monkeypatch.setattr(config, "x_search_max_results", lambda: 10)
    monkeypatch.setattr(config, "x_search_save_tweets", lambda: False)
    monkeypatch.setattr(config, "x_search_merge_keywords", lambda: True)

    async def empty_search(**kwargs):
        return {
            "total": 0,
            "saved": 0,
            "query": kwargs["keyword"],
            "output_path": "",
            "csv_path": "",
            "tweets": [],
        }

    monkeypatch.setattr(search_module, "search_twitter_keyword", empty_search)

    cli.cmd_twitter_search(["alpha,beta"])

    capsys.readouterr()
    assert list(tmp_path.glob("X/search/3day_all/alpha+beta_*.md"))
    assert not list(tmp_path.glob("X/search/3day_hot/alpha+beta_*.md"))


def test_cli_twitter_search_merge_dedupes_cross_keyword_tweets(monkeypatch, tmp_path, capsys):
    import feedgrab.cli as cli
    import feedgrab.config as config
    import feedgrab.fetchers.twitter_keyword_search as search_module

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    monkeypatch.setattr(config, "x_search_enabled", lambda: True)
    monkeypatch.setattr(config, "x_search_lang", lambda: "zh+zxx")
    monkeypatch.setattr(config, "x_search_days", lambda: 1)
    monkeypatch.setattr(config, "x_search_min_faves", lambda: 0)
    monkeypatch.setattr(config, "x_search_min_retweets", lambda: 0)
    monkeypatch.setattr(config, "x_search_sort", lambda: "all")
    monkeypatch.setattr(config, "x_search_exclude_retweets", lambda: True)
    monkeypatch.setattr(config, "x_search_delay", lambda: 0)
    monkeypatch.setattr(config, "x_search_max_results", lambda: 10)
    monkeypatch.setattr(config, "x_search_save_tweets", lambda: False)
    monkeypatch.setattr(config, "x_search_merge_keywords", lambda: True)

    shared_tweet = {
        "id": "1001",
        "author": "bigqiang",
        "text": "chatgpt and codex",
        "views": 10,
    }

    async def fake_search(**kwargs):
        tweets = [dict(shared_tweet)]
        if kwargs["keyword"] == "beta":
            # 模拟同一关键词内部 sort=all / lang=zh+zxx 展开后的重复
            tweets.append(dict(shared_tweet))
        return {
            "total": len(tweets),
            "saved": 0,
            "query": kwargs["keyword"],
            "output_path": "",
            "csv_path": "",
            "tweets": tweets,
        }

    monkeypatch.setattr(search_module, "search_twitter_keyword", fake_search)

    cli.cmd_twitter_search(["alpha,beta"])

    out = capsys.readouterr().out
    merged_files = list(tmp_path.glob("X/search/1day_all/alpha+beta_*.md"))
    assert len(merged_files) == 1
    text = merged_files[0].read_text(encoding="utf-8")
    assert text.count("https://x.com/bigqiang/status/1001") == 1
    assert "移除 2 条跨关键词重复记录" in out


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
