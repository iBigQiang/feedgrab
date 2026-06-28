# -*- coding: utf-8 -*-
"""v0.22.0 P0 enhancement regressions (twitter-web-exporter borrowings).

Covers:
- P0-2: TweetTombstone / TweetUnavailable explicit handling (main + quoted)
- P0-3: TimelinePinEntry extraction in UserTweets timeline
- P0-4: Video variant selection no longer filters by content_type
"""

from feedgrab.fetchers.twitter_graphql import (
    extract_tweet_data,
    parse_user_tweets_entries,
    parse_moderated_timeline_entries,
    _entry_sort_index,
    _sort_entries_by_sortindex,
)


# ---------------------------------------------------------------------------
# P0-2: TweetTombstone / TweetUnavailable
# ---------------------------------------------------------------------------

def _wrap_entry(tweet_result: dict) -> dict:
    """Wrap a tweet __typename payload into a UserTweets timeline entry shape."""
    return {
        "entryId": "tweet-1234567890",
        "content": {
            "entryType": "TimelineTimelineItem",
            "itemContent": {
                "itemType": "TimelineTweet",
                "tweet_results": {"result": tweet_result},
            },
        },
    }


def test_tweet_tombstone_returns_none_and_logs():
    entry = _wrap_entry({
        "__typename": "TweetTombstone",
        "tombstone": {"text": {"text": "This Post is from an account that no longer exists."}},
    })
    assert extract_tweet_data(entry) is None


def test_cookie_refresh_uses_cdp_without_input_in_worker_mode(monkeypatch, tmp_path):
    import builtins
    import urllib.request

    import feedgrab.fetchers.twitter_graphql as twitter_graphql
    import feedgrab.login as login_module

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    captured = []

    monkeypatch.setenv("FEEDGRAB_WORKER_MODE", "true")
    monkeypatch.setenv("CHROME_CDP_LOGIN", "true")
    monkeypatch.setenv("FEEDGRAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(twitter_graphql, "_last_cookie_prompt_time", 0)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(
        builtins,
        "input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("input should not be called")),
    )

    def fake_login_via_cdp(platform, session_path):
        captured.append((platform, session_path))
        return True

    monkeypatch.setattr(login_module, "_login_via_cdp", fake_login_via_cdp)

    assert twitter_graphql._prompt_cookie_refresh_via_cdp() is True
    assert captured == [("twitter", tmp_path / "twitter.json")]


def test_tweet_unavailable_returns_none():
    entry = _wrap_entry({
        "__typename": "TweetUnavailable",
        "reason": "Protected",
    })
    assert extract_tweet_data(entry) is None


def test_quoted_tweet_tombstone_carries_marker():
    entry = _wrap_entry({
        "__typename": "Tweet",
        "rest_id": "100",
        "legacy": {
            "id_str": "100",
            "full_text": "Look at this:",
            "user_id_str": "u1",
            "created_at": "Fri Nov 01 12:00:00 +0000 2024",
            "favorite_count": 1, "retweet_count": 0,
            "reply_count": 0, "bookmark_count": 0,
            "entities": {}, "extended_entities": {},
        },
        "core": {
            "user_results": {"result": {
                "rest_id": "u1",
                "legacy": {"screen_name": "alice", "name": "Alice"},
            }},
        },
        "views": {"count": "10"},
        "quoted_status_result": {"result": {
            "__typename": "TweetTombstone",
            "tombstone": {"text": {"text": "This Post is unavailable."}},
        }},
    })
    td = extract_tweet_data(entry)
    assert td is not None
    qt = td["quoted_tweet"]
    assert qt is not None
    assert qt.get("is_tombstone") is True
    assert "unavailable" in qt.get("tombstone_text", "").lower()


def test_quoted_tweet_unavailable_carries_marker():
    entry = _wrap_entry({
        "__typename": "Tweet",
        "rest_id": "101",
        "legacy": {
            "id_str": "101", "full_text": "RT this:",
            "user_id_str": "u1", "created_at": "Fri Nov 01 12:00:00 +0000 2024",
            "favorite_count": 0, "retweet_count": 0,
            "reply_count": 0, "bookmark_count": 0,
            "entities": {}, "extended_entities": {},
        },
        "core": {"user_results": {"result": {
            "rest_id": "u1",
            "legacy": {"screen_name": "alice", "name": "Alice"},
        }}},
        "views": {"count": "5"},
        "quoted_status_result": {"result": {
            "__typename": "TweetUnavailable",
            "reason": "Protected",
        }},
    })
    td = extract_tweet_data(entry)
    qt = td["quoted_tweet"]
    assert qt.get("is_unavailable") is True
    assert "protected" in qt.get("tombstone_text", "").lower()


# ---------------------------------------------------------------------------
# P0-3: TimelinePinEntry extraction
# ---------------------------------------------------------------------------

def _make_user_tweets_response(*, pin_tweet_id: str = "", normal_ids: tuple = ()) -> dict:
    """Build a minimal UserTweets GraphQL response with optional pin + normal tweets."""
    instructions = []
    if pin_tweet_id:
        instructions.append({
            "type": "TimelinePinEntry",
            "entry": {
                "entryId": f"tweet-{pin_tweet_id}",
                "sortIndex": "9999",
                "content": {
                    "entryType": "TimelineTimelineItem",
                    "itemContent": {
                        "itemType": "TimelineTweet",
                        "tweet_results": {"result": {
                            "__typename": "Tweet",
                            "rest_id": pin_tweet_id,
                            "legacy": {
                                "id_str": pin_tweet_id,
                                "full_text": "pinned content",
                                "user_id_str": "u1",
                                "created_at": "Fri Nov 01 12:00:00 +0000 2024",
                                "favorite_count": 100, "retweet_count": 50,
                                "reply_count": 5, "bookmark_count": 0,
                                "entities": {}, "extended_entities": {},
                            },
                            "core": {"user_results": {"result": {
                                "rest_id": "u1",
                                "legacy": {"screen_name": "alice", "name": "Alice"},
                            }}},
                            "views": {"count": "1000"},
                        }},
                    },
                },
            },
        })

    add_entries = {"type": "TimelineAddEntries", "entries": []}
    for tid in normal_ids:
        add_entries["entries"].append({
            "entryId": f"tweet-{tid}",
            "sortIndex": tid,
            "content": {
                "entryType": "TimelineTimelineItem",
                "itemContent": {
                    "itemType": "TimelineTweet",
                    "tweet_results": {"result": {
                        "__typename": "Tweet",
                        "rest_id": tid,
                        "legacy": {
                            "id_str": tid,
                            "full_text": f"tweet {tid}",
                            "user_id_str": "u1",
                            "created_at": "Fri Nov 01 12:00:00 +0000 2024",
                            "favorite_count": 1, "retweet_count": 0,
                            "reply_count": 0, "bookmark_count": 0,
                            "entities": {}, "extended_entities": {},
                        },
                        "core": {"user_results": {"result": {
                            "rest_id": "u1",
                            "legacy": {"screen_name": "alice", "name": "Alice"},
                        }}},
                        "views": {"count": "10"},
                    }},
                },
            },
        })
    instructions.append(add_entries)

    return {
        "data": {"user": {"result": {"timeline_v2": {"timeline": {
            "instructions": instructions,
        }}}}},
    }


def test_pin_entry_extracted_at_head_of_entries():
    resp = _make_user_tweets_response(pin_tweet_id="999", normal_ids=("1", "2"))
    entries, cursors = parse_user_tweets_entries(resp)
    assert len(entries) == 3
    # Pinned tweet must be first
    pin = entries[0]
    assert pin.get("_is_pinned") is True
    assert pin["entryId"] == "tweet-999"


def test_pin_entry_propagates_is_pinned_through_extract():
    resp = _make_user_tweets_response(pin_tweet_id="999", normal_ids=())
    entries, _ = parse_user_tweets_entries(resp)
    td = extract_tweet_data(entries[0])
    assert td is not None
    assert td.get("is_pinned") is True
    assert td.get("id") == "999"


def test_no_pin_entry_is_not_marked_pinned():
    resp = _make_user_tweets_response(pin_tweet_id="", normal_ids=("1",))
    entries, _ = parse_user_tweets_entries(resp)
    td = extract_tweet_data(entries[0])
    assert td is not None
    assert td.get("is_pinned") is False


# ---------------------------------------------------------------------------
# P0-4: Video variant selection — no longer filters by content_type
# ---------------------------------------------------------------------------

def _wrap_video_entry(variants: list) -> dict:
    return _wrap_entry({
        "__typename": "Tweet",
        "rest_id": "200",
        "legacy": {
            "id_str": "200", "full_text": "video tweet",
            "user_id_str": "u1",
            "created_at": "Fri Nov 01 12:00:00 +0000 2024",
            "favorite_count": 0, "retweet_count": 0,
            "reply_count": 0, "bookmark_count": 0,
            "entities": {}, "extended_entities": {
                "media": [{
                    "type": "video",
                    "media_url_https": "https://pbs.twimg.com/poster.jpg",
                    "video_info": {"variants": variants},
                }],
            },
        },
        "core": {"user_results": {"result": {
            "rest_id": "u1",
            "legacy": {"screen_name": "alice", "name": "Alice"},
        }}},
        "views": {"count": "10"},
    })


def test_video_variant_picks_highest_bitrate_when_only_mp4():
    variants = [
        {"content_type": "video/mp4", "bitrate": 320000, "url": "low.mp4"},
        {"content_type": "video/mp4", "bitrate": 2176000, "url": "high.mp4"},
        {"content_type": "application/x-mpegURL", "url": "stream.m3u8"},  # no bitrate
    ]
    td = extract_tweet_data(_wrap_video_entry(variants))
    assert td["videos"] == ["high.mp4"]


def test_video_variant_picks_webm_if_bitrate_higher():
    """If Twitter ever serves webm with higher bitrate, we should pick it."""
    variants = [
        {"content_type": "video/mp4", "bitrate": 800000, "url": "mp4-low.mp4"},
        {"content_type": "video/webm", "bitrate": 3000000, "url": "webm-high.webm"},
        {"content_type": "application/x-mpegURL", "url": "stream.m3u8"},  # no bitrate
    ]
    td = extract_tweet_data(_wrap_video_entry(variants))
    # webm wins by bitrate, no longer filtered out
    assert td["videos"] == ["webm-high.webm"]


def test_video_variant_skips_when_no_bitrate_anywhere():
    variants = [
        {"content_type": "application/x-mpegURL", "url": "stream.m3u8"},
    ]
    td = extract_tweet_data(_wrap_video_entry(variants))
    # No bitrate-having variant → no video URL, only poster image kept
    assert td["videos"] == []
    assert td["images"] == ["https://pbs.twimg.com/poster.jpg"]


# ---------------------------------------------------------------------------
# P1-2: sortIndex helpers (v0.23.0, twe utils/api.ts:24-29)
# ---------------------------------------------------------------------------

def test_entry_sort_index_parses_snowflake_string():
    assert _entry_sort_index({"sortIndex": "1234567890"}) == 1234567890


def test_entry_sort_index_handles_bigint_values():
    big = "1872888888888888888"  # > 32-bit
    assert _entry_sort_index({"sortIndex": big}) == int(big)


def test_entry_sort_index_returns_zero_on_missing():
    assert _entry_sort_index({}) == 0
    assert _entry_sort_index({"sortIndex": None}) == 0
    assert _entry_sort_index({"sortIndex": ""}) == 0
    assert _entry_sort_index({"sortIndex": "not-a-number"}) == 0


def test_sort_entries_by_sortindex_desc():
    entries = [
        {"sortIndex": "100", "id": "a"},
        {"sortIndex": "300", "id": "b"},
        {"sortIndex": "200", "id": "c"},
    ]
    _sort_entries_by_sortindex(entries)
    assert [e["id"] for e in entries] == ["b", "c", "a"]


def test_parse_user_tweets_entries_sorts_out_of_order_response():
    """Build a UserTweets response where API returns entries out of sortIndex order."""
    resp = {
        "data": {"user": {"result": {"timeline_v2": {"timeline": {
            "instructions": [{
                "type": "TimelineAddEntries",
                "entries": [
                    {
                        "entryId": "tweet-100",
                        "sortIndex": "100",
                        "content": {
                            "entryType": "TimelineTimelineItem",
                            "itemContent": {
                                "itemType": "TimelineTweet",
                                "tweet_results": {"result": {"__typename": "Tweet"}},
                            },
                        },
                    },
                    {
                        "entryId": "tweet-300",
                        "sortIndex": "300",
                        "content": {
                            "entryType": "TimelineTimelineItem",
                            "itemContent": {
                                "itemType": "TimelineTweet",
                                "tweet_results": {"result": {"__typename": "Tweet"}},
                            },
                        },
                    },
                    {
                        "entryId": "tweet-200",
                        "sortIndex": "200",
                        "content": {
                            "entryType": "TimelineTimelineItem",
                            "itemContent": {
                                "itemType": "TimelineTweet",
                                "tweet_results": {"result": {"__typename": "Tweet"}},
                            },
                        },
                    },
                ],
            }],
        }}}}},
    }
    entries, _cursors = parse_user_tweets_entries(resp)
    # Sorted desc: 300 → 200 → 100
    assert [e["entryId"] for e in entries] == ["tweet-300", "tweet-200", "tweet-100"]


def test_parse_user_tweets_pin_stays_at_head_after_sort():
    """Pin entry inserted at index 0 after sort, regardless of its own sortIndex."""
    resp = _make_user_tweets_response(pin_tweet_id="999", normal_ids=("1", "2"))
    # Mutate sortIndex so that normal ids are higher than pin's
    instructions = resp["data"]["user"]["result"]["timeline_v2"]["timeline"]["instructions"]
    add_entries = next(i for i in instructions if i["type"] == "TimelineAddEntries")
    # Force pin to have a low sortIndex, normal entries to have higher ones
    for inst in instructions:
        if inst["type"] == "TimelinePinEntry":
            inst["entry"]["sortIndex"] = "1"
    for e in add_entries["entries"]:
        # Normal tweets get sortIndex 1000 and 2000
        e["sortIndex"] = "2000" if e["entryId"] == "tweet-1" else "1000"
    entries, _ = parse_user_tweets_entries(resp)
    # Pin must still be first
    assert entries[0]["entryId"] == "tweet-999"
    assert entries[0].get("_is_pinned") is True


# ---------------------------------------------------------------------------
# P1-3: ModeratedTimeline parser (v0.23.0, twe tweet-detail_api.ts:58-60)
# ---------------------------------------------------------------------------

def test_parse_moderated_timeline_extracts_tweets_in_sorted_order():
    resp = {
        "data": {"tweet": {"result": {"timeline_response": {"timeline": {
            "instructions": [{
                "type": "TimelineAddEntries",
                "entries": [
                    {
                        "entryId": "tweet-200",
                        "sortIndex": "200",
                        "content": {
                            "entryType": "TimelineTimelineItem",
                            "itemContent": {
                                "itemType": "TimelineTweet",
                                "tweet_results": {"result": {"__typename": "Tweet"}},
                            },
                        },
                    },
                    {
                        "entryId": "tweet-400",
                        "sortIndex": "400",
                        "content": {
                            "entryType": "TimelineTimelineItem",
                            "itemContent": {
                                "itemType": "TimelineTweet",
                                "tweet_results": {"result": {"__typename": "Tweet"}},
                            },
                        },
                    },
                    {
                        "entryId": "cursor-bottom-xxx",
                        "content": {
                            "entryType": "TimelineTimelineCursor",
                            "cursorType": "Bottom",
                            "value": "NEXT_CURSOR",
                        },
                    },
                ],
            }],
        }}}}},
    }
    entries, cursors = parse_moderated_timeline_entries(resp)
    # Sorted desc by sortIndex
    assert [e["entryId"] for e in entries] == ["tweet-400", "tweet-200"]
    assert cursors.get("bottom") == "NEXT_CURSOR"


def test_parse_moderated_timeline_empty_response():
    assert parse_moderated_timeline_entries({}) == ([], {})
    assert parse_moderated_timeline_entries({"data": {}}) == ([], {})
