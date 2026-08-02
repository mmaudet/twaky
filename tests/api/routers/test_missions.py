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

    def test_include_terminal_false_uses_list_live(self, monkeypatch):
        """Default (include_terminal absent/false) must call list_live only."""
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        live = _fake_mission("live", state=MissionState.RUNNING)
        with (
            patch(
                "twaky.api.routers.missions.repository.list_live", return_value=[live]
            ) as mock_live,
            patch(
                "twaky.api.routers.missions.repository.list_all", return_value=[]
            ) as mock_all,
        ):
            r = TestClient(app).get("/missions", cookies=_cookie())
        assert r.status_code == 200
        assert len(r.json()) == 1
        mock_live.assert_called_once()
        mock_all.assert_not_called()

    def test_include_terminal_true_uses_list_all(self, monkeypatch):
        """include_terminal=true must call list_all and include terminal states."""
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        live = _fake_mission("live", state=MissionState.RUNNING)
        done = _fake_mission("done", state=MissionState.DONE)
        failed = _fake_mission("fail", state=MissionState.FAILED)
        with (
            patch(
                "twaky.api.routers.missions.repository.list_all",
                return_value=[live, done, failed],
            ) as mock_all,
            patch(
                "twaky.api.routers.missions.repository.list_live", return_value=[]
            ) as mock_live,
        ):
            r = TestClient(app).get(
                "/missions?include_terminal=true", cookies=_cookie()
            )
        assert r.status_code == 200
        states = {row["state"] for row in r.json()}
        assert states == {"running", "done", "failed"}
        mock_all.assert_called_once()
        mock_live.assert_not_called()


class TestDetail:
    def test_get_returns_mission(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        m = _fake_mission()
        with patch("twaky.api.routers.missions.repository.get", return_value=m):
            r = TestClient(app).get(f"/missions/{m.id}", cookies=_cookie())
        assert r.status_code == 200
        assert r.json()["intent_text"] == m.intent_text

    def test_get_missing_returns_404(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        with patch("twaky.api.routers.missions.repository.get", return_value=None):
            r = TestClient(app).get(f"/missions/{uuid4()}", cookies=_cookie())
        assert r.status_code == 404

    def test_get_wrong_owner_returns_404(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        # Mission belongs to bob@x, not alice@x
        m = _fake_mission()
        m.owner_email = "bob@x"
        with patch("twaky.api.routers.missions.repository.get", return_value=m):
            r = TestClient(app).get(f"/missions/{m.id}", cookies=_cookie())
        assert r.status_code == 404


class TestResume:
    def test_resume_happy(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        m = _fake_mission(state=MissionState.AWAITING_USER)
        with (
            patch("twaky.api.routers.missions.engine.resume") as mock_resume,
            patch("twaky.api.routers.missions.repository.get", return_value=m),
        ):
            r = TestClient(app).post(
                f"/missions/{m.id}/resume",
                json={"user_response": {"approved": True}},
                cookies=_cookie(),
            )
        assert r.status_code == 200
        mock_resume.assert_called_once_with(m.id, user_response={"approved": True})

    def test_resume_wrong_state_returns_409(self, monkeypatch):
        from twaky import config as _cfg
        from twaky.missions.guards import InvalidTransition

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        m = _fake_mission(state=MissionState.RUNNING)
        with (
            patch(
                "twaky.api.routers.missions.engine.resume",
                side_effect=InvalidTransition("bad"),
            ),
            patch("twaky.api.routers.missions.repository.get", return_value=m),
        ):
            r = TestClient(app).post(
                f"/missions/{m.id}/resume",
                json={"user_response": {}},
                cookies=_cookie(),
            )
        assert r.status_code == 409

    def test_resume_missing_returns_404(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        with patch("twaky.api.routers.missions.repository.get", return_value=None):
            r = TestClient(app).post(
                f"/missions/{uuid4()}/resume",
                json={"user_response": {}},
                cookies=_cookie(),
            )
        assert r.status_code == 404

    def test_resume_wrong_owner_returns_404(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        # Mission belongs to bob@x, not alice@x
        m = _fake_mission(state=MissionState.AWAITING_USER)
        m.owner_email = "bob@x"
        with patch("twaky.api.routers.missions.repository.get", return_value=m):
            r = TestClient(app).post(
                f"/missions/{m.id}/resume",
                json={"user_response": {"approved": True}},
                cookies=_cookie(),
            )
        assert r.status_code == 404


class TestCancel:
    def test_cancel_happy(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        m = _fake_mission()
        with (
            patch("twaky.api.routers.missions.engine.cancel") as mock_cancel,
            patch("twaky.api.routers.missions.repository.get", return_value=m),
        ):
            r = TestClient(app).post(
                f"/missions/{m.id}/cancel",
                json={"reason": "user_requested"},
                cookies=_cookie(),
            )
        assert r.status_code == 200
        mock_cancel.assert_called_once_with(m.id, reason="user_requested")

    def test_cancel_terminal_returns_409(self, monkeypatch):
        from twaky import config as _cfg
        from twaky.missions.guards import InvalidTransition

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        m = _fake_mission(state=MissionState.DONE)
        with (
            patch(
                "twaky.api.routers.missions.engine.cancel",
                side_effect=InvalidTransition("bad"),
            ),
            patch("twaky.api.routers.missions.repository.get", return_value=m),
        ):
            r = TestClient(app).post(
                f"/missions/{m.id}/cancel",
                json={"reason": "x"},
                cookies=_cookie(),
            )
        assert r.status_code == 409

    def test_cancel_wrong_owner_returns_404(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)

        # Mission belongs to bob@x, not alice@x
        m = _fake_mission()
        m.owner_email = "bob@x"
        with patch("twaky.api.routers.missions.repository.get", return_value=m):
            r = TestClient(app).post(
                f"/missions/{m.id}/cancel",
                json={"reason": "user_requested"},
                cookies=_cookie(),
            )
        assert r.status_code == 404


class TestTrace:
    def test_trace_redirects_to_langfuse(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example.com")
        monkeypatch.setenv("LANGFUSE_PROJECT_ID", "proj-42")
        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)
        monkeypatch.setattr("twaky.api.routers.missions._settings", new_settings)
        m = _fake_mission()
        with patch("twaky.api.routers.missions.repository.get", return_value=m):
            r = TestClient(app).get(
                f"/missions/{m.id}/trace", cookies=_cookie(), follow_redirects=False
            )
        assert r.status_code == 302
        loc = r.headers["location"]
        assert "langfuse.example.com" in loc
        assert f"mission-{m.id}" in loc

    def test_trace_missing_config_returns_503(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setenv("LANGFUSE_HOST", "")
        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)
        monkeypatch.setattr("twaky.api.routers.missions._settings", new_settings)
        m = _fake_mission()
        with patch("twaky.api.routers.missions.repository.get", return_value=m):
            r = TestClient(app).get(
                f"/missions/{m.id}/trace", cookies=_cookie(), follow_redirects=False
            )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "langfuse_not_configured"
