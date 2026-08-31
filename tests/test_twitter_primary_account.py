# -*- coding: utf-8 -*-
"""Tests for 主账号优先机制。

书签等**账号私有数据**只能由收藏它的那个账号读取，轮换到备用号
（x_2.json ...）在语义上无解：要么读到那个账号自己的数据，要么直接
权限错误。这组用例锁住「主账号优先、无主账号即硬失败」的行为。
"""

import json
import pytest
from unittest.mock import patch

from feedgrab.fetchers import twitter_cookies


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    twitter_cookies._rate_limited_accounts.clear()
    twitter_cookies._current_account_key = ""
    yield
    twitter_cookies._rate_limited_accounts.clear()
    twitter_cookies._current_account_key = ""


def _ck(token: str) -> dict:
    """合法 cookie dict（auth_token + ct0 均 >= 20 chars）。"""
    return {"auth_token": token, "ct0": "c" * 32}


# --------------------------------------------------------------------------
# 主账号 / 备用号 判定
# --------------------------------------------------------------------------

def test_primary_labels_recognised():
    for label in (
        "env",
        "playwright(twitter.json)",
        "cookie_file(x.json)",
        "chrome_cdp",
    ):
        assert twitter_cookies.is_primary_cookie_label(label), label


def test_numbered_files_are_spares_not_primary():
    for label in (
        "cookie_file(x_2.json)",
        "cookie_file(x_6.json)",
        "playwright(twitter_2.json)",
    ):
        assert not twitter_cookies.is_primary_cookie_label(label), label


# --------------------------------------------------------------------------
# load_primary_twitter_cookies
# --------------------------------------------------------------------------

def test_primary_loader_prefers_twitter_json_over_spares():
    """备用号排在前面也不影响：只认主账号。"""
    sets = [
        ("cookie_file(x_2.json)", _ck("s2" * 20)),
        ("playwright(twitter.json)", _ck("p1" * 20)),
        ("cookie_file(x_3.json)", _ck("s3" * 20)),
    ]
    with patch.object(twitter_cookies, "_load_all_cookie_sets", return_value=sets):
        got = twitter_cookies.load_primary_twitter_cookies()
    assert got["auth_token"].startswith("p1")


def test_primary_loader_returns_empty_when_only_spares():
    """只有备用号时必须返回空 —— 绝不静默降级。"""
    sets = [
        ("cookie_file(x_2.json)", _ck("s2" * 20)),
        ("cookie_file(x_3.json)", _ck("s3" * 20)),
    ]
    with patch.object(twitter_cookies, "_load_all_cookie_sets", return_value=sets):
        got = twitter_cookies.load_primary_twitter_cookies()
    assert got == {}


# --------------------------------------------------------------------------
# fetch_with_cookie_rotation(primary_only=True)
# --------------------------------------------------------------------------

def test_primary_only_never_rotates_to_spares():
    sets = [
        ("playwright(twitter.json)", _ck("p1" * 20)),
        ("cookie_file(x_2.json)", _ck("s2" * 20)),
        ("cookie_file(x_3.json)", _ck("s3" * 20)),
    ]
    calls = []

    def fake_fetch(cookies=None, cursor=None):
        calls.append(cookies["auth_token"][:2])
        return None

    with patch.object(twitter_cookies, "_load_all_cookie_sets", return_value=sets):
        resp, used = twitter_cookies.fetch_with_cookie_rotation(
            fake_fetch, label="Bookmarks", cursor=None, primary_only=True,
        )

    assert resp is None
    assert calls == ["p1"], "primary_only 只允许主账号试一次"
    assert used["auth_token"].startswith("p1")


def test_primary_only_returns_primary_response():
    sets = [
        ("playwright(twitter.json)", _ck("p1" * 20)),
        ("cookie_file(x_2.json)", _ck("s2" * 20)),
    ]

    def fake_fetch(cookies=None, cursor=None):
        return {"data": {"who": cookies["auth_token"][:2]}}

    with patch.object(twitter_cookies, "_load_all_cookie_sets", return_value=sets):
        resp, used = twitter_cookies.fetch_with_cookie_rotation(
            fake_fetch, label="Bookmarks", cursor=None, primary_only=True,
        )

    assert resp == {"data": {"who": "p1"}}
    assert used["auth_token"].startswith("p1")


def test_primary_only_sends_nothing_when_no_primary():
    """无主账号时连请求都不该发出，避免拿备用号白跑。"""
    sets = [("cookie_file(x_2.json)", _ck("s2" * 20))]
    calls = []

    def fake_fetch(cookies=None, cursor=None):
        calls.append(1)
        return {"data": {"ok": True}}

    with patch.object(twitter_cookies, "_load_all_cookie_sets", return_value=sets):
        resp, used = twitter_cookies.fetch_with_cookie_rotation(
            fake_fetch, label="Bookmarks", cursor=None, primary_only=True,
        )

    assert resp is None
    assert used == {}
    assert calls == [], "不得用备用号代抓私有数据"


def test_primary_only_swallows_fetcher_exception():
    sets = [("playwright(twitter.json)", _ck("p1" * 20))]

    def boom(cookies=None, cursor=None):
        raise ValueError("网络异常")

    with patch.object(twitter_cookies, "_load_all_cookie_sets", return_value=sets):
        resp, used = twitter_cookies.fetch_with_cookie_rotation(
            boom, label="Bookmarks", cursor=None, primary_only=True,
        )

    assert resp is None
    assert used["auth_token"].startswith("p1")


def test_public_content_still_rotates_across_accounts():
    """回归：公开内容（不传 primary_only）保持多账号轮换。"""
    sets = [
        ("playwright(twitter.json)", _ck("p1" * 20)),
        ("cookie_file(x_2.json)", _ck("s2" * 20)),
    ]
    calls = []

    def fake_fetch(user_id, cookies=None, cursor=None):
        calls.append(cookies["auth_token"][:2])
        if cookies["auth_token"].startswith("p1"):
            twitter_cookies.mark_cookie_rate_limited(cookies)
            return None
        return {"data": {"ok": True}}

    with patch.object(twitter_cookies, "_load_all_cookie_sets", return_value=sets):
        resp, used = twitter_cookies.fetch_with_cookie_rotation(
            fake_fetch, "user_1", label="UserTweets", network_retry_delay=0,
        )

    assert resp == {"data": {"ok": True}}
    assert calls == ["p1", "s2"], "公开内容必须仍能轮换到备用号"


# --------------------------------------------------------------------------
# 游客态空壳 session
# --------------------------------------------------------------------------

def _write_session(path, token=""):
    """写一个 Playwright storage_state：token 为空则是游客态空壳。"""
    if token:
        cookies = [
            {"name": "auth_token", "value": token, "domain": ".x.com"},
            {"name": "ct0", "value": "c" * 32, "domain": ".x.com"},
        ]
    else:
        cookies = [{"name": "guest_id", "value": "v1%3A17", "domain": ".x.com"}]
    path.write_text(json.dumps({"cookies": cookies}), encoding="utf-8")


def test_guest_shell_session_does_not_shadow_valid_legacy(tmp_path):
    """空壳 twitter.json 曾遮蔽 legacy 有效 session，导致主账号被跳过。"""
    session_dir = tmp_path / "sessions"
    legacy_dir = tmp_path / "legacy"
    session_dir.mkdir()
    legacy_dir.mkdir()
    _write_session(session_dir / "twitter.json")
    _write_session(legacy_dir / "twitter.json", token="t" * 40)

    with patch.object(twitter_cookies, "get_session_dir", return_value=session_dir):
        with patch.object(twitter_cookies, "_LEGACY_SESSION_DIRS", [legacy_dir]):
            results = twitter_cookies._load_all_playwright_sessions()

    assert results, "legacy 有效 session 必须被迁移进来"
    assert results[0][1]["auth_token"] == "t" * 40


def test_valid_session_is_never_overwritten_by_legacy(tmp_path):
    """已有有效登录态时不得被 legacy 覆盖。"""
    session_dir = tmp_path / "sessions"
    legacy_dir = tmp_path / "legacy"
    session_dir.mkdir()
    legacy_dir.mkdir()
    _write_session(session_dir / "twitter.json", token="n" * 40)
    _write_session(legacy_dir / "twitter.json", token="o" * 40)

    with patch.object(twitter_cookies, "get_session_dir", return_value=session_dir):
        with patch.object(twitter_cookies, "_LEGACY_SESSION_DIRS", [legacy_dir]):
            results = twitter_cookies._load_all_playwright_sessions()

    assert results[0][1]["auth_token"] == "n" * 40


def test_guest_shell_session_logs_warning(tmp_path):
    """空壳被跳过时必须留下可诊断的 WARNING，而不是静默轮换。"""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    _write_session(session_dir / "twitter.json")

    records = []
    sink_id = twitter_cookies.logger.add(
        lambda msg: records.append(msg.record), level="WARNING", format="{message}"
    )
    try:
        with patch.object(twitter_cookies, "get_session_dir", return_value=session_dir):
            with patch.object(twitter_cookies, "_LEGACY_SESSION_DIRS", []):
                results = twitter_cookies._load_all_playwright_sessions()
    finally:
        twitter_cookies.logger.remove(sink_id)

    assert results == []
    joined = " ".join(r["message"] for r in records)
    assert "twitter.json" in joined
    assert "游客态空壳" in joined


# --------------------------------------------------------------------------
# likes / favoriters —— 实测（2026-08-31）确认的「本人限定」渠道
#
# 交叉对照结果（主账号 = @iBigQiang，小号 = x_2.json）：
#   Likes(自己)      主号 OK 20 / 小号 EMPTY   -> 边界是「账号本人」
#   Likes(别人)      主号 EMPTY / 小号 EMPTY
#   Favoriters(自己推文) 主号 OK 20 / 小号 EMPTY -> 边界是「推文作者本人」
#   Favoriters(别人推文,12817赞) 主号 EMPTY / 小号 EMPTY
#   Retweeters(别人推文,995转)   主号 OK 20 / 小号 OK 20 -> 公开，保持轮换
# 所以轮换对前两者只会丢掉唯一可能成功的账号。
# --------------------------------------------------------------------------

def _spy_rotation(monkeypatch, response):
    """替换 fetch_with_cookie_rotation，记录它收到的 kwargs。"""
    seen = {}

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return (response, {})

    monkeypatch.setattr(twitter_cookies, "fetch_with_cookie_rotation", spy)
    return seen


def _empty_user_timeline():
    """X 对「别人的 likes」返回的真实形态：200 + 空 timeline。"""
    return {"data": {"user": {"result": {"__typename": "User", "timeline": {}}}}}


def test_user_likes_pins_primary_account(monkeypatch, tmp_path):
    import asyncio
    from feedgrab.fetchers import twitter_user_tweets as ut

    seen = _spy_rotation(monkeypatch, _empty_user_timeline())
    monkeypatch.setattr(
        ut, "fetch_user_by_screen_name",
        lambda sn, ck: {"user_id": "1001044583273418752", "name": "Q", "screen_name": sn},
    )
    monkeypatch.setattr(ut, "_save_batch_record", lambda *a, **k: tmp_path / "rec.json")

    asyncio.run(ut.fetch_user_tweets(
        "https://x.com/iBigQiang", {"auth_token": "t", "ct0": "c"}, mode="likes",
    ))
    assert seen.get("primary_only") is True, "likes 必须钉在主账号上"


def test_user_tweets_and_replies_keep_rotating(monkeypatch, tmp_path):
    """回归：公开的 tweets / replies 不受影响，仍走轮换。"""
    import asyncio
    from feedgrab.fetchers import twitter_user_tweets as ut

    for mode in ("tweets", "replies"):
        seen = _spy_rotation(monkeypatch, {"data": {}})
        monkeypatch.setattr(
            ut, "fetch_user_by_screen_name",
            lambda sn, ck: {"user_id": "1", "name": "Q", "screen_name": sn},
        )
        monkeypatch.setattr(ut, "_save_batch_record", lambda *a, **k: tmp_path / "rec.json")

        asyncio.run(ut.fetch_user_tweets(
            "https://x.com/iBigQiang", {"auth_token": "t", "ct0": "c"}, mode=mode,
        ))
        assert seen.get("primary_only") is False, f"{mode} 应保持多账号轮换"


def _terminated_timeline(key):
    """X 对「别人推文的点赞者」返回的真实形态：立即 TimelineTerminateTimeline。"""
    return {
        "data": {
            key: {
                "timeline": {
                    "instructions": [
                        {"direction": "Bottom", "type": "TimelineTerminateTimeline"}
                    ]
                }
            }
        }
    }


def test_favoriters_pins_primary_account(monkeypatch):
    import asyncio
    from feedgrab.fetchers import twitter_retweeters as rt

    seen = _spy_rotation(monkeypatch, _terminated_timeline("favoriters_timeline"))
    monkeypatch.setattr(rt, "_save_outputs", lambda *a, **k: ("s.md", "s.csv"))

    asyncio.run(rt.fetch_tweet_user_list(
        "https://x.com/iBigQiang/status/2015088004109615266/likes",
        {"auth_token": "t", "ct0": "c"},
    ))
    assert seen.get("primary_only") is True, "点赞者必须钉在主账号上"


def test_retweeters_keep_rotating(monkeypatch):
    """回归：转推者实测对任何登录态都公开，必须保持轮换。"""
    import asyncio
    from feedgrab.fetchers import twitter_retweeters as rt

    seen = _spy_rotation(monkeypatch, _terminated_timeline("retweeters_timeline"))
    monkeypatch.setattr(rt, "_save_outputs", lambda *a, **k: ("s.md", "s.csv"))

    asyncio.run(rt.fetch_tweet_user_list(
        "https://x.com/iBigQiang/status/2015088004109615266/retweets",
        {"auth_token": "t", "ct0": "c"},
    ))
    assert seen.get("primary_only") is False, "转推者应保持多账号轮换"


def test_favoriters_empty_message_names_the_real_boundary(monkeypatch):
    """空结果必须说清「只有作者本人能看」，不能归因为「作者隐藏了列表」。"""
    import asyncio
    from feedgrab.fetchers import twitter_retweeters as rt

    _spy_rotation(monkeypatch, _terminated_timeline("favoriters_timeline"))
    monkeypatch.setattr(rt, "_save_outputs", lambda *a, **k: ("s.md", "s.csv"))

    records = []
    sink = rt.logger.add(
        lambda m: records.append(m.record), level="WARNING", format="{message}"
    )
    try:
        asyncio.run(rt.fetch_tweet_user_list(
            "https://x.com/iBigQiang/status/2015088004109615266/likes",
            {"auth_token": "t", "ct0": "c"},
        ))
    finally:
        rt.logger.remove(sink)

    joined = " ".join(r["message"] for r in records)
    assert "作者" in joined
    assert "任何账号都为空" in joined or "任何账号" in joined


# --------------------------------------------------------------------------
# reader 入口：缺主账号时必须立刻报错，不能拿备用号白跑一趟
# --------------------------------------------------------------------------

def _no_primary(monkeypatch):
    """模拟「只有备用号、没有主账号」。"""
    from feedgrab.fetchers import twitter_cookies as tcm
    monkeypatch.setattr(tcm, "load_primary_twitter_cookies", lambda: {})


def test_read_user_likes_raises_without_primary(monkeypatch):
    import asyncio
    from feedgrab import reader as rd
    from feedgrab import config

    monkeypatch.setattr(config, "x_user_likes_enabled", lambda: True)
    _no_primary(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(rd.UniversalReader()._read_user_likes("https://x.com/iBigQiang/likes"))
    msg = str(exc.value)
    assert "sessions/twitter.json" in msg
    assert "login twitter" in msg
    assert "私有化" in msg, "必须解释为什么备用号没用"


def test_read_tweet_user_list_favoriters_raises_without_primary(monkeypatch):
    import asyncio
    from feedgrab import reader as rd
    from feedgrab import config

    monkeypatch.setattr(config, "x_tweet_user_list_enabled", lambda: True)
    _no_primary(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(rd.UniversalReader()._read_tweet_user_list(
            "https://x.com/iBigQiang/status/2015088004109615266/likes"
        ))
    msg = str(exc.value)
    assert "sessions/twitter.json" in msg
    assert "作者本人" in msg


def test_read_tweet_user_list_retweeters_ignores_primary(monkeypatch):
    """回归：转推者是公开的，没有主账号也该照常用备用号跑。"""
    import asyncio
    from feedgrab import reader as rd
    from feedgrab import config
    from feedgrab.fetchers import twitter_cookies as tcm
    from feedgrab.fetchers import twitter_retweeters as rt

    monkeypatch.setattr(config, "x_tweet_user_list_enabled", lambda: True)
    _no_primary(monkeypatch)
    monkeypatch.setattr(tcm, "load_twitter_cookies", lambda: _ck("s2" * 20))

    called = {}

    async def fake_fetch(url, cookies):
        called["token"] = cookies["auth_token"][:2]
        return {"mode": "retweeters", "tweet_id": "1", "total": 0}

    monkeypatch.setattr(rt, "fetch_tweet_user_list", fake_fetch)

    asyncio.run(rd.UniversalReader()._read_tweet_user_list(
        "https://x.com/iBigQiang/status/2015088004109615266/retweets"
    ))
    assert called["token"] == "s2", "转推者应能用备用号抓取"
