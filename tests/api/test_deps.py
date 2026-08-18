"""require_owner FastAPI dependency."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from twaky.api.deps import require_owner
from twaky.api.session import SESSION_COOKIE_NAME, sign_session
from twaky.config import settings

_TEST_SECRET = "test-secret-32bytes-min-abcdefgh"


@pytest.fixture
def secret_env(monkeypatch):
    """Point the settings singleton at this module's test identity.

    Patches the *object*, never the environment. ``monkeypatch.setenv`` alone
    does nothing here — pydantic-settings reads the environment once, at
    construction — and the obvious workaround, ``importlib.reload(twaky.config)``,
    is worse than the problem: it rebinds ``twaky.config.settings`` to a brand
    new instance for the remainder of the session, while the ~120 modules that
    did ``from twaky.config import settings`` keep the old one. Tests running
    later then patch one object and get checked against another (observed as
    401s, then 403s, in tests/integration/test_api_*.py).

    ``require_owner`` and ``sign_session`` both read their attributes off
    ``settings`` at call time, so setattr is sufficient — and monkeypatch
    reverts it.
    """
    monkeypatch.setattr(settings, "api_session_secret", _TEST_SECRET)
    monkeypatch.setattr(settings, "twaky_owner_email", "alice@x")
    yield


@pytest.fixture
def test_app(secret_env):
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key=_TEST_SECRET,
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
