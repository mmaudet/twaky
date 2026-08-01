"""Web search via SearXNG, exposed as a LangChain @tool.

SearXNG runs on twake-network at settings.searxng_endpoint. JSON API is
`GET /search?q=<q>&format=json`.
"""

from __future__ import annotations

import asyncio

import httpx
from langchain_core.tools import tool

from twaky.config import settings


async def _search_impl(query: str, limit: int = 5) -> list[dict]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{settings.searxng_endpoint.rstrip('/')}/search",
            params={"q": query, "format": "json", "categories": "general"},
        )
        resp.raise_for_status()
        data = resp.json()
    results = data.get("results") or []
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
        }
        for r in results[:limit]
    ]


@tool
def web_search(query: str, limit: int = 5) -> list[dict]:
    """Search the public web via SearXNG. Returns up to `limit` results
    as a list of {title, url, content} dicts.
    """
    return asyncio.run(_search_impl(query, limit))


__all__ = ["web_search"]
