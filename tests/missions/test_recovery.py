"""Restart-recovery reconciles live missions with LangGraph checkpoints."""

from __future__ import annotations

import os

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import engine, recovery, repository
from twaky.missions.models import MissionState, PlanStep


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


def _cleanup(mid):
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))
        conn.commit()


def test_planning_mission_with_no_checkpoint_is_failed():
    """A mission stuck in PLANNING (crashed between start_planning and commit_plan)
    must be transitioned to FAILED by recovery — the state machine must allow it."""
    m = engine.declare(
        intent_text="ghost planning", owner_email="a@x", declared_by="a@x"
    )
    engine.start_planning(m.id)  # DECLARED → PLANNING
    # DO NOT commit_plan — leave in PLANNING with no checkpoint

    results = recovery.resume_missions_after_restart(owner_email="a@x")
    ids = {mid: action for (mid, action) in results}
    assert ids.get(m.id) == "failed_checkpoint_lost"
    final = repository.get(m.id)
    assert final.state == MissionState.FAILED
    _cleanup(m.id)


def test_mission_without_checkpoint_is_failed_at_recovery():
    """Simulate a crash right after commit_plan, before LangGraph wrote a checkpoint."""
    m = engine.declare(intent_text="ghost", owner_email="a@x", declared_by="a@x")
    engine.start_planning(m.id)
    engine.commit_plan(m.id, [PlanStep(agent="chronos", tool="list_events", args={})])
    # State is RUNNING but no LangGraph checkpoint was written for this thread_id.

    results = recovery.resume_missions_after_restart(owner_email="a@x")
    ids = {mid: action for (mid, action) in results}
    assert ids.get(m.id) == "failed_checkpoint_lost"

    final = repository.get(m.id)
    assert final.state == MissionState.FAILED
    assert "checkpoint_lost" in (final.state_reason or "")
    _cleanup(m.id)
