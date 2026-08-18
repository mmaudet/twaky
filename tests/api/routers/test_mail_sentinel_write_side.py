"""REST endpoints for SP5b write-side."""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session
from twaky.config import settings
from twaky.sentinels.mail.store import memories as mem
from twaky.sentinels.mail.store import observations as obs
from twaky.sentinels.mail.store.observations import (
    ExtractionOutcome,
    ObservationType,
)

pytestmark = pytest.mark.integration


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


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("MODEL", "sentinel-default-model")
    from twaky import config as _cfg

    monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_observation")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_observation")


def _cookie() -> dict[str, str]:
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


@pytest.fixture
def client():
    return TestClient(app, cookies=_cookie())


def test_patch_memory_persist_true_sets_no_expiry(client):
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="p")
    resp = client.patch(f"/mail-sentinel/memories/{m.id}", json={"persist": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["expires_at"] is None


def test_patch_memory_persist_false_sets_ttl(client):
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="p")
    mem.set_persist(m.id, True)
    resp = client.patch(f"/mail-sentinel/memories/{m.id}", json={"persist": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["expires_at"] is not None


def test_patch_memory_404_when_missing(client):
    resp = client.patch(f"/mail-sentinel/memories/{uuid4()}", json={"persist": True})
    assert resp.status_code == 404


def test_get_observations_returns_recent(client):
    obs.insert_if_new(
        email_id="e1",
        mailbox_id="m1",
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
    )
    resp = client.get("/mail-sentinel/observations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["observation_type"] == "draft_sent"


def test_delete_memory_returns_204(client):
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="to-delete")
    resp = client.delete(f"/mail-sentinel/memories/{m.id}")
    assert resp.status_code == 204
    # verify actually gone
    remaining = mem.list_recent(limit=10)
    assert all(x.id != m.id for x in remaining)


def test_delete_memory_404_when_missing(client):
    from uuid import uuid4

    resp = client.delete(f"/mail-sentinel/memories/{uuid4()}")
    assert resp.status_code == 404


def test_list_memories_exposes_source_and_confidence(client):
    mem.insert(
        kind="fact",
        scope="global",
        scope_value="*",
        content="p",
        source="auto_diff",
        confidence=0.9,
    )
    resp = client.get("/mail-sentinel/memories")
    assert resp.status_code == 200
    entries = resp.json()
    assert any(e.get("source") == "auto_diff" for e in entries)
