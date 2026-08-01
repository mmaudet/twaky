"""SearXNG-backed @tool web_search."""

from __future__ import annotations

import httpx
import pytest

from twaky.tools.web_search import _search_impl, web_search


class TestSearchImpl:
    @pytest.mark.asyncio
    async def test_calls_expected_url(self, monkeypatch):
        seen = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"results": [{"title": "T", "url": "http://x", "content": "C"}]}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, url, params=None):
                seen["url"] = url
                seen["params"] = params
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        rows = await _search_impl("twake linagora", limit=5)
        assert seen["url"].endswith("/search")
        assert seen["params"]["q"] == "twake linagora"
        assert seen["params"]["format"] == "json"
        assert rows == [{"title": "T", "url": "http://x", "content": "C"}]

    @pytest.mark.asyncio
    async def test_limit_truncates(self, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "results": [
                        {"title": f"T{i}", "url": f"http://x/{i}", "content": ""}
                        for i in range(10)
                    ]
                }

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, url, params=None):
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        rows = await _search_impl("q", limit=3)
        assert len(rows) == 3


class TestToolWrapper:
    def test_web_search_is_a_langchain_tool(self):
        assert web_search.name == "web_search"
        assert "search" in web_search.description.lower()
