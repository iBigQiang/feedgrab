# -*- coding: utf-8 -*-
"""Twitter keyword search query expansion tests."""

import asyncio

from feedgrab.fetchers import twitter_keyword_search as tks


def test_build_search_query_expands_ascii_keyword_case_variants():
    query = tks.build_search_query(
        "WorkBuddy",
        lang="",
        days=0,
        exclude_retweets=True,
    )

    assert query == '("WorkBuddy" OR "Workbuddy" OR "workbuddy") -is:retweet'


def test_search_expands_zh_zxx_language_and_all_sort(monkeypatch):
    calls = []

    def fake_fetch_with_cookie_rotation(fetcher, **kwargs):
        calls.append(
            {
                "raw_query": kwargs["raw_query"],
                "product": kwargs["product"],
            }
        )
        return {"call": len(calls)}, None

    monkeypatch.setattr(tks, "load_twitter_cookies", lambda: {"auth_token": "token", "ct0": "csrf"})
    monkeypatch.setattr(
        "feedgrab.fetchers.twitter_cookies.fetch_with_cookie_rotation",
        fake_fetch_with_cookie_rotation,
    )
    monkeypatch.setattr(tks, "parse_search_entries", lambda response: ([response], {}))
    monkeypatch.setattr(
        tks,
        "extract_tweet_data",
        lambda entry: {
            "id": "same-tweet",
            "author": "author",
            "text": "WorkBuddy",
            "views": entry["call"],
        },
    )

    result = asyncio.run(
        tks.search_twitter_keyword(
            keyword="WorkBuddy",
            lang="zh+zxx",
            days=0,
            sort="all",
            max_results=100,
            exclude_retweets=True,
            skip_summary=True,
        )
    )

    assert result["total"] == 1
    assert result["query"] == (
        '("WorkBuddy" OR "Workbuddy" OR "workbuddy") lang:zh -is:retweet'
        " | "
        '("WorkBuddy" OR "Workbuddy" OR "workbuddy") lang:zxx -is:retweet'
    )
    assert calls == [
        {
            "raw_query": '("WorkBuddy" OR "Workbuddy" OR "workbuddy") lang:zh -is:retweet',
            "product": "Latest",
        },
        {
            "raw_query": '("WorkBuddy" OR "Workbuddy" OR "workbuddy") lang:zxx -is:retweet',
            "product": "Latest",
        },
        {
            "raw_query": '("WorkBuddy" OR "Workbuddy" OR "workbuddy") lang:zh -is:retweet',
            "product": "Top",
        },
        {
            "raw_query": '("WorkBuddy" OR "Workbuddy" OR "workbuddy") lang:zxx -is:retweet',
            "product": "Top",
        },
    ]


def test_search_sorts_merged_results_before_applying_limit(monkeypatch):
    calls = []

    def fake_fetch_with_cookie_rotation(fetcher, **kwargs):
        calls.append(kwargs["product"])
        return {"call": len(calls), "product": kwargs["product"]}, None

    monkeypatch.setattr(tks, "load_twitter_cookies", lambda: {"auth_token": "token", "ct0": "csrf"})
    monkeypatch.setattr(
        "feedgrab.fetchers.twitter_cookies.fetch_with_cookie_rotation",
        fake_fetch_with_cookie_rotation,
    )
    monkeypatch.setattr(tks, "parse_search_entries", lambda response: ([response], {}))

    def fake_extract_tweet_data(entry):
        return {
            "id": f"tweet-{entry['call']}",
            "author": "author",
            "text": "WorkBuddy",
            "views": 1000 if entry["product"] == "Top" else entry["call"],
        }

    monkeypatch.setattr(tks, "extract_tweet_data", fake_extract_tweet_data)

    result = asyncio.run(
        tks.search_twitter_keyword(
            keyword="WorkBuddy",
            lang="zh+zxx",
            days=0,
            sort="all",
            max_results=2,
            exclude_retweets=True,
            skip_summary=True,
        )
    )

    assert [tweet["id"] for tweet in result["tweets"]] == ["tweet-3", "tweet-4"]


def test_summary_table_prefers_article_title_over_outer_article_url(tmp_path):
    output_path = tmp_path / "WorkBuddy.md"

    tks._generate_summary_table(
        keyword="WorkBuddy",
        query='("WorkBuddy") lang:zxx',
        sort="all",
        days=3,
        tweets=[
            {
                "id": "2072678445675614562",
                "author": "AdrianPunk115",
                "author_name": "Adrian Punk",
                "text": "http://x.com/i/article/2072663892124127232",
                "article": {
                    "title": "下半年的现象级Agent：从 0 到 1 速通 WorkBuddy",
                    "body": "你第一次打开 WorkBuddy，先别问它能帮你做什么。",
                },
                "created_at": "Thu Jul 02 10:00:00 +0000 2026",
                "views": 53168,
            }
        ],
        output_path=output_path,
    )

    markdown = output_path.read_text(encoding="utf-8")
    csv_text = output_path.with_suffix(".csv").read_text(encoding="utf-8-sig")

    assert "下半年的现象级Agent：从 0 到 1 速通 WorkBuddy" in markdown
    assert "下半年的现象级Agent：从 0 到 1 速通 WorkBuddy" in csv_text
    assert "http://x.com/i/article/2072663892124127232" not in markdown
    assert "http://x.com/i/article/2072663892124127232" not in csv_text


def test_x_platform_settings_schema_includes_article_search_expansion_options():
    from feedgrab.service.platform_settings import PLATFORM_SETTINGS_SCHEMA

    x_group = next(group for group in PLATFORM_SETTINGS_SCHEMA.platforms if group.id == "x")
    fields = {field.name: field for field in x_group.fields}

    assert [option["value"] for option in fields["X_SEARCH_LANG"].options] == [
        "",
        "zh+zxx",
        "zh",
        "en",
        "ja",
    ]
    assert [option["value"] for option in fields["X_SEARCH_SORT"].options] == [
        "live",
        "top",
        "all",
    ]
