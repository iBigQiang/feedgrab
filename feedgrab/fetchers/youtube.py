# -*- coding: utf-8 -*-
"""
YouTube video fetcher — multi-tier content extraction:

Tier 0: InnerTube API (zero deps, zero quota) + smart segmentation + chapters
Tier 1: yt-dlp auto-subtitles + smart segmentation
Tier 2: yt-dlp audio + Groq Whisper transcription
Tier 3: API description / Jina fallback

Optional: YOUTUBE_API_KEY (rich metadata), GROQ_API_KEY (Whisper transcription)
"""

import html
import re
import os
import shutil
import subprocess
import tempfile
import unicodedata
from loguru import logger
from typing import Dict, Any, List, Optional, Tuple


def _js_runtime_args() -> list:
    """Detect available JS runtime for yt-dlp (needed for YouTube extraction).

    yt-dlp only enables deno by default. If node/bun is installed but not deno,
    we must explicitly pass --js-runtimes to enable it.
    """
    for name, cmd in [("deno", "deno"), ("node", "node"), ("bun", "bun")]:
        if shutil.which(cmd):
            return ["--js-runtimes", name, "--remote-components", "ejs:github"]
    return []


def _cookies_args() -> list:
    """Return yt-dlp cookies args to bypass YouTube bot detection.

    Uses --cookies-from-browser to extract cookies from the user's browser.
    Default: chrome. Set YTDLP_COOKIES_BROWSER="" to disable.
    """
    browser = os.getenv("YTDLP_COOKIES_BROWSER", "chrome").strip()
    if browser:
        return ["--cookies-from-browser", browser]
    return []


def _extract_video_id(url: str) -> str:
    """Extract video ID from YouTube URL."""
    match = re.search(r'(?:v=|youtu\.be/|/shorts/)([a-zA-Z0-9_-]{11})', url)
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# InnerTube API — Tier 0 (zero deps, zero quota)
# ---------------------------------------------------------------------------

_INNERTUBE_PLAYER_URL = "https://www.youtube.com/youtubei/v1/player"

# Sentence-ending punctuation (Latin + CJK)
_SENTENCE_END_RE = re.compile(r'[.?!…。？！⁈⁇‼‽．]+')

# CJK Unicode ranges for smart text joining
_CJK_RANGES = (
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x3040, 0x309F),    # Hiragana
    (0x30A0, 0x30FF),    # Katakana
    (0xAC00, 0xD7AF),    # Hangul
    (0xFF00, 0xFFEF),    # Fullwidth Forms
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _fetch_innertube_transcript(
    video_id: str, lang: str = "en"
) -> Tuple[List[Dict], Dict]:
    """Fetch subtitles via YouTube InnerTube API (zero deps, zero API key).

    Returns (snippets, innertube_meta):
        snippets: [{text, start (float seconds), duration (float seconds)}]
        innertube_meta: {title, author, description, ...} from InnerTube
    """
    from feedgrab.utils import http_client

    snippets: List[Dict] = []
    meta: Dict = {}

    try:
        # Step 1: Get page HTML → extract INNERTUBE_API_KEY
        resp = http_client.get(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"},
            timeout=15,
        )
        page_html = resp.text

        # Handle EU consent redirect
        if "consent.youtube.com" in page_html or 'action="https://consent' in page_html:
            logger.info("[InnerTube] EU consent detected, adding CONSENT cookie")
            resp = http_client.get(
                f"https://www.youtube.com/watch?v={video_id}",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Cookie": "CONSENT=YES+cb.20210328-17-p0.en+FX+999",
                },
                timeout=15,
            )
            page_html = resp.text

        key_match = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', page_html)
        api_key = key_match.group(1) if key_match else "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"

        # Step 2: InnerTube Player API (ANDROID client bypasses some restrictions)
        player_resp = http_client.post(
            f"{_INNERTUBE_PLAYER_URL}?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "context": {
                    "client": {
                        "clientName": "ANDROID",
                        "clientVersion": "20.10.38",
                    }
                },
                "videoId": video_id,
            },
            timeout=15,
        )
        player_data = player_resp.json()

        # Extract basic metadata from InnerTube
        video_details = player_data.get("videoDetails", {})
        meta = {
            "title": video_details.get("title", ""),
            "author": video_details.get("author", ""),
            "description": video_details.get("shortDescription", ""),
            "length_seconds": int(video_details.get("lengthSeconds", 0)),
            "view_count": int(video_details.get("viewCount", 0)),
            "thumbnail": (video_details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", "")),
        }

        # Step 3: Find caption tracks
        captions = player_data.get("captions", {})
        renderer = captions.get("playerCaptionsTracklistRenderer", {})
        tracks = renderer.get("captionTracks", [])

        if not tracks:
            logger.info("[InnerTube] No caption tracks available")
            return [], meta

        # Language matching: prefer exact match, then prefix match, then first track + tlang
        langs_to_try = [lang, "zh-CN", "zh-Hans", "zh-Hant", "zh", "en", "en-US"]
        seen = set()
        langs_to_try = [l for l in langs_to_try if l not in seen and not seen.add(l)]

        base_url = ""
        for try_lang in langs_to_try:
            for track in tracks:
                code = track.get("languageCode", "")
                if code == try_lang or code.startswith(try_lang.split("-")[0]):
                    base_url = track["baseUrl"]
                    logger.info(f"[InnerTube] Matched caption track: {code}")
                    break
            if base_url:
                break

        if not base_url:
            # Use first track + translation param
            base_url = tracks[0]["baseUrl"]
            target = langs_to_try[0]
            if "&tlang=" not in base_url:
                base_url += f"&tlang={target}"
            logger.info(f"[InnerTube] Using first track + tlang={target}")

        # Strip fmt=srv3 to get default XML format (<text start="" dur="">)
        base_url = re.sub(r'&fmt=[^&]+', '', base_url)

        # Step 4: Download and parse subtitle XML
        xml_resp = http_client.get(base_url, timeout=15)
        xml_text = xml_resp.text

        for m in re.finditer(
            r'<text\s+start="([^"]+)"\s+dur="([^"]+)"[^>]*>(.*?)</text>',
            xml_text, re.DOTALL,
        ):
            start = float(m.group(1))
            dur = float(m.group(2))
            text = html.unescape(html.unescape(m.group(3))).replace("\n", " ").strip()
            if text:
                snippets.append({"text": text, "start": start, "duration": dur})

        logger.info(f"[InnerTube] Got {len(snippets)} subtitle snippets")

    except Exception as e:
        logger.warning(f"[InnerTube] Failed: {e}")
        return [], meta

    return snippets, meta


# ---------------------------------------------------------------------------
# Smart segmentation — sentence splitting + paragraph grouping
# ---------------------------------------------------------------------------

def _merge_text(a: str, b: str) -> str:
    """Merge two text fragments: no space between CJK chars, space for Latin."""
    if not a:
        return b
    if not b:
        return a
    if _is_cjk(a[-1]) or _is_cjk(b[0]):
        return a + b
    return a + " " + b


def _seconds_to_ts(s: float) -> str:
    """Convert seconds to HH:MM:SS timestamp."""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _segment_into_sentences(snippets: List[Dict]) -> List[Dict]:
    """Split raw subtitle snippets into natural sentences with timestamps.

    Input: [{text, start (float), duration (float)}]
    Output: [{text, start: "HH:MM:SS", end: "HH:MM:SS"}]

    For auto-generated captions without punctuation, falls back to
    snippet-level grouping (no sentence splitting).
    """
    if not snippets:
        return []

    # Phase 1: Split each snippet at sentence-ending punctuation
    parts = []  # [{text, start (float), end (float)}]
    for snip in snippets:
        text = snip["text"]
        s_start = snip["start"]
        s_dur = snip["duration"]
        total_len = len(text)
        if total_len == 0:
            continue

        # Find all sentence-end positions
        boundaries = []
        for m in _SENTENCE_END_RE.finditer(text):
            boundaries.append(m.end())

        if not boundaries:
            parts.append({"text": text, "start": s_start, "end": s_start + s_dur})
            continue

        prev = 0
        for bound in boundaries:
            frag = text[prev:bound].strip()
            if frag:
                frag_start = s_start + (prev / total_len) * s_dur
                frag_end = s_start + (bound / total_len) * s_dur
                parts.append({"text": frag, "start": frag_start, "end": frag_end})
            prev = bound

        # Remaining text after last punctuation
        if prev < total_len:
            frag = text[prev:].strip()
            if frag:
                frag_start = s_start + (prev / total_len) * s_dur
                parts.append({"text": frag, "start": frag_start, "end": s_start + s_dur})

    # Fallback: if very few punctuation found (<10% of snippets), skip sentence
    # merging and convert raw snippets directly (auto-generated captions without punctuation)
    punct_ratio = sum(1 for s in snippets if _SENTENCE_END_RE.search(s["text"])) / len(snippets)
    if punct_ratio < 0.1 or len(parts) < 1:
        return [
            {
                "text": snip["text"],
                "start": _seconds_to_ts(snip["start"]),
                "end": _seconds_to_ts(snip["start"] + snip["duration"]),
            }
            for snip in snippets if snip["text"]
        ]

    # Phase 2: Merge parts into complete sentences
    sentences = []
    buf_text = ""
    buf_start = 0.0
    buf_end = 0.0

    for part in parts:
        if not buf_text:
            buf_start = part["start"]
        buf_text = _merge_text(buf_text, part["text"])
        buf_end = part["end"]

        # Check if this part ends with sentence-ending punctuation
        if _SENTENCE_END_RE.search(part["text"].rstrip()[-3:] if len(part["text"]) >= 3 else part["text"]):
            sentences.append({
                "text": buf_text,
                "start": _seconds_to_ts(buf_start),
                "end": _seconds_to_ts(buf_end),
            })
            buf_text = ""

    # Flush remaining buffer
    if buf_text:
        sentences.append({
            "text": buf_text,
            "start": _seconds_to_ts(buf_start),
            "end": _seconds_to_ts(buf_end),
        })

    return sentences


def _group_into_paragraphs(
    sentences: List[Dict], max_per_group: int = 5, gap_threshold: float = 2.0
) -> List[List[Dict]]:
    """Group sentences into paragraphs.

    Rules: max N sentences per paragraph, force break on >gap_threshold silence.
    """
    if not sentences:
        return []

    def _ts_to_sec(ts: str) -> float:
        parts = ts.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

    groups = []
    current = []

    for i, sent in enumerate(sentences):
        if current and (
            len(current) >= max_per_group
            or (i > 0 and _ts_to_sec(sent["start"]) - _ts_to_sec(sentences[i - 1]["end"]) > gap_threshold)
        ):
            groups.append(current)
            current = []
        current.append(sent)

    if current:
        groups.append(current)

    return groups


# ---------------------------------------------------------------------------
# Chapter parsing
# ---------------------------------------------------------------------------

def _parse_chapters(description: str) -> List[Dict]:
    """Parse chapter timestamps from video description.

    Returns [{title, start_seconds}] or [] if fewer than 2 chapters found.
    """
    if not description:
        return []

    chapters = []
    for line in description.split("\n"):
        line = line.strip()
        m = re.match(r'^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s+(.+)$', line)
        if m:
            h = int(m.group(1) or 0)
            mins = int(m.group(2))
            secs = int(m.group(3))
            title = m.group(4).strip()
            chapters.append({"title": title, "start_seconds": h * 3600 + mins * 60 + secs})

    return chapters if len(chapters) >= 2 else []


def _format_chapter_ts(seconds: int) -> str:
    """Format chapter timestamp as M:SS or H:MM:SS."""
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Transcript Markdown formatting
# ---------------------------------------------------------------------------

def _format_transcript_markdown(
    sentences: List[Dict],
    chapters: List[Dict],
) -> str:
    """Format segmented sentences + chapters into structured Markdown.

    Output with chapters:
        ## Chapter Title [0:00]
        Text... [00:00:05 → 00:01:23]

    Output without chapters:
        Text... [00:00:05 → 00:01:23]
    """

    def _ts_to_sec(ts: str) -> float:
        parts = ts.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

    def _render_paragraphs(sents: List[Dict]) -> str:
        groups = _group_into_paragraphs(sents)
        lines = []
        for group in groups:
            text = " ".join(s["text"] for s in group)
            # CJK-aware join
            merged = group[0]["text"]
            for s in group[1:]:
                merged = _merge_text(merged, s["text"])
            ts_start = group[0]["start"]
            ts_end = group[-1]["end"]
            lines.append(f"{merged} [{ts_start} → {ts_end}]")
        return "\n\n".join(lines)

    if not sentences:
        return ""

    if not chapters:
        return _render_paragraphs(sentences)

    # Split sentences by chapter boundaries
    parts = []
    for i, ch in enumerate(chapters):
        ch_start = ch["start_seconds"]
        ch_end = chapters[i + 1]["start_seconds"] if i + 1 < len(chapters) else float("inf")

        ch_sents = [
            s for s in sentences
            if ch_start <= _ts_to_sec(s["start"]) < ch_end
        ]

        header = f"## {ch['title']} [{_format_chapter_ts(ch_start)}]"
        body = _render_paragraphs(ch_sents) if ch_sents else ""
        parts.append(f"{header}\n\n{body}" if body else header)

    return "\n\n".join(parts)


def _get_subtitles_via_ytdlp(url: str, lang: str = "en") -> List[Dict]:
    """Download auto-generated subtitles using yt-dlp.

    Returns list of snippet dicts [{text, start, duration}], or [] if unavailable.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "sub")

        cmd = [
            "yt-dlp",
            *_js_runtime_args(),
            *_cookies_args(),
            "--write-auto-sub",
            "--write-sub",
            "--sub-lang", lang,
            "--sub-format", "srt",
            "--skip-download",
            "-o", output_path,
            url,
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            logger.warning("未找到 yt-dlp。请安装：pip install yt-dlp")
            return []
        except subprocess.TimeoutExpired:
            logger.warning("yt-dlp 字幕下载超时")
            return []

        for ext in [f".{lang}.srt", f".{lang}.vtt"]:
            sub_file = output_path + ext
            if os.path.exists(sub_file):
                return _parse_srt_to_snippets(sub_file)

    return []


def _parse_srt_to_snippets(filepath: str) -> List[Dict]:
    """Parse SRT file into structured snippets [{text, start, duration}]."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    snippets = []
    # Match SRT blocks: sequence number, timestamp line, text lines
    blocks = re.split(r'\n\s*\n', content.strip())

    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 2:
            continue

        # Find the timestamp line
        ts_line = None
        text_lines = []
        for line in lines:
            if '-->' in line:
                ts_line = line
            elif ts_line is not None:
                # Text lines come after timestamp
                cleaned = line.strip()
                if cleaned and not (cleaned.startswith('[') and cleaned.endswith(']')):
                    text_lines.append(cleaned)

        if not ts_line or not text_lines:
            continue

        # Parse timestamps: 00:00:01,234 --> 00:00:03,456
        ts_match = re.match(
            r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})',
            ts_line.strip(),
        )
        if not ts_match:
            continue

        g = ts_match.groups()
        start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000
        end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7]) / 1000

        text = " ".join(text_lines)
        snippets.append({"text": text, "start": start, "duration": end - start})

    return snippets


# ---------------------------------------------------------------------------
# Groq Whisper helpers
# ---------------------------------------------------------------------------

_GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def _whisper_single(audio_path: str, api_key: str, model: str, lang: str) -> List[Dict]:
    """Transcribe a single audio file via Groq Whisper verbose_json.

    Returns snippets [{text, start (float), duration (float)}].
    """
    import requests  # use standard requests for multipart upload (curl_cffi incompatible)

    try:
        with open(audio_path, "rb") as f:
            response = requests.post(
                _GROQ_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (os.path.basename(audio_path), f, "audio/mp4")},
                data={
                    "model": model,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                    "language": lang,
                },
                timeout=180,
            )

        if response.status_code != 200:
            logger.warning(f"Groq Whisper API error: {response.status_code} {response.text[:200]}")
            return []

        data = response.json()
        segments = data.get("segments", [])
        return [
            {
                "text": seg["text"].strip(),
                "start": float(seg["start"]),
                "duration": float(seg["end"]) - float(seg["start"]),
            }
            for seg in segments if seg.get("text", "").strip()
        ]
    except Exception as e:
        logger.warning(f"Whisper 转录失败：{e}")
        return []


def _whisper_chunked(
    audio_path: str, tmpdir: str, api_key: str, model: str, lang: str,
    chunk_secs: int = 600, overlap_secs: int = 10,
) -> List[Dict]:
    """Split large audio into chunks via ffmpeg, transcribe each, merge results.

    Each chunk is 10 minutes with 10-second overlap. Timestamps are offset-adjusted.
    Overlap segments are deduplicated by dropping segments from the next chunk
    that fall within the overlap window of the previous chunk.
    """
    # Probe audio duration via ffprobe
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        total_duration = float(probe.stdout.strip())
    except Exception:
        logger.warning("ffprobe 执行失败，无法确定音频时长并切分")
        return []

    # Generate chunk boundaries
    chunks = []
    start = 0.0
    while start < total_duration:
        end = min(start + chunk_secs, total_duration)
        chunks.append((start, end))
        start += chunk_secs - overlap_secs  # overlap with previous chunk

    logger.info(f"正在将 {total_duration:.0f}s 音频切分为 {len(chunks)} 段"
                f"（每段 {chunk_secs}s，重叠 {overlap_secs}s）")

    all_snippets: List[Dict] = []
    prev_end = 0.0  # track where previous chunk's content ends

    for i, (c_start, c_end) in enumerate(chunks):
        chunk_path = os.path.join(tmpdir, f"chunk_{i:03d}.m4a")
        # ffmpeg extract chunk
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", audio_path,
                 "-ss", str(c_start), "-to", str(c_end),
                 "-c", "copy", chunk_path],
                capture_output=True, timeout=30,
            )
        except Exception as e:
            logger.warning(f"ffmpeg 切分第 {i} 段失败：{e}")
            continue

        if not os.path.exists(chunk_path):
            continue

        logger.info(f"  音频片段 {i + 1}/{len(chunks)}：{c_start:.0f}s-{c_end:.0f}s "
                     f"({os.path.getsize(chunk_path) // 1024}KB)")

        segments = _whisper_single(chunk_path, api_key, model, lang)

        # Offset timestamps and deduplicate overlap region
        for seg in segments:
            abs_start = seg["start"] + c_start
            # Skip segments that fall within the overlap of the previous chunk
            if abs_start < prev_end - 1.0:  # 1s tolerance
                continue
            all_snippets.append({
                "text": seg["text"],
                "start": abs_start,
                "duration": seg["duration"],
            })

        prev_end = c_end

    return all_snippets


def _transcribe_via_whisper(url: str) -> List[Dict]:
    """Download audio with yt-dlp and transcribe via Groq Whisper API.

    Returns snippets [{text, start (float), duration (float)}] with segment
    timestamps, compatible with _segment_into_sentences() pipeline.
    Returns [] if unavailable.

    Requires: GROQ_API_KEY env var + yt-dlp installed.
    Supports files up to 100MB (Groq Dev tier). Larger files are split into
    10-minute chunks via ffmpeg and transcribed sequentially.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.info("未设置 GROQ_API_KEY，跳过 Whisper 转录")
        return []

    from feedgrab.config import groq_whisper_model, youtube_whisper_lang
    model = groq_whisper_model()
    lang = youtube_whisper_lang()

    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = os.path.join(tmpdir, "audio.%(ext)s")

        base_cmd = [
            "yt-dlp",
            *_js_runtime_args(),
            "-x",
            "--audio-format", "m4a",
            "--audio-quality", "5",
            "-o", output_template,
            "--no-playlist",
            url,
        ]

        # Try with cookies first, then without (Chrome DB lock on Windows)
        cookie_args = _cookies_args()
        attempts = [base_cmd[:1] + cookie_args + base_cmd[1:]] if cookie_args else []
        attempts.append(base_cmd)  # fallback without cookies

        downloaded = False
        for cmd in attempts:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                # Check if audio file was created
                found = False
                for f in os.listdir(tmpdir):
                    if f.startswith("audio."):
                        found = True
                        break
                if found:
                    downloaded = True
                    break
                # If cookie attempt failed, log and try next
                if cookie_args and cmd is attempts[0]:
                    stderr = result.stderr or ""
                    if "cookie" in stderr.lower() or result.returncode != 0:
                        logger.info("[Whisper] Cookie 提取失败，改为不带 Cookie 重试...")
            except FileNotFoundError:
                logger.warning("未找到 yt-dlp，无法下载音频")
                return []
            except subprocess.TimeoutExpired:
                logger.warning("yt-dlp 音频下载超时")
                return []

        if not downloaded:
            logger.warning("没有下载到音频文件")
            return []

        # Find the downloaded audio file
        audio_path = os.path.join(tmpdir, "audio.m4a")
        if not os.path.exists(audio_path):
            for f in os.listdir(tmpdir):
                if f.startswith("audio."):
                    audio_path = os.path.join(tmpdir, f)
                    break
            else:
                logger.warning("没有下载到音频文件")
                return []

        file_size = os.path.getsize(audio_path)
        _MAX_DIRECT = 100 * 1024 * 1024  # 100MB Groq Dev tier limit

        if file_size > _MAX_DIRECT:
            # Split into 10-min chunks via ffmpeg
            logger.info(f"Audio {file_size // 1024 // 1024}MB > 100MB, splitting into chunks...")
            snippets = _whisper_chunked(audio_path, tmpdir, api_key, model, lang)
        else:
            logger.info(f"Transcribing {file_size // 1024}KB audio via Groq Whisper ({model})...")
            snippets = _whisper_single(audio_path, api_key, model, lang)

        if snippets:
            logger.info(f"Whisper transcript: {len(snippets)} segments, "
                        f"{sum(len(s['text']) for s in snippets)} chars")
        return snippets


async def fetch_youtube(url: str, sub_lang: str = "en") -> Dict[str, Any]:
    """
    Fetch YouTube video content with multi-tier strategy.

    Tier 0: InnerTube API (zero deps, zero quota) + smart segmentation + chapters
    Tier 1: yt-dlp subtitles + smart segmentation
    Tier 2: yt-dlp audio + Groq Whisper transcription
    Tier 3: API description / Jina fallback
    """
    logger.info(f"Fetching YouTube: {url}")
    video_id = _extract_video_id(url)

    # Step 1: Try YouTube Data API v3 for rich metadata (1 quota unit)
    api_meta = None
    has_caption_hint = None
    if os.getenv("YOUTUBE_API_KEY", "").strip():
        try:
            from feedgrab.fetchers.youtube_search import get_single_video
            api_meta = get_single_video(video_id) if video_id else None
            if api_meta:
                has_caption_hint = api_meta.get("has_caption")
                logger.info(
                    f"[YouTube] API metadata OK: {api_meta['title'][:60]} "
                    f"(caption={has_caption_hint})"
                )
        except Exception as e:
            logger.warning(f"[YouTube] API metadata failed ({e}), falling back")

    # Step 2: Tier 0 — InnerTube API (zero deps, zero quota)
    snippets = []
    innertube_meta = {}
    description_text = ""  # for chapter parsing
    if video_id:
        snippets, innertube_meta = _fetch_innertube_transcript(video_id, lang=sub_lang)
        description_text = innertube_meta.get("description", "")

    # Step 3: Tier 1 — yt-dlp fallback (if InnerTube failed)
    if not snippets and has_caption_hint is not False:
        langs_to_try = [sub_lang, "zh-CN", "zh-Hans", "zh-Hant", "zh", "en", "en-US"]
        seen = set()
        langs_to_try = [l for l in langs_to_try if l not in seen and not seen.add(l)]

        for lang in langs_to_try:
            logger.info(f"[Tier 1] yt-dlp subtitles ({lang})...")
            snippets = _get_subtitles_via_ytdlp(url, lang=lang)
            if snippets:
                break

    # Process snippets through segmentation pipeline
    transcript = ""
    has_transcript = False
    if snippets:
        # Use API description for chapters if available, else InnerTube description
        desc_for_chapters = (api_meta.get("description", "") if api_meta else "") or description_text
        chapters = _parse_chapters(desc_for_chapters)
        sentences = _segment_into_sentences(snippets)
        transcript = _format_transcript_markdown(sentences, chapters)
        has_transcript = True
        tier_used = "InnerTube" if innertube_meta else "yt-dlp"
        logger.info(
            f"[YouTube] {tier_used} transcript: {len(transcript)} chars, "
            f"{len(sentences)} sentences, {len(chapters)} chapters"
        )

    # Step 4: Tier 2 — Whisper transcription (returns snippets with timestamps)
    if not has_transcript:
        logger.info("[Tier 2] No subtitles, trying Whisper transcription...")
        whisper_snippets = _transcribe_via_whisper(url)
        if whisper_snippets:
            desc_for_ch = (api_meta.get("description", "") if api_meta else "") or description_text
            chapters = _parse_chapters(desc_for_ch)
            sentences = _segment_into_sentences(whisper_snippets)
            transcript = _format_transcript_markdown(sentences, chapters)
            has_transcript = True
            logger.info(
                f"[Tier 2] Whisper transcript: {len(transcript)} chars, "
                f"{len(sentences)} sentences, {len(chapters)} chapters"
            )

    # Step 5: Tier 3 — Description / Jina fallback (no transcript available)
    if not has_transcript:
        no_transcript_hint = "> **Note**: 本视频无可用字幕（无自动字幕、无手动字幕），以下为视频描述。\n\n"
        if api_meta and api_meta.get("description"):
            logger.info("[Tier 3] Using API description")
            transcript = no_transcript_hint + api_meta["description"]
        elif innertube_meta.get("description"):
            logger.info("[Tier 3] Using InnerTube description")
            transcript = no_transcript_hint + innertube_meta["description"]
        else:
            logger.info("[Tier 3] Falling back to Jina")
            from feedgrab.fetchers.jina import fetch_via_jina
            jina_data = fetch_via_jina(url)
            transcript = jina_data.get("content", "")

    content = transcript

    # Build result — prefer YouTube Data API metadata > InnerTube metadata
    if api_meta:
        return {
            "title": api_meta["title"],
            "description": content,
            "author": api_meta["channel_title"],
            "url": url,
            "video_id": video_id,
            "has_transcript": has_transcript,
            "platform": "youtube",
            "channel_id": api_meta["channel_id"],
            "published_at": api_meta["published_at"],
            "duration": api_meta["duration"],
            "duration_seconds": api_meta["duration_seconds"],
            "view_count": api_meta["view_count"],
            "like_count": api_meta["like_count"],
            "comment_count": api_meta["comment_count"],
            "tags": api_meta["tags"],
            "category_id": api_meta["category_id"],
            "definition": api_meta["definition"],
            "has_caption": api_meta["has_caption"],
            "thumbnail": api_meta["thumbnail"],
        }
    elif innertube_meta:
        return {
            "title": innertube_meta.get("title", "") or f"YouTube Video {video_id}",
            "description": content,
            "author": innertube_meta.get("author", ""),
            "url": url,
            "video_id": video_id,
            "has_transcript": has_transcript,
            "platform": "youtube",
            "view_count": innertube_meta.get("view_count", 0),
            "duration_seconds": innertube_meta.get("length_seconds", 0),
            "thumbnail": innertube_meta.get("thumbnail", ""),
        }
    else:
        from feedgrab.fetchers.jina import fetch_via_jina
        jina_data = fetch_via_jina(url) if not content else {"title": "", "author": ""}
        return {
            "title": jina_data.get("title", "") or f"YouTube Video {video_id}",
            "description": content,
            "author": jina_data.get("author", ""),
            "url": url,
            "video_id": video_id,
            "has_transcript": has_transcript,
            "platform": "youtube",
        }
