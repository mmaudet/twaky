"""Tests for the read_url @tool (httpx + trafilatura)."""

from __future__ import annotations

import httpx
import pytest

from twaky.tools.read_url import _fetch_and_extract, read_url

SAMPLE_HTML = """
<html><body>
<article><h1>Twaky is nice</h1><p>Some paragraph about assistants.</p></article>
<footer>Copyright</footer>
</body></html>
"""


class TestFetchAndExtract:
    @pytest.mark.asyncio
    async def test_extracts_main_content(self, monkeypatch):
        class FakeResponse:
            text = SAMPLE_HTML

            def raise_for_status(self):
                pass

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, url):
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        text = await _fetch_and_extract("http://twaky/", 100)
        assert "Twaky" in text
        assert "assistants" in text
        # Truncation:
        assert len(text) <= 100

    @pytest.mark.asyncio
    async def test_empty_page_returns_empty_string(self, monkeypatch):
        class FakeResponse:
            text = "<html><body></body></html>"

            def raise_for_status(self):
                pass

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, url):
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        text = await _fetch_and_extract("http://twaky/", 100)
        assert text == ""


class TestToolWrapper:
    def test_read_url_is_a_langchain_tool(self):
        assert read_url.name == "read_url"
