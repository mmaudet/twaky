"""GET /agents surface (mounted without /api prefix) — auth + happy + 404 cases."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from twaky.agents_config.models import AgentConfig
from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("MODEL", "sentinel-default-model")


def _cookie() -> dict[str, str]:
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _fake_cfg(agent_id: str, model: str | None = None) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        display_name=agent_id.capitalize(),
        role="orchestrator" if agent_id == "atlas" else "specialist",
        system_prompt="you are " + agent_id,
        model=model,
        temperature=None,
        updated_at=datetime.now(UTC),
    )


class TestListAgents:
    def test_no_session_returns_401(self):
        r = TestClient(app).get("/agents")
        assert r.status_code == 401

    def test_happy_returns_summaries(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        rows = [
            _fake_cfg("atlas"),
            _fake_cfg("chronos"),
            _fake_cfg("plume"),
            _fake_cfg("iris"),
        ]
        with patch("twaky.api.routers.agents.repository.list_all", return_value=rows):
            r = TestClient(app).get("/agents", cookies=_cookie())
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 4
        assert {a["id"] for a in body} == {"atlas", "chronos", "plume", "iris"}
        assert "system_prompt" not in body[0]  # summary shape
        assert all("effective_model" in a for a in body)


class TestGetAgent:
    def test_happy(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        cfg = _fake_cfg("plume", model="openai/gpt-4o")
        with patch("twaky.api.routers.agents.repository.get", return_value=cfg):
            r = TestClient(app).get("/agents/plume", cookies=_cookie())
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "plume"
        assert body["model"] == "openai/gpt-4o"
        assert body["effective_model"] == "openai/gpt-4o"
        assert body["system_prompt"] == "you are plume"

    def test_effective_model_falls_back(self, monkeypatch):
        from twaky import config as _cfg

        new_settings = _cfg.Settings(_env_file=None)
        monkeypatch.setattr("twaky.api.deps.settings", new_settings)
        monkeypatch.setattr("twaky.agents_config.service.settings", new_settings)

        # settings.model comes from the TWAKY_MODEL env fixture at module top.
        cfg = _fake_cfg("plume", model=None)
        with patch("twaky.api.routers.agents.repository.get", return_value=cfg):
            r = TestClient(app).get("/agents/plume", cookies=_cookie())
        assert r.json()["effective_model"] == "sentinel-default-model"

    def test_missing_returns_404(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        with patch("twaky.api.routers.agents.repository.get", return_value=None):
            r = TestClient(app).get("/agents/zeus", cookies=_cookie())
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "agent_not_found"


class TestDefaultPrompt:
    def test_happy(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        r = TestClient(app).get("/agents/plume/default_prompt", cookies=_cookie())
        assert r.status_code == 200
        body = r.json()
        assert "system_prompt" in body
        assert body["system_prompt"]  # non-empty

    def test_unknown_returns_404(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        r = TestClient(app).get("/agents/zeus/default_prompt", cookies=_cookie())
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "agent_not_found"


class TestPatchAgent:
    def test_no_session_returns_401(self):
        r = TestClient(app).patch("/agents/plume", json={"temperature": 0.3})
        assert r.status_code == 401

    def test_happy_temperature(self, monkeypatch):
        from dataclasses import replace

        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        updated_cfg = replace(_fake_cfg("plume"), temperature=0.3)

        with patch(
            "twaky.api.routers.agents.repository.update",
            return_value=updated_cfg,
        ) as up:
            r = TestClient(app).patch(
                "/agents/plume",
                json={"temperature": 0.3},
                cookies=_cookie(),
            )
        assert r.status_code == 200
        body = r.json()
        assert body["temperature"] == 0.3
        up.assert_called_once_with("plume", {"temperature": 0.3})

    def test_happy_model_null(self, monkeypatch):
        from dataclasses import replace

        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        cfg = replace(_fake_cfg("plume"), model=None)
        with patch("twaky.api.routers.agents.repository.update", return_value=cfg):
            r = TestClient(app).patch(
                "/agents/plume", json={"model": None}, cookies=_cookie()
            )
        assert r.status_code == 200
        assert r.json()["model"] is None

    def test_temperature_out_of_range_returns_422(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        r = TestClient(app).patch(
            "/agents/plume", json={"temperature": 3.0}, cookies=_cookie()
        )
        # Either pydantic (le=2.0) OR our service raises 422 — both acceptable
        assert r.status_code == 422

    def test_empty_prompt_returns_422(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        r = TestClient(app).patch(
            "/agents/plume", json={"system_prompt": "   "}, cookies=_cookie()
        )
        assert r.status_code == 422

    def test_empty_body_returns_422(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        r = TestClient(app).patch("/agents/plume", json={}, cookies=_cookie())
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_unknown_agent_returns_404(self, monkeypatch):
        from twaky import config as _cfg
        from twaky.agents_config.repository import AgentConfigNotFound

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        with patch(
            "twaky.api.routers.agents.repository.update",
            side_effect=AgentConfigNotFound("no"),
        ):
            r = TestClient(app).patch(
                "/agents/zeus", json={"temperature": 0.5}, cookies=_cookie()
            )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "agent_not_found"

    def test_unknown_field_returns_422(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        r = TestClient(app).patch(
            "/agents/plume", json={"tools": ["read_email"]}, cookies=_cookie()
        )
        # pydantic extra="forbid" rejects unknown fields with 422
        assert r.status_code == 422
