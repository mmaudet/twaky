"""API tests for /mail-sentinel/* endpoints.

Requires a live Postgres instance (TWAKY_PG_HOST=172.27.0.33).
Autouse fixture wipes all 3 mail-sentinel tables before and after each test.

Auth: _cookie() returns a signed session for "alice@x", matching the
TWAKY_OWNER_EMAIL set in the autouse _env fixture.
"""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from twaky.api.main import app
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


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("MODEL", "sentinel-default-model")


@pytest.fixture(autouse=True)
def _wipe_tables():
    """Wipe all 3 mail-sentinel tables before and after each test."""
    _truncate()
    yield
    _truncate()


def _truncate() -> None:
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mail_sentinel_learned_pattern;"
            "DELETE FROM mail_sentinel_memory;"
            "DELETE FROM mail_sentinel_rule;"
        )


def _cookie() -> dict[str, str]:
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _client() -> TestClient:
    """Return a TestClient with no session cookie."""
    return TestClient(app)


def _owner_client(monkeypatch) -> TestClient:
    """Return a TestClient with a valid owner session cookie."""
    from twaky import config as _cfg

    monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
    return TestClient(app, cookies=_cookie())


def _valid_rule_body(**over) -> dict:
    body: dict = {
        "name": f"test-rule-{uuid4().hex[:8]}",
        "conditions": [
            {"field": "from", "operator": "contains", "value": "example.com"}
        ],
        "combinator": "OR",
        "actions": ["archive"],
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# GET /mail-sentinel/rules — list
# ---------------------------------------------------------------------------


class TestListRules:
    def test_unauthenticated_returns_401(self):
        r = _client().get("/mail-sentinel/rules")
        assert r.status_code == 401

    def test_empty_list(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/mail-sentinel/rules")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_created_rule(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_rule_body(name="list-test-rule")
        client.post("/mail-sentinel/rules", json=body)
        r = client.get("/mail-sentinel/rules")
        assert r.status_code == 200
        names = [rule["name"] for rule in r.json()]
        assert "list-test-rule" in names

    def test_enabled_true_filter(self, monkeypatch):
        client = _owner_client(monkeypatch)
        client.post(
            "/mail-sentinel/rules",
            json=_valid_rule_body(name="enabled-rule", enabled=True),
        )
        client.post(
            "/mail-sentinel/rules",
            json=_valid_rule_body(name="disabled-rule", enabled=False),
        )
        r = client.get("/mail-sentinel/rules?enabled=true")
        assert r.status_code == 200
        names = [rule["name"] for rule in r.json()]
        assert "enabled-rule" in names
        assert "disabled-rule" not in names

    def test_enabled_false_filter(self, monkeypatch):
        client = _owner_client(monkeypatch)
        client.post(
            "/mail-sentinel/rules",
            json=_valid_rule_body(name="enabled-rule-2", enabled=True),
        )
        client.post(
            "/mail-sentinel/rules",
            json=_valid_rule_body(name="disabled-rule-2", enabled=False),
        )
        r = client.get("/mail-sentinel/rules?enabled=false")
        assert r.status_code == 200
        names = [rule["name"] for rule in r.json()]
        assert "disabled-rule-2" in names
        assert "enabled-rule-2" not in names

    def test_summary_shape(self, monkeypatch):
        client = _owner_client(monkeypatch)
        client.post("/mail-sentinel/rules", json=_valid_rule_body(name="shape-rule"))
        r = client.get("/mail-sentinel/rules")
        assert r.status_code == 200
        item = r.json()[0]
        assert "id" in item
        assert "name" in item
        assert "action_count" in item
        assert "condition_count" in item
        assert "conditions" not in item  # summary omits conditions list
        assert "actions" not in item  # summary omits actions list


# ---------------------------------------------------------------------------
# POST /mail-sentinel/rules — create
# ---------------------------------------------------------------------------


class TestCreateRule:
    def test_unauthenticated_returns_401(self):
        r = _client().post("/mail-sentinel/rules", json=_valid_rule_body())
        assert r.status_code == 401

    def test_creates_rule_returns_201(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_rule_body(name="create-test-rule")
        r = client.post("/mail-sentinel/rules", json=body)
        assert r.status_code == 201
        resp = r.json()
        assert resp["name"] == "create-test-rule"
        assert "id" in resp
        assert "created_at" in resp
        assert "updated_at" in resp
        assert resp["action_count"] == 1
        assert resp["condition_count"] == 1

    def test_422_empty_actions(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_rule_body(actions=[])
        r = client.post("/mail-sentinel/rules", json=body)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_422_bad_regex_in_condition(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_rule_body(
            conditions=[{"field": "subject", "operator": "regex", "value": "[unclosed"}]
        )
        r = client.post("/mail-sentinel/rules", json=body)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_422_invalid_combinator(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_rule_body(combinator="NAND")
        r = client.post("/mail-sentinel/rules", json=body)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_422_bad_rule_name(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_rule_body(name="InvalidName")  # must be lowercase
        r = client.post("/mail-sentinel/rules", json=body)
        assert r.status_code == 422

    def test_422_unknown_field_in_body(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_rule_body()
        body["unknown_field"] = "oops"
        r = client.post("/mail-sentinel/rules", json=body)
        assert r.status_code == 422

    def test_detail_has_conditions_and_actions(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_rule_body(name="detail-rule", actions=["archive", "mark-read"])
        # mark-read is not a valid action; use valid ones
        body["actions"] = ["archive", "mark_read"]
        r = client.post("/mail-sentinel/rules", json=body)
        assert r.status_code == 201
        resp = r.json()
        assert isinstance(resp["conditions"], list)
        assert isinstance(resp["actions"], list)
        assert resp["combinator"] in ("OR", "AND")


# ---------------------------------------------------------------------------
# GET /mail-sentinel/rules/{id} — detail
# ---------------------------------------------------------------------------


class TestGetRule:
    def test_unauthenticated_returns_401(self):
        r = _client().get(f"/mail-sentinel/rules/{uuid4()}")
        assert r.status_code == 401

    def test_get_existing_rule(self, monkeypatch):
        client = _owner_client(monkeypatch)
        body = _valid_rule_body(name="get-me-rule")
        created = client.post("/mail-sentinel/rules", json=body).json()
        rule_id = created["id"]
        r = client.get(f"/mail-sentinel/rules/{rule_id}")
        assert r.status_code == 200
        assert r.json()["name"] == "get-me-rule"

    def test_404_unknown_id(self, monkeypatch):
        client = _owner_client(monkeypatch)
        r = client.get(f"/mail-sentinel/rules/{uuid4()}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "mail_rule_not_found"

    def test_422_malformed_uuid(self, monkeypatch):
        client = _owner_client(monkeypatch)
        r = client.get("/mail-sentinel/rules/not-a-uuid")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /mail-sentinel/rules/{id} — partial update
# ---------------------------------------------------------------------------


class TestPatchRule:
    def test_unauthenticated_returns_401(self):
        r = _client().patch(f"/mail-sentinel/rules/{uuid4()}", json={"enabled": False})
        assert r.status_code == 401

    def test_patch_enabled_false(self, monkeypatch):
        client = _owner_client(monkeypatch)
        created = client.post(
            "/mail-sentinel/rules", json=_valid_rule_body(name="patch-me-rule")
        ).json()
        rule_id = created["id"]
        r = client.patch(f"/mail-sentinel/rules/{rule_id}", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_422_empty_body(self, monkeypatch):
        client = _owner_client(monkeypatch)
        created = client.post(
            "/mail-sentinel/rules", json=_valid_rule_body(name="patch-empty-rule")
        ).json()
        r = client.patch(f"/mail-sentinel/rules/{created['id']}", json={})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"
        assert "at least one field" in r.json()["error"]["message"]

    def test_422_unknown_field(self, monkeypatch):
        client = _owner_client(monkeypatch)
        created = client.post(
            "/mail-sentinel/rules", json=_valid_rule_body(name="patch-unknown-rule")
        ).json()
        r = client.patch(
            f"/mail-sentinel/rules/{created['id']}", json={"nonexistent_field": "x"}
        )
        assert r.status_code == 422

    def test_422_bad_combinator(self, monkeypatch):
        client = _owner_client(monkeypatch)
        created = client.post(
            "/mail-sentinel/rules", json=_valid_rule_body(name="patch-bad-comb")
        ).json()
        r = client.patch(
            f"/mail-sentinel/rules/{created['id']}", json={"combinator": "XOR"}
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_404_unknown_id(self, monkeypatch):
        client = _owner_client(monkeypatch)
        r = client.patch(f"/mail-sentinel/rules/{uuid4()}", json={"enabled": True})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "mail_rule_not_found"


# ---------------------------------------------------------------------------
# DELETE /mail-sentinel/rules/{id}
# ---------------------------------------------------------------------------


class TestDeleteRule:
    def test_unauthenticated_returns_401(self):
        r = _client().delete(f"/mail-sentinel/rules/{uuid4()}")
        assert r.status_code == 401

    def test_delete_returns_204(self, monkeypatch):
        client = _owner_client(monkeypatch)
        created = client.post(
            "/mail-sentinel/rules", json=_valid_rule_body(name="delete-me-rule")
        ).json()
        r = client.delete(f"/mail-sentinel/rules/{created['id']}")
        assert r.status_code == 204
        assert r.content == b""

    def test_delete_404_on_unknown(self, monkeypatch):
        client = _owner_client(monkeypatch)
        r = client.delete(f"/mail-sentinel/rules/{uuid4()}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "mail_rule_not_found"

    def test_delete_then_get_returns_404(self, monkeypatch):
        client = _owner_client(monkeypatch)
        created = client.post(
            "/mail-sentinel/rules", json=_valid_rule_body(name="delete-then-get")
        ).json()
        rule_id = created["id"]
        client.delete(f"/mail-sentinel/rules/{rule_id}")
        r = client.get(f"/mail-sentinel/rules/{rule_id}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /mail-sentinel/memories — list
# ---------------------------------------------------------------------------


class TestListMemories:
    def test_unauthenticated_returns_401(self):
        r = _client().get("/mail-sentinel/memories")
        assert r.status_code == 401

    def test_empty_list(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/mail-sentinel/memories")
        assert r.status_code == 200
        assert r.json() == []

    def test_limit_above_500_returns_422(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/mail-sentinel/memories?limit=501")
        assert r.status_code == 422

    def test_limit_zero_returns_422(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/mail-sentinel/memories?limit=0")
        assert r.status_code == 422

    def test_scope_filter_accepted(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/mail-sentinel/memories?scope=global")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# GET /mail-sentinel/learned-patterns — list
# ---------------------------------------------------------------------------


class TestListLearnedPatterns:
    def test_unauthenticated_returns_401(self):
        r = _client().get("/mail-sentinel/learned-patterns")
        assert r.status_code == 401

    def test_empty_list(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/mail-sentinel/learned-patterns")
        assert r.status_code == 200
        assert r.json() == []

    def test_active_only_filter_accepted(self, monkeypatch):
        r = _owner_client(monkeypatch).get(
            "/mail-sentinel/learned-patterns?active_only=true"
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# DELETE /mail-sentinel/learned-patterns/{sender_email}/{rule_name}
# ---------------------------------------------------------------------------


class TestForgetPattern:
    def test_unauthenticated_returns_401(self):
        r = _client().delete(
            "/mail-sentinel/learned-patterns/foo@example.com/test-rule"
        )
        assert r.status_code == 401

    def test_forget_nonexistent_is_silent_204(self, monkeypatch):
        """DELETE on a nonexistent pattern returns 204 (silent, no 404)."""
        client = _owner_client(monkeypatch)
        r = client.delete(
            "/mail-sentinel/learned-patterns/nobody@example.com/ghost-rule"
        )
        assert r.status_code == 204
        assert r.content == b""

    def test_forget_existing_pattern(self, monkeypatch):
        """Insert a pattern via store, then forget it via API."""
        from twaky.sentinels.mail.store import learned_patterns as lp_store

        lp_store.record_decision("sender@example.com", "my-rule", confidence_hint=0.95)
        client = _owner_client(monkeypatch)
        r = client.delete("/mail-sentinel/learned-patterns/sender@example.com/my-rule")
        assert r.status_code == 204
        # Confirm it was deleted
        remaining = lp_store.list_all()
        assert not any(
            p.sender_email == "sender@example.com" and p.rule_name == "my-rule"
            for p in remaining
        )
