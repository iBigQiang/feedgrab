# -*- coding: utf-8 -*-
"""线程抓取不得把「别人的回复」替换成根推内容。

2026-08-31 实测发现：书签里收藏了 @ClockWorkMe 在某 thread 下回复 @iBigQiang
的两条推文，落盘的 md 正文却都是根推作者 @LaoVStories 的「汇丰红狮子已收到…」，
被收藏那两条的正文彻底丢失。因为标题也跟着变成根推的，三个文件撞名，才触发
``_resolve_filepath`` 的 hash 后缀，表现为「重复文件」。

根因链：

1. ``_classify_tweet``：``conversation_id != id`` 或 ``in_reply_to`` 非空即判为
   thread —— 任何回复推文都会走线程路径，包括别人的回复；
2. ``fetch_tweet_thread`` 的语义是「重建**根推作者**的自回复链」，
   ``_is_same_thread`` 第一条判据就是 ``user_id != root_user_id -> False``；
3. 传入的 ``tweet_id`` 只用于发起 TweetDetail 请求，组装时被丢弃
   （``root_tweet = thread_tweets[0]``）。

修复：目标推文没能通过同作者过滤时返回 None，让调用方退回单条抓取
（``twitter.py`` 的 fallback 按 ``tweet_data["id"] == tweet_id`` 精确取目标推文）。
"""

from unittest.mock import patch

import pytest


# --- 测试替身 -------------------------------------------------------------

def _tweet(tid: str, user_id: str, author: str, conv_id: str,
           in_reply_to_user: str = "", text: str = "") -> dict:
    """构造 extract_tweet_data() 形状的扁平 dict（只保留过滤用到的字段）。"""
    return {
        "id": tid,
        "user_id": user_id,
        "author": author,
        "author_name": author,
        "conversation_id": conv_id,
        "in_reply_to_user_id": in_reply_to_user,
        "in_reply_to_status_id": "" if tid == conv_id else conv_id,
        "text": text or f"tweet {tid}",
        "images": [],
        "videos": [],
        "hashtags": [],
    }


# 真实形态：@LaoVStories 的根推 + 自回复，@ClockWorkMe 在其下的回复
ROOT_ID = "2019023586112176237"
ROOT_UID = "root-user"
REPLY_BY_OTHER_ID = "2019060923751632901"
OTHER_UID = "other-user"

_ROOT = _tweet(ROOT_ID, ROOT_UID, "LaoVStories", ROOT_ID, text="汇丰红狮子已收到。")
_ROOT_SELF_REPLY = _tweet("2019023586112176238", ROOT_UID, "LaoVStories", ROOT_ID,
                          in_reply_to_user=ROOT_UID, text="补充一点")
_OTHER_REPLY = _tweet(REPLY_BY_OTHER_ID, OTHER_UID, "ClockWorkMe", ROOT_ID,
                      in_reply_to_user=ROOT_UID,
                      text="@iBigQiang @LaoVStories 红的是ATM提款卡")


def _run_thread(tweet_id: str, entries: list):
    """跑 fetch_tweet_thread，把网络层全部替换成固定 entries。"""
    from feedgrab.fetchers import twitter_thread as tt

    with patch.object(tt, "fetch_tweet_detail", return_value={"data": {}}), \
         patch.object(tt, "parse_tweet_entries", return_value=[]), \
         patch.object(tt, "parse_cursors", return_value={}), \
         patch.object(tt, "_parse_entries_to_tweets", return_value=list(entries)), \
         patch.object(tt, "x_fetch_moderated_replies", return_value=False):
        return tt.fetch_tweet_thread(tweet_id, {"auth_token": "t", "ct0": "c"})


# --- 回归：别人的回复不能被替换成根推 --------------------------------------

def test_other_persons_reply_does_not_return_root_thread():
    """收藏别人在 thread 下的回复时，绝不能返回根推作者的自回复链。"""
    result = _run_thread(REPLY_BY_OTHER_ID, [_ROOT, _ROOT_SELF_REPLY, _OTHER_REPLY])

    assert result is None, (
        "目标推文被同作者过滤掉后仍返回了线程数据 —— "
        "调用方会把根推内容写到这条推文的 URL 下"
    )


def test_root_author_own_thread_still_works():
    """回归：抓根推作者自己的推文，线程重建必须照常工作。"""
    result = _run_thread(ROOT_ID, [_ROOT, _ROOT_SELF_REPLY, _OTHER_REPLY])

    assert result is not None
    assert result["author"] == "LaoVStories"
    assert [t["id"] for t in result["tweets"]] == [ROOT_ID, "2019023586112176238"]
    # 别人的回复不该混进自回复链
    assert REPLY_BY_OTHER_ID not in [t["id"] for t in result["tweets"]]


def test_self_reply_in_middle_of_thread_still_works():
    """回归：从 thread 中间某条（作者本人的）进入，也要拿到完整链。"""
    result = _run_thread("2019023586112176238", [_ROOT, _ROOT_SELF_REPLY, _OTHER_REPLY])

    assert result is not None
    assert result["tweet_count"] == 2
    assert "2019023586112176238" in [t["id"] for t in result["tweets"]]


def test_empty_thread_still_returns_none():
    """回归：没有任何同线程推文时仍返回 None（原有行为不变）。"""
    assert _run_thread(REPLY_BY_OTHER_ID, []) is None


# --- 分类器：别人的回复确实会被送进线程路径 --------------------------------

def test_classify_marks_other_persons_reply_as_thread():
    """说明 bug 的入口：分类器只看是不是回复，不看回复者是谁。

    这条断言不是要求改分类器 —— 线程路径本来就该处理回复；关键是线程路径
    自己要能识别出「目标不属于本链」并退出。
    """
    from feedgrab.fetchers.twitter_bookmarks import _classify_tweet

    assert _classify_tweet(_OTHER_REPLY) == "thread"
    assert _classify_tweet(_ROOT_SELF_REPLY) == "thread"
    # 根推本身不是回复
    assert _classify_tweet(_ROOT) == "single"


# --- 调用方：拿到 None 后必须回落到目标推文自身 ----------------------------

def test_graphql_fallback_picks_the_requested_tweet(monkeypatch):
    """fetch_tweet_thread 返回 None 时，_fetch_via_graphql 必须取目标推文本身。"""
    import asyncio
    from feedgrab.fetchers import twitter as tw

    monkeypatch.setattr(
        "feedgrab.fetchers.twitter_cookies.has_required_cookies", lambda ck: True
    )
    monkeypatch.setattr(
        "feedgrab.fetchers.twitter_thread.fetch_tweet_thread",
        lambda tid, ck: None,
    )
    monkeypatch.setattr(
        "feedgrab.fetchers.twitter_graphql.fetch_tweet_detail",
        lambda tid, ck: {"data": {}},
    )
    monkeypatch.setattr(
        "feedgrab.fetchers.twitter_graphql.parse_tweet_entries",
        lambda resp: [{"e": 1}, {"e": 2}],
    )
    # 第一个 entry 是根推，第二个才是目标 —— 必须按 id 精确挑中目标
    seq = iter([_ROOT, _OTHER_REPLY])
    monkeypatch.setattr(
        "feedgrab.fetchers.twitter_graphql.extract_tweet_data",
        lambda entry: next(seq),
    )

    url = f"https://x.com/ClockWorkMe/status/{REPLY_BY_OTHER_ID}"
    data = asyncio.run(tw._fetch_via_graphql(url, REPLY_BY_OTHER_ID,
                                             cookies={"auth_token": "t", "ct0": "c"}))

    assert data["author"] == "@ClockWorkMe", "落盘作者必须是被抓那条推文的作者"
    assert "红的是ATM提款卡" in data["text"], "正文必须是被抓那条推文的正文"
    assert "汇丰红狮子" not in data["text"], "绝不能把根推正文写成这条推文"
    assert data["url"] == url
    assert data["has_thread"] is False
