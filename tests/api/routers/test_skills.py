"""API tests for GET /skills and GET /skills/{id}."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session
from twaky.skills_config.models import Skill


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("MODEL", "sentinel-default-model")


def _cookie() -> dict[str, str]:
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _fake_skill(
    name: str = "echo",
    skill_id: UUID | None = None,
) -> Skill:
    return Skill(
        id=skill_id or uuid4(),
        name=name,
        description="Echo tool",
        python_source="def run(**kw): return 1",
        config_schema={},
        config_values={},
        bound_agents=["atlas"],
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestListSkills:
    def test_no_session_returns_401(self):
        r = TestClient(app).get("/skills")
        assert r.status_code == 401

    def test_empty_list(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        with patch("twaky.api.routers.skills.repository.list_all", return_value=[]):
            r = TestClient(app).get("/skills", cookies=_cookie())
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_summaries(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        rows = [_fake_skill("a"), _fake_skill("b")]
        with patch("twaky.api.routers.skills.repository.list_all", return_value=rows):
            r = TestClient(app).get("/skills", cookies=_cookie())
        assert r.status_code == 200
        body = r.json()
        assert [s["name"] for s in body] == ["a", "b"]
        # SkillSummary omits python_source + config_*
        assert "python_source" not in body[0]
        assert "config_schema" not in body[0]
        assert "config_values" not in body[0]


class TestGetSkill:
    def test_happy_returns_full_shape(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        sk = _fake_skill("echo")
        with patch("twaky.api.routers.skills.repository.get", return_value=sk):
            r = TestClient(app).get(f"/skills/{sk.id}", cookies=_cookie())
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "echo"
        assert body["python_source"] == "def run(**kw): return 1"
        assert body["config_schema"] == {}
        assert body["bound_agents"] == ["atlas"]

    def test_404_for_unknown_id(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        with patch("twaky.api.routers.skills.repository.get", return_value=None):
            r = TestClient(app).get(f"/skills/{uuid4()}", cookies=_cookie())
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "skill_not_found"

    def test_422_for_malformed_uuid(self, monkeypatch):
        from twaky import config as _cfg

        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        r = TestClient(app).get("/skills/not-a-uuid", cookies=_cookie())
        # FastAPI path-param validation rejects non-UUID strings with 422
        assert r.status_code == 422
