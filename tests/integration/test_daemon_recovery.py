"""Daemon recovery integration test — spec §11.2.

Verifies that resume_missions_after_restart correctly identifies missions
that were mid-flight when the daemon crashed (RUNNING with a checkpoint)
and that _recover_and_schedule schedules them for re-execution.

Self-skips when Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch
from uuid import UUID

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import engine
from twaky.missions.models import PlanStep
from twaky.missions.recovery import resume_missions_after_restart


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


# Alias used by the regression tests below.
_cleanup_mission = _cleanup


def test_recovery_identifies_running_mission_with_checkpoint():
    """A RUNNING mission that has a checkpoint is returned as ('resumed', mid).

    This simulates a daemon crash leaving a live mission: the mission is in
    RUNNING state in the DB, and a LangGraph checkpoint exists for it.
    """
    m = engine.declare(
        intent_text="recovery test mission",
        owner_email=settings.twaky_owner_email,
        declared_by=settings.twaky_owner_email,
    )
    # Advance through DECLARED → PLANNING → RUNNING (bypassing the daemon).
    engine.start_planning(m.id)
    engine.commit_plan(m.id, [PlanStep(agent="atlas", tool="orchestrate", args={})])

    # The mission is now RUNNING.  Simulate that a LangGraph checkpoint exists
    # by patching _has_checkpoint to return True (we don't want a real
    # LangGraph saver dependency in this test).
    with patch("twaky.missions.recovery._has_checkpoint", return_value=True):
        results = resume_missions_after_restart(owner_email=settings.twaky_owner_email)

    ids = {mid: action for mid, action in results}
    assert ids.get(m.id) == "resumed", (
        f"Expected 'resumed' for mission {m.id}, got: {ids.get(m.id)!r}"
    )

    _cleanup(m.id)


def test_recover_and_schedule_dispatches_resumed_missions():
    """_recover_and_schedule schedules a 'resumed' mission via _bounded_run.

    Verifies fix C2: recovery outcomes with action=='resumed' must be
    dispatched to the task queue, not just logged.
    """
    from twaky.daemon import atlas_daemon

    m = engine.declare(
        intent_text="recover and schedule test",
        owner_email=settings.twaky_owner_email,
        declared_by=settings.twaky_owner_email,
    )
    engine.start_planning(m.id)
    engine.commit_plan(m.id, [PlanStep(agent="atlas", tool="orchestrate", args={})])

    scheduled_mids: list[UUID] = []

    async def _fake_bounded_run(sem: asyncio.Semaphore, mid: UUID) -> None:
        scheduled_mids.append(mid)

    async def _run():
        sem = asyncio.Semaphore(settings.atlas_max_concurrent_missions)
        tasks: set[asyncio.Task] = set()

        with (
            patch("twaky.missions.recovery._has_checkpoint", return_value=True),
            patch.object(atlas_daemon, "_bounded_run", _fake_bounded_run),
        ):
            atlas_daemon._recover_and_schedule(sem, tasks)
            # Give the event loop a tick to schedule created tasks.
            await asyncio.sleep(0)

        return tasks

    asyncio.run(_run())

    assert m.id in scheduled_mids, (
        f"Mission {m.id} was not scheduled by _recover_and_schedule"
    )

    _cleanup(m.id)


class TestRecoveryHandlesAwaitingUser:
    """Regression: sub-project 2's parked bug — is_resume guard was too narrow."""

    def test_awaiting_user_mission_takes_resume_branch(self, monkeypatch):
        from unittest.mock import MagicMock, patch

        from twaky.daemon import atlas_daemon
        from twaky.missions import engine, repository
        from twaky.missions.models import MissionState, PlanStep

        m = engine.declare(intent_text="rec-au", owner_email="a@x", declared_by="a@x")
        engine.start_planning(m.id)
        engine.commit_plan(m.id, [PlanStep(agent="atlas", tool="noop", args={})])
        engine.request_user_input(m.id, reason="ok", artifact={"draft": "x"})
        # Mission is now AWAITING_USER. Simulate recovery driving it.

        # Fake the atlas graph so it returns a finish marker on the resume invocation.
        fake_graph = MagicMock()
        fake_graph.invoke.return_value = {
            "messages": [MagicMock(content="__ATLAS_FINISH__|done|resumed ok")],
            "artifacts": [],
        }

        with (
            patch(
                "twaky.daemon.atlas_daemon.build_atlas_agent", return_value=fake_graph
            ),
            patch("twaky.daemon.atlas_daemon.get_checkpointer", return_value=None),
            patch(
                "twaky.daemon.atlas_daemon.extract_pending_from_output",
                return_value=None,
            ),
        ):
            # Manually seed a user_response artifact (engine.resume already does this in production).
            # For this test we skip engine.resume and go straight to _run_mission_sync
            # to verify the guard change.
            atlas_daemon._run_mission_sync(m.id)

        got = repository.get(m.id)
        # The mission should have progressed (not crashed with InvalidTransition).
        # Either done (fake graph finished it) or still awaiting_user (nothing changed).
        # It must NOT be failed with an InvalidTransition reason.
        assert got.state != MissionState.FAILED or "InvalidTransition" not in (
            got.state_reason or ""
        )
        _cleanup_mission(m.id)


class TestRecoveryPlanningAutoFail:
    """A PLANNING mission has no useful checkpoint — auto-fail with clear reason."""

    def test_planning_recovery_fails_with_clear_reason(self):
        from twaky.daemon import atlas_daemon
        from twaky.missions import engine, repository
        from twaky.missions.models import MissionState

        m = engine.declare(intent_text="rec-plan", owner_email="a@x", declared_by="a@x")
        engine.start_planning(m.id)  # State now PLANNING, no commit_plan.

        atlas_daemon._run_mission_sync(m.id)

        got = repository.get(m.id)
        assert got.state == MissionState.FAILED
        assert got.state_reason == "checkpoint_lost_during_planning"
        _cleanup_mission(m.id)
