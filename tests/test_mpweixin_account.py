# -*- coding: utf-8 -*-
"""WeChat MP account batch fetch tests."""

import asyncio

import pytest


def test_mpweixin_account_requires_exact_account_match():
    from feedgrab.fetchers import mpweixin_account

    class FakePage:
        async def evaluate(self, _script, _account_name):
            return {
                "base_resp": {"ret": 0},
                "list": [
                    {"nickname": "强子的学习手记", "fakeid": "first"},
                    {"nickname": "强子的商家运营手记", "fakeid": "second"},
                ],
            }

    account = asyncio.run(mpweixin_account._find_account(FakePage(), "强子手记"))

    assert account is None


def test_mpweixin_account_preserves_session_expired_error(monkeypatch, tmp_path):
    """Session errors before pagination should not be masked by cleanup state."""
    from feedgrab.fetchers import mpweixin_account

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "wechat.json").write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    class FakePage:
        url = "https://mp.weixin.qq.com/"

        async def goto(self, *args, **kwargs):
            return None

        async def wait_for_timeout(self, *args, **kwargs):
            return None

    class FakeContext:
        async def new_page(self):
            return FakePage()

        async def close(self):
            return None

    class FakeBrowser:
        async def new_context(self, **kwargs):
            return FakeContext()

        async def close(self):
            return None

    class FakePlaywright:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_stealth_launch(*args, **kwargs):
        return FakeBrowser()

    async def fake_setup_resource_blocking(*args, **kwargs):
        return None

    monkeypatch.setattr(mpweixin_account, "get_session_dir", lambda: session_dir)
    monkeypatch.setattr(mpweixin_account, "load_index", lambda platform: {})
    monkeypatch.setattr(mpweixin_account, "save_index", lambda index, platform: None)
    monkeypatch.setattr(mpweixin_account, "_load_progress", lambda account_name: {})
    monkeypatch.setattr(mpweixin_account, "_clear_progress", lambda account_name: None)
    monkeypatch.setattr(
        "feedgrab.fetchers.browser.get_async_playwright",
        lambda: FakePlaywright,
    )
    monkeypatch.setattr(
        "feedgrab.fetchers.browser.stealth_launch",
        fake_stealth_launch,
    )
    monkeypatch.setattr(
        "feedgrab.fetchers.browser.get_stealth_context_options",
        lambda: {},
    )
    monkeypatch.setattr(
        "feedgrab.fetchers.browser.setup_resource_blocking",
        fake_setup_resource_blocking,
    )

    with pytest.raises(RuntimeError, match="微信公众号后台登录态已过期或无效"):
        asyncio.run(mpweixin_account.fetch_account_articles("林月半子的AI笔记", delay=0))


def test_mpweixin_account_missing_session_error_is_chinese(monkeypatch, tmp_path):
    from feedgrab.fetchers import mpweixin_account

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()

    monkeypatch.setattr(mpweixin_account, "get_session_dir", lambda: session_dir)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(mpweixin_account.fetch_account_articles("老码小张", delay=0))

    message = str(excinfo.value)
    assert "微信公众号后台登录态文件不存在" in message
    assert "'feedgrab login wechat'" in message
