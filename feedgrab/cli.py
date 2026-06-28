# -*- coding: utf-8 -*-
"""
feedgrab CLI — fetch content from any platform.

Usage:
    feedgrab <url>                     # Fetch a single URL
    feedgrab <url1> <url2> ...         # Fetch multiple URLs
    feedgrab list                      # Show content statistics
"""

import sys
import os
import re
import shutil
import subprocess
import time
import asyncio
from pathlib import Path
from typing import Optional

# Fix Windows console encoding — force UTF-8 instead of GBK
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # Suppress noisy asyncio ProactorEventLoop "Exception ignored in __del__"
    # messages on Windows (harmless pipe cleanup from Playwright subprocess).
    # These bypass warnings module — must intercept via sys.unraisablehook.
    import warnings
    warnings.filterwarnings("ignore", category=ResourceWarning)
    _original_unraisablehook = sys.unraisablehook
    def _quiet_unraisablehook(unraisable):
        if isinstance(unraisable.exc_value, (OSError, ValueError)):
            return  # silence pipe/transport cleanup noise
        _original_unraisablehook(unraisable)
    sys.unraisablehook = _quiet_unraisablehook

from loguru import logger
from dotenv import load_dotenv
load_dotenv()

# Configure log level: default INFO, override with FEEDGRAB_LOG_LEVEL env var
_log_level = os.getenv("FEEDGRAB_LOG_LEVEL", "INFO").upper()
logger.remove()
logger.add(sys.stderr, level=_log_level)

from feedgrab.reader import UniversalReader
from feedgrab.service import FetchService


def _read_clipboard() -> str:
    """Read text from system clipboard (Windows/macOS/Linux)."""
    if sys.platform == "win32":
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip()
    elif sys.platform == "darwin":
        r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    else:
        for cmd in (["xclip", "-selection", "clipboard", "-o"],
                    ["xsel", "--clipboard", "--output"]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    return r.stdout.strip()
            except FileNotFoundError:
                continue
    return ""


def cmd_clip():
    """Read URL from clipboard and fetch it.

    Solves the PowerShell '&' operator issue — user copies a URL, runs
    'feedgrab clip', and the URL is read from clipboard without shell parsing.
    """
    text = _read_clipboard()
    if not text:
        print("❌ 剪贴板为空或无法读取")
        sys.exit(1)

    # Extract first URL from clipboard text
    url_match = re.search(r'https?://[^\s<>"\']+', text)
    if not url_match:
        print(f"❌ 剪贴板中未找到 URL: {text[:100]}")
        sys.exit(1)

    url = url_match.group(0)
    # Strip trailing punctuation that might be copied
    url = url.rstrip(".,;:!?")
    print(f"📋 从剪贴板读取: {url}")
    cmd_fetch([url])


def cmd_fetch(urls: list):
    """Fetch one or more URLs."""
    fetch_service = FetchService()

    async def run():
        if len(urls) == 1:
            # Bookmark batch mode: special output
            if "/i/bookmarks" in urls[0]:
                item = (await fetch_service.fetch_url(urls[0])).content
                print(f"\n\u2705 {item.content}")
                return

            # List tweets batch mode: special output
            if "/i/lists/" in urls[0] and "x.com" in urls[0]:
                item = (await fetch_service.fetch_url(urls[0])).content
                print(f"\n\u2705 {item.content}")
                return

            # XHS user notes batch mode or Twitter user tweets batch mode
            if ("/user/profile/" in urls[0] and "xiaohongshu.com" in urls[0]) or \
               ("/search_result" in urls[0] and "xiaohongshu.com" in urls[0]) or \
               ("x.com/" in urls[0] and "/status/" not in urls[0] and "/i/" not in urls[0]):
                item = (await fetch_service.fetch_url(urls[0])).content
                print(f"\n\u2705 {item.content}")
                return

            item = (await fetch_service.fetch_url(urls[0])).content
            print(f"\u2705 [{item.source_type.value}] {item.title[:60]}")
            print(f"   {item.url}")
            print(f"   {item.content[:200]}...")
        else:
            results = await fetch_service.fetch_urls(urls)
            success_count = 0
            for result in results:
                if getattr(result, "success", True) and result.content is not None:
                    item = result.content
                    print(f"\u2705 [{item.source_type.value}] {item.title[:60]}")
                    success_count += 1
                    continue

                request = getattr(result, "request", None)
                error = getattr(result, "error", None) or {}
                platform = getattr(result, "platform", "") or "unknown"
                url = getattr(request, "url", "")
                message = error.get("message") if isinstance(error, dict) else str(error)
                print(f"\u274c 失败 [{platform}] {url}: {message or '未知错误'}")

            print(f"\n\U0001f4e6 已抓取 {success_count}/{len(urls)} 个 URL")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n\u23f9 已取消")
    except Exception as e:
        print(f"\u274c {e}")
        sys.exit(1)


def cmd_list():
    """Show content statistics by scanning output directories."""
    vault = os.getenv("OBSIDIAN_VAULT", "").strip()
    output_dir = os.getenv("OUTPUT_DIR", "").strip()

    if vault:
        base_dir = Path(vault)
    elif output_dir:
        base_dir = Path(output_dir)
    else:
        print("\u274c OUTPUT_DIR \u6216 OBSIDIAN_VAULT \u672a\u914d\u7f6e")
        return

    if not base_dir.exists():
        print(f"\u274c \u76ee\u5f55\u4e0d\u5b58\u5728: {base_dir}")
        return

    # Platform emoji map
    emoji_map = {
        "X": "\U0001f426", "XHS": "\U0001f4d5", "Bilibili": "\U0001f3ac",
        "WeChat": "\U0001f4ac", "YouTube": "\u25b6\ufe0f", "Telegram": "\U0001f4e2",
        "RSS": "\U0001f4f0", "Manual": "\u270f\ufe0f",
    }

    total = 0
    platform_stats = []

    # Scan each platform directory
    for platform_dir in sorted(base_dir.iterdir()):
        if not platform_dir.is_dir():
            continue

        name = platform_dir.name
        # Count .md files in this platform (non-recursive first level)
        top_level_mds = list(platform_dir.glob("*.md"))
        sub_dirs = []

        for sub in sorted(platform_dir.iterdir()):
            if sub.is_dir() and sub.name != "index":
                count = len(list(sub.glob("*.md")))
                if count > 0:
                    sub_dirs.append((sub.name, count))

        platform_total = len(top_level_mds) + sum(c for _, c in sub_dirs)
        if platform_total == 0:
            continue

        total += platform_total
        emoji = emoji_map.get(name, "\U0001f4c4")
        platform_stats.append((name, emoji, platform_total, top_level_mds, sub_dirs))

    if not platform_stats:
        print("\U0001f4e6 \u8fd8\u6ca1\u6709\u62d3\u53d6\u4efb\u4f55\u5185\u5bb9")
        return

    print(f"\U0001f4e6 feedgrab \u5185\u5bb9\u7edf\u8ba1 ({base_dir})\n")

    for name, emoji, platform_total, top_mds, sub_dirs in platform_stats:
        print(f"  {emoji} {name}: {platform_total} \u7bc7")

        if top_mds and sub_dirs:
            # Has both top-level files and subdirectories
            print(f"     (root)  {len(top_mds)} \u7bc7")
        for sub_name, count in sub_dirs:
            print(f"     {sub_name}/  {count} \u7bc7")

    print(f"\n  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    print(f"  \u603b\u8ba1: {total} \u7bc7")


def cmd_login(platform: str, headless: bool = False):
    """Open browser for manual login to a platform."""
    from feedgrab.login import login
    login(platform, headless=headless)


def cmd_reset(folder_name: str):
    """Reset a subfolder: delete .md files and remove their item_ids from dedup index."""
    vault = os.getenv("OBSIDIAN_VAULT", "").strip()
    output_dir = os.getenv("OUTPUT_DIR", "").strip()

    if vault:
        base_dir = Path(vault)
    elif output_dir:
        base_dir = Path(output_dir)
    else:
        print("\u274c OUTPUT_DIR \u6216 OBSIDIAN_VAULT \u672a\u914d\u7f6e")
        return

    # Find matching subfolder under any platform directory
    target = None
    for platform_dir in base_dir.iterdir():
        if not platform_dir.is_dir():
            continue
        candidate = platform_dir / folder_name
        if candidate.is_dir():
            target = candidate
            break

    if not target:
        print(f"\u274c \u627e\u4e0d\u5230\u76ee\u5f55: {folder_name}")
        # Show available folders
        print("\n\u53ef\u7528\u76ee\u5f55:")
        for platform_dir in sorted(base_dir.iterdir()):
            if not platform_dir.is_dir():
                continue
            for sub in sorted(platform_dir.iterdir()):
                if sub.is_dir() and sub.name != "index":
                    count = len(list(sub.glob("*.md")))
                    if count > 0:
                        print(f"  {sub.name}/  ({count} \u7bc7)")
        return

    # Scan .md files and extract item_ids from front matter
    md_files = list(target.glob("*.md"))
    if not md_files:
        print(f"\u274c {folder_name}/ \u4e2d\u6ca1\u6709 .md \u6587\u4ef6")
        return

    item_ids = []
    for md_file in md_files:
        try:
            with open(md_file, "r", encoding="utf-8") as f:
                in_frontmatter = False
                for line in f:
                    stripped = line.strip()
                    if stripped == "---":
                        if not in_frontmatter:
                            in_frontmatter = True
                            continue
                        else:
                            break  # end of front matter
                    if in_frontmatter and stripped.startswith("item_id:"):
                        iid = stripped.split(":", 1)[1].strip()
                        if iid:
                            item_ids.append(iid)
                        break
        except OSError:
            pass

    print(f"\U0001f4c1 {folder_name}/")
    print(f"   {len(md_files)} \u4e2a .md \u6587\u4ef6")
    print(f"   {len(item_ids)} \u4e2a item_id \u5c06\u4ece\u53bb\u91cd\u7d22\u5f15\u4e2d\u79fb\u9664")
    confirm = input("\n\u786e\u8ba4\u91cd\u7f6e? (y/N) ")
    if confirm.lower() != "y":
        print("\u274f \u5df2\u53d6\u6d88")
        return

    # Remove from dedup index (platform-aware)
    from feedgrab.utils.dedup import load_index, save_index
    platform_name = target.parent.name  # "X" or "XHS"
    platform_key = platform_name if platform_name in ("X", "XHS") else "X"
    index = load_index(platform=platform_key)
    removed = 0
    for iid in item_ids:
        if iid in index:
            del index[iid]
            removed += 1
    save_index(index, platform=platform_key)

    # Delete .md files
    deleted = 0
    for md_file in md_files:
        try:
            md_file.unlink()
            deleted += 1
        except OSError:
            pass

    print(f"\n\u2705 \u91cd\u7f6e\u5b8c\u6210:")
    print(f"   \u5220\u9664 {deleted} \u4e2a .md \u6587\u4ef6")
    print(f"   移除 {removed} 个去重索引条目")
    print(f"   现在可以重新拓取了")


def cmd_clean_index(skip_confirm: bool = False):
    """Clean up batch records and cache files from index directories.

    Preserves item_id_url.json (global dedup index), removes everything else:
    - status_*.json       (UserTweets batch records)
    - api_status_*.json   (API batch records)
    - bookmarks_*.json    (Bookmarks batch records)
    - list_*.json         (List batch records)
    - .api_discovery_*.jsonl (API checkpoint caches)
    """
    from feedgrab.utils.dedup import get_index_path

    # Collect index dirs from all platforms
    platforms = ["X", "XHS"]
    cleaned_files = 0
    cleaned_bytes = 0
    index_dirs_checked = []

    for plat in platforms:
        index_dir = get_index_path(platform=plat).parent
        if not index_dir.exists():
            continue
        index_dirs_checked.append(index_dir)

        for f in index_dir.iterdir():
            if f.name == "item_id_url.json":
                continue  # preserve global dedup index
            if f.is_file():
                size = f.stat().st_size
                cleaned_files += 1
                cleaned_bytes += size

    if cleaned_files == 0:
        print("✅ 索引目录已经很干净，无需清理")
        return

    # Show summary before confirming
    print(f"🗂  扫描到 {cleaned_files} 个可清理文件 ({cleaned_bytes / 1024 / 1024:.1f} MB)")
    print(f"   保留: item_id_url.json (全局去重索引)")
    print(f"   清理: 批量记录 + 断点缓存")
    for d in index_dirs_checked:
        print(f"   目录: {d}")

    confirm = "y" if skip_confirm else input("\n确认清理? (y/N) ")
    if confirm.lower() != "y":
        print("✗ 已取消")
        return

    # Delete files
    deleted = 0
    freed = 0
    for plat in platforms:
        index_dir = get_index_path(platform=plat).parent
        if not index_dir.exists():
            continue
        for f in index_dir.iterdir():
            if f.name == "item_id_url.json":
                continue
            if f.is_file():
                size = f.stat().st_size
                try:
                    f.unlink()
                    deleted += 1
                    freed += size
                except OSError as e:
                    print(f"   ⚠ 无法删除 {f.name}: {e}")

    print(f"\n✅ 清理完成: 删除 {deleted} 个文件，释放 {freed / 1024 / 1024:.1f} MB")


def cmd_detect_ua():
    """Detect real Chrome User-Agent and save to .env file."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\u274c 未安装 Playwright。请运行：\n"
              '   pip install "feedgrab[browser]"\n'
              "   playwright install chromium")
        return

    print("\U0001f50d 正在检测本机 Chrome User-Agent...")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                channel="chrome",
            )
            page = browser.new_page()
            ua = page.evaluate("navigator.userAgent")
            browser.close()
    except Exception as e:
        print(f"\u274c UA 检测失败：{e}")
        print("   正在回退到不指定 channel='chrome' 的检测方式...")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                ua = page.evaluate("navigator.userAgent")
                browser.close()
        except Exception as e2:
            print(f"\u274c 检测失败：{e2}")
            return

    # Headless mode reports "HeadlessChrome" — normalize to "Chrome"
    ua = ua.replace("HeadlessChrome", "Chrome")

    print(f"\n   检测结果：{ua}")

    # Write to .env
    env_path = Path.cwd() / ".env"
    key_line = f"BROWSER_USER_AGENT={ua}"

    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        if "BROWSER_USER_AGENT=" in content:
            # Replace existing line
            import re
            content = re.sub(
                r"^#?\s*BROWSER_USER_AGENT=.*$",
                key_line,
                content,
                flags=re.MULTILINE,
            )
            env_path.write_text(content, encoding="utf-8")
            print(f"\n\u2705 已更新 {env_path} 中的 BROWSER_USER_AGENT")
        else:
            # Append
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f"\n# Auto-detected by: feedgrab detect-ua\n{key_line}\n")
            print(f"\n\u2705 已追加 BROWSER_USER_AGENT 到 {env_path}")
    else:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"# Auto-detected by: feedgrab detect-ua\n{key_line}\n")
        print(f"\n\u2705 已创建 {env_path}，并写入 BROWSER_USER_AGENT")

    print("   后续浏览器交互将使用这个 UA。")


def cmd_doctor(platform: str = "all"):
    """Run diagnostic checks on feedgrab integrations.

    platform: 'all' | 'x' | 'xhs' | 'mpweixin'
    """
    import time
    from pathlib import Path
    from feedgrab.config import get_data_dir, get_session_dir

    ok_count = 0
    warn_count = 0
    fail_count = 0
    step = 0

    def ok(msg):
        nonlocal ok_count
        ok_count += 1
        print(f"  \u2705 {msg}")

    def warn(msg):
        nonlocal warn_count
        warn_count += 1
        print(f"  \u26a0\ufe0f  {msg}")

    def fail(msg):
        nonlocal fail_count
        fail_count += 1
        print(f"  \u274c {msg}")

    def section(title):
        nonlocal step
        step += 1
        print(f"\n[{step}] {title}")

    # ── Shared: browser engine ───────────────────────────────────────
    def check_browser():
        section("Browser engine")
        try:
            import patchright  # noqa: F401
            ok("patchright (stealth browser)")
        except ImportError:
            try:
                import playwright  # noqa: F401
                ok("playwright (non-stealth)")
                warn("patchright recommended — pip install patchright")
            except ImportError:
                warn("No browser engine — pip install patchright")

    # ── Twitter/X ────────────────────────────────────────────────────
    def check_x():
        section("Twitter/X dependencies")
        for mod, desc, install in [
            ("curl_cffi", "TLS fingerprint", "pip install curl_cffi"),
            ("x_client_transaction", "transaction-id signing", "pip install XClientTransaction"),
            ("bs4", "HTML parsing", "pip install beautifulsoup4"),
            ("browserforge", "browser fingerprint", "pip install browserforge"),
        ]:
            try:
                __import__(mod)
                ok(f"{mod} ({desc})")
            except ImportError:
                warn(f"{mod} — {install}")

        section("Twitter Cookie")
        try:
            from feedgrab.fetchers.twitter_cookies import load_twitter_cookies
            cookies = load_twitter_cookies()
            if cookies and cookies.get("auth_token") and cookies.get("ct0"):
                tok = cookies["auth_token"]
                ok(f"auth_token={tok[:8]}...  ct0=已存在")
            else:
                fail("未找到有效 Cookie，请运行：feedgrab login twitter")
        except Exception as e:
            fail(f"Cookie 加载错误：{e}")

        section("queryId 解析")
        try:
            from feedgrab.fetchers.twitter_graphql import resolve_query_ids
            t0 = time.time()
            ids = resolve_query_ids()
            elapsed = time.time() - t0
            if ids:
                ok(f"已解析 {len(ids)} 个 queryId，用时 {elapsed:.1f}s")
                for name in ["TweetDetail", "SearchTimeline", "UserTweets", "Bookmarks"]:
                    qid = ids.get(name, "?")
                    ok(f"  {name}: {qid}")
            else:
                fail("未解析到 queryId")
        except Exception as e:
            fail(f"queryId 解析失败：{e}")

        section("x-client-transaction-id")
        try:
            from feedgrab.fetchers.twitter_graphql import _get_transaction_id
            tid = _get_transaction_id("GET", "/i/api/graphql/test")
            if tid:
                ok(f"已生成：{tid[:20]}...")
            else:
                warn("生成失败，SearchTimeline 可能返回 404")
        except Exception as e:
            warn(f"错误：{e}")

        section("Twitter 网络")
        try:
            from feedgrab.utils.http_client import get as http_get
            t0 = time.time()
            resp = http_get("https://x.com", timeout=10)
            elapsed = time.time() - t0
            if resp.status_code == 200:
                ok(f"x.com 可访问（{elapsed:.1f}s）")
            else:
                warn(f"x.com status {resp.status_code} ({elapsed:.1f}s)")
        except Exception as e:
            fail(f"x.com 不可访问：{e}")

        try:
            from feedgrab.utils.http_client import get as http_get
            t0 = time.time()
            resp = http_get(
                "https://raw.githubusercontent.com/fa0311/twitter-openapi/"
                "main/src/config/placeholder.json",
                timeout=10,
            )
            elapsed = time.time() - t0
            if resp.status_code == 200:
                ok(f"社区 queryId 源可访问（{elapsed:.1f}s）")
            else:
                warn(f"社区源状态码：{resp.status_code}")
        except Exception as e:
            warn(f"社区源不可访问：{e}")

    # ── Xiaohongshu ──────────────────────────────────────────────────
    def check_xhs():
        check_browser()

        section("小红书 API（xhshow）")
        try:
            from xhshow import CryptoConfig
            ok("xhshow 已安装")
        except ImportError:
            warn("未安装 xhshow，API 模式不可用（pip install xhshow）")

        section("小红书登录态")
        from feedgrab.fetchers.browser import SESSION_DIR
        session_path = Path(SESSION_DIR) / "xhs"
        if session_path.exists():
            ok(f"已找到登录态：{session_path}")
            # Check for key cookies
            try:
                session_json = Path(SESSION_DIR) / "xhs.json"
                if session_json.exists():
                    import json
                    data = json.loads(session_json.read_text(encoding="utf-8"))
                    cookies = {c["name"]: c["value"] for c in data.get("cookies", [])
                               if "xiaohongshu.com" in c.get("domain", "")}
                    if cookies.get("a1"):
                        ok(f"Cookie a1 已存在（关键 Cookie：{len(cookies)} 个）")
                    else:
                        warn("缺少 Cookie a1，登录态可能无效。请运行：feedgrab login xhs")
            except Exception:
                pass
        else:
            warn("未找到登录态，请运行：feedgrab login xhs")

        section("小红书 API 连通性")
        try:
            from feedgrab.utils.http_client import get as http_get
            t0 = time.time()
            resp = http_get("https://edith.xiaohongshu.com", timeout=10)
            elapsed = time.time() - t0
            ok(f"edith.xiaohongshu.com 可访问（{elapsed:.1f}s，状态码 {resp.status_code}）")
        except Exception as e:
            warn(f"edith.xiaohongshu.com 不可访问：{e}")

        section("小红书网络")
        try:
            from feedgrab.utils.http_client import get as http_get
            t0 = time.time()
            resp = http_get("https://www.xiaohongshu.com", timeout=10)
            elapsed = time.time() - t0
            if resp.status_code == 200:
                ok(f"xiaohongshu.com 可访问（{elapsed:.1f}s）")
            else:
                warn(f"xiaohongshu.com status {resp.status_code} ({elapsed:.1f}s)")
        except Exception as e:
            fail(f"xiaohongshu.com 不可访问：{e}")

    # ── WeChat MP ────────────────────────────────────────────────────
    def check_mpweixin():
        check_browser()

        section("微信公众号登录态")
        session_path = get_session_dir() / "wechat.json"
        if session_path.exists():
            ok(f"已找到登录态：{session_path}")
            # Check age
            age_hours = (time.time() - session_path.stat().st_mtime) / 3600
            if age_hours > 96:
                warn(f"登录态已保存 {age_hours:.0f} 小时，可能已过期（通常约 4 天有效）。"
                     "请运行：feedgrab login wechat")
            else:
                ok(f"登录态时长：{age_hours:.0f} 小时（通常约 96 小时有效）")
        else:
            fail("未找到登录态，请运行：feedgrab login wechat")

        section("微信公众号网络")
        try:
            from feedgrab.utils.http_client import get as http_get
            t0 = time.time()
            resp = http_get("https://mp.weixin.qq.com", timeout=10)
            elapsed = time.time() - t0
            if resp.status_code == 200:
                ok(f"mp.weixin.qq.com 可访问（{elapsed:.1f}s）")
            else:
                ok(f"mp.weixin.qq.com 已响应（{elapsed:.1f}s，状态码 {resp.status_code}）")
        except Exception as e:
            fail(f"mp.weixin.qq.com 不可访问：{e}")

    # ── Feishu / Lark ─────────────────────────────────────────────
    def check_feishu():
        check_browser()

        section("飞书 Open API（lark-oapi）")
        try:
            import lark_oapi  # noqa: F401
            ok("lark-oapi 已安装")
            from feedgrab.config import feishu_app_id, feishu_app_secret
            if feishu_app_id() and feishu_app_secret():
                ok(f"FEISHU_APP_ID={feishu_app_id()[:6]}...  FEISHU_APP_SECRET=已配置")
            else:
                warn("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，Tier 0 API 不可用")
        except ImportError:
            warn("未安装 lark-oapi，Tier 0 API 不可用（pip install lark-oapi）")

        section("飞书登录态")
        session_path = Path(get_session_dir()) / "feishu.json"
        if session_path.exists():
            ok(f"已找到登录态：{session_path}")
            age_hours = (time.time() - session_path.stat().st_mtime) / 3600
            ok(f"登录态时长：{age_hours:.0f} 小时")
        else:
            warn("未找到登录态，请运行：feedgrab login feishu（Tier 1 Playwright 需要）")

        section("飞书配置")
        from feedgrab.config import feishu_download_images, feishu_page_load_timeout
        ok(f"FEISHU_DOWNLOAD_IMAGES={feishu_download_images()}")
        ok(f"FEISHU_PAGE_LOAD_TIMEOUT={feishu_page_load_timeout()}ms")

        section("飞书网络")
        try:
            from feedgrab.utils.http_client import get as http_get
            t0 = time.time()
            resp = http_get("https://my.feishu.cn", timeout=10)
            elapsed = time.time() - t0
            if resp.status_code < 400:
                ok(f"my.feishu.cn 可访问（{elapsed:.1f}s，状态码 {resp.status_code}）")
            else:
                warn(f"my.feishu.cn status {resp.status_code} ({elapsed:.1f}s)")
        except Exception as e:
            fail(f"my.feishu.cn 不可访问：{e}")

    # ── Dispatch ─────────────────────────────────────────────────────
    platform = platform.lower()
    targets = {
        "x": ("Twitter/X", check_x),
        "twitter": ("Twitter/X", check_x),
        "xhs": ("Xiaohongshu", check_xhs),
        "mpweixin": ("WeChat MP", check_mpweixin),
        "wechat": ("WeChat MP", check_mpweixin),
        "feishu": ("Feishu/Lark", check_feishu),
        "lark": ("Feishu/Lark", check_feishu),
    }

    if platform == "all":
        print("feedgrab doctor — 全量诊断\n")
        check_browser()
        check_x()
        check_xhs()
        check_mpweixin()
        check_feishu()
    elif platform in targets:
        label, fn = targets[platform]
        print(f"feedgrab doctor {platform} — {label} 诊断\n")
        fn()
    else:
        print(f"\u274c 未知平台：{platform}")
        print("用法：feedgrab doctor [x | xhs | mpweixin | feishu]")
        return

    # ── Summary ──────────────────────────────────────────────────────
    print(f"\n{'=' * 50}")
    print(f"  结果：通过 {ok_count} 项，警告 {warn_count} 项，错误 {fail_count} 项")
    if fail_count == 0 and warn_count == 0:
        print("  全部检查通过！")
    elif fail_count == 0:
        print("  核心功能正常，部分可选能力缺失。")
    else:
        print("  部分检查失败，请按上方提示处理。")


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _set_env_value(env_path: Path, key: str, value: str):
    """Set or update a key=value in .env file."""
    content = env_path.read_text(encoding="utf-8")
    pattern = rf"^#?\s*{re.escape(key)}=.*$"
    replacement = f"{key}={value}"
    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, replacement, content, count=1, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{replacement}\n"
    env_path.write_text(content, encoding="utf-8")


def _get_env_value(content: str, key: str) -> str:
    """Extract a key's value from .env content string (strips inline comments)."""
    m = re.search(rf"^{re.escape(key)}=(.*)$", content, re.MULTILINE)
    if not m:
        return ""
    val = m.group(1).strip()
    # Strip inline comments: OUTPUT_DIR=./output  # comment → ./output
    if "  #" in val:
        val = val.split("  #")[0].strip()
    return val


def _session_age_str(session_file: Path) -> str:
    """Return human-readable age of a session file."""
    age_sec = time.time() - session_file.stat().st_mtime
    if age_sec < 3600:
        return f"{int(age_sec / 60)} 分钟前"
    elif age_sec < 86400:
        return f"{int(age_sec / 3600)} 小时前"
    else:
        return f"{int(age_sec / 86400)} 天前"


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------

def _step_check_env():
    """[1/5] Check Python, feedgrab, Playwright, Chromium."""
    print("\n[1/5] 检查运行环境...")

    # Python
    print(f"  \u2705 Python {sys.version.split()[0]}")

    # feedgrab
    try:
        from importlib.metadata import version as pkg_version
        v = pkg_version("feedgrab")
        print(f"  \u2705 feedgrab {v}")
    except Exception:
        print("  \u2705 feedgrab (dev mode)")

    # Playwright
    pw_ok = False
    try:
        import playwright  # noqa: F401
        print("  \u2705 Playwright 已安装")
        pw_ok = True
    except ImportError:
        print("  \u26a0\ufe0f  Playwright 未安装")
        ans = input("     \u2192 是否自动安装？(Y/n) ").strip().lower()
        if ans in ("", "y", "yes"):
            import subprocess
            print("     \u2192 正在安装 playwright...")
            subprocess.run([sys.executable, "-m", "pip", "install", "playwright"],
                           check=True, capture_output=True)
            print("     \u2192 正在安装 Chromium 浏览器...")
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                           check=True, capture_output=True)
            print("  \u2705 Playwright + Chromium 已就绪")
            pw_ok = True
        else:
            print("  \u23ed 已跳过（浏览器功能不可用）")

    if not pw_ok:
        return

    # Check Chromium is installed
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel="chrome")
            browser.close()
        print("  \u2705 Chrome 浏览器可用")
    except Exception:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                browser.close()
            print("  \u2705 Chromium 浏览器可用")
        except Exception:
            print("  \u26a0\ufe0f  Chromium 未安装")
            ans = input("     \u2192 是否自动安装？(Y/n) ").strip().lower()
            if ans in ("", "y", "yes"):
                import subprocess
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                               check=True, capture_output=True)
                print("  \u2705 Chromium 已安装")
            else:
                print("  \u23ed 已跳过")


def _step_create_dotenv():
    """[2/5] Create .env and set OUTPUT_DIR."""
    print("\n[2/5] 配置文件...")

    env_path = Path.cwd() / ".env"
    example_path = Path.cwd() / ".env.example"

    if env_path.exists():
        print("  \u2705 .env 文件已存在")
    elif example_path.exists():
        shutil.copy(example_path, env_path)
        print("  \u2705 已从 .env.example 创建 .env")
    else:
        env_path.touch()
        print("  \u2705 已创建空 .env")

    # Check OUTPUT_DIR
    content = env_path.read_text(encoding="utf-8")
    current = _get_env_value(content, "OUTPUT_DIR")
    if current and current != "./output":
        print(f"  \u2705 OUTPUT_DIR = {current}")
    else:
        default = "./output"
        ans = input(f"  请输入内容输出目录 (直接回车使用默认 {default}): ").strip()
        output_dir = ans or default
        _set_env_value(env_path, "OUTPUT_DIR", output_dir)
        print(f"  \u2705 OUTPUT_DIR = {output_dir}")


def _step_detect_ua():
    """[3/5] Detect UA — reuse cmd_detect_ua logic."""
    print("\n[3/5] 检测浏览器指纹...")

    # Reload .env to check current value
    from dotenv import load_dotenv
    load_dotenv(override=True)
    current_ua = os.getenv("BROWSER_USER_AGENT", "").strip()
    if current_ua:
        short = current_ua.split("Chrome/")[1][:10] if "Chrome/" in current_ua else current_ua[-30:]
        print(f"  \u2705 已配置: Chrome/{short}...")
        return

    # Run detection
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("  \u23ed Playwright 未安装，跳过 UA 检测")
        return

    cmd_detect_ua()


_SETUP_PLATFORMS = [
    ("xhs", "小红书", "请在弹出的浏览器窗口中扫码登录"),
    ("twitter", "Twitter/X", "请在弹出的浏览器窗口中登录"),
    ("wechat", "微信公众号", "请在弹出的浏览器窗口中登录"),
]


def _step_platform_login():
    """[4/5] Interactive platform login."""
    print("\n[4/5] 平台登录")

    from feedgrab.config import get_session_dir

    session_dir = get_session_dir()
    canonical_map = {"xhs": "xhs", "twitter": "twitter", "wechat": "wechat"}

    for key, name, desc in _SETUP_PLATFORMS:
        canonical = canonical_map[key]
        session_file = session_dir / f"{canonical}.json"

        if session_file.exists():
            age = _session_age_str(session_file)
            print(f"  \u2705 {name} session 已存在 ({age})")
            ans = input(f"     \u2192 重新登录？(y/N) ").strip().lower()
            if ans not in ("y", "yes"):
                continue
        else:
            ans = input(f"  \U0001f511 登录{name}？(Y/n) ").strip().lower()
            if ans not in ("", "y", "yes"):
                print(f"     \u23ed 已跳过")
                continue

        print(f"     \U0001f310 {desc}...")
        try:
            from feedgrab.login import login
            login(key, headless=False)
        except Exception as e:
            print(f"     \u274c 登录失败: {e}")


def _step_enable_features():
    """[5/5] Enable batch features based on available sessions."""
    print("\n[5/5] 启用批量功能")

    from feedgrab.config import get_session_dir

    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return

    session_dir = get_session_dir()

    # XHS batch
    xhs_session = session_dir / "xhs.json"
    if xhs_session.exists():
        content = env_path.read_text(encoding="utf-8")
        xhs_enabled = _get_env_value(content, "XHS_USER_NOTES_ENABLED")
        if xhs_enabled.lower() == "true":
            print("  \u2705 小红书批量抓取已启用")
        else:
            ans = input("  启用小红书批量抓取（作者主页 + 搜索）？(Y/n) ").strip().lower()
            if ans in ("", "y", "yes"):
                _set_env_value(env_path, "XHS_USER_NOTES_ENABLED", "true")
                _set_env_value(env_path, "XHS_SEARCH_ENABLED", "true")
                print("  \u2705 XHS_USER_NOTES_ENABLED=true")
                print("  \u2705 XHS_SEARCH_ENABLED=true")
            else:
                print("  \u23ed 已跳过")
    else:
        print("  \u23ed 小红书未登录，跳过批量功能配置")

    # Twitter batch
    twitter_session = session_dir / "twitter.json"
    if twitter_session.exists():
        content = env_path.read_text(encoding="utf-8")
        x_enabled = _get_env_value(content, "X_BOOKMARKS_ENABLED")
        if x_enabled.lower() == "true":
            print("  \u2705 Twitter 批量抓取已启用")
        else:
            ans = input("  启用 Twitter 批量抓取（书签 + 账号推文）？(Y/n) ").strip().lower()
            if ans in ("", "y", "yes"):
                _set_env_value(env_path, "X_BOOKMARKS_ENABLED", "true")
                _set_env_value(env_path, "X_USER_TWEETS_ENABLED", "true")
                print("  \u2705 X_BOOKMARKS_ENABLED=true")
                print("  \u2705 X_USER_TWEETS_ENABLED=true")
            else:
                print("  \u23ed 已跳过")
    else:
        print("  \u23ed Twitter 未登录，跳过批量功能配置")


def cmd_setup():
    """Interactive first-time deployment guide."""
    print("\n\U0001f4e6 feedgrab 首次部署引导")
    print("=" * 40)

    _step_check_env()
    _step_create_dotenv()
    _step_detect_ua()
    _step_platform_login()
    _step_enable_features()

    print("\n" + "=" * 40)
    print("\U0001f389 部署完成！\n")
    print("试试：")
    print('  feedgrab https://www.xiaohongshu.com/explore/xxx')
    print('  feedgrab "https://www.xiaohongshu.com/search_result?keyword=..."')
    print('  feedgrab list')
    print()


def _youtube_resolve_meta(url: str) -> dict:
    """Get YouTube video metadata for filename/directory construction.

    Returns dict with keys: video_id, author, pub, title, filename_prefix, output_dir.
    """
    import re as _re

    # Resolve output base directory
    vault_path = os.getenv("OBSIDIAN_VAULT", "")
    output_dir_env = os.getenv("OUTPUT_DIR", "")
    if vault_path:
        base_dir = os.path.join(vault_path, "YouTube")
    elif output_dir_env:
        base_dir = os.path.join(output_dir_env, "YouTube")
    else:
        base_dir = os.path.expanduser("~/Downloads/YouTube")

    video_id = ""
    match = _re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    if match:
        video_id = match.group(1)

    author = ""
    pub = ""
    title = ""
    filename_prefix = ""

    if video_id and os.getenv("YOUTUBE_API_KEY", "").strip():
        try:
            from feedgrab.fetchers.youtube_search import get_single_video
            meta = get_single_video(video_id)
            if meta:
                author = meta.get("channel_title", "").strip()
                pub = meta.get("published_at", "")[:10]
                title = meta.get("title", "").strip()
                # Collapse whitespace in author (match _sanitize_filename behavior)
                author = _re.sub(r'\s+', ' ', author).strip()
                parts = []
                if author:
                    parts.append(author)
                if pub:
                    parts.append(pub)
                prefix = "_".join(parts)
                safe_title = _re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', title)[:80]
                filename_prefix = f"{prefix}：{safe_title}" if prefix else safe_title
        except Exception:
            pass

    # Add author subdirectory: YouTube/{author}/
    if author:
        safe_author = _re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', author).strip('. ')
        safe_author = _re.sub(r'\s+', ' ', safe_author).strip()
        output_dir = os.path.join(base_dir, safe_author)
    else:
        output_dir = base_dir

    return {
        "video_id": video_id,
        "author": author,
        "pub": pub,
        "title": title,
        "filename_prefix": filename_prefix,
        "output_dir": output_dir,
    }


def cmd_hackernews_list(args: list):
    """Fetch HackerNews list stories and save each item as Markdown."""
    import asyncio

    category = args[0].strip().lower()
    if category == "newest":
        category = "new"

    limit = None
    if "--limit" in args:
        idx = args.index("--limit")
        if idx + 1 >= len(args):
            print("❌ --limit requires a number")
            sys.exit(1)
        try:
            limit = int(args[idx + 1])
        except ValueError:
            print("❌ --limit must be a number")
            sys.exit(1)
    elif "-n" in args:
        idx = args.index("-n")
        if idx + 1 >= len(args):
            print("❌ -n requires a number")
            sys.exit(1)
        try:
            limit = int(args[idx + 1])
        except ValueError:
            print("❌ -n must be a number")
            sys.exit(1)

    async def run():
        from feedgrab.config import hn_enabled, hn_list_limit
        if not hn_enabled():
            raise RuntimeError("HackerNews 抓取已禁用，请设置 HN_ENABLED=true")
        from feedgrab.fetchers.hackernews import fetch_hackernews_list
        from feedgrab.schema import from_hackernews
        from feedgrab.utils.storage import save_to_markdown

        actual_limit = limit or hn_list_limit()
        print(f"\n📰 HackerNews {category}: fetching {actual_limit} items...")
        items = await fetch_hackernews_list(category, limit=actual_limit)
        saved = 0
        for data in items:
            content = from_hackernews(data)
            content.category = category
            path = save_to_markdown(content)
            if path:
                saved += 1
        print(f"✅ HackerNews {category}: saved {saved}/{len(items)} items")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n⏹ 已取消")
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)



def _parse_named_int(args: list, name: str) -> Optional[int]:
    if name in args:
        idx = args.index(name)
        if idx + 1 >= len(args):
            print(f"❌ {name} requires a number")
            sys.exit(1)
        try:
            return int(args[idx + 1])
        except ValueError:
            print(f"❌ {name} must be a number")
            sys.exit(1)
    return None


def _parse_named_str(args: list, name: str) -> Optional[str]:
    if name in args:
        idx = args.index(name)
        if idx + 1 >= len(args):
            print(f"❌ {name} requires a value")
            sys.exit(1)
        return args[idx + 1]
    return None


def cmd_medium_user(args: list):
    """Fetch a Medium user's recent articles via RSS + tier chain."""
    import asyncio
    handle = args[0].strip()
    limit = _parse_named_int(args, "--limit") or _parse_named_int(args, "-n")

    async def run():
        from feedgrab.config import medium_enabled, medium_user_limit
        if not medium_enabled():
            raise RuntimeError("Medium 抓取已禁用，请设置 MEDIUM_ENABLED=true")
        from feedgrab.fetchers.medium import fetch_medium_user
        from feedgrab.schema import from_medium
        from feedgrab.utils.storage import save_to_markdown

        n = limit or medium_user_limit()
        print(f"\n📰 Medium 用户 {handle}: 抓取 {n} 篇...")
        items = await fetch_medium_user(handle, limit=n)
        saved = 0
        category = handle.lstrip("@")
        for data in items:
            content = from_medium(data)
            content.category = f"user_{category}"
            path = save_to_markdown(content)
            if path is None:
                continue
            saved += 1
        print(f"✅ Medium 用户 {handle}: 保存 {saved}/{len(items)} 篇")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n⏹ 已取消")
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


def cmd_medium_pub(args: list):
    """Fetch a Medium publication's recent articles via RSS + tier chain."""
    import asyncio
    slug = args[0].strip().strip("/")
    limit = _parse_named_int(args, "--limit") or _parse_named_int(args, "-n")

    async def run():
        from feedgrab.config import medium_enabled, medium_user_limit
        if not medium_enabled():
            raise RuntimeError("Medium 抓取已禁用，请设置 MEDIUM_ENABLED=true")
        from feedgrab.fetchers.medium import fetch_medium_publication
        from feedgrab.schema import from_medium
        from feedgrab.utils.storage import save_to_markdown

        n = limit or medium_user_limit()
        print(f"\n📰 Medium 出版物 {slug}: 抓取 {n} 篇...")
        items = await fetch_medium_publication(slug, limit=n)
        saved = 0
        for data in items:
            content = from_medium(data)
            content.category = f"pub_{slug}"
            path = save_to_markdown(content)
            if path is None:
                continue
            saved += 1
        print(f"✅ Medium 出版物 {slug}: 保存 {saved}/{len(items)} 篇")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n⏹ 已取消")
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


def cmd_reddit_sub(args: list):
    """Fetch posts from a subreddit."""
    import asyncio
    sub = args[0].strip().lstrip("r/").strip("/")
    sort = (_parse_named_str(args, "--sort") or "hot").lower()
    if sort not in {"hot", "new", "top", "best", "rising"}:
        print(f"❌ 不支持的 sort: {sort}（可选 hot/new/top/best/rising）")
        sys.exit(1)
    limit = _parse_named_int(args, "--limit") or _parse_named_int(args, "-n")

    async def run():
        from feedgrab.config import reddit_enabled, reddit_sub_limit
        if not reddit_enabled():
            raise RuntimeError("Reddit 抓取已禁用，请设置 REDDIT_ENABLED=true")
        from feedgrab.fetchers.reddit import fetch_reddit_subreddit
        from feedgrab.schema import from_reddit
        from feedgrab.utils.storage import save_to_markdown

        n = limit or reddit_sub_limit()
        print(f"\n📰 Reddit r/{sub} ({sort}): 抓取 {n} 条...")
        items = await fetch_reddit_subreddit(sub, sort=sort, limit=n)
        saved = 0
        for data in items:
            content = from_reddit(data)
            content.category = f"r_{sub}_{sort}"
            path = save_to_markdown(content)
            if path is None:
                continue
            saved += 1
        print(f"✅ Reddit r/{sub}: 保存 {saved}/{len(items)} 条")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n⏹ 已取消")
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


def cmd_weibo_user(args: list):
    """Fetch a Weibo user's recent posts."""
    import asyncio
    uid = args[0].strip()
    limit = _parse_named_int(args, "--limit") or _parse_named_int(args, "-n")

    async def run():
        from feedgrab.config import weibo_enabled, weibo_user_limit
        if not weibo_enabled():
            raise RuntimeError("Weibo 抓取已禁用，请设置 WEIBO_ENABLED=true")
        from feedgrab.fetchers.weibo import fetch_weibo_user
        from feedgrab.schema import from_weibo
        from feedgrab.utils.storage import save_to_markdown

        n = limit or weibo_user_limit()
        print(f"\n📰 Weibo 用户 {uid}: 抓取 {n} 条...")
        items, screen_name = await fetch_weibo_user(uid, limit=n)
        saved = 0
        category_name = f"user_{uid}"
        if screen_name:
            from feedgrab.utils.storage import _sanitize_filename
            safe = _sanitize_filename(screen_name) or screen_name
            category_name = f"user_{uid}_{safe}"
        for data in items:
            content = from_weibo(data)
            content.category = category_name
            path = save_to_markdown(content)
            if path is None:
                continue
            saved += 1
        print(f"✅ Weibo 用户 {uid}: 保存 {saved}/{len(items)} 条")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n⏹ 已取消")
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


def cmd_feishu_wiki(url: str):
    """Batch-fetch all documents in a Feishu wiki space."""
    import asyncio

    async def run():
        from feedgrab.fetchers.feishu_wiki import fetch_feishu_wiki
        result = await fetch_feishu_wiki(url)
        wiki_title = result.get("wiki_title", "未知知识库")
        total = result.get("total", 0)
        fetched = result.get("fetched", 0)
        skipped = result.get("skipped", 0)
        failed = result.get("failed", 0)
        print(f"\n{'=' * 50}")
        print(f"📂 知识库：{wiki_title}")
        print(f"   文档总数：{total}")
        print(f"   已抓取：  {fetched}")
        print(f"   已跳过：  {skipped}")
        print(f"   失败：    {failed}")
        print(f"{'=' * 50}")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n⏹ 已取消")
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


def cmd_youtube_download(url: str, mode: str = "video"):
    """Download YouTube video/audio/subtitles to {OUTPUT_DIR}/YouTube/{author}/.

    Args:
        url: YouTube video URL
        mode: 'video', 'audio', 'subtitle', or 'all'
    """
    from feedgrab.fetchers.youtube_search import download_video, download_subtitles

    meta = _youtube_resolve_meta(url)
    output_dir = meta["output_dir"]
    filename_prefix = meta["filename_prefix"]
    quality = os.getenv("YOUTUBE_DOWNLOAD_QUALITY", "1080p").strip()

    if mode == "all":
        # Run all 4 tasks: MD + video + audio + subtitle
        print(f"\n📥 YouTube 一键抓取：{url}")
        print(f"   输出目录：{output_dir}\n")

        # Task 1: MD (feedgrab url)
        print("── [1/4] 正在保存 Markdown...")
        try:
            reader = UniversalReader()
            item = asyncio.run(reader.read(url))
            print(f"   ✅ Markdown 已保存：{item.title[:60]}")
        except Exception as e:
            print(f"   ⚠️ Markdown 保存失败：{e}")

        # Task 2: Video
        print("── [2/4] 正在下载视频（MP4）...")
        vpath = download_video(url, output_dir=output_dir, quality=quality,
                               filename_prefix=filename_prefix)
        print(f"   {'✅' if vpath else '❌'} 视频：{vpath or '失败'}")

        # Task 3: Audio
        print("── [3/4] 正在下载音频（MP3）...")
        apath = download_video(url, output_dir=output_dir, audio_only=True,
                               filename_prefix=filename_prefix)
        print(f"   {'✅' if apath else '❌'} 音频：{apath or '失败'}")

        # Task 4: Subtitle
        print("── [4/4] 正在下载字幕（SRT）...")
        spath = download_subtitles(url, output_dir=output_dir,
                                   filename_prefix=filename_prefix)
        print(f"   {'✅' if spath else '⚠️'} 字幕：{spath or '不可用'}")

        print(f"\n✅ 全部任务完成 → {output_dir}")
        return

    # Single mode
    mode_label = {"video": "MP4", "audio": "MP3", "subtitle": "SRT"}.get(mode, mode)
    print(f"\n📥 YouTube 下载（{mode_label}）：{url}")
    print(f"   输出目录：{output_dir}\n")

    if mode == "subtitle":
        path = download_subtitles(url, output_dir=output_dir, filename_prefix=filename_prefix)
    elif mode == "audio":
        path = download_video(url, output_dir=output_dir, audio_only=True,
                              filename_prefix=filename_prefix)
    else:
        path = download_video(url, output_dir=output_dir, quality=quality,
                              filename_prefix=filename_prefix)

    if path:
        print(f"\n✅ 已下载：{path}")
    else:
        print("\n❌ 下载失败")
        sys.exit(1)


def cmd_youtube_search(args: list):
    """Search YouTube videos and save results as Obsidian Markdown."""
    from feedgrab.fetchers.youtube_search import youtube_search, download_video
    from feedgrab.schema import from_youtube
    from feedgrab.utils.storage import save_to_markdown

    keyword = args[0]

    # Parse CLI options
    def _opt(name: str, default: str = "") -> str:
        if name in args:
            idx = args.index(name)
            if idx + 1 < len(args):
                return args[idx + 1]
        return default

    channel = _opt("--channel")
    order = _opt("--order", "relevance")
    after = _opt("--after")
    before = _opt("--before")
    min_dur = _opt("--min-duration")
    max_dur = _opt("--max-duration")
    limit = int(_opt("--limit", "0")) or 0
    do_download = "--download" in args
    audio_only = "--audio-only" in args

    try:
        results = youtube_search(
            keyword,
            channel=channel,
            max_results=limit,
            order=order,
            after=after,
            before=before,
            min_duration=min_dur,
            max_duration=max_dur,
        )
    except RuntimeError as e:
        print(f"\u274c {e}")
        sys.exit(1)

    if not results:
        print("\u274c 未找到结果")
        return

    print(f"\n\U0001f50d YouTube 搜索：\"{keyword}\" — {len(results)} 条结果\n")

    saved = 0
    for i, video in enumerate(results, 1):
        # Display result
        print(
            f"  {i}. [{video['duration']}] {video['title'][:70]}\n"
            f"     {video['channel_title']} · "
            f"{video['view_count']:,} 次观看 · "
            f"{video['published_at'][:10]}"
        )

        # Save to Obsidian Markdown
        video["search_keyword"] = keyword
        content = from_youtube(video)
        # Put search results in search subdirectory
        content.category = f"search/{_sanitize_for_dirname(keyword)}"
        save_to_markdown(content)
        saved += 1

        # Download if requested
        if do_download:
            download_video(video["url"], audio_only=audio_only)

    print(f"\n\u2705 已保存 {saved} 个视频到 YouTube/search/{keyword}/")


def _sanitize_for_dirname(name: str) -> str:
    """Clean a string for use as a directory name."""
    import re as _re
    name = _re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name)
    return name.strip('. ')[:50]


def cmd_mpweixin_account(account_name: str):
    """Fetch all articles from a WeChat public account via MP backend API."""
    from feedgrab.config import mpweixin_id_since, mpweixin_id_delay
    from feedgrab.fetchers.mpweixin_account import fetch_account_articles

    since = mpweixin_id_since()
    delay = mpweixin_id_delay()

    async def run():
        result = await fetch_account_articles(
            account_name, since=since, delay=delay,
        )
        print(f"\n\u2705 微信公众号账号批量抓取完成：'{account_name}'")
        print(f"   总数：{result['total']}，已抓取：{result['fetched']}，"
              f"已跳过：{result['skipped']}，失败：{result['failed']}")
        if result['articles']:
            print("\n   文章：")
            for art in result['articles']:
                title = art.get('title', 'untitled')[:50]
                date = art.get('publish_date', '')
                print(f"   - [{date}] {title}")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n\u23f9 已取消")
    except SystemExit:
        raise
    except Exception as e:
        print(f"\u274c {e}")
        sys.exit(1)


def cmd_mpweixin_album(url: str):
    """Fetch all articles from a WeChat album (专辑/合集)."""
    from feedgrab.config import mpweixin_zhuanji_since, mpweixin_zhuanji_delay
    from feedgrab.fetchers.mpweixin_album import fetch_album_articles

    since = mpweixin_zhuanji_since()
    delay = mpweixin_zhuanji_delay()

    async def run():
        result = await fetch_album_articles(
            url, since=since, delay=delay,
        )
        album_name = result.get('album_name', '') or '未知专辑'
        print(f"\n\u2705 微信公众号专辑抓取完成：'{album_name}'")
        print(f"   总数：{result['total']}，已抓取：{result['fetched']}，"
              f"已跳过：{result['skipped']}，失败：{result['failed']}")
        if result['articles']:
            print("\n   文章：")
            for art in result['articles']:
                title = art.get('title', 'untitled')[:50]
                date = art.get('publish_date', '')
                print(f"   - [{date}] {title}")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n\u23f9 已取消")
    except SystemExit:
        raise
    except Exception as e:
        print(f"\u274c {e}")
        sys.exit(1)


def _split_keywords(raw: str) -> list[str]:
    """Split comma-separated keywords (supports both , and ，).

    Examples:
        "claude code,openclaw,养龙虾" → ["claude code", "openclaw", "养龙虾"]
        "AI agent" → ["AI agent"]
    """
    parts = re.split(r"[,，]", raw)
    return [k.strip() for k in parts if k.strip()]


def cmd_twitter_search(args: list):
    """Search Twitter for tweets by keyword and generate engagement-ranked summary."""
    from feedgrab.config import (
        x_search_enabled, x_search_lang, x_search_days,
        x_search_min_faves, x_search_min_retweets,
        x_search_sort, x_search_exclude_retweets,
        x_search_delay, x_search_max_results, x_search_save_tweets,
        x_search_merge_keywords,
    )

    if not x_search_enabled():
        print("\u274c X 关键词搜索未启用。")
        print("   请在 .env 中设置 X_SEARCH_ENABLED=true 后再使用。")
        return

    # v0.23.0: --people flag \u2192 SearchTimeline with product=People (returns users)
    if "--people" in args:
        _run_search_people(args)
        return

    keywords = _split_keywords(args[0])

    # Parse CLI options
    def _opt(name: str, default: str = "") -> str:
        if name in args:
            idx = args.index(name)
            if idx + 1 < len(args):
                return args[idx + 1]
        return default

    lang = _opt("--lang", x_search_lang())
    days = int(_opt("--days", str(x_search_days())))
    min_faves = int(_opt("--min-faves", str(x_search_min_faves())))
    min_retweets = int(_opt("--min-retweets", str(x_search_min_retweets())))
    sort = _opt("--sort", x_search_sort())
    max_results = int(_opt("--limit", str(x_search_max_results())))
    raw = "--raw" in args
    save_tweets = x_search_save_tweets() or "--save" in args
    merge = (x_search_merge_keywords() or "--merge" in args) and len(keywords) > 1

    from feedgrab.fetchers.twitter_keyword_search import search_twitter_keyword

    if len(keywords) > 1:
        mode = "合并" if merge else "分别输出"
        print(f"\n\U0001f50d X 批量搜索：{len(keywords)} 个关键词（{mode}）")

    all_tweets_merged: list[dict] = []
    successful_keywords = 0
    failed_keywords: list[str] = []

    for ki, keyword in enumerate(keywords):
        if len(keywords) > 1:
            print(f"\n{'='*50}")
            print(f"[{ki+1}/{len(keywords)}] {keyword}")
            print(f"{'='*50}")

        try:
            result = asyncio.run(search_twitter_keyword(
                keyword=keyword,
                lang=lang,
                days=days,
                min_faves=min_faves,
                min_retweets=min_retweets,
                sort=sort,
                exclude_retweets=x_search_exclude_retweets(),
                max_results=max_results,
                scroll_delay=x_search_delay(),
                save_tweets=save_tweets,
                raw=raw,
                skip_summary=merge,
            ))
            print(f"\n\u2705 X 搜索完成：'{keyword}'")
            print(f"   查询语句：{result['query']}")
            print(f"   推文总数：{result['total']}")
            if not merge:
                if result.get("output_path"):
                    print(f"   汇总：{result['output_path']}")
                if result.get("csv_path"):
                    print(f"   CSV: {result['csv_path']}")
            if result.get("saved"):
                print(f"   已保存单篇推文：{result['saved']}")

            successful_keywords += 1
            if merge:
                for td in result.get("tweets", []):
                    td["_keyword"] = keyword
                all_tweets_merged.extend(result.get("tweets", []))
        except KeyboardInterrupt:
            print("\n\u23f9 已取消")
            return
        except SystemExit:
            raise
        except Exception as e:
            print(f"\u274c [{keyword}] {e}")
            failed_keywords.append(keyword)
            if len(keywords) == 1:
                sys.exit(1)

    if failed_keywords and successful_keywords == 0:
        print(f"\u274c 所有 X 关键词搜索都失败：{', '.join(failed_keywords)}")
        sys.exit(1)

    # Generate merged summary table
    if merge:
        from feedgrab.fetchers.twitter_keyword_search import _generate_summary_table, _resolve_output_base
        from pathlib import Path
        from datetime import datetime as _dt

        base_dir = _resolve_output_base()
        sort_label = "new" if sort == "live" else "hot"
        date_str = _dt.now().strftime("%Y-%m-%d")
        merged_dir = base_dir / "X" / "search" / f"{days}day_{sort_label}"
        merged_name = "+".join(re.sub(r'[\\/:*?"<>|]', '_', k) for k in keywords)
        merged_path = merged_dir / f"{merged_name}_{date_str}.md"

        _generate_summary_table(
            keyword=" + ".join(keywords),
            query=" | ".join(keywords),
            sort=sort,
            days=days,
            tweets=all_tweets_merged,
            output_path=merged_path,
            show_keyword=True,
        )
        print(f"\n\U0001f4ca 合并汇总：{merged_path}")
        print(f"   CSV: {merged_path.with_suffix('.csv')}")
        print(f"   总数：{len(all_tweets_merged)} 条推文，来自 {len(keywords)} 个关键词")


def cmd_twitter_tweet_user_list(args: list, mode: str):
    """v0.23.0: batch-fetch users who retweeted / liked a tweet.

    mode: 'retweeters' or 'favoriters'
    args: [<tweet_url_or_id>]
    """
    import asyncio
    from feedgrab.config import x_tweet_user_list_enabled
    from feedgrab.fetchers.twitter_retweeters import (
        fetch_tweet_user_list, extract_tweet_id,
    )
    from feedgrab.fetchers.twitter_cookies import (
        load_twitter_cookies, has_required_cookies,
    )

    if not x_tweet_user_list_enabled():
        print("❌ X 推文互动用户抓取未启用。")
        print("   请在 .env 中设置 X_TWEET_USER_LIST_ENABLED=true 后再使用。")
        return

    if not args:
        print(f"❌ 用法：feedgrab x-{mode} <tweet_url_or_id>")
        return

    tweet_id = extract_tweet_id(args[0])
    if not tweet_id:
        print(f"❌ 无法从输入解析推文 ID: {args[0]}")
        print('   示例：feedgrab x-retweeters https://x.com/<u>/status/123456')
        print('         feedgrab x-retweeters 1234567890')
        return

    cookies = load_twitter_cookies()
    if not has_required_cookies(cookies):
        print("❌ 需要 Twitter Cookie，请先运行: feedgrab login twitter")
        return

    try:
        result = asyncio.run(
            fetch_tweet_user_list(f"{mode}:{tweet_id}", cookies)
        )
    except Exception as e:
        print(f"❌ 抓取失败: {e}")
        return

    label = "转推者" if mode == "retweeters" else "点赞者"
    print(f"✅ 推文 {tweet_id} 的{label}抓取完成")
    print(f"   总数：{result['total']}")
    print(f"   汇总：{result.get('summary_path', '')}")
    print(f"   CSV:  {result.get('csv_path', '')}")


def _run_search_people(args: list):
    """v0.23.0: SearchTimeline product=People → user-list output."""
    import asyncio

    if not args:
        print("❌ 用法：feedgrab x-so <keyword> --people")
        return

    keyword = args[0]
    if keyword == "--people":
        print("❌ 缺少关键词。用法：feedgrab x-so <keyword> --people")
        return

    from feedgrab.fetchers.twitter_search_people import search_people
    from feedgrab.fetchers.twitter_cookies import (
        load_twitter_cookies, has_required_cookies,
    )

    cookies = load_twitter_cookies()
    if not has_required_cookies(cookies):
        print("❌ 需要 Twitter Cookie，请先运行: feedgrab login twitter")
        return

    try:
        result = search_people(keyword, cookies)
    except Exception as e:
        print(f"❌ 人物搜索失败: {e}")
        return

    print(f"✅ 人物搜索完成：'{keyword}'")
    print(f"   总数：{result['total']}")
    print(f"   汇总：{result.get('summary_path', '')}")
    print(f"   CSV:  {result.get('csv_path', '')}")


def cmd_zhihu_search(args: list):
    """Search Zhihu for answers/articles by keyword and generate engagement-ranked summary."""
    from feedgrab.config import zhihu_search_limit, zhihu_search_save_answers

    keywords = _split_keywords(args[0])

    def _opt(name: str, default: str = "") -> str:
        if name in args:
            idx = args.index(name)
            if idx + 1 < len(args):
                return args[idx + 1]
        return default

    sort = _opt("--sort", "hot")
    max_results = int(_opt("--limit", str(zhihu_search_limit())))
    save_answers = zhihu_search_save_answers() or "--save" in args
    merge = "--merge" in args and len(keywords) > 1

    from feedgrab.fetchers.zhihu_search import search_zhihu_keyword

    if len(keywords) > 1:
        mode = "合并" if merge else "分别输出"
        print(f"\n\U0001f50d 知乎批量搜索：{len(keywords)} 个关键词（{mode}）")

    all_items_merged: list[dict] = []

    for ki, keyword in enumerate(keywords):
        if len(keywords) > 1:
            print(f"\n{'='*50}")
            print(f"[{ki+1}/{len(keywords)}] {keyword}")
            print(f"{'='*50}")

        try:
            result = asyncio.run(search_zhihu_keyword(
                keyword=keyword,
                sort=sort,
                max_results=max_results,
                save_answers=save_answers,
                skip_summary=merge,
            ))
            print(f"\n\u2705 知乎搜索完成：'{keyword}'")
            print(f"   结果总数：{result['total']}")
            if not merge:
                if result.get("output_path"):
                    print(f"   汇总：{result['output_path']}")
                if result.get("csv_path"):
                    print(f"   CSV: {result['csv_path']}")
            if result.get("saved"):
                print(f"   已保存单篇回答/文章：{result['saved']}")

            if merge:
                for item in result.get("items", []):
                    item["_keyword"] = keyword
                all_items_merged.extend(result.get("items", []))
        except KeyboardInterrupt:
            print("\n\u23f9 已取消")
            return
        except SystemExit:
            raise
        except Exception as e:
            print(f"\u274c [{keyword}] {e}")
            if len(keywords) == 1:
                sys.exit(1)

    # Generate merged summary table
    if merge and all_items_merged:
        from feedgrab.fetchers.zhihu_search import _generate_summary_table, _resolve_output_base
        from pathlib import Path
        from datetime import datetime as _dt

        base_dir = _resolve_output_base()
        sort_label = "new" if sort == "new" else "hot"
        date_str = _dt.now().strftime("%Y-%m-%d")
        merged_dir = base_dir / "Zhihu" / "search" / sort_label
        merged_name = "+".join(re.sub(r'[\\/:*?"<>|]', '_', k) for k in keywords)
        merged_path = merged_dir / f"{merged_name}_{date_str}.md"

        _generate_summary_table(
            keyword=" + ".join(keywords),
            sort=sort,
            items=all_items_merged,
            output_path=merged_path,
            show_keyword=True,
        )
        print(f"\n\U0001f4ca 合并汇总：{merged_path}")
        print(f"   CSV: {merged_path.with_suffix('.csv')}")
        print(f"   总数：{len(all_items_merged)} 条结果，来自 {len(keywords)} 个关键词")


def cmd_xhs_search(args: list):
    """Search XHS for notes by keyword and generate engagement-ranked summary."""
    from feedgrab.config import xhs_search_sort, xhs_search_note_type, xhs_search_max_pages, xhs_search_save_notes, xhs_search_merge_keywords

    keywords = _split_keywords(args[0])

    # Parse CLI options
    def _opt(name: str, default: str = "") -> str:
        if name in args:
            idx = args.index(name)
            if idx + 1 < len(args):
                return args[idx + 1]
        return default

    sort = _opt("--sort", xhs_search_sort())
    note_type = _opt("--type", xhs_search_note_type())
    max_results = int(_opt("--limit", str(xhs_search_max_pages() * 20)))
    save_notes = xhs_search_save_notes() or "--save" in args
    merge = (xhs_search_merge_keywords() or "--merge" in args) and len(keywords) > 1

    from feedgrab.fetchers.xhs_search_notes import search_xhs_keyword

    if len(keywords) > 1:
        mode = "合并" if merge else "分别输出"
        print(f"\n\U0001f50d 小红书批量搜索：{len(keywords)} 个关键词（{mode}）")

    all_notes_merged: list[dict] = []

    for ki, keyword in enumerate(keywords):
        if len(keywords) > 1:
            print(f"\n{'='*50}")
            print(f"[{ki+1}/{len(keywords)}] {keyword}")
            print(f"{'='*50}")

        try:
            result = search_xhs_keyword(
                keyword=keyword,
                sort=sort,
                note_type=note_type,
                max_results=max_results,
                save_notes=save_notes,
                skip_summary=merge,
            )
            print(f"\n\u2705 小红书搜索完成：'{keyword}'")
            print(f"   笔记总数：{result['total']}")
            if not merge:
                if result.get("output_path"):
                    print(f"   汇总：{result['output_path']}")
                if result.get("csv_path"):
                    print(f"   CSV: {result['csv_path']}")
            if result.get("saved"):
                print(f"   已保存单篇笔记：{result['saved']}")

            if merge:
                for nd in result.get("notes", []):
                    nd["_keyword"] = keyword
                all_notes_merged.extend(result.get("notes", []))
        except KeyboardInterrupt:
            print("\n\u23f9 已取消")
            return
        except SystemExit:
            raise
        except Exception as e:
            print(f"\u274c [{keyword}] {e}")
            if len(keywords) == 1:
                sys.exit(1)

    # Generate merged summary table
    if merge and all_notes_merged:
        from feedgrab.fetchers.xhs_search_notes import _generate_xhs_summary_table, _resolve_output_base
        from pathlib import Path
        from datetime import datetime as _dt

        _SORT_ZH = {"general": "综合", "popular": "热门", "latest": "最新"}
        base_dir = _resolve_output_base()
        sort_label = _SORT_ZH.get(sort, sort)
        date_str = _dt.now().strftime("%Y-%m-%d")
        merged_dir = base_dir / "XHS" / "search" / sort_label
        merged_name = "+".join(re.sub(r'[\\/:*?"<>|]', '_', k) for k in keywords)
        merged_path = merged_dir / f"{merged_name}_{date_str}.md"

        _generate_xhs_summary_table(
            keyword=" + ".join(keywords),
            sort=sort,
            note_type=note_type,
            notes=all_notes_merged,
            output_path=merged_path,
            show_keyword=True,
        )
        print(f"\n\U0001f4ca 合并汇总：{merged_path}")
        print(f"   CSV: {merged_path.with_suffix('.csv')}")
        print(f"   总数：{len(all_notes_merged)} 篇笔记，来自 {len(keywords)} 个关键词")


def cmd_wechat_search(keyword: str, max_results: int = 0):
    """Search WeChat articles by keyword via Sogou."""
    from feedgrab.config import mpweixin_sogou_enabled, mpweixin_sogou_max_results, mpweixin_sogou_delay

    if not mpweixin_sogou_enabled():
        print("\u274c 搜狗微信搜索未启用。")
        print("   请在 .env 中设置 MPWEIXIN_SOGOU_ENABLED=true 后再使用。")
        return

    # Use config default if not specified via --limit
    if max_results <= 0:
        max_results = mpweixin_sogou_max_results()
    delay = mpweixin_sogou_delay()

    from feedgrab.fetchers.wechat_search import search_wechat_articles

    async def run():
        result = await search_wechat_articles(
            keyword, max_results=max_results, fetch_content=True, delay=delay
        )
        print(f"\n\u2705 微信搜索完成：'{keyword}'")
        print(f"   找到：{result['total']}，已抓取：{result['fetched']}，"
              f"已跳过：{result['skipped']}，失败：{result['failed']}")
        if result['articles']:
            print("\n   文章：")
            for art in result['articles']:
                title = art.get('title', 'untitled')[:50]
                author = art.get('author', '')
                date = art.get('publish_date', '')
                print(f"   - [{date}] {title} ({author})")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n\u23f9 已取消")
    except SystemExit:
        raise
    except Exception as e:
        print(f"\u274c {e}")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("""
\U0001f4d6 feedgrab \u2014 通用内容抓取器

用法：
    feedgrab setup              首次部署向导（新用户推荐）
    feedgrab <url>              抓取任意 URL 内容
    feedgrab clip               从剪贴板抓取 URL（避开 PowerShell 的 & 解析问题）
    feedgrab <url1> <url2>      批量抓取多个 URL
    feedgrab x-so <keyword>     按关键词搜索 X/Twitter（互动数据表）
    feedgrab xhs-so <keyword>   按关键词搜索小红书（互动数据表）
    feedgrab mpweixin-id <name> 抓取公众号账号历史文章
    feedgrab mpweixin-so <keyword>  按关键词搜索微信公众号文章
    feedgrab ytb-so <keyword>   按关键词搜索 YouTube 视频
    feedgrab ytb-dlv <url>      下载 YouTube 视频（MP4）
    feedgrab ytb-dla <url>      下载 YouTube 音频（MP3）
    feedgrab ytb-dlz <url>      下载 YouTube 字幕（SRT）
    feedgrab ytb-all <url>      一键处理：Markdown + 视频 + 音频 + 字幕
    feedgrab login <platform>   登录平台并保存登录态，供浏览器兜底使用
    feedgrab detect-ua          检测真实 Chrome UA 并写入 .env
    feedgrab doctor             运行全量诊断
    feedgrab doctor x           Twitter/X 诊断（Cookie、queryId、网络）
    feedgrab doctor xhs         小红书诊断（登录态、网络）
    feedgrab doctor mpweixin    微信公众号诊断（登录态、网络）
    feedgrab list               查看内容统计
    feedgrab reset <folder>     重置子目录（删除文件并清理去重索引）
    feedgrab clean-index        清理索引中的批量记录和缓存文件

支持平台：
    微信公众号、Telegram、X/Twitter、YouTube、
    Bilibili、小红书、知乎、RSS，以及任意网页

示例：
    feedgrab https://mp.weixin.qq.com/s/abc123
    feedgrab https://x.com/elonmusk/status/123456
    feedgrab https://x.com/i/bookmarks
    feedgrab https://x.com/iBigQiang
    feedgrab https://www.xiaohongshu.com/user/profile/5eb416f...
    feedgrab "https://www.xiaohongshu.com/search_result?keyword=..."
    feedgrab x-so openclaw
    feedgrab x-so "AI Agent" --days 7 --min-faves 50 --sort top
    feedgrab xhs-so "AI Agent"
    feedgrab xhs-so "AI Agent" --sort popular --type video
    feedgrab zhihu-so "AI Agent"
    feedgrab zhihu-so "openclaw" --limit 20 --sort hot
    feedgrab mpweixin-id "饼干哥哥AGI"
    feedgrab mpweixin-so "AI Agent"
    feedgrab ytb-so "AI Agent"
    feedgrab ytb-so "教程" --channel @AndrewNg --order viewCount
    feedgrab ytb-dlv https://www.youtube.com/watch?v=xxx   # 下载视频
    feedgrab ytb-dla https://www.youtube.com/watch?v=xxx   # 下载音频
    feedgrab ytb-dlz https://www.youtube.com/watch?v=xxx   # 下载字幕
    feedgrab ytb-all https://www.youtube.com/watch?v=xxx   # Markdown+视频+音频+字幕
    feedgrab login xhs
    feedgrab setup              # 首次设置向导
""")
        return

    cmd = sys.argv[1].lower()

    if cmd == "setup":
        cmd_setup()
    elif cmd == "clip":
        cmd_clip()
    elif cmd == "login":
        if len(sys.argv) < 3:
            print("\u274c 用法：feedgrab login <platform> [--headless]")
            print("   支持平台：xhs、wechat、twitter")
            sys.exit(1)
        headless = "--headless" in sys.argv
        cmd_login(sys.argv[2], headless=headless)
    elif cmd == "detect-ua":
        cmd_detect_ua()
    elif cmd == "doctor":
        plat = sys.argv[2] if len(sys.argv) > 2 else "all"
        cmd_doctor(plat)
    elif cmd == "list":
        cmd_list()
    elif cmd == "reset":
        if len(sys.argv) < 3:
            print("\u274c 用法：feedgrab reset <folder>")
            print("   示例：feedgrab reset bookmarks/OpenClaw")
            print("   示例：feedgrab reset status_author/强子手记")
            sys.exit(1)
        cmd_reset(sys.argv[2])
    elif cmd == "clean-index":
        skip = "--yes" in sys.argv or "-y" in sys.argv
        cmd_clean_index(skip_confirm=skip)
    elif cmd == "mpweixin-id":
        if len(sys.argv) < 3:
            print("\u274c 用法：feedgrab mpweixin-id <公众号名称>")
            print('   示例：feedgrab mpweixin-id "饼干哥哥AGI"')
            print("   需要先登录微信公众号后台：feedgrab login wechat")
            sys.exit(1)
        cmd_mpweixin_account(sys.argv[2])
    elif cmd == "mpweixin-zhuanji":
        if len(sys.argv) < 3:
            print("\u274c 用法：feedgrab mpweixin-zhuanji <album_url>")
            print('   示例：feedgrab mpweixin-zhuanji "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=xxx&album_id=xxx"')
            print("   配置：MPWEIXIN_ZHUANJI_SINCE=2026-01-01  （日期过滤）")
            print("         MPWEIXIN_ZHUANJI_DELAY=3            （请求间隔）")
            sys.exit(1)
        cmd_mpweixin_album(sys.argv[2])
    elif cmd == "mpweixin-so":
        if len(sys.argv) < 3:
            print("\u274c 用法：feedgrab mpweixin-so <keyword> [--limit N]")
            print('   示例：feedgrab mpweixin-so "AI Agent"')
            sys.exit(1)
        keyword = sys.argv[2]
        limit = 0  # 0 means use config default
        if "--limit" in sys.argv:
            idx = sys.argv.index("--limit")
            if idx + 1 < len(sys.argv):
                try:
                    limit = int(sys.argv[idx + 1])
                except ValueError:
                    pass
        cmd_wechat_search(keyword, max_results=limit)
    elif cmd == "x-so":
        if len(sys.argv) < 3:
            print("\u274c 用法：feedgrab x-so <keyword> [options]")
            print('   示例：feedgrab x-so openclaw')
            print('            feedgrab x-so openclaw --days 3 --lang en')
            print('            feedgrab x-so "AI Agent" --min-faves 100 --sort top')
            print("            feedgrab x-so 'openclaw lang:zh since:2026-03-06' --raw")
            print('            feedgrab x-so "openclaw,ChatGPT,DeepSeek"  # multi-keyword')
            print("   选项：")
            print("     --days N           时间范围，单位天（默认：1）")
            print("     --lang LANG        语言过滤（默认：zh）")
            print("     --min-faves N      最低点赞数（默认：0）")
            print("     --min-retweets N   最低转推数（默认：0）")
            print("     --sort MODE        live=最新，top=热门（默认：live）")
            print("     --limit N          最大结果数（默认：100）")
            print("     --raw              将关键词作为原始查询语句使用")
            print("     --save             保存单篇推文 .md 文件")
            print("     --merge            将多关键词结果合并到一个表格")
            sys.exit(1)
        cmd_twitter_search(sys.argv[2:])
    elif cmd == "xhs-so":
        if len(sys.argv) < 3:
            print("\u274c 用法：feedgrab xhs-so <keyword> [options]")
            print('   示例：feedgrab xhs-so "AI Agent"')
            print('            feedgrab xhs-so "AI Agent" --sort popular')
            print('            feedgrab xhs-so "AI Agent" --type video')
            print('            feedgrab xhs-so "AI Agent" --sort latest --limit 50')
            print('            feedgrab xhs-so "claude code,openclaw,养龙虾"  # 多关键词')
            print("   选项：")
            print("     --sort MODE        general=综合，popular=热门，latest=最新（默认：general）")
            print("     --type TYPE        all=全部，video=视频，image=图片（默认：all）")
            print("     --limit N          最大结果数（默认：200）")
            print("     --save             保存单篇笔记 .md 文件")
            print("     --merge            将多关键词结果合并到一个表格")
            sys.exit(1)
        cmd_xhs_search(sys.argv[2:])
    elif cmd == "zhihu-so":
        if len(sys.argv) < 3:
            print("\u274c 用法：feedgrab zhihu-so <keyword> [options]")
            print('   示例：feedgrab zhihu-so "openclaw"')
            print('            feedgrab zhihu-so "AI Agent" --sort hot --limit 20')
            print('            feedgrab zhihu-so "openclaw,ChatGPT" --merge')
            print("   选项：")
            print("     --sort MODE        hot=热门，new=最新（默认：hot）")
            print("     --limit N          最大结果数（默认：50）")
            print("     --save             保存单篇回答/文章 .md 文件")
            print("     --merge            将多关键词结果合并到一个表格")
            sys.exit(1)
        cmd_zhihu_search(sys.argv[2:])
    elif cmd == "ytb-so":
        if len(sys.argv) < 3:
            print("\u274c 用法：feedgrab ytb-so <keyword> [options]")
            print('   示例：feedgrab ytb-so "AI Agent"')
            print('            feedgrab ytb-so "教程" --channel @AndrewNg')
            print('            feedgrab ytb-so "ML" --order viewCount --after 2025-01-01')
            print('            feedgrab ytb-so "AI" --download --limit 5')
            print("   选项：")
            print("     --channel <handle>   限定 YouTube 频道")
            print("     --order <order>      relevance/date/viewCount/rating（默认：relevance）")
            print("     --after YYYY-MM-DD   只保留此日期之后的视频")
            print("     --before YYYY-MM-DD  只保留此日期之前的视频")
            print("     --min-duration <dur> 最短时长，例如 10m、1h")
            print("     --max-duration <dur> 最长时长，例如 30m、2h")
            print("     --limit N            最大结果数（默认：10，最大：50）")
            print("     --download           搜索后下载视频")
            print("     --audio-only         只下载音频（MP3）")
            sys.exit(1)
        cmd_youtube_search(sys.argv[2:])
    elif cmd in ("ytb-dlv", "ytb-dla", "ytb-dlz", "ytb-all"):
        if len(sys.argv) < 3:
            print(f"❌ 用法：feedgrab {cmd} <youtube_url>")
            print(f"   示例：feedgrab {cmd} https://www.youtube.com/watch?v=xxx")
            sys.exit(1)
        mode_map = {"ytb-dlv": "video", "ytb-dla": "audio", "ytb-dlz": "subtitle", "ytb-all": "all"}
        cmd_youtube_download(sys.argv[2], mode=mode_map[cmd])
    elif cmd == "feishu-wiki":
        if len(sys.argv) < 3:
            print("❌ 用法：feedgrab feishu-wiki <wiki_url>")
            print("   示例：feedgrab feishu-wiki https://xxx.feishu.cn/wiki/ABC123")
            print("   配置：FEISHU_WIKI_BATCH_ENABLED=true（必填）")
            print("         FEISHU_APP_ID + FEISHU_APP_SECRET（Tier 0：Open API）")
            print("         feedgrab login feishu（Tier 1：浏览器）")
            sys.exit(1)
        # Force-enable batch for this command
        import os
        os.environ["FEISHU_WIKI_BATCH_ENABLED"] = "true"
        cmd_feishu_wiki(sys.argv[2])
    elif cmd == "hn":
        if len(sys.argv) < 3:
            print("❌ 用法：feedgrab hn <category> [--limit N]")
            print("   分类：top | new | best | ask | show | jobs")
            print("   示例：feedgrab hn top --limit 30")
            print("            feedgrab hn ask")
            sys.exit(1)
        cmd_hackernews_list(sys.argv[2:])
    elif cmd == "medium-user":
        if len(sys.argv) < 3:
            print("❌ 用法：feedgrab medium-user <@username> [--limit N]")
            print("   示例：feedgrab medium-user @dotey --limit 20")
            sys.exit(1)
        cmd_medium_user(sys.argv[2:])
    elif cmd == "medium-pub":
        if len(sys.argv) < 3:
            print("❌ 用法：feedgrab medium-pub <publication-slug> [--limit N]")
            print("   示例：feedgrab medium-pub better-programming --limit 20")
            sys.exit(1)
        cmd_medium_pub(sys.argv[2:])
    elif cmd == "reddit-sub":
        if len(sys.argv) < 3:
            print("❌ 用法：feedgrab reddit-sub <subreddit> [--sort hot|new|top|best] [--limit N]")
            print("   示例：feedgrab reddit-sub MachineLearning --sort hot --limit 25")
            sys.exit(1)
        cmd_reddit_sub(sys.argv[2:])
    elif cmd == "weibo-user":
        if len(sys.argv) < 3:
            print("❌ 用法：feedgrab weibo-user <uid> [--limit N]")
            print("   示例：feedgrab weibo-user 1234567890 --limit 20")
            sys.exit(1)
        cmd_weibo_user(sys.argv[2:])
    elif cmd == "x-retweeters":
        if len(sys.argv) < 3:
            print("❌ 用法：feedgrab x-retweeters <tweet_url_or_id>")
            print("   示例：feedgrab x-retweeters https://x.com/<u>/status/1234567890")
            print("            feedgrab x-retweeters 1234567890")
            sys.exit(1)
        cmd_twitter_tweet_user_list(sys.argv[2:], mode="retweeters")
    elif cmd == "x-favoriters":
        if len(sys.argv) < 3:
            print("❌ 用法：feedgrab x-favoriters <tweet_url_or_id>")
            print("   示例：feedgrab x-favoriters https://x.com/<u>/status/1234567890")
            print("            feedgrab x-favoriters 1234567890")
            sys.exit(1)
        cmd_twitter_tweet_user_list(sys.argv[2:], mode="favoriters")
    elif cmd.startswith("http") or cmd.startswith("www.") or "." in cmd:
        urls = [arg for arg in sys.argv[1:] if arg.startswith(("http", "www.")) or "." in arg]
        cmd_fetch(urls)
    else:
        print(f"\u274c 未知命令：{cmd}")
        print("   不带参数运行 'feedgrab' 可查看帮助")


if __name__ == "__main__":
    main()
