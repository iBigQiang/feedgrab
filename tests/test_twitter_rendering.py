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

def test_tweet_video_rendered_as_html5_player():
    data = {
        "text": "这种儿童绘本风的动画真的太治愈了",
        "author": "@liyue_ai",
        "author_name": "李岳",
        "url": "https://x.com/liyue_ai/status/2075136397326139804",
        "title": "这种儿童绘本风的动画真的太治愈了",
        "platform": "twitter",
        "thread_tweets": [
            {
                "id": "2075136397326139804",
                "text": "这种儿童绘本风的动画真的太治愈了",
                "author": "liyue_ai",
                "author_name": "李岳",
                "images": [],
                "videos": [
                    "https://video.twimg.com/amplify_video/1/vid/avc1/1280x720/a.mp4?tag=28&mux=1"
                ],
                "quoted_tweet": {
                    "id": "2074110263054286848",
                    "text": "引用推文",
                    "author": "someone",
                    "author_name": "某人",
                    "url": "https://x.com/someone/status/2074110263054286848",
                    "images": [],
                    "videos": ["https://video.twimg.com/amplify_video/2/vid/b.mp4?tag=28"],
                },
            },
        ],
        "article_data": {},
    }

    content = from_twitter(data)

    # 主推文视频：<video> 标签 + & 转义为 &amp;
    assert (
        '<video controls src="https://video.twimg.com/amplify_video/1/vid/avc1/1280x720/a.mp4?tag=28&amp;mux=1"></video>'
        in content.content
    )
    # 引用推文视频：blockquote 内同样用 <video> 标签
    assert '> <video controls src="https://video.twimg.com/amplify_video/2/vid/b.mp4?tag=28"></video>' in content.content
    # 旧的纯链接写法不再出现
    assert "[▶ video]" not in content.content


def test_replace_urls_in_md_matches_html_escaped_video_src(tmp_path):
    from feedgrab.utils.media import _replace_urls_in_md

    remote = "https://video.twimg.com/amplify_video/1/vid/a.mp4?tag=28&mux=1"
    escaped = remote.replace("&", "&amp;")
    md = tmp_path / "t.md"
    md.write_text(
        f'<video controls src="{escaped}"></video>\n\n![image](https://pbs.twimg.com/media/x.jpg)\n',
        encoding="utf-8",
    )

    _replace_urls_in_md(md, {
        remote: "attachments/1/a.mp4",
        "https://pbs.twimg.com/media/x.jpg": "attachments/1/x.jpg",
    })

    text = md.read_text(encoding="utf-8")
    assert '<video controls src="attachments/1/a.mp4"></video>' in text
    assert "![image](attachments/1/x.jpg)" in text
    assert "twimg.com" not in text

def test_quoted_tweet_media_included_in_download_list():
    data = {
        "text": "主推文",
        "author": "@a",
        "author_name": "A",
        "url": "https://x.com/a/status/1",
        "title": "主推文",
        "platform": "twitter",
        "images": ["https://pbs.twimg.com/media/main.jpg"],
        "videos": ["https://video.twimg.com/main.mp4?tag=28"],
        "thread_tweets": [
            {
                "id": "1",
                "text": "主推文",
                "author": "a",
                "author_name": "A",
                "images": ["https://pbs.twimg.com/media/main.jpg"],
                "videos": ["https://video.twimg.com/main.mp4?tag=28"],
                "quoted_tweet": {
                    "id": "2",
                    "text": "被引用推文",
                    "author": "b",
                    "author_name": "B",
                    "url": "https://x.com/b/status/2",
                    "images": ["https://pbs.twimg.com/amplify_video_thumb/2/img/qt.jpg"],
                    "videos": ["https://video.twimg.com/amplify_video/2/vid/qt.mp4?tag=28"],
                },
            },
        ],
        "article_data": {},
    }

    content = from_twitter(data)

    # 引用推文媒体并入 extra 下载清单（去重、保序）
    assert content.extra["images"] == [
        "https://pbs.twimg.com/media/main.jpg",
        "https://pbs.twimg.com/amplify_video_thumb/2/img/qt.jpg",
    ]
    assert content.extra["videos"] == [
        "https://video.twimg.com/main.mp4?tag=28",
        "https://video.twimg.com/amplify_video/2/vid/qt.mp4?tag=28",
    ]
