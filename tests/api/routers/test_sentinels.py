"""API tests for /sentinels/* endpoints.

These tests require a live Postgres instance (TWAKY_PG_HOST=172.27.0.33)
because the sentinel repository uses the real DB pool.  Unlike the skills
tests (which monkeypatch the repository), sentinel tests exercise the real
SQL layer against the seed ``mail`` row inserted by 008_init_sentinels.sh.

Auth is cookie-only: _cookie() returns a signed session for "alice@x",
which matches TWAKY_OWNER_EMAIL set in the autouse _env fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session
from twaky.db import get_pool
from twaky.sentinels import repository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("MODEL", "sentinel-default-model")


def _cookie() -> dict[str, str]:
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _client() -> TestClient:
    """Return a TestClient with no session cookie."""
    return TestClient(app)


def _owner_client(monkeypatch) -> TestClient:
    """Return a TestClient with a valid owner session cookie.

    monkeypatch is required to wire the env-overridden settings into the
    ``require_owner`` dependency (which reads the module-level ``settings``
    object that is loaded at import time).
    """
    from twaky import config as _cfg

    monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
    return TestClient(app, cookies=_cookie())


# ---------------------------------------------------------------------------
# GET /sentinels — list
# ---------------------------------------------------------------------------


class TestListSentinels:
    def test_unauthenticated_returns_401(self):
        r = _client().get("/sentinels")
        assert r.status_code == 401

    def test_list_returns_seed_row(self, monkeypatch):
        """The mail sentinel seeded by 008_init_sentinels.sh must appear."""
        r = _owner_client(monkeypatch).get("/sentinels")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        names = [s["name"] for s in body]
        assert "mail" in names

    def test_list_shape_has_required_fields(self, monkeypatch):
        """Every summary must expose the 5 documented fields."""
        r = _owner_client(monkeypatch).get("/sentinels")
        assert r.status_code == 200
        for item in r.json():
            assert "name" in item
            assert "display_name" in item
            assert "enabled" in item
            assert "version" in item
            assert "stats_24h" in item
            assert "total" in item["stats_24h"]
            assert "errors" in item["stats_24h"]

    def test_list_stats_24h_are_non_negative_ints(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/sentinels")
        assert r.status_code == 200
        for item in r.json():
            assert isinstance(item["stats_24h"]["total"], int)
            assert item["stats_24h"]["total"] >= 0
            assert isinstance(item["stats_24h"]["errors"], int)
            assert item["stats_24h"]["errors"] >= 0


# ---------------------------------------------------------------------------
# GET /sentinels/{name} — detail
# ---------------------------------------------------------------------------


class TestGetSentinel:
    def test_unauthenticated_returns_401(self):
        r = _client().get("/sentinels/mail")
        assert r.status_code == 401

    def test_get_detail_mail_returns_full_shape(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/sentinels/mail")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "mail"
        assert body["display_name"] == "Mail sentinel"
        assert "description" in body
        assert "config_schema" in body
        assert "config_values" in body
        assert "created_at" in body
        assert "updated_at" in body

    def test_get_detail_404_on_unknown(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/sentinels/does-not-exist-xyz")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "sentinel_not_found"

    def test_get_detail_config_values_dict(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/sentinels/mail")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["config_values"], dict)
        assert isinstance(body["config_schema"], dict)


# ---------------------------------------------------------------------------
# PATCH /sentinels/{name}
# ---------------------------------------------------------------------------


class TestPatchSentinel:
    def test_unauthenticated_returns_401(self):
        r = _client().patch("/sentinels/mail", json={"enabled": False})
        assert r.status_code == 401

    def test_patch_empty_body_returns_422(self, monkeypatch):
        r = _owner_client(monkeypatch).patch("/sentinels/mail", json={})
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "validation_failed"
        assert "at least one field" in body["error"]["message"]

    def test_patch_extra_field_returns_422(self, monkeypatch):
        """SentinelPatch has extra='forbid'."""
        r = _owner_client(monkeypatch).patch(
            "/sentinels/mail", json={"unknown_key": True}
        )
        assert r.status_code == 422

    def test_patch_enabled_toggle(self, monkeypatch):
        """Toggle enabled off then restore to original state."""
        client = _owner_client(monkeypatch)
        # Read current state
        original = client.get("/sentinels/mail").json()
        original_enabled = original["enabled"]

        try:
            # Toggle to the opposite value
            r = client.patch("/sentinels/mail", json={"enabled": not original_enabled})
            assert r.status_code == 200
            assert r.json()["enabled"] == (not original_enabled)
        finally:
            # Always restore
            client.patch("/sentinels/mail", json={"enabled": original_enabled})

    def test_patch_404_on_unknown(self, monkeypatch):
        r = _owner_client(monkeypatch).patch(
            "/sentinels/no-such-sentinel", json={"enabled": True}
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "sentinel_not_found"

    def test_patch_config_values_valid(self, monkeypatch):
        """Patch config_values with valid values according to the schema."""
        client = _owner_client(monkeypatch)
        # Save original values before patching
        original_cfg = repository.get("mail")
        assert original_cfg is not None
        original_values = original_cfg.config_values

        try:
            valid_patch = {
                "memory_candidate_pool": 50,
                "memory_inject_max": 8,
                "pattern_min_samples": 4,
                "pattern_confidence_threshold": 0.8,
                "event_source": "jmap_poll",
            }
            r = client.patch("/sentinels/mail", json={"config_values": valid_patch})
            assert r.status_code == 200
            body = r.json()
            assert body["config_values"]["memory_candidate_pool"] == 50
        finally:
            repository.update("mail", {"config_values": original_values})

    def test_patch_config_values_invalid_schema_returns_422(self, monkeypatch):
        """config_values that violate the JSON schema must return 422 with validation_failed."""
        # memory_candidate_pool must be 10..500; send something out of range
        invalid_patch = {
            "memory_candidate_pool": 9999,  # exceeds max of 500
        }
        r = _owner_client(monkeypatch).patch(
            "/sentinels/mail", json={"config_values": invalid_patch}
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "validation_failed"
        assert "errors" in body["error"]["detail"]
        assert len(body["error"]["detail"]["errors"]) > 0

    def test_patch_config_values_wrong_type_returns_422(self, monkeypatch):
        """config_values with wrong type should fail JSON schema validation."""
        invalid_patch = {
            "memory_candidate_pool": "not-a-number",  # should be integer
        }
        r = _owner_client(monkeypatch).patch(
            "/sentinels/mail", json={"config_values": invalid_patch}
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------
# GET /sentinels/{name}/runs
# ---------------------------------------------------------------------------


class TestListRuns:
    def test_unauthenticated_returns_401(self):
        r = _client().get("/sentinels/mail/runs")
        assert r.status_code == 401

    def test_list_runs_returns_paginated_list(self, monkeypatch):
        """Insert two runs then verify both appear in list and have the right shape."""
        run_a = repository.insert_run(
            {
                "sentinel_name": "mail",
                "event_ref": "test:list-runs:a",
                "outcome": "processed",
                "llm_calls": 2,
                "started_at": datetime.now(UTC),
            }
        )
        run_b = repository.insert_run(
            {
                "sentinel_name": "mail",
                "event_ref": "test:list-runs:b",
                "outcome": "error",
                "llm_calls": 0,
                "error_repr": "something went wrong",
                "started_at": datetime.now(UTC),
            }
        )
        try:
            r = _owner_client(monkeypatch).get("/sentinels/mail/runs?limit=10")
            assert r.status_code == 200
            body = r.json()
            assert isinstance(body, list)

            run_ids = [item["id"] for item in body]
            assert str(run_a.id) in run_ids
            assert str(run_b.id) in run_ids

            # Verify shape of a returned row
            sample = next(item for item in body if item["id"] == str(run_a.id))
            assert sample["sentinel_name"] == "mail"
            assert sample["event_ref"] == "test:list-runs:a"
            assert sample["outcome"] == "processed"
            assert sample["llm_calls"] == 2
            # SentinelRunSummary must NOT expose trace / error_repr
            assert "trace" not in sample
            assert "error_repr" not in sample
        finally:
            with get_pool().connection() as conn:
                conn.execute(
                    "DELETE FROM sentinel_run WHERE id = ANY(%s)",
                    ([run_a.id, run_b.id],),
                )

    def test_list_runs_404_on_unknown_sentinel(self, monkeypatch):
        r = _owner_client(monkeypatch).get("/sentinels/no-such-sentinel/runs")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "sentinel_not_found"

    def test_list_runs_limit_bounded(self, monkeypatch):
        """limit > 500 is rejected by FastAPI query-param validation."""
        r = _owner_client(monkeypatch).get("/sentinels/mail/runs?limit=501")
        assert r.status_code == 422

    def test_list_runs_limit_min_bounded(self, monkeypatch):
        """limit < 1 is rejected."""
        r = _owner_client(monkeypatch).get("/sentinels/mail/runs?limit=0")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /sentinels/runs/{run_id}
# ---------------------------------------------------------------------------


class TestGetRunDetail:
    def test_unauthenticated_returns_401(self):
        r = _client().get(f"/sentinels/runs/{uuid4()}")
        assert r.status_code == 401

    def test_get_run_detail_404_on_unknown(self, monkeypatch):
        r = _owner_client(monkeypatch).get(f"/sentinels/runs/{uuid4()}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "sentinel_run_not_found"

    def test_get_run_detail_returns_full_shape(self, monkeypatch):
        """Insert a run with trace and error_repr; verify the detail endpoint returns both."""
        run = repository.insert_run(
            {
                "sentinel_name": "mail",
                "event_ref": "test:run-detail:x",
                "outcome": "error",
                "llm_calls": 1,
                "error_repr": "SomeError: details here",
                "trace": [{"node": "load_thread", "ts": "2026-08-10T10:00:00Z"}],
                "started_at": datetime.now(UTC),
            }
        )
        try:
            r = _owner_client(monkeypatch).get(f"/sentinels/runs/{run.id}")
            assert r.status_code == 200
            body = r.json()
            assert body["id"] == str(run.id)
            assert body["sentinel_name"] == "mail"
            assert body["event_ref"] == "test:run-detail:x"
            assert body["outcome"] == "error"
            assert body["error_repr"] == "SomeError: details here"
            assert isinstance(body["trace"], list)
            assert len(body["trace"]) == 1
            assert body["trace"][0]["node"] == "load_thread"
        finally:
            with get_pool().connection() as conn:
                conn.execute("DELETE FROM sentinel_run WHERE id = %s", (run.id,))

    def test_get_run_detail_422_for_malformed_uuid(self, monkeypatch):
        """FastAPI path-param validation rejects non-UUID strings with 422."""
        r = _owner_client(monkeypatch).get("/sentinels/runs/not-a-uuid")
        assert r.status_code == 422
