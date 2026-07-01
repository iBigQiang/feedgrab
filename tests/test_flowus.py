# -*- coding: utf-8 -*-
"""FlowUs fetcher tests."""

import asyncio
import sys
import types

from feedgrab.fetchers import flowus
from feedgrab.fetchers.flowus import _BT_MEDIA, _BT_PAGE, _walk_blocks


def _image_blocks(root_id="doc"):
    return {
        root_id: {
            "type": _BT_PAGE,
            "data": {"seoTitle": "FlowUs Doc"},
            "subNodes": ["img"],
        },
        "img": {
            "type": _BT_MEDIA,
            "data": {
                "display": "image",
                "link": "https://my.feishu.cn/space/api/box/stream/download/asynccode/?code=raw",
                "ossName": "oss/abc/image.png",
                "extName": "png",
                "segments": [{"text": "image.png"}],
            },
        },
    }


def test_flowus_remote_mode_prefers_signed_online_image_url():
    signed_url = "https://cdn2.flowus.cn/oss/abc/image.png?time=1&token=signed&role=sharePaid"

    markdown, images = _walk_blocks(
        _image_blocks(),
        "doc",
        localize_images=False,
        online_image_urls={"oss/abc/image.png": signed_url},
    )

    assert f"![image.png]({signed_url})" in markdown
    assert "asynccode" not in markdown
    assert images == []


def test_flowus_local_mode_keeps_attachment_path_and_download_info():
    signed_url = "https://cdn2.flowus.cn/oss/abc/image.png?time=1&token=signed&role=sharePaid"

    markdown, images = _walk_blocks(
        _image_blocks(),
        "doc",
        img_subdir="abc123",
        localize_images=True,
        online_image_urls={"oss/abc/image.png": signed_url},
    )

    assert "![image.png](attachments/abc123/000_image.png)" in markdown
    assert signed_url not in markdown
    assert images == [
        {
            "url": "https://my.feishu.cn/space/api/box/stream/download/asynccode/?code=raw",
            "oss_name": "oss/abc/image.png",
            "filename": "000_image.png",
        }
    ]


def test_connect_flowus_cdp_does_not_persist_incomplete_auth_cookies(monkeypatch, tmp_path):
    class FakePage:
        pass

    class FakeContext:
        async def cookies(self):
            return [
                {"name": "locale", "value": "zh-CN", "domain": ".flowus.cn"},
                {"name": "next_lng", "value": "zh", "domain": ".flowus.cn"},
            ]

        async def new_page(self):
            return FakePage()

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        async def connect_over_cdp(self, _ws_url):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeAsyncPlaywright:
        async def start(self):
            return FakePlaywright()

    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(async_playwright=lambda: FakeAsyncPlaywright()),
    )
    monkeypatch.setattr("feedgrab.config.get_session_dir", lambda: tmp_path)

    result = asyncio.run(flowus._connect_flowus_cdp("https://flowus.cn/share/test"))

    assert result is not None
    assert not (tmp_path / "flowus.json").exists()


def test_fetch_flowus_remote_mode_resolves_signed_online_image_url(monkeypatch):
    url = "https://flowus.cn/share/08d68f8b-5968-4b5e-a5d5-291de4b3ae4c?code=TEST"
    signed_url = "https://cdn2.flowus.cn/oss/abc/image.png?time=1&token=signed&role=sharePaid"
    doc_id = "08d68f8b-5968-4b5e-a5d5-291de4b3ae4c"

    monkeypatch.setattr("feedgrab.config.flowus_download_images", lambda: False)
    monkeypatch.setattr(
        flowus,
        "_fetch_via_http",
        lambda *_args: {
            "doc_data": {"blocks": _image_blocks(doc_id)},
            "public_data": {},
            "sharer": {},
        },
    )

    def fake_resolve(doc_url, oss_names):
        assert doc_url == url
        assert oss_names == ["oss/abc/image.png"]
        return {"oss/abc/image.png": signed_url}

    monkeypatch.setattr(flowus, "_resolve_image_urls_from_dom", fake_resolve)

    data = asyncio.run(flowus.fetch_flowus(url))

    assert f"![image.png]({signed_url})" in data["content"]
    assert "asynccode" not in data["content"]
    assert data["images_info"] == []
