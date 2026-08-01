"""HTML → text @tool via httpx + trafilatura."""

from __future__ import annotations

import asyncio

import httpx
import trafilatura
from langchain_core.tools import tool


async def _fetch_and_extract(url: str, max_chars: int) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    text = trafilatura.extract(resp.text) or ""
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


@tool
def read_url(url: str, max_chars: int = 8000) -> str:
    """Fetch a web page and return its main text content (up to max_chars)."""
    return asyncio.run(_fetch_and_extract(url, max_chars))


__all__ = ["read_url"]
