"""Integration tests for MissionEmitter."""

from __future__ import annotations

import os

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import repository as mission_repo
from twaky.missions.models import MissionState
from twaky.sentinels.emitter import MissionEmitter


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


def _cleanup(mid) -> None:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))
        conn.commit()


# ---------------------------------------------------------------------------
# Pure unit test — no DB, no marker
# ---------------------------------------------------------------------------


def test_declared_by_prefix():
    emitter = MissionEmitter("mail")
    assert emitter.declared_by == "sentinel:mail"


# ---------------------------------------------------------------------------
# Integration tests — require DB
# ---------------------------------------------------------------------------

_needs_db = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


@_needs_db
def test_emit_creates_mission_in_awaiting_user():
    emitter = MissionEmitter("mail")
    artifact = {
        "kind": "sentinel_evidence",
        "sentinel": "mail",
        "evidence": {"email_id": "eml-42", "sender": "alice@example.com"},
    }
    mid = emitter.emit(
        intent_text="Mail: Q3 report",
        reason="review draft",
        artifact=artifact,
    )
    mission = mission_repo.get(mid)
    assert mission is not None
    assert mission.declared_by == "sentinel:mail"
    assert mission.state == MissionState.AWAITING_USER
    assert mission.intent_text == "Mail: Q3 report"
    assert len(mission.artifacts) == 1
    assert mission.artifacts[0]["kind"] == "sentinel_evidence"
    _cleanup(mid)


@_needs_db
def test_emitter_reason_becomes_state_reason():
    emitter = MissionEmitter("mail")
    mid = emitter.emit(
        intent_text="Mail: approval needed",
        reason="draft awaiting approval",
        artifact={"kind": "sentinel_evidence", "sentinel": "mail", "evidence": {}},
    )
    mission = mission_repo.get(mid)
    assert mission is not None
    assert mission.state_reason == "draft awaiting approval"
    _cleanup(mid)
