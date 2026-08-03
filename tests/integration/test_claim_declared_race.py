"""Regression: _claim_declared must be atomic under concurrent NOTIFY.

Pre-existing bug observed in production: a mission declared while a
periodic sweep was in-flight got picked up TWICE, both workers called
engine.start_planning, the second crashed with
InvalidTransition('planning → planning') and finished the mission as
'atlas_crashed: InvalidTransition'.

Root cause: _claim_declared did SELECT FOR UPDATE SKIP LOCKED then
COMMIT, releasing the lock while the row was still 'declared' — the
next concurrent call saw it as claimable.

Fix: _claim_declared now does a single UPDATE ... FROM (SELECT ... FOR
UPDATE SKIP LOCKED) ... RETURNING, transitioning DECLARED → PLANNING
inside the lock's scope. A concurrent call finds the row locked, gets
zero rows back, returns None.
"""

from __future__ import annotations

import os
from uuid import UUID

import psycopg
import pytest

from twaky.config import settings
from twaky.daemon import atlas_daemon
from twaky.missions import engine, repository
from twaky.missions.models import MissionState


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


def _cleanup(mid: UUID) -> None:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))
        conn.commit()


def test_claim_atomically_transitions_declared_to_planning():
    """Successful claim advances the row's state in the same call — no
    residual DECLARED → PLANNING gap for a peer to race into."""
    m = engine.declare(
        intent_text="race probe", owner_email="race@x", declared_by="race@x"
    )
    assert repository.get(m.id).state == MissionState.DECLARED

    claimed = atlas_daemon._claim_declared("race@x")
    assert claimed == m.id
    assert repository.get(m.id).state == MissionState.PLANNING
    _cleanup(m.id)


def test_claim_returns_none_when_row_is_already_locked():
    """A second, concurrent _claim_declared must see the row as locked
    (SKIP LOCKED) and return None — not silently claim a phantom copy."""
    m = engine.declare(
        intent_text="hold lock", owner_email="hold@x", declared_by="hold@x"
    )

    # Simulate an in-flight peer holding FOR UPDATE on this row.
    peer = psycopg.connect(_dsn())
    peer.autocommit = False
    try:
        with peer.cursor() as cur:
            cur.execute("SELECT id FROM mission WHERE id = %s FOR UPDATE", (m.id,))

        # While the peer holds the lock, _claim_declared must skip it.
        result = atlas_daemon._claim_declared("hold@x")
        assert result is None, (
            "expected None (row locked by peer); got a claim, meaning "
            "two workers would drive the same mission and the second "
            "would crash on InvalidTransition"
        )
        # The row is still DECLARED (untouched).
        assert repository.get(m.id).state == MissionState.DECLARED
    finally:
        peer.rollback()
        peer.close()

    # Now that the peer has released the lock, the claim succeeds.
    result = atlas_daemon._claim_declared("hold@x")
    assert result == m.id
    assert repository.get(m.id).state == MissionState.PLANNING
    _cleanup(m.id)


def test_second_claim_after_success_returns_none():
    """After a successful claim (state=PLANNING), a repeat call must not
    re-claim the same row — the state filter (WHERE state='declared')
    ensures the atomic transition is idempotent-safe."""
    m = engine.declare(intent_text="one-shot", owner_email="one@x", declared_by="one@x")
    first = atlas_daemon._claim_declared("one@x")
    assert first == m.id

    second = atlas_daemon._claim_declared("one@x")
    assert second is None, (
        f"repeat claim returned {second} instead of None; the state "
        f"filter (state='declared') is broken"
    )
    _cleanup(m.id)
