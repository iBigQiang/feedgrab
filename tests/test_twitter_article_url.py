"""X Article URL body-fetch regressions."""

from feedgrab.fetchers import twitter
from feedgrab.fetchers import twitter_bookmarks
from feedgrab.fetchers import jina


def test_article_stub_fetches_body_from_explicit_i_article_url(monkeypatch):
    """A post containing an Article URL must fetch that article, not its status page."""
    article_url = "https://x.com/i/article/2081952588040409088"
    article_body = "长文章正文。" * 120

    def fake_fetch_via_jina(url):
        if url == article_url:
            return {"content": article_body}
        return {"content": ""}

    monkeypatch.setattr(jina, "fetch_via_jina", fake_fetch_via_jina)
    monkeypatch.setattr(twitter_bookmarks.time, "sleep", lambda _: None)
    data = {
        "text": article_url,
        "author": "@writer",
        "article_data": {},
        "videos": [],
    }

    twitter._try_fetch_article_body(
        data,
        "https://x.com/writer/status/2081952588040409088",
        "[Test]",
    )

    assert data["text"] == article_body


def test_article_body_uses_graphql_article_id_as_canonical_url(monkeypatch):
    """Batch paths can resolve Article bodies even when tweet text has no URL."""
    article_id = "2081952588040409088"
    article_url = f"https://x.com/i/article/{article_id}"
    article_body = "GraphQL 长文章正文。" * 80

    monkeypatch.setattr(
        jina,
        "fetch_via_jina",
        lambda url: {"content": article_body if url == article_url else ""},
    )

    body = twitter_bookmarks._fetch_article_body(
        "https://x.com/writer/status/2081952588040409088",
        {"id": article_id},
        "writer",
        "[Test]",
    )

    assert body == article_body
