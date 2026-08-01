"""Tests for the OIDC helpers used by Plume's JMAP auth."""

from __future__ import annotations

import httpx
import pytest

from twaky.auth import oidc

ISSUER = "https://auth.twake-dev.example.com"
CLIENT_ID = "twaky-plume"
CLIENT_SECRET = "s3cret"


def _mock_client(responses):
    class FakeResponse:
        def __init__(self, data, status=200):
            self._data = data
            self.status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore

        def json(self):
            return self._data

    class FakeClient:
        _i = 0

        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, data=None, headers=None):
            r = responses[FakeClient._i]
            FakeClient._i += 1
            return FakeResponse(*r) if isinstance(r, tuple) else FakeResponse(r)

    return FakeClient


class TestClientCredentials:
    @pytest.mark.asyncio
    async def test_returns_access_token(self, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _mock_client([{"access_token": "svc-tok", "expires_in": 3600}]),
        )
        tok = await oidc._client_credentials_token(
            client_id=CLIENT_ID, client_secret=CLIENT_SECRET, issuer=ISSUER
        )
        assert tok == "svc-tok"


class TestTokenExchange:
    @pytest.mark.asyncio
    async def test_exchange_returns_impersonated_token(self, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _mock_client([{"access_token": "user-tok", "expires_in": 3600}]),
        )
        tok = await oidc._exchange_token(
            subject_email="alice@x",
            actor_token="svc-tok",
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        assert tok == "user-tok"


class TestGetImpersonatedTokenCache:
    def test_cache_reuses_token(self, monkeypatch):
        # Two calls, one HTTP roundtrip for each phase.
        calls = _mock_client(
            [
                {"access_token": "svc", "expires_in": 3600},
                {"access_token": "user", "expires_in": 3600},
            ]
        )
        monkeypatch.setattr(httpx, "AsyncClient", calls)
        # First call — full path.
        tok1 = oidc.get_impersonated_token(
            "alice@x",
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        # Second call — should hit cache; if it hits network again, we set up a
        # single-response mock that would raise IndexError on the third call.
        tok2 = oidc.get_impersonated_token(
            "alice@x",
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        assert tok1 == tok2 == "user"


@pytest.fixture(autouse=True)
def _clear_cache():
    oidc._clear_cache_for_tests()
    yield
    oidc._clear_cache_for_tests()
