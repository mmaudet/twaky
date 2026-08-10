"""API tests for /mail-sentinel/auth endpoints.

These tests require a live Postgres instance (TWAKY_PG_HOST=172.27.0.33)
because the oauth repository uses the real DB pool.

Auth is cookie-only: _cookie() returns a signed session for "alice@x",
which matches TWAKY_OWNER_EMAIL set in the autouse _env fixture.

Error envelope shape: {"error": {"code": "...", "message": "..."}}
(the SP6 error_response() pattern, registered in errors.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session
from twaky.crypto.secrets import encrypt
from twaky.oauth import repository

# ---------------------------------------------------------------------------
# Module-level Fernet key — generated once, stable across all tests in session
# ---------------------------------------------------------------------------

_FERNET_KEY = Fernet.generate_key().decode()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("MODEL", "sentinel-default-model")
    monkeypatch.setenv("TWAKY_SECRET_KEY", _FERNET_KEY)
    # Rebuild Settings from updated env and patch every module that holds a reference
    from twaky import config as _cfg
    from twaky.crypto import secrets as _crypto

    cfg = _cfg.Settings(_env_file=None)
    monkeypatch.setattr("twaky.api.deps.settings", cfg)
    monkeypatch.setattr("twaky.crypto.secrets.settings", cfg)
    _crypto._fernet.cache_clear()
    yield
    _crypto._fernet.cache_clear()


@pytest.fixture(autouse=True)
def _wipe():
    """Delete all oauth_credential rows before and after each test."""
    _delete_mail_cred()
    yield
    _delete_mail_cred()


def _delete_mail_cred() -> None:
    try:
        repository.delete("mail")
    except Exception:  # noqa: BLE001, S110
        pass


def _cookie() -> dict[str, str]:
    """Return a valid owner session cookie dict."""
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _owner_client(monkeypatch) -> TestClient:
    """Return a TestClient wired with the test env settings and owner session."""
    from twaky import config as _cfg

    monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
    return TestClient(app, cookies=_cookie())


def _unauthenticated_client() -> TestClient:
    """Return a TestClient with no session cookie."""
    return TestClient(app)


def _seed_cred(*, account_email: str = "alice@example.com") -> None:
    """Insert a realistic mail oauth_credential row for testing."""
    from twaky.crypto import secrets as _crypto

    # Ensure the test key is active before encrypting
    _crypto._fernet.cache_clear()
    repository.upsert(
        sentinel_name="mail",
        provider="linagora_lemonldap",
        client_id="twaky-mail-sentinel",
        token_endpoint="https://auth.example.com/oauth2/token",
        session_url="https://jmap.example.com/jmap/session",
        scope="openid profile email offline_access",
        refresh_token_enc=encrypt("test_rt"),
        access_token_enc=encrypt("test_at"),
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        account_email=account_email,
        account_name="Alice Test",
    )


# ---------------------------------------------------------------------------
# GET /mail-sentinel/auth
# ---------------------------------------------------------------------------


class TestGetAuthStatus:
    def test_get_401_unauthenticated(self):
        r = _unauthenticated_client().get("/mail-sentinel/auth")
        assert r.status_code == 401

    def test_get_returns_disconnected_when_no_row(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/mail-sentinel/auth")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is False
        assert body["provider"] is None
        assert body["account_email"] is None
        assert body["account_name"] is None
        assert body["session_url"] is None
        assert body["access_token_expires_at"] is None
        assert body["last_refresh_at"] is None
        assert body["last_refresh_error"] is None

    def test_get_returns_connected_after_upsert(self, monkeypatch):
        _seed_cred(account_email="alice@example.com")
        r = _owner_client(monkeypatch).get("/mail-sentinel/auth")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is True
        assert body["provider"] == "linagora_lemonldap"
        assert body["account_email"] == "alice@example.com"
        assert body["account_name"] == "Alice Test"
        assert body["session_url"] == "https://jmap.example.com/jmap/session"
        assert body["access_token_expires_at"] is not None
        assert body["last_refresh_at"] is not None
        assert body["last_refresh_error"] is None


# ---------------------------------------------------------------------------
# POST /mail-sentinel/auth/refresh
# ---------------------------------------------------------------------------


class TestRefreshAuth:
    def test_refresh_401_unauthenticated(self):
        r = _unauthenticated_client().post("/mail-sentinel/auth/refresh")
        assert r.status_code == 401

    def test_refresh_409_when_no_credential(self, monkeypatch):
        r = _owner_client(monkeypatch).post("/mail-sentinel/auth/refresh")
        assert r.status_code == 409
        body = r.json()
        assert body["error"]["code"] == "oauth_credential_not_found"

    def test_refresh_calls_manager_and_returns_updated_status(self, monkeypatch):
        _seed_cred(account_email="alice@example.com")
        mock_manager = MagicMock()
        mock_manager.force_refresh = AsyncMock(return_value="new-token")
        monkeypatch.setattr(
            "twaky.api.routers.mail_sentinel_auth.get_manager",
            lambda _name: mock_manager,
        )
        r = _owner_client(monkeypatch).post("/mail-sentinel/auth/refresh")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is True
        assert body["account_email"] == "alice@example.com"
        mock_manager.force_refresh.assert_called_once()

    def test_refresh_502_when_refresh_failed(self, monkeypatch):
        from twaky.oauth.refresh_manager import RefreshFailed

        _seed_cred()
        mock_manager = MagicMock()
        mock_manager.force_refresh = AsyncMock(
            side_effect=RefreshFailed("invalid_grant")
        )
        monkeypatch.setattr(
            "twaky.api.routers.mail_sentinel_auth.get_manager",
            lambda _name: mock_manager,
        )
        r = _owner_client(monkeypatch).post("/mail-sentinel/auth/refresh")
        assert r.status_code == 502
        body = r.json()
        assert body["error"]["code"] == "refresh_failed"
        assert body["error"]["message"] == "invalid_grant"


# ---------------------------------------------------------------------------
# DELETE /mail-sentinel/auth
# ---------------------------------------------------------------------------


class TestDeleteAuth:
    def test_delete_401_unauthenticated(self):
        r = _unauthenticated_client().delete("/mail-sentinel/auth")
        assert r.status_code == 401

    def test_delete_204_and_get_shows_disconnected(self, monkeypatch):
        _seed_cred()
        client = _owner_client(monkeypatch)
        # Confirm connected
        r = client.get("/mail-sentinel/auth")
        assert r.json()["connected"] is True

        # Delete
        r = client.delete("/mail-sentinel/auth")
        assert r.status_code == 204

        # GET now shows disconnected
        r = client.get("/mail-sentinel/auth")
        assert r.status_code == 200
        assert r.json()["connected"] is False

    def test_delete_204_idempotent(self, monkeypatch):
        client = _owner_client(monkeypatch)
        # First delete (no row)
        r = client.delete("/mail-sentinel/auth")
        assert r.status_code == 204
        # Second delete (still no row)
        r = client.delete("/mail-sentinel/auth")
        assert r.status_code == 204
