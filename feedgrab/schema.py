# -*- coding: utf-8 -*-
"""
Unified content schema for feedgrab.

Defines the standard data format for all content sources:
- Telegram channels
- RSS feeds
- Bilibili videos
- Xiaohongshu (RED) notes
- WeChat articles
- X/Twitter posts
- YouTube videos
- Manual input
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional, List
from enum import Enum
import hashlib
import json
import re


class SourceType(str, Enum):
    """Content source types."""
    TELEGRAM = "telegram"
    RSS = "rss"
    BILIBILI = "bilibili"
    XIAOHONGSHU = "xhs"
    TWITTER = "twitter"
    WECHAT = "wechat"
    YOUTUBE = "youtube"
    GITHUB = "github"
    FEISHU = "feishu"
    KDOCS = "kdocs"
    YOUDAO = "youdao"
    ZHIHU = "zhihu"
    LINUXDO = "linuxdo"
    IDCFLARE = "idcflare"
    XIAOYUZHOU = "xiaoyuzhou"
    XIMALAYA = "ximalaya"
    HACKERNEWS = "hackernews"
    MEDIUM = "medium"
    REDDIT = "reddit"
    WEIBO = "weibo"
    DOUYIN = "douyin"
    ZSXQ = "zsxq"
    FLOWUS = "flowus"
    X_USER_LIST = "x_user_list"  # v0.22.0: Twitter user-list exports
    WEB = "web"
    MANUAL = "manual"


class MediaType(str, Enum):
    """Media types."""
    TEXT = "text"
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"


class Priority(str, Enum):
    """Content priority levels."""
    HOT = "hot"
    QUALITY = "quality"
    DEEP = "deep"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class UnifiedContent:
    """Unified content format across all platforms."""

    # === Required ===
    source_type: SourceType
    source_name: str
    title: str
    content: str
    url: str

    # === Auto-generated ===
    id: str = ""
    fetched_at: str = ""

    # === Media ===
    media_type: MediaType = MediaType.TEXT
    media_url: Optional[str] = None

    # === Scoring ===
    score: int = 0
    priority: Priority = Priority.NORMAL
    category: str = ""
    tags: List[str] = field(default_factory=list)

    # === Processing state ===
    processed: bool = False
    digest_date: Optional[str] = None

    # === Translation ===
    title_cn: Optional[str] = None
    content_cn: Optional[str] = None

    # === Metadata ===
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = hashlib.md5(self.url.encode()).hexdigest()[:12]
        if not self.fetched_at:
            self.fetched_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        d['source_type'] = self.source_type.value
        d['media_type'] = self.media_type.value
        d['priority'] = self.priority.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'UnifiedContent':
        if isinstance(data.get('source_type'), str):
            data['source_type'] = SourceType(data['source_type'])
        if isinstance(data.get('media_type'), str):
            data['media_type'] = MediaType(data['media_type'])
        if isinstance(data.get('priority'), str):
            data['priority'] = Priority(data['priority'])
        known = {f.name for f in cls.__dataclass_fields__.values()}
        data = {k: v for k, v in data.items() if k in known}
        return cls(**data)


# =============================================================================
# Converters: platform-specific dict → UnifiedContent
# =============================================================================

def from_telegram(msg: dict, channel_name: str, channel_username: str) -> UnifiedContent:
    return UnifiedContent(
        source_type=SourceType.TELEGRAM,
        source_name=channel_name,
        title=msg.get('text', '')[:100],
        content=msg.get('text', ''),
        url=msg.get('url', f"https://t.me/{channel_username}"),
        extra={"views": msg.get('views', 0), "channel_username": channel_username},
    )


def from_rss(article: dict) -> UnifiedContent:
    return UnifiedContent(
        source_type=SourceType.RSS,
        source_name=article.get('source', ''),
        title=article.get('title', ''),
        content=article.get('summary', ''),
        url=article.get('url', article.get('link', '')),
        score=article.get('score', 0),
        category=article.get('category', ''),
        title_cn=article.get('title_cn'),
        content_cn=article.get('summary_cn'),
    )


def from_bilibili(video: dict) -> UnifiedContent:
    # Build content: description + transcript (if available)
    description = video.get('description', '') or ''
    transcript = video.get('transcript', '') or ''
    if transcript:
        content = f"{description}\n\n## 🎙️ 转录\n\n{transcript}" if description else transcript
    else:
        content = description

    return UnifiedContent(
        source_type=SourceType.BILIBILI,
        source_name=video.get('author', ''),
        title=video.get('title', ''),
        content=content,
        url=video.get('url', ''),
        media_type=MediaType.VIDEO,
        media_url=video.get('cover', ''),
        extra={
            "bvid": video.get('bvid', ''),
            "aid": video.get('aid', 0),
            "cid": video.get('cid', 0),
            "duration": video.get('duration', 0),
            "view_count": video.get('view_count', 0),
            "like_count": video.get('like_count', 0),
            "coin_count": video.get('coin_count', 0),
            "favorite_count": video.get('favorite_count', 0),
            "has_transcript": video.get('has_transcript', False),
            "cover_image": video.get('cover', ''),
        },
    )


def _render_quoted_tweet(qt: dict) -> str:
    """Render a quoted tweet as a Markdown blockquote with full content."""
    if not qt or not qt.get("text"):
        return ""
    lines = []
    author = qt.get("author", "")
    author_name = qt.get("author_name", "")
    qt_url = qt.get("url", "")
    # Header line
    if author_name:
        lines.append(f"> **{author_name}** (@{author})")
    else:
        lines.append(f"> **@{author}**")
    if qt_url:
        lines.append(f"> {qt_url}")
    lines.append(">")
    # Text body
    for line in qt["text"].split("\n"):
        lines.append(f"> {line}")
    # Images
    for img in qt.get("images", []):
        if img:
            lines.append(f">\n> ![image]({img})")
    # Videos
    for vid in qt.get("videos", []):
        if vid:
            lines.append(f">\n> [▶ video]({vid})")
    return "\n".join(lines)


def _render_twitter_tweet_part(t: dict, prefix: str = "") -> str:
    """Render one tweet dict including text, media, and quoted tweet."""
    text = (t.get("text") or "").strip()
    part = f"{prefix}{text}" if text else prefix.rstrip()
    # Inline images
    for img_url in t.get("images", []):
        if img_url:
            part += f"\n\n![image]({img_url})"
    # Inline videos
    for video_url in t.get("videos", []):
        if video_url:
            part += f"\n\n[▶ video]({video_url})"
    # Quoted tweet — full blockquote with media
    qt_block = _render_quoted_tweet(t.get("quoted_tweet"))
    if qt_block:
        part += f"\n\n{qt_block}"
    return part.strip()


def _has_article_body(article_data: dict) -> bool:
    """True only when GraphQL/Jina supplied real Article body content."""
    body = (article_data or {}).get("body", "")
    return bool(body and len(body.strip()) > 200)


def _clean_twitter_title(text: str, max_len: int = 50) -> str:
    """Collapse tweet text into a short, single-line title."""
    text = re.sub(r'\*{1,3}', '', text or "")
    text = re.sub(r'[\r\n\t]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= max_len:
        return text

    candidate = text[:max_len]
    for i in range(len(candidate) - 1, max_len // 3 - 1, -1):
        if candidate[i] in "。！？.!?":
            return candidate[:i + 1]
    return candidate


def from_twitter(data: dict) -> UnifiedContent:
    # If thread data is present, assemble rich content from all tweets
    tweets = data.get("thread_tweets", [])
    article_data = data.get("article_data") or {}
    is_article = False

    if tweets:
        article_body = (article_data or {}).get("body", "")
        is_article = _has_article_body(article_data)
        parts = []
        rest_count = len(tweets) - 1
        for i, t in enumerate(tweets):
            # Article body is authoritative for the root tweet only. Everything
            # else that GraphQL captured (self-replies, quoted tweets, media)
            # still belongs in the final Markdown.
            tweet_to_render = dict(t)
            if i == 0 and is_article:
                tweet_to_render["text"] = article_body
            prefix = ""
            if len(tweets) > 1 and i > 0:
                prefix = f"**[{i}/{rest_count}]** "
            part = _render_twitter_tweet_part(tweet_to_render, prefix=prefix)
            if part:
                parts.append(part)
        content = "\n\n---\n\n".join(parts)

        # Article: prepend cover image at the top
        article_cover = article_data.get("cover_image", "") if is_article else ""
        if article_cover:
            content = f"![cover]({article_cover})\n\n{content}"
    else:
        content = data.get("text", "")

    # cover_image: article cover > explicit cover_image > first image
    cover_image = article_data.get("cover_image", "") if is_article else ""
    if not cover_image:
        cover_image = data.get("cover_image", "")
    if not cover_image and data.get("images"):
        cover_image = data["images"][0]

    # Classify tweet type: article / thread / status
    if is_article or article_data.get("has_content"):
        tweet_type = "article"
    elif tweets and len(tweets) > 1:
        tweet_type = "thread"
    else:
        tweet_type = "status"

    title = _clean_twitter_title(data.get("title") or data.get("text", ""))

    return UnifiedContent(
        source_type=SourceType.TWITTER,
        source_name=data.get("author", ""),
        title=title,
        content=content,
        url=data.get("url", ""),
        tags=data.get("hashtags", []),
        extra={
            "tweet_type": tweet_type,
            "tweet_count": len(tweets) if tweets else 1,
            "has_thread": bool(tweets),
            "author_name": data.get("author_name", ""),
            "created_at": data.get("created_at", ""),
            "cover_image": cover_image,
            "likes": data.get("likes", 0),
            "retweets": data.get("retweets", 0),
            "replies": data.get("replies", 0),
            "bookmarks": data.get("bookmarks", 0),
            "views": data.get("views", "0"),
            "images": data.get("images", []),
            "videos": data.get("videos", []),
            "quoted_tweets": data.get("quoted_tweets", []),
            "author_replies": data.get("author_replies", []),
            "comments": data.get("comments", []),
            # New metadata
            "quote_count": data.get("quote_count", 0),
            "lang": data.get("lang", ""),
            "source_app": data.get("source_app", ""),
            "possibly_sensitive": data.get("possibly_sensitive", False),
            "is_blue_verified": data.get("is_blue_verified", False),
            "followers_count": data.get("followers_count", 0),
            "statuses_count": data.get("statuses_count", 0),
            "listed_count": data.get("listed_count", 0),
            # P0-3: pinned tweet marker (TimelinePinEntry instruction)
            "is_pinned": data.get("is_pinned", False),
            # v0.23.0: ModeratedTimeline supplement
            "moderated_replies": data.get("moderated_replies", []),
            "has_moderated_replies": data.get("has_moderated_replies", False),
        },
    )


def from_wechat(article: dict) -> UnifiedContent:
    # Best cover image: article page cover > sogou thumbnail
    cover_image = article.get('cover_image', '') or article.get('thumbnail', '')
    content_text = article.get('content', '')

    # Extract video URLs from JS evaluate data
    raw_videos = article.get('videos', [])
    video_urls = [v['src'] for v in raw_videos if v.get('src')]
    image_urls = []  # WeChat images are inline in HTML, not separate list

    # Prepend cover image at top of content
    if cover_image and content_text:
        content_text = f"![cover]({cover_image})\n\n{content_text}"
    elif cover_image:
        content_text = f"![cover]({cover_image})"

    return UnifiedContent(
        source_type=SourceType.WECHAT,
        source_name=article.get('author', ''),
        title=article.get('title', ''),
        content=content_text,
        url=article.get('url', ''),
        tags=article.get('tags', []),
        extra={
            "publish_date": article.get('publish_date', ''),
            "cover_image": cover_image,
            "thumbnail": article.get('thumbnail', ''),
            "summary": article.get('summary', ''),
            "original_url": article.get('original_url', ''),
            "search_keyword": article.get('search_keyword', ''),
            "reads": article.get('reads', 0),
            "likes": article.get('likes', 0),
            "wow": article.get('wow', 0),
            "shares": article.get('shares', 0),
            "comments": article.get('comments', 0),
            "comment_list": article.get('comment_list', []),
            "videos": video_urls,
            "images": image_urls,
        },
    )


def from_xiaohongshu(note: dict) -> UnifiedContent:
    images = note.get('images', [])
    note_type = note.get('note_type', '')
    media = MediaType.VIDEO if note_type == 'video' else (MediaType.IMAGE if images else MediaType.TEXT)
    return UnifiedContent(
        source_type=SourceType.XIAOHONGSHU,
        source_name=note.get('author', ''),
        title=note.get('title', ''),
        content=note.get('content', ''),
        url=note.get('url', ''),
        media_type=media,
        tags=note.get('tags', []),
        extra={
            "author_url": note.get('author_url', ''),
            "cover_image": images[0] if images else "",
            "likes": note.get('likes', 0),
            "collects": note.get('collects', 0),
            "comments": note.get('comments', 0),
            "share_count": note.get('share_count', 0),
            "note_type": note_type,
            "images": images,
            "date": note.get('date', ''),
            "comment_list": note.get('comment_list', []),
        },
    )


def from_youtube(video: dict) -> UnifiedContent:
    published = video.get("published_at", "")
    if published:
        # ISO 8601 → YYYY-MM-DD HH:MM
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            published = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            published = published[:10]

    return UnifiedContent(
        source_type=SourceType.YOUTUBE,
        source_name=video.get('author', '') or video.get('channel_title', ''),
        title=video.get('title', ''),
        content=video.get('description', ''),
        url=video.get('url', ''),
        media_type=MediaType.VIDEO,
        tags=video.get('tags', []),
        extra={
            "video_id": video.get('video_id', ''),
            "duration": video.get('duration', ''),
            "duration_seconds": video.get('duration_seconds', 0),
            "view_count": video.get('view_count', 0),
            "like_count": video.get('like_count', 0),
            "comment_count": video.get('comment_count', 0),
            "channel_id": video.get('channel_id', ''),
            "published_at": published,
            "category_id": video.get('category_id', ''),
            "definition": video.get('definition', ''),
            "has_caption": video.get('has_caption', False),
            "has_transcript": video.get('has_transcript', False),
            "thumbnail": video.get('thumbnail', ''),
        },
    )


def from_github(data: dict) -> UnifiedContent:
    """Convert GitHub repo data dict to UnifiedContent."""
    owner = data.get("owner", "")
    repo = data.get("repo", "")
    repo_key = f"{owner}/{repo}"
    item_id = hashlib.md5(repo_key.encode()).hexdigest()[:12]

    title = data.get("title", repo)

    return UnifiedContent(
        source_type=SourceType.GITHUB,
        source_name=owner,
        title=title,
        content=data.get("content", ""),
        url=data.get("url", f"https://github.com/{owner}/{repo}"),
        id=item_id,
        tags=data.get("topics", []),
        extra={
            "owner": owner,
            "repo": repo,
            "full_name": data.get("full_name", repo_key),
            "description": data.get("description", ""),
            "stars": data.get("stars", 0),
            "forks": data.get("forks", 0),
            "language": data.get("language", ""),
            "license": data.get("license", ""),
            "default_branch": data.get("default_branch", "main"),
            "open_issues": data.get("open_issues", 0),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "pushed_at": data.get("pushed_at", ""),
            "owner_avatar": data.get("owner_avatar", ""),
            "readme_file": data.get("readme_file", "README.md"),
        },
    )


def from_feishu(data: dict) -> UnifiedContent:
    return UnifiedContent(
        source_type=SourceType.FEISHU,
        source_name=data.get("author", ""),
        title=data.get("title", ""),
        content=data.get("content", ""),
        url=data.get("url", ""),
        tags=data.get("tags", []),
        extra={
            "doc_type": data.get("doc_type", ""),
            "doc_token": data.get("doc_token", ""),
            "word_count": data.get("word_count", 0),
            "create_time": data.get("create_time", ""),
            "edit_time": data.get("edit_time", ""),
            "cover_image": data.get("cover_image", ""),
            "images": data.get("images", []),
            "images_info": data.get("images_info", []),
            "img_subdir": data.get("img_subdir", ""),
        },
    )


def from_kdocs(data: dict) -> UnifiedContent:
    """Convert KDocs (WPS) data dict to UnifiedContent."""
    return UnifiedContent(
        source_type=SourceType.KDOCS,
        source_name=data.get("author", ""),
        title=data.get("title", ""),
        content=data.get("content", ""),
        url=data.get("url", ""),
        tags=data.get("tags", []),
        extra={
            "doc_token": data.get("doc_token", ""),
            "word_count": data.get("word_count", 0),
            "create_time": data.get("create_time", ""),
            "edit_time": data.get("edit_time", ""),
            "creator_id": data.get("creator_id", ""),
            "images_info": data.get("images_info", []),
            "img_subdir": data.get("img_subdir", ""),
        },
    )


def from_flowus(data: dict) -> UnifiedContent:
    """Convert FlowUs (息流) data dict to UnifiedContent."""
    return UnifiedContent(
        source_type=SourceType.FLOWUS,
        source_name=data.get("author", ""),
        title=data.get("title", ""),
        content=data.get("content", ""),
        url=data.get("url", ""),
        tags=data.get("tags", []),
        extra={
            "doc_token": data.get("doc_token", ""),
            "share_code": data.get("share_code", ""),
            "space_title": data.get("space_title", ""),
            "seo_description": data.get("seo_description", ""),
            "word_count": data.get("word_count", 0),
            "create_time": data.get("create_time", ""),
            "edit_time": data.get("edit_time", ""),
            "images_info": data.get("images_info", []),
            "img_subdir": data.get("img_subdir", ""),
        },
    )


def from_youdao(data: dict) -> UnifiedContent:
    """Convert Youdao Note data dict to UnifiedContent."""
    return UnifiedContent(
        source_type=SourceType.YOUDAO,
        source_name=data.get("author", ""),
        title=data.get("title", ""),
        content=data.get("content", ""),
        url=data.get("url", ""),
        tags=data.get("tags", []),
        extra={
            "share_key": data.get("share_key", ""),
            "page_views": data.get("page_views", 0),
            "create_time": data.get("create_time", ""),
            "edit_time": data.get("edit_time", ""),
            "images_info": data.get("images_info", []),
            "img_subdir": data.get("img_subdir", ""),
        },
    )


def from_zhihu(data: dict) -> UnifiedContent:
    """Convert Zhihu data dict to UnifiedContent."""
    return UnifiedContent(
        source_type=SourceType.ZHIHU,
        source_name=data.get("author", ""),
        title=data.get("title", ""),
        content=data.get("content", ""),
        url=data.get("url", ""),
        tags=data.get("tags", []),
        extra={
            "content_type": data.get("content_type", ""),
            "question_id": data.get("question_id", ""),
            "answer_id": data.get("answer_id", ""),
            "article_id": data.get("article_id", ""),
            "question_title": data.get("question_title", ""),
            "question_detail": data.get("question_detail", ""),
            "upvotes": data.get("upvotes", 0),
            "comments": data.get("comments", 0),
            "thanks": data.get("thanks", 0),
            "collected": data.get("collected", 0),
            "views": data.get("views", 0),
            "author_url": data.get("author_url", ""),
            "publish_date": data.get("publish_date", ""),
            "img_subdir": data.get("img_subdir", ""),
            "answers_list": data.get("answers_list", []),
        },
    )


def from_linuxdo(data: dict) -> UnifiedContent:
    """Convert LinuxDo / Discourse topic dict to UnifiedContent."""
    return UnifiedContent(
        source_type=SourceType.LINUXDO,
        source_name=data.get("author", "") or "linux.do",
        title=data.get("title", ""),
        content=data.get("content", ""),
        url=data.get("url", ""),
        category=data.get("category", ""),
        tags=data.get("tags", []),
        extra={
            "topic_id": data.get("topic_id", ""),
            "topic_slug": data.get("topic_slug", ""),
            "category": data.get("category", ""),
            "category_id": data.get("category_id", 0),
            "posts_count": data.get("posts_count", 0),
            "reply_count": data.get("reply_count", 0),
            "reply_mode": data.get("reply_mode", "author"),
            "rendered_reply_count": data.get("rendered_reply_count", 0),
            "like_count": data.get("like_count", 0),
            "views": data.get("views", 0),
            "created_at": data.get("created_at", ""),
            "last_posted_at": data.get("last_posted_at", ""),
            "cover_image": data.get("cover_image", ""),
            "post_count_loaded": data.get("post_count_loaded", 0),
        },
    )


def from_idcflare(data: dict) -> UnifiedContent:
    """Convert IDCFlare / Discourse topic dict to UnifiedContent."""
    return UnifiedContent(
        source_type=SourceType.IDCFLARE,
        source_name=data.get("author", "") or "idcflare.com",
        title=data.get("title", ""),
        content=data.get("content", ""),
        url=data.get("url", ""),
        category=data.get("category", ""),
        tags=data.get("tags", []),
        extra={
            "topic_id": data.get("topic_id", ""),
            "topic_slug": data.get("topic_slug", ""),
            "category": data.get("category", ""),
            "category_id": data.get("category_id", 0),
            "posts_count": data.get("posts_count", 0),
            "reply_count": data.get("reply_count", 0),
            "reply_mode": data.get("reply_mode", "author"),
            "rendered_reply_count": data.get("rendered_reply_count", 0),
            "like_count": data.get("like_count", 0),
            "views": data.get("views", 0),
            "created_at": data.get("created_at", ""),
            "last_posted_at": data.get("last_posted_at", ""),
            "cover_image": data.get("cover_image", ""),
            "post_count_loaded": data.get("post_count_loaded", 0),
        },
    )


def from_zsxq(data: dict) -> UnifiedContent:
    """Convert Zsxq article/topic dict to UnifiedContent."""
    return UnifiedContent(
        source_type=SourceType.ZSXQ,
        source_name=data.get("author", "") or "zsxq",
        title=data.get("title", "") or "Untitled",
        content=data.get("content", ""),
        url=data.get("url", ""),
        tags=data.get("tags", []),
        extra={
            "zsxq_type": data.get("zsxq_type", "article"),  # article | topic
            "article_id": data.get("article_id", ""),
            "topic_id": data.get("topic_id", ""),
            "group_id": data.get("group_id", ""),
            "group_name": data.get("group_name", ""),
            "likes": data.get("likes", 0),
            "comments": data.get("comments", 0),
            "reads": data.get("reads", 0),
            "rewards": data.get("rewards", 0),
            "comment_mode": data.get("comment_mode", "none"),
            "rendered_comment_count": data.get("rendered_comment_count", 0),
            "created_at": data.get("created_at", ""),
            "cover_image": data.get("cover_image", ""),
            "images": data.get("images", []),
            "is_silent": data.get("is_silent", False),
        },
    )


def from_hackernews(data: dict) -> UnifiedContent:
    """Build UnifiedContent from a HackerNews fetcher result dict."""
    return UnifiedContent(
        source_type=SourceType.HACKERNEWS,
        source_name=data.get("author", "") or "hackernews",
        title=data.get("title", "") or "Untitled",
        content=data.get("content", ""),
        url=data.get("url", ""),
        tags=data.get("tags", []),
        extra={
            "hn_id": data.get("id", ""),
            "hn_type": data.get("type", "story"),
            "score": data.get("score", 0),
            "comment_count": data.get("comment_count", 0),
            "linked_url": data.get("linked_url", ""),
            "created_at": data.get("created_at", ""),
            "author_name": data.get("author", ""),
        },
    )


def from_medium(data: dict) -> UnifiedContent:
    """Build UnifiedContent from a Medium fetcher result dict."""
    author_handle = data.get("author", "") or "medium"
    return UnifiedContent(
        source_type=SourceType.MEDIUM,
        source_name=author_handle,
        title=data.get("title", "") or "Untitled",
        content=data.get("content", ""),
        url=data.get("url", ""),
        tags=data.get("tags", []),
        extra={
            "author_name": data.get("author_name", "") or author_handle,
            "published_at": data.get("published", ""),
            "cover_image": data.get("image", ""),
            "is_member_only": bool(data.get("is_member_only", False)),
        },
    )


def from_reddit(data: dict) -> UnifiedContent:
    """Build UnifiedContent from a Reddit fetcher result dict."""
    author = data.get("author", "") or "reddit"
    return UnifiedContent(
        source_type=SourceType.REDDIT,
        source_name=author if author.startswith("u/") or author == "reddit" else f"u/{author}",
        title=data.get("title", "") or "Untitled",
        content=data.get("content", ""),
        url=data.get("url", ""),
        category=data.get("category", ""),
        tags=data.get("tags", []),
        extra={
            "reddit_id": data.get("id", ""),
            "subreddit": data.get("subreddit", ""),
            "flair": data.get("flair", ""),
            "score": data.get("score", 0),
            "upvote_ratio": data.get("upvote_ratio", 0.0),
            "comment_count": data.get("comment_count", 0),
            "is_self": bool(data.get("is_self", True)),
            "linked_url": data.get("linked_url", ""),
            "created_at": data.get("created_at", ""),
            "author_name": data.get("author_name", "") or author,
            "search_keyword": data.get("search_keyword", ""),
            "search_sort": data.get("search_sort", ""),
            "search_time_range": data.get("search_time_range", ""),
            "result_type": data.get("result_type", ""),
            "request_url": data.get("request_url", ""),
            "tier": data.get("tier", ""),
            "reply_mode": data.get("reply_mode", ""),
            "rendered_reply_count": data.get("rendered_reply_count", 0),
            "more_expanded_count": data.get("more_expanded_count", 0),
            "more_remaining_count": data.get("more_remaining_count", 0),
            "post_hint": data.get("post_hint", ""),
            "preview_image_url": data.get("preview_image_url", ""),
            "gallery_urls": data.get("gallery_urls", []),
            "media_url": data.get("media_url", ""),
            "images": data.get("images", []),
            "videos": data.get("videos", []),
            "page_count": data.get("page_count", 0),
        },
    )


def from_weibo(data: dict) -> UnifiedContent:
    """Build UnifiedContent from a Weibo fetcher result dict."""
    return UnifiedContent(
        source_type=SourceType.WEIBO,
        source_name=data.get("author", "") or "weibo",
        title=data.get("title", "") or "Untitled",
        content=data.get("content", ""),
        url=data.get("url", ""),
        tags=data.get("tags", []),
        extra={
            "mid": data.get("mid", ""),
            "bid": data.get("bid", ""),
            "uid": data.get("uid", ""),
            "author_name": data.get("author_name", ""),
            "created_at": data.get("created_at", ""),
            "likes": data.get("likes", 0),
            "comments": data.get("comments", 0),
            "reposts": data.get("reposts", 0),
            "source_app": data.get("source_app", ""),
            "mblog_type": data.get("mblog_type", "status"),
            "images": data.get("images", []),
            "videos": data.get("videos", []),
        },
    )


def from_douyin(data: dict) -> UnifiedContent:
    """Build UnifiedContent from a Douyin fetcher result dict."""
    return UnifiedContent(
        source_type=SourceType.DOUYIN,
        source_name=data.get("author", "") or "douyin",
        title=data.get("title", "") or "Untitled",
        content=data.get("content", ""),
        url=data.get("url", ""),
        tags=data.get("tags", []),
        extra={
            "aweme_id": data.get("aweme_id", ""),
            "aweme_type": data.get("aweme_type", "video"),
            "author_name": data.get("author_name", ""),
            "author_sec_uid": data.get("author_sec_uid", ""),
            "created_at": data.get("created_at", ""),
            "plays": data.get("plays", 0),
            "likes": data.get("likes", 0),
            "comments": data.get("comments", 0),
            "shares": data.get("shares", 0),
            "duration_seconds": data.get("duration_seconds", 0),
            "music_title": data.get("music_title", ""),
            "music_author": data.get("music_author", ""),
            "cover_image": data.get("cover_image", ""),
        },
    )


def from_manual(title: str, content: str, url: str = "") -> UnifiedContent:
    return UnifiedContent(
        source_type=SourceType.MANUAL,
        source_name="manual",
        title=title,
        content=content,
        url=url or f"manual://{hashlib.md5(title.encode()).hexdigest()[:8]}",
    )


def from_web(data: dict) -> UnifiedContent:
    """Build UnifiedContent from paywall-bypass or generic web fetcher output.

    Accepts the dict returned by ``try_paywall_bypass()`` / ``fetch_via_jina()``
    (keys: title, content, url, author, published, image, strategy).
    """
    from urllib.parse import urlparse
    url = data.get("url", "")
    source_name = urlparse(url).netloc if url else "web"
    extra = {}
    if data.get("strategy"):
        extra["strategy"] = data["strategy"]
    if data.get("image"):
        extra["cover_image"] = data["image"]
    if data.get("author"):
        extra["author"] = data["author"]
    if data.get("published"):
        extra["published"] = data["published"]
    return UnifiedContent(
        source_type=SourceType.WEB,
        source_name=source_name,
        title=data.get("title", "") or "Untitled",
        content=data.get("content", ""),
        url=url,
        extra=extra,
    )


def _build_podcast_content(data: dict) -> str:
    """Combine shownotes / description + transcript into a single Markdown body."""
    shownotes = (data.get("shownotes") or "").strip()
    description = (data.get("description") or "").strip()
    transcript = (data.get("transcript") or "").strip()

    parts = []
    intro = shownotes or description
    if intro:
        parts.append("## 📝 Shownotes\n\n" + intro if shownotes else intro)
    if transcript:
        parts.append("## 🎙️ 完整转录\n\n" + transcript)
    return "\n\n".join(parts)


def from_xiaoyuzhou(data: dict) -> UnifiedContent:
    """Build UnifiedContent from xiaoyuzhou fetcher output."""
    return UnifiedContent(
        source_type=SourceType.XIAOYUZHOU,
        source_name=data.get("podcast_name", "") or data.get("author", "") or "xiaoyuzhou",
        title=data.get("title", "") or "Untitled",
        content=_build_podcast_content(data),
        url=data.get("url", ""),
        media_type=MediaType.AUDIO,
        media_url=data.get("audio_url", "") or data.get("cover_image", ""),
        extra={
            "episode_id": data.get("episode_id", ""),
            "podcast_name": data.get("podcast_name", ""),
            "podcast_id": data.get("podcast_id", ""),
            "author": data.get("author", ""),
            "duration": data.get("duration", ""),
            "duration_seconds": data.get("duration_seconds", 0),
            "published": data.get("published", ""),
            "cover_image": data.get("cover_image", ""),
            "audio_url": data.get("audio_url", ""),
            "has_transcript": data.get("has_transcript", False),
        },
    )


def from_ximalaya(data: dict) -> UnifiedContent:
    """Build UnifiedContent from ximalaya fetcher output."""
    return UnifiedContent(
        source_type=SourceType.XIMALAYA,
        source_name=data.get("album_name", "") or data.get("author", "") or "ximalaya",
        title=data.get("title", "") or "Untitled",
        content=_build_podcast_content(data),
        url=data.get("url", ""),
        media_type=MediaType.AUDIO,
        media_url=data.get("audio_url", "") or data.get("cover_image", ""),
        extra={
            "track_id": data.get("track_id", ""),
            "album_name": data.get("album_name", ""),
            "album_id": data.get("album_id", ""),
            "author": data.get("author", ""),
            "duration": data.get("duration", ""),
            "duration_seconds": data.get("duration_seconds", 0),
            "published": data.get("published", ""),
            "cover_image": data.get("cover_image", ""),
            "audio_url": data.get("audio_url", ""),
            "can_play": data.get("can_play", True),
            "has_transcript": data.get("has_transcript", False),
        },
    )


# =============================================================================
# Unified Inbox
# =============================================================================

class UnifiedInbox:
    """JSON-based content inbox with dedup."""

    def __init__(self, filepath: str = "unified_inbox.json"):
        self.filepath = filepath
        self.items: List[UnifiedContent] = []
        self.load()

    def load(self):
        import os
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.items = [UnifiedContent.from_dict(d) for d in data]
            except (json.JSONDecodeError, IOError):
                self.items = []

    def save(self):
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump([item.to_dict() for item in self.items], f,
                      ensure_ascii=False, indent=2)

    def add(self, item: UnifiedContent) -> bool:
        if any(i.id == item.id for i in self.items):
            return False
        self.items.append(item)
        return True

    def add_batch(self, items: List[UnifiedContent]) -> int:
        return sum(1 for item in items if self.add(item))

    def get_unprocessed(self) -> List[UnifiedContent]:
        return [i for i in self.items if not i.processed]

    def get_by_source(self, source_type: SourceType) -> List[UnifiedContent]:
        return [i for i in self.items if i.source_type == source_type]

    def mark_processed(self, item_id: str, digest_date: str = None):
        for item in self.items:
            if item.id == item_id:
                item.processed = True
                if digest_date:
                    item.digest_date = digest_date
                break

    def clear_old(self, days: int = 7):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        self.items = [i for i in self.items if i.fetched_at > cutoff]
