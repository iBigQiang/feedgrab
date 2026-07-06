from feedgrab.fetchers.feishu import blocks_to_markdown
from feedgrab.fetchers.feishu_wiki import _normalize_sidebar_nodes


def test_normalize_sidebar_nodes_extracts_tokens_from_tree_node_uid():
    raw_nodes = [
        {
            "text": "📌 使用指南：如何最高效地学完这套教程（持续更新中）",
            "node_uid": "level=1&rootNodeId=TOC-ROOT&wikiToken=B8VDwLOcdiE3ybkoABMcpGbrn3g",
        },
        {
            "text": "知识猫图解：内容拆解 Agent 提示词",
            "node_uid": "firstLevelWikiToken=UpT6wphAiicrMFkvN0ncZYelnfd&level=2&rootNodeId=TOC-ROOT&wikiToken=ZFJswuByOi5okdkW9qZcrJEwn3b",
        },
    ]

    links = _normalize_sidebar_nodes(
        raw_nodes,
        base_url="https://ycnj2htgnvdy.feishu.cn",
    )

    assert links == [
        {
            "title": "📌 使用指南：如何最高效地学完这套教程（持续更新中）",
            "url": "https://ycnj2htgnvdy.feishu.cn/wiki/B8VDwLOcdiE3ybkoABMcpGbrn3g",
            "token": "B8VDwLOcdiE3ybkoABMcpGbrn3g",
        },
        {
            "title": "知识猫图解：内容拆解 Agent 提示词",
            "url": "https://ycnj2htgnvdy.feishu.cn/wiki/ZFJswuByOi5okdkW9qZcrJEwn3b",
            "token": "ZFJswuByOi5okdkW9qZcrJEwn3b",
        },
    ]


def test_normalize_sidebar_nodes_dedupes_virtual_tree_repeats_and_cleans_titles():
    raw_nodes = [
        {
            "text": "‍⁢​​⁣‌​一、先导篇：为什么应该选 GPT-Image2？ - 飞书云文档",
            "node_uid": "level=1&rootNodeId=TOC-ROOT&wikiToken=Eq4wwiM1piCB7Skqsn9cjUBqnNe",
        },
        {
            "text": "一、先导篇：为什么应该选 GPT-Image2？",
            "node_uid": "level=1&rootNodeId=TOC-ROOT&wikiToken=Eq4wwiM1piCB7Skqsn9cjUBqnNe",
        },
        {
            "title": "旧版锚点节点",
            "url": "https://ycnj2htgnvdy.feishu.cn/wiki/C3pdwDDqPizKjekwXvUcx2sCnNd",
        },
    ]

    links = _normalize_sidebar_nodes(
        raw_nodes,
        base_url="https://ycnj2htgnvdy.feishu.cn",
    )

    assert links == [
        {
            "title": "一、先导篇：为什么应该选 GPT-Image2？",
            "url": "https://ycnj2htgnvdy.feishu.cn/wiki/Eq4wwiM1piCB7Skqsn9cjUBqnNe",
            "token": "Eq4wwiM1piCB7Skqsn9cjUBqnNe",
        },
        {
            "title": "旧版锚点节点",
            "url": "https://ycnj2htgnvdy.feishu.cn/wiki/C3pdwDDqPizKjekwXvUcx2sCnNd",
            "token": "C3pdwDDqPizKjekwXvUcx2sCnNd",
        },
    ]


def test_blocks_to_markdown_renders_feishu_fallback_code_snapshot_as_fenced_code():
    blocks = [
        {
            "type": "fallback",
            "children": [],
            "snapshot": {
                "type": "code",
                "language": "Plain Text",
                "text": {
                    "initialAttributedTexts": {
                        "text": {
                            "0": "主标题：\"为什么你的知识卡片没人收藏\"\n",
                            "1": "模块 1：\"信息太散\"",
                        }
                    }
                },
            },
        }
    ]

    md = blocks_to_markdown(blocks)

    assert md == (
        "````plaintext\n"
        "主标题：\"为什么你的知识卡片没人收藏\"\n"
        "模块 1：\"信息太散\"\n"
        "````"
    )


def test_blocks_to_markdown_uses_four_backticks_for_regular_code_blocks():
    blocks = [
        {
            "type": "code",
            "zoneState": {
                "allText": "帮我做一张关于时间管理的图。\n",
                "content": {
                    "ops": [
                        {"insert": "帮我做一张关于时间管理的图。", "attributes": {}},
                        {"insert": "\n", "attributes": {"fixEnter": "true"}},
                    ]
                },
            },
            "snapshot": {"type": "code", "language": "Plain Text"},
        }
    ]

    md = blocks_to_markdown(blocks)

    assert md == "````plaintext\n帮我做一张关于时间管理的图。\n````"


def test_blocks_to_markdown_uses_snapshot_row_and_column_ids_for_feishu_tables():
    def text_block(text: str):
        return {
            "type": "text",
            "children": [],
            "zoneState": {
                "allText": text + "\n",
                "content": {
                    "ops": [
                        {"insert": text, "attributes": {}},
                        {"insert": "\n", "attributes": {"fixEnter": "true"}},
                    ]
                },
            },
            "snapshot": {"type": "text"},
        }

    def cell(text: str):
        return {
            "type": "table_cell",
            "children": [text_block(text)],
            "snapshot": {"type": "table_cell"},
        }

    blocks = [
        {
            "type": "table",
            "children": [
                cell("列1"),
                cell("列2"),
                cell("列3"),
                cell("值A"),
                cell("值B"),
                cell("值C"),
            ],
            "snapshot": {
                "type": "table",
                "rows_id": ["row1", "row2"],
                "columns_id": ["col1", "col2", "col3"],
            },
        }
    ]

    md = blocks_to_markdown(blocks)

    assert md == (
        "| 列1 | 列2 | 列3 |\n"
        "| --- | --- | --- |\n"
        "| 值A | 值B | 值C |"
    )


def test_blocks_to_markdown_renders_video_file_as_local_preview():
    blocks = [
        {
            "type": "file",
            "children": [],
            "file": {
                "token": "boxcnVideoToken",
                "name": "完整上传教程.mp4",
                "mime_type": "video/mp4",
            },
        }
    ]
    media = []

    md = blocks_to_markdown(blocks, media=media, img_subdir="doc123")

    assert md == (
        '<video controls src="attachments/doc123/001_完整上传教程.mp4">'
        "</video>"
    )
    assert media == [
        {
            "token": "boxcnVideoToken",
            "name": "完整上传教程.mp4",
            "mime_type": "video/mp4",
            "size": 0,
            "media_type": "video",
            "_filename": "001_完整上传教程.mp4",
        }
    ]


def test_blocks_to_markdown_renders_nested_video_file_under_heading():
    blocks = [
        {
            "type": "heading3",
            "children": [
                {
                    "type": "file",
                    "children": [],
                    "snapshot": {
                        "type": "file",
                        "file": {
                            "token": "YEx5bBnPWoWBLixZFt0cEny2nQd",
                            "mimeType": "video/mp4",
                            "name": "565450a9c3d4a6785e9ba13f29a6ba14.mp4",
                        },
                    },
                }
            ],
            "zoneState": {
                "allText": "完整上传教程\n",
                "content": {
                    "ops": [
                        {"insert": "完整上传教程", "attributes": {}},
                        {"insert": "\n", "attributes": {"fixEnter": "true"}},
                    ]
                },
            },
            "snapshot": {"type": "heading3"},
        }
    ]
    media = []

    md = blocks_to_markdown(blocks, media=media, img_subdir="doc123")

    assert md == (
        "### 完整上传教程\n\n"
        '<video controls src="attachments/doc123/'
        '001_565450a9c3d4a6785e9ba13f29a6ba14.mp4"></video>'
    )
    assert media[0]["token"] == "YEx5bBnPWoWBLixZFt0cEny2nQd"
    assert media[0]["mime_type"] == "video/mp4"
    assert media[0]["media_type"] == "video"


def test_blocks_to_markdown_renders_fallback_video_snapshot_as_local_preview():
    blocks = [
        {
            "type": "fallback",
            "children": [],
            "snapshot": {
                "type": "video",
                "fileToken": "boxcnFallbackVideo",
                "fileName": "转码演示.mp4",
                "mimeType": "video/mp4",
            },
        }
    ]
    media = []

    md = blocks_to_markdown(blocks, media=media, img_subdir="doc123")

    assert md == (
        '<video controls src="attachments/doc123/001_转码演示.mp4">'
        "</video>"
    )
    assert media[0]["token"] == "boxcnFallbackVideo"
    assert media[0]["media_type"] == "video"


def test_blocks_to_markdown_collects_video_file_in_table_cell():
    blocks = [
        {
            "type": "table",
            "children": [
                {
                    "type": "table_cell",
                    "children": [
                        {
                            "type": "text",
                            "children": [
                                {
                                    "type": "file",
                                    "children": [],
                                    "snapshot": {
                                        "type": "file",
                                        "file": {
                                            "token": "HeDEbOzoYoEDBTx50aWcAGqlnBe",
                                            "mimeType": "video/mp4",
                                            "name": "微信视频2026-06-17_203024_102.mp4",
                                        },
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
            "snapshot": {
                "type": "table",
                "rows_id": ["row1"],
                "columns_id": ["col1"],
            },
        }
    ]
    media = []

    md = blocks_to_markdown(blocks, media=media, img_subdir="doc123")

    assert (
        '<video controls src="attachments/doc123/'
        '001_微信视频2026-06-17_203024_102.mp4"></video>'
    ) in md
    assert media[0]["token"] == "HeDEbOzoYoEDBTx50aWcAGqlnBe"
    assert media[0]["media_type"] == "video"


def test_blocks_to_markdown_preserves_table_cell_text_before_video():
    blocks = [
        {
            "type": "table",
            "children": [
                {
                    "type": "table_cell",
                    "zoneState": {
                        "allText": "教程视频\n",
                        "content": {"ops": [{"insert": "教程视频"}]},
                    },
                    "children": [
                        {
                            "type": "file",
                            "children": [],
                            "snapshot": {
                                "type": "file",
                                "file": {
                                    "token": "HeDEbOzoYoEDBTx50aWcAGqlnBe",
                                    "mimeType": "video/mp4",
                                    "name": "demo.mp4",
                                },
                            },
                        }
                    ],
                }
            ],
            "snapshot": {
                "type": "table",
                "rows_id": ["row1"],
                "columns_id": ["col1"],
            },
        }
    ]
    media = []

    md = blocks_to_markdown(blocks, media=media, img_subdir="doc123")

    assert (
        "| 教程视频<br>"
        '<video controls src="attachments/doc123/001_demo.mp4"></video> |'
    ) in md
    assert media[0]["token"] == "HeDEbOzoYoEDBTx50aWcAGqlnBe"


def test_blocks_to_markdown_renders_children_under_synced_container():
    blocks = [
        {
            "type": "synced_reference",
            "children": [
                {
                    "type": "file",
                    "children": [],
                    "snapshot": {
                        "type": "file",
                        "file": {
                            "token": "YEx5bBnPWoWBLixZFt0cEny2nQd",
                            "mimeType": "video/mp4",
                            "name": "565450a9c3d4a6785e9ba13f29a6ba14.mp4",
                        },
                    },
                }
            ],
            "snapshot": {"type": "synced_reference"},
        }
    ]
    media = []

    md = blocks_to_markdown(blocks, media=media, img_subdir="doc123")

    assert md == (
        '<video controls src="attachments/doc123/'
        '001_565450a9c3d4a6785e9ba13f29a6ba14.mp4"></video>'
    )
    assert media[0]["token"] == "YEx5bBnPWoWBLixZFt0cEny2nQd"


def test_download_feishu_media_writes_pre_downloaded_video_bytes(tmp_path, monkeypatch):
    from feedgrab.fetchers.feishu import download_feishu_media

    monkeypatch.setattr("feedgrab.fetchers.feishu._is_api_available", lambda: False)

    md_path = tmp_path / "doc.md"
    md_path.write_text("# doc", encoding="utf-8")

    download_feishu_media(
        str(md_path),
        [
            {
                "token": "boxcnVideoToken",
                "name": "完整上传教程.mp4",
                "mime_type": "video/mp4",
                "media_type": "video",
                "_filename": "001_完整上传教程.mp4",
                "_bytes": b"video-bytes",
            }
        ],
        "https://example.feishu.cn/wiki/doc",
        media_subdir="doc123",
    )

    assert (
        tmp_path / "attachments" / "doc123" / "001_完整上传教程.mp4"
    ).read_bytes() == b"video-bytes"


def test_download_feishu_media_prefers_original_file_url(
    tmp_path, monkeypatch
):
    """原始文件端点 /download/all/ 优先；DOM 播放流 URL 是转码预览，不应抢先。"""
    from feedgrab.fetchers.feishu import download_feishu_media

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "feishu.json").write_text(
        '{"cookies":[{"name":"_csrf_token","value":"csrf"}]}',
        encoding="utf-8",
    )
    requested = []

    class Response:
        status_code = 200
        content = b"original-file-bytes"
        headers = {"content-type": "video/mp4"}

    def fake_get(url, **kwargs):
        requested.append(url)
        return Response()

    monkeypatch.setattr("feedgrab.fetchers.feishu._is_api_available", lambda: False)
    monkeypatch.setattr("feedgrab.fetchers.feishu.get_session_dir", lambda: session_dir)
    monkeypatch.setattr("feedgrab.utils.http_client.get", fake_get)

    md_path = tmp_path / "doc.md"
    md_path.write_text("# doc", encoding="utf-8")
    video_url = (
        "https://internal-api-drive-stream.feishu.cn/space/api/box/stream/"
        "download/video/HeDEbOzoYoEDBTx50aWcAGqlnBe/"
        "?quality=720p&data_version=7652524667739049193&mount_point=docx_file"
    )

    download_feishu_media(
        str(md_path),
        [
            {
                "token": "HeDEbOzoYoEDBTx50aWcAGqlnBe",
                "name": "微信视频2026-06-17_203024_102.mp4",
                "mime_type": "video/mp4",
                "media_type": "video",
                "_filename": "001_video.mp4",
                "url": video_url,
            }
        ],
        "https://example.feishu.cn/wiki/doc",
        media_subdir="doc123",
    )

    assert requested == [
        "https://example.feishu.cn/space/api/box/stream/download/all/"
        "HeDEbOzoYoEDBTx50aWcAGqlnBe/"
    ]
    assert (
        tmp_path / "attachments" / "doc123" / "001_video.mp4"
    ).read_bytes() == b"original-file-bytes"


def test_download_feishu_media_falls_back_to_discovered_video_url(
    tmp_path, monkeypatch
):
    """原始文件端点被拒（如返回 JSON 错误）时，回退 DOM 收集的播放流 URL。"""
    from feedgrab.fetchers.feishu import download_feishu_media

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "feishu.json").write_text(
        '{"cookies":[{"name":"_csrf_token","value":"csrf"}]}',
        encoding="utf-8",
    )
    requested = []

    class DeniedResponse:
        status_code = 200
        content = b'{"code":1001,"msg":"forbidden"}'
        headers = {"content-type": "application/json"}

    class StreamResponse:
        status_code = 200
        content = b"video-from-stream-url"
        headers = {"content-type": "video/mp4"}

    def fake_get(url, **kwargs):
        requested.append(url)
        if "/download/all/" in url:
            return DeniedResponse()
        return StreamResponse()

    monkeypatch.setattr("feedgrab.fetchers.feishu._is_api_available", lambda: False)
    monkeypatch.setattr("feedgrab.fetchers.feishu.get_session_dir", lambda: session_dir)
    monkeypatch.setattr("feedgrab.utils.http_client.get", fake_get)

    md_path = tmp_path / "doc.md"
    md_path.write_text("# doc", encoding="utf-8")
    video_url = (
        "https://internal-api-drive-stream.feishu.cn/space/api/box/stream/"
        "download/video/HeDEbOzoYoEDBTx50aWcAGqlnBe/?quality=720p"
    )

    download_feishu_media(
        str(md_path),
        [
            {
                "token": "HeDEbOzoYoEDBTx50aWcAGqlnBe",
                "name": "微信视频2026-06-17_203024_102.mp4",
                "mime_type": "video/mp4",
                "media_type": "video",
                "_filename": "001_video.mp4",
                "url": video_url,
            }
        ],
        "https://example.feishu.cn/wiki/doc",
        media_subdir="doc123",
    )

    assert len(requested) == 2
    assert "/download/all/" in requested[0]
    assert requested[1] == video_url
    assert (
        tmp_path / "attachments" / "doc123" / "001_video.mp4"
    ).read_bytes() == b"video-from-stream-url"
