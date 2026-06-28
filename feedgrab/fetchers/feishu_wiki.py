# -*- coding: utf-8 -*-
"""
Feishu Wiki batch fetcher – recursively fetch all documents in a wiki space.

Tier 0: Open API – wiki/v2 node traversal + docx blocks per document
Tier 1: Playwright – sidebar DOM parsing + per-page PageMain extraction
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from feedgrab.config import (
    feishu_app_id,
    feishu_app_secret,
    feishu_download_images,
    feishu_page_load_timeout,
    feishu_wiki_batch_enabled,
    feishu_wiki_delay,
    feishu_wiki_since,
    get_data_dir,
)
from feedgrab.fetchers.feishu import (
    _clean_feishu_title,
    _decode_sheet_client_vars,
    _merge_sheet_snapshot_blocks,
    _fetch_document_blocks,
    _get_lark_client,
    _is_api_available,
    _PLAYWRIGHT_SHEET_CACHE,
    _resolve_wiki_node,
    blocks_to_markdown,
    download_feishu_images,
    parse_feishu_url,
)
from feedgrab.utils.dedup import add_item, has_item, load_index
from feedgrab.utils.storage import save_to_markdown
from feedgrab.schema import from_feishu

logger = logging.getLogger(__name__)

_FEISHU_WIKI_TOKEN_RE = re.compile(r"(?:^|[?&])wikiToken=([A-Za-z0-9]+)")
_FEISHU_WIKI_URL_RE = re.compile(r"/wiki/([A-Za-z0-9]+)")


# ---------------------------------------------------------------------------
# Progress / checkpoint
# ---------------------------------------------------------------------------

def _progress_path(token: str) -> Path:
    return Path(get_data_dir()) / f"_progress_feishu_wiki_{token}.json"


def _load_progress(token: str) -> dict:
    p = _progress_path(token)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done": []}


def _save_progress(token: str, progress: dict):
    p = _progress_path(token)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")


def _clear_progress(token: str):
    p = _progress_path(token)
    if p.exists():
        p.unlink()


def _extract_wiki_token(node_uid: str = "", url: str = "") -> str:
    """Extract a Feishu wiki token from a tree-node uid or wiki URL."""
    if node_uid:
        match = _FEISHU_WIKI_TOKEN_RE.search(node_uid)
        if match:
            return match.group(1)
    if url:
        match = _FEISHU_WIKI_URL_RE.search(url)
        if match:
            return match.group(1)
    return ""


def _normalize_sidebar_nodes(
    raw_nodes: List[Dict[str, Any]],
    base_url: str,
) -> List[Dict[str, str]]:
    """Normalize sidebar DOM records into ordered wiki links."""
    base_url = base_url.rstrip("/")
    links: List[Dict[str, str]] = []
    seen: set[str] = set()

    for raw in raw_nodes or []:
        node_uid = str(raw.get("node_uid") or raw.get("uid") or "").strip()
        raw_url = str(raw.get("url") or "").strip()
        token = str(raw.get("token") or "").strip() or _extract_wiki_token(
            node_uid=node_uid,
            url=raw_url,
        )
        if not token or token in seen:
            continue

        title = _clean_feishu_title(
            str(raw.get("title") or raw.get("text") or raw.get("label") or "")
        )
        title = title or token
        url = raw_url
        if url.startswith("/"):
            url = f"{base_url}{url}"
        if not url:
            url = f"{base_url}/wiki/{token}"

        seen.add(token)
        links.append({
            "title": title,
            "url": url,
            "token": token,
        })

    return links


# ---------------------------------------------------------------------------
# Tier 0: Open API wiki node traversal
# ---------------------------------------------------------------------------

def _list_wiki_children(space_id: str, parent_node_token: str) -> List[dict]:
    """List all child nodes under a parent in a wiki space via Open API."""
    from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

    client = _get_lark_client()
    all_nodes: List[dict] = []
    page_token: Optional[str] = None

    while True:
        builder = (
            ListSpaceNodeRequest.builder()
            .space_id(space_id)
            .parent_node_token(parent_node_token)
            .page_size(50)
        )
        if page_token:
            builder = builder.page_token(page_token)
        req = builder.build()
        resp = client.wiki.v2.space_node.list(req)

        if not resp.success():
            raise RuntimeError(
                f"wiki list_nodes failed: code={resp.code} msg={resp.msg}"
            )
        if resp.data and resp.data.items:
            for node in resp.data.items:
                all_nodes.append({
                    "node_token": node.node_token or "",
                    "obj_token": node.obj_token or "",
                    "obj_type": node.obj_type or "",
                    "title": node.title or "",
                    "has_child": getattr(node, "has_child", False),
                    "obj_create_time": getattr(node, "obj_create_time", "") or "",
                    "obj_edit_time": getattr(node, "obj_edit_time", "") or "",
                })
        if resp.data and resp.data.has_more:
            page_token = resp.data.page_token
        else:
            break

    return all_nodes


def _collect_all_nodes(
    space_id: str,
    root_token: str,
    since: str = "",
    depth: int = 0,
) -> List[dict]:
    """Recursively collect all document nodes in a wiki tree."""
    if depth > 20:
        return []

    children = _list_wiki_children(space_id, root_token)
    result: List[dict] = []

    for node in children:
        # Apply date filter if configured
        if since and node.get("obj_edit_time"):
            try:
                edit_ts = int(node["obj_edit_time"])
                since_ts = int(datetime.strptime(since, "%Y-%m-%d").timestamp())
                if edit_ts < since_ts:
                    continue
            except (ValueError, TypeError):
                pass

        node["depth"] = depth
        result.append(node)

        if node.get("has_child"):
            sub = _collect_all_nodes(
                space_id, node["node_token"], since, depth + 1
            )
            result.extend(sub)

    return result


async def _fetch_wiki_via_api(
    url: str,
    root_token: str,
) -> Dict[str, Any]:
    """Tier 0 – Batch fetch entire wiki tree via Open API."""
    # Resolve root node to get space_id
    root_info = _resolve_wiki_node(root_token)
    space_id = root_info["space_id"]
    wiki_title = root_info.get("title", root_token)

    if not space_id:
        raise RuntimeError("Cannot determine space_id from wiki root node")

    # Collect all nodes recursively
    since = feishu_wiki_since()
    print(f"📂 Scanning wiki tree: {wiki_title}")
    all_nodes = _collect_all_nodes(space_id, root_token, since)
    doc_nodes = [n for n in all_nodes if n["obj_type"] in ("docx", "doc")]
    print(f"📄 Found {len(doc_nodes)} documents ({len(all_nodes)} total nodes)")

    if not doc_nodes:
        return {
            "wiki_title": wiki_title,
            "total": 0,
            "fetched": 0,
            "skipped": 0,
            "failed": 0,
            "docs": [],
        }

    # Load dedup index + progress
    dedup_idx = load_index("Feishu")
    progress = _load_progress(root_token)
    done_set = set(progress.get("done", []))
    delay = feishu_wiki_delay()

    fetched = 0
    skipped = 0
    failed = 0

    for i, node in enumerate(doc_nodes, 1):
        node_token = node["node_token"]
        obj_token = node["obj_token"]
        title = node.get("title", obj_token)

        # Skip if already done in this run
        if node_token in done_set:
            skipped += 1
            continue

        # Skip if already in dedup index
        item_id = hashlib.md5(node_token.encode()).hexdigest()[:12]
        if has_item(item_id, dedup_idx):
            skipped += 1
            done_set.add(node_token)
            continue

        print(f"  [{i}/{len(doc_nodes)}] {title}")

        try:
            doc_url = url.rsplit("/wiki/", 1)[0] + f"/wiki/{node_token}"
            _img_subdir = hashlib.md5(doc_url.encode()).hexdigest()[:12]

            doc_title, blocks = _fetch_document_blocks(obj_token)
            images_list: List[dict] = []
            content = blocks_to_markdown(blocks, images=images_list,
                                         img_subdir=_img_subdir)

            data = {
                "title": title or doc_title,
                "content": content,
                "url": doc_url,
                "author": "",
                "doc_type": node["obj_type"],
                "doc_token": obj_token,
                "images": [img.get("token", "") for img in images_list],
                "images_info": images_list,
                "img_subdir": _img_subdir,
                "tags": [],
            }

            # Save via standard pipeline
            uc = from_feishu(data)
            uc.category = wiki_title
            saved_path = save_to_markdown(uc)
            add_item(item_id, data["url"], dedup_idx)

            # Download images if enabled
            if saved_path and images_list and feishu_download_images():
                download_feishu_images(saved_path, images_list, doc_url,
                                       img_subdir=_img_subdir)

            fetched += 1
            done_set.add(node_token)

            # Save progress periodically
            if fetched % 5 == 0:
                progress["done"] = list(done_set)
                _save_progress(root_token, progress)

        except Exception as e:
            logger.warning(f"[Feishu Wiki] Failed to fetch {title}: {e}")
            failed += 1

        if i < len(doc_nodes):
            time.sleep(delay)

    # Cleanup progress file on success
    if not failed:
        _clear_progress(root_token)
    else:
        progress["done"] = list(done_set)
        _save_progress(root_token, progress)

    return {
        "wiki_title": wiki_title,
        "total": len(doc_nodes),
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Tier 1: Playwright sidebar extraction
# ---------------------------------------------------------------------------

FEISHU_WIKI_SIDEBAR_JS = """
(
  async () => {
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    const getTreeRoot = () =>
      document.querySelector('#TOC-ROOT')
      || document.querySelector('.wiki-tree-inner-container[role="list"]')
      || document.querySelector('.wiki-space-detail-directory-tree [role="list"]');

    const getScroller = () =>
      document.querySelector('.wiki-space-detail-directory-tree-wrapper .workspace-scroll-area')
      || document.querySelector('.sidebar-styled__ScrollableContainer-czoANO')
      || document.querySelector('.workspace-scroll-area');

    const ensureTreeVisible = async () => {
      let treeRoot = getTreeRoot();
      if (treeRoot) return treeRoot;

      const directoryToggle = document.querySelector(
        '[data-e2e="wiki-space-detail-tree-expand-btn"][role="button"]'
      );
      if (directoryToggle) {
        directoryToggle.dispatchEvent(
          new MouseEvent('click', { bubbles: true, cancelable: true })
        );
        await sleep(400);
      }
      return getTreeRoot();
    };

    const extractVisibleNodes = () => {
      const root = getTreeRoot();
      if (!root) return [];

      return Array.from(
        root.querySelectorAll('.workspace-tree-view-node[data-node-uid]')
      ).map((node) => {
        const textEl =
          node.querySelector('.workspace-tree-view-node-content')
          || node.querySelector('[role="button"]');
        const arrow = node.querySelector('.workspace-tree-view-node-expand-arrow');
        return {
          text: (textEl?.textContent || node.textContent || '').trim(),
          node_uid: node.getAttribute('data-node-uid') || '',
          level: node.getAttribute('data-node-level') || '',
          indent: node.getAttribute('data-node-indent') || '',
          expandable: !!(
            arrow
            && arrow.className.includes('workspace-tree-view-node-expand-arrow--has-icon')
          ),
          expanded: node.classList.contains('workspace-tree-view-node--expanded'),
        };
      });
    };

    const visibleCollapsedArrows = () =>
      Array.from(
        document.querySelectorAll(
          '#TOC-ROOT .workspace-tree-view-node-expand-arrow--collapsed.workspace-tree-view-node-expand-arrow--has-icon, '
          + '.wiki-tree-inner-container .workspace-tree-view-node-expand-arrow--collapsed.workspace-tree-view-node-expand-arrow--has-icon'
        )
      );

    const seen = new Map();
    const remember = (nodes) => {
      for (const node of nodes) {
        if (!node?.node_uid || !node?.text) continue;
        if (!seen.has(node.node_uid)) {
          seen.set(node.node_uid, node);
        }
      }
    };

    const scrollAndCollectTree = async () => {
      const treeRoot = await ensureTreeVisible();
      if (!treeRoot) return false;

      const scroller = getScroller();
      if (!scroller) {
        remember(extractVisibleNodes());
        return seen.size > 0;
      }

      for (let pass = 0; pass < 8; pass++) {
        scroller.scrollTop = 0;
        scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
        await sleep(250);

        let lastSignature = '';
        let stableSteps = 0;

        for (let step = 0; step < 160; step++) {
          remember(extractVisibleNodes());

          const arrows = visibleCollapsedArrows();
          let expandedThisStep = false;
          for (const arrow of arrows) {
            arrow.dispatchEvent(
              new MouseEvent('click', { bubbles: true, cancelable: true })
            );
            expandedThisStep = true;
            await sleep(220);
            remember(extractVisibleNodes());
          }

          const maxScrollTop = Math.max(
            0,
            scroller.scrollHeight - scroller.clientHeight
          );
          const signature = `${seen.size}:${visibleCollapsedArrows().length}:${scroller.scrollTop}:${maxScrollTop}`;
          if (!expandedThisStep && signature === lastSignature) {
            stableSteps += 1;
          } else {
            stableSteps = 0;
          }
          lastSignature = signature;

          if (scroller.scrollTop >= maxScrollTop - 2) {
            if (!expandedThisStep || stableSteps >= 2) {
              break;
            }
          } else {
            const stepSize = Math.max(
              200,
              Math.floor(scroller.clientHeight * 0.7)
            );
            scroller.scrollTop = Math.min(maxScrollTop, scroller.scrollTop + stepSize);
            scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
            await sleep(250);
          }
        }

        if (!visibleCollapsedArrows().length) break;
      }

      return seen.size > 0;
    };

    const treeFound = await scrollAndCollectTree();
    if (treeFound) {
      return {
        source: 'tree',
        nodes: Array.from(seen.values()),
      };
    }

    const sidebar = document.querySelector('.wiki-sidebar-tree')
      || document.querySelector('[class*="catalogue"]')
      || document.querySelector('[class*="tree-node"]')?.closest('[class*="sidebar"]')
      || document.querySelector('nav');

    if (!sidebar) return { error: 'Sidebar not found', nodes: [] };

    const anchors = sidebar.querySelectorAll('a[href*="/wiki/"]');
    const fallback = [];
    const fallbackSeen = new Set();
    anchors.forEach((a) => {
      const href = a.href;
      const match = href.match(/\\/wiki\\/([A-Za-z0-9]+)/);
      if (match && !fallbackSeen.has(match[1])) {
        fallbackSeen.add(match[1]);
        fallback.push({
          title: (a.textContent || '').trim(),
          url: href,
          token: match[1],
        });
      }
    });

    return { source: 'anchors', nodes: fallback };
  }
)()
"""


async def _fetch_wiki_via_playwright(
    url: str,
    root_token: str,
) -> Dict[str, Any]:
    """Tier 1/2 – Batch fetch wiki documents via Playwright sidebar scraping.

    Tries CDP direct connect first (zero startup, reuses Chrome login),
    then falls back to launching a new browser instance.
    """
    from feedgrab.fetchers.browser import (
        get_session_path,
        get_stealth_context_options,
        _connect_feishu_cdp,
        _evaluate_feishu_doc_on_page,
    )

    # ── Try CDP direct connect first ─────────────────────────
    pw_cdp, browser_cdp, page_cdp, via_cdp = await _connect_feishu_cdp()
    if via_cdp:
        pw = pw_cdp
        browser = browser_cdp
        page = page_cdp
        skip_warmup = True
        logger.info("[Feishu Wiki] Using CDP direct connect")
    else:
        # ── Fall back to launching new browser ───────────────
        skip_warmup = False

        # Use vanilla playwright — patchright causes ERR_CONNECTION_CLOSED
        try:
            from playwright.async_api import async_playwright as _pw_factory
        except ImportError:
            from feedgrab.fetchers.browser import get_async_playwright
            _pw_factory = get_async_playwright()

        session_path = get_session_path("feishu")
        if not Path(session_path).exists():
            raise RuntimeError("未找到飞书登录态。请运行：feedgrab login feishu")

        pw = await _pw_factory().start()
        browser = await pw.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx_opts = get_stealth_context_options(storage_state=session_path)
        context = await browser.new_context(**ctx_opts)
        page = await context.new_page()

    browser_launched = not via_cdp  # Track for cleanup

    try:

        # Disable copy restrictions (same as evaluate_feishu_doc)
        await page.add_init_script("""
            if (window.copyControl && window.copyControl.enable) {
                window.copyControl.enable();
            }
        """)

        # Sheet data interceptor — captures client_vars + lazy block payloads
        sheet_cv_data: dict = {}
        sheet_block_data: dict = {}

        async def _capture_sheet_response(response):
            try:
                if response.status != 200:
                    return
                if "client_vars" in response.url:
                    body = await response.json()
                    if (
                        body.get("code") == 0
                        and body.get("data", {}).get("snapshot")
                    ):
                        token = body["data"].get("token", "")
                        sheet_id = body["data"].get("sheetId", "")
                        key = f"{token}_{sheet_id}" if sheet_id else token
                        if key:
                            sheet_cv_data[key] = body["data"]
                            logger.info(
                                f"[Feishu Wiki PW] Intercepted sheet data: {key}"
                            )
                    return
                if "/space/api/v3/sheet/block" in response.url:
                    body = await response.json()
                    blocks = body.get("data", {}).get("blocks", {})
                    if blocks:
                        sheet_block_data.update(blocks)
                        logger.info(
                            f"[Feishu Wiki PW] Intercepted sheet blocks: +{len(blocks)} "
                            f"(total {len(sheet_block_data)})"
                        )
            except Exception:
                pass

        page.on("response", _capture_sheet_response)

        # Navigate to wiki root
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)  # Wait for sidebar to render
        try:
            await page.wait_for_function(
                """
                () => !!(
                    document.querySelector('#TOC-ROOT .workspace-tree-view-node[data-node-uid]')
                    || document.querySelector('.wiki-tree-inner-container .workspace-tree-view-node[data-node-uid]')
                    || document.querySelector('a[href*="/wiki/"]')
                )
                """,
                timeout=10000,
            )
        except Exception:
            pass

        # Extract sidebar links
        sidebar_data = await page.evaluate(FEISHU_WIKI_SIDEBAR_JS)
        if not sidebar_data or sidebar_data.get("error"):
            raise RuntimeError(
                f"Sidebar extraction failed: {sidebar_data.get('error', 'unknown')}"
            )

        links = _normalize_sidebar_nodes(
            sidebar_data.get("nodes") or sidebar_data.get("links") or [],
            base_url=url.rsplit("/wiki/", 1)[0],
        )
        wiki_title = _clean_feishu_title(await page.title())
        print(f"📂 Wiki: {wiki_title}")
        print(f"📄 Found {len(links)} pages in sidebar")

        if not links:
            return {
                "wiki_title": wiki_title,
                "total": 0,
                "fetched": 0,
                "skipped": 0,
                "failed": 0,
            }

        # Load dedup + progress
        dedup_idx = load_index("Feishu")
        progress = _load_progress(root_token)
        done_set = set(progress.get("done", []))
        delay = feishu_wiki_delay()

        fetched = 0
        skipped = 0
        failed = 0

        for i, link in enumerate(links, 1):
            token = link["token"]
            title = link.get("title", token)
            link_url = link["url"]

            if token in done_set:
                skipped += 1
                continue

            item_id = hashlib.md5(token.encode()).hexdigest()[:12]
            if has_item(item_id, dedup_idx):
                skipped += 1
                done_set.add(token)
                continue

            print(f"  [{i}/{len(links)}] {title}")

            try:
                doc_page = await page.context.new_page()
                try:
                    data = await _evaluate_feishu_doc_on_page(
                        link_url,
                        doc_page,
                        skip_warmup=True,
                    )
                finally:
                    try:
                        await doc_page.close()
                    except Exception:
                        pass

                if not data or data.get("error"):
                    raise RuntimeError(data.get("error", "extraction failed"))

                # Populate sheet cache for blocks_to_markdown()
                _PLAYWRIGHT_SHEET_CACHE.clear()
                prefer_api_for_sparse = _is_api_available()
                extra_sheet_blocks = data.get("sheet_blocks") or {}
                for tk, cv in (data.get("sheet_client_vars") or {}).items():
                    try:
                        merged_cv = _merge_sheet_snapshot_blocks(
                            cv, extra_sheet_blocks
                        )
                        table_md = _decode_sheet_client_vars(
                            merged_cv,
                            allow_sparse_blocks=not prefer_api_for_sparse,
                        )
                        if table_md:
                            _PLAYWRIGHT_SHEET_CACHE[tk] = table_md
                            logger.info(
                                f"[Feishu Wiki PW] Pre-decoded sheet: {tk}"
                            )
                        else:
                            _PLAYWRIGHT_SHEET_CACHE[tk] = ""
                            logger.info(
                                f"[Feishu Wiki PW] Sheet decode deferred to fallback: {tk}"
                            )
                    except Exception as e:
                        _PLAYWRIGHT_SHEET_CACHE[tk] = ""
                        logger.debug(
                            f"[Feishu Wiki PW] Sheet decode failed for {tk}: {e}"
                        )

                # Convert block tree to Markdown with image collection
                _img_subdir = hashlib.md5(link_url.encode()).hexdigest()[:12]
                images_list: List[dict] = []
                block_tree = data.get("blockTree")
                if block_tree:
                    children = block_tree.get("children", [])
                    content = blocks_to_markdown(children, images=images_list,
                                                 img_subdir=_img_subdir)
                else:
                    content = data.get("content", "")

                _PLAYWRIGHT_SHEET_CACHE.clear()

                pre_bytes = data.get("_image_bytes", {})
                if pre_bytes:
                    for img_info in images_list:
                        tk = img_info.get("token", "")
                        if tk and tk in pre_bytes:
                            img_info["_bytes"] = pre_bytes[tk]

                doc_data = {
                    "title": title or data.get("title", ""),
                    "content": content,
                    "url": link_url,
                    "author": data.get("author", ""),
                    "doc_type": "wiki",
                    "doc_token": token,
                    "images": [img.get("token", "") for img in images_list],
                    "images_info": images_list,
                    "img_subdir": _img_subdir,
                    "tags": [],
                }

                uc = from_feishu(doc_data)
                uc.category = wiki_title
                saved_path = save_to_markdown(uc)
                add_item(item_id, link_url, dedup_idx)

                # Download images if enabled
                if saved_path and images_list and feishu_download_images():
                    download_feishu_images(saved_path, images_list, link_url,
                                           img_subdir=_img_subdir)

                fetched += 1
                done_set.add(token)

                if fetched % 5 == 0:
                    progress["done"] = list(done_set)
                    _save_progress(root_token, progress)

            except Exception as e:
                logger.warning(f"[Feishu Wiki PW] Failed {title}: {e}")
                failed += 1

            if i < len(links):
                await asyncio.sleep(delay)

        if not failed:
            _clear_progress(root_token)
        else:
            progress["done"] = list(done_set)
            _save_progress(root_token, progress)

        return {
            "wiki_title": wiki_title,
            "total": len(links),
            "fetched": fetched,
            "skipped": skipped,
            "failed": failed,
        }

    finally:
        if via_cdp:
            # CDP: close tab only, don't kill user's Chrome
            try:
                await page.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
        elif browser:
            await browser.close()
        await pw.stop()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fetch_feishu_wiki(url: str) -> Dict[str, Any]:
    """Batch fetch all documents in a Feishu wiki space.

    Returns a summary dict with counts.
    """
    if not feishu_wiki_batch_enabled():
        raise ValueError(
            "飞书知识库批量抓取未启用。请设置 FEISHU_WIKI_BATCH_ENABLED=true，"
            "或使用 feishu-wiki CLI 命令。"
        )

    parsed = parse_feishu_url(url)
    if not parsed or parsed["type"] != "wiki":
        raise ValueError(f"不是飞书知识库 URL：{url}")

    root_token = parsed["token"]

    # Tier 0: Open API
    if _is_api_available():
        try:
            logger.info("[Feishu Wiki] Tier 0: Open API batch")
            return await _fetch_wiki_via_api(url, root_token)
        except Exception as e:
            logger.warning(f"[Feishu Wiki] Tier 0 failed ({e}), falling back")

    # Tier 1: Playwright
    try:
        logger.info("[Feishu Wiki] Tier 1: Playwright sidebar")
        return await _fetch_wiki_via_playwright(url, root_token)
    except Exception as e:
        raise RuntimeError(
            f"飞书知识库批量抓取失败：{e}。"
            "可选处理：1) 配置 FEISHU_APP_ID + FEISHU_APP_SECRET；"
            "2) 运行 'feedgrab login feishu'"
        )
