# -*- coding: utf-8 -*-
"""Bookmark URL forms and failure-surfacing regressions.

Covers the 2026-08-31 fix: X started serving bookmark folders under
``/i/history/bookmarks/{id}``, and permission failures (HTTP 200 + errors)
were being reported as a successful run with 0 items.
"""

import asyncio

import pytest


# --- URL routing -----------------------------------------------------------

def test_detect_platform_supports_history_bookmarks():
    from feedgrab.reader import UniversalReader

    reader = UniversalReader()
    assert reader._detect_platform(
        "https://x.com/i/history/bookmarks/2012897882299572505"
    ) == "twitter_bookmarks"
    assert reader._detect_platform(
        "https://x.com/i/history/bookmarks"
    ) == "twitter_bookmarks"
    assert reader._detect_platform(
        "https://twitter.com/i/history/bookmarks/123456"
    ) == "twitter_bookmarks"


def test_detect_platform_bookmarks_no_regression():
    from feedgrab.reader import UniversalReader

    reader = UniversalReader()
    # Legacy forms keep working
    assert reader._detect_platform("https://x.com/i/bookmarks") == "twitter_bookmarks"
    assert reader._detect_platform(
        "https://x.com/i/bookmarks/2012897882299572505"
    ) == "twitter_bookmarks"
    # Neighbouring routes must not be swallowed
    assert reader._detect_platform("https://x.com/iBigQiang/status/1") == "twitter"
    assert reader._detect_platform("https://x.com/iBigQiang") == "twitter_user_tweets"
    assert reader._detect_platform("https://x.com/i/lists/456") == "twitter_list_tweets"


def test_parse_bookmark_url_all_forms():
    from feedgrab.fetchers.twitter_bookmarks import _parse_bookmark_url

    assert _parse_bookmark_url(
        "https://x.com/i/history/bookmarks/2012897882299572505"
    ) == {"type": "folder", "folder_id": "2012897882299572505"}
    assert _parse_bookmark_url(
        "https://x.com/i/bookmarks/2012897882299572505"
    ) == {"type": "folder", "folder_id": "2012897882299572505"}
    assert _parse_bookmark_url("https://x.com/i/bookmarks") == {
        "type": "all", "folder_id": ""
    }
    assert _parse_bookmark_url("https://x.com/i/history/bookmarks") == {
        "type": "all", "folder_id": ""
    }


# --- Response parsing ------------------------------------------------------

def test_parse_bookmark_entries_reads_collection_timeline():
    """X renamed bookmark_folder_timeline -> bookmark_collection_timeline."""
    from feedgrab.fetchers.twitter_graphql import parse_bookmark_entries

    response = {
        "data": {
            "bookmark_collection_timeline": {
                "timeline": {
                    "instructions": [
                        {
                            "type": "TimelineAddEntries",
                            "entries": [
                                {"entryId": "tweet-111", "content": {}},
                                {
                                    "entryId": "cursor-bottom-1",
                                    "content": {"cursorType": "Bottom", "value": "CURSOR"},
                                },
                            ],
                        }
                    ]
                }
            }
        }
    }
    entries, cursors = parse_bookmark_entries(response)
    assert [e["entryId"] for e in entries] == ["tweet-111"]
    assert cursors["bottom"] == "CURSOR"


# --- Failure surfacing -----------------------------------------------------

def test_graphql_error_summary():
    from feedgrab.fetchers.twitter_bookmarks import _graphql_error_summary

    assert _graphql_error_summary(None) == ""
    assert _graphql_error_summary({"data": {}}) == ""
    assert _graphql_error_summary(
        {"errors": [{"message": "a"}, {"message": "b"}]}
    ) == "a; b"


def _permission_error_response():
    """The exact shape X returns for a non-Premium account (observed live)."""
    return {
        "data": {"bookmark_collection_timeline": {}},
        "errors": [
            {
                "code": 37,
                "kind": "Permissions",
                "name": "AuthorizationError",
                "message": "Authorization: User is not authorized to use bookmark collections.",
            }
        ],
    }


def test_fetch_bookmarks_raises_on_permission_error(monkeypatch):
    """A 200-with-errors response must NOT be reported as 'completed, 0 items'."""
    from feedgrab.fetchers import twitter_bookmarks as tb
    from feedgrab.fetchers import twitter_cookies

    monkeypatch.setattr(tb, "_resolve_folder_name", lambda fid, ck: "TestFolder")
    monkeypatch.setattr(
        twitter_cookies,
        "fetch_with_cookie_rotation",
        lambda *a, **k: (_permission_error_response(), {}),
    )

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            tb.fetch_bookmarks(
                "https://x.com/i/history/bookmarks/2012897882299572505",
                {"auth_token": "t", "ct0": "c"},
            )
        )
    msg = str(excinfo.value)
    assert "not authorized" in msg
    # Actionable hint: rotating accounts cannot read someone else's bookmarks
    assert "私有数据" in msg


def test_fetch_bookmarks_quiet_when_truly_empty(monkeypatch):
    """An empty timeline with no errors is a normal end, not a failure."""
    from feedgrab.fetchers import twitter_bookmarks as tb
    from feedgrab.fetchers import twitter_cookies

    empty = {"data": {"bookmark_collection_timeline": {"timeline": {"instructions": []}}}}
    monkeypatch.setattr(tb, "_resolve_folder_name", lambda fid, ck: "TestFolder")
    monkeypatch.setattr(
        twitter_cookies, "fetch_with_cookie_rotation", lambda *a, **k: (empty, {})
    )

    result = asyncio.run(
        tb.fetch_bookmarks(
            "https://x.com/i/history/bookmarks/2012897882299572505",
            {"auth_token": "t", "ct0": "c"},
        )
    )
    assert result["total"] == 0
    assert result["fetched"] == 0


def test_fetch_bookmark_folders_names_permission_error(monkeypatch):
    """A 200-with-errors folder list must be logged as a failure, not '0 folders'."""
    from feedgrab.fetchers import twitter_graphql as tg

    # Structure observed live: data is valid but empty, the real signal is errors
    response = {
        "data": {"viewer": {"user_results": {"result": {"__typename": "User"}}}},
        "errors": [
            {
                "code": 37,
                "name": "AuthorizationError",
                "message": "Authorization: User is not authorized to use bookmark collections.",
            }
        ],
    }
    monkeypatch.setattr(tg, "_get_query_id", lambda name: "QID")
    monkeypatch.setattr(tg, "build_graphql_headers", lambda ck: {})
    monkeypatch.setattr(tg, "_execute_graphql", lambda **kw: response)

    records = []
    handler_id = tg.logger.add(
        lambda msg: records.append(msg.record), level="ERROR", format="{message}"
    )
    try:
        assert tg.fetch_bookmark_folders({"auth_token": "t", "ct0": "c"}) == []
    finally:
        tg.logger.remove(handler_id)

    joined = " ".join(r["message"] for r in records)
    assert "not authorized" in joined
    assert "X Premium" in joined


def test_fetch_bookmarks_raises_when_first_page_has_no_response(monkeypatch):
    """401/403 导致首页无响应时，绝不能上报「完成，总数 0」。"""
    from feedgrab.fetchers import twitter_bookmarks as tb
    from feedgrab.fetchers import twitter_cookies

    monkeypatch.setattr(tb, "_resolve_folder_name", lambda fid, ck: "TestFolder")
    monkeypatch.setattr(
        twitter_cookies, "fetch_with_cookie_rotation", lambda *a, **k: (None, {})
    )

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            tb.fetch_bookmarks(
                "https://x.com/i/history/bookmarks/2012897882299572505",
                {"auth_token": "t", "ct0": "c"},
            )
        )
    msg = str(excinfo.value)
    assert "401" in msg
    assert "login twitter" in msg


def test_fetch_bookmarks_pins_primary_account(monkeypatch):
    """书签分页必须显式要求主账号，不得走多账号轮换。"""
    from feedgrab.fetchers import twitter_bookmarks as tb
    from feedgrab.fetchers import twitter_cookies

    seen = {}
    empty = {
        "data": {"bookmark_collection_timeline": {"timeline": {"instructions": []}}}
    }

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return (empty, {})

    monkeypatch.setattr(tb, "_resolve_folder_name", lambda fid, ck: "TestFolder")
    monkeypatch.setattr(twitter_cookies, "fetch_with_cookie_rotation", spy)

    asyncio.run(
        tb.fetch_bookmarks(
            "https://x.com/i/history/bookmarks/2012897882299572505",
            {"auth_token": "t", "ct0": "c"},
        )
    )
    assert seen.get("primary_only") is True, "书签必须钉在主账号上"


def test_detect_platform_supports_bare_history_overview():
    """X moved the bookmarks overview to a bare /i/history (2026-08 change).

    Before the fix this fell through to the single-tweet route and the run
    failed with a parse error instead of listing bookmarks.
    """
    from feedgrab.reader import UniversalReader

    reader = UniversalReader()
    assert reader._detect_platform("https://x.com/i/history") == "twitter_bookmarks"
    assert reader._detect_platform("https://x.com/i/history/") == "twitter_bookmarks"
    assert reader._detect_platform("https://twitter.com/i/history") == "twitter_bookmarks"


def test_bare_history_parses_as_all_bookmarks():
    from feedgrab.fetchers.twitter_bookmarks import _parse_bookmark_url

    assert _parse_bookmark_url("https://x.com/i/history") == {
        "type": "all", "folder_id": ""
    }


def test_other_history_subpages_are_not_swallowed():
    """Only /i/history and /i/history/bookmarks* are bookmarks.

    Matching by prefix would hand any future /i/history/<page> to the bookmark
    fetcher, which would then fail confusingly.
    """
    from feedgrab.reader import UniversalReader

    reader = UniversalReader()
    assert reader._detect_platform("https://x.com/i/history/views") != "twitter_bookmarks"


def test_route_and_parse_agree_on_what_a_folder_id_is():
    """The router and the parser must accept the same digits.

    The route used \\d (which also matches Arabic-Indic digits) while
    _parse_bookmark_url used [0-9]. A folder id in Unicode digits therefore
    routed to bookmarks but parsed as type="all" — a request for one folder
    silently became "download every bookmark".
    """
    from feedgrab.reader import UniversalReader
    from feedgrab.fetchers.twitter_bookmarks import _parse_bookmark_url

    reader = UniversalReader()
    unicode_digits = "https://x.com/i/history/bookmarks/٤٥٦"
    assert _parse_bookmark_url(unicode_digits)["type"] == "all"
    assert reader._detect_platform(unicode_digits) != "twitter_bookmarks"
    # ASCII digits keep routing and parsing as one folder
    ascii_digits = "https://x.com/i/history/bookmarks/456"
    assert reader._detect_platform(ascii_digits) == "twitter_bookmarks"
    assert _parse_bookmark_url(ascii_digits) == {"type": "folder", "folder_id": "456"}
