"""Engine transition integration tests — one legal + one illegal path each."""

from __future__ import annotations

import os

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import engine, repository
from twaky.missions.guards import InvalidTransition
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


def test_declare_creates_row():
    m = engine.declare(intent_text="do X", owner_email="a@x", declared_by="a@x")
    got = repository.get(m.id)
    assert got is not None
    assert got.state == MissionState.DECLARED
    _cleanup(m.id)


def test_full_happy_path():
    m = engine.declare(intent_text="do X", owner_email="a@x", declared_by="a@x")
    engine.start_planning(m.id)
    assert repository.get(m.id).state == MissionState.PLANNING

    plan = [PlanStep(agent="chronos", tool="list_events", args={})]
    engine.commit_plan(m.id, plan)
    got = repository.get(m.id)
    assert got.state == MissionState.RUNNING
    assert got.plan == plan

    engine.request_user_input(m.id, reason="approve draft", artifact={"draft": "hi"})
    assert repository.get(m.id).state == MissionState.AWAITING_USER

    engine.resume(m.id, user_response={"ok": True})
    assert repository.get(m.id).state == MissionState.RUNNING

    engine.finish(m.id, outcome="done", artifacts=[{"final": "ok"}])
    final = repository.get(m.id)
    assert final.state == MissionState.DONE
    # The brief's equality check on artifacts was a bug: resume() appends a
    # {"kind": "user_response", ...} artifact between the draft and the final,
    # so the list has 3 entries, not 2. Instead we check structural invariants:
    # first artifact is the draft, last is the final, and a user_response is in between.
    assert len(final.artifacts) == 3
    assert final.artifacts[0] == {"draft": "hi"}
    assert final.artifacts[-1] == {"final": "ok"}
    assert final.artifacts[1]["kind"] == "user_response"
    assert final.artifacts[1]["payload"] == {"ok": True}
    _cleanup(m.id)


def test_illegal_transition_rejected():
    m = engine.declare(intent_text="X", owner_email="a@x", declared_by="a@x")
    with pytest.raises(InvalidTransition):
        engine.commit_plan(m.id, [])  # DECLARED → RUNNING skipping PLANNING
    assert repository.get(m.id).state == MissionState.DECLARED  # unchanged
    _cleanup(m.id)


def test_cancel_from_any_non_terminal():
    m = engine.declare(intent_text="X", owner_email="a@x", declared_by="a@x")
    engine.start_planning(m.id)
    engine.cancel(m.id, reason="user_aborted")
    got = repository.get(m.id)
    assert got.state == MissionState.CANCELLED
    assert got.state_reason == "user_aborted"
    _cleanup(m.id)
