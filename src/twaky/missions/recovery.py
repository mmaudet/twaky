"""Restart resilience for the Mission engine.

At Atlas boot, scans missions in a non-terminal, non-declared state and
reconciles them with the LangGraph checkpointer:

- If a checkpoint exists → the caller (Atlas) is expected to resume it.
- If no checkpoint exists → the mission is transitioned to `failed` with
  reason `checkpoint_lost_after_restart`. The user can re-declare.
"""

from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

import structlog
from langchain_core.runnables import RunnableConfig

from twaky.missions import engine, repository
from twaky.missions.checkpointer import get_checkpointer
from twaky.missions.models import MissionState

log = structlog.get_logger("twaky.missions.recovery")

Action = Literal["resumed", "failed_checkpoint_lost"]


def _has_checkpoint(mission_id: UUID) -> bool:
    saver = get_checkpointer()
    cfg = cast(
        RunnableConfig,
        {"configurable": {"thread_id": str(mission_id), "checkpoint_ns": ""}},
    )
    return saver.get_tuple(cfg) is not None


def resume_missions_after_restart(owner_email: str) -> list[tuple[UUID, Action]]:
    """Reconcile live missions with checkpointer. Returns per-mission action.

    Only missions atlas can advance on its own are considered:
      - PLANNING: crashed between start_planning and commit_plan → auto-failed
        by _run_mission_sync (checkpoint_lost_during_planning).
      - RUNNING: had a checkpoint mid-execution → resumed by atlas.

    DECLARED and AWAITING_USER are intentionally left alone:
      - DECLARED has no checkpoint yet; the declared-mission listener
        picks it up on the next NOTIFY (or periodic sweep).
      - AWAITING_USER waits for a user_response event — re-invoking the
        graph at boot would re-emit request_user_input on an already-
        AWAITING_USER row, which the state machine (correctly) rejects
        as an illegal no-op transition. Only engine.resume (fired when
        the user submits input) may move it to RUNNING and re-schedule.
    """
    live = repository.list_live(owner_email)
    resumable_by_atlas = {MissionState.PLANNING, MissionState.RUNNING}
    to_check = [m for m in live if m.state in resumable_by_atlas]

    out: list[tuple[UUID, Action]] = []
    for m in to_check:
        if _has_checkpoint(m.id):
            log.info("resume_ready", mission_id=str(m.id), state=m.state.value)
            out.append((m.id, "resumed"))
        else:
            log.warning("checkpoint_lost", mission_id=str(m.id))
            engine.finish(
                m.id,
                outcome="failed",
                artifacts=[],
                reason="checkpoint_lost_after_restart",
            )
            out.append((m.id, "failed_checkpoint_lost"))
    return out


__all__ = ["Action", "resume_missions_after_restart"]
