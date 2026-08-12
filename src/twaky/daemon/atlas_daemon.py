"""Atlas daemon main loop — claim, run, transition, checkpoint."""

from __future__ import annotations

import asyncio
import json
import signal
from uuid import UUID

import structlog
from langchain_core.messages import HumanMessage

from twaky.agents import config_listener as agents_config_listener
from twaky.agents import registry as agents_registry
from twaky.agents.atlas.agent import build_atlas_agent
from twaky.agents.atlas.pending import extract_pending_from_output
from twaky.agents.atlas.tools import FINISH_MARKER
from twaky.config import settings
from twaky.daemon.heartbeat import bump
from twaky.daemon.notify import listen
from twaky.db import get_pool
from twaky.missions import engine, repository
from twaky.missions.checkpointer import get_checkpointer, setup_checkpointer_tables
from twaky.missions.models import Mission, MissionState, PlanStep
from twaky.missions.recovery import resume_missions_after_restart
from twaky.skills import config_listener as skills_config_listener
from twaky.skills import registry as skills_registry

log = structlog.get_logger("twaky.atlas_daemon")

# ──────────────────────────────────────────────────────────────────────────────
# Claim helpers
# ──────────────────────────────────────────────────────────────────────────────


def _claim_declared(owner_email: str) -> UUID | None:
    """Atomically claim the oldest DECLARED mission and transition it to PLANNING.

    Returns the mission id if a row was claimed, else None.

    The claim happens in a single UPDATE...RETURNING statement to close a
    race: previously, this method did SELECT ... FOR UPDATE SKIP LOCKED
    then COMMIT, releasing the lock while the row was still 'declared'.
    Two concurrent NOTIFY (or NOTIFY + periodic sweep) both saw the row
    as claimable, both scheduled _run_mission_sync, both called
    engine.start_planning — the second crashing with
    InvalidTransition('planning → planning').

    With the atomic claim, only one worker's UPDATE can affect the row;
    the second gets 0 rows back and returns None.

    Note: this bypasses the engine._transition path (langfuse span, state
    check), which is acceptable here because:
      * transition legality is fixed (declared → planning, always).
      * the mission_changed NOTIFY still fires (DB trigger).
      * the DECLARED → PLANNING span is short-lived and low-signal.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mission "
            "SET state = 'planning', updated_at = now() "
            "WHERE id = ("
            "  SELECT id FROM mission "
            "  WHERE state = 'declared' AND owner_email = %s "
            "  ORDER BY declared_at LIMIT 1 "
            "  FOR UPDATE SKIP LOCKED"
            ") "
            "RETURNING id",
            (owner_email,),
        )
        row = cur.fetchone()
        conn.commit()
    return row[0] if row else None


def _claim_running(mid: UUID) -> Mission | None:
    """Return the mission if it exists and is in RUNNING state, else None.

    Used on the mission_resumed path: a peer daemon might already have
    picked up the mission, so we confirm state before scheduling.
    """
    m = repository.get(mid)
    if m is not None and m.state == MissionState.RUNNING:
        return m
    return None


# Keep the old name for callers that still reference it (tests etc.)
def _claim_next(owner_email: str) -> UUID | None:
    """Alias for _claim_declared — kept for backward compatibility."""
    return _claim_declared(owner_email)


# ──────────────────────────────────────────────────────────────────────────────
# Finish-marker inspection
# ──────────────────────────────────────────────────────────────────────────────


def _last_finish_marker(state: dict) -> tuple[str, str] | None:
    """Return (outcome, final_answer) if the last tool message carries FINISH_MARKER."""
    for m in reversed(state.get("messages", [])[-6:]):
        content = getattr(m, "content", "")
        if isinstance(content, str) and content.startswith(FINISH_MARKER):
            _, outcome, answer = content.split("|", 2)
            return outcome, answer
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Bounded async task
# ──────────────────────────────────────────────────────────────────────────────


async def _bounded_run(
    sem: asyncio.Semaphore, mid: UUID, *, is_fresh_claim: bool = False
) -> None:
    async with sem:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    _run_mission_sync, mid, is_fresh_claim=is_fresh_claim
                ),
                timeout=settings.atlas_mission_timeout_s,
            )
        except TimeoutError:
            log.warning(
                "mission timed out",
                mission_id=str(mid),
                timeout_s=settings.atlas_mission_timeout_s,
            )
            # NOTE: the underlying thread may still run to completion — Python
            # has no thread-kill primitive.  Accept as a known limitation.  # TBD
            engine.finish(
                mid,
                outcome="failed",
                artifacts=[],
                reason="mission_timeout",
            )
        except Exception as exc:
            log.exception("mission crashed", mission_id=str(mid))
            engine.finish(
                mid,
                outcome="failed",
                artifacts=[],
                reason=f"atlas_crashed: {type(exc).__name__}",
            )


# ──────────────────────────────────────────────────────────────────────────────
# Synchronous mission driver (called via asyncio.to_thread)
# ──────────────────────────────────────────────────────────────────────────────


def _run_mission_sync(mid: UUID, *, is_fresh_claim: bool = False) -> None:
    """Blocking mission driver — called via asyncio.to_thread.

    Handles both fresh (DECLARED, atomically claimed to PLANNING) missions
    and resumed (RUNNING / AWAITING_USER) missions after a daemon restart
    or user-response notification.

    ``is_fresh_claim`` distinguishes the two paths that both see PLANNING:
      * True: caller just came from _claim_declared → the atomic UPDATE
        transitioned DECLARED → PLANNING; commit the plan and invoke.
      * False (default): boot recovery of a PLANNING mission that crashed
        before graph.invoke ever ran → auto-fail with checkpoint_lost.
    """
    m = repository.get(mid)
    if m is None:
        log.warning("mission vanished before run", mission_id=str(mid))
        return

    graph = build_atlas_agent(checkpointer=get_checkpointer())
    config = {"configurable": {"thread_id": str(mid)}}

    if m.state == MissionState.PLANNING and not is_fresh_claim:
        # Boot recovery of a PLANNING mission: its LangGraph checkpoint
        # never got a chance to be written (planning hasn't reached
        # graph.invoke yet). Auto-fail with a descriptive reason rather
        # than start_planning() → InvalidTransition.
        engine.finish(
            mid,
            outcome="failed",
            artifacts=[],
            reason="checkpoint_lost_during_planning",
        )
        return

    is_resume = m.state in (MissionState.RUNNING, MissionState.AWAITING_USER)

    if not is_resume:
        # Fresh mission. Two entry paths converge here:
        #   * Production: _claim_declared already atomically transitioned
        #     DECLARED → PLANNING. Skip start_planning (idempotent no-op
        #     the state machine rejects) and go straight to commit_plan.
        #   * Test/direct call: caller passes a DECLARED mission without
        #     the atomic claim path. Do the transition manually.
        if m.state == MissionState.DECLARED:
            engine.start_planning(mid)
        plan = [PlanStep(agent="atlas", tool="orchestrate", args={})]
        engine.commit_plan(mid, plan)

        state = graph.invoke(
            {
                "mission_id": mid,
                "owner_email": m.owner_email,
                "intent_text": m.intent_text,
                "messages": [HumanMessage(content=m.intent_text)],
                "artifacts": [],
                "step_count": 0,
                "pending_user_input": None,
                "total_tokens": 0,
            },
            config=config,
        )
    else:
        # Resume path: state already RUNNING (from engine.resume or recovery).
        # Find the latest user_response artifact and inject it as a HumanMessage
        # so the graph continues from wherever the checkpointer left off.
        user_response_artifact = next(
            (
                a
                for a in reversed(m.artifacts)
                if isinstance(a, dict) and a.get("kind") == "user_response"
            ),
            None,
        )
        seed_messages: list = []
        if user_response_artifact is not None:
            payload = user_response_artifact.get("payload", {})
            seed_messages = [HumanMessage(content=json.dumps(payload))]

        state = graph.invoke(
            {"messages": seed_messages} if seed_messages else {},
            config=config,
        )

    # ── I4: check step-limit BEFORE the fallthrough ──────────────────────────
    if state.get("step_count", 0) > settings.atlas_max_steps:
        log.warning("mission hit step limit", mission_id=str(mid))
        engine.finish(
            mid,
            outcome="failed",
            artifacts=state.get("artifacts", []),
            reason="step_limit_exceeded",
        )
        return

    # ── I5: token budget logging ──────────────────────────────────────────────
    total_tokens = state.get("total_tokens", 0)
    if total_tokens > settings.atlas_max_tokens:
        log.warning(
            "mission exceeded token budget",
            mission_id=str(mid),
            total_tokens=total_tokens,
            budget=settings.atlas_max_tokens,
        )
        # TBD: enforce hard cap once token accounting is reliable across providers

    pending = extract_pending_from_output(state)
    if pending is not None:
        engine.request_user_input(
            mid,
            reason=pending.get("kind", "input"),
            artifact=pending.get("artifact", {}),
        )
        return

    marker = _last_finish_marker(state)
    if marker is not None:
        outcome, answer = marker
        target = "done" if outcome == "done" else "failed"
        engine.finish(
            mid,
            outcome=target,  # type: ignore[arg-type]
            artifacts=[{"final_answer": answer}],
        )
        return

    # LLM ended without calling finish_mission — treat as done with whatever
    # answer we have, but log a warning.
    # Guard: re-read the mission state — the graph invoke may have transitioned
    # it (e.g. AWAITING_USER via request_user_input). Calling finish() from a
    # non-RUNNING state raises InvalidTransition and strands the mission.
    current = repository.get(mid)
    if current is None or current.state != MissionState.RUNNING:
        log.warning(
            "fallthrough guard: mission not RUNNING, skipping finish",
            mission_id=str(mid),
            state=str(current.state) if current is not None else "missing",
        )
        return
    log.warning("mission ended without finish_mission", mission_id=str(mid))
    engine.finish(
        mid,
        outcome="done",
        artifacts=state.get("artifacts", []),
        reason="ended_without_finish_marker",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Scheduling helpers
# ──────────────────────────────────────────────────────────────────────────────


def _schedule_declared_loop(sem: asyncio.Semaphore, tasks: set[asyncio.Task]) -> None:
    """Drain all claimable declared missions up to semaphore capacity.

    Loops while the semaphore has capacity AND _claim_declared returns a
    mission id, so a burst of declares is parallelised up to the concurrency
    limit rather than trickling through one per NOTIFY.

    Capacity is gauged by counting non-done tasks against
    atlas_max_concurrent_missions — cleaner than probing the private
    Semaphore._value attribute.
    """
    while True:
        active = sum(1 for t in tasks if not t.done())
        if active >= settings.atlas_max_concurrent_missions:
            break

        mid = _claim_declared(settings.twaky_owner_email)
        if mid is None:
            break
        # Fresh claim: _claim_declared already transitioned DECLARED → PLANNING.
        t = asyncio.create_task(_bounded_run(sem, mid, is_fresh_claim=True))
        tasks.add(t)
        t.add_done_callback(tasks.discard)


def _schedule_resumed(
    sem: asyncio.Semaphore, tasks: set[asyncio.Task], mid: UUID
) -> None:
    """Schedule a resumed mission if it is still in RUNNING state."""
    m = _claim_running(mid)
    if m is None:
        log.debug("mission_resumed ignored — not in RUNNING state", mission_id=str(mid))
        return
    t = asyncio.create_task(_bounded_run(sem, mid))
    tasks.add(t)
    t.add_done_callback(tasks.discard)


def _recover_and_schedule(
    sem: asyncio.Semaphore, tasks: set[asyncio.Task]
) -> list[tuple[UUID, str]]:
    """Run recovery and immediately schedule any resumed missions.

    Factored out of run() so it can be tested directly.
    Returns the list of (mid, action) tuples from resume_missions_after_restart.
    """
    raw = resume_missions_after_restart(settings.twaky_owner_email)
    outcomes: list[tuple[UUID, str]] = [(mid, action) for mid, action in raw]
    for mid, action in outcomes:
        log.info("recovery", mission_id=str(mid), action=action)
        if action == "resumed":
            t = asyncio.create_task(_bounded_run(sem, mid))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
    return outcomes


# ──────────────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────────────

_PERIODIC_WAKE_S = 5.0  # spec §7.3: catch missions declared before daemon booted


async def _main_loop() -> None:
    stop = asyncio.Event()

    def _handle(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    sem = asyncio.Semaphore(settings.atlas_max_concurrent_missions)
    tasks: set[asyncio.Task] = set()

    # Recovery: schedule mid-flight missions that survived a daemon restart.
    _recover_and_schedule(sem, tasks)

    async def _listener():
        async for ch, payload in listen(
            ["mission_declared", "mission_resumed"], settings.pg_dsn
        ):
            if stop.is_set():
                return
            if ch == "mission_declared":
                _schedule_declared_loop(sem, tasks)
            elif ch == "mission_resumed":
                try:
                    mid = UUID(payload)
                except (ValueError, AttributeError):
                    log.warning("bad mission_resumed payload", payload=payload)
                    continue
                _schedule_resumed(sem, tasks, mid)

    async def _periodic_sweep():
        """Periodic wake every 5 s to catch missions declared before boot (spec §7.3)."""
        while not stop.is_set():
            await asyncio.sleep(_PERIODIC_WAKE_S)
            if not stop.is_set():
                _schedule_declared_loop(sem, tasks)

    # Initial sweep for pre-declared missions.
    _schedule_declared_loop(sem, tasks)

    listener_task = asyncio.create_task(_listener())
    sweep_task = asyncio.create_task(_periodic_sweep())

    # Heartbeat every 10s.
    async def _heart():
        while not stop.is_set():
            bump()
            await asyncio.sleep(10)

    heart_task = asyncio.create_task(_heart())

    agents_config_task = asyncio.create_task(agents_config_listener.run(stop))
    skills_config_task = asyncio.create_task(skills_config_listener.run(stop))

    # Wait for shutdown.
    await stop.wait()
    listener_task.cancel()
    sweep_task.cancel()
    heart_task.cancel()
    agents_config_task.cancel()
    skills_config_task.cancel()
    if tasks:
        log.info(f"draining {len(tasks)} in-flight missions")
        await asyncio.wait(tasks, timeout=25)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────


def run() -> None:
    """Entry point for `twaky atlas run`."""
    log.info("atlas daemon booting", owner=settings.twaky_owner_email)
    setup_checkpointer_tables()
    agents_registry.invalidate_all()  # clean cache slate after restart
    skills_registry.invalidate_all()  # clean skills cache slate after restart
    bump()
    asyncio.run(_main_loop())
    log.info("atlas daemon stopped")


__all__ = ["_recover_and_schedule", "run"]
