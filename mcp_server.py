# -*- coding: utf-8 -*-
"""
feedgrab MCP Server — expose content reading as MCP tools.

Usage:
    python mcp_server.py                    # stdio transport (for Claude Code)
    python mcp_server.py --transport sse    # SSE transport (for web clients)

Claude Code config (~/.claude/claude_desktop_config.json):
    {
        "mcpServers": {
            "feedgrab": {
                "command": "python",
                "args": ["/path/to/feedgrab/mcp_server.py"]
            }
        }
    }
"""

import asyncio
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

from feedgrab.schema import UnifiedInbox
from feedgrab.service import FetchService

mcp = FastMCP(
    "feedgrab",
    instructions="Universal content reader — give it any URL, get structured content back.",
)

fetch_service = FetchService(inbox=UnifiedInbox())


@mcp.tool()
async def read_url(url: str) -> str:
    """
    Read content from any URL and return structured result.

    Supports: YouTube, Bilibili, X/Twitter, WeChat, Xiaohongshu,
    Telegram, RSS, and any generic web page.

    Returns JSON with: title, content, url, source_type, platform metadata.
    """
    import json

    result = await fetch_service.fetch_url(url)
    content = result.content
    result = content.to_dict()
    # Keep it readable
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def read_batch(urls: list[str]) -> str:
    """
    Read multiple URLs concurrently. Returns JSON array of results.

    Failed URLs are logged but don't block other results.
    """
    import json

    results = await fetch_service.fetch_urls(urls)
    payload = []
    for result in results:
        if getattr(result, "success", True) and result.content is not None:
            item = result.content.to_dict()
            item["ok"] = True
            payload.append(item)
            continue

        payload.append(
            {
                "ok": False,
                "request": result.request.to_dict(),
                "platform": result.platform,
                "error": result.error,
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_inbox() -> str:
    """
    List all items in the content inbox.

    Returns JSON array of previously fetched content.
    """
    import json

    items = [item.to_dict() for item in fetch_service.list_inbox()]
    return json.dumps(items, ensure_ascii=False, indent=2)


@mcp.tool()
async def detect_platform(url: str) -> str:
    """
    Detect which platform a URL belongs to.

    Returns the platform name: youtube, bilibili, twitter, wechat,
    xhs, telegram, rss, or generic.
    """
    return fetch_service.detect_platform(url)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="feedgrab MCP Server")
    parser.add_argument(
        "--transport", default="stdio", choices=["stdio", "sse"],
        help="Transport mode (default: stdio)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind SSE server (default: 127.0.0.1). "
        "WARNING: binding to 0.0.0.0 exposes the server to the network "
        "without authentication — use at your own risk.",
    )
    parser.add_argument("--port", type=int, default=8000, help="SSE port (default: 8000)")
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")
