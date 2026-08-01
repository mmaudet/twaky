"""OIDC login / callback / logout."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME


@pytest.fixture(autouse=True)
def _oidc_env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("API_OIDC_CLIENT_ID", "twaky-api")
    monkeypatch.setenv("API_OIDC_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("API_OIDC_ISSUER", "https://auth.example.com/")
    monkeypatch.setenv("API_BASE_URL", "https://twaky.example.com")


class TestLogin:
    def test_redirects_to_authorize(self):
        with patch("twaky.api.routers.oauth.oauth_client") as make:
            oauth = MagicMock()
            client = MagicMock()
            client.authorize_redirect = AsyncMock(
                return_value=MagicMock(
                    status_code=302,
                    headers={
                        "location": "https://auth.example.com/oauth2/authorize?...",
                        "set-cookie": "",
                    },
                )
            )
            oauth.twaky_api = client
            make.return_value = oauth
            r = TestClient(app).get(
                "/oauth/login?return_to=/missions", follow_redirects=False
            )
        assert r.status_code == 302
        assert "authorize" in r.headers["location"]


class TestCallback:
    def test_bad_state_returns_400(self):
        # Without prior /oauth/login, state cookie missing → authlib rejects.
        r = TestClient(app).get(
            "/oauth/callback?code=abc&state=xyz", follow_redirects=False
        )
        assert r.status_code == 400

    def test_email_not_owner_returns_403(self):
        with patch("twaky.api.routers.oauth.oauth_client") as make:
            oauth = MagicMock()
            client = MagicMock()
            client.authorize_access_token = AsyncMock(
                return_value={
                    "access_token": "at",
                    "userinfo": {"email": "bob@x", "sub": "bob-uuid"},
                }
            )
            oauth.twaky_api = client
            make.return_value = oauth
            r = TestClient(app).get(
                "/oauth/callback?code=abc&state=xyz", follow_redirects=False
            )
        assert r.status_code == 403

    def test_owner_email_sets_session(self):
        with patch("twaky.api.routers.oauth.oauth_client") as make:
            oauth = MagicMock()
            client = MagicMock()
            client.authorize_access_token = AsyncMock(
                return_value={
                    "access_token": "at",
                    "userinfo": {"email": "alice@x", "sub": "alice-uuid"},
                }
            )
            oauth.twaky_api = client
            make.return_value = oauth
            r = TestClient(app).get(
                "/oauth/callback?code=abc&state=xyz", follow_redirects=False
            )
        assert r.status_code == 302
        # Cookie set on the response.
        assert SESSION_COOKIE_NAME in r.cookies


class TestLogout:
    def test_purges_session_and_redirects(self):
        r = TestClient(app).post("/oauth/logout", follow_redirects=False)
        # Redirect to LemonLDAP end-session endpoint.
        assert r.status_code == 302
        # Session cookie cleared (empty or expired).
        assert r.cookies.get(SESSION_COOKIE_NAME) in (None, "")
