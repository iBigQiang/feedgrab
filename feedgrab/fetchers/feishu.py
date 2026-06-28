# -*- coding: utf-8 -*-
"""
Feishu / Lark document fetcher.

Tier 0: Open API (lark-oapi SDK, needs FEISHU_APP_ID + FEISHU_APP_SECRET)
Tier 1: Playwright + window.PageMain (needs feedgrab login feishu)
Tier 2: Jina Reader (zero-config fallback)
"""

import hashlib
import json as _json_mod
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from feedgrab.config import (
    feishu_app_id,
    feishu_app_secret,
    feishu_download_images,
    get_session_dir,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Title helpers
# ---------------------------------------------------------------------------

# Zero-width chars that Feishu injects into document.title for tracking
_ZW_RE = re.compile(r"[\u200B-\u200F\u2028-\u202F\u2060-\u206F\uFEFF]")
_GENERIC_TITLES = {"Docs", "飞书云文档", "Feishu Docs", "Lark Docs", "Untitled"}


def _clean_feishu_title(raw: str) -> str:
    """Strip zero-width characters, newlines, and generic suffixes from a Feishu title."""
    cleaned = _ZW_RE.sub("", raw)
    # Collapse newlines / carriage returns into a single space
    cleaned = re.sub(r"[\r\n]+", " ", cleaned)
    # Collapse multiple spaces
    cleaned = re.sub(r"  +", " ", cleaned)
    for suffix in (" - 飞书云文档", " - Feishu Docs", " - Lark Docs"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned.strip()


def _extract_title_from_content(md_content: str) -> str:
    """Extract the first Markdown heading as a fallback title."""
    for line in md_content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""

# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

_FEISHU_DOMAINS = (
    ".feishu.cn",
    ".larksuite.com",
    ".larkoffice.com",
    ".f.mioffice.cn",
)


def _get_feishu_domains() -> tuple:
    """Return Feishu domains including any user-configured custom ones."""
    import os
    custom = os.getenv("FEISHU_CUSTOM_DOMAINS", "").strip()
    if not custom:
        return _FEISHU_DOMAINS
    extra = tuple(
        d.strip() if d.strip().startswith(".") else f".{d.strip()}"
        for d in custom.split(",")
        if d.strip()
    )
    return _FEISHU_DOMAINS + extra

_DOC_TYPE_PATTERNS = {
    "wiki": re.compile(r"/wiki/([A-Za-z0-9]+)"),
    "docx": re.compile(r"/docx/([A-Za-z0-9]+)"),
    "docs": re.compile(r"/docs/([A-Za-z0-9]+)"),
}


def is_feishu_url(url: str) -> bool:
    """Check whether *url* belongs to a Feishu / Lark domain."""
    netloc = urlparse(url).netloc.lower()
    for d in _get_feishu_domains():
        if netloc.endswith(d) or netloc == d.lstrip("."):
            return True
    return False


def parse_feishu_url(url: str) -> Optional[Dict[str, str]]:
    """Extract doc_type and token from a Feishu URL.

    Returns ``{"type": "wiki"|"docx"|"docs", "token": "..."}`` or *None*.
    """
    path = urlparse(url).path
    # /wiki/settings/... is a space settings page, not a document
    if "/wiki/settings/" in path:
        return None
    for doc_type, pat in _DOC_TYPE_PATTERNS.items():
        m = pat.search(path)
        if m:
            return {"type": doc_type, "token": m.group(1)}
    return None


# ---------------------------------------------------------------------------
# Open API helpers (lark-oapi SDK)
# ---------------------------------------------------------------------------

_lark_client = None  # cached client instance


def _is_api_available() -> bool:
    """Return True when lark-oapi is installed **and** credentials configured."""
    if not feishu_app_id() or not feishu_app_secret():
        return False
    try:
        import lark_oapi  # noqa: F401
        return True
    except ImportError:
        return False


def _get_lark_client():
    """Get or create a cached ``lark_oapi.Client``."""
    global _lark_client
    if _lark_client is not None:
        return _lark_client
    import lark_oapi as lark

    _lark_client = (
        lark.Client.builder()
        .app_id(feishu_app_id())
        .app_secret(feishu_app_secret())
        .build()
    )
    return _lark_client


def _resolve_wiki_node(node_token: str) -> Dict[str, str]:
    """Resolve a wiki *node_token* to its underlying obj_token + obj_type.

    Returns ``{"obj_token": "...", "obj_type": "docx"|"sheet"|...,
               "title": "...", "space_id": "..."}``.
    """
    import lark_oapi as lark
    from lark_oapi.api.wiki.v2 import GetNodeSpaceRequest

    client = _get_lark_client()
    req = (
        GetNodeSpaceRequest.builder()
        .token(node_token)
        .build()
    )
    resp = client.wiki.v2.space.get_node(req)
    if not resp.success():
        raise RuntimeError(
            f"wiki get_node failed: code={resp.code} msg={resp.msg}"
        )
    node = resp.data.node
    return {
        "obj_token": node.obj_token,
        "obj_type": node.obj_type,
        "title": node.title or "",
        "space_id": node.space_id or "",
        "has_child": getattr(node, "has_child", False),
        "node_token": node.node_token or node_token,
        "parent_node_token": getattr(node, "parent_node_token", ""),
    }


def _fetch_document_blocks(document_id: str) -> Tuple[str, List[Any]]:
    """Fetch all blocks of a docx document via Open API.

    Returns ``(doc_title, blocks_list)``.
    """
    import lark_oapi as lark
    from lark_oapi.api.docx.v1 import (
        ListDocumentBlockRequest,
        GetDocumentRequest,
    )

    client = _get_lark_client()

    # 1. Get document basic info (title)
    doc_req = GetDocumentRequest.builder().document_id(document_id).build()
    doc_resp = client.docx.v1.document.get(doc_req)
    doc_title = ""
    if doc_resp.success() and doc_resp.data and doc_resp.data.document:
        doc_title = doc_resp.data.document.title or ""

    # 2. Get all blocks (paginated, 500 per page)
    all_blocks: List[Any] = []
    page_token: Optional[str] = None
    while True:
        builder = (
            ListDocumentBlockRequest.builder()
            .document_id(document_id)
            .page_size(500)
            .document_revision_id(-1)
        )
        if page_token:
            builder = builder.page_token(page_token)
        req = builder.build()
        resp = client.docx.v1.document_block.list(req)
        if not resp.success():
            raise RuntimeError(
                f"blocks list failed: code={resp.code} msg={resp.msg}"
            )
        if resp.data and resp.data.items:
            all_blocks.extend(resp.data.items)
        if resp.data and resp.data.has_more:
            page_token = resp.data.page_token
        else:
            break

    return doc_title, all_blocks


def _fetch_via_api(url: str, parsed: Dict[str, str]) -> Dict[str, Any]:
    """Tier 0 – Fetch document content through Open API."""
    doc_type = parsed["type"]
    token = parsed["token"]

    if doc_type == "wiki":
        node_info = _resolve_wiki_node(token)
        obj_type = node_info["obj_type"]
        if obj_type not in ("docx", "doc"):
            raise RuntimeError(
                f"Wiki node obj_type={obj_type!r} is not a document "
                "(only docx/doc supported)"
            )
        document_id = node_info["obj_token"]
        wiki_title = node_info.get("title", "")
    else:
        document_id = token
        wiki_title = ""

    doc_title, blocks = _fetch_document_blocks(document_id)
    title = wiki_title or doc_title
    images_list: List[dict] = []
    # Per-document image subdirectory: first 7 chars of item_id
    _img_subdir = hashlib.md5(url.encode()).hexdigest()[:12]
    content = blocks_to_markdown(blocks, images=images_list,
                                 img_subdir=_img_subdir)

    return {
        "title": title,
        "content": content,
        "url": url,
        "author": "",
        "doc_type": doc_type,
        "doc_token": document_id,
        "images": [img.get("token", "") for img in images_list],
        "images_info": images_list,
        "img_subdir": _img_subdir,
        "tags": [],
    }


# ---------------------------------------------------------------------------
# Block → Markdown converter  (works with both API blocks and editor blocks)
# ---------------------------------------------------------------------------

# Open API block_type numbers → names
_BLOCK_TYPE_MAP = {
    1: "page",
    2: "text",
    3: "heading1", 4: "heading2", 5: "heading3",
    6: "heading4", 7: "heading5", 8: "heading6",
    9: "heading7", 10: "heading8", 11: "heading9",
    12: "bullet",
    13: "ordered",
    14: "code",
    15: "quote",
    16: "equation",
    17: "todo",
    18: "bitable",
    19: "callout",
    22: "divider",
    23: "file",
    24: "grid",
    25: "grid_column",
    26: "iframe",
    27: "image",
    30: "sheet",
    31: "table",
    32: "table_cell",
    35: "quote_container",
}

# Module-level heading cache for TOC generation
_TOC_HEADINGS: List[tuple] = []  # [(level, text), ...]


def _collect_headings(blocks) -> None:
    """Pre-scan blocks recursively to collect headings for TOC."""
    _TOC_HEADINGS.clear()
    _scan_headings(blocks)


def _scan_headings(blocks) -> None:
    for block in blocks:
        btype = _resolve_block_type(block)
        if btype.startswith("heading"):
            level = int(btype[-1]) if btype[-1].isdigit() else 1
            text = _elements_text(block)
            if text.strip():
                _TOC_HEADINGS.append((level, text.strip()))
        children = _get_children(block)
        if children:
            _scan_headings(children)


def _render_isv_block(block) -> Optional[str]:
    """Render ISV block — currently supports catalog (TOC) component."""
    snap = block.get("snapshot", {}) if isinstance(block, dict) else {}
    data = snap.get("data", {})
    # Detect catalog/TOC component
    if not isinstance(data, dict) or "showCataLogLevel" not in data:
        return None  # Not a TOC component, skip
    max_level = data.get("showCataLogLevel", 3)
    if not _TOC_HEADINGS:
        return None
    lines: List[str] = []
    for level, text in _TOC_HEADINGS:
        if level > max_level:
            continue
        indent = "  " * (level - 1)
        lines.append(f"{indent}- {text}")
    return "\n".join(lines) if lines else None


def blocks_to_markdown(
    blocks, depth: int = 0, images: Optional[List[dict]] = None,
    img_subdir: str = "", _is_root: bool = True,
) -> str:
    """Convert a list of API block objects to Markdown.

    *blocks* can be SDK response objects (with ``block_type`` int attribute)
    or dicts from Playwright extraction (with ``type`` string key).

    If *images* list is provided, image metadata dicts are appended to it
    (side-effect collector for the download pipeline).

    *img_subdir* — when set, image paths become
    ``attachments/{img_subdir}/{fname}`` instead of ``attachments/{fname}``.
    """
    # Pre-collect headings for TOC generation (ISV catalog blocks)
    # Only on the true root call, not recursive _render_children calls
    if _is_root:
        _collect_headings(blocks)

    parts: List[str] = []
    ordered_counter = 0

    for block in blocks:
        btype = _resolve_block_type(block)

        # Reset ordered counter when we leave an ordered-list run
        if btype != "ordered":
            ordered_counter = 0

        md = _block_to_md(block, btype, depth, ordered_counter, images,
                          img_subdir)

        if btype == "ordered":
            ordered_counter += 1

        if md is not None:
            parts.append(md)

    return "\n\n".join(parts)


def _resolve_block_type(block) -> str:
    """Normalize block type to a string name."""
    # SDK response object — has int block_type attribute
    if hasattr(block, "block_type"):
        bt = block.block_type
        if isinstance(bt, int):
            return _BLOCK_TYPE_MAP.get(bt, f"unknown_{bt}")
        return str(bt)
    # Dict from Playwright — has string "type" key
    if isinstance(block, dict):
        t = block.get("type", "")
        if isinstance(t, int):
            return _BLOCK_TYPE_MAP.get(t, f"unknown_{t}")
        if t == "fallback":
            snap = block.get("snapshot")
            if isinstance(snap, dict) and snap.get("type") == "code":
                return "code"
        return str(t)
    return "unknown"


def _block_to_md(
    block, btype: str, depth: int, ordered_idx: int,
    images: Optional[List[dict]] = None,
    img_subdir: str = "",
) -> Optional[str]:
    """Render a single block to Markdown text, or None to skip."""
    indent = "  " * depth

    if btype == "page":
        return None  # root container, skip

    if btype == "text":
        return _elements_text(block)

    if btype.startswith("heading"):
        level = int(btype[-1]) if btype[-1].isdigit() else 1
        level = min(level, 6)  # h7-h9 → h6
        text = _elements_text(block)
        return f"{'#' * level} {text}"

    if btype == "bullet":
        text = _elements_text(block)
        child_md = _render_children(block, depth + 1, images, img_subdir)
        line = f"{indent}- {text}"
        return f"{line}\n{child_md}" if child_md else line

    if btype == "ordered":
        text = _elements_text(block)
        child_md = _render_children(block, depth + 1, images, img_subdir)
        seq_label = _calc_ordered_label(block, ordered_idx)
        line = f"{indent}{seq_label}. {text}"
        return f"{line}\n{child_md}" if child_md else line

    if btype == "todo":
        text = _elements_text(block)
        done = _get_todo_done(block)
        mark = "x" if done else " "
        return f"{indent}- [{mark}] {text}"

    if btype == "code":
        text = _get_code_text(block)
        lang = _get_code_lang(block)
        fence = "````"
        return f"{fence}{lang}\n{text}\n{fence}"

    if btype in ("quote", "quote_container"):
        text = _elements_text(block)
        child_md = _render_children(block, depth, images, img_subdir)
        combined = text or child_md or ""
        lines = combined.split("\n")
        return "\n".join(f"> {l}" for l in lines)

    if btype == "callout":
        text = _elements_text(block)
        child_md = _render_children(block, depth, images, img_subdir)
        combined = text or child_md or ""
        lines = combined.split("\n")
        return "\n".join(f"> {l}" for l in lines)

    if btype == "equation":
        formula = _get_equation_text(block)
        return f"$$\n{formula}\n$$" if formula else None

    if btype == "divider":
        return "---"

    if btype == "image":
        info = _get_image_info(block)
        token = info.get("token", "") or _get_image_token(block)
        if not token:
            return None
        name = info.get("name", "") or "image"
        alt = name.rsplit(".", 1)[0] if "." in name else name
        idx = len(images) + 1 if images is not None else 0
        fname = _image_filename(info if info else {"name": name}, idx)
        if images is not None:
            info["_filename"] = fname
            images.append(info)
        # Use relative path for Obsidian compatibility.
        if img_subdir:
            path = f"attachments/{img_subdir}/{fname}"
        else:
            path = f"attachments/{fname}"
        return f"![{alt}]({path})"

    if btype == "file":
        name = _get_file_name(block)
        token = _get_file_token(block)
        return f"[{name}]({token})" if name else None

    if btype == "table":
        return _render_table(block)

    if btype in ("grid", "grid_column"):
        return _render_children(block, depth, images, img_subdir)

    if btype == "iframe":
        src = _get_iframe_src(block)
        return f"[Embed]({src})" if src else None

    # Sheet / Bitable / embedded objects (editor reports type="fallback")
    if btype in ("fallback", "bitable", "sheet"):
        return _render_embedded_block(block)

    # ISV catalog (TOC) component — generate from sibling headings
    if btype == "isv":
        return _render_isv_block(block)

    # Fallback: try to extract any text
    text = _elements_text(block)
    if text:
        return text
    return None


# ---------------------------------------------------------------------------
# TextElement → Markdown text
# ---------------------------------------------------------------------------

def _elements_text(block) -> str:
    """Extract formatted Markdown text from a block's text elements."""
    elements = _get_elements(block)
    if not elements:
        # Fallback for editor blocks with zoneState
        return _get_zone_text(block)
    parts: List[str] = []
    for el in elements:
        parts.append(_element_to_md(el))
    return "".join(parts)


def _element_to_md(el) -> str:
    """Convert a single TextElement to Markdown."""
    # SDK response object
    if hasattr(el, "text_run") and el.text_run:
        tr = el.text_run
        text = tr.content or ""
        style = tr.text_element_style if hasattr(tr, "text_element_style") else None
        return _apply_style(text, style)
    if hasattr(el, "mention_user") and el.mention_user:
        uid = el.mention_user.user_id or "user"
        return f"@{uid}"
    if hasattr(el, "mention_doc") and el.mention_doc:
        md = el.mention_doc
        title = getattr(md, "title", "") or "document"
        url = getattr(md, "url", "") or ""
        return f"[{title}]({url})" if url else title
    if hasattr(el, "equation") and el.equation:
        return f"${el.equation.content or ''}$"

    # Dict from Playwright
    if isinstance(el, dict):
        if "text_run" in el:
            tr = el["text_run"]
            text = tr.get("content", "")
            style = tr.get("text_element_style")
            return _apply_style(text, style)
        if "mention_user" in el:
            return f"@{el['mention_user'].get('user_id', 'user')}"
        if "mention_doc" in el:
            md = el["mention_doc"]
            title = md.get("title", "document")
            url = md.get("url", "")
            return f"[{title}]({url})" if url else title
        if "equation" in el:
            return f"${el['equation'].get('content', '')}$"
        # Bare text (from editor zoneState ops)
        if "insert" in el:
            text = el["insert"]
            attrs = el.get("attributes", {})
            return _apply_style_dict(text, attrs)

    return ""


def _apply_style(text: str, style) -> str:
    """Wrap *text* with Markdown markers from an SDK TextElementStyle or dict."""
    if not style or not text:
        return text

    def _get(attr, default=False):
        # Handle both SDK objects (getattr) and dicts (.get)
        if isinstance(style, dict):
            return style.get(attr, default)
        return getattr(style, attr, default)

    if _get("inline_code"):
        return f"`{text}`"
    if _get("bold"):
        text = f"**{text}**"
    if _get("italic"):
        text = f"*{text}*"
    if _get("strikethrough"):
        text = f"~~{text}~~"
    link = _get("link", None)
    if link:
        if isinstance(link, dict):
            url = link.get("url", "")
        else:
            url = getattr(link, "url", "") or ""
        if url:
            text = f"[{text}]({url})"
    return text


def _apply_style_dict(text: str, attrs: dict) -> str:
    """Wrap *text* with Markdown markers from a dict of style attributes."""
    if not attrs or not text:
        return text
    if attrs.get("inline_code") or attrs.get("code"):
        return f"`{text}`"
    if attrs.get("bold"):
        text = f"**{text}**"
    if attrs.get("italic"):
        text = f"*{text}*"
    if attrs.get("strikethrough"):
        text = f"~~{text}~~"
    url = attrs.get("link", {}).get("url", "") if isinstance(attrs.get("link"), dict) else ""
    if not url and isinstance(attrs.get("href"), str):
        url = attrs["href"]
    if url:
        text = f"[{text}]({url})"
    return text


# ---------------------------------------------------------------------------
# Block field accessors (handle both SDK objects and dicts)
# ---------------------------------------------------------------------------

def _get_elements(block) -> list:
    """Get text elements from a block."""
    # SDK object: block.<type_name>.elements  (e.g. block.text.elements)
    for attr in ("text", "heading1", "heading2", "heading3", "heading4",
                 "heading5", "heading6", "heading7", "heading8", "heading9",
                 "bullet", "ordered", "todo", "quote", "callout"):
        obj = getattr(block, attr, None)
        if obj is not None:
            els = getattr(obj, "elements", None)
            if els:
                return els
    # Dict form
    if isinstance(block, dict):
        for key in ("text", "heading1", "heading2", "heading3", "heading4",
                     "heading5", "heading6", "heading7", "heading8", "heading9",
                     "bullet", "ordered", "todo", "quote", "callout"):
            obj = block.get(key)
            if isinstance(obj, dict) and "elements" in obj:
                return obj["elements"]
        # Playwright Delta ops: zoneState.content.ops [{insert, attributes}]
        zs = block.get("zoneState")
        if isinstance(zs, dict):
            content = zs.get("content")
            if isinstance(content, dict):
                ops = content.get("ops")
                if isinstance(ops, list) and ops:
                    return ops
    return []


def _get_zone_text(block) -> str:
    """Fallback: extract allText from editor block's zoneState."""
    if isinstance(block, dict):
        zs = block.get("zoneState")
        if isinstance(zs, dict):
            return zs.get("allText", "").strip()
    return ""


def _get_todo_done(block) -> bool:
    obj = getattr(block, "todo", None)
    if obj is not None:
        return getattr(obj, "done", False)
    if isinstance(block, dict) and "todo" in block:
        return block["todo"].get("done", False)
    return False


def _get_code_text(block) -> str:
    obj = getattr(block, "code", None)
    if obj is not None:
        els = getattr(obj, "elements", None)
        if els:
            return "".join(
                (el.text_run.content if hasattr(el, "text_run") and el.text_run else "")
                for el in els
            )
    if isinstance(block, dict) and "code" in block:
        els = block["code"].get("elements", [])
        return "".join(
            el.get("text_run", {}).get("content", "") for el in els
        )
    if isinstance(block, dict):
        snap = block.get("snapshot")
        if isinstance(snap, dict) and snap.get("type") == "code":
            text = _get_snapshot_plain_text(snap)
            if text:
                return text.rstrip("\n")
    return _get_zone_text(block)


def _get_code_lang(block) -> str:
    obj = getattr(block, "code", None)
    if obj is not None:
        style = getattr(obj, "style", None)
        if style:
            lang = getattr(style, "language", None)
            if lang and isinstance(lang, int):
                return _CODE_LANG_MAP.get(lang, "")
            if isinstance(lang, str):
                return lang
    if isinstance(block, dict) and "code" in block:
        style = block["code"].get("style", {})
        lang = style.get("language", "")
        if isinstance(lang, int):
            return _CODE_LANG_MAP.get(lang, "")
        return _normalize_code_lang(lang)
    if isinstance(block, dict):
        snap = block.get("snapshot")
        if isinstance(snap, dict) and snap.get("type") == "code":
            return _normalize_code_lang(snap.get("language", ""))
    return ""


def _normalize_code_lang(lang: Any) -> str:
    """Normalize code language names for Markdown fences."""
    if isinstance(lang, int):
        return _CODE_LANG_MAP.get(lang, "")
    if not isinstance(lang, str):
        return ""
    normalized = re.sub(r"\s+", "", lang.strip().lower())
    if normalized in ("", "text", "plaintext", "plaintextplain"):
        return "plaintext" if normalized else ""
    if normalized == "plain":
        return "plaintext"
    return normalized


def _get_snapshot_plain_text(snapshot: dict) -> str:
    """Extract concatenated text payload from a serialized Feishu snapshot."""
    text_obj = snapshot.get("text")
    if not isinstance(text_obj, dict):
        return ""
    initial = text_obj.get("initialAttributedTexts")
    if not isinstance(initial, dict):
        return ""
    text_map = initial.get("text")
    if isinstance(text_map, dict):
        def _sort_key(value: str):
            sval = str(value)
            return (0, int(sval)) if sval.isdigit() else (1, sval)
        parts = []
        for key in sorted(text_map.keys(), key=_sort_key):
            value = text_map.get(key)
            if isinstance(value, str):
                parts.append(value)
        return "".join(parts)
    if isinstance(text_map, list):
        return "".join(v for v in text_map if isinstance(v, str))
    return ""


# Feishu code language enum → string  (partial, common languages)
_CODE_LANG_MAP = {
    1: "plaintext", 2: "abap", 3: "ada", 4: "apache",
    5: "apex", 6: "assembly", 7: "bash", 8: "c",
    9: "c#", 10: "c++", 11: "cobol", 12: "css",
    13: "coffeescript", 14: "d", 15: "dart", 16: "delphi",
    17: "django", 18: "dockerfile", 19: "erlang", 20: "fortran",
    22: "go", 23: "groovy", 24: "html", 25: "htmlbars",
    26: "http", 27: "haskell", 28: "json", 29: "java",
    30: "javascript", 31: "julia", 32: "kotlin", 33: "latex",
    34: "lisp", 35: "lua", 36: "matlab", 37: "makefile",
    38: "markdown", 39: "nginx", 40: "objective-c",
    41: "openedge", 42: "perl", 43: "php",
    44: "powershell", 45: "prolog", 46: "protobuf",
    47: "python", 48: "r", 49: "rpm", 50: "ruby",
    51: "rust", 52: "sas", 53: "scss", 54: "sql",
    55: "scala", 56: "scheme", 57: "shell", 58: "swift",
    59: "thrift", 60: "typescript", 61: "vbscript",
    62: "visual basic", 63: "xml", 64: "yaml", 65: "cmake",
    66: "diff", 67: "gams", 68: "gauge", 69: "gherkin",
    70: "graphql", 71: "groovy", 72: "toml",
}


def _get_equation_text(block) -> str:
    obj = getattr(block, "equation", None)
    if obj is not None:
        return getattr(obj, "content", "") or ""
    if isinstance(block, dict) and "equation" in block:
        return block["equation"].get("content", "")
    return ""


def _get_image_token(block) -> str:
    obj = getattr(block, "image", None)
    if obj is not None:
        return getattr(obj, "token", "") or ""
    if isinstance(block, dict):
        # Dict from API response: block["image"]["token"]
        if "image" in block:
            return block["image"].get("token", "")
        # Dict from Playwright: block["snapshot"]["image"]["token"]
        snap = block.get("snapshot", {})
        if isinstance(snap, dict) and "image" in snap:
            return snap["image"].get("token", "")
    return ""


def _get_image_info(block) -> dict:
    """Extract full image metadata (for download pipeline)."""
    # SDK object
    obj = getattr(block, "image", None)
    if obj is not None:
        return {
            "token": getattr(obj, "token", "") or "",
            "name": getattr(obj, "name", "") or "",
            "mime_type": getattr(obj, "mime_type", "") or "",
            "width": getattr(obj, "width", 0) or 0,
            "height": getattr(obj, "height", 0) or 0,
        }
    # Dict (API or Playwright snapshot)
    if isinstance(block, dict):
        img = block.get("image", {})
        if not img:
            img = block.get("snapshot", {}).get("image", {})
        if isinstance(img, dict) and img.get("token"):
            return {
                "token": img.get("token", ""),
                "name": img.get("name", ""),
                "mime_type": img.get("mimeType", "") or img.get("mime_type", ""),
                "width": img.get("width", 0),
                "height": img.get("height", 0),
            }
    return {}


def _get_file_name(block) -> str:
    obj = getattr(block, "file", None)
    if obj is not None:
        return getattr(obj, "name", "") or "file"
    if isinstance(block, dict) and "file" in block:
        return block["file"].get("name", "file")
    return "file"


def _get_file_token(block) -> str:
    obj = getattr(block, "file", None)
    if obj is not None:
        return getattr(obj, "token", "") or ""
    if isinstance(block, dict) and "file" in block:
        return block["file"].get("token", "")
    return ""


def _get_iframe_src(block) -> str:
    obj = getattr(block, "iframe", None)
    if obj is not None:
        comp = getattr(obj, "component", None)
        if comp:
            return getattr(comp, "url", "") or ""
    if isinstance(block, dict) and "iframe" in block:
        return block["iframe"].get("component", {}).get("url", "")
    return ""


def _render_embedded_block(block) -> Optional[str]:
    """Render an embedded block (sheet/bitable/mindmap/etc.) as a Markdown note.

    The Feishu editor stores embedded objects as "fallback" blocks with the
    real type in ``snapshot.type``. For sheets, we attempt to fetch cell data
    via Open API and render as GFM table.

    SDK Block objects (from Open API) store the token in dedicated attributes
    like ``block.sheet.token`` or ``block.bitable.token``.
    """
    snap = {}
    if isinstance(block, dict):
        snap = block.get("snapshot", {}) or {}
    else:
        # Playwright editor block: snapshot dict
        s = getattr(block, "snapshot", None)
        if s:
            snap = s if isinstance(s, dict) else {}
        # SDK Block: extract from typed attributes (sheet/bitable/etc.)
        if not snap:
            sheet_obj = getattr(block, "sheet", None)
            if sheet_obj and getattr(sheet_obj, "token", None):
                snap = {"type": "sheet", "token": sheet_obj.token}
            else:
                bt_obj = getattr(block, "bitable", None)
                if bt_obj and getattr(bt_obj, "token", None):
                    snap = {"type": "bitable", "token": bt_obj.token}

    embed_type = snap.get("type", "") or ""
    token = snap.get("token", "") or ""

    type_labels = {
        "sheet": "电子表格",
        "bitable": "多维表格",
        "mindnote": "思维导图",
        "slide": "演示文稿",
        "board": "画板",
    }
    label = type_labels.get(embed_type, embed_type or "嵌入内容")

    # For sheets: try Open API extraction
    if embed_type == "sheet" and token:
        table_md = _fetch_embedded_sheet(token)
        if table_md:
            return table_md

    if token:
        return f"> **[{label}]** (token: {token})"
    return f"> **[{label}]**"


# Module-level cache for Playwright-extracted sheet tables.
# Populated by _fetch_via_playwright() before blocks_to_markdown() runs,
# consumed by _render_embedded_block() → _fetch_embedded_sheet().
_PLAYWRIGHT_SHEET_CACHE: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Protobuf cell decoder (for client_vars snapshot)
# ---------------------------------------------------------------------------

def _parse_protobuf_fields(data: bytes) -> List[tuple]:
    """Parse protobuf wire format into (field_number, wire_type, value) tuples."""
    from google.protobuf.internal.decoder import _DecodeVarint

    fields: List[tuple] = []
    pos = 0
    while pos < len(data):
        try:
            tag, new_pos = _DecodeVarint(data, pos)
        except Exception:
            break
        fn = tag >> 3
        wt = tag & 0x7
        pos = new_pos
        if wt == 0:  # varint
            val, pos = _DecodeVarint(data, pos)
            fields.append((fn, wt, val))
        elif wt == 1:  # 64-bit
            fields.append((fn, wt, data[pos : pos + 8]))
            pos += 8
        elif wt == 2:  # length-delimited
            length, pos = _DecodeVarint(data, pos)
            fields.append((fn, wt, data[pos : pos + length]))
            pos += length
        elif wt == 5:  # 32-bit
            fields.append((fn, wt, data[pos : pos + 4]))
            pos += 4
        else:
            break
    return fields


def _extract_protobuf_cell_strings(block_raw: bytes) -> List[str]:
    """Navigate the 5-level Protobuf structure to extract cell text strings.

    Structure discovered via reverse engineering of Feishu sheet ``client_vars``
    snapshot blocks:

        L0[field 1] → L1[field 2] → L2[field 12] → L3[field 2]
        → L4[repeated field 2 = cell strings in row-major order]
    """

    def _get_ld(fields: List[tuple], field_num: int) -> Optional[bytes]:
        for fn, wt, v in fields:
            if fn == field_num and wt == 2:
                return v
        return None

    try:
        L0 = _parse_protobuf_fields(block_raw)
        l1_data = _get_ld(L0, 1)
        if not l1_data:
            return []
        L1 = _parse_protobuf_fields(l1_data)
        l2_data = _get_ld(L1, 2)
        if not l2_data:
            return []
        L2 = _parse_protobuf_fields(l2_data)
        l3_data = _get_ld(L2, 12)
        if not l3_data:
            return []
        L3 = _parse_protobuf_fields(l3_data)
        cell_blob = _get_ld(L3, 2)
        if not cell_blob:
            return []
        L4 = _parse_protobuf_fields(cell_blob)
        strings = []
        for fn, wt, v in L4:
            if fn == 2 and wt == 2:
                try:
                    strings.append(v.decode("utf-8"))
                except UnicodeDecodeError:
                    strings.append("")
        return strings
    except Exception:
        return []


def _parse_sheet_block_payload(block_raw: bytes) -> Optional[List[tuple]]:
    """Return the decoded L3 payload for a sheet block, or None on failure."""

    def _get_ld(fields: List[tuple], field_num: int) -> Optional[bytes]:
        for fn, wt, v in fields:
            if fn == field_num and wt == 2:
                return v
        return None

    try:
        L0 = _parse_protobuf_fields(block_raw)
        l1_data = _get_ld(L0, 1)
        if not l1_data:
            return None
        L1 = _parse_protobuf_fields(l1_data)
        l2_data = _get_ld(L1, 2)
        if not l2_data:
            return None
        L2 = _parse_protobuf_fields(l2_data)
        l3_data = _get_ld(L2, 12)
        if not l3_data:
            return None
        return _parse_protobuf_fields(l3_data)
    except Exception:
        return None


def _decode_packed_varints(data: bytes) -> List[int]:
    """Decode a protobuf packed-varint byte sequence."""
    from google.protobuf.internal.decoder import _DecodeVarint

    values: List[int] = []
    pos = 0
    while pos < len(data):
        value, pos = _DecodeVarint(data, pos)
        values.append(int(value))
    return values


def _extract_sheet_slot_mapping(block_raw: bytes) -> Optional[List[Optional[int]]]:
    """Extract per-slot string indices from the sheet block payload.

    Live Feishu sheet payloads use two different encodings:

    1. A packed varint array under slot-blob field 1. Values are 1-based
       string references and may intentionally repeat earlier strings when a
       later cell reuses the same vendor/type/price.
    2. A repeated descriptor list under slot-blob field 2. Some older payloads
       expose the string index directly there.

    The packed form is the authoritative one for the misaligned-table bug.
    """
    payload = _parse_sheet_block_payload(block_raw)
    if not payload:
        return None

    slot_blob = None
    for fn, wt, v in payload:
        if fn == 6 and wt == 2:
            slot_blob = v
            break
    if not slot_blob:
        return None

    try:
        slot_fields = _parse_protobuf_fields(slot_blob)

        packed = next(
            (v for fn, wt, v in slot_fields if fn == 1 and wt == 2 and v),
            b"",
        )
        if packed:
            packed_values = _decode_packed_varints(packed)
            entries = [
                (value - 1) if value > 0 else None
                for value in packed_values
            ]
            if any(value is not None for value in entries):
                return entries

        entries: List[Optional[int]] = []
        saw_nonempty = False
        for fn, wt, v in slot_fields:
            if fn != 2 or wt != 2:
                continue
            if not v:
                entries.append(None)
                continue
            inner = _parse_protobuf_fields(v)
            mapping: Dict[int, int] = {
                inner_fn: inner_v
                for inner_fn, inner_wt, inner_v in inner
                if inner_wt == 0
            }
            string_idx = mapping.get(2)
            if string_idx is not None:
                entries.append(int(string_idx))
                saw_nonempty = True
            else:
                entries.append(None)
        if not saw_nonempty:
            return None
        return entries
    except Exception:
        return None


def _pick_sheet_meta(meta: dict, cv_data: dict) -> Tuple[Optional[str], Optional[dict]]:
    """Pick the most likely target sheet metadata from workbook-level meta."""
    target_sheet_id = str(cv_data.get("sheetId") or "").strip()
    if target_sheet_id and target_sheet_id in meta:
        return target_sheet_id, meta[target_sheet_id]

    token = str(cv_data.get("token") or "").strip()
    if token and "_" in token:
        suffix = token.rsplit("_", 1)[-1].strip()
        if suffix and suffix in meta:
            return suffix, meta[suffix]

    populated = [
        (sheet_id, sheet_meta)
        for sheet_id, sheet_meta in meta.items()
        if sheet_meta.get("cellBlockMetas")
    ]
    if len(populated) == 1:
        return populated[0]
    if populated:
        return populated[0]
    return None, None



def _merge_sheet_snapshot_blocks(cv_data: dict, extra_blocks: dict) -> dict:
    """Merge lazy-loaded ``/sheet/block`` payloads into a client_vars snapshot."""
    if not extra_blocks:
        return cv_data

    snapshot = cv_data.get("snapshot")
    if not isinstance(snapshot, dict):
        return cv_data

    import copy

    merged = copy.deepcopy(cv_data)
    merged_snapshot = merged.setdefault("snapshot", {})
    blocks = dict(merged_snapshot.get("blocks") or {})
    before = len(blocks)
    blocks.update(extra_blocks)
    merged_snapshot["blocks"] = blocks
    if len(blocks) != before:
        logger.info(
            f"[Feishu] Merged lazy sheet blocks: +{len(blocks) - before} "
            f"(total {len(blocks)})"
        )
    return merged


def _render_sheet_markdown(rows_data: List[List[str]]) -> Optional[str]:
    """Render a 2D string grid into a GFM table."""
    while rows_data and all(not c.strip() for c in rows_data[-1]):
        rows_data.pop()
    if not rows_data:
        return None

    max_cols = max((len(row) for row in rows_data), default=0)
    if max_cols < 1:
        return None

    normalized_rows: List[List[str]] = []
    for row in rows_data:
        cells = list(row)
        while len(cells) < max_cols:
            cells.append("")
        normalized_rows.append(cells)

    header = [
        c.replace("|", "\\|").replace("\n", " ")
        for c in normalized_rows[0]
    ]
    lines: List[str] = []
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in normalized_rows[1:]:
        cells = [
            c.replace("|", "\\|").replace("\n", " ") for c in row
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _decode_sheet_client_vars(
    cv_data: dict,
    *,
    allow_sparse_blocks: bool = True,
) -> Optional[str]:
    """Decode a ``client_vars`` snapshot into a GFM Markdown table.

    *cv_data* is the ``data`` field from the ``client_vars`` JSON response,
    containing ``snapshot.gzipBlockMeta`` and ``snapshot.blocks``.

    Returns a GFM table string, or *None* if decoding fails.
    """
    import base64
    import gzip

    try:
        snapshot = cv_data.get("snapshot", {})
        if not snapshot or "gzipBlockMeta" not in snapshot:
            return None

        meta = _json_mod.loads(
            gzip.decompress(base64.b64decode(snapshot["gzipBlockMeta"]))
        )
        blocks_dict = snapshot.get("blocks", {})
        if not blocks_dict:
            return None

        target_sheet_id, sheet_meta = _pick_sheet_meta(meta, cv_data)
        if not sheet_meta:
            return None

        cell_block_metas = sheet_meta.get("cellBlockMetas", [])
        if not cell_block_metas:
            return None

        decoded_blocks: List[Tuple[dict, List[str], int, Optional[List[Optional[int]]]]] = []
        max_row = 0
        max_col = 0
        for cb in sorted(
            cell_block_metas,
            key=lambda item: (
                (item.get("range") or {}).get("rowStart", 0),
                (item.get("range") or {}).get("colStart", 0),
            ),
        ):
            block_id = cb.get("blockId")
            rng = cb.get("range") or {}
            if not block_id or block_id not in blocks_dict:
                continue
            try:
                block_raw = gzip.decompress(base64.b64decode(blocks_dict[block_id]))
            except Exception:
                continue
            strings = _extract_protobuf_cell_strings(block_raw)
            slot_mapping = _extract_sheet_slot_mapping(block_raw)

            row_start = int(rng.get("rowStart", 0) or 0)
            row_end = int(rng.get("rowEnd", row_start) or row_start)
            col_start = int(rng.get("colStart", 0) or 0)
            col_end = int(rng.get("colEnd", col_start) or col_start)
            if row_end <= row_start or col_end <= col_start:
                continue

            slots = max(0, (row_end - row_start) * (col_end - col_start))
            max_row = max(max_row, row_end)
            max_col = max(max_col, col_end)
            decoded_blocks.append((rng, strings, slots, slot_mapping))

        if not decoded_blocks or max_row < 1 or max_col < 1:
            return None

        grid: List[List[str]] = [
            ["" for _ in range(max_col)] for _ in range(max_row)
        ]
        filled_any = False
        usable_blocks = 0

        for rng, strings, slots, slot_mapping in decoded_blocks:
            row_start = int(rng.get("rowStart", 0) or 0)
            row_end = int(rng.get("rowEnd", row_start) or row_start)
            col_start = int(rng.get("colStart", 0) or 0)
            col_end = int(rng.get("colEnd", col_start) or col_start)

            if slots <= 0 or not strings:
                continue

            mapped_slots: Optional[List[Optional[str]]] = None
            if slot_mapping:
                candidates = slot_mapping
                if len(candidates) == slots + 1 and candidates[0] is None:
                    candidates = candidates[1:]
                if len(candidates) == slots:
                    mapped_slots = []
                    for string_idx in candidates:
                        if string_idx is None:
                            mapped_slots.append(None)
                            continue
                        if 0 <= string_idx < len(strings):
                            mapped_slots.append(strings[string_idx])
                        else:
                            mapped_slots.append(None)
                else:
                    logger.debug(
                        f"[Feishu] Ignore sheet slot mapping due to length mismatch: "
                        f"sheet={target_sheet_id or '?'} range=({row_start}:{row_end},{col_start}:{col_end}) "
                        f"mapping={len(candidates)} slots={slots}"
                    )

            available = len([v for v in mapped_slots if v is not None]) if mapped_slots else len(strings)
            missing = max(0, slots - available)
            missing_ratio = (missing / slots) if slots else 0.0
            if missing_ratio > 0.2:
                logger.info(
                    f"[Feishu] Skip sparse sheet block: sheet={target_sheet_id or '?'} "
                    f"range=({row_start}:{row_end},{col_start}:{col_end}) "
                    f"values={available}/{slots}"
                )
                continue

            if missing:
                if not allow_sparse_blocks:
                    logger.info(
                        f"[Feishu] Defer sparse sheet to API fallback: sheet={target_sheet_id or '?'} "
                        f"range=({row_start}:{row_end},{col_start}:{col_end}) "
                        f"values={available}/{slots}"
                    )
                    return None
                logger.info(
                    f"[Feishu] Keep sparse sheet block: sheet={target_sheet_id or '?'} "
                    f"range=({row_start}:{row_end},{col_start}:{col_end}) "
                    f"values={available}/{slots}"
                )

            usable_blocks += 1
            idx = 0
            for row in range(row_start, row_end):
                for col in range(col_start, col_end):
                    value: Optional[str]
                    if mapped_slots is not None:
                        if idx >= len(mapped_slots):
                            break
                        value = mapped_slots[idx]
                        idx += 1
                    else:
                        if idx >= len(strings) or idx >= slots:
                            break
                        value = strings[idx]
                        idx += 1
                    if value:
                        grid[row][col] = value
                        filled_any = True
                if mapped_slots is not None:
                    if idx >= len(mapped_slots):
                        break
                else:
                    if idx >= len(strings) or idx >= slots:
                        break

        if usable_blocks == 0 or not filled_any:
            return None

        rows_data = [row[:] for row in grid]
        while rows_data and all(not c.strip() for c in rows_data[-1]):
            rows_data.pop()
        while rows_data and rows_data[0] and all(not c.strip() for c in rows_data[0]):
            rows_data.pop(0)

        table_md = _render_sheet_markdown(rows_data)
        if not table_md:
            return None

        logger.info(
            f"[Feishu] Decoded sheet protobuf: sheet={target_sheet_id or '?'} "
            f"blocks={len(decoded_blocks)} usable={usable_blocks} rows={len(rows_data)} cols={max_col}"
        )
        return table_md
    except Exception as e:
        logger.debug(f"[Feishu] Sheet protobuf decode error: {e}")
        return None


def _fetch_embedded_sheet(token: str) -> Optional[str]:
    """Try to extract embedded sheet data via Playwright cache or Open API.

    The *token* from the embed block snapshot may be:
      - A plain spreadsheet_token (e.g. ``VWPAsdcIph...``)
      - ``{spreadsheet_token}_{sheetId}`` (underscore-separated)
    We try the full token first, then split at the last ``_`` if that fails.

    Returns a GFM Markdown table string, or None if extraction fails.
    """
    # Check Playwright-extracted cache first (populated by _fetch_via_playwright)
    cache_key = None
    if token in _PLAYWRIGHT_SHEET_CACHE:
        cache_key = token
    else:
        cache_candidates: List[str] = []
        if "_" in token:
            base_token = token.rsplit("_", 1)[0]
            if base_token in _PLAYWRIGHT_SHEET_CACHE:
                cache_key = base_token
            else:
                cache_candidates = [
                    key for key in _PLAYWRIGHT_SHEET_CACHE
                    if key == base_token or key.startswith(f"{base_token}_")
                ]
        else:
            cache_candidates = [
                key for key in _PLAYWRIGHT_SHEET_CACHE
                if key.startswith(f"{token}_")
            ]

        if cache_key is None and len(cache_candidates) == 1:
            cache_key = cache_candidates[0]
            logger.info(
                f"[Feishu] Sheet cache alias hit: {token} -> {cache_key}"
            )
        elif len(cache_candidates) > 1:
            logger.debug(
                f"[Feishu] Sheet cache ambiguous for {token}: {cache_candidates}"
            )
        else:
            logger.debug(f"[Feishu] Sheet cache miss: {token}")

    if cache_key is not None:
        cached_table = _PLAYWRIGHT_SHEET_CACHE[cache_key]
        if cached_table:
            logger.info(f"[Feishu] Sheet from Playwright cache: {cache_key}")
            return cached_table
        logger.info(
            f"[Feishu] Sheet cache deferred to Open API fallback: {cache_key}"
        )

    if not _is_api_available():
        return None

    try:
        import json as _json
        import lark_oapi as lark
        from lark_oapi.api.sheets.v3 import QuerySpreadsheetSheetRequest

        client = _get_lark_client()

        # ---- helpers ----
        def _try_query_sheets(ss_token: str):
            req = (
                QuerySpreadsheetSheetRequest.builder()
                .spreadsheet_token(ss_token)
                .build()
            )
            resp = client.sheets.v3.spreadsheet_sheet.query(req)
            if resp.success() and resp.data:
                return resp.data.sheets or []
            return None

        def _read_values(ss_token: str, range_str: str):
            """Use raw BaseRequest to call sheets v2 values endpoint."""
            raw_req = lark.BaseRequest()
            raw_req.http_method = lark.HttpMethod.GET
            raw_req.uri = (
                "/open-apis/sheets/v2/spreadsheets"
                f"/{ss_token}/values/{range_str}"
            )
            raw_req.token_types = {lark.AccessTokenType.TENANT}
            resp = client.request(raw_req)
            if resp.code != 0:
                logger.debug(f"[Feishu] Sheet values failed: code={resp.code} msg={resp.msg}")
                return None
            # resp.raw.content is JSON bytes
            try:
                data = _json.loads(resp.raw.content)
                vr = data.get("data", {}).get("valueRange", {})
                return vr.get("values")
            except Exception:
                return None

        # ---- Step 1: resolve spreadsheet_token + optional sheetId hint ----
        ss_token = token
        sheet_id_hint = ""
        sheets_list = _try_query_sheets(ss_token)

        if sheets_list is None and "_" in token:
            # Token might be "{ss_token}_{sheetId}" — try splitting
            parts = token.rsplit("_", 1)
            ss_token = parts[0]
            sheet_id_hint = parts[1]
            sheets_list = _try_query_sheets(ss_token)

        if not sheets_list:
            logger.debug(f"[Feishu] Could not query sheets for token={token}")
            return None

        # Pick the target sheet (prefer the hinted sheetId)
        sheet = sheets_list[0]
        if sheet_id_hint:
            for s in sheets_list:
                if getattr(s, "sheet_id", "") == sheet_id_hint:
                    sheet = s
                    break

        sheet_id = getattr(sheet, "sheet_id", "")
        if not sheet_id:
            return None
        row_count = min(getattr(sheet, "row_count", 50) or 50, 200)
        col_count = min(getattr(sheet, "column_count", 10) or 10, 26)
        title = getattr(sheet, "title", "") or ""

        # ---- Step 2: fetch cell values via v2 raw API ----
        end_col = chr(64 + col_count)  # A=65 → col 1
        range_str = f"{sheet_id}!A1:{end_col}{row_count}"
        rows = _read_values(ss_token, range_str)

        if not rows:
            logger.debug("[Feishu] Sheet values empty or API error")
            return None

        # Filter out empty trailing rows
        while rows and all(not str(c or "").strip() for c in (rows[-1] or [])):
            rows.pop()
        if not rows:
            return None

        # ---- Step 3: render as GFM table ----
        lines: List[str] = []
        if title:
            lines.append(f"**{title}**")
            lines.append("")

        header = [str(c or "").replace("|", "\\|") for c in rows[0]]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")

        for row in rows[1:]:
            cells = [
                str(c or "").replace("|", "\\|").replace("\n", " ")
                for c in (row or [])
            ]
            while len(cells) < len(header):
                cells.append("")
            lines.append("| " + " | ".join(cells) + " |")

        logger.info(
            f"[Feishu] Extracted embedded sheet: {len(rows)} rows x {len(header)} cols"
        )
        return "\n".join(lines)

    except ImportError:
        logger.debug("[Feishu] lark-oapi sheets module not available")
        return None
    except Exception as e:
        logger.debug(f"[Feishu] Sheet extraction error: {e}")
        return None


# ---------------------------------------------------------------------------
# Ordered list sequence labels
# ---------------------------------------------------------------------------

def _calc_ordered_label(block, idx: int) -> str:
    """Compute the display label for an ordered list item.

    Feishu ordered blocks carry a ``seq`` field in their snapshot:
    - ``"1"``/``"2"``... → explicit numeric start
    - ``"a"``/``"A"`` → alphabetical
    - ``"i"``/``"I"`` → roman numeral
    - ``"auto"`` → continue from the previous sibling (default numeric)
    """
    seq = _get_ordered_seq(block)

    if not seq or seq == "auto":
        return str(idx + 1)

    # Explicit numeric: "1" means start from 1
    try:
        base = int(seq)
        return str(base + idx)
    except ValueError:
        pass

    # Alphabetical: "a" → a, b, c...  "A" → A, B, C...
    if len(seq) == 1 and seq.isalpha():
        base_ord = ord(seq)
        return chr(base_ord + idx)

    # Roman numeral: "i" or "I"
    if seq.lower() == "i":
        roman = _to_roman(idx + 1)
        return roman.lower() if seq == "i" else roman

    return str(idx + 1)


def _get_ordered_seq(block) -> str:
    """Extract the ``seq`` field from an ordered block."""
    # SDK object
    obj = getattr(block, "ordered", None)
    if obj is not None:
        return getattr(obj, "seq", "") or ""
    # Dict (Playwright snapshot)
    if isinstance(block, dict):
        snap = block.get("snapshot", {})
        if isinstance(snap, dict):
            return snap.get("seq", "") or ""
        return block.get("seq", "") or ""
    return ""


def _to_roman(n: int) -> str:
    """Convert integer to Roman numeral string."""
    vals = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    result = ""
    for val, sym in vals:
        while n >= val:
            result += sym
            n -= val
    return result


# ---------------------------------------------------------------------------
# Children / nested blocks
# ---------------------------------------------------------------------------

def _render_children(
    block, depth: int, images: Optional[List[dict]] = None,
    img_subdir: str = "",
) -> str:
    """Render child blocks (for nested structures like lists, grids, etc.)."""
    children = _get_children(block)
    if not children:
        return ""
    return blocks_to_markdown(children, depth, images, img_subdir,
                              _is_root=False)


def _get_children(block) -> list:
    if hasattr(block, "children"):
        ch = block.children
        if ch:
            return ch
    if isinstance(block, dict):
        return block.get("children", [])
    return []


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

def _render_table(block) -> str:
    """Render a table block as GFM Markdown table."""
    children = _get_children(block)
    if not children:
        return ""

    # Determine row/column counts from the table property
    rows_prop = _get_table_property(block)
    row_count = rows_prop.get("row_size", 0)
    col_count = rows_prop.get("column_size", 0)

    if not row_count or not col_count:
        if row_count and not col_count:
            col_count = max(1, (len(children) + row_count - 1) // row_count)
        elif col_count and not row_count:
            row_count = max(1, (len(children) + col_count - 1) // col_count)
        else:
            # Final fallback: preserve the previous heuristic instead of
            # guessing a multi-row layout without explicit snapshot metadata.
            col_count = len(children)
            row_count = 1

    # Build cell texts row by row
    rows: List[List[str]] = []
    for i in range(row_count):
        row_cells: List[str] = []
        for j in range(col_count):
            idx = i * col_count + j
            if idx < len(children):
                row_cells.append(_render_table_cell(children[idx]))
            else:
                row_cells.append("")
        rows.append(row_cells)

    if not rows:
        return ""

    # Format as GFM table
    lines: List[str] = []
    header = rows[0]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _render_table_cell(block) -> str:
    cell_text = _elements_text(block).strip()
    if not cell_text:
        cell_children = _get_children(block)
        if cell_children:
            cell_text = blocks_to_markdown(cell_children, _is_root=False).strip()
    return cell_text.replace("|", "\\|").replace("\n", "<br>")


def _get_table_property(block) -> dict:
    obj = getattr(block, "table", None)
    if obj is not None:
        return {
            "row_size": getattr(obj, "row_size", 0) or 0,
            "column_size": getattr(obj, "column_size", 0) or 0,
        }
    if isinstance(block, dict) and "table" in block:
        t = block["table"]
        snapshot = block.get("snapshot", {}) if isinstance(block.get("snapshot"), dict) else {}
        row_size = t.get("row_size", 0) or len(snapshot.get("rows_id") or [])
        column_size = t.get("column_size", 0) or len(snapshot.get("columns_id") or [])
        return {
            "row_size": row_size,
            "column_size": column_size,
        }
    if isinstance(block, dict):
        snapshot = block.get("snapshot", {})
        if isinstance(snapshot, dict):
            return {
                "row_size": len(snapshot.get("rows_id") or []),
                "column_size": len(snapshot.get("columns_id") or []),
            }
    return {}


# ---------------------------------------------------------------------------
# Tier 2: Playwright + window.PageMain (CDP direct → launch fallback)
# ---------------------------------------------------------------------------

async def _fetch_via_playwright(url: str) -> Dict[str, Any]:
    """Tier 2 – Extract document content via browser editor memory."""
    from feedgrab.fetchers.browser import (
        evaluate_feishu_doc,
        get_session_path,
    )

    session_path = get_session_path("feishu")
    from feedgrab.config import feishu_cdp_enabled
    if not Path(session_path).exists() and not feishu_cdp_enabled():
        raise RuntimeError(
            "未找到飞书登录态。请运行：feedgrab login feishu"
        )

    data = await evaluate_feishu_doc(url, session_path)
    if not data or data.get("error"):
        err = data.get("error", "未知错误") if data else "没有返回数据"
        raise RuntimeError(f"Playwright 提取失败：{err}")

    # Pre-extract embedded sheet data from intercepted client_vars responses.
    # Populate the module-level cache so _render_embedded_block() can use it.
    _PLAYWRIGHT_SHEET_CACHE.clear()
    prefer_api_for_sparse = _is_api_available()
    extra_sheet_blocks = data.get("sheet_blocks") or {}
    for token_key, cv_data in (data.get("sheet_client_vars") or {}).items():
        try:
            merged_cv_data = _merge_sheet_snapshot_blocks(
                cv_data, extra_sheet_blocks
            )
            table_md = _decode_sheet_client_vars(
                merged_cv_data,
                allow_sparse_blocks=not prefer_api_for_sparse,
            )
            if table_md:
                _PLAYWRIGHT_SHEET_CACHE[token_key] = table_md
                logger.info(f"[Feishu] Pre-decoded sheet: {token_key}")
            else:
                _PLAYWRIGHT_SHEET_CACHE[token_key] = ""
                logger.info(f"[Feishu] Sheet decode deferred to fallback: {token_key}")
        except Exception as e:
            _PLAYWRIGHT_SHEET_CACHE[token_key] = ""
            logger.debug(f"[Feishu] Sheet decode failed for {token_key}: {e}")

    # Convert editor block tree to Markdown
    images_list: List[dict] = []
    _img_subdir = hashlib.md5(url.encode()).hexdigest()[:12]
    block_tree = data.get("blockTree")
    if block_tree:
        children = block_tree.get("children", [])
        content = blocks_to_markdown(children, images=images_list,
                                     img_subdir=_img_subdir)
    else:
        content = data.get("content", "")

    # Clear cache after conversion
    _PLAYWRIGHT_SHEET_CACHE.clear()

    # Populate pre-downloaded image bytes from browser session
    pre_bytes = data.get("_image_bytes", {})
    if pre_bytes:
        for img_info in images_list:
            tk = img_info.get("token", "")
            if tk and tk in pre_bytes:
                img_info["_bytes"] = pre_bytes[tk]
        # Free the large dict from data to reduce memory
        del data["_image_bytes"]

    # Title: clean zero-width chars + fallback to first heading in content
    title = _clean_feishu_title(data.get("title", ""))
    if not title or title in ("Docs", "飞书云文档", "Feishu Docs", "Lark Docs"):
        title = _extract_title_from_content(content) or title

    # Download images if enabled (Playwright tier uses internal CDN URL)
    # Images are downloaded later by reader.py after save_to_markdown()

    parsed = parse_feishu_url(url) or {}
    return {
        "title": title,
        "content": content,
        "url": url,
        "author": data.get("author", ""),
        "doc_type": parsed.get("type", ""),
        "doc_token": parsed.get("token", ""),
        "images": [img.get("token", "") for img in images_list],
        "images_info": images_list,
        "img_subdir": _img_subdir,
        "tags": [],
    }


# ---------------------------------------------------------------------------
# Tier 1.5: Internal export API (docx → Markdown)
# ---------------------------------------------------------------------------

# JS to call Feishu internal export API from browser context
_EXPORT_CREATE_JS = r"""
async (args) => {
  const { token, type, origin } = args;
  const csrfToken = (document.cookie.match(/_csrf_token=([^;]+)/) || [])[1] || '';
  const requestId = 'feedgrab_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
  const headers = {
    'Content-Type': 'application/json',
    'X-CSRFToken': csrfToken,
    'Request-Id': requestId,
    'X-Request-Id': requestId,
    'doc-biz': 'Lark',
    'doc-platform': 'web',
    'x-lgw-biz': 'Lark',
    'x-lgw-platform': 'web'
  };

  // Step 1: Create export task
  const createResp = await fetch(`${origin}/space/api/export/create/`, {
    method: 'POST',
    credentials: 'include',
    headers: headers,
    body: JSON.stringify({ token: token, type: type, file_extension: 'docx' })
  });
  const createData = await createResp.json();
  if (createData.code !== 0) return { error: 'export create failed: ' + JSON.stringify(createData) };
  const ticket = createData.data.ticket;

  // Step 2: Poll for completion (max 60 attempts)
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 1000));
    const resultResp = await fetch(
      `${origin}/space/api/export/result/${ticket}?token=${token}&type=${type}`,
      { credentials: 'include', headers: headers }
    );
    const resultData = await resultResp.json();
    if (resultData.data && resultData.data.job_status === 0) {
      return {
        file_token: resultData.data.file_token,
        file_name: resultData.data.file_name || 'export.docx'
      };
    }
    if (resultData.data && resultData.data.job_status === 2) {
      return { error: 'export job failed' };
    }
  }
  return { error: 'export timeout' };
}
"""


async def _fetch_via_export_api(url: str, session_path: str) -> Dict[str, Any]:
    """Tier 1.5 – Export document as docx via internal API, convert to Markdown.

    Requires browser session (same as Tier 1). Borrows the internal export
    endpoints discovered in feishuToMarkdown Chrome extension.
    """
    from urllib.parse import urlparse

    parsed_url = urlparse(url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    parsed_doc = parse_feishu_url(url) or {}
    token = parsed_doc.get("token", "")
    doc_type = parsed_doc.get("type", "docx")

    # Map doc_type to export API type parameter
    export_type_map = {"wiki": "wiki", "docx": "docx", "docs": "doc"}
    export_type = export_type_map.get(doc_type, "docx")

    # Launch browser, navigate to feishu to establish session, call export API
    try:
        from playwright.async_api import async_playwright as _pw_factory
    except ImportError:
        from feedgrab.fetchers.browser import get_async_playwright
        _pw_factory = get_async_playwright()

    pw = await _pw_factory().start()
    browser = None
    docx_bytes = None
    page_title = ""
    try:
        from feedgrab.fetchers.browser import get_stealth_context_options
        browser = await pw.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx_opts = get_stealth_context_options(storage_state=session_path)
        context = await browser.new_context(**ctx_opts)
        page = await context.new_page()

        # Navigate to document to establish session context
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        page_title = _clean_feishu_title(await page.title())

        # Call export API via browser JS
        export_result = await page.evaluate(
            _EXPORT_CREATE_JS,
            {"token": token, "type": export_type, "origin": origin},
        )

        if not export_result or export_result.get("error"):
            err = export_result.get("error", "unknown") if export_result else "no data"
            raise RuntimeError(f"Export API: {err}")

        file_token = export_result["file_token"]

        # Step 3: Download the docx file
        download_url = f"{origin}/space/api/box/stream/download/all/{file_token}/"
        resp = await page.request.get(download_url)
        if resp.status != 200:
            raise RuntimeError(f"Docx download failed: status={resp.status}")
        docx_bytes = await resp.body()

    finally:
        if browser:
            await browser.close()
        await pw.stop()

    if not docx_bytes or len(docx_bytes) < 100:
        raise RuntimeError("Export API returned empty docx")

    # Parse docx → Markdown
    content = _docx_to_markdown(docx_bytes)
    title = page_title or _extract_title_from_content(content) or "Untitled"

    return {
        "title": title,
        "content": content,
        "url": url,
        "author": "",
        "doc_type": doc_type,
        "doc_token": token,
        "images": [],
        "tags": [],
    }


def _docx_to_markdown(docx_bytes: bytes) -> str:
    """Convert docx bytes to Markdown text.

    Uses markdownify + BeautifulSoup if available, otherwise zipfile text
    extraction fallback.
    """
    import io
    import zipfile

    # Try mammoth first (best docx→html conversion)
    try:
        import mammoth
        result = mammoth.convert_to_html(io.BytesIO(docx_bytes))
        html = result.value
        # mammoth → html, now convert to markdown
        try:
            from markdownify import markdownify as md
            return md(html, heading_style="ATX", bullets="-").strip()
        except ImportError:
            # Basic HTML tag stripping
            return _basic_html_to_md(html)
    except ImportError:
        pass

    # Fallback: try python-docx for paragraph text extraction
    try:
        import docx
        doc = docx.Document(io.BytesIO(docx_bytes))
        parts = []
        for para in doc.paragraphs:
            style = para.style.name if para.style else ""
            text = para.text.strip()
            if not text:
                continue
            if "Heading 1" in style:
                parts.append(f"# {text}")
            elif "Heading 2" in style:
                parts.append(f"## {text}")
            elif "Heading 3" in style:
                parts.append(f"### {text}")
            elif "List" in style:
                parts.append(f"- {text}")
            else:
                parts.append(text)
        return "\n\n".join(parts)
    except ImportError:
        pass

    # Last resort: extract raw text from docx XML
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        if "word/document.xml" in zf.namelist():
            xml = zf.read("word/document.xml").decode("utf-8")
            # Rough text extraction from XML
            text = re.sub(r"<[^>]+>", "\n", xml)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text.strip()

    return ""


def _basic_html_to_md(html: str) -> str:
    """Very basic HTML to Markdown for when markdownify is unavailable."""
    # Headings
    for i in range(6, 0, -1):
        html = re.sub(
            rf"<h{i}[^>]*>(.*?)</h{i}>",
            lambda m, lv=i: f"{'#' * lv} {m.group(1)}\n\n",
            html, flags=re.DOTALL,
        )
    # Bold, italic
    html = re.sub(r"<strong>(.*?)</strong>", r"**\1**", html)
    html = re.sub(r"<em>(.*?)</em>", r"*\1*", html)
    # Links
    html = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", html)
    # Paragraphs and line breaks
    html = re.sub(r"<p[^>]*>", "\n\n", html)
    html = re.sub(r"</p>", "", html)
    html = re.sub(r"<br\s*/?>", "\n", html)
    # Strip remaining tags
    html = re.sub(r"<[^>]+>", "", html)
    return html.strip()


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

def download_feishu_images(
    md_path: str, images_info: List[dict], doc_url: str,
    img_subdir: str = "",
) -> None:
    """Download images to ``{md_dir}/attachments/{img_subdir}/`` after Markdown is saved.

    Tries Open API download first, then CDN fallback.
    Content already contains ``![alt](attachments/{img_subdir}/xxx.png)`` relative paths.
    """
    if not images_info:
        return

    md_dir = Path(md_path).parent
    att_dir = md_dir / "attachments"
    if img_subdir:
        att_dir = att_dir / img_subdir
    att_dir.mkdir(parents=True, exist_ok=True)

    # --- Strategy 1: Open API download ---
    if _is_api_available():
        _download_images_via_api(att_dir, images_info)
        return

    # --- Strategy 2: CDN download (browser session) ---
    _download_images_via_cdn(att_dir, images_info, doc_url)


def _sanitize_filename(name: str) -> str:
    """Sanitize a filename by replacing chars that break Markdown link syntax."""
    import re as _re
    # Split extension
    if '.' in name:
        stem, ext = name.rsplit('.', 1)
        ext = '.' + ext
    else:
        stem, ext = name, ''
    # Replace spaces with hyphens, problematic chars with underscores
    stem = stem.replace(' ', '-')
    stem = _re.sub(r'[()@#%\[\]{}|<>!]', '_', stem)
    # Collapse consecutive hyphens/underscores
    stem = _re.sub(r'[-_]{2,}', '-', stem)
    stem = stem.strip('-_ ')
    return (stem + ext) if stem else name


def _image_filename(info: dict, idx: int) -> str:
    """Build a filename for an image, e.g. ``001_screenshot.png``."""
    name = info.get("name", "")
    if name:
        return f"{idx:03d}_{_sanitize_filename(name)}"
    mime = info.get("mime_type", "")
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
           "image/webp": ".webp", "image/svg+xml": ".svg"}.get(mime, ".png")
    return f"{idx:03d}_image{ext}"


def _download_images_via_api(att_dir: Path, images: List[dict]) -> None:
    """Download images via Open API to *att_dir*."""
    import lark_oapi as lark
    from lark_oapi.api.drive.v1 import DownloadMediaRequest

    client = _get_lark_client()

    for idx, info in enumerate(images, 1):
        token = info.get("token", "")
        if not token:
            continue

        fname = info.get("_filename") or _image_filename(info, idx)
        fpath = att_dir / fname
        if fpath.exists() and fpath.stat().st_size > 0:
            logger.info(f"[Feishu] Image {idx} already exists: {fname}")
            continue

        try:
            req = DownloadMediaRequest.builder().file_token(token).build()
            resp = client.drive.v1.media.download(req)
            if resp.success():
                fpath.write_bytes(resp.file.read())
                logger.info(f"[Feishu] Downloaded image {idx}: {fname}")
            else:
                logger.warning(
                    f"[Feishu] Image download failed {token}: {resp.msg}"
                )
        except Exception as e:
            logger.warning(f"[Feishu] Image download error {token}: {e}")


def _download_images_via_cdn(
    att_dir: Path, images: List[dict], doc_url: str,
) -> None:
    """Download images via Feishu internal CDN to *att_dir*.

    Uses the same origin as the document URL to build download URLs.
    Requires browser session cookies for authentication.
    """
    from urllib.parse import urlparse

    parsed = urlparse(doc_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    # Load cookies from session for authenticated requests
    session_path = Path(get_session_dir()) / "feishu.json"
    cookies = {}
    if session_path.exists():
        import json
        try:
            state = json.loads(session_path.read_text(encoding="utf-8"))
            for c in state.get("cookies", []):
                cookies[c["name"]] = c["value"]
        except Exception:
            pass

    if not cookies:
        logger.warning("[Feishu] No session cookies for image download")
        return

    # Build authenticated headers — Feishu CDN requires CSRF + browser-like context
    csrf_token = cookies.get("_csrf_token", "")
    headers = {
        "Referer": doc_url,
        "Origin": origin,
        "X-CSRFToken": csrf_token,
        "X-Request-Id": f"feedgrab_img_{int(__import__('time').time())}",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    }

    from feedgrab.utils.http_client import get as http_get

    for idx, info in enumerate(images, 1):
        token = info.get("token", "")
        if not token:
            continue

        fname = info.get("_filename") or _image_filename(info, idx)
        fpath = att_dir / fname
        if fpath.exists() and fpath.stat().st_size > 0:
            logger.info(f"[Feishu] Image {idx} already exists: {fname}")
            continue

        # Priority 1: pre-downloaded bytes from browser session
        pre_bytes = info.pop("_bytes", None)
        if pre_bytes:
            fpath.write_bytes(pre_bytes)
            logger.info(
                f"[Feishu] Wrote pre-downloaded image {idx}: {fname}"
            )
            continue

        # Priority 2: CDN download with authenticated headers
        cdn_url = (
            f"{origin}/space/api/box/stream/download/all/{token}/"
        )
        try:
            resp = http_get(
                cdn_url,
                cookies=cookies,
                timeout=30,
                headers=headers,
            )
            if resp.status_code == 200 and len(resp.content) > 100:
                fpath.write_bytes(resp.content)
                logger.info(f"[Feishu] Downloaded image {idx}: {fname}")
            else:
                logger.warning(
                    f"[Feishu] CDN image download failed {token}: "
                    f"status={resp.status_code}"
                )
        except Exception as e:
            logger.warning(f"[Feishu] CDN image download error {token}: {e}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fetch_feishu(url: str) -> Dict[str, Any]:
    """Fetch a Feishu document with multi-tier fallback.

    Tier 0: Open API (needs APP_ID + APP_SECRET)
    Tier 1: CDP direct connect (needs Chrome --remote-debugging-port)
    Tier 2: Launch new browser (needs sessions/feishu.json)
    Tier 3: Internal export API (docx → Markdown)
    Tier 4: Jina Reader (zero-config)

    Returns a dict suitable for ``schema.from_feishu()``.
    """
    parsed = parse_feishu_url(url)
    if not parsed:
        raise ValueError(f"无法解析飞书 URL：{url}")

    # -- Tier 0: Open API --------------------------------------------------
    if _is_api_available():
        try:
            logger.info("[Feishu] Tier 0: Open API")
            return _fetch_via_api(url, parsed)
        except Exception as e:
            logger.warning(f"[Feishu] Tier 0 failed ({e}), falling back")

    # -- Tier 1/2: Playwright (CDP direct → launch fallback) ---------------
    from feedgrab.config import feishu_cdp_enabled
    session_path = str(Path(get_session_dir()) / "feishu.json")
    has_session = Path(session_path).exists()
    if has_session or feishu_cdp_enabled():
        try:
            tier_label = "Tier 1: CDP direct" if feishu_cdp_enabled() else "Tier 2: Playwright"
            logger.info(f"[Feishu] {tier_label} + PageMain")
            return await _fetch_via_playwright(url)
        except Exception as e:
            logger.warning(f"[Feishu] Playwright failed ({e}), trying export API")

        # -- Tier 3: Internal export API (docx → Markdown) -----------------
        if has_session:
            try:
                logger.info("[Feishu] Tier 3: Internal export API (docx)")
                return await _fetch_via_export_api(url, session_path)
            except Exception as e:
                logger.warning(f"[Feishu] Tier 3 failed ({e}), falling back")

    # -- Tier 4: Jina Reader -----------------------------------------------
    try:
        logger.info("[Feishu] Tier 4: Jina Reader")
        from feedgrab.fetchers.jina import fetch_via_jina

        data = fetch_via_jina(url)
        content = data.get("content", "")
        # Detect login/block page
        if not content or len(content) < 50:
            raise RuntimeError("Jina returned empty or login page")
        return {
            "title": data.get("title", ""),
            "content": content,
            "url": url,
            "author": data.get("author", ""),
            "doc_type": parsed.get("type", ""),
            "doc_token": parsed.get("token", ""),
            "images": [],
            "tags": [],
        }
    except Exception as e:
        logger.warning(f"[Feishu] Tier 4 Jina failed ({e})")

    raise RuntimeError(
        f"飞书文档抓取所有方式都失败：{url}。"
        "可选处理：1) 配置 FEISHU_APP_ID + FEISHU_APP_SECRET 使用 API；"
        "2) 设置 FEISHU_CDP_ENABLED=true 并启动带 --remote-debugging-port 的 Chrome；"
        "3) 运行 'feedgrab login feishu' 保存浏览器登录态；"
        "4) 确认文档公开可访问，以便 Jina 兜底。"
    )
