"""Atlas daemon main loop — claim, run, transition, checkpoint."""

from __future__ import annotations

import asyncio
import signal
from uuid import UUID

import structlog
from langchain_core.messages import HumanMessage

from twaky.agents.atlas.agent import build_atlas_agent
from twaky.agents.atlas.pending import extract_pending_from_output
from twaky.agents.atlas.tools import FINISH_MARKER
from twaky.config import settings
from twaky.daemon.heartbeat import bump
from twaky.daemon.notify import listen
from twaky.db import get_pool
from twaky.missions import engine, repository
from twaky.missions.checkpointer import get_checkpointer, setup_checkpointer_tables
from twaky.missions.models import PlanStep
from twaky.missions.recovery import resume_missions_after_restart

log = structlog.get_logger("twaky.atlas_daemon")


def _claim_next(owner_email: str) -> UUID | None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM mission "
            "WHERE state = 'declared' AND owner_email = %s "
            "ORDER BY declared_at LIMIT 1 FOR UPDATE SKIP LOCKED",
            (owner_email,),
        )
        row = cur.fetchone()
        conn.commit()
    return row[0] if row else None


def _last_finish_marker(state: dict) -> tuple[str, str] | None:
    """Return (outcome, final_answer) if the last tool message carries FINISH_MARKER."""
    for m in reversed(state.get("messages", [])[-6:]):
        content = getattr(m, "content", "")
        if isinstance(content, str) and content.startswith(FINISH_MARKER):
            _, outcome, answer = content.split("|", 2)
            return outcome, answer
    return None


async def _bounded_run(sem: asyncio.Semaphore, mid: UUID) -> None:
    async with sem:
        try:
            await asyncio.to_thread(_run_mission_sync, mid)
        except Exception as exc:
            log.exception("mission crashed", mission_id=str(mid))
            engine.finish(
                mid,
                outcome="failed",
                artifacts=[],
                reason=f"atlas_crashed: {type(exc).__name__}",
            )


def _run_mission_sync(mid: UUID) -> None:
    """Blocking mission driver — called via asyncio.to_thread."""
    m = repository.get(mid)
    if m is None:
        log.warning("mission vanished before run", mission_id=str(mid))
        return

    engine.start_planning(mid)
    # Simple synthesized plan — one step per major delegation the LLM may choose.
    plan = [PlanStep(agent="atlas", tool="orchestrate", args={})]
    engine.commit_plan(mid, plan)

    graph = build_atlas_agent(checkpointer=get_checkpointer())
    config = {"configurable": {"thread_id": str(mid)}}
    state = graph.invoke(
        {
            "mission_id": mid,
            "owner_email": m.owner_email,
            "intent_text": m.intent_text,
            "messages": [HumanMessage(content=m.intent_text)],
            "artifacts": [],
            "step_count": 0,
            "pending_user_input": None,
        },
        config=config,
    )

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
    log.warning("mission ended without finish_mission", mission_id=str(mid))
    engine.finish(
        mid,
        outcome="done",
        artifacts=state.get("artifacts", []),
        reason="ended_without_finish_marker",
    )


async def _main_loop() -> None:
    stop = asyncio.Event()

    def _handle(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    sem = asyncio.Semaphore(settings.atlas_max_concurrent_missions)
    tasks: set[asyncio.Task] = set()

    async def _listener():
        async for _ch, _payload in listen(
            ["mission_declared", "mission_resumed"], settings.pg_dsn
        ):
            if stop.is_set():
                return
            _schedule_next(sem, tasks)

    def _schedule_next(sem: asyncio.Semaphore, tasks: set[asyncio.Task]) -> None:
        mid = _claim_next(settings.twaky_owner_email)
        if mid is None:
            return
        t = asyncio.create_task(_bounded_run(sem, mid))
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    # Initial sweep for pre-declared missions.
    _schedule_next(sem, tasks)

    listener_task = asyncio.create_task(_listener())

    # Heartbeat every 10s.
    async def _heart():
        while not stop.is_set():
            bump()
            await asyncio.sleep(10)

    heart_task = asyncio.create_task(_heart())

    # Wait for shutdown.
    await stop.wait()
    listener_task.cancel()
    heart_task.cancel()
    if tasks:
        log.info(f"draining {len(tasks)} in-flight missions")
        await asyncio.wait(tasks, timeout=25)


def run() -> None:
    """Entry point for `twaky atlas run`."""
    log.info("atlas daemon booting", owner=settings.twaky_owner_email)
    setup_checkpointer_tables()
    for mid, action in resume_missions_after_restart(settings.twaky_owner_email):
        log.info("recovery", mission_id=str(mid), action=action)
    bump()
    asyncio.run(_main_loop())
    log.info("atlas daemon stopped")


__all__ = ["run"]
