"""Repository integration tests (self-skips if twaky-pg unreachable)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import repository as repo
from twaky.missions.models import Mission, MissionState, PlanStep


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


def _make(owner: str = "alice@example.com", intent: str = "test mission") -> Mission:
    now = datetime.now(UTC)
    return Mission(
        id=uuid4(),
        owner_email=owner,
        declared_by=owner,
        declared_at=now,
        intent_text=intent,
        state=MissionState.DECLARED,
        artifacts=[],
        created_at=now,
        updated_at=now,
    )


def test_insert_and_get_roundtrip():
    m = _make()
    repo.insert(m)
    got = repo.get(m.id)
    assert got is not None
    assert got.id == m.id
    assert got.intent_text == m.intent_text
    assert got.state == MissionState.DECLARED
    _cleanup(m.id)


def test_update_state_bumps_updated_at():
    m = _make()
    repo.insert(m)
    got1 = repo.get(m.id)
    repo.update_state(m.id, MissionState.PLANNING, reason="atlas_took_over")
    got2 = repo.get(m.id)
    assert got2.state == MissionState.PLANNING
    assert got2.state_reason == "atlas_took_over"
    assert got2.updated_at > got1.updated_at
    _cleanup(m.id)


def test_update_state_with_plan():
    m = _make()
    repo.insert(m)
    plan = [PlanStep(agent="chronos", tool="list_events", args={"date": "2026-08-01"})]
    repo.update_state(m.id, MissionState.RUNNING, plan=plan)
    got = repo.get(m.id)
    assert got.plan == plan
    _cleanup(m.id)


def test_list_live_filters_by_state_and_owner():
    a = _make(owner="alice@x", intent="a")
    b = _make(owner="alice@x", intent="b")
    c = _make(owner="bob@x", intent="c")
    for m in (a, b, c):
        repo.insert(m)
    repo.update_state(b.id, MissionState.DONE, reason="ok")

    live_alice = repo.list_live("alice@x")
    ids = {m.id for m in live_alice}
    assert a.id in ids
    assert b.id not in ids  # terminal
    assert c.id not in ids  # different owner

    for m in (a, b, c):
        _cleanup(m.id)


def test_get_missing_returns_none():
    assert repo.get(uuid4()) is None


def _cleanup(mid):
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))
        conn.commit()
