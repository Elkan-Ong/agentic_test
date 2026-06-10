"""
GNews MCP Server
----------------
An MCP (Model Context Protocol) server that wraps the GNews API,
exposing two tools to Claude:
  - get_top_headlines: Fetch top headlines by topic/country
  - search_news: Search articles by keyword

Run standalone (stdio transport):
    GNEWS_API_KEY=your_key python gnews_mcp_server.py

Free tier: 100 requests/day — https://gnews.io
"""

import os
import asyncio
import httpx
from mcp.server.fastmcp import FastMCP

# ── Server setup ──────────────────────────────────────────────────────────────
mcp = FastMCP("gnews-briefing")

GNEWS_API_KEY = os.getenv("GNEWS_API_KEY", "")
GNEWS_BASE_URL = "https://gnews.io/api/v4"

VALID_TOPICS = [
    "breaking-news", "world", "nation", "business",
    "technology", "entertainment", "sports", "science", "health",
]


def _format_articles(articles: list) -> str:
    """Format a list of GNews article dicts into a readable string."""
    if not articles:
        return "No articles found."
    lines = []
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. {a['title']}")
        if a.get("description"):
            lines.append(f"   {a['description']}")
        lines.append(
            f"   Source: {a['source']['name']}  |  {a.get('publishedAt', '')[:10]}"
        )
        lines.append("")
    return "\n".join(lines)


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_top_headlines(
    topic: str = "world",
    country: str = "us",
    max_results: int = 5,
) -> str:
    """
    Fetch top news headlines by topic and country.

    Args:
        topic: One of breaking-news, world, nation, business, technology,
               entertainment, sports, science, health  (default: world)
        country: Two-letter country code, e.g. us, gb, sg, au  (default: us)
        max_results: How many articles to return, 1-10  (default: 5)
    """
    if not GNEWS_API_KEY:
        return "ERROR: GNEWS_API_KEY environment variable not set."

    params = {
        "token": GNEWS_API_KEY,
        "lang": "en",
        "max": min(max(1, max_results), 10),
        "topic": topic if topic in VALID_TOPICS else "world",
        "country": country,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{GNEWS_BASE_URL}/top-headlines", params=params)
        resp.raise_for_status()
        data = resp.json()

    return _format_articles(data.get("articles", []))


@mcp.tool()
async def search_news(query: str, max_results: int = 5) -> str:
    """
    Search for recent news articles matching a keyword or phrase.

    Args:
        query: Search query, e.g. "artificial intelligence", "stock market crash"
        max_results: How many articles to return, 1-10  (default: 5)
    """
    if not GNEWS_API_KEY:
        return "ERROR: GNEWS_API_KEY environment variable not set."

    params = {
        "token": GNEWS_API_KEY,
        "q": query,
        "lang": "en",
        "max": min(max(1, max_results), 10),
        "sortby": "publishedAt",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{GNEWS_BASE_URL}/search", params=params)
        resp.raise_for_status()
        data = resp.json()

    return _format_articles(data.get("articles", []))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()  # stdio transport by default
