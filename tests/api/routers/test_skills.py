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
from twaky.skills_config.repository import SkillNameConflict, SkillNotFound


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


# ---------------------------------------------------------------------------
# Helpers for POST / PATCH / DELETE tests
# ---------------------------------------------------------------------------


def _owner_client(monkeypatch) -> TestClient:
    """Return a TestClient with a valid owner session cookie."""
    from twaky import config as _cfg

    monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
    return TestClient(app, cookies=_cookie())


def _valid_body(**over) -> dict:
    body: dict = {
        "name": "echo",
        "description": "Echo tool",
        "python_source": "def run(**kwargs):\n    return 'ok'",
        "bound_agents": ["atlas"],
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# POST /skills
# ---------------------------------------------------------------------------


class TestPostSkill:
    def test_post_401_without_session(self):
        r = TestClient(app).post("/skills", json=_valid_body())
        assert r.status_code == 401

    def test_post_creates_skill(self, monkeypatch):
        client = _owner_client(monkeypatch)
        sk = _fake_skill("echo")
        with patch("twaky.api.routers.skills.repository.create", return_value=sk):
            resp = client.post("/skills", json=_valid_body())
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "echo"
        assert body["bound_agents"] == ["atlas"]

    @pytest.mark.parametrize("bad_name", ["Echo", "1abc", "with-hyphen", ""])
    def test_post_422_on_bad_name(self, monkeypatch, bad_name):
        client = _owner_client(monkeypatch)
        # Pydantic validates name pattern before reaching the router body
        resp = client.post("/skills", json=_valid_body(name=bad_name))
        assert resp.status_code == 422

    def test_post_422_on_syntax_error(self, monkeypatch):
        client = _owner_client(monkeypatch)
        # Service layer: ast.parse fails → validation_failed
        resp = client.post("/skills", json=_valid_body(python_source="def run("))
        assert resp.status_code == 422
        assert "SyntaxError" in resp.json()["error"]["message"]

    def test_post_422_on_missing_run(self, monkeypatch):
        client = _owner_client(monkeypatch)
        # Service layer: no top-level def run → validation_failed
        resp = client.post(
            "/skills",
            json=_valid_body(python_source="def other():\n    pass"),
        )
        assert resp.status_code == 422
        assert "run" in resp.json()["error"]["message"].lower()

    def test_post_422_on_unknown_bound_agent(self, monkeypatch):
        client = _owner_client(monkeypatch)
        # Pydantic validates Literal["atlas", ...] before reaching router body
        resp = client.post("/skills", json=_valid_body(bound_agents=["atlas", "zeus"]))
        assert resp.status_code == 422

    def test_post_422_on_invalid_json_schema(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_body()
        body["config_schema"] = {"type": "not-a-real-type"}
        # Service layer: jsonschema.check_schema fails → validation_failed
        resp = client.post("/skills", json=body)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_failed"

    def test_post_422_on_config_values_mismatching_schema(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_body()
        body["config_schema"] = {
            "type": "object",
            "required": ["k"],
            "properties": {"k": {"type": "string"}},
        }
        body["config_values"] = {}
        # Service layer: jsonschema.validate fails → validation_failed
        resp = client.post("/skills", json=body)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_failed"

    def test_post_422_on_duplicate_name(self, monkeypatch):
        client = _owner_client(monkeypatch)
        with patch(
            "twaky.api.routers.skills.repository.create",
            side_effect=SkillNameConflict("echo"),
        ):
            resp = client.post("/skills", json=_valid_body(name="echo"))
        assert resp.status_code == 422
        assert "already exists" in resp.json()["error"]["message"]
        assert resp.json()["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------
# PATCH /skills/{skill_id}
# ---------------------------------------------------------------------------


class TestPatchSkill:
    def test_no_session_returns_401(self):
        r = TestClient(app).patch(f"/skills/{uuid4()}", json={"description": "x"})
        assert r.status_code == 401

    def test_patch_updates_description(self, monkeypatch):
        client = _owner_client(monkeypatch)
        sk = _fake_skill("echo")
        updated = _fake_skill("echo", skill_id=sk.id)
        # Return a skill with an updated description
        from dataclasses import replace

        updated = replace(updated, description="new")
        with patch("twaky.api.routers.skills.repository.update", return_value=updated):
            resp = client.patch(f"/skills/{sk.id}", json={"description": "new"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "new"

    def test_patch_422_on_empty_body(self, monkeypatch):
        client = _owner_client(monkeypatch)
        sk = _fake_skill("echo")
        # Empty body → service layer ValidationError ("at least one field required")
        resp = client.patch(f"/skills/{sk.id}", json={})
        assert resp.status_code == 422
        assert "at least one field" in resp.json()["error"]["message"]
        assert resp.json()["error"]["code"] == "validation_failed"

    def test_patch_404_on_unknown(self, monkeypatch):
        client = _owner_client(monkeypatch)
        with patch(
            "twaky.api.routers.skills.repository.update",
            side_effect=SkillNotFound("not-found"),
        ):
            resp = client.patch(f"/skills/{uuid4()}", json={"description": "x"})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "skill_not_found"

    def test_patch_422_on_name_collision(self, monkeypatch):
        client = _owner_client(monkeypatch)
        sk = _fake_skill("other")
        with patch(
            "twaky.api.routers.skills.repository.update",
            side_effect=SkillNameConflict("taken"),
        ):
            resp = client.patch(f"/skills/{sk.id}", json={"name": "taken"})
        assert resp.status_code == 422
        assert "already exists" in resp.json()["error"]["message"]
        assert resp.json()["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------
# DELETE /skills/{skill_id}
# ---------------------------------------------------------------------------


class TestDeleteSkill:
    def test_no_session_returns_401(self):
        r = TestClient(app).delete(f"/skills/{uuid4()}")
        assert r.status_code == 401

    def test_delete_returns_204(self, monkeypatch):
        client = _owner_client(monkeypatch)
        sk = _fake_skill("echo")
        with patch("twaky.api.routers.skills.repository.delete", return_value=True):
            resp = client.delete(f"/skills/{sk.id}")
        assert resp.status_code == 204
        assert resp.content == b""

    def test_delete_404_on_unknown(self, monkeypatch):
        client = _owner_client(monkeypatch)
        with patch("twaky.api.routers.skills.repository.delete", return_value=False):
            resp = client.delete(f"/skills/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "skill_not_found"
