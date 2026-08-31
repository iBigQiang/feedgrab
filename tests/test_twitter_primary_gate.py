# -*- coding: utf-8 -*-
"""主账号闸门的入口一致性 + 限流归因回归。

`test_twitter_primary_account.py` 锁的是「哪些渠道钉主账号」这条策略本身；
这里锁的是策略的**入口覆盖面**和**失败归因**：

- 子域形态（`www.` / `mobile.`）不能绕过闸门；
- 闸门判定只有一个来源（`mode_requires_primary` ← `_MODE_CONFIG`）；
- CLI 入口与 reader 入口必须同一套闸门，不能静默「总数：0」；
- 主账号被限流时不能误诊成「登录态过期」；
- 主账号白名单锁的是**生成侧真实产出**的 label，不是手写字面量。
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


def _no_primary(monkeypatch):
    """模拟「只有备用号、没有主账号」。"""
    monkeypatch.setattr(twitter_cookies, "load_primary_twitter_cookies", lambda: {})


def _write_session(path, token: str):
    """写一个 Playwright storage_state。"""
    path.write_text(
        json.dumps({
            "cookies": [
                {"name": "auth_token", "value": token, "domain": ".x.com"},
                {"name": "ct0", "value": "c" * 32, "domain": ".x.com"},
            ]
        }),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# 子域形态 + 策略单一来源
#
# reader._detect_platform 只看 domain 里有没有 "x.com"，所以 www./mobile. 形态
# 会照常路由进来；但 parse_tweet_user_list_url 的正则原先写死裸域，这类 URL 解析
# 成 (None, None)，主账号闸门直接落空（随后 fetch_tweet_user_list 抛 ValueError，
# 功能整条不可用）。
# --------------------------------------------------------------------------

def test_tweet_user_list_url_accepts_subdomains():
    from feedgrab.fetchers.twitter_retweeters import parse_tweet_user_list_url as parse

    assert parse("https://www.x.com/iBigQiang/status/2015088004109615266/likes") == (
        "favoriters", "2015088004109615266",
    )
    assert parse("https://mobile.twitter.com/iBigQiang/status/123/retweets") == (
        "retweeters", "123",
    )
    # 裸域照旧
    assert parse("https://x.com/iBigQiang/status/123/likes") == ("favoriters", "123")
    # 不是用户列表页就不该被认成用户列表页
    assert parse("https://x.com/iBigQiang/status/123") == (None, None)


def test_mode_requires_primary_is_single_source_of_truth():
    """闸门判定必须来自 _MODE_CONFIG，而不是各调用点重写 mode 名。"""
    from feedgrab.fetchers import twitter_retweeters as rt

    for mode, cfg in rt._MODE_CONFIG.items():
        assert rt.mode_requires_primary(mode) is bool(cfg["primary_only"]), mode
    # URL 没解析出 mode：交给调用方的通用 cookie 检查，下游必然 ValueError
    assert rt.mode_requires_primary(None) is False
    # 未知 mode 保守要主账号（宁可多要一次登录，不要把私有读散给备用号）
    assert rt.mode_requires_primary("some_future_mode") is True


def test_read_tweet_user_list_gate_covers_subdomain(monkeypatch):
    """www. 形态过去解析成 (None, None)，会绕过闸门落进轮换分支。"""
    import asyncio
    from feedgrab import reader as rd
    from feedgrab import config

    monkeypatch.setattr(config, "x_tweet_user_list_enabled", lambda: True)
    _no_primary(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(rd.UniversalReader()._read_tweet_user_list(
            "https://www.x.com/iBigQiang/status/2015088004109615266/likes"
        ))
    assert "sessions/twitter.json" in str(exc.value)


# --------------------------------------------------------------------------
# CLI 入口（x-favoriters / x-retweeters）—— 与 reader 必须同一套闸门
# --------------------------------------------------------------------------

def _patch_cli_env(monkeypatch, primary: dict, spare: dict):
    from feedgrab import config

    monkeypatch.setattr(config, "x_tweet_user_list_enabled", lambda: True)
    monkeypatch.setattr(twitter_cookies, "load_primary_twitter_cookies", lambda: primary)
    monkeypatch.setattr(twitter_cookies, "load_twitter_cookies", lambda: spare)


def _stub_cli_fetcher(monkeypatch, record: dict):
    from feedgrab.fetchers import twitter_retweeters as rt

    async def fake_fetch(spec, cookies):
        record["spec"] = spec
        record["token"] = cookies.get("auth_token", "")[:2]
        return {"total": 3, "summary_path": "s.md", "csv_path": "s.csv"}

    monkeypatch.setattr(rt, "fetch_tweet_user_list", fake_fetch)


def test_cli_favoriters_requires_primary(monkeypatch, capsys):
    """缺主账号时要给出与 reader 一致的中文指引，而不是静默「总数：0」。"""
    from feedgrab import cli

    _patch_cli_env(monkeypatch, primary={}, spare=_ck("s2" * 20))
    record = {}
    _stub_cli_fetcher(monkeypatch, record)

    cli.cmd_twitter_tweet_user_list(["2015088004109615266"], "favoriters")

    out = capsys.readouterr().out
    assert "sessions/twitter.json" in out
    assert "login twitter" in out
    assert "作者本人" in out
    assert record == {}, "缺主账号时不得用备用号发请求"


def test_cli_favoriters_uses_primary_not_spare(monkeypatch, capsys):
    from feedgrab import cli

    _patch_cli_env(monkeypatch, primary=_ck("p1" * 20), spare=_ck("s2" * 20))
    record = {}
    _stub_cli_fetcher(monkeypatch, record)

    cli.cmd_twitter_tweet_user_list(["2015088004109615266"], "favoriters")

    assert record["token"] == "p1", "点赞者必须用主账号"
    assert record["spec"] == "favoriters:2015088004109615266"
    assert "总数：3" in capsys.readouterr().out


def test_cli_retweeters_still_rotates(monkeypatch, capsys):
    """回归：转推者是公开的，没有主账号也该照常用备用号跑。"""
    from feedgrab import cli

    _patch_cli_env(monkeypatch, primary={}, spare=_ck("s2" * 20))
    record = {}
    _stub_cli_fetcher(monkeypatch, record)

    cli.cmd_twitter_tweet_user_list(
        ["https://x.com/iBigQiang/status/2015088004109615266"], "retweeters",
    )

    assert record["token"] == "s2"
    assert record["spec"] == "retweeters:2015088004109615266"
    assert "总数：3" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 主账号限流：不能把 15 分钟冷却误诊成「登录态过期」
#
# 轮换路径靠 load_twitter_cookies() 内部跳过限流账号；钉死单账号后没有可跳的
# 对象，必须显式查表，否则请求打出去必 429，回到上层只剩「无响应」一个信号。
# --------------------------------------------------------------------------

def test_cookie_rate_limit_remaining_is_per_account():
    ck = _ck("p1" * 20)
    assert twitter_cookies.cookie_rate_limit_remaining(ck) == 0
    assert twitter_cookies.cookie_rate_limit_remaining({}) == 0

    twitter_cookies.mark_cookie_rate_limited(ck)
    remaining = twitter_cookies.cookie_rate_limit_remaining(ck)
    assert 0 < remaining <= twitter_cookies.RATE_LIMIT_COOLDOWN
    assert twitter_cookies.cookie_rate_limit_remaining(_ck("s2" * 20)) == 0


def _primary_only_errors(sets, fetcher):
    """跑一次 primary_only 抓取，返回 (response, ERROR 日志文本, 调用次数)。"""
    calls = []

    def counted(cookies=None, cursor=None):
        calls.append(1)
        return fetcher(cookies=cookies, cursor=cursor)

    records = []
    sink = twitter_cookies.logger.add(
        lambda m: records.append(m.record), level="ERROR", format="{message}"
    )
    try:
        with patch.object(twitter_cookies, "_load_all_cookie_sets", return_value=sets):
            resp, _used = twitter_cookies.fetch_with_cookie_rotation(
                counted, label="Bookmarks", cursor=None, primary_only=True,
            )
    finally:
        twitter_cookies.logger.remove(sink)
    return resp, " ".join(r["message"] for r in records), len(calls)


def test_primary_only_skips_request_while_cooling_down():
    sets = [("playwright(twitter.json)", _ck("p1" * 20))]
    twitter_cookies.mark_cookie_rate_limited(_ck("p1" * 20))

    resp, errors, calls = _primary_only_errors(
        sets, lambda cookies=None, cursor=None: {"data": {"ok": True}}
    )

    assert resp is None
    assert calls == 0, "已知在冷却期就不该再发一次必然 429 的请求"
    assert "限流冷却" in errors
    assert "过期" not in errors, "不能把冷却说成登录态过期"


def test_primary_only_reports_rate_limit_not_dead_login():
    """本次请求触发 429 时，要说清登录态没问题、等冷却即可。"""
    sets = [("playwright(twitter.json)", _ck("p1" * 20))]

    def limited(cookies=None, cursor=None):
        twitter_cookies.mark_cookie_rate_limited(cookies)
        return None

    resp, errors, calls = _primary_only_errors(sets, limited)

    assert resp is None
    assert calls == 1
    assert "触发限流" in errors
    assert "登录态本身没问题" in errors
    assert "过期" not in errors


def test_primary_only_still_flags_expired_login():
    """回归：没被限流却拿不到响应，仍要提示检查登录态。"""
    sets = [("playwright(twitter.json)", _ck("p1" * 20))]

    resp, errors, calls = _primary_only_errors(
        sets, lambda cookies=None, cursor=None: None
    )

    assert resp is None
    assert calls == 1
    assert "过期" in errors
    assert "限流" not in errors


# --------------------------------------------------------------------------
# 白名单必须锁住「生成侧真实产出的 label」，而不是手写字面量
# --------------------------------------------------------------------------

def test_playwright_labels_match_primary_whitelist(tmp_path):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    _write_session(session_dir / "twitter.json", token="t" * 40)
    _write_session(session_dir / "twitter_2.json", token="s" * 40)

    with patch.object(twitter_cookies, "get_session_dir", return_value=session_dir):
        with patch.object(twitter_cookies, "_LEGACY_SESSION_DIRS", []):
            results = twitter_cookies._load_all_playwright_sessions()

    labels = [label for label, _ in results]
    assert labels == ["playwright(twitter.json)", "playwright(twitter_2.json)"]
    primary = [x for x in labels if twitter_cookies.is_primary_cookie_label(x)]
    assert primary == ["playwright(twitter.json)"], "生成侧 label 必须命中白名单"


def test_cookie_file_labels_match_primary_whitelist(tmp_path):
    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir()
    for name, token in (("x.json", "t" * 40), ("x_2.json", "s" * 40)):
        (cookie_dir / name).write_text(
            json.dumps({"auth_token": token, "ct0": "c" * 32}), encoding="utf-8",
        )

    with patch.object(twitter_cookies, "get_cookie_dir", return_value=cookie_dir):
        with patch.object(twitter_cookies, "_LEGACY_COOKIE_DIRS", []):
            results = twitter_cookies._load_all_cookie_files()

    labels = [label for label, _ in results]
    assert labels == ["cookie_file(x.json)", "cookie_file(x_2.json)"]
    primary = [x for x in labels if twitter_cookies.is_primary_cookie_label(x)]
    assert primary == ["cookie_file(x.json)"], "生成侧 label 必须命中白名单"
