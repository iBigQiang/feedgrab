# -*- coding: utf-8 -*-
"""WeChat MP account batch fetch tests."""

import asyncio

import pytest


def _install_browser_fakes(monkeypatch, page):
    """Wire minimal Playwright fakes so batch fetch runs without a real browser."""

    class FakeContext:
        async def new_page(self):
            return page

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

    monkeypatch.setattr(
        "feedgrab.fetchers.browser.get_async_playwright", lambda: FakePlaywright)
    monkeypatch.setattr(
        "feedgrab.fetchers.browser.stealth_launch", fake_stealth_launch)
    monkeypatch.setattr(
        "feedgrab.fetchers.browser.get_stealth_context_options", lambda: {})
    monkeypatch.setattr(
        "feedgrab.fetchers.browser.setup_resource_blocking", fake_setup_resource_blocking)


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


# ---------------------------------------------------------------------------
# ret=200013 freq control — a rate-limited run must never look like a finished one
# ---------------------------------------------------------------------------

def test_fetch_article_list_raises_on_freq_control():
    """ret=200013 used to be swallowed as 'listing exhausted' → 'fetched 0, done'."""
    from feedgrab.fetchers import mpweixin_account

    class FakePage:
        async def evaluate(self, _script, _params):
            return {"base_resp": {"ret": 200013, "err_msg": "freq control"}}

    with pytest.raises(mpweixin_account.MPWeixinFreqControlError) as excinfo:
        asyncio.run(mpweixin_account._fetch_article_list(FakePage(), "fakeid"))

    message = str(excinfo.value)
    assert "200013" in message
    assert "freq control" in message
    assert "进度均已保留" in message


def test_fetch_article_list_surfaces_unknown_error_with_err_msg():
    """Unknown backend errors must carry err_msg instead of ending the listing."""
    from feedgrab.fetchers import mpweixin_account

    class FakePage:
        async def evaluate(self, _script, _params):
            return {"base_resp": {"ret": 200002, "err_msg": "invalid args"}}

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(mpweixin_account._fetch_article_list(FakePage(), "fakeid"))

    message = str(excinfo.value)
    assert "200002" in message
    assert "invalid args" in message


def test_freq_control_keeps_progress_and_does_not_report_success(monkeypatch, tmp_path):
    """A rate-limited run keeps its progress file so the next run can resume."""
    from feedgrab.fetchers import mpweixin_account

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "wechat.json").write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    class FakePage:
        url = "https://mp.weixin.qq.com/cgi-bin/home?lang=zh_CN&token=123456"

        async def goto(self, *args, **kwargs):
            return None

        async def wait_for_timeout(self, *args, **kwargs):
            return None

        async def evaluate(self, script, _params=None):
            if "searchbiz" in script:
                return {
                    "base_resp": {"ret": 0},
                    "list": [{"nickname": "老码小张", "fakeid": "fid"}],
                }
            return {"base_resp": {"ret": 200013, "err_msg": "freq control"}}

    _install_browser_fakes(monkeypatch, FakePage())

    cleared = []
    monkeypatch.setattr(mpweixin_account, "get_session_dir", lambda: session_dir)
    monkeypatch.setattr(mpweixin_account, "load_index", lambda platform: {})
    monkeypatch.setattr(mpweixin_account, "save_index", lambda index, platform: None)
    monkeypatch.setattr(mpweixin_account, "_load_progress", lambda name: {})
    monkeypatch.setattr(mpweixin_account, "_save_progress", lambda name, data: None)
    monkeypatch.setattr(mpweixin_account, "_clear_progress", lambda name: cleared.append(name))

    with pytest.raises(mpweixin_account.MPWeixinFreqControlError):
        asyncio.run(mpweixin_account.fetch_account_articles("老码小张", delay=0))

    assert cleared == [], "限流中断不得清除分页进度，否则剩余文章会被永久跳过"


def test_empty_page_is_not_treated_as_end_of_listing(monkeypatch, tmp_path):
    """A page whose publish records yield no appmsgex is empty, not the end.

    WeChat only signals exhaustion with an empty publish_list.  Treating any
    article-less page as "done" would clear the progress file and silently
    strand every later article.
    """
    from feedgrab.fetchers import mpweixin_account

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "wechat.json").write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    class FakePage:
        url = "https://mp.weixin.qq.com/cgi-bin/home?lang=zh_CN&token=123456"

        async def goto(self, *args, **kwargs):
            return None

        async def wait_for_timeout(self, *args, **kwargs):
            return None

        async def evaluate(self, script, _params=None):
            if "searchbiz" in script:
                return {
                    "base_resp": {"ret": 0},
                    "list": [{"nickname": "老码小张", "fakeid": "fid"}],
                }
            return {}

    _install_browser_fakes(monkeypatch, FakePage())

    calls = []

    async def fake_list(page, fakeid, begin=0, size=20):
        calls.append(begin)
        # 第一页有群发记录但解不出图文；第二页 publish_list 为空才是真末尾
        return ([], False, 10) if begin == 0 else ([], True, 10)

    cleared = []
    monkeypatch.setattr(mpweixin_account, "_fetch_article_list", fake_list)
    monkeypatch.setattr(mpweixin_account, "get_session_dir", lambda: session_dir)
    monkeypatch.setattr(mpweixin_account, "load_index", lambda platform: {})
    monkeypatch.setattr(mpweixin_account, "save_index", lambda index, platform: None)
    monkeypatch.setattr(mpweixin_account, "_load_progress", lambda name: {})
    monkeypatch.setattr(mpweixin_account, "_save_progress", lambda name, data: None)
    monkeypatch.setattr(mpweixin_account, "_clear_progress", lambda name: cleared.append(name))
    monkeypatch.setenv("MPWEIXIN_ID_PAGE_DELAY", "0")

    result = asyncio.run(mpweixin_account.fetch_account_articles("老码小张", delay=0))

    assert calls == [0, 20], "无图文的一页应继续翻页，不能当成列表读完"
    assert cleared == ["老码小张"], "只有真正读到列表末尾才清进度"
    assert result["interrupted"] == ""


# ---------------------------------------------------------------------------
# Placeholder pages — deleted / violation / privacy / risk control
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,text,url,expected", [
    ("微信公众平台", "环境异常\n\n当前环境异常，完成验证后即可继续访问。\n\n去验证", "", "captcha"),
    ("微信公众平台", "该内容已被发布者删除\n\n微信公众平台运营中心", "", "deleted"),
    ("微信公众平台", "此内容因违规无法查看\n\n接相关投诉，此内容违反...", "", "violation"),
    ("微信公众平台", "此内容发送失败无法查看", "", "violation"),
    ("微信公众平台", "根据作者隐私设置，无法查看该内容", "", "privacy"),
    # A real article never reaches the detector, and unknown phrasing must not
    # be guessed at — both degrade to the previous save-as-is behaviour.
    ("正常文章标题", "这是一篇正常文章的正文", "", ""),
    ("微信公众平台", "微信将来可能改的新文案", "", ""),
    # The risk-control URL is conclusive on its own.
    ("任意标题", "任意正文", "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=x", "captcha"),
])
def test_detect_wechat_unavailable(title, text, url, expected):
    from feedgrab.fetchers.browser import detect_wechat_unavailable

    assert detect_wechat_unavailable(title, text, url) == expected


def test_record_unavailable_keeps_captcha_retryable():
    """Risk control is transient — stay out of dedup so a later run retries."""
    from feedgrab.fetchers import mpweixin_account

    result = {"fetched": 0, "skipped": 0, "failed": 0}
    index = {}

    mpweixin_account._record_unavailable(
        "captcha", "标题", "https://mp.weixin.qq.com/s/abc", "item1", result, index,
    )

    assert result["failed"] == 1
    assert result["skipped"] == 0
    assert index == {}, "风控验证页不得写入去重索引，否则重跑会被跳过"


@pytest.mark.parametrize("reason", ["deleted", "violation", "privacy"])
def test_record_unavailable_indexes_permanently_gone_content(reason):
    """Content that is gone for good is indexed so runs stop re-opening it."""
    from feedgrab.fetchers import mpweixin_account

    result = {"fetched": 0, "skipped": 0, "failed": 0}
    index = {}

    mpweixin_account._record_unavailable(
        reason, "标题", "https://mp.weixin.qq.com/s/abc", "item1", result, index,
    )

    assert result["skipped"] == 1
    assert result["failed"] == 0
    assert "item1" in index


# ---------------------------------------------------------------------------
# Pagination throttling
# ---------------------------------------------------------------------------

def test_page_sleep_stays_within_jitter_band(monkeypatch):
    from feedgrab.fetchers import mpweixin_account

    monkeypatch.setenv("MPWEIXIN_ID_PAGE_DELAY", "10")
    monkeypatch.setenv("MPWEIXIN_ID_PAGE_JITTER", "0.4")

    values = [mpweixin_account._page_sleep_seconds() for _ in range(200)]

    assert all(6.0 <= v <= 14.0 for v in values)
    assert len(set(values)) > 1, "翻页间隔应带随机抖动，不能是固定节奏"


def test_page_size_defaults_to_20_and_is_capped(monkeypatch):
    """Fewer list calls is the only lever against a request-counted quota."""
    from feedgrab.config import mpweixin_id_page_size

    monkeypatch.delenv("MPWEIXIN_ID_PAGE_SIZE", raising=False)
    assert mpweixin_id_page_size() == 20

    monkeypatch.setenv("MPWEIXIN_ID_PAGE_SIZE", "500")
    assert mpweixin_id_page_size() == 20

    monkeypatch.setenv("MPWEIXIN_ID_PAGE_SIZE", "0")
    assert mpweixin_id_page_size() == 1


def test_freq_retry_defaults_to_no_retry(monkeypatch):
    """Retrying inside a run only spends more of the same blocked quota."""
    from feedgrab.config import mpweixin_id_freq_retry

    monkeypatch.delenv("MPWEIXIN_ID_FREQ_RETRY", raising=False)
    assert mpweixin_id_freq_retry() == 0
