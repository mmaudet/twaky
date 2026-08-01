"""Mission CRUD API — declare + list."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session
from twaky.missions.models import Mission, MissionState


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")


def _cookie() -> dict[str, str]:
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _fake_mission(
    intent: str = "hi", state: MissionState = MissionState.DECLARED
) -> Mission:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return Mission(
        id=uuid4(),
        intent_text=intent,
        owner_email="alice@x",
        declared_by="alice@x",
        declared_at=now,
        state=state,
        plan=[],
        artifacts=[],
        created_at=now,
        updated_at=now,
    )


class TestDeclare:
    def test_no_session_returns_401(self):
        r = TestClient(app).post("/missions", json={"intent_text": "hi"})
        assert r.status_code == 401

    def test_happy_path_returns_201(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        m = _fake_mission()
        with patch("twaky.api.routers.missions.engine.declare", return_value=m) as decl:
            r = TestClient(app).post(
                "/missions", json={"intent_text": "hi"}, cookies=_cookie()
            )
        assert r.status_code == 201
        body = r.json()
        assert body["intent_text"] == "hi"
        assert body["owner_email"] == "alice@x"
        decl.assert_called_once()
        kwargs = decl.call_args.kwargs
        assert kwargs["intent_text"] == "hi"
        assert kwargs["owner_email"] == "alice@x"
        assert kwargs["declared_by"] == "alice@x"

    def test_missing_intent_returns_422(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        r = TestClient(app).post("/missions", json={}, cookies=_cookie())
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"


class TestList:
    def test_no_session_returns_401(self):
        r = TestClient(app).get("/missions")
        assert r.status_code == 401

    def test_returns_list(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        m1 = _fake_mission("one")
        m2 = _fake_mission("two", state=MissionState.RUNNING)
        with patch(
            "twaky.api.routers.missions.repository.list_live", return_value=[m1, m2]
        ):
            r = TestClient(app).get("/missions", cookies=_cookie())
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        assert body[0]["intent_text"] == "one"

    def test_state_filter(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        m = _fake_mission("run", state=MissionState.RUNNING)
        with patch("twaky.api.routers.missions.repository.list_live", return_value=[m]):
            r = TestClient(app).get("/missions?state=running", cookies=_cookie())
        assert r.status_code == 200
        assert all(row["state"] == "running" for row in r.json())

    def test_invalid_state_returns_422(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        r = TestClient(app).get("/missions?state=BOGUS", cookies=_cookie())
        assert r.status_code == 422
