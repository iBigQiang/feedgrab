# -*- coding: utf-8 -*-
"""
WeChat MP account article batch fetch — enumerate all articles from a
public account via the MP backend API.

Requires:
    - feedgrab login wechat (saves MP backend session)
    - Session gives access to /cgi-bin/searchbiz + /cgi-bin/appmsgpublish APIs

Usage:
    feedgrab mpweixin-id "饼干哥哥AGI"

Data flow:
    1. Load MP backend session (sessions/wechat.json)
    2. searchbiz API → find account by name → get fakeid
    3. appmsgpublish API → paginate article list → filter by date
    4. For each article: open URL in browser → evaluate_wechat_article → save
    5. Dedup via mpweixin/index/item_id_url.json + progress cache for resume
"""

import asyncio
import json
import random
import time
from datetime import datetime
from pathlib import Path
from loguru import logger
from typing import Dict, Any, List, Optional

from feedgrab.config import (
    get_session_dir, mpweixin_id_since, mpweixin_id_delay,
    mpweixin_id_page_size, mpweixin_id_page_delay, mpweixin_id_page_jitter,
    mpweixin_id_max_articles, mpweixin_id_freq_retry,
)
from feedgrab.utils.dedup import (
    load_index, save_index, has_item, add_item, item_id_from_url,
    get_index_path,
)

MPWEIXIN_LOGIN_COMMAND = "'feedgrab login wechat'"
MPWEIXIN_SESSION_EXPIRED_MESSAGE = (
    f"微信公众号后台登录态已过期或无效。请运行 {MPWEIXIN_LOGIN_COMMAND} 重新登录。"
)
MPWEIXIN_SESSION_MISSING_MESSAGE = (
    f"微信公众号后台登录态文件不存在。请先运行 {MPWEIXIN_LOGIN_COMMAND}。"
)

# Backoff steps used when MPWEIXIN_ID_FREQ_RETRY is enabled.
_FREQ_BACKOFF_SECONDS = (60, 300, 900)

# Consecutive captcha pages that abort the run.  Once WeChat starts serving
# risk-control pages, continuing only burns quota without producing articles.
_MAX_CONSECUTIVE_CAPTCHA = 5

MPWEIXIN_FREQ_CONTROL_MESSAGE = (
    "微信后台「查询他人公众号文章列表」触发频率限制（ret=200013 freq control）。\n"
    "   该限制按请求次数计量，与目标公众号、微信账号、接口版本、count 取值均无关，\n"
    "   换号 / 换接口 / 改参数都无法绕过；此时登录态本身仍然有效。\n"
    "   已抓取的内容与分页进度均已保留，解除限制后直接重跑即可续抓。\n"
    "   建议：\n"
    "     1) 暂停批量抓取，等待微信解除限制\n"
    "     2) 调大 MPWEIXIN_ID_PAGE_SIZE（默认 20）减少列表请求次数，\n"
    "        调大 MPWEIXIN_ID_PAGE_DELAY（默认 8 秒）放慢翻页节奏\n"
    "     3) 急需新文可临时改用 feedgrab mpweixin-so <公众号名>（搜狗搜索，约 10 条）"
)

MPWEIXIN_RISK_CONTROL_MESSAGE = (
    f"连续 {_MAX_CONSECUTIVE_CAPTCHA} 篇文章被微信风控验证页拦截，已中止本轮批量。\n"
    "   这些文章未保存、也未记入去重索引，稍后重跑可自动补抓。\n"
    "   建议：暂停一段时间再跑，并调大 MPWEIXIN_ID_DELAY 放慢单篇节奏。"
)

# Normalized reasons produced by browser.detect_wechat_unavailable().
_UNAVAILABLE_LABELS = {
    "deleted": "已被发布者删除",
    "violation": "因违规无法查看",
    "privacy": "作者隐私设置限制",
    "captcha": "微信风控验证页",
}


class MPWeixinFreqControlError(RuntimeError):
    """Raised when the MP backend rejects a list request with ret=200013."""

    def __init__(self, message: str = MPWEIXIN_FREQ_CONTROL_MESSAGE):
        super().__init__(message)


class MPWeixinRiskControlError(RuntimeError):
    """Raised when consecutive article pages are replaced by a captcha page."""

    def __init__(self, message: str = MPWEIXIN_RISK_CONTROL_MESSAGE):
        super().__init__(message)


# ---------------------------------------------------------------------------
# Progress cache — resume after interruption
# ---------------------------------------------------------------------------

def _progress_path(account_name: str) -> Path:
    """Return path for the progress cache file."""
    index_dir = get_index_path("mpweixin").parent
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in account_name)
    return index_dir / f"_progress_mpweixin_id_{safe}.json"


def _load_progress(account_name: str) -> dict:
    path = _progress_path(account_name)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_progress(account_name: str, data: dict):
    path = _progress_path(account_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _clear_progress(account_name: str):
    path = _progress_path(account_name)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# MP Backend API via Playwright
# ---------------------------------------------------------------------------

async def _find_account(page, account_name: str) -> Optional[dict]:
    """Search for a WeChat public account by name via searchbiz API.

    Returns dict with fakeid, nickname, round_head_img, signature, or None.
    """
    js = """async (query) => {
        const url = new URL('https://mp.weixin.qq.com/cgi-bin/searchbiz');
        const token = window.location.href.match(/token=(\\d+)/)?.[1] || '';
        url.searchParams.set('action', 'search_biz');
        url.searchParams.set('begin', '0');
        url.searchParams.set('count', '5');
        url.searchParams.set('query', query);
        url.searchParams.set('token', token);
        url.searchParams.set('lang', 'zh_CN');
        url.searchParams.set('f', 'json');
        url.searchParams.set('ajax', '1');
        const resp = await fetch(url.toString(), { credentials: 'include' });
        return resp.json();
    }"""

    data = await page.evaluate(js, account_name)

    if not data or data.get("base_resp", {}).get("ret") != 0:
        ret = data.get("base_resp", {}).get("ret", "unknown") if data else "no response"
        logger.error(f"[mpweixin-id] searchbiz failed: ret={ret}")
        return None

    accounts = data.get("list", [])
    if not accounts:
        logger.warning(f"[mpweixin-id] No accounts found for '{account_name}'")
        return None

    # Account batch fetch needs the exact official account fakeid.  Using the
    # first fuzzy search result can silently fetch the wrong account or zero rows.
    for acc in accounts:
        if acc.get("nickname") == account_name:
            return acc
    candidates = "、".join(str(acc.get("nickname", "")).strip() for acc in accounts[:5] if acc.get("nickname"))
    logger.warning(
        f"[mpweixin-id] No exact account match for '{account_name}'. "
        f"Candidates: {candidates or 'none'}"
    )
    return None


async def _fetch_article_list(page, fakeid: str, begin: int = 0,
                               size: int = 5) -> tuple:
    """Fetch one page of articles via appmsgpublish API.

    Returns (articles: list[dict], is_complete: bool, total: int).
    Each article dict has: title, link, create_time, digest, cover, etc.
    """
    js = """async (params) => {
        const url = new URL('https://mp.weixin.qq.com/cgi-bin/appmsgpublish');
        const token = window.location.href.match(/token=(\\d+)/)?.[1] || '';
        url.searchParams.set('sub', 'list');
        url.searchParams.set('search_field', 'null');
        url.searchParams.set('begin', String(params.begin));
        url.searchParams.set('count', String(params.size));
        url.searchParams.set('query', '');
        url.searchParams.set('fakeid', params.fakeid);
        url.searchParams.set('type', '101_1');
        url.searchParams.set('free_publish_type', '1');
        url.searchParams.set('sub_action', 'list_ex');
        url.searchParams.set('token', token);
        url.searchParams.set('lang', 'zh_CN');
        url.searchParams.set('f', 'json');
        url.searchParams.set('ajax', '1');
        const resp = await fetch(url.toString(), { credentials: 'include' });
        return resp.json();
    }"""

    data = await page.evaluate(js, {"fakeid": fakeid, "begin": begin, "size": size})

    if not data:
        raise RuntimeError(
            "微信后台 appmsgpublish 接口无响应。已抓取内容与进度均已保留，可稍后重试。"
        )

    base_resp = data.get("base_resp", {}) or {}
    ret = base_resp.get("ret", -1)
    err_msg = base_resp.get("err_msg", "")
    if ret == 200003:
        raise RuntimeError(MPWEIXIN_SESSION_EXPIRED_MESSAGE)
    if ret == 200013:
        # A rate-limited page must never be reported as "list exhausted" — that
        # is what used to turn a blocked run into "fetched 0 articles, done".
        logger.error(f"[mpweixin-id] appmsgpublish failed: ret={ret} err_msg={err_msg}")
        raise MPWeixinFreqControlError()
    if ret != 0:
        raise RuntimeError(
            f"微信后台 appmsgpublish 接口返回错误：ret={ret} "
            f"err_msg={err_msg or '<empty>'}。已抓取内容与进度均已保留，可稍后重试。"
        )

    publish_page_str = data.get("publish_page", "")
    if not publish_page_str:
        return [], True, 0

    publish_page = json.loads(publish_page_str) if isinstance(publish_page_str, str) else publish_page_str
    total = publish_page.get("total_count", 0)
    publish_list = publish_page.get("publish_list", [])

    articles = []
    for item in publish_list:
        info_str = item.get("publish_info", "")
        if not info_str:
            continue
        info = json.loads(info_str) if isinstance(info_str, str) else info_str
        for art in info.get("appmsgex", []):
            articles.append(art)

    is_complete = len(publish_list) == 0
    return articles, is_complete, total


async def _fetch_list_with_backoff(page, fakeid: str, begin: int, size: int,
                                   retries: int = 0) -> tuple:
    """Fetch one list page, optionally backing off after freq control.

    Defaults to no retry: a freq-control block normally outlasts any wait a
    single run can afford, so retrying just spends more of the same quota.
    """
    attempt = 0
    while True:
        try:
            return await _fetch_article_list(page, fakeid, begin=begin, size=size)
        except MPWeixinFreqControlError:
            if attempt >= retries:
                raise
            wait = _FREQ_BACKOFF_SECONDS[min(attempt, len(_FREQ_BACKOFF_SECONDS) - 1)]
            logger.warning(
                f"[mpweixin-id] >>> 触发频率限制 ret=200013，{wait}s 后重试"
                f"（第 {attempt + 1}/{retries} 次）<<<"
            )
            await asyncio.sleep(wait)
            attempt += 1


def _page_sleep_seconds() -> float:
    """Delay between list pages, jittered to avoid a perfectly regular cadence."""
    base = mpweixin_id_page_delay()
    jitter = mpweixin_id_page_jitter()
    if base <= 0 or jitter <= 0:
        return base
    return max(0.0, base * (1.0 + random.uniform(-jitter, jitter)))


def _record_unavailable(reason: str, title: str, link: str, item_id: str,
                        result: dict, dedup_index: dict,
                        log_prefix: str = "mpweixin-id") -> None:
    """Account for a placeholder page without writing an empty article file.

    Risk-control pages stay out of the dedup index so a later run retries them.
    Content that is genuinely gone (deleted / violation / privacy) is indexed,
    so later runs stop re-opening the same dead link.
    """
    label = _UNAVAILABLE_LABELS.get(reason, reason)
    if reason == "captcha":
        result["failed"] += 1
        logger.warning(
            f"[{log_prefix}] >>> {label}，未保存也未记入去重索引，稍后重跑可补抓："
            f"{title[:40]} <<<"
        )
        return
    result["skipped"] += 1
    if item_id:
        add_item(item_id, link, dedup_index)
    logger.info(f"[{log_prefix}] 跳过（{label}）：{title[:40]}")


# ---------------------------------------------------------------------------
# Main batch fetch
# ---------------------------------------------------------------------------

async def fetch_account_articles(
    account_name: str,
    since: str = "",
    delay: float = 3.0,
) -> Dict[str, Any]:
    """Fetch all articles from a WeChat public account.

    Args:
        account_name: Public account name (e.g. "饼干哥哥AGI")
        since: Only fetch articles after this date (YYYY-MM-DD), empty = all
        delay: Seconds between article fetches

    Returns:
        Dict with total, fetched, skipped, failed counts.
    """
    from feedgrab.fetchers.browser import (
        get_async_playwright, stealth_launch, get_stealth_context_options,
        setup_resource_blocking, generate_referer,
        evaluate_wechat_article, fetch_wechat_comments,
    )
    from feedgrab.fetchers.wechat_search import _html_to_markdown
    from feedgrab.schema import from_wechat
    from feedgrab.utils.storage import save_to_markdown
    from feedgrab.config import mpweixin_fetch_comments, mpweixin_max_comments

    _fetch_comments = mpweixin_fetch_comments()
    _max_comments = mpweixin_max_comments()

    session_path = get_session_dir() / "wechat.json"
    if not session_path.exists():
        raise RuntimeError(MPWEIXIN_SESSION_MISSING_MESSAGE)

    since_ts = 0
    if since:
        try:
            since_ts = int(datetime.strptime(since, "%Y-%m-%d").timestamp())
        except ValueError:
            logger.warning(f"[mpweixin-id] Invalid since date: {since}, ignoring")

    # Load dedup index + progress cache
    dedup_index = load_index("mpweixin")
    progress = _load_progress(account_name)
    resume_begin = progress.get("next_begin", 0)
    if resume_begin > 0:
        logger.info(f"[mpweixin-id] Resuming from offset {resume_begin}")

    result = {
        "total": 0,
        "fetched": progress.get("fetched", 0),
        "skipped": progress.get("skipped", 0),
        "failed": progress.get("failed", 0),
        "articles": [],
        # Non-empty when the run stopped early: "freq_control" / "risk_control"
        # / "max_articles".  Callers must not report an interrupted run as done.
        "interrupted": "",
    }

    async_pw = get_async_playwright()
    async with async_pw() as p:
        browser = await stealth_launch(p, headless=True)
        ctx_opts = get_stealth_context_options()
        ctx_opts["storage_state"] = str(session_path)
        context = await browser.new_context(**ctx_opts)
        page = await context.new_page()
        await setup_resource_blocking(page)
        date_cutoff_reached = False
        # A run counts as complete only when the listing was read to the end or
        # the date cutoff was hit.  Any other exit keeps the progress file, so
        # the next run resumes instead of silently skipping the remainder.
        completed = False
        max_reached = False
        consecutive_captcha = 0
        _max_articles = mpweixin_id_max_articles()
        _freq_retry = mpweixin_id_freq_retry()

        try:
            # Navigate to MP backend to establish session context
            logger.info("[mpweixin-id] Loading MP backend session...")
            await page.goto(
                "https://mp.weixin.qq.com/",
                wait_until="domcontentloaded", timeout=30_000,
            )
            await page.wait_for_timeout(2000)

            # Check if session is valid (should redirect to home with token)
            current_url = page.url
            if "token=" not in current_url:
                raise RuntimeError(MPWEIXIN_SESSION_EXPIRED_MESSAGE)
            logger.info("[mpweixin-id] Session valid")

            # Step 1: Find account
            logger.info(f"[mpweixin-id] Searching account: {account_name}")
            account = await _find_account(page, account_name)
            if not account:
                raise RuntimeError(f"未找到公众号：{account_name}")

            fakeid = account["fakeid"]
            nickname = account.get("nickname", account_name)
            logger.info(f"[mpweixin-id] Found: {nickname} (fakeid={fakeid})")

            # Step 2: Paginate article list
            begin = resume_begin
            page_size = mpweixin_id_page_size()

            def _record_progress(next_begin: int) -> None:
                _save_progress(account_name, {
                    "fakeid": fakeid,
                    "nickname": nickname,
                    "next_begin": next_begin,
                    "fetched": result["fetched"],
                    "skipped": result["skipped"],
                    "failed": result["failed"],
                })

            while not date_cutoff_reached:
                logger.info(
                    f"[mpweixin-id] Fetching articles offset={begin} count={page_size}"
                )
                articles, is_complete, total = await _fetch_list_with_backoff(
                    page, fakeid, begin=begin, size=page_size, retries=_freq_retry,
                )
                result["total"] = total

                if not articles:
                    # Only an empty publish_list means the listing is exhausted.
                    # A page can carry publish records that yield no appmsgex
                    # (text-only or channel posts); that is an empty page, not
                    # the end of the list, and must not clear the progress file.
                    if is_complete:
                        completed = True
                        break
                    logger.info(f"[mpweixin-id] offset={begin} 无图文消息，翻下一页")
                    begin += page_size
                    _record_progress(begin)
                    await asyncio.sleep(_page_sleep_seconds())
                    continue

                for art in articles:
                    create_time = art.get("create_time", 0)
                    title = art.get("title", "untitled")
                    link = art.get("link", "")

                    # Date filter
                    if since_ts and create_time < since_ts:
                        logger.info(
                            f"[mpweixin-id] Reached date cutoff at "
                            f"{datetime.fromtimestamp(create_time).strftime('%Y-%m-%d')}"
                        )
                        date_cutoff_reached = True
                        break

                    # Dedup check
                    item_id = item_id_from_url(link) if link else ""
                    if item_id and has_item(item_id, dedup_index):
                        logger.debug(f"[mpweixin-id] Skip (dedup): {title[:40]}")
                        result["skipped"] += 1
                        continue

                    # Fetch full article
                    logger.info(
                        f"[mpweixin-id] [{result['fetched']+result['skipped']+result['failed']+1}] "
                        f"{title[:50]}"
                    )

                    try:
                        # Open article in a new tab
                        art_page = await context.new_page()
                        await setup_resource_blocking(art_page)
                        await art_page.goto(
                            link, wait_until="domcontentloaded", timeout=30_000,
                            referer=generate_referer(link),
                        )
                        art_data = await evaluate_wechat_article(
                            art_page, md_converter=_html_to_markdown,
                        )

                        # Deleted / violating / private / risk-control pages come
                        # back as a placeholder shell.  Saving those is what
                        # produced the "微信公众平台" empty article files.
                        reason = art_data.get("unavailable_reason", "")
                        if reason:
                            await art_page.close()
                            _record_unavailable(
                                reason, title, link, item_id, result, dedup_index,
                            )
                            consecutive_captcha = (
                                consecutive_captcha + 1 if reason == "captcha" else 0
                            )
                        else:
                            consecutive_captcha = 0

                            # Fetch comments before closing page
                            if _fetch_comments and art_data.get("comment_id"):
                                cmt = await fetch_wechat_comments(
                                    art_page, art_data["comment_id"],
                                    appmsg_token=art_data.get("appmsg_token", ""),
                                    max_comments=_max_comments,
                                )
                                if cmt:
                                    art_data["comment_list"] = cmt

                            await art_page.close()

                            # Use API metadata as fallback when page extraction misses fields
                            if not art_data.get("title"):
                                # API title → digest (for 小绿书 image posts without title)
                                art_data["title"] = title if title != "untitled" else art.get("digest", "")
                            if not art_data.get("author"):
                                art_data["author"] = nickname
                            if not art_data.get("cover_image"):
                                art_data["cover_image"] = art.get("cover", "")
                            if not art_data.get("summary"):
                                art_data["summary"] = art.get("digest", "")

                            # Save
                            item = from_wechat(art_data)
                            item.category = f"account/{nickname}"
                            saved_path = save_to_markdown(item)

                            # Download media if enabled
                            if saved_path and (item.extra.get("videos") or item.extra.get("images")):
                                from feedgrab.config import mpweixin_download_media
                                if mpweixin_download_media():
                                    from feedgrab.utils.media import download_media
                                    download_media(
                                        saved_path,
                                        item.extra.get("images", []),
                                        item.extra.get("videos", []),
                                        item.id,
                                        platform="wechat",
                                    )

                            # Update dedup index
                            if item_id:
                                add_item(item_id, link, dedup_index)

                            result["fetched"] += 1
                            result["articles"].append({
                                "title": art_data.get("title", ""),
                                "author": nickname,
                                "publish_date": art_data.get("publish_date", ""),
                                "url": link,
                            })
                    except Exception as e:
                        logger.error(f"[mpweixin-id] Failed: {title[:40]} — {e}")
                        result["failed"] += 1

                    # Progress records the page being processed, not the next
                    # one: an interruption mid-page must re-read this page (dedup
                    # skips what was already saved) rather than skip the rest of it.
                    _record_progress(begin)

                    if consecutive_captcha >= _MAX_CONSECUTIVE_CAPTCHA:
                        result["interrupted"] = "risk_control"
                        raise MPWeixinRiskControlError()

                    if _max_articles and result["fetched"] >= _max_articles:
                        max_reached = True
                        break

                    if delay > 0:
                        await asyncio.sleep(delay)

                if max_reached:
                    logger.warning(
                        f"[mpweixin-id] >>> 已达单次上限 MPWEIXIN_ID_MAX_ARTICLES="
                        f"{_max_articles}，本轮停止；进度已保存，重跑可继续 <<<"
                    )
                    result["interrupted"] = "max_articles"
                    break

                if is_complete or date_cutoff_reached:
                    completed = True
                    break

                # Whole page done — only now advance the resume offset.
                begin += page_size
                _record_progress(begin)
                await asyncio.sleep(_page_sleep_seconds())

        except MPWeixinFreqControlError:
            result["interrupted"] = "freq_control"
            raise
        finally:
            # Save dedup index
            save_index(dedup_index, "mpweixin")
            # Only a run that read the listing to the end (or stopped at the date
            # cutoff) may drop its progress.  Clearing it after a rate-limited or
            # aborted run used to strand the remaining articles.
            if completed:
                _clear_progress(account_name)
            await context.close()
            await browser.close()

    return result
