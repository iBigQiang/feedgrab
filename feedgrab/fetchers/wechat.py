# -*- coding: utf-8 -*-
"""
WeChat article fetcher — three-tier fallback:

1. Stealth browser with WeChat-specific extraction (rich HTML + full metadata)
2. Jina Reader (lightweight fallback, no browser deps)
3. Browser retry (fresh context)

Browser-first strategy: WeChat articles are best extracted via browser
(complete metadata, code blocks, images). Jina often times out for WeChat
CDN and returns incomplete data (no author/date/cover/tags).
"""

from loguru import logger
from typing import Dict, Any


# Placeholder reasons produced by browser.detect_wechat_unavailable().
WECHAT_UNAVAILABLE_LABELS = {
    "deleted": "该内容已被发布者删除",
    "violation": "该内容因违规无法查看",
    "privacy": "该内容受作者隐私设置限制",
    "captcha": "被微信风控验证页拦截（稍后重试可能恢复）",
}


class WeChatUnavailableError(RuntimeError):
    """WeChat served a placeholder page instead of the article body."""

    def __init__(self, reason: str):
        self.reason = reason
        label = WECHAT_UNAVAILABLE_LABELS.get(reason, reason)
        super().__init__(
            f"微信文章不可抓取：{label}。\n"
            "   微信返回的是占位页而非文章正文，已终止抓取，不保存空白 Markdown。"
        )


async def _browser_fetch(url: str) -> Dict[str, Any]:
    """Fetch WeChat article via stealth browser with WeChat-specific JS extraction."""
    from feedgrab.fetchers.browser import (
        get_async_playwright, stealth_launch, get_stealth_context_options,
        setup_resource_blocking, generate_referer,
        evaluate_wechat_article, fetch_wechat_comments,
    )
    from feedgrab.fetchers.wechat_search import _html_to_markdown
    from feedgrab.config import mpweixin_fetch_comments, mpweixin_max_comments

    async_pw = get_async_playwright()
    async with async_pw() as p:
        browser = await stealth_launch(p, headless=True)
        context = await browser.new_context(**get_stealth_context_options())
        page = await context.new_page()
        await setup_resource_blocking(page)

        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=30_000,
                referer=generate_referer(url),
            )
            result = await evaluate_wechat_article(
                page, md_converter=_html_to_markdown,
            )
            # WeChat serves a placeholder page (deleted / violation / privacy /
            # risk control) with a non-empty body, so the empty-content check
            # below never catches it — that is how "微信公众平台" shells got saved.
            reason = result.get("unavailable_reason", "")
            if reason:
                raise WeChatUnavailableError(reason)
            if not result.get("content"):
                raise ValueError("Browser extraction returned empty content")

            # Fetch comments if enabled (requires WeChat client session for auth)
            if mpweixin_fetch_comments() and result.get("comment_id"):
                comments = await fetch_wechat_comments(
                    page, result["comment_id"],
                    appmsg_token=result.get("appmsg_token", ""),
                    max_comments=mpweixin_max_comments(),
                )
                if comments:
                    result["comment_list"] = comments

            logger.info(f"[WeChat] Browser OK: {result['title'][:60]}")
            return result
        finally:
            await context.close()
            await browser.close()


async def fetch_wechat(url: str) -> Dict[str, Any]:
    """
    Fetch a WeChat public account article with fallback.

    Tier 1: Browser (complete metadata + rich Markdown)
    Tier 2: Jina Reader (lightweight fallback)
    Tier 3: Browser retry (fresh context)

    Args:
        url: mp.weixin.qq.com article URL

    Returns:
        Dict with: title, content, author, url, platform,
                   cover_image, publish_date, summary, tags, original_url
    """
    # Tier 1: Browser (best data completeness)
    try:
        logger.info(f"[WeChat] Tier 1 — Browser: {url}")
        return await _browser_fetch(url)
    except WeChatUnavailableError:
        # A placeholder page is a definitive answer, not a transient failure:
        # falling through would just let Jina scrape the same placeholder text
        # into a Markdown file.
        raise
    except Exception as e:
        logger.warning(f"[WeChat] Browser failed ({e}), falling back to Jina")

    # Tier 2: Jina Reader (lightweight, no browser deps)
    try:
        logger.info(f"[WeChat] Tier 2 — Jina: {url}")
        from feedgrab.fetchers.jina import fetch_via_jina

        data = fetch_via_jina(url)
        if data.get("content"):
            logger.info(f"[WeChat] Jina OK: {data['title'][:60]}")
            return {
                "title": data["title"],
                "content": data["content"],
                "author": data.get("author", ""),
                "url": url,
                "platform": "wechat",
                "cover_image": "",
                "publish_date": "",
                "summary": "",
                "tags": [],
                "original_url": "",
            }
        logger.warning("[WeChat] Jina returned empty content")
    except Exception as e:
        logger.warning(f"[WeChat] Jina failed ({e})")

    # Tier 3: Browser retry (fresh context, last resort)
    try:
        logger.info(f"[WeChat] Tier 3 — Browser retry: {url}")
        return await _browser_fetch(url)
    except WeChatUnavailableError:
        raise
    except Exception as e:
        logger.error(f"[WeChat] 所有抓取层都失败：{e}")
        raise RuntimeError(
            "微信公众号文章抓取所有兜底方式都失败。\n"
            f"   最后错误：{e}"
        )
