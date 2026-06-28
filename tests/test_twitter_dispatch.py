# -*- coding: utf-8 -*-
"""Twitter dispatcher and cookie loading regressions."""

import asyncio
import importlib
import json
import time


def _write_playwright_twitter_session(path, auth_token: str = "a" * 40, ct0: str = "b" * 160) -> None:
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "auth_token", "value": auth_token, "domain": ".x.com"},
                    {"name": "ct0", "value": ct0, "domain": ".x.com"},
                ]
            }
        ),
        encoding="utf-8",
    )


def test_twitter_cookie_loader_uses_current_data_dir(monkeypatch, tmp_path):
    from feedgrab.fetchers import twitter_cookies

    monkeypatch.delenv("X_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("X_CT0", raising=False)
    stale_dir = tmp_path / "stale"
    current_dir = tmp_path / "current"
    current_dir.mkdir()
    _write_playwright_twitter_session(current_dir / "twitter.json")

    monkeypatch.setenv("FEEDGRAB_DATA_DIR", str(stale_dir))
    twitter_cookies = importlib.reload(twitter_cookies)
    monkeypatch.setattr(twitter_cookies, "_LEGACY_COOKIE_DIRS", [])
    monkeypatch.setattr(twitter_cookies, "_LEGACY_SESSION_DIRS", [])
    monkeypatch.setenv("FEEDGRAB_DATA_DIR", str(current_dir))

    cookies = twitter_cookies.load_twitter_cookies()

    assert cookies["auth_token"] == "a" * 40
    assert cookies["ct0"] == "b" * 160


def test_fetch_twitter_tries_next_graphql_cookie_before_fxtwitter(monkeypatch):
    from feedgrab.fetchers import twitter, twitter_cookies, twitter_fxtwitter

    monkeypatch.delenv("X_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("X_CT0", raising=False)
    bad = {"auth_token": "bad" * 20, "ct0": "c" * 160}
    good = {"auth_token": "good" * 10, "ct0": "d" * 160}
    seen = []

    monkeypatch.setattr(
        twitter_cookies,
        "load_all_twitter_cookie_sets",
        lambda: [("playwright(twitter.json)", bad), ("cookie_file(x_2.json)", good)],
        raising=False,
    )
    monkeypatch.setattr(twitter_cookies, "load_twitter_cookies", lambda: bad)
    monkeypatch.setattr(twitter_cookies, "activate_twitter_cookie", lambda _cookies: None, raising=False)
    monkeypatch.setattr(twitter_cookies, "is_twitter_cookie_rate_limited", lambda _cookies: False, raising=False)

    async def fake_graphql(url, tweet_id, cookies=None):
        seen.append(cookies["auth_token"])
        if cookies is bad:
            raise RuntimeError("401 unauthorized")
        return {
            "text": "GraphQL full tweet",
            "author": "@iBigQiang",
            "author_name": "强子手记",
            "url": url,
            "title": "GraphQL title",
            "platform": "twitter",
            "thread_tweets": [{"id": tweet_id, "text": "GraphQL full tweet"}],
            "has_thread": False,
        }

    monkeypatch.setattr(twitter, "_fetch_via_graphql", fake_graphql)
    monkeypatch.setattr(twitter_fxtwitter, "is_circuit_broken", lambda: False)
    monkeypatch.setattr(
        twitter_fxtwitter,
        "fetch_via_fxtwitter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("FxTwitter should not be called")),
    )

    result = asyncio.run(twitter.fetch_twitter("https://x.com/iBigQiang/status/2071053794977423489"))

    assert result["text"] == "GraphQL full tweet"
    assert seen == ["bad" * 20, "good" * 10]


def test_fetch_twitter_does_not_retry_bad_cookie_when_more_cookies_exist(monkeypatch):
    from feedgrab.fetchers import twitter, twitter_cookies, twitter_fxtwitter

    monkeypatch.delenv("X_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("X_CT0", raising=False)
    bad = {"auth_token": "bad" * 20, "ct0": "c" * 160}
    good = {"auth_token": "good" * 10, "ct0": "d" * 160}
    seen = []

    monkeypatch.setattr(
        twitter_cookies,
        "load_all_twitter_cookie_sets",
        lambda: [("playwright(twitter.json)", bad), ("cookie_file(x_2.json)", good)],
    )
    monkeypatch.setattr(twitter_cookies, "activate_twitter_cookie", lambda _cookies: None)
    monkeypatch.setattr(twitter_cookies, "is_twitter_cookie_rate_limited", lambda _cookies: False)
    monkeypatch.setattr(
        time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("should try next cookie without sleeping")),
    )

    async def fake_graphql(url, tweet_id, cookies=None):
        seen.append(cookies["auth_token"])
        if cookies is bad:
            raise RuntimeError("GraphQL returned no usable data")
        return {
            "text": "GraphQL full tweet",
            "author": "@iBigQiang",
            "author_name": "强子手记",
            "url": url,
            "title": "GraphQL title",
            "platform": "twitter",
            "thread_tweets": [{"id": tweet_id, "text": "GraphQL full tweet"}],
            "has_thread": False,
        }

    monkeypatch.setattr(twitter, "_fetch_via_graphql", fake_graphql)
    monkeypatch.setattr(twitter_fxtwitter, "is_circuit_broken", lambda: False)
    monkeypatch.setattr(
        twitter_fxtwitter,
        "fetch_via_fxtwitter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("FxTwitter should not be called")),
    )

    result = asyncio.run(twitter.fetch_twitter("https://x.com/iBigQiang/status/2071053794977423489"))

    assert result["text"] == "GraphQL full tweet"
    assert seen == ["bad" * 20, "good" * 10]
