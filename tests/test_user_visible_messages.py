# -*- coding: utf-8 -*-
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_login_user_visible_messages_are_chinese():
    text = _read("feedgrab/login.py")
    forbidden = [
        "Unknown platform:",
        "Supported:",
        "CDP extraction failed, falling back to browser login",
        "Playwright is not installed. Run:",
        "Opening {platform} login page",
        "Please log in manually in the browser window.",
        "Headless login:",
        "QR code will be saved to:",
        "Waiting for login",
        "QR screenshot saved:",
        "Open this image and scan the QR code with your phone.",
        "Login timed out or cancelled. No session saved.",
        "CDP login not configured for platform:",
        "Chrome CDP not reachable on",
        "Enable Remote Debugging:",
        "Or launch Chrome with:",
        "No {canonical} cookies found.",
        "No cookies found for {canonical}.",
        "websocket-client not installed. Run:",
        "Session saved via CDP:",
        "Session saved to",
    ]
    for phrase in forbidden:
        assert phrase not in text


def test_cli_user_visible_messages_are_chinese_for_desktop_commands():
    text = _read("feedgrab/cli.py")
    forbidden = [
        "Failed [",
        "Fetched {success_count}",
        "Cancelled",
        "Playwright is not installed. Run:",
        "Detecting real Chrome User-Agent",
        "Failed to detect UA:",
        "Falling back: trying without channel='chrome'",
        "Detection failed:",
        "Detected:",
        "Updated BROWSER_USER_AGENT",
        "Appended BROWSER_USER_AGENT",
        "Created {env_path} with BROWSER_USER_AGENT",
        "All browser interactions will now use this UA.",
        "feedgrab doctor — full diagnostic",
        "Unknown platform:",
        "Usage: feedgrab doctor",
        "Result:",
        "All checks passed!",
        "Core functionality OK",
        "Some checks failed",
        "Album fetch complete:",
        "Articles:",
        "X keyword search is disabled.",
        "Set X_SEARCH_ENABLED=true",
        "X batch search:",
        "X search complete:",
        "Total tweets:",
        "Individual tweets saved:",
        "X search failed for all keywords:",
        "Zhihu batch search:",
        "Zhihu search complete:",
        "Total results:",
        "Individual answers saved:",
        "XHS batch search:",
        "XHS search complete:",
        "Total notes:",
        "Individual notes saved:",
        "Sogou WeChat search is disabled.",
        "Sogou search complete:",
        "No results found",
        "Download failed",
        "Downloaded:",
        "not available",
    ]
    for phrase in forbidden:
        assert phrase not in text


def test_common_fetcher_user_visible_messages_are_chinese():
    files = [
        "feedgrab/fetchers/xhs.py",
        "feedgrab/fetchers/xhs_api.py",
        "feedgrab/fetchers/wechat.py",
        "feedgrab/fetchers/feishu.py",
        "feedgrab/fetchers/feishu_wiki.py",
        "feedgrab/fetchers/zhihu.py",
        "feedgrab/fetchers/zhihu_search.py",
        "feedgrab/fetchers/twitter.py",
        "feedgrab/fetchers/twitter_keyword_search.py",
        "feedgrab/fetchers/youtube_search.py",
        "feedgrab/fetchers/xhs_search_notes.py",
    ]
    forbidden = [
        "XHS blocked Jina and no saved session found.",
        "Run: feedgrab login xhs",
        "Then retry this URL.",
        "XHS session expired",
        "All XHS fetch methods failed.",
        "Last error:",
        "Try: feedgrab login xhs",
        "XHS session not found:",
        "XHS session missing 'a1' cookie",
        "XHS session expired",
        "All WeChat fetch methods failed.",
        "Feishu session not found. Run: feedgrab login feishu",
        "Wiki batch is disabled.",
        "Set FEISHU_WIKI_BATCH_ENABLED=true",
        "Wiki batch failed:",
        "Run 'feedgrab login feishu'",
        "All methods failed for",
        "Run 'feedgrab login feishu' for browser access",
        "Jina returned login page. Run: feedgrab login zhihu",
        "Tip: Run 'feedgrab login zhihu'",
        "*No results found.*",
        "No results found",
        "yt-dlp not found. Install:",
        "Cookie expired! Run: feedgrab login twitter",
        "All Twitter fetch methods failed",
    ]
    text = "\n".join(_read(path) for path in files)
    for phrase in forbidden:
        assert phrase not in text
