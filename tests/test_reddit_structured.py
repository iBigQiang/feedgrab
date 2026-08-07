# -*- coding: utf-8 -*-

import asyncio

from feedgrab.fetchers import reddit
from feedgrab.fetchers import jina


def test_render_post_exposes_structured_body_and_comments():
    post = {
        "id": "abc123",
        "title": "Structured Reddit",
        "author": "alice",
        "subreddit": "python",
        "score": 42,
        "upvote_ratio": 0.91,
        "num_comments": 2,
        "link_flair_text": "Discussion",
        "is_self": True,
        "permalink": "/r/python/comments/abc123/structured/",
        "selftext_html": "<p>Hello <strong>world</strong></p>",
        "created_utc": 1760000000,
    }
    comments = [
        {
            "id": "c1",
            "author": "bob",
            "body_html": "<p>Useful <em>comment</em></p>",
            "score": 7,
            "created_utc": 1760000100,
            "permalink": "/r/python/comments/abc123/_/c1/",
        }
    ]

    result = reddit._render_post(post, comments)

    assert result["body"] == "Hello **world**"
    assert result["fetch_status"] == "ok"
    assert result["comments"] == [
        {
            "id": "c1",
            "author": "bob",
            "body": "Useful *comment*",
            "score": 7,
            "created_at": "2025-10-09T08:55:00Z",
            "permalink": "/r/python/comments/abc123/_/c1/",
        }
    ]
    assert "Hello **world**" in result["content"]
    assert "Useful *comment*" in result["content"]


def test_reddit_exposes_structured_fetch_entrypoints():
    assert callable(getattr(reddit, "fetch_reddit_structured", None))
    assert callable(getattr(reddit, "fetch_reddit_subreddit_structured", None))


def test_jina_fallback_reports_partial_structured_result(monkeypatch):
    monkeypatch.setattr(reddit, "_fetch_json_direct", lambda _url: None)

    async def no_browser_result(_url):
        return None

    monkeypatch.setattr(reddit, "_fetch_json_via_browser", no_browser_result)
    monkeypatch.setattr(
        jina,
        "fetch_via_jina",
        lambda _url: {"title": "Fallback", "content": "Fallback body"},
    )

    result = asyncio.run(
        reddit.fetch_reddit_structured(
            "https://www.reddit.com/r/python/comments/abc123/fallback/"
        )
    )

    assert result["body"] == "Fallback body"
    assert result["comments"] == []
    assert result["fetch_status"] == "jina_fallback"
