"""Tests for the read_url @tool (httpx + trafilatura)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from twaky.tools.read_url import _fetch_and_extract, _validate_url, read_url

SAMPLE_HTML = """
<html><body>
<article><h1>Twaky is nice</h1><p>Some paragraph about assistants.</p></article>
<footer>Copyright</footer>
</body></html>
"""

# Public IP (example.com)
PUBLIC_IP = "93.184.216.34"


class TestValidateUrl:
    def test_validate_url_rejects_loopback(self):
        with pytest.raises(ValueError, match="blocked"):
            _validate_url("http://127.0.0.1/foo")

    def test_validate_url_rejects_link_local(self):
        with pytest.raises(ValueError, match="blocked"):
            _validate_url("http://169.254.169.254/latest/meta-data/")

    def test_validate_url_rejects_private_range(self):
        with pytest.raises(ValueError, match="blocked"):
            _validate_url("http://10.0.0.1/")

    def test_validate_url_rejects_non_http_scheme(self):
        with pytest.raises(ValueError, match="disallowed scheme"):
            _validate_url("file:///etc/passwd")

    def test_validate_url_rejects_internal_hostname(self):
        # Patch socket.getaddrinfo to return a private IP
        with (
            patch(
                "twaky.tools.read_url.socket.getaddrinfo",
                return_value=[(0, 0, 0, "", ("172.27.0.6", 0))],
            ),
            pytest.raises(ValueError, match="blocked"),
        ):
            _validate_url("http://twaky-pg:5432/")

    def test_validate_url_allows_public_ip(self):
        # Public IP passes through
        with patch(
            "twaky.tools.read_url.socket.getaddrinfo",
            return_value=[(0, 0, 0, "", (PUBLIC_IP, 0))],
        ):
            _validate_url("http://example.com/")  # no raise


class TestFetchAndExtract:
    @pytest.mark.asyncio
    async def test_extracts_main_content(self, monkeypatch):
        class FakeResponse:
            def __init__(self):
                self.status_code = 200
                self.text = SAMPLE_HTML
                self.headers = {}

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
        monkeypatch.setattr(
            "twaky.tools.read_url.socket.getaddrinfo",
            lambda *a, **kw: [(0, 0, 0, "", (PUBLIC_IP, 0))],
        )

        text = await _fetch_and_extract("http://example.com/", 100)
        assert "Twaky" in text
        assert "assistants" in text
        # Truncation:
        assert len(text) <= 100

    @pytest.mark.asyncio
    async def test_empty_page_returns_empty_string(self, monkeypatch):
        class FakeResponse:
            def __init__(self):
                self.status_code = 200
                self.text = "<html><body></body></html>"
                self.headers = {}

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
        monkeypatch.setattr(
            "twaky.tools.read_url.socket.getaddrinfo",
            lambda *a, **kw: [(0, 0, 0, "", (PUBLIC_IP, 0))],
        )

        text = await _fetch_and_extract("http://example.com/", 100)
        assert text == ""


class TestToolWrapper:
    def test_read_url_is_a_langchain_tool(self):
        assert read_url.name == "read_url"
