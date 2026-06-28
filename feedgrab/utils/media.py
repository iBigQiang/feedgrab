# -*- coding: utf-8 -*-
"""
Media download — download images/videos to local attachments directory.

Follows the Feishu image download pattern (feishu.py download_feishu_images):
    1. save_to_markdown() returns the saved .md path
    2. download_media() downloads files to {md_dir}/attachments/{item_id}/
    3. Replaces remote URLs in .md with relative paths

Configuration:
    X_DOWNLOAD_MEDIA=true       (default false)
    XHS_DOWNLOAD_MEDIA=true     (default false)
"""

import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from loguru import logger


def download_media(
    md_path: str,
    images: list,
    videos: list,
    item_id: str,
    platform: str = "twitter",
    *,
    context: dict = None,
) -> None:
    """Download images/videos to {md_dir}/attachments/{item_id}/ and rewrite .md URLs.

    Args:
        md_path: Path to the saved .md file (from save_to_markdown).
        images: List of image URLs.
        videos: List of video URLs.
        item_id: Unique ID for subdirectory (matches front matter item_id).
        platform: "twitter" or "xhs" — determines URL optimization and headers.
        context: Optional dict carrying metadata for filename pattern token
                 substitution (X-only, opt-in via X_MEDIA_FILENAME_PATTERN).
                 Keys: tweet_id, screen_name, user_id, created_at.
    """
    all_urls = [u for u in (images or []) if u] + [u for u in (videos or []) if u]
    if not all_urls:
        return

    md_file = Path(md_path)
    if not md_file.exists():
        return

    att_dir = md_file.parent / "attachments" / item_id
    att_dir.mkdir(parents=True, exist_ok=True)

    url_map = {}  # {remote_url: relative_path}
    downloaded = 0

    # v0.23.0: X-only media filename pattern (opt-in via env)
    import os as _os
    pattern = _os.getenv("X_MEDIA_FILENAME_PATTERN", "").strip()
    apply_pattern = bool(pattern) and platform == "twitter"
    image_count = len([u for u in (images or []) if u])

    for idx, url in enumerate(all_urls, start=1):
        is_video_url = idx > image_count  # videos come after images in all_urls
        media_type = "video" if is_video_url else "photo"

        filename = _extract_filename(url, platform)
        if not filename:
            continue

        if apply_pattern:
            filename = _apply_filename_pattern(
                pattern=pattern,
                fallback_name=filename,
                ctx=context or {},
                num=idx,
                media_type=media_type,
            )

        dest = att_dir / filename

        # Skip if already downloaded
        if dest.exists() and dest.stat().st_size > 0:
            rel = f"attachments/{item_id}/{filename}"
            url_map[url] = rel
            downloaded += 1
            continue

        # Optimize URL for best quality
        dl_url = _optimize_url(url, platform)
        headers = _download_headers(platform)

        ok = _download_file(dl_url, dest, headers)
        if ok:
            rel = f"attachments/{item_id}/{filename}"
            url_map[url] = rel
            downloaded += 1
        else:
            # Clean up empty file
            if dest.exists() and dest.stat().st_size == 0:
                dest.unlink(missing_ok=True)

    # Replace URLs in .md
    if url_map:
        _replace_urls_in_md(md_file, url_map)

    total = len(all_urls)
    if downloaded > 0:
        logger.info(f"[media] 已下载 {downloaded}/{total} 个文件 → attachments/{item_id}/")
    if downloaded < total:
        logger.warning(f"[media] {total - downloaded} 个文件下载失败，已保留远程 URL")


def _download_file(url: str, dest: Path, headers: dict = None) -> bool:
    """Download a single file. Returns True on success."""
    from feedgrab.utils.http_client import get as http_get

    try:
        is_video = any(ext in dest.suffix.lower() for ext in [".mp4", ".m4v", ".webm"])
        timeout = 120 if is_video else 30

        resp = http_get(url, headers=headers or {}, timeout=timeout)
        resp.raise_for_status()

        data = resp.content
        if not data or len(data) < 100:
            logger.debug(f"[media] 空响应：{url}")
            return False

        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        logger.debug(f"[media] 下载失败：{url} — {e}")
        return False


def _extract_filename(url: str, platform: str) -> str:
    """Extract a clean filename from a media URL.

    Twitter images:  pbs.twimg.com/media/GmNx7jHXcAA5EYi?format=jpg → GmNx7jHXcAA5EYi.jpg
    Twitter videos:  video.twimg.com/.../xxx.mp4?tag=12 → xxx.mp4
    XHS images:      sns-webpic-qc.xhscdn.com/.../xxx.jpg!nd_xxx → xxx.jpg
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    if platform == "twitter":
        # Image: /media/GmNx7jHXcAA5EYi (format in query) or /media/xxx.jpg
        if "/media/" in path:
            stem = path.split("/")[-1]
            # If stem already has an extension (e.g. xxx.jpg), use as-is
            if "." in stem:
                return _sanitize(stem)
            qs = parse_qs(parsed.query)
            fmt = qs.get("format", ["jpg"])[0]
            return f"{stem}.{fmt}"

        # Video/other: extract filename from path
        basename = path.split("/")[-1]
        # Remove query params from extension
        if "." in basename:
            name = basename.split("?")[0]
            return _sanitize(name)

    elif platform == "xhs":
        basename = path.split("/")[-1]
        # Strip XHS CDN suffixes like !nd_dft_wgth_webp_3
        basename = re.sub(r"![a-z_0-9]+$", "", basename)
        if basename:
            return _sanitize(basename)

    elif platform == "wechat":
        # mpvideo.qpic.cn/.../xxx.f10002.mp4?dis_k=...
        basename = path.split("/")[-1]
        # Strip query params already handled by urlparse, just sanitize
        if "." in basename:
            return _sanitize(basename)
        # Fallback: use last two segments (some URLs have no extension in last part)
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and "." in parts[-1]:
            return _sanitize(parts[-1])
        # Last resort: hash-based name
        if basename:
            return _sanitize(f"{basename}.mp4")

    elif platform == "weibo":
        # Image: wx1.sinaimg.cn/large/abc123.jpg → abc123.jpg
        # Video: f.video.weibocdn.com/.../abc123.mp4?label=mp4_hd&Expires=...
        basename = path.split("/")[-1]
        if basename and "." in basename:
            return _sanitize(basename)
        if basename:
            return _sanitize(f"{basename}.mp4")

    # Generic fallback
    basename = path.split("/")[-1]
    if "." in basename:
        return _sanitize(basename.split("?")[0])

    return ""


# ---------------------------------------------------------------------------
# v0.23.0: X-only media filename pattern (opt-in via X_MEDIA_FILENAME_PATTERN)
# ---------------------------------------------------------------------------

# Whitelist of allowed tokens (defensive against path-traversal injection).
_PATTERN_TOKENS = {
    "{date}", "{datetime}", "{screen_name}", "{user_id}", "{tweet_id}",
    "{num}", "{type}", "{ext}", "{name}",
}

_FS_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _apply_filename_pattern(
    pattern: str,
    fallback_name: str,
    ctx: dict,
    num: int,
    media_type: str,
) -> str:
    """Apply X_MEDIA_FILENAME_PATTERN substitution to build a media filename.

    Args:
        pattern: User-supplied pattern (e.g. "{date}_{screen_name}_{num}.{ext}")
        fallback_name: Original CDN-stem filename (e.g. "GmNx7jHXcAA5EYi.jpg")
        ctx: Context dict from caller (tweet_id, screen_name, user_id, created_at)
        num: 1-based media index within the tweet
        media_type: "photo" / "video" / "animated_gif"

    Returns:
        Sanitized filename string. Falls back to original on any error.
    """
    try:
        # Split fallback into stem + ext (for {name} and {ext} tokens)
        if "." in fallback_name:
            base_stem, _, ext = fallback_name.rpartition(".")
        else:
            base_stem, ext = fallback_name, "bin"

        # tweet_id: prefer extraction from url (Snowflake), else ctx value (may be a hash)
        real_tweet_id = ctx.get("tweet_id", "") or ""
        ctx_url = ctx.get("url", "") or ""
        if ctx_url:
            m = re.search(r"/status/(\d+)", ctx_url)
            if m:
                real_tweet_id = m.group(1)

        # Parse created_at → date / datetime tokens
        from datetime import datetime
        created_at = (ctx.get("created_at") or "").strip()
        date_token = ""
        datetime_token = ""
        if created_at:
            # Twitter format: "Tue May 18 09:36:31 +0000 2026"
            dt = None
            for fmt in (
                "%a %b %d %H:%M:%S %z %Y",  # Twitter v1.1
                "%Y-%m-%dT%H:%M:%S%z",       # ISO 8601 w/ tz
                "%Y-%m-%dT%H:%M:%SZ",        # ISO 8601 UTC
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    dt = datetime.strptime(created_at, fmt)
                    break
                except ValueError:
                    continue
            if dt:
                date_token = dt.strftime("%Y%m%d")
                datetime_token = dt.strftime("%Y%m%d_%H%M%S")

        replacements = {
            "{date}": date_token or "nodate",
            "{datetime}": datetime_token or "nodate",
            "{screen_name}": ctx.get("screen_name", "") or "unknown",
            "{user_id}": ctx.get("user_id", "") or "0",
            "{tweet_id}": real_tweet_id or "0",
            "{num}": str(num),
            "{type}": media_type,
            "{ext}": ext,
            "{name}": base_stem,
        }

        result = pattern
        for token, value in replacements.items():
            # Sanitize each substitution against fs-unsafe chars
            safe_value = _FS_UNSAFE.sub("_", str(value))
            result = result.replace(token, safe_value)

        # Final filename safety pass (in case the pattern itself had unsafe chars)
        result = _FS_UNSAFE.sub("_", result)
        # Reject empty / pure-extension results
        if not result or result.lstrip(".") == "":
            return fallback_name

        return result[:200]  # length cap

    except Exception as e:
        logger.debug(f"[media] filename pattern failed, using fallback: {e}")
        return fallback_name


def _optimize_url(url: str, platform: str) -> str:
    """Optimize URL for highest quality download.

    Twitter: append name=orig for original resolution + strip avatar size suffix.
    XHS: strip CDN resize suffix.
    WeChat: upgrade http to https.
    """
    if platform == "twitter" and "pbs.twimg.com/media/" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        qs["name"] = ["orig"]
        new_query = urlencode(qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    if platform == "twitter" and "pbs.twimg.com/profile_images/" in url:
        # 头像原图：去掉 _normal / _bigger / _mini / _400x400 尺寸前缀
        return re.sub(
            r"_(normal|bigger|mini|400x400)\.(jpg|jpeg|png|gif|webp)(\?.*)?$",
            r".\2\3",
            url,
        )

    if platform == "xhs":
        # Remove CDN resize/format suffixes
        return re.sub(r"![a-z_0-9]+$", "", url)

    if platform == "wechat":
        # mpvideo.qpic.cn requires HTTPS
        if url.startswith("http://"):
            url = "https://" + url[7:]
        return url

    return url


def _download_headers(platform: str) -> dict:
    """Platform-specific download headers."""
    if platform == "xhs":
        return {"Referer": "https://www.xiaohongshu.com/"}
    if platform == "wechat":
        return {"Referer": "https://mp.weixin.qq.com/"}
    if platform == "weibo":
        # weibocdn requires Referer + UA from m.weibo.cn for the signed URL
        # to actually return 200; otherwise it 403s even with a fresh URL.
        from feedgrab.config import get_user_agent
        return {
            "Referer": "https://m.weibo.cn/",
            "User-Agent": get_user_agent(),
        }
    return {}


def _replace_urls_in_md(md_path: Path, url_map: dict) -> None:
    """Replace remote URLs with local relative paths in a .md file."""
    content = md_path.read_text(encoding="utf-8")
    changed = False

    for remote_url, local_path in url_map.items():
        if remote_url in content:
            content = content.replace(remote_url, local_path)
            changed = True

    if changed:
        md_path.write_text(content, encoding="utf-8")


def _sanitize(name: str) -> str:
    """Sanitize filename for filesystem safety."""
    # Remove problematic chars
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Collapse multiple underscores/hyphens
    name = re.sub(r'[_-]{2,}', '-', name)
    return name[:200]  # cap length
