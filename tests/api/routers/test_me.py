"""GET /me — authenticated user info."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")


class TestMe:
    def test_no_session_returns_401(self):
        r = TestClient(app).get("/me")
        assert r.status_code == 401

    def test_authenticated_returns_owner_info(self, monkeypatch):
        # Patch the settings singleton so the owner is "alice@x".
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)
        monkeypatch.setattr("twaky.api.routers.me.settings", new_settings)
        cookie = sign_session("alice@x")
        r = TestClient(app).get("/me", cookies={SESSION_COOKIE_NAME: cookie})
        assert r.status_code == 200
        body = r.json()
        assert body["owner_email"] == "alice@x"
        assert "langfuse_base_url" in body
