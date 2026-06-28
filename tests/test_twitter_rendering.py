# -*- coding: utf-8 -*-
"""Twitter/X schema rendering regressions."""

from feedgrab.schema import SourceType, UnifiedContent, from_twitter
from feedgrab.utils.storage import _format_markdown


def test_long_thread_with_quoted_tweet_is_not_misclassified_as_article():
    long_root = "看了4篇教程，终于成为土耳其ChatGPT用户！" + "补充说明" * 130
    data = {
        "text": long_root,
        "author": "@daodao166888",
        "author_name": "刀刀",
        "url": "https://x.com/daodao166888/status/2051640789206499666",
        "title": "看了4篇教程，终于成为土耳其ChatGPT用户！",
        "platform": "twitter",
        "thread_tweets": [
            {
                "id": "2051640789206499666",
                "text": long_root,
                "author": "daodao166888",
                "author_name": "刀刀",
                "images": [],
                "videos": [],
            },
            {
                "id": "2051641511692108146",
                "text": "参考的4条X推文（感谢三位大佬的无私分享！❤️）",
                "author": "daodao166888",
                "author_name": "刀刀",
                "images": [],
                "videos": [],
                "quoted_tweet": {
                    "id": "2050951612546638254",
                    "text": "土耳其 Apple ID 礼品卡充值教程原文",
                    "author": "yanhua1010",
                    "author_name": "烟花",
                    "url": "https://x.com/yanhua1010/status/2050951612546638254",
                    "images": [],
                    "videos": [],
                },
            },
            {
                "id": "2051673715340107821",
                "text": "Authenticator App 二次验证操作补充",
                "author": "daodao166888",
                "author_name": "刀刀",
                "images": [],
                "videos": [],
                "quoted_tweet": {
                    "id": "2051672318968320149",
                    "text": "OpenAI 账号安全设置补充说明",
                    "author": "daodao166888",
                    "author_name": "刀刀",
                    "url": "https://x.com/daodao166888/status/2051672318968320149",
                    "images": [],
                    "videos": [],
                },
            },
        ],
        "article_data": {},
    }

    content = from_twitter(data)

    assert content.extra["tweet_type"] == "thread"
    assert "土耳其 Apple ID 礼品卡充值教程原文" in content.content
    assert "OpenAI 账号安全设置补充说明" in content.content
    assert "> **烟花** (@yanhua1010)" in content.content


def test_from_twitter_cleans_multiline_title_from_fallback_payload():
    title = (
        "终于把 #feedgrab-desktop 版基本优化差不多了（这GUI界面完全是codex给画的，专人大佬请嘴下留情谨慎吐槽啊）"
        "\n\n"
        "非专业编程，从opus-4.6出来以后看到ai的能力才开始实践 vibe coding"
    )

    content = from_twitter({
        "text": title,
        "author": "@iBigQiang",
        "author_name": "强子手记",
        "url": "https://x.com/iBigQiang/status/2071053794977423489",
        "title": title,
        "platform": "twitter",
        "thread_tweets": [{"id": "2071053794977423489", "text": title}],
        "article_data": {},
    })

    assert "\n" not in content.title
    assert "非专业编程" not in content.title
    assert len(content.title) <= 50


def test_front_matter_title_stays_single_line_when_source_title_has_newlines():
    item = UnifiedContent(
        source_type=SourceType.TWITTER,
        source_name="@iBigQiang",
        title='第一行 #tag\n\n第二行 "quoted"',
        content="正文",
        url="https://x.com/iBigQiang/status/1",
    )

    rendered = _format_markdown(item)
    front_matter = rendered.split("---\n", 1)[1].split("\n---", 1)[0]

    assert 'title: "第一行 #tag 第二行 \\"quoted\\""' in front_matter
    assert not any(
        line.startswith("第二行")
        for line in front_matter.splitlines()
    )
