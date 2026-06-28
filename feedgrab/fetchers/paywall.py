# -*- coding: utf-8 -*-
"""
Paywall bypass engine — 7-tier cascade against known paywall news sites.

Strategy stack (in order):
    Tier 1: Googlebot UA + X-Forwarded-For + Google Referer (for GOOGLEBOT_DOMAINS)
    Tier 2: Bingbot UA + Bing Referer (for BINGBOT_DOMAINS)
    Tier 3: Generic rotation over PAYWALL_DOMAINS — Googlebot/Bingbot/FB/Twitter
    Tier 4: AMP page redirect (for AMP_DOMAINS)
    Tier 5: EU IP spoofing + Google Referer
    Tier 6: archive.today (with CAPTCHA detection)
    Tier 7: Google Cache

JSON-LD articleBody extraction runs as the primary parser in every tier;
HTML-to-Markdown (via markdownify) is the fallback parser.

Returns None (→ Jina fallback) when all tiers fail or when the URL is not
a known paywall domain and JSON-LD pre-check is disabled.

Reference:
    Original Bash implementation: qiaomu-anything-to-notebooklm/scripts/fetch_url.sh
    Technique source: https://gitflic.ru/project/magnolia1234/bypass-paywalls-chrome-clean
"""

import random
import re
from typing import Optional, Dict, List
from urllib.parse import urlparse

import requests
from loguru import logger

from feedgrab.config import (
    paywall_timeout,
    paywall_use_amp,
    paywall_use_archive,
    paywall_use_google_cache,
    paywall_domains_extra,
    paywall_jsonld_for_all,
)
from feedgrab.utils import http_client
from feedgrab.utils.jsonld import extract_jsonld_article, extract_title_from_html


# ---------------------------------------------------------------------------
# Paywall domain lists (hardcoded, community-maintained via fetch_url.sh)
# ---------------------------------------------------------------------------

# Sites where Googlebot UA gets full content (SEO whitelist)
GOOGLEBOT_DOMAINS = (
    "wsj.com|barrons.com|ft.com|economist.com|theaustralian.com.au|"
    "thetimes.co.uk|telegraph.co.uk|zeit.de|handelsblatt.com|leparisien.fr|"
    "nzz.ch|usatoday.com|quora.com|lefigaro.fr|lemonde.fr|spiegel.de|"
    "sueddeutsche.de|frankfurter-allgemeine.de|brisbanetimes.com.au|"
    "smh.com.au|theage.com.au"
)

# Sites where Bingbot UA works better
BINGBOT_DOMAINS = "haaretz.com|nzherald.co.nz|stratfor.com|themarker.com"

# Sites that allow social referral traffic
FACEBOOK_REF_DOMAINS = "law.com|ftm.nl|law360.com|sloanreview.mit.edu"

# Sites with usable AMP versions
AMP_DOMAINS = (
    "wsj.com|bostonglobe.com|latimes.com|chicagotribune.com|"
    "seattletimes.com|theatlantic.com|wired.com|newyorker.com|"
    "washingtonpost.com|smh.com.au|theage.com.au|brisbanetimes.com.au"
)

# All known paywall domains (triggers generic-bypass tier)
PAYWALL_DOMAINS = (
    "nytimes.com|wsj.com|ft.com|economist.com|bloomberg.com|washingtonpost.com|"
    "newyorker.com|wired.com|theatlantic.com|medium.com|businessinsider.com|"
    "technologyreview.com|scmp.com|seattletimes.com|bostonglobe.com|latimes.com|"
    "chicagotribune.com|theglobeandmail.com|afr.com|thetimes.co.uk|telegraph.co.uk|"
    "spiegel.de|zeit.de|sueddeutsche.de|barrons.com|forbes.com|foreignaffairs.com|"
    "foreignpolicy.com|harvard.edu|newscientist.com|scientificamerican.com|"
    "theinformation.com|statista.com|handelsblatt.com|nzz.ch|leparisien.fr|"
    "lefigaro.fr|lemonde.fr|haaretz.com|nzherald.co.nz|theaustralian.com.au|"
    "smh.com.au|theage.com.au|quora.com|usatoday.com"
)

# ---------------------------------------------------------------------------
# Crawler User-Agents
# ---------------------------------------------------------------------------

UA_GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
UA_BINGBOT = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
UA_CHROME_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# Paywall-indicator phrases (appear in response body when article is gated)
_PAYWALL_PHRASES = re.compile(
    r"subscribe to (?:continue|read|access|unlock)|"
    r"paywall|"
    r"premium[._\s]content|"
    r"metered[._\s]paywall|"
    r"article[._\s]limit|"
    r"sign[._\s]in[._\s]to[._\s](?:continue|read)|"
    r"create[._\s]a[._\s]free[._\s]account[._\s]to[._\s]unlock|"
    r"membership[._\s]to[._\s]continue|"
    r"subscribe now for full access|"
    r"to continue reading|"
    r"remaining free articles|"
    r"has been removed|"
    r"already a subscriber",
    re.IGNORECASE,
)

# CAPTCHA / challenge indicators (archive.today, Cloudflare, etc.)
_CAPTCHA_PHRASES = re.compile(
    r"security check|captcha|recaptcha|hcaptcha|"
    r"please complete|cloudflare.*challenge|verify you are human",
    re.IGNORECASE,
)

# Known error / login-wall snippets
_ERROR_PHRASES = (
    "Don't miss what's happening",
    "Access Denied",
    "404 Not Found",
    "403 Forbidden",
)


# ---------------------------------------------------------------------------
# Domain matching
# ---------------------------------------------------------------------------

def _get_domain(url: str) -> str:
    """Extract netloc (lowercase, no www.)."""
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _match_domain(url: str, pipe_list: str) -> bool:
    """Check if URL's domain matches any domain in pipe-separated list."""
    if not pipe_list:
        return False
    domain = _get_domain(url)
    if not domain:
        return False
    for d in pipe_list.split("|"):
        d = d.strip()
        if d and (domain == d or domain.endswith("." + d)):
            return True
    return False


def is_paywall_domain(url: str) -> bool:
    """True if URL is in any known paywall domain list (+ user extras)."""
    return (
        _match_domain(url, PAYWALL_DOMAINS)
        or _match_domain(url, GOOGLEBOT_DOMAINS)
        or _match_domain(url, BINGBOT_DOMAINS)
        or _match_domain(url, paywall_domains_extra())
    )


# ---------------------------------------------------------------------------
# Content validators (translated from fetch_url.sh helpers)
# ---------------------------------------------------------------------------

def _has_content(text: str) -> bool:
    """Minimum-content gate: 500+ chars, 8+ lines, no error snippets."""
    if not text:
        return False
    if len(text) < 500:
        return False
    if text.count("\n") < 8:
        return False
    for phrase in _ERROR_PHRASES:
        if phrase in text:
            return False
    return True


def _is_paywall_content(text: str) -> bool:
    """Detect paywall-indicator phrases in response body."""
    return bool(text and _PAYWALL_PHRASES.search(text))


def _is_captcha_page(text: str) -> bool:
    """Detect CAPTCHA/challenge page."""
    return bool(text and _CAPTCHA_PHRASES.search(text))


# ---------------------------------------------------------------------------
# HTML → Markdown
# ---------------------------------------------------------------------------

def _html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown using markdownify (with SEO-noise stripping)."""
    try:
        from markdownify import markdownify as md
    except ImportError:
        # Fallback: crude tag stripper (mirrors fetch_url.sh sed chain)
        cleaned = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>",
                         "", html, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    # Strip script/style/nav/footer/aside before conversion
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "aside", "header", "form"]):
            tag.decompose()
        html = str(soup)
    except ImportError:
        pass

    return md(
        html,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
    ).strip()


def _extract_article_from_html(url: str, html: str, strategy: str) -> Optional[dict]:
    """Parse article from HTML — JSON-LD first, then markdownify fallback.

    Returns None if extracted content fails quality gates.
    """
    if not html:
        return None

    # Pass 1: JSON-LD articleBody (fastest, highest fidelity)
    ld = extract_jsonld_article(html)
    if ld and ld.get("articleBody") and len(ld["articleBody"]) > 200:
        title = ld.get("headline") or extract_title_from_html(html) or urlparse(url).path
        return {
            "title": title.strip()[:200],
            "content": ld["articleBody"],
            "url": url,
            "author": ld.get("author", ""),
            "published": ld.get("datePublished", ""),
            "image": ld.get("image", ""),
            "strategy": f"{strategy}+jsonld",
        }

    # Pass 2: markdownify the full page
    md_text = _html_to_markdown(html)
    if not _has_content(md_text) or _is_paywall_content(md_text):
        return None

    title = extract_title_from_html(html) or urlparse(url).path
    return {
        "title": title.strip()[:200],
        "content": md_text,
        "url": url,
        "author": "",
        "published": "",
        "image": "",
        "strategy": f"{strategy}+markdown",
    }


# ---------------------------------------------------------------------------
# HTTP fetch primitive
# ---------------------------------------------------------------------------

def _fetch_html(
    url: str,
    user_agent: str,
    referer: str = "",
    extra_headers: Optional[Dict[str, str]] = None,
) -> str:
    """Low-level HTTP GET with custom UA/referer/headers. Empty string on failure."""
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)

    try:
        resp = http_client.get(
            url,
            headers=headers,
            timeout=paywall_timeout(),
            cookies={},  # explicit empty jar (== curl -b "")
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            return ""
        return resp.text or ""
    except requests.Timeout:
        logger.debug(f"[Paywall] timeout: {url}")
        return ""
    except requests.RequestException as e:
        logger.debug(f"[Paywall] fetch error: {e}")
        return ""


# ---------------------------------------------------------------------------
# Tier implementations
# ---------------------------------------------------------------------------

def _try_tier1_googlebot(url: str) -> Optional[dict]:
    """Tier 1: Googlebot UA for GOOGLEBOT_DOMAINS."""
    if not _match_domain(url, GOOGLEBOT_DOMAINS):
        return None
    logger.info(f"[Paywall] Tier 1 — Googlebot UA: {url}")
    html = _fetch_html(
        url,
        user_agent=UA_GOOGLEBOT,
        referer="https://www.google.com/",
        extra_headers={"X-Forwarded-For": "66.249.66.1"},
    )
    return _extract_article_from_html(url, html, "tier1.googlebot")


def _try_tier2_bingbot(url: str) -> Optional[dict]:
    """Tier 2: Bingbot UA for BINGBOT_DOMAINS."""
    if not _match_domain(url, BINGBOT_DOMAINS):
        return None
    logger.info(f"[Paywall] Tier 2 — Bingbot UA: {url}")
    html = _fetch_html(url, user_agent=UA_BINGBOT, referer="https://www.bing.com/")
    return _extract_article_from_html(url, html, "tier2.bingbot")


def _try_tier3_generic(url: str) -> Optional[dict]:
    """Tier 3: generic rotation over all PAYWALL_DOMAINS."""
    if not _match_domain(url, PAYWALL_DOMAINS):
        return None

    # 3a — Googlebot UA + X-Forwarded-For (applies even if not in GOOGLEBOT_DOMAINS)
    logger.info(f"[Paywall] Tier 3a — Googlebot generic: {url}")
    html = _fetch_html(
        url,
        user_agent=UA_GOOGLEBOT,
        referer="https://www.google.com/",
        extra_headers={"X-Forwarded-For": "66.249.66.1"},
    )
    got = _extract_article_from_html(url, html, "tier3a.googlebot")
    if got:
        return got

    # 3b — Bingbot UA
    logger.info(f"[Paywall] Tier 3b — Bingbot generic: {url}")
    html = _fetch_html(url, user_agent=UA_BINGBOT, referer="https://www.bing.com/")
    got = _extract_article_from_html(url, html, "tier3b.bingbot")
    if got:
        return got

    # 3c — Facebook Referer (for FACEBOOK_REF_DOMAINS)
    if _match_domain(url, FACEBOOK_REF_DOMAINS):
        logger.info(f"[Paywall] Tier 3c — Facebook Referer: {url}")
        html = _fetch_html(url, user_agent=UA_CHROME_MAC, referer="https://www.facebook.com/")
        got = _extract_article_from_html(url, html, "tier3c.facebook")
        if got:
            return got

    # 3d — Twitter (t.co) Referer
    logger.info(f"[Paywall] Tier 3d — Twitter Referer: {url}")
    html = _fetch_html(url, user_agent=UA_CHROME_MAC, referer="https://t.co/")
    got = _extract_article_from_html(url, html, "tier3d.twitter")
    if got:
        return got

    return None


def _try_tier4_amp(url: str) -> Optional[dict]:
    """Tier 4: AMP page variants (AMP paywalls are typically weaker)."""
    if not paywall_use_amp() or not _match_domain(url, AMP_DOMAINS):
        return None

    amp_suffixes = ("/amp", "?outputType=amp", ".amp.html", "?amp")
    for suf in amp_suffixes:
        if url.endswith(suf):
            continue
        amp_url = url + suf
        logger.info(f"[Paywall] Tier 4 — AMP variant: {amp_url}")
        html = _fetch_html(amp_url, user_agent=UA_CHROME_MAC, referer="https://www.google.com/")
        got = _extract_article_from_html(url, html, f"tier4.amp{suf}")
        if got:
            return got

    # .html → .amp.html / trailing-slash → /amp rewrite
    amp_url = re.sub(r"\.html$", ".amp.html", url)
    if amp_url == url and url.endswith("/"):
        amp_url = url + "amp"
    if amp_url != url:
        logger.info(f"[Paywall] Tier 4 — AMP rewrite: {amp_url}")
        html = _fetch_html(amp_url, user_agent=UA_CHROME_MAC, referer="https://www.google.com/")
        got = _extract_article_from_html(url, html, "tier4.amp.rewrite")
        if got:
            return got
    return None


def _try_tier5_eu_ip(url: str) -> Optional[dict]:
    """Tier 5: EU IP (X-Forwarded-For) for GDPR-gated content."""
    if not _match_domain(url, PAYWALL_DOMAINS):
        return None
    eu_ip = f"185.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}"
    logger.info(f"[Paywall] Tier 5 — EU IP {eu_ip}: {url}")
    html = _fetch_html(
        url,
        user_agent=UA_CHROME_MAC,
        referer="https://www.google.com/",
        extra_headers={"X-Forwarded-For": eu_ip},
    )
    return _extract_article_from_html(url, html, "tier5.eu_ip")


def _try_tier6_archive_today(url: str) -> Optional[dict]:
    """Tier 6: archive.today snapshot (CAPTCHA-aware)."""
    if not paywall_use_archive():
        return None
    archive_url = f"https://archive.today/newest/{url}"
    logger.info(f"[Paywall] Tier 6 — archive.today: {archive_url}")
    html = _fetch_html(archive_url, user_agent=UA_CHROME_MAC)
    if not html:
        return None
    if _is_captcha_page(html):
        logger.warning(
            f"[Paywall] Tier 6 — archive.ph needs human verification, skipping.\n"
            f"   Open in browser: {archive_url}"
        )
        return None
    return _extract_article_from_html(url, html, "tier6.archive_today")


def _try_tier7_google_cache(url: str) -> Optional[dict]:
    """Tier 7: Google web cache (deprecated but some articles linger)."""
    if not paywall_use_google_cache():
        return None
    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
    logger.info(f"[Paywall] Tier 7 — Google Cache: {cache_url}")
    html = _fetch_html(cache_url, user_agent=UA_CHROME_MAC)
    return _extract_article_from_html(url, html, "tier7.google_cache")


# ---------------------------------------------------------------------------
# Non-paywall JSON-LD fast path (for generic URLs, optional)
# ---------------------------------------------------------------------------

def _try_direct_jsonld(url: str) -> Optional[dict]:
    """Pre-emptive JSON-LD extraction for non-paywall domains.

    Fires before any paywall tier when PAYWALL_JSONLD_FOR_ALL is enabled.
    Much faster than Jina on SEO-heavy sites that embed articleBody.
    """
    logger.info(f"[Paywall] Tier 0 — Direct JSON-LD probe: {url}")
    html = _fetch_html(url, user_agent=UA_CHROME_MAC, referer="https://www.google.com/")
    if not html:
        return None
    ld = extract_jsonld_article(html)
    if not ld or not ld.get("articleBody") or len(ld["articleBody"]) < 500:
        return None
    # Skip pages that still contain paywall markers
    if _is_paywall_content(ld["articleBody"]):
        return None
    title = ld.get("headline") or extract_title_from_html(html) or urlparse(url).path
    return {
        "title": title.strip()[:200],
        "content": ld["articleBody"],
        "url": url,
        "author": ld.get("author", ""),
        "published": ld.get("datePublished", ""),
        "image": ld.get("image", ""),
        "strategy": "tier0.jsonld_direct",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def try_paywall_bypass(url: str) -> Optional[dict]:
    """Try to fetch URL via paywall-bypass cascade.

    Returns a dict compatible with fetch_via_jina() on success:
        {title, content, url, author, published, image, strategy}
    Returns None when all tiers fail — caller should fall back to Jina.

    Behavior:
        - Non-paywall domains: only Tier 0 (JSON-LD probe) runs if enabled.
        - Paywall domains: Tiers 1→7 attempt in order until one succeeds.
    """
    is_paywall = is_paywall_domain(url)

    # Tier 0 — direct JSON-LD probe (covers both paywall and generic)
    if paywall_jsonld_for_all() or is_paywall:
        got = _try_direct_jsonld(url)
        if got:
            logger.info(f"[Paywall] Success via {got['strategy']}: {got['title'][:60]}")
            return got

    # Only run bypass tiers for known paywall domains
    if not is_paywall:
        return None

    tiers = (
        _try_tier1_googlebot,
        _try_tier2_bingbot,
        _try_tier3_generic,
        _try_tier4_amp,
        _try_tier5_eu_ip,
        _try_tier6_archive_today,
        _try_tier7_google_cache,
    )
    for tier in tiers:
        try:
            got = tier(url)
        except Exception as e:
            logger.warning(f"[Paywall] Tier error: {tier.__name__} — {e}")
            continue
        if got:
            logger.info(f"[Paywall] Success via {got['strategy']}: {got['title'][:60]}")
            return got

    logger.warning(f"[Paywall] 所有付费墙绕过层都失败，改用 Jina 兜底：{url}")
    return None
