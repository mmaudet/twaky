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


# Isolated owner_email — the live atlas daemon only processes missions
# for ``settings.twaky_owner_email``. Using an .invalid TLD (RFC 6761)
# guarantees the daemon never claims our test missions and races us to
# an unexpected state. See docs/superpowers/investigations/2026-08-10-
# nine-flakes.md flakes #6 #7.
_ISOLATED_OWNER = "recovery-test@test.invalid"


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
        owner_email=_ISOLATED_OWNER,
        declared_by=_ISOLATED_OWNER,
    )
    # Advance through DECLARED → PLANNING → RUNNING (bypassing the daemon).
    engine.start_planning(m.id)
    engine.commit_plan(m.id, [PlanStep(agent="atlas", tool="orchestrate", args={})])

    # The mission is now RUNNING.  Simulate that a LangGraph checkpoint exists
    # by patching _has_checkpoint to return True (we don't want a real
    # LangGraph saver dependency in this test).
    with patch("twaky.missions.recovery._has_checkpoint", return_value=True):
        results = resume_missions_after_restart(owner_email=_ISOLATED_OWNER)

    ids = {mid: action for mid, action in results}
    assert ids.get(m.id) == "resumed", (
        f"Expected 'resumed' for mission {m.id}, got: {ids.get(m.id)!r}"
    )

    _cleanup(m.id)


def test_recover_and_schedule_dispatches_resumed_missions(monkeypatch):
    """_recover_and_schedule schedules a 'resumed' mission via _bounded_run.

    Verifies fix C2: recovery outcomes with action=='resumed' must be
    dispatched to the task queue, not just logged.

    Additionally patches ``atlas_daemon.settings.twaky_owner_email`` to
    the isolated owner so ``resume_missions_after_restart`` (which reads
    ``settings.twaky_owner_email`` internally) finds our test mission
    rather than the live owner's real missions. This complements the
    ``_ISOLATED_OWNER`` on ``engine.declare`` — both sides need to agree.
    """
    from twaky.daemon import atlas_daemon

    monkeypatch.setattr(atlas_daemon.settings, "twaky_owner_email", _ISOLATED_OWNER)

    m = engine.declare(
        intent_text="recover and schedule test",
        owner_email=_ISOLATED_OWNER,
        declared_by=_ISOLATED_OWNER,
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
        """Verifies the fallthrough guard keeps AWAITING_USER missions intact.

        The fake graph returns without ``__ATLAS_FINISH__`` and without
        pending user input, so the fallthrough path fires. The guard
        re-reads the mission state (AWAITING_USER), determines it is not
        RUNNING, and returns without calling engine.finish — leaving the
        mission in AWAITING_USER.

        See flake #8 in
        docs/superpowers/investigations/2026-08-10-nine-flakes.md.
        """
        from unittest.mock import MagicMock, patch

        from twaky.daemon import atlas_daemon
        from twaky.missions import engine, repository
        from twaky.missions.models import MissionState, PlanStep

        m = engine.declare(intent_text="rec-au", owner_email="a@x", declared_by="a@x")
        engine.start_planning(m.id)
        engine.commit_plan(m.id, [PlanStep(agent="atlas", tool="noop", args={})])
        engine.request_user_input(m.id, reason="ok", artifact={"draft": "x"})
        # Mission is now AWAITING_USER. Simulate recovery driving it.

        # Fake graph: plain assistant message, NO __ATLAS_FINISH__ marker.
        # The fallthrough guard is what protects the mission from an
        # illegal AWAITING_USER → DONE transition attempt.
        fake_graph = MagicMock()
        fake_graph.invoke.return_value = {
            "messages": [MagicMock(content="acknowledged user response")],
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
            atlas_daemon._run_mission_sync(m.id)

        # The resume branch fired (invoke was called).
        assert fake_graph.invoke.called, (
            "build_atlas_agent's invoke was never called — the resume branch "
            "did not fire on an AWAITING_USER mission"
        )

        # The guard prevented the illegal transition: mission stays AWAITING_USER.
        got = repository.get(m.id)
        assert got.state == MissionState.AWAITING_USER, (
            f"Expected mission to remain AWAITING_USER after fallthrough guard, "
            f"got {got.state!r} (reason: {got.state_reason!r})"
        )

        _cleanup_mission(m.id)

    def test_running_mission_reaches_fallthrough_finish_normally(self):
        """Guard does not block the legitimate RUNNING → DONE fallthrough path.

        A RUNNING mission whose fake graph returns without ``__ATLAS_FINISH__``
        and without pending user input should be finished with
        state_reason == "ended_without_finish_marker".
        """
        from unittest.mock import MagicMock, patch

        from twaky.daemon import atlas_daemon
        from twaky.missions import engine, repository
        from twaky.missions.models import MissionState, PlanStep

        m = engine.declare(
            intent_text="rec-running-fallthrough",
            owner_email="a@x",
            declared_by="a@x",
        )
        engine.start_planning(m.id)
        engine.commit_plan(m.id, [PlanStep(agent="atlas", tool="noop", args={})])
        # Mission is now RUNNING.

        fake_graph = MagicMock()
        fake_graph.invoke.return_value = {
            "messages": [MagicMock(content="no finish marker")],
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
            atlas_daemon._run_mission_sync(m.id, is_fresh_claim=True)

        got = repository.get(m.id)
        assert got.state == MissionState.DONE, (
            f"Expected RUNNING fallthrough to reach DONE, got {got.state!r}"
        )
        assert got.state_reason == "ended_without_finish_marker", (
            f"Unexpected state_reason: {got.state_reason!r}"
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
