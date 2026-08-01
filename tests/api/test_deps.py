"""require_owner FastAPI dependency."""

from __future__ import annotations

import importlib

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

# Import testing to ensure it's loaded before we reload session in fixtures
from twaky.api import testing  # noqa: F401
from twaky.api.deps import require_owner
from twaky.api.session import SESSION_COOKIE_NAME, sign_session


def _setup_test_env(monkeypatch):
    """Setup test environment with required env vars."""
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    # Force settings reload to pick up env vars, then reload modules that import it
    # Note: We don't reload twaky.api.testing to preserve the identity check in tests
    import twaky.api.deps
    import twaky.api.session
    import twaky.config

    importlib.reload(twaky.config)
    importlib.reload(twaky.api.session)
    importlib.reload(twaky.api.deps)


@pytest.fixture
def secret_env(monkeypatch):
    _setup_test_env(monkeypatch)
    yield


@pytest.fixture
def test_app(secret_env):
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="test-secret-32bytes-min-abcdefgh",
        session_cookie=SESSION_COOKIE_NAME,
        max_age=28800,
        same_site="lax",
        https_only=False,  # tests over http
    )

    @app.get("/protected")
    def _protected(email: str = Depends(require_owner)) -> dict:
        return {"email": email}

    return app


class TestRequireOwner:
    def test_no_cookie_returns_401(self, test_app):
        client = TestClient(test_app)
        r = client.get("/protected")
        assert r.status_code == 401

    def test_valid_owner_returns_200(self, test_app):
        client = TestClient(test_app)
        cookie = sign_session("alice@x")
        client.cookies.set(SESSION_COOKIE_NAME, cookie)
        r = client.get("/protected")
        assert r.status_code == 200
        assert r.json() == {"email": "alice@x"}

    def test_wrong_email_returns_403(self, test_app):
        client = TestClient(test_app)
        cookie = sign_session("bob@x")
        client.cookies.set(SESSION_COOKIE_NAME, cookie)
        r = client.get("/protected")
        assert r.status_code == 403
