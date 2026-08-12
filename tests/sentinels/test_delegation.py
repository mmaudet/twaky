"""Integration tests for twaky.sentinels.delegation.Delegation.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_TEST_DSN env to override the default pg_dsn.
"""

from __future__ import annotations

import os
import threading
import time

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import engine, repository
from twaky.missions.models import MissionState
from twaky.sentinels.delegation import Delegation


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
# Helpers
# ---------------------------------------------------------------------------


def _cleanup(mission_id) -> None:
    """Best-effort: cancel + delete mission row created during tests."""
    try:
        m = repository.get(mission_id)
        if m is not None and not m.state.is_terminal:
            engine.cancel(mission_id, reason="test cleanup")
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM mission WHERE id = %s", (mission_id,))
            conn.commit()
    except Exception:  # noqa: BLE001, S110
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_delegate_returns_done_when_mission_completes():
    """Background thread resolves the mission; delegate() must return state='done'.

    Uses a unique intent_text per test invocation so the resolver thread
    can filter to this test's specific mission — previously the resolver
    called ``repository.list_all()`` and finished the FIRST
    non-terminal sentinel:mail mission it found, which in the full test
    suite (or with the live daemon running) sometimes turned out to be
    the sibling ``test_delegate_times_out`` mission, corrupting its
    state and causing that test to fail. See flake #1 in
    docs/superpowers/investigations/2026-08-10-nine-flakes.md.
    """
    from uuid import uuid4

    unique_intent = f"resolve-test-{uuid4()}"

    d = Delegation("mail", _dsn())

    resolved_event = threading.Event()

    def _resolver():
        # Wait briefly so delegate() has had time to declare the mission and start
        # listening on mission_changed before we attempt to finish it.
        time.sleep(0.5)
        # Poll for a mission declared by sentinel:mail with our unique intent.
        missions = repository.list_all(settings.twaky_owner_email, limit=20)
        target = next(
            (
                m
                for m in missions
                if m.declared_by == "sentinel:mail"
                and m.intent_text == unique_intent
                and not m.state.is_terminal
            ),
            None,
        )
        if target is None:
            resolved_event.set()
            return
        try:
            engine.finish(
                target.id,
                outcome="done",
                artifacts=[{"result": "ok"}],
                reason="test cleanup",
            )
        except Exception:  # noqa: BLE001, S110
            pass
        resolved_event.set()

    resolver_thread = threading.Thread(target=_resolver, daemon=True)
    resolver_thread.start()

    result = d.delegate(intent_text=unique_intent, timeout_s=5.0)

    resolver_thread.join(timeout=3.0)

    assert result.state == "done"
    assert any(a.get("result") == "ok" for a in result.payload)

    _cleanup(result.mission_id)


def test_delegate_times_out_without_cancelling():
    """No resolver → delegate() returns state='timeout'; mission NOT auto-cancelled."""
    d = Delegation("mail", _dsn())

    result = d.delegate(intent_text="Never resolved", timeout_s=0.5)

    assert result.state == "timeout"

    # Mission must still exist in DB — not auto-cancelled by delegate().
    mission = repository.get(result.mission_id)
    assert mission is not None, "delegate() must not delete the mission on timeout"
    # Accept DECLARED or PLANNING as "still live" — atlas daemon may have
    # already advanced the state, but delegate() must NOT auto-cancel on timeout.
    assert mission.state in {
        MissionState.DECLARED,
        MissionState.PLANNING,
        MissionState.RUNNING,
        MissionState.AWAITING_USER,
    }, f"Mission should still be live after timeout, got {mission.state}"

    # Cleanup.
    try:
        engine.cancel(result.mission_id, reason="test cleanup")
    except Exception:  # noqa: BLE001, S110
        pass
    _cleanup(result.mission_id)
