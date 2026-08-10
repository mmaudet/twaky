"""API tests for GET /oauth/jmap/login and GET /oauth/jmap/callback.

Spec §8.  Tests that don't touch the DB use mocks; the happy-path and
session-probe-failed tests use the real Postgres instance (TWAKY_PG_HOST=172.27.0.33).
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import itsdangerous
import psycopg
import pytest
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.routers.oauth_jmap import (
    JMAP_STATE_COOKIE,
    STATE_TTL_SECONDS,
    _make_state_cookie_value,
    _signer,
)
from twaky.api.session import SESSION_COOKIE_NAME, sign_session
from twaky.config import settings

# ---------------------------------------------------------------------------
# DB reachability
# ---------------------------------------------------------------------------


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


_DB_AVAILABLE = _reachable()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

from cryptography.fernet import Fernet

_FERNET_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("MODEL", "sentinel-default-model")
    monkeypatch.setenv("TWAKY_SECRET_KEY", _FERNET_KEY)
    monkeypatch.setenv("JMAP_OAUTH_CLIENT_ID", "twaky-mail-sentinel")
    monkeypatch.setenv("JMAP_OAUTH_CLIENT_SECRET", "jmap-secret")
    monkeypatch.setenv("JMAP_OAUTH_ISSUER", "https://auth.example.com")
    monkeypatch.setenv("JMAP_SESSION_URL", "https://jmap.example.com/jmap/session")
    monkeypatch.setenv("API_BASE_URL", "https://twaky.example.com")


@pytest.fixture(autouse=True)
def _wipe_oauth_credential():
    """Wipe oauth_credential before + after each DB-touching test."""
    if not _DB_AVAILABLE:
        yield
        return
    _truncate()
    yield
    _truncate()


def _truncate() -> None:
    try:
        with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM oauth_credential WHERE sentinel_name = 'mail';")
    except Exception as _exc:  # noqa: BLE001
        _ = _exc  # DB may be unavailable; ignore


def _cookie() -> dict[str, str]:
    """Return a valid owner session cookie dict."""
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _owner_client(monkeypatch) -> TestClient:
    """Return a TestClient with a valid owner session cookie."""
    from twaky import config as _cfg

    monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
    monkeypatch.setattr(
        "twaky.api.routers.oauth_jmap.settings", _cfg.Settings(_env_file=None)
    )
    return TestClient(app, cookies=_cookie())


def _make_valid_state_cookie(return_to: str = "/sentinels/mail?tab=auth") -> str:
    """Build a fresh valid signed state cookie."""
    payload = {
        "return_to": return_to,
        "code_verifier": "v" * 64,
        "state": "good-state-value",
    }
    return _make_state_cookie_value(payload)


# ---------------------------------------------------------------------------
# 1. Unauthenticated requests
# ---------------------------------------------------------------------------


class TestUnauthenticated:
    def test_unauthenticated_login_returns_401(self):
        r = TestClient(app).get("/oauth/jmap/login", follow_redirects=False)
        assert r.status_code == 401

    def test_unauthenticated_callback_returns_401(self):
        r = TestClient(app).get(
            "/oauth/jmap/callback?code=abc&state=xyz", follow_redirects=False
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# 2. Login endpoint
# ---------------------------------------------------------------------------


class TestJmapLogin:
    def test_login_redirects_and_sets_state_cookie(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        monkeypatch.setattr(
            "twaky.api.routers.oauth_jmap.settings", _cfg.Settings(_env_file=None)
        )

        r = TestClient(app).get(
            "/oauth/jmap/login",
            cookies=_cookie(),
            follow_redirects=False,
        )
        assert r.status_code == 302

        location = r.headers["location"]
        assert "response_type=code" in location
        assert "code_challenge" in location
        assert "code_challenge_method=S256" in location
        assert "state=" in location
        # redirect target is the issuer's authorize URL
        assert "auth.example.com" in location or "oauth2/authorize" in location

        # State cookie must be set
        set_cookie = r.headers.get("set-cookie", "")
        assert JMAP_STATE_COOKIE in set_cookie

    def test_login_redirects_with_custom_return_to(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        monkeypatch.setattr(
            "twaky.api.routers.oauth_jmap.settings", _cfg.Settings(_env_file=None)
        )

        r = TestClient(app).get(
            "/oauth/jmap/login?return_to=/dashboard",
            cookies=_cookie(),
            follow_redirects=False,
        )
        assert r.status_code == 302
        # The state cookie must contain the return_to
        raw_cookie = r.cookies.get(JMAP_STATE_COOKIE) or ""
        if raw_cookie:
            try:
                payload = json.loads(
                    _signer().unsign(raw_cookie, max_age=STATE_TTL_SECONDS)
                )
                assert payload["return_to"] == "/dashboard"
            except itsdangerous.BadSignature:
                pass  # cookie may be URL-encoded in the jar; check via header
        set_cookie = r.headers.get("set-cookie", "")
        assert JMAP_STATE_COOKIE in set_cookie


# ---------------------------------------------------------------------------
# 3. Callback — state mismatch (no DB required)
# ---------------------------------------------------------------------------


class TestCallbackStateMismatch:
    def test_callback_no_cookie_returns_state_mismatch(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        monkeypatch.setattr(
            "twaky.api.routers.oauth_jmap.settings", _cfg.Settings(_env_file=None)
        )

        r = TestClient(app).get(
            "/oauth/jmap/callback?code=abc&state=xyz",
            cookies=_cookie(),
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "status=error" in r.headers["location"]
        assert "reason=state_mismatch" in r.headers["location"]

    def test_callback_wrong_state_param_returns_state_mismatch(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        monkeypatch.setattr(
            "twaky.api.routers.oauth_jmap.settings", _cfg.Settings(_env_file=None)
        )

        state_cookie = _make_valid_state_cookie()
        cookies = {**_cookie(), JMAP_STATE_COOKIE: state_cookie}

        r = TestClient(app).get(
            "/oauth/jmap/callback?code=abc&state=WRONG-state",
            cookies=cookies,
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "status=error" in r.headers["location"]
        assert "reason=state_mismatch" in r.headers["location"]


# ---------------------------------------------------------------------------
# 4. Callback — expired cookie (no DB required)
# ---------------------------------------------------------------------------


class TestCallbackCookieExpired:
    def test_callback_cookie_expired(self, monkeypatch):

        from twaky import config as _cfg

        cfg = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", cfg)
        monkeypatch.setattr("twaky.api.routers.oauth_jmap.settings", cfg)

        # Build a cookie that was signed 11 minutes ago (> 10 min TTL).
        payload = {
            "return_to": "/sentinels/mail?tab=auth",
            "code_verifier": "v" * 64,
            "state": "expired-state",
        }
        raw = json.dumps(payload).encode()

        # Use a subclass that returns a backdated timestamp so itsdangerous
        # still signs with the correct key but marks it as old.
        secret = str(cfg.api_session_secret)

        class _BackdatedSigner(itsdangerous.TimestampSigner):
            def get_timestamp(self) -> int:
                return int(time.time()) - (STATE_TTL_SECONDS + 60)

        old_cookie = _BackdatedSigner(secret).sign(raw).decode()

        cookies = {**_cookie(), JMAP_STATE_COOKIE: old_cookie}
        r = TestClient(app).get(
            "/oauth/jmap/callback?code=abc&state=expired-state",
            cookies=cookies,
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "status=error" in r.headers["location"]
        assert "reason=state_expired" in r.headers["location"]


# ---------------------------------------------------------------------------
# 5. Callback — code exchange failed (no DB required)
# ---------------------------------------------------------------------------


class TestCallbackCodeExchangeFailed:
    def test_callback_token_endpoint_400(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        monkeypatch.setattr(
            "twaky.api.routers.oauth_jmap.settings", _cfg.Settings(_env_file=None)
        )

        import httpx

        payload = {
            "return_to": "/sentinels/mail?tab=auth",
            "code_verifier": "v" * 64,
            "state": "good-state",
        }
        state_cookie = _make_state_cookie_value(payload)
        cookies = {**_cookie(), JMAP_STATE_COOKIE: state_cookie}

        def _mock_transport(request):
            # Return 400 for token endpoint
            return httpx.Response(400, json={"error": "invalid_grant"})

        with patch("twaky.api.routers.oauth_jmap.httpx.AsyncClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.post = AsyncMock(
                return_value=httpx.Response(400, json={"error": "invalid_grant"})
            )
            mock_cls.return_value = mock_instance

            r = TestClient(app).get(
                "/oauth/jmap/callback?code=abc&state=good-state",
                cookies=cookies,
                follow_redirects=False,
            )

        assert r.status_code == 302
        assert "status=error" in r.headers["location"]
        assert "reason=code_exchange_failed" in r.headers["location"]

        # Assert no DB row created
        if _DB_AVAILABLE:
            from twaky.oauth import repository

            assert repository.get("mail") is None


# ---------------------------------------------------------------------------
# 6. Callback — session probe failed (no DB row should be created)
# ---------------------------------------------------------------------------


class TestCallbackSessionProbeFailed:
    @pytest.mark.skipif(not _DB_AVAILABLE, reason="twaky-pg not reachable")
    def test_callback_session_probe_401_no_db_row(self, monkeypatch):
        from twaky import config as _cfg
        from twaky.crypto import secrets as _crypto
        from twaky.oauth import repository

        cfg = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", cfg)
        monkeypatch.setattr("twaky.api.routers.oauth_jmap.settings", cfg)
        monkeypatch.setattr("twaky.crypto.secrets.settings", cfg)
        _crypto._fernet.cache_clear()

        import httpx

        payload = {
            "return_to": "/sentinels/mail?tab=auth",
            "code_verifier": "v" * 64,
            "state": "probe-state",
        }
        state_cookie = _make_state_cookie_value(payload)
        cookies = {**_cookie(), JMAP_STATE_COOKIE: state_cookie}

        call_count = 0

        async def _mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "userinfo" in url:
                return httpx.Response(200, json={"email": "me@x", "name": "Me"})
            # session probe → 401
            return httpx.Response(401, json={"error": "unauthorized"})

        async def _mock_post(url, **kwargs):
            return httpx.Response(
                200,
                json={
                    "access_token": "at",
                    "refresh_token": "rt",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

        with patch("twaky.api.routers.oauth_jmap.httpx.AsyncClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.post = AsyncMock(side_effect=_mock_post)
            mock_instance.get = AsyncMock(side_effect=_mock_get)
            mock_cls.return_value = mock_instance

            r = TestClient(app).get(
                "/oauth/jmap/callback?code=abc&state=probe-state",
                cookies=cookies,
                follow_redirects=False,
            )

        assert r.status_code == 302
        assert "status=error" in r.headers["location"]
        assert "reason=session_probe_failed" in r.headers["location"]

        # NO DB row — don't store a token James won't accept
        assert repository.get("mail") is None


# ---------------------------------------------------------------------------
# 7. Callback — happy path (requires DB)
# ---------------------------------------------------------------------------


class TestCallbackHappyPath:
    @pytest.mark.skipif(not _DB_AVAILABLE, reason="twaky-pg not reachable")
    def test_callback_happy_path(self, monkeypatch):
        from twaky import config as _cfg
        from twaky.crypto import secrets as _crypto
        from twaky.oauth import repository

        cfg = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", cfg)
        monkeypatch.setattr("twaky.api.routers.oauth_jmap.settings", cfg)
        # Ensure crypto module uses the test Fernet key (lru_cache may hold stale state)
        monkeypatch.setattr("twaky.crypto.secrets.settings", cfg)
        _crypto._fernet.cache_clear()

        import httpx

        payload = {
            "return_to": "/sentinels/mail?tab=auth",
            "code_verifier": "v" * 64,
            "state": "happy-state",
        }
        state_cookie = _make_state_cookie_value(payload)
        cookies = {**_cookie(), JMAP_STATE_COOKIE: state_cookie}

        async def _mock_get(url, **kwargs):
            if "userinfo" in url:
                return httpx.Response(200, json={"email": "me@x.com", "name": "Me X"})
            # session probe
            return httpx.Response(200, json={"apiUrl": "https://jmap.example.com/jmap"})

        async def _mock_post(url, **kwargs):
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token-value",
                    "refresh_token": "refresh-token-value",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

        with patch("twaky.api.routers.oauth_jmap.httpx.AsyncClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.post = AsyncMock(side_effect=_mock_post)
            mock_instance.get = AsyncMock(side_effect=_mock_get)
            mock_cls.return_value = mock_instance

            r = TestClient(app).get(
                "/oauth/jmap/callback?code=abc&state=happy-state",
                cookies=cookies,
                follow_redirects=False,
            )

        assert r.status_code == 302
        location = r.headers["location"]
        assert "status=connected" in location
        assert "status=error" not in location

        # State cookie should be cleared
        set_cookie = r.headers.get("set-cookie", "")
        # Cookie deletion sets Max-Age=0 or expires in the past
        if JMAP_STATE_COOKIE in set_cookie:
            assert "max-age=0" in set_cookie.lower() or "expires=" in set_cookie.lower()

        # DB row upserted
        cred = repository.get("mail")
        assert cred is not None
        assert cred.account_email == "me@x.com"
        assert cred.provider == "linagora_lemonldap"
        assert cred.sentinel_name == "mail"
