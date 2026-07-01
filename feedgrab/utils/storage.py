# -*- coding: utf-8 -*-
"""
Storage utilities — save per-platform Markdown files.

- output/{Platform}/{title}.md (one file per item, for human reading)
"""

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger

from feedgrab.schema import UnifiedContent, SourceType


def _format_twitter_datetime(created_at: str) -> str:
    """Parse Twitter's RFC 2822 created_at into 'YYYY-MM-DD HH:MM' for display."""
    from feedgrab.config import parse_twitter_date_local
    return parse_twitter_date_local(created_at, "%Y-%m-%d %H:%M")


def _parse_xhs_date(raw: str) -> str:
    """Parse XHS date string into 'YYYY-MM-DD'.

    Formats:
      - "02-18 福建"             → MM-DD + location (assume current year)
      - "编辑于 2025-08-16"      → full date with year
      - "3天前 江苏"              → relative time (N天前/昨天/前天/N小时前/N分钟前)
      - "编辑于 昨天 10:17 福建"  → "编辑于" + relative time + HH:MM + location
      - "编辑于 3天前 福建"       → "编辑于" + relative time + location
    """
    if not raw:
        return ""
    text = raw.strip()

    # Strip "编辑于" prefix for uniform parsing
    text = re.sub(r"^编辑于\s*", "", text)

    # Format: full date "YYYY-MM-DD"
    full_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if full_match:
        return f"{full_match.group(1)}-{full_match.group(2)}-{full_match.group(3)}"

    # Format: relative time → convert to absolute date
    now = datetime.now()
    days_match = re.match(r"(\d+)\s*天前", text)
    if days_match:
        return (now - timedelta(days=int(days_match.group(1)))).strftime("%Y-%m-%d")
    if text.startswith("昨天"):
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if text.startswith("前天"):
        return (now - timedelta(days=2)).strftime("%Y-%m-%d")
    if re.match(r"\d+\s*小时前", text) or re.match(r"\d+\s*分钟前", text) or text.startswith("刚刚"):
        return now.strftime("%Y-%m-%d")

    # Format: "MM-DD ..." (no year)
    match = re.match(r"(\d{2})-(\d{2})", text)
    if not match:
        return ""
    month, day = int(match.group(1)), int(match.group(2))
    try:
        candidate = datetime(now.year, month, day)
        if candidate > now:
            candidate = datetime(now.year - 1, month, day)
        return candidate.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _parse_xhs_location(raw: str) -> str:
    """Extract location from XHS date string.

    '02-18 福建'    → '福建'
    '3天前 江苏'    → '江苏'
    '编辑于 2025-08-16' → '' (no location)
    """
    if not raw:
        return ""
    text = raw.strip()
    # "编辑于" format has no location
    if "编辑于" in text:
        return ""
    # "MM-DD location"
    match = re.match(r"\d{2}-\d{2}\s+(.+)", text)
    if match:
        return match.group(1).strip()
    # Relative time: "3天前 江苏", "昨天 21:33北京", "N小时前广东"
    rel_match = re.match(r"(?:\d+\s*[天小时分钟]+前|昨天.*?|前天|刚刚)\s*(\S+)$", text)
    if rel_match:
        loc = rel_match.group(1).strip()
        # Filter out time strings like "21:33" that aren't locations
        if not re.match(r"\d", loc):
            return loc
    return ""


def _yaml_quoted_single_line(value: str) -> str:
    """Return a double-quoted YAML scalar safe for Obsidian Properties."""
    text = re.sub(r"[\r\n\t]+", " ", str(value or ""))
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _format_subtitle_text(text: str) -> str:
    """Format subtitle/transcript text with paragraph breaks.

    SRT/VTT subtitles are joined into one long string. This function
    inserts paragraph breaks at sentence boundaries to improve readability.
    Splits roughly every 3-5 sentences (targeting ~200-300 char segments).
    """
    if not text or len(text) < 100:
        return text

    # If already has paragraph breaks, leave as-is
    if "\n\n" in text:
        return text

    # Split at sentence-ending punctuation followed by space
    # Covers Chinese（。！？）and English (. ! ?)
    parts = re.split(r'(?<=[。！？.!?])\s+', text)
    if len(parts) <= 1:
        return text

    paragraphs = []
    current = []
    char_count = 0

    for part in parts:
        current.append(part)
        char_count += len(part)
        # Break at ~200-300 chars for readable paragraphs
        if char_count >= 250:
            paragraphs.append(" ".join(current))
            current = []
            char_count = 0

    if current:
        paragraphs.append(" ".join(current))

    return "\n\n".join(paragraphs)


def _format_iso_datetime(raw: str, with_time: bool = True) -> str:
    """Parse ISO 8601 datetime into local time string."""
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")
    except ValueError:
        return raw[:16] if with_time else raw[:10]


# SourceType → subdirectory name
PLATFORM_FOLDER_MAP = {
    SourceType.TWITTER: "X",
    SourceType.XIAOHONGSHU: "XHS",
    SourceType.BILIBILI: "Bilibili",
    SourceType.WECHAT: "mpweixin",
    SourceType.YOUTUBE: "YouTube",
    SourceType.GITHUB: "GitHub",
    SourceType.FEISHU: "Feishu",
    SourceType.KDOCS: "KDocs",
    SourceType.YOUDAO: "NoteYouDao",
    SourceType.ZHIHU: "Zhihu",
    SourceType.LINUXDO: "LinuxDo",
    SourceType.IDCFLARE: "IDCFlare",
    SourceType.XIAOYUZHOU: "Xiaoyuzhou",
    SourceType.XIMALAYA: "Ximalaya",
    SourceType.HACKERNEWS: "HackerNews",
    SourceType.MEDIUM: "Medium",
    SourceType.REDDIT: "Reddit",
    SourceType.WEIBO: "Weibo",
    SourceType.DOUYIN: "Douyin",
    SourceType.ZSXQ: "Zsxq",
    SourceType.FLOWUS: "FlowUs",
    SourceType.TELEGRAM: "Telegram",
    SourceType.RSS: "RSS",
    SourceType.WEB: "Web",
    SourceType.MANUAL: "Manual",
}

# Windows reserved filenames (case-insensitive)
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})

# Characters illegal in filenames on Windows / most filesystems
_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _sanitize_filename(name: str) -> str:
    """Clean a string for use as a filename.

    - Strip control characters and illegal chars
    - Collapse whitespace
    - Guard against Windows reserved names
    - Truncate to 100 chars without cutting words
    """
    # Replace illegal chars with space
    name = _ILLEGAL_CHARS_RE.sub(" ", name)
    # Strip leading/trailing dots and spaces (Windows quirk)
    name = name.strip(". ")
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    # Guard against Windows reserved names
    stem = name.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        name = f"_{name}"
    # Truncate to 100 chars without cutting words
    if len(name) > 100:
        truncated = name[:100]
        last_space = truncated.rfind(" ")
        if last_space > 50:
            truncated = truncated[:last_space]
        name = truncated.rstrip(". ")
    return name


def _generate_filename(item: UnifiedContent) -> str:
    """Build a clean filename (without extension) from an item.

    Twitter format: "作者名_YYYY-MM-DD：标题"
    Other platforms: title → content prefix → id
    """
    extra = item.extra or {}

    # Determine the display title
    if item.title and item.title.strip():
        raw_title = item.title.strip()
    elif item.content and item.content.strip():
        # Strip leading ![cover](...) markdown image (e.g. WeChat prepends cover)
        text = re.sub(r'^!\[cover\]\([^)]*\)\s*', '', item.content.strip())
        raw_title = text[:50] if text else item.id
    else:
        raw_title = item.id

    # Twitter: prepend author display name + published date
    if item.source_type == SourceType.TWITTER:
        author_display = extra.get("author_name", "") or item.source_name or ""
        # Remove @ prefix for cleaner filename
        author_display = author_display.lstrip("@").strip()

        # Parse published date from Twitter's created_at
        published = ""
        if extra.get("created_at"):
            from feedgrab.config import parse_twitter_date_local
            published = parse_twitter_date_local(extra["created_at"])

        if author_display and published:
            raw = f"{author_display}_{published}：{raw_title}"
        elif author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    elif item.source_type == SourceType.XIAOHONGSHU:
        author_display = (item.source_name or "").strip()
        published = _parse_xhs_date(extra.get("date", ""))

        if author_display and published:
            raw = f"{author_display}_{published}：{raw_title}"
        elif author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    elif item.source_type == SourceType.WECHAT:
        author_display = (item.source_name or "").strip()
        # Only use date (no time) in filename
        published = extra.get("publish_date", "")[:10]

        if author_display and published:
            raw = f"{author_display}_{published}：{raw_title}"
        elif author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    elif item.source_type == SourceType.YOUTUBE:
        author_display = (item.source_name or "").strip()
        published = extra.get("published_at", "")[:10]

        if author_display and published:
            raw = f"{author_display}_{published}：{raw_title}"
        elif author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    elif item.source_type == SourceType.FEISHU:
        author_display = (item.source_name or "").strip()
        if author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    elif item.source_type == SourceType.GITHUB:
        owner = extra.get("owner", "")
        repo = extra.get("repo", "")
        if owner and repo:
            raw = f"{owner}_{repo}：{raw_title}" if raw_title else f"{owner}_{repo}"
        else:
            raw = raw_title
    elif item.source_type in (SourceType.LINUXDO, SourceType.IDCFLARE):
        author_display = (item.source_name or "").strip()
        published = _format_iso_datetime(extra.get("created_at", ""), with_time=False)

        if author_display and published:
            raw = f"{author_display}_{published}：{raw_title}"
        elif author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    elif item.source_type == SourceType.HACKERNEWS:
        author_display = (item.source_name or "").strip()
        # HN created_at is "YYYY-MM-DD HH:MM" — keep date only
        published = (extra.get("created_at") or "")[:10]

        if author_display and published:
            raw = f"{author_display}_{published}：{raw_title}"
        elif author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    elif item.source_type == SourceType.MEDIUM:
        author_display = (item.source_name or "").strip()
        # Medium published may be RFC822 ("Wed, 02 Apr 2026 10:11:12 GMT") or ISO 8601
        published = _format_iso_datetime(extra.get("published_at", ""), with_time=False)

        if author_display and published:
            raw = f"{author_display}_{published}：{raw_title}"
        elif author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    elif item.source_type == SourceType.REDDIT:
        sub = (extra.get("subreddit") or "").strip()
        published = (extra.get("created_at") or "")[:10]
        prefix = f"r_{sub}" if sub else "reddit"
        if published:
            raw = f"{prefix}_{published}：{raw_title}"
        else:
            raw = f"{prefix}：{raw_title}"
    elif item.source_type == SourceType.WEIBO:
        author_display = (item.source_name or "").strip()
        published = (extra.get("created_at") or "")[:10]
        if author_display and published:
            raw = f"{author_display}_{published}：{raw_title}"
        elif author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    elif item.source_type == SourceType.DOUYIN:
        author_display = (item.source_name or "").strip()
        published = (extra.get("created_at") or "")[:10]
        if author_display and published:
            raw = f"{author_display}_{published}：{raw_title}"
        elif author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    elif item.source_type == SourceType.ZSXQ:
        author_display = (item.source_name or "").strip()
        published = (extra.get("created_at") or "")[:10]
        if author_display and published:
            raw = f"{author_display}_{published}：{raw_title}"
        elif author_display:
            raw = f"{author_display}：{raw_title}"
        else:
            raw = raw_title
    else:
        raw = raw_title

    name = _sanitize_filename(raw)
    if not name:
        name = item.id
    return name


def _resolve_filepath(directory: Path, name: str, item_id: str) -> Path:
    """Return a unique .md path inside *directory*.

    If ``name.md`` already exists but was NOT produced by the same item
    (different item_id), append ``_<item_id>`` to disambiguate.
    Same item_id → same path (allows overwrite / update).
    """
    candidate = directory / f"{name}.md"
    if not candidate.exists():
        return candidate

    # Check if the existing file belongs to the same item (same id → overwrite)
    try:
        with open(candidate, "r", encoding="utf-8") as f:
            head = f.read(2048)
        if f"item_id: {item_id}" in head:
            return candidate
    except OSError:
        pass

    # Conflict: different item produced the same filename
    return directory / f"{name}_{item_id}.md"


def _format_markdown(item: UnifiedContent) -> str:
    """Build the full Markdown content with Obsidian-compatible YAML front matter."""
    is_twitter = item.source_type == SourceType.TWITTER
    is_xhs = item.source_type == SourceType.XIAOHONGSHU
    is_wechat = item.source_type == SourceType.WECHAT
    extra = item.extra or {}

    # --- Parse published date ---
    published = ""
    if extra.get("created_at"):
        # Twitter: RFC 2822 → local timezone
        from feedgrab.config import parse_twitter_date_local
        published = parse_twitter_date_local(extra["created_at"])
    elif is_xhs and extra.get("date"):
        # XHS: "02-18 福建"
        published = _parse_xhs_date(extra["date"])
    elif is_wechat and extra.get("publish_date"):
        published = extra["publish_date"]
    elif extra.get("create_time") and item.source_type in (SourceType.FEISHU, SourceType.KDOCS, SourceType.YOUDAO, SourceType.FLOWUS):
        published = extra["create_time"][:10]  # YYYY-MM-DD
    elif item.source_type in (SourceType.LINUXDO, SourceType.IDCFLARE) and extra.get("created_at"):
        published = _format_iso_datetime(extra["created_at"])
    elif item.source_type == SourceType.HACKERNEWS and extra.get("created_at"):
        # HN already formatted as "YYYY-MM-DD HH:MM"
        published = extra["created_at"][:10]
    elif item.source_type == SourceType.MEDIUM and extra.get("published_at"):
        published = _format_iso_datetime(extra["published_at"], with_time=False)
    elif item.source_type in (SourceType.REDDIT, SourceType.WEIBO, SourceType.DOUYIN) and extra.get("created_at"):
        published = _format_iso_datetime(extra["created_at"], with_time=False)
    elif item.source_type == SourceType.ZSXQ and extra.get("created_at"):
        published = _format_iso_datetime(extra["created_at"], with_time=False)
    fetched_date = item.fetched_at[:10] if item.fetched_at else ""

    # --- YAML front matter (Obsidian Properties format) ---
    fm_lines = [
        "---",
        f"title: {_yaml_quoted_single_line(item.title)}",
        f'source: "{item.url}"',
        f"author:",
        f'  - "{item.source_name}"',
    ]

    if extra.get("author_name") and not is_xhs:
        fm_lines.append(f'author_name: "{extra["author_name"]}"')
    if is_xhs and extra.get("author_url"):
        fm_lines.append(f'author_url: "{extra["author_url"]}"')

    if published:
        fm_lines.append(f"published: {published}")
    fm_lines.append(f"created: {fetched_date}")

    if extra.get("cover_image"):
        fm_lines.append(f'cover_image: "{extra["cover_image"]}"')

    # Twitter metrics (always show, including 0)
    if is_twitter:
        fm_lines.append(f'tweet_type: "{extra.get("tweet_type", "status")}"')
        fm_lines.append(f"tweet_count: {extra.get('tweet_count', 1)}")
        fm_lines.append(f"has_thread: {str(extra.get('has_thread', False)).lower()}")
        # P0-3: pinned tweet marker
        if extra.get("is_pinned"):
            fm_lines.append(f"is_pinned: true")
        # v0.23.0: ModeratedTimeline marker
        if extra.get("has_moderated_replies"):
            fm_lines.append(
                f"moderated_replies_count: "
                f"{len(extra.get('moderated_replies', []) or [])}"
            )
        for metric in ("likes", "retweets", "replies", "bookmarks", "views"):
            fm_lines.append(f"{metric}: {extra.get(metric, 0)}")
        # New: quote_count (被引用次数)
        fm_lines.append(f"quotes: {extra.get('quote_count', 0)}")
        # New: author profile metadata
        if extra.get("is_blue_verified"):
            fm_lines.append(f"is_blue_verified: true")
        fm_lines.append(f"followers_count: {extra.get('followers_count', 0)}")
        fm_lines.append(f"statuses_count: {extra.get('statuses_count', 0)}")
        fm_lines.append(f"listed_count: {extra.get('listed_count', 0)}")
        # New: tweet metadata
        if extra.get("lang"):
            fm_lines.append(f'lang: "{extra["lang"]}"')
        if extra.get("source_app"):
            fm_lines.append(f'source_app: "{extra["source_app"]}"')
        if extra.get("possibly_sensitive"):
            fm_lines.append(f"possibly_sensitive: true")

    # XHS metrics (always show, including 0)
    if is_xhs:
        for metric in ("likes", "collects", "comments"):
            fm_lines.append(f"{metric}: {extra.get(metric, 0)}")
        location = _parse_xhs_location(extra.get("date", ""))
        if location:
            fm_lines.append(f'location: "{location}"')

    # WeChat metadata
    if is_wechat:
        # cover_image already handled by the generic block above;
        # only add thumbnail fallback when no cover_image exists
        if not extra.get("cover_image") and extra.get("thumbnail"):
            fm_lines.append(f'cover_image: "{extra["thumbnail"]}"')
        if extra.get("summary"):
            fm_summary = extra["summary"].replace('"', '\\"')[:200]
            fm_lines.append(f'summary: "{fm_summary}"')
        if extra.get("original_url"):
            fm_lines.append(f'original_url: "{extra["original_url"]}"')
        if extra.get("search_keyword"):
            fm_lines.append(f'search_keyword: "{extra["search_keyword"]}"')
        # Engagement metrics (only when available via authenticated session)
        if extra.get("reads"):
            fm_lines.append(f"reads: {extra['reads']}")
            fm_lines.append(f"likes: {extra.get('likes', 0)}")
            fm_lines.append(f"wow: {extra.get('wow', 0)}")
            fm_lines.append(f"shares: {extra.get('shares', 0)}")
            fm_lines.append(f"comments: {extra.get('comments', 0)}")

    # Bilibili extras
    if item.source_type == SourceType.BILIBILI:
        if extra.get("bvid"):
            fm_lines.append(f"bvid: {extra['bvid']}")
        if extra.get("duration"):
            fm_lines.append(f"duration: {extra['duration']}")

    # YouTube extras
    if item.source_type == SourceType.YOUTUBE:
        if extra.get("published_at"):
            fm_lines.append(f"published: {extra['published_at']}")
        if extra.get("duration"):
            fm_lines.append(f'duration: "{extra["duration"]}"')
        if extra.get("view_count"):
            fm_lines.append(f"views: {extra['view_count']}")
        if extra.get("like_count"):
            fm_lines.append(f"likes: {extra['like_count']}")
        if extra.get("comment_count"):
            fm_lines.append(f"comments: {extra['comment_count']}")
        if extra.get("definition"):
            fm_lines.append(f'definition: "{extra["definition"]}"')
        if extra.get("has_transcript"):
            fm_lines.append(f"has_transcript: true")
        if extra.get("thumbnail"):
            fm_lines.append(f'cover_image: "{extra["thumbnail"]}"')
        if extra.get("channel_id"):
            fm_lines.append(f'channel_id: "{extra["channel_id"]}"')
        if extra.get("video_id"):
            fm_lines.append(f'video_id: "{extra["video_id"]}"')
        if extra.get("search_keyword"):
            fm_lines.append(f'search_keyword: "{extra["search_keyword"]}"')

    # GitHub extras
    is_github = item.source_type == SourceType.GITHUB
    if is_github:
        if extra.get("description"):
            fm_desc = extra["description"].replace('"', '\\"')[:200]
            fm_lines.append(f'description: "{fm_desc}"')
        fm_lines.append(f"stars: {extra.get('stars', 0)}")
        fm_lines.append(f"forks: {extra.get('forks', 0)}")
        if extra.get("language"):
            fm_lines.append(f'language: "{extra["language"]}"')
        if extra.get("license"):
            fm_lines.append(f'license: "{extra["license"]}"')
        if extra.get("default_branch"):
            fm_lines.append(f'default_branch: "{extra["default_branch"]}"')
        if extra.get("open_issues"):
            fm_lines.append(f"open_issues: {extra['open_issues']}")
        if extra.get("created_at"):
            fm_lines.append(f'repo_created: "{extra["created_at"][:10]}"')
        if extra.get("updated_at"):
            fm_lines.append(f'repo_updated: "{extra["updated_at"][:10]}"')
        if extra.get("pushed_at"):
            fm_lines.append(f'last_push: "{extra["pushed_at"][:10]}"')
        if extra.get("readme_file") and extra["readme_file"] != "README.md":
            fm_lines.append(f'readme_file: "{extra["readme_file"]}"')

    # Feishu extras
    is_feishu = item.source_type == SourceType.FEISHU
    if is_feishu:
        if extra.get("doc_type"):
            fm_lines.append(f'doc_type: "{extra["doc_type"]}"')
        if extra.get("doc_token"):
            fm_lines.append(f'doc_token: "{extra["doc_token"]}"')

    # KDocs extras
    is_kdocs = item.source_type == SourceType.KDOCS
    if is_kdocs:
        if extra.get("doc_token"):
            fm_lines.append(f'doc_token: "{extra["doc_token"]}"')
        if extra.get("edit_time"):
            fm_lines.append(f'edit_time: "{extra["edit_time"]}"')

    # FlowUs extras
    is_flowus = item.source_type == SourceType.FLOWUS
    if is_flowus:
        if extra.get("doc_token"):
            fm_lines.append(f'doc_token: "{extra["doc_token"]}"')
        if extra.get("share_code"):
            fm_lines.append(f'share_code: "{extra["share_code"]}"')
        if extra.get("space_title"):
            fm_lines.append(f'space_title: "{extra["space_title"]}"')
        if extra.get("edit_time"):
            fm_lines.append(f'edit_time: "{extra["edit_time"]}"')

    # Youdao Note extras
    is_youdao = item.source_type == SourceType.YOUDAO
    if is_youdao:
        if extra.get("share_key"):
            fm_lines.append(f'share_key: "{extra["share_key"]}"')
        if extra.get("page_views"):
            fm_lines.append(f"page_views: {extra['page_views']}")
        if extra.get("edit_time"):
            fm_lines.append(f'edit_time: "{extra["edit_time"]}"')

    # Zhihu extras
    is_zhihu = item.source_type == SourceType.ZHIHU
    if is_zhihu:
        if extra.get("content_type"):
            fm_lines.append(f'content_type: "{extra["content_type"]}"')
        fm_lines.append(f"upvotes: {extra.get('upvotes', 0)}")
        fm_lines.append(f"comments: {extra.get('comments', 0)}")
        fm_lines.append(f"thanks: {extra.get('thanks', 0)}")
        fm_lines.append(f"collected: {extra.get('collected', 0)}")
        if extra.get("views"):
            fm_lines.append(f"views: {extra['views']}")
        if extra.get("publish_date"):
            fm_lines.append(f"published: {extra['publish_date'][:10]}")

    # LinuxDo / Discourse extras
    is_discourse = item.source_type in (SourceType.LINUXDO, SourceType.IDCFLARE)
    if is_discourse:
        if extra.get("topic_id"):
            fm_lines.append(f'topic_id: "{extra["topic_id"]}"')
        if extra.get("topic_slug"):
            fm_lines.append(f'topic_slug: "{extra["topic_slug"]}"')
        if extra.get("category"):
            fm_lines.append(f'forum_category: "{extra["category"]}"')
        if extra.get("category_id"):
            fm_lines.append(f"category_id: {extra['category_id']}")
        fm_lines.append(f"posts_count: {extra.get('posts_count', 0)}")
        fm_lines.append(f"reply_count: {extra.get('reply_count', 0)}")
        if extra.get("reply_mode"):
            fm_lines.append(f'reply_mode: "{extra["reply_mode"]}"')
        fm_lines.append(f"rendered_reply_count: {extra.get('rendered_reply_count', 0)}")
        fm_lines.append(f"likes: {extra.get('like_count', 0)}")
        fm_lines.append(f"views: {extra.get('views', 0)}")
        if extra.get("last_posted_at"):
            fm_lines.append(f'last_posted_at: "{_format_iso_datetime(extra["last_posted_at"])}"')

    # HackerNews extras
    is_hn = item.source_type == SourceType.HACKERNEWS
    if is_hn:
        if extra.get("hn_id"):
            fm_lines.append(f'hn_id: "{extra["hn_id"]}"')
        if extra.get("hn_type"):
            fm_lines.append(f'hn_type: "{extra["hn_type"]}"')
        fm_lines.append(f"score: {extra.get('score', 0)}")
        fm_lines.append(f"comment_count: {extra.get('comment_count', 0)}")
        if extra.get("linked_url"):
            fm_lines.append(f'linked_url: "{extra["linked_url"]}"')

    # Medium extras
    is_medium = item.source_type == SourceType.MEDIUM
    if is_medium:
        if extra.get("cover_image"):
            fm_lines.append(f'cover_image: "{extra["cover_image"]}"')
        fm_lines.append(f"is_member_only: {str(bool(extra.get('is_member_only', False))).lower()}")

    # Reddit extras
    is_reddit = item.source_type == SourceType.REDDIT
    if is_reddit:
        if extra.get("reddit_id"):
            fm_lines.append(f'reddit_id: "{extra["reddit_id"]}"')
        if extra.get("subreddit"):
            fm_lines.append(f'subreddit: "{extra["subreddit"]}"')
        if extra.get("flair"):
            fm_lines.append(f'flair: "{extra["flair"]}"')
        fm_lines.append(f"score: {extra.get('score', 0)}")
        if extra.get("upvote_ratio"):
            fm_lines.append(f"upvote_ratio: {extra['upvote_ratio']}")
        fm_lines.append(f"comment_count: {extra.get('comment_count', 0)}")
        fm_lines.append(f"is_self: {str(bool(extra.get('is_self', True))).lower()}")
        if extra.get("linked_url"):
            fm_lines.append(f'linked_url: "{extra["linked_url"]}"')
        if extra.get("search_keyword"):
            fm_lines.append(f'search_keyword: "{extra["search_keyword"]}"')
        if extra.get("search_sort"):
            fm_lines.append(f'search_sort: "{extra["search_sort"]}"')
        if extra.get("search_time_range"):
            fm_lines.append(f'search_time_range: "{extra["search_time_range"]}"')
        if extra.get("result_type"):
            fm_lines.append(f'result_type: "{extra["result_type"]}"')

    # Weibo extras
    is_weibo = item.source_type == SourceType.WEIBO
    if is_weibo:
        if extra.get("mid"):
            fm_lines.append(f'mid: "{extra["mid"]}"')
        if extra.get("uid"):
            fm_lines.append(f'author_uid: "{extra["uid"]}"')
        fm_lines.append(f"likes: {extra.get('likes', 0)}")
        fm_lines.append(f"comments: {extra.get('comments', 0)}")
        fm_lines.append(f"reposts: {extra.get('reposts', 0)}")
        if extra.get("source_app"):
            fm_lines.append(f'source_app: "{extra["source_app"]}"')
        if extra.get("mblog_type"):
            fm_lines.append(f'mblog_type: "{extra["mblog_type"]}"')

    # Douyin extras
    is_douyin = item.source_type == SourceType.DOUYIN
    if is_douyin:
        if extra.get("aweme_id"):
            fm_lines.append(f'aweme_id: "{extra["aweme_id"]}"')
        if extra.get("aweme_type"):
            fm_lines.append(f'aweme_type: "{extra["aweme_type"]}"')
        if extra.get("author_sec_uid"):
            fm_lines.append(f'author_sec_uid: "{extra["author_sec_uid"]}"')
        fm_lines.append(f"plays: {extra.get('plays', 0)}")
        fm_lines.append(f"likes: {extra.get('likes', 0)}")
        fm_lines.append(f"comments: {extra.get('comments', 0)}")
        fm_lines.append(f"shares: {extra.get('shares', 0)}")
        if extra.get("duration_seconds"):
            fm_lines.append(f"duration_seconds: {extra['duration_seconds']}")
        if extra.get("music_title"):
            fm_lines.append(f'music_title: "{extra["music_title"]}"')
        if extra.get("music_author"):
            fm_lines.append(f'music_author: "{extra["music_author"]}"')
        if extra.get("cover_image"):
            fm_lines.append(f'cover_image: "{extra["cover_image"]}"')

    # Zsxq extras
    is_zsxq = item.source_type == SourceType.ZSXQ
    if is_zsxq:
        if extra.get("zsxq_type"):
            fm_lines.append(f'zsxq_type: "{extra["zsxq_type"]}"')
        if extra.get("article_id"):
            fm_lines.append(f'article_id: "{extra["article_id"]}"')
        if extra.get("topic_id"):
            fm_lines.append(f'topic_id: "{extra["topic_id"]}"')
        if extra.get("group_id"):
            fm_lines.append(f'group_id: "{extra["group_id"]}"')
        if extra.get("group_name"):
            fm_lines.append(f'group_name: "{extra["group_name"]}"')
        fm_lines.append(f"likes: {extra.get('likes', 0)}")
        fm_lines.append(f"comments: {extra.get('comments', 0)}")
        if extra.get("reads"):
            fm_lines.append(f"reads: {extra['reads']}")
        if extra.get("rewards"):
            fm_lines.append(f"rewards: {extra['rewards']}")
        if extra.get("comment_mode"):
            fm_lines.append(f'comment_mode: "{extra["comment_mode"]}"')
        if extra.get("rendered_comment_count") is not None:
            fm_lines.append(f"rendered_comment_count: {extra.get('rendered_comment_count', 0)}")

    # Tags (from tweet hashtags or other sources)
    if item.tags:
        # XHS: only top 3 tags in front matter (full list goes in body)
        fm_tags = item.tags[:3] if is_xhs else item.tags
        fm_lines.append("tags:")
        for tag in fm_tags:
            fm_lines.append(f'  - "{tag}"')

    # Internal tracking
    fm_lines.append(f"item_id: {item.id}")

    fm_lines.append("---")
    fm_lines.append("")  # blank line after front matter

    # WeChat images require no-referrer to avoid 403 from mmbiz.qpic.cn
    if is_wechat:
        fm_lines.append('<meta name="referrer" content="no-referrer">')
        fm_lines.append("")

    # --- body ---
    if is_twitter:
        # Twitter threads already formatted with [1/N], just output content
        fm_lines.append(item.content)

        # --- 作者回帖 (author replies to commenters) ---
        author_replies = extra.get("author_replies", [])
        if author_replies:
            fm_lines.append("")
            fm_lines.append("---")
            fm_lines.append("")
            fm_lines.append("## 作者回帖")
            fm_lines.append("")
            for idx, reply in enumerate(author_replies, 1):
                date_str = _format_twitter_datetime(reply.get("created_at", ""))
                text = reply.get("text", "").strip()
                if text:
                    fm_lines.append(f"**[回帖 {idx}]** {date_str}")
                    fm_lines.append(text)
                    fm_lines.append("")

        # --- 评论区 (other users' comments) ---
        comments = extra.get("comments", [])
        if comments:
            fm_lines.append("")
            fm_lines.append("---")
            fm_lines.append("")
            fm_lines.append(f"## 评论区 ({len(comments)}条)")
            fm_lines.append("")
            for c in comments:
                c_author = c.get("author", "")
                date_str = _format_twitter_datetime(c.get("created_at", ""))
                likes = c.get("likes", 0)
                text = c.get("text", "").strip()
                if text:
                    meta = [f"**@{c_author}**"]
                    if date_str:
                        meta.append(date_str)
                    if likes:
                        meta.append(f"❤️ {likes}")
                    fm_lines.append(" · ".join(meta))
                    fm_lines.append(text)
                    fm_lines.append("")

        # --- v0.23.0: 被作者隐藏的回复 (ModeratedTimeline opt-in) ---
        moderated_replies = extra.get("moderated_replies", [])
        if moderated_replies:
            fm_lines.append("")
            fm_lines.append("---")
            fm_lines.append("")
            fm_lines.append(
                f"## ⚠️ 被作者隐藏的回复 ({len(moderated_replies)}条)"
            )
            fm_lines.append("")
            fm_lines.append(
                "> 以下回复被推文作者隐藏，仅作者本人 cookie 可见。"
            )
            fm_lines.append("")
            for c in moderated_replies:
                c_author = c.get("author", "")
                date_str = _format_twitter_datetime(c.get("created_at", ""))
                likes = c.get("likes", 0)
                text = c.get("text", "").strip()
                if text:
                    meta = [f"**@{c_author}**"]
                    if date_str:
                        meta.append(date_str)
                    if likes:
                        meta.append(f"❤️ {likes}")
                    fm_lines.append(" · ".join(meta))
                    fm_lines.append(text)
                    fm_lines.append("")
    elif is_xhs:
        # XHS: 文字在前，标签在中，图片相册在后（按翻页顺序）
        if item.title and item.title.strip():
            fm_lines.append(f"# {item.title.strip()}")
            fm_lines.append("")
        if item.content:
            fm_lines.append(item.content)
            fm_lines.append("")
        # 全部标签（保持小红书原始 #标签 格式）
        if item.tags:
            tag_line = " ".join(f"#{t}" for t in item.tags)
            fm_lines.append(tag_line)
            fm_lines.append("")
        images = extra.get("images", [])
        if images:
            for i, img in enumerate(images, 1):
                fm_lines.append(f"![{i}]({img})")
                fm_lines.append("")
        # XHS comments section (when fetched via API)
        comment_list = extra.get("comment_list", [])
        if comment_list:
            fm_lines.append("## 评论")
            fm_lines.append("")
            for c in comment_list:
                user = c.get("user_nickname", "匿名")
                text = c.get("content", "").strip()
                likes = c.get("like_count", 0)
                sub_comments = c.get("sub_comments", [])
                fm_lines.append(f"> **{user}**（{likes} 赞）：{text}")
                fm_lines.append(">")
                for sc in sub_comments:
                    sc_user = sc.get("user_nickname", "匿名")
                    sc_text = sc.get("content", "").strip()
                    fm_lines.append(f">> **{sc_user}**：{sc_text}")
                    fm_lines.append(">>")
                fm_lines.append("")
    else:
        # Non-Twitter/XHS: add title heading + full content
        # WeChat / YouTube / GitHub / Feishu: skip title heading
        # (Feishu content already includes the document title from block tree)
        is_youtube = item.source_type == SourceType.YOUTUBE
        if not is_wechat and not is_youtube and not is_github and not is_feishu and not is_kdocs and not is_youdao and not is_zhihu and not is_hn and not is_weibo and item.title and item.title.strip():
            fm_lines.append(f"# {item.title.strip()}")
            fm_lines.append("")

        # YouTube: cover image preview + subtitle paragraph formatting
        if is_youtube:
            if extra.get("thumbnail"):
                fm_lines.append(f"![cover]({extra['thumbnail']})")
                fm_lines.append("")
            fm_lines.append(_format_subtitle_text(item.content))
        elif is_zhihu and extra.get("content_type") in ("answer", "question"):
            # Zhihu Q&A: question detail + multi-answer content
            if item.title:
                fm_lines.append(f"# {item.title.strip()}")
                fm_lines.append("")
            q_detail = extra.get("question_detail", "").strip()
            if q_detail:
                fm_lines.append("## 问题描述")
                fm_lines.append("")
                fm_lines.append(q_detail)
                fm_lines.append("")

            answers_list = extra.get("answers_list", [])
            if answers_list:
                total = len(answers_list)
                for idx, ans in enumerate(answers_list):
                    fm_lines.append("---")
                    fm_lines.append("")
                    fm_lines.append(f"## [{idx+1}/{total}楼] {ans.get('author', '匿名')}")
                    fm_lines.append("")
                    # Engagement stats line
                    stats = []
                    stats.append(f"赞同 {ans.get('upvotes', 0)}")
                    stats.append(f"评论 {ans.get('comments', 0)}")
                    stats.append(f"收藏 {ans.get('collected', 0)}")
                    stats.append(f"喜欢 {ans.get('thanks', 0)}")
                    fm_lines.append(f"> {' · '.join(stats)}")
                    fm_lines.append("")
                    content = ans.get("content", "")
                    if content:
                        fm_lines.append(content)
                    fm_lines.append("")
            elif item.content:
                # Single answer fallback (no answers_list)
                fm_lines.append("## 回答")
                fm_lines.append("")
                fm_lines.append(item.content)
        else:
            fm_lines.append(item.content)

        # WeChat comments section (when fetched via appmsg_comment API)
        if is_wechat:
            comment_list = extra.get("comment_list", [])
            if comment_list:
                fm_lines.append("")
                fm_lines.append("## 评论")
                fm_lines.append("")
                for c in comment_list:
                    user = c.get("user_nickname", "匿名")
                    text = c.get("content", "").strip()
                    likes = c.get("like_count", 0)
                    sub_comments = c.get("sub_comments", [])
                    fm_lines.append(f"> **{user}**（{likes} 赞）：{text}")
                    fm_lines.append(">")
                    for sc in sub_comments:
                        sc_user = sc.get("user_nickname", "匿名")
                        sc_text = sc.get("content", "").strip()
                        fm_lines.append(f">> **{sc_user}**：{sc_text}")
                        fm_lines.append(">>")
                    fm_lines.append("")

    fm_lines.append("")  # trailing newline
    result = "\n".join(fm_lines)
    # Strip Twitter emoji SVG images (displayed oversized in Obsidian)
    # e.g. ![Image 1: 😄](https://abs-0.twimg.com/emoji/v2/svg/1f604.svg)
    result = re.sub(r'!\[[^\]]*\]\(https://abs-0\.twimg\.com/emoji/[^)]+\)', '', result)
    return result


# =========================================================================
# Public API
# =========================================================================

def save_to_markdown(item: UnifiedContent, filepath: str = None):
    """Save content as a standalone Markdown file in a platform subdirectory.

    Directory resolution (when *filepath* is None):
      1. OBSIDIAN_VAULT → {vault}/{Platform}/
      2. OUTPUT_DIR     → {output_dir}/{Platform}/
      3. Neither set    → skip

    Each item becomes one ``.md`` file.  Re-fetching the same URL
    overwrites the existing file (update in place).

    Returns the resolved file path (str) on success, or None when the
    output directory is not configured.
    """
    if filepath:
        # Caller provided an explicit path — write directly (legacy compat)
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(_format_markdown(item))
        logger.info(f"Saved to Markdown: {path}")
        return str(path)

    # Determine base output directory
    vault_path = os.getenv("OBSIDIAN_VAULT", "")
    output_dir = os.getenv("OUTPUT_DIR", "")

    if vault_path:
        base_dir = Path(vault_path)
    elif output_dir:
        base_dir = Path(output_dir)
    else:
        return None

    # Platform subdirectory
    folder = PLATFORM_FOLDER_MAP.get(item.source_type, "Other")
    platform_dir = base_dir / folder

    # YouTube: add author subdirectory (YouTube/{author}/)
    if item.source_type == SourceType.YOUTUBE and not item.category:
        author = (item.source_name or "").strip()
        if author:
            safe_author = _sanitize_filename(author)
            if safe_author:
                item.category = safe_author

    # Category subdirectory (e.g., "bookmarks/OpenClaw" for bookmark folders)
    if item.category:
        parts = item.category.split("/")
        safe_parts = [_sanitize_filename(p) for p in parts if p]
        if safe_parts:
            platform_dir = platform_dir / Path(*safe_parts)

    platform_dir.mkdir(parents=True, exist_ok=True)

    # Build filename and resolve conflicts
    name = _generate_filename(item)
    path = _resolve_filepath(platform_dir, name, item.id)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(_format_markdown(item))

    logger.info(f"Saved to Markdown: {path}")
    return str(path)
