"""Tests for the Xquik paid X/Twitter provider."""

import json
from types import SimpleNamespace

import pytest

from feedgrab.config import x_api_provider, xquik_api_base_url, xquik_api_key
from feedgrab.fetchers import twitter_api_user_tweets, xquik_api
from feedgrab.fetchers.twitter_bookmarks import _classify_tweet
from feedgrab.fetchers.twitter_paid_provider import (
    PaidXProvider,
    configured_paid_provider_name,
    load_paid_provider,
)


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_xquik_config_normalizes_provider_and_base_url(monkeypatch):
    monkeypatch.setenv("X_API_PROVIDER", " XQUIK ")
    monkeypatch.setenv("XQUIK_API_KEY", " test-key ")
    monkeypatch.setenv("XQUIK_API_BASE_URL", "https://example.test/api/v1/")

    assert x_api_provider() == "xquik"
    assert xquik_api_key() == "test-key"
    assert xquik_api_base_url() == "https://example.test/api/v1"


def test_xquik_search_uses_current_public_parameters(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        return FakeResponse({"tweets": [], "has_next_page": False, "next_cursor": ""})

    monkeypatch.setenv("XQUIK_API_KEY", "test-key")
    monkeypatch.setattr(xquik_api.http_client, "get", fake_get)

    result = xquik_api.search_tweets(
        "from:xquikcom",
        cursor="next",
        since_date="2026-08-01",
        until_date="2026-08-31",
    )

    assert result == {"tweets": [], "has_next_page": False, "next_cursor": ""}
    assert captured["url"] == "https://xquik.com/api/v1/x/tweets/search"
    assert captured["kwargs"]["headers"] == {"x-api-key": "test-key"}
    assert captured["kwargs"]["params"] == {
        "q": "from:xquikcom",
        "queryType": "Latest",
        "limit": 200,
        "cursor": "next",
        "sinceDate": "2026-08-01",
        "untilDate": "2026-08-31",
    }


def test_xquik_user_tweets_encode_path_and_use_page_size(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        return FakeResponse({"tweets": [], "has_next_page": False, "next_cursor": ""})

    monkeypatch.setenv("XQUIK_API_KEY", "test-key")
    monkeypatch.setattr(xquik_api.http_client, "get", fake_get)

    xquik_api.get_user_last_tweets(
        "name/escape",
        cursor="cursor-1",
        include_replies=True,
        since_date="2026-08-01",
    )

    assert captured["url"].endswith("/x/users/name%2Fescape/tweets")
    assert captured["kwargs"]["params"] == {
        "includeReplies": True,
        "pageSize": 200,
        "cursor": "cursor-1",
        "sinceDate": "2026-08-01",
    }
    assert twitter_api_user_tweets._timeline_tweets({"tweets": [{"id": "xquik"}]}) == [
        {"id": "xquik"}
    ]
    assert twitter_api_user_tweets._timeline_tweets(
        {"data": {"tweets": [{"id": "twitterapi"}]}}
    ) == [{"id": "twitterapi"}]


def test_xquik_rate_limit_honors_retry_after(monkeypatch):
    responses = [
        FakeResponse({}, 429, {"Retry-After": "7"}),
        FakeResponse({"tweets": [], "has_next_page": False, "next_cursor": ""}),
    ]
    sleeps = []
    monkeypatch.setenv("XQUIK_API_KEY", "test-key")
    monkeypatch.setattr(
        xquik_api.http_client, "get", lambda *_args, **_kwargs: responses.pop(0)
    )
    monkeypatch.setattr(xquik_api.time, "sleep", sleeps.append)

    result = xquik_api.search_tweets("from:xquikcom")

    assert result["has_next_page"] is False
    assert sleeps == [7.0]


def test_xquik_payment_error_does_not_retry(monkeypatch):
    monkeypatch.setenv("XQUIK_API_KEY", "test-key")
    monkeypatch.setattr(
        xquik_api.http_client,
        "get",
        lambda *_args, **_kwargs: FakeResponse({"error": "payment_required"}, 402),
    )

    with pytest.raises(RuntimeError, match="余额不足"):
        xquik_api.search_tweets("from:xquikcom")


def test_parse_xquik_tweet_preserves_media_and_metadata():
    parsed = xquik_api.parse_api_tweet(
        {
            "id": "123",
            "text": "release https://t.co/link",
            "url": "https://x.com/xquikcom/status/123",
            "createdAt": "2026-08-30T12:00:00Z",
            "conversationId": "100",
            "inReplyToId": "99",
            "inReplyToUserId": "42",
            "isReply": True,
            "likeCount": 7,
            "retweetCount": 2,
            "replyCount": 1,
            "quoteCount": 4,
            "bookmarkCount": 3,
            "viewCount": 400,
            "lang": "en",
            "source": "Twitter Web App",
            "possiblySensitive": True,
            "article": {"title": "Release notes"},
            "author": {
                "id": "42",
                "username": "xquikcom",
                "name": "Xquik",
                "isBlueVerified": True,
                "followers": 10,
                "statusesCount": 20,
            },
            "entities": {
                "hashtags": [{"text": "release"}],
                "urls": [
                    {
                        "url": "https://t.co/link",
                        "expandedUrl": "https://example.test/release",
                    }
                ],
            },
            "media": [
                {"type": "photo", "mediaUrl": "https://img.test/photo.jpg"},
                {
                    "type": "video",
                    "mediaUrl": "https://img.test/preview.jpg",
                    "videoVariants": [
                        {
                            "contentType": "video/mp4",
                            "bitrate": 256000,
                            "url": "https://video.test/low.mp4",
                        },
                        {
                            "contentType": "video/mp4",
                            "bitrate": 832000,
                            "url": "https://video.test/high.mp4",
                        },
                    ],
                },
            ],
            "quoted_tweet": {
                "id": "456",
                "text": "quoted",
                "url": "https://x.com/quoted/status/456",
                "author": {"username": "quoted", "name": "Quoted"},
                "media": [{"type": "photo", "mediaUrl": "https://img.test/quoted.jpg"}],
            },
        }
    )

    assert parsed["text"] == "release https://example.test/release"
    assert parsed["author"] == "xquikcom"
    assert parsed["conversation_id"] == "100"
    assert parsed["images"] == ["https://img.test/photo.jpg"]
    assert parsed["videos"] == ["https://video.test/high.mp4"]
    assert parsed["quoted_tweet"]["images"] == ["https://img.test/quoted.jpg"]
    assert parsed["article"] == {
        "title": "Release notes",
        "has_content": True,
    }
    assert _classify_tweet(parsed) == "article"
    assert parsed["quote_count"] == 4
    assert parsed["is_blue_verified"] is True
    assert parsed["possibly_sensitive"] is True


def test_paid_provider_selection_respects_explicit_choice(monkeypatch):
    monkeypatch.setenv("X_API_PROVIDER", "xquik")
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "twitter-key")

    assert configured_paid_provider_name() == "xquik"
    assert load_paid_provider().provider_id == "xquik"


def test_paid_provider_selection_uses_configured_supplementary_key(monkeypatch):
    monkeypatch.setenv("X_API_PROVIDER", "graphql")
    monkeypatch.delenv("TWITTERAPI_IO_KEY", raising=False)
    monkeypatch.setenv("XQUIK_API_KEY", "xquik-key")

    assert configured_paid_provider_name() == "xquik"


def test_xquik_discovery_follows_empty_page_and_reuses_complete_cache(
    monkeypatch,
    tmp_path,
):
    calls = []
    responses = [
        {"tweets": [], "has_next_page": True, "next_cursor": "cursor-1"},
        {
            "tweets": [{"id": "123", "createdAt": "2026-08-20T12:00:00Z"}],
            "has_next_page": False,
            "next_cursor": "",
        },
    ]

    def search_tweets(query, **kwargs):
        calls.append((query, kwargs))
        return responses.pop(0)

    module = SimpleNamespace(
        search_tweets=search_tweets,
        get_user_last_tweets=lambda *_args, **_kwargs: None,
        parse_api_tweet=lambda raw: {
            "id": raw["id"],
            "created_at": raw["createdAt"],
        },
    )
    provider = PaidXProvider("xquik", "Xquik", "xquik", module)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(twitter_api_user_tweets.time, "sleep", lambda _seconds: None)

    result = twitter_api_user_tweets._discover_tweets_via_search(
        "xquikcom",
        since_date="2026-08-01",
        until_date="2026-08-31",
        provider=provider,
    )

    assert result == [{"id": "123", "created_at": "2026-08-20T12:00:00Z"}]
    assert calls[0][1]["cursor"] == ""
    assert calls[1][1]["cursor"] == "cursor-1"
    cache_path = (
        tmp_path
        / "X"
        / "index"
        / ".xquik_discovery_xquikcom_since_2026-08-01_until_2026-08-31.jsonl"
    )
    cache_entries = [json.loads(line) for line in cache_path.read_text().splitlines()]
    assert {"_cursor": "cursor-1"} in cache_entries
    assert {"_status": "complete"} in [
        {"_status": entry["_status"]} for entry in cache_entries if "_status" in entry
    ]

    module.search_tweets = lambda *_args, **_kwargs: pytest.fail(
        "complete cache must not call API"
    )
    assert (
        twitter_api_user_tweets._discover_tweets_via_search(
            "xquikcom",
            since_date="2026-08-01",
            until_date="2026-08-31",
            provider=provider,
        )
        == result
    )


def test_twitterapi_discovery_uses_initial_max_id(monkeypatch, tmp_path):
    queries = []

    def search_tweets(query, **_kwargs):
        queries.append(query)
        return {"tweets": [], "has_next_page": False, "next_cursor": ""}

    module = SimpleNamespace(
        search_tweets=search_tweets,
        get_user_last_tweets=lambda *_args, **_kwargs: None,
        parse_api_tweet=lambda raw: raw,
    )
    provider = PaidXProvider("api", "TwitterAPI.io", "api", module)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))

    twitter_api_user_tweets._discover_tweets_via_search(
        "example",
        initial_max_id=456,
        provider=provider,
    )

    assert queries == ["from:example max_id:456"]
