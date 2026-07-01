import asyncio
import json
from urllib.parse import parse_qs, urlparse

import pytest

from feedgrab.fetchers import reddit


def test_flat_reddit_cookie_session_is_normalized(tmp_path):
    session_file = tmp_path / "reddit.json"
    session_file.write_text(
        """
        {
          "token_v2": "token-value"
          "reddit_session": "session-value"
          "csrf_token": "csrf-value"
          "session_tracker": "tracker-value"
          "SSID": "google-cookie-should-be-ignored"
        }
        """,
        encoding="utf-8",
    )

    assert reddit._ensure_reddit_storage_state(session_file) == str(session_file)

    state = json.loads(session_file.read_text(encoding="utf-8"))
    cookies = state["cookies"]
    assert state["origins"] == []
    assert [cookie["name"] for cookie in cookies] == [
        "reddit_session",
        "csrf_token",
        "token_v2",
        "session_tracker",
    ]
    assert {cookie["domain"] for cookie in cookies} == {".reddit.com"}
    assert {cookie["path"] for cookie in cookies} == {"/"}
    assert {cookie["secure"] for cookie in cookies} == {True}
    assert {cookie["sameSite"] for cookie in cookies} == {"Lax"}


def test_reddit_session_path_uses_configured_session_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("FEEDGRAB_DATA_DIR", str(tmp_path))

    assert reddit._reddit_session_path() == tmp_path / "reddit.json"


def test_reddit_direct_json_uses_cookie_header_from_session(monkeypatch, tmp_path):
    monkeypatch.setenv("FEEDGRAB_DATA_DIR", str(tmp_path))
    (tmp_path / "reddit.json").write_text(
        json.dumps(
            {
                "reddit_session": "session-value",
                "csrf_token": "csrf-value",
                "token_v2": "token-value",
                "SSID": "google-cookie-should-be-ignored",
            }
        ),
        encoding="utf-8",
    )
    seen_headers = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return [{"ok": True}]

    def fake_get(_url, *, headers, timeout):
        seen_headers.update(headers)
        assert timeout == 20
        return Response()

    monkeypatch.setattr(reddit.http_client, "get", fake_get)

    assert reddit._fetch_json_direct("https://old.reddit.com/comments/abc/.json") == [{"ok": True}]
    assert seen_headers["Cookie"] == (
        "reddit_session=session-value; csrf_token=csrf-value; token_v2=token-value"
    )
    assert "SSID" not in seen_headers["Cookie"]


def test_validate_reddit_session_calls_api_me_with_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("FEEDGRAB_DATA_DIR", str(tmp_path))
    (tmp_path / "reddit.json").write_text(
        json.dumps({"reddit_session": "session-value", "token_v2": "token-value"}),
        encoding="utf-8",
    )
    seen = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"name": "testuser", "subreddit": {"display_name_prefixed": "u/testuser"}}

    def fake_get(url, *, headers, timeout):
        seen["url"] = url
        seen["headers"] = headers
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(reddit.http_client, "get", fake_get)

    result = reddit.validate_reddit_session()

    assert result["status"] == "ok"
    assert result["authenticated"] is True
    assert result["username"] == "testuser"
    assert seen["url"] == "https://www.reddit.com/api/me.json"
    assert "reddit_session=session-value" in seen["headers"]["Cookie"]
    assert "token_v2=token-value" in seen["headers"]["Cookie"]


def test_reddit_backend_status_reports_active_opencli(monkeypatch):
    monkeypatch.setattr(reddit, "validate_reddit_session", lambda **_kwargs: {"status": "missing", "message": "no session"})
    monkeypatch.setattr(reddit, "_probe_reddit_cdp_cookie", lambda: {"name": "cdp_cookie", "status": "off", "message": "no cdp"})
    monkeypatch.setattr(reddit, "_probe_reddit_playwright_session", lambda: {"name": "playwright_session", "status": "off", "message": "no session"})
    monkeypatch.setattr(reddit, "_probe_opencli_reddit", lambda: {"name": "opencli", "status": "ok", "message": "OpenCLI 可用"})
    monkeypatch.setattr(reddit, "_probe_rdt_cli", lambda: {"name": "rdt-cli", "status": "off", "message": "missing"})

    result = reddit.probe_reddit_backends()

    assert result["active_backend"] == "opencli"
    assert [item["name"] for item in result["checks"]] == [
        "direct_json_session",
        "cdp_cookie",
        "playwright_session",
        "opencli",
        "rdt-cli",
    ]


def test_reddit_direct_json_retries_after_retry_header(monkeypatch):
    calls = []
    sleeps = []

    class Response429:
        status_code = 429
        headers = {"Retry-After": "3"}

        @staticmethod
        def json():
            return {}

    class Response200:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"ok": True}

    def fake_get(url, *, headers, timeout):
        calls.append(url)
        return Response429() if len(calls) == 1 else Response200()

    monkeypatch.setattr(reddit.http_client, "get", fake_get)
    monkeypatch.setattr(reddit.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert reddit._fetch_json_direct("https://old.reddit.com/r/python/hot.json") == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [3.0]


def test_reddit_jina_block_page_is_not_returned(monkeypatch):
    monkeypatch.setattr(reddit, "_fetch_json_direct", lambda _url: None)

    async def fake_browser(_url):
        return None

    monkeypatch.setattr(reddit, "_fetch_json_via_browser", fake_browser)

    from feedgrab.fetchers import jina

    monkeypatch.setattr(
        jina,
        "fetch_via_jina",
        lambda _url: {
            "title": "Title:",
            "content": (
                "Warning: Target URL returned error 403: Forbidden\n\n"
                "You've been blocked by network security."
            ),
        },
    )

    with pytest.raises(RuntimeError, match="Reddit 抓取全部 Tier 失败"):
        asyncio.run(
            reddit.fetch_reddit(
                "https://www.reddit.com/r/todayilearned/comments/1uh9b5o/example/"
            )
        )


def test_reddit_single_post_defaults_to_search_posts_category(monkeypatch):
    payload = [
        {
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "id": "abc123",
                            "title": "Direct post",
                            "subreddit": "delta",
                            "author": "alice",
                            "score": 8,
                            "num_comments": 0,
                            "permalink": "/r/delta/comments/abc123/direct_post/",
                            "is_self": True,
                            "selftext": "hello",
                        },
                    }
                ]
            }
        },
        {"data": {"children": []}},
    ]

    monkeypatch.setattr(reddit, "_fetch_json_direct", lambda _url: payload)

    result = asyncio.run(reddit.fetch_reddit("https://www.reddit.com/r/delta/comments/abc123/direct_post/"))

    assert result["category"] == "search/posts"


def test_reddit_search_builds_global_old_reddit_query_and_filters_posts(monkeypatch):
    seen_urls = []

    def fake_direct(url):
        seen_urls.append(url)
        return {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "id": "abc123",
                            "title": "Codex release discussion",
                            "subreddit": "technology",
                            "author": "alice",
                            "score": 42,
                            "num_comments": 7,
                            "upvote_ratio": 0.91,
                            "created_utc": 1782600000,
                            "permalink": "/r/technology/comments/abc123/codex_release_discussion/",
                            "url": "https://www.reddit.com/r/technology/comments/abc123/codex_release_discussion/",
                            "selftext": "A short post about codex.",
                        },
                    },
                    {"kind": "t1", "data": {"id": "comment-should-be-ignored"}},
                ]
            },
        }

    monkeypatch.setattr(reddit, "_fetch_json_direct", fake_direct)

    result = asyncio.run(
        reddit.fetch_reddit_search("codex", sort="comments", time_range="all", limit=10)
    )

    parsed = urlparse(seen_urls[0])
    params = parse_qs(parsed.query)
    assert parsed.netloc == "old.reddit.com"
    assert parsed.path == "/search.json"
    assert params["q"] == ["codex"]
    assert params["type"] == ["link"]
    assert params["sort"] == ["comments"]
    assert params["t"] == ["all"]
    assert params["limit"] == ["10"]
    assert params["raw_json"] == ["1"]
    assert result["search_sort"] == "comments"
    assert result["search_time_range"] == "all"
    assert result["subreddit"] == ""
    assert result["result_type"] == "posts/link"
    assert result["items"][0]["kind"] == "t3"
    assert result["items"][0]["title"] == "Codex release discussion"
    assert "comment-should-be-ignored" not in json.dumps(result, ensure_ascii=False)
    assert "| r/technology | u/alice | 42 | 7 |" in result["content"]


def test_reddit_search_category_uses_keyword_directory(monkeypatch):
    monkeypatch.setattr(
        reddit,
        "_fetch_json_direct",
        lambda _url: {"kind": "Listing", "data": {"children": []}},
    )

    result = asyncio.run(reddit.fetch_reddit_search("codex", sort="comments", time_range="all", limit=10))

    assert result["category"] == "search/codex"


def test_reddit_search_storage_uses_vault_and_category_path(monkeypatch, tmp_path):
    from pathlib import Path

    from feedgrab.schema import SourceType, UnifiedContent
    from feedgrab.utils.storage import save_to_markdown

    vault = tmp_path / "vault"
    output_dir = tmp_path / "output"
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))

    summary = UnifiedContent(
        source_type=SourceType.REDDIT,
        source_name="reddit",
        title="Reddit Search Codex",
        content="summary",
        url="https://www.reddit.com/search/?q=codex",
        category="search/codex",
    )
    post = UnifiedContent(
        source_type=SourceType.REDDIT,
        source_name="u/alice",
        title="Reddit Post",
        content="post",
        url="https://www.reddit.com/r/test/comments/abc/post/",
        category="search/posts",
    )

    summary_path = Path(save_to_markdown(summary))
    post_path = Path(save_to_markdown(post))

    assert summary_path.parent == vault / "Reddit" / "search" / "codex"
    assert post_path.parent == vault / "Reddit" / "search" / "posts"
    assert not (output_dir / "Reddit").exists()


def test_reddit_search_save_posts_assigns_posts_category(monkeypatch):
    from feedgrab import cli
    from feedgrab import config
    from feedgrab.fetchers import reddit as reddit_fetcher
    from feedgrab.utils import storage

    async def fake_search(*_args, **_kwargs):
        return {
            "id": "reddit_search_abc",
            "title": "Reddit 搜索：codex",
            "content": "summary",
            "url": "https://www.reddit.com/search/?q=codex",
            "author": "reddit",
            "category": "search/codex",
            "items": [{"permalink": "https://www.reddit.com/r/test/comments/abc/post/"}],
        }

    async def fake_post(_url):
        return {
            "id": "abc",
            "title": "Reddit Post",
            "content": "post",
            "url": "https://www.reddit.com/r/test/comments/abc/post/",
            "author": "alice",
        }

    saved_categories = []

    def fake_save(item):
        saved_categories.append(item.category)
        return f"C:\\tmp\\{item.id}.md"

    monkeypatch.setattr(config, "reddit_search_enabled", lambda: True)
    monkeypatch.setattr(config, "reddit_search_limit", lambda: 10)
    monkeypatch.setattr(config, "reddit_search_save_posts", lambda: False)
    monkeypatch.setattr(config, "reddit_search_sort", lambda: "relevance")
    monkeypatch.setattr(config, "reddit_search_subreddit", lambda: "")
    monkeypatch.setattr(config, "reddit_search_time_range", lambda: "all")
    monkeypatch.setattr(reddit_fetcher, "fetch_reddit_search", fake_search)
    monkeypatch.setattr(reddit_fetcher, "fetch_reddit", fake_post)
    monkeypatch.setattr(storage, "save_to_markdown", fake_save)

    cli.cmd_reddit_search(["codex", "--save-posts"])

    assert saved_categories == ["search/codex", "search/posts"]


def test_reddit_search_paginates_with_after_cursor(monkeypatch):
    seen_urls = []

    def listing(post_id, after):
        return {
            "kind": "Listing",
            "data": {
                "after": after,
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "id": post_id,
                            "title": f"Post {post_id}",
                            "subreddit": "python",
                            "author": "alice",
                            "score": 1,
                            "num_comments": 0,
                            "permalink": f"/r/python/comments/{post_id}/post/",
                        },
                    }
                ],
            },
        }

    def fake_direct(url):
        seen_urls.append(url)
        return listing("a1", "t3_after") if len(seen_urls) == 1 else listing("a2", None)

    monkeypatch.setattr(reddit, "_fetch_json_direct", fake_direct)

    result = asyncio.run(reddit.fetch_reddit_search("codex", limit=2))

    assert [item["id"] for item in result["items"]] == ["a1", "a2"]
    assert "after=t3_after" in seen_urls[1]


def test_reddit_reply_mode_tree_renders_nested_replies(monkeypatch):
    monkeypatch.setattr(reddit, "reddit_reply_mode", lambda: "tree")
    payload = [
        {"data": {"children": [{"kind": "t3", "data": {"id": "post1", "title": "Nested", "subreddit": "python", "author": "op", "permalink": "/r/python/comments/post1/nested/"}}]}},
        {
            "data": {
                "children": [
                    {
                        "kind": "t1",
                        "data": {
                            "author": "top",
                            "score": 5,
                            "body": "top comment",
                            "replies": {
                                "data": {
                                    "children": [
                                        {"kind": "t1", "data": {"author": "child", "score": 2, "body": "nested reply"}}
                                    ]
                                }
                            },
                        },
                    }
                ]
            }
        },
    ]

    comments = reddit._extract_comments(payload, max_n=10, mode="tree")
    rendered = reddit._render_post(payload[0]["data"]["children"][0]["data"], comments)

    assert comments[1]["_depth"] == 1
    assert "top comment" in rendered["content"]
    assert "> nested reply" in rendered["content"]


def test_reddit_reply_mode_all_expands_morechildren(monkeypatch):
    payload = [
        {"data": {"children": [{"kind": "t3", "data": {"id": "post1", "title": "More", "subreddit": "python", "author": "op", "permalink": "/r/python/comments/post1/more/"}}]}},
        {
            "data": {
                "children": [
                    {"kind": "t1", "data": {"id": "a", "name": "t1_a", "parent_id": "t3_post1", "author": "top", "score": 5, "body": "top comment", "replies": ""}},
                    {"kind": "more", "data": {"parent_id": "t3_post1", "children": ["b", "c"], "count": 2}},
                ]
            }
        },
    ]
    seen_urls = []

    def fake_direct(url):
        seen_urls.append(url)
        return {
            "json": {
                "errors": [],
                "data": {
                    "things": [
                        {"kind": "t1", "data": {"id": "b", "name": "t1_b", "parent_id": "t3_post1", "author": "more", "score": 3, "body": "expanded comment", "replies": ""}},
                    ]
                },
            }
        }

    monkeypatch.setattr(reddit, "_fetch_json_direct", fake_direct)
    monkeypatch.setattr(reddit, "reddit_morechildren_rounds", lambda: 1)

    expanded, stats = reddit._expand_morechildren(payload, post_id="post1")
    comments = reddit._extract_comments(expanded, max_n=10, mode="all")

    assert stats["expanded_count"] == 1
    assert stats["remaining_count"] == 0
    assert "/api/morechildren.json" in seen_urls[0]
    assert "children=b%2Cc" in seen_urls[0]
    assert [comment["body"] for comment in comments] == ["top comment", "expanded comment"]


def test_reddit_post_media_fields_are_extracted_into_result():
    post = {
        "id": "abc",
        "title": "Media",
        "subreddit": "pics",
        "author": "alice",
        "permalink": "/r/pics/comments/abc/media/",
        "post_hint": "image",
        "preview": {"images": [{"source": {"url": "https://preview.example/image.jpg"}}]},
        "gallery_data": {"items": [{"media_id": "m1"}]},
        "media_metadata": {"m1": {"s": {"u": "https://gallery.example/one.jpg"}}},
    }

    result = reddit._render_post(post, [])

    assert result["post_hint"] == "image"
    assert result["preview_image_url"] == "https://preview.example/image.jpg"
    assert result["gallery_urls"] == ["https://gallery.example/one.jpg"]
    assert "媒体" in result["content"]


def test_reddit_search_omits_time_for_hot_and_new(monkeypatch):
    seen_urls = []

    def fake_direct(url):
        seen_urls.append(url)
        return {"kind": "Listing", "data": {"children": []}}

    monkeypatch.setattr(reddit, "_fetch_json_direct", fake_direct)

    asyncio.run(reddit.fetch_reddit_search("codex", sort="hot", time_range="week", limit=5))
    asyncio.run(reddit.fetch_reddit_search("codex", sort="new", time_range="month", limit=5))

    assert all("t" not in parse_qs(urlparse(url).query) for url in seen_urls)
    assert parse_qs(urlparse(seen_urls[0]).query)["sort"] == ["hot"]
    assert parse_qs(urlparse(seen_urls[1]).query)["sort"] == ["new"]


def test_reddit_subreddit_search_uses_restrict_sr_and_posts_type(monkeypatch):
    seen_urls = []
    monkeypatch.setattr(reddit, "_fetch_json_direct", lambda url: seen_urls.append(url) or {"data": {"children": []}})

    asyncio.run(
        reddit.fetch_reddit_search("codex", sort="top", time_range="week", limit=8, subreddit="ChatGPT")
    )

    parsed = urlparse(seen_urls[0])
    params = parse_qs(parsed.query)
    assert parsed.path == "/r/ChatGPT/search.json"
    assert params["restrict_sr"] == ["on"]
    assert params["type"] == ["link"]
    assert params["sort"] == ["top"]
    assert params["t"] == ["week"]


def test_parse_reddit_search_url_extracts_query_sort_time_and_subreddit():
    kind, info = reddit.parse_reddit_url(
        "https://www.reddit.com/r/ChatGPT/search/?q=codex&type=posts&sort=top&t=week"
    )

    assert kind == "search"
    assert info == {
        "keyword": "codex",
        "sort": "top",
        "time_range": "week",
        "subreddit": "ChatGPT",
    }


def test_login_reddit_is_registered():
    from feedgrab.login import PLATFORM_URLS, _CDP_COOKIE_DOMAINS, _CDP_COOKIE_URLS
    from feedgrab.service.platform_settings import get_login_capability

    assert PLATFORM_URLS["reddit"] == "https://www.reddit.com/login/"
    assert ".reddit.com" in _CDP_COOKIE_DOMAINS["reddit"]
    assert "https://www.reddit.com" in _CDP_COOKIE_URLS["reddit"]
    assert get_login_capability("reddit").login_required is True


def test_login_reddit_headless_is_blocked(monkeypatch, tmp_path, capsys):
    import feedgrab.login as login_mod

    called = {"headless": False}
    monkeypatch.setattr(login_mod, "_session_dir", lambda: tmp_path)
    monkeypatch.setattr(login_mod, "_login_headless", lambda *_args, **_kwargs: called.__setitem__("headless", True))

    login_mod.login("reddit", headless=True)

    assert called["headless"] is False
    assert "Reddit 不支持 headless 自动登录" in capsys.readouterr().out


def test_login_reddit_force_interactive_skips_existing_cdp_cookie_extract(monkeypatch, tmp_path):
    import feedgrab.login as login_mod

    calls = {"extract": 0, "interactive": 0}
    monkeypatch.setattr(login_mod, "_session_dir", lambda: tmp_path)
    monkeypatch.setenv("CHROME_CDP_LOGIN", "true")
    monkeypatch.setenv("FEEDGRAB_FORCE_INTERACTIVE_LOGIN", "true")
    monkeypatch.setattr(login_mod, "_login_via_cdp", lambda *_args, **_kwargs: calls.__setitem__("extract", calls["extract"] + 1) or True)
    monkeypatch.setattr(login_mod, "_login_interactive_via_cdp", lambda *_args, **_kwargs: calls.__setitem__("interactive", calls["interactive"] + 1) or True)

    login_mod.login("reddit")

    assert calls == {"extract": 0, "interactive": 1}
