"""Mission state-transition engine.

Every mutation of the `mission` table goes through this module. Callers
outside this file MUST NOT write to the row directly. The engine:

1. Opens a transaction and locks the row with SELECT ... FOR UPDATE.
2. Validates the transition via guards.check_transition.
3. Applies the update (state, state_reason, plan, artifacts, updated_at).
4. Commits.

Langfuse trace emission: every transition emits a ``mission.<transition>``
span attached to the mission's stable session_id (assigned at declare time).
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog

from twaky import observability
from twaky.db import get_pool
from twaky.missions import repository
from twaky.missions.guards import check_transition
from twaky.missions.models import Mission, MissionState, PlanStep

log = structlog.get_logger("twaky.missions.engine")


def _trace(name: str, mission_id: UUID, extra: dict[str, Any] | None = None) -> Any:
    """Emit a ``mission.<name>`` trace attached to the mission's session_id.

    No-op if Langfuse is not configured — observability.get_client() returns
    None and this helper silently returns a nullcontext.
    """
    lf = observability.get_client()
    if lf is None:
        return contextlib.nullcontext()
    m = repository.get(mission_id)
    session_id = (m.langfuse_session_id if m else None) or f"mission-{mission_id}"
    span = lf.start_as_current_span(name=f"mission.{name}")
    # Best-effort: set trace-level session_id (matches what agent.ask does).
    try:
        span.update_trace(session_id=session_id, user_id=(m.owner_email if m else ""))
    except Exception:  # noqa: BLE001, S110
        pass
    if extra:
        try:
            span.update(input=extra)
        except Exception:  # noqa: BLE001, S110
            pass
    return span


def _flush() -> None:
    """Best-effort flush so short-lived CLI processes don't lose traces."""
    lf = observability.get_client()
    if lf is None:
        return
    try:
        lf.flush()
    except Exception:  # noqa: BLE001, S110
        pass


def declare(
    intent_text: str,
    owner_email: str,
    declared_by: str,
    due_at: datetime | None = None,
) -> Mission:
    """Create a fresh Mission in state=declared and persist it."""
    now = datetime.now(UTC)
    m = Mission(
        id=uuid4(),
        owner_email=owner_email,
        declared_by=declared_by,
        declared_at=now,
        intent_text=intent_text,
        state=MissionState.DECLARED,
        due_at=due_at,
        artifacts=[],
        langfuse_session_id=f"mission-{uuid4()}",  # stable session id from birth
        created_at=now,
        updated_at=now,
    )
    repository.insert(m)
    # Unified channel for API SSE consumers.
    _notify_state_change(m.id, MissionState.DECLARED)
    # Wake the atlas daemon.
    _notify("mission_declared", str(m.id))
    with _trace("declare", m.id, extra={"intent_text": intent_text}):
        pass
    _flush()
    return m


def _transition(
    mission_id: UUID,
    to_state: MissionState,
    reason: str | None = None,
    plan: list[PlanStep] | None = None,
    append_artifact: dict[str, Any] | None = None,
    replace_artifacts: list[dict[str, Any]] | None = None,
) -> None:
    """Common transition path — lock, check, update, commit."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        current = repository.select_for_update(cur, mission_id)
        check_transition(current.state, to_state)
        artifacts = replace_artifacts
        if append_artifact is not None:
            artifacts = list(current.artifacts) + [append_artifact]
        # Direct SQL here (not repository.update_state) to stay in the same txn.
        sets = ["state = %s", "state_reason = %s", "updated_at = %s"]
        params: list[Any] = [to_state.value, reason, datetime.now(UTC)]
        if plan is not None:
            sets.append("plan = %s::jsonb")
            params.append(json.dumps([s.model_dump() for s in plan]))
        if artifacts is not None:
            sets.append("artifacts = %s::jsonb")
            params.append(json.dumps(artifacts))
        params.append(mission_id)
        cur.execute(f"UPDATE mission SET {', '.join(sets)} WHERE id = %s", params)
        conn.commit()
    _notify_state_change(mission_id, to_state)


def start_planning(mission_id: UUID) -> None:
    with _trace("start_planning", mission_id):
        _transition(mission_id, MissionState.PLANNING)
    _flush()


def commit_plan(mission_id: UUID, plan: list[PlanStep]) -> None:
    with _trace("commit_plan", mission_id):
        _transition(mission_id, MissionState.RUNNING, plan=plan)
    _flush()


def request_user_input(mission_id: UUID, reason: str, artifact: dict[str, Any]) -> None:
    with _trace("request_user_input", mission_id, extra={"reason": reason}):
        _transition(
            mission_id,
            MissionState.AWAITING_USER,
            reason=reason,
            append_artifact=artifact,
        )
    _flush()


def resume(mission_id: UUID, user_response: dict[str, Any]) -> None:
    with _trace("resume", mission_id):
        _transition(
            mission_id,
            MissionState.RUNNING,
            reason="user_response_received",
            append_artifact={
                "kind": "user_response",
                "at": datetime.now(UTC).isoformat(),
                "payload": user_response,
            },
        )
    _notify("mission_resumed", str(mission_id))
    _flush()


def finish(
    mission_id: UUID,
    outcome: Literal["done", "failed"],
    artifacts: list[dict[str, Any]],
    reason: str = "",
) -> None:
    target = MissionState.DONE if outcome == "done" else MissionState.FAILED
    with (
        _trace("finish", mission_id, extra={"outcome": outcome}),
        get_pool().connection() as conn,
        conn.cursor() as cur,
    ):
        # Append the final artifacts to the existing list (don't clobber).
        current = repository.select_for_update(cur, mission_id)
        check_transition(current.state, target)
        merged = list(current.artifacts) + list(artifacts)
        cur.execute(
            "UPDATE mission SET state = %s, state_reason = %s, artifacts = %s::jsonb, "
            "updated_at = %s WHERE id = %s",
            (
                target.value,
                reason or None,
                json.dumps(merged),
                datetime.now(UTC),
                mission_id,
            ),
        )
        conn.commit()
    _notify_state_change(mission_id, target)
    _flush()


def cancel(mission_id: UUID, reason: str) -> None:
    with _trace("cancel", mission_id, extra={"reason": reason}):
        _transition(mission_id, MissionState.CANCELLED, reason=reason)
    _flush()


def _notify_state_change(mission_id: UUID, new_state: MissionState) -> None:
    """Emit unified mission_changed NOTIFY for API SSE consumers."""
    payload = json.dumps(
        {
            "mission_id": str(mission_id),
            "state": new_state.value,
            "at": datetime.now(UTC).isoformat(),
        }
    )
    _notify("mission_changed", payload)


def _notify(channel: str, payload: str) -> None:
    """Fire-and-forget PG NOTIFY; never raise from the engine path.

    Uses ``pg_notify(channel, payload)`` (SQL function form) instead of
    the ``NOTIFY channel, payload`` command. The command form does NOT
    accept parametrized payloads via psycopg's %s substitution — psycopg
    generates ``NOTIFY channel, $1`` which Postgres rejects at parse time
    with 'syntax error at or near "$1"'. The exception used to be
    silently swallowed by the broad ``except Exception: pass`` below,
    meaning every NOTIFY from this engine was a no-op — mission
    scheduling relied entirely on the atlas daemon's 5-second periodic
    sweep, and mission_resumed (no sweep fallback) simply never fired.

    Kept ``except Exception: pass`` because NOTIFY failure must not
    propagate into the transition path, but log at exception level so
    future silent-failure regressions surface in observability.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_notify(%s, %s)", (channel, payload))
            conn.commit()
    except Exception:
        log.exception("NOTIFY delivery failed", channel=channel)


__all__ = [
    "cancel",
    "commit_plan",
    "declare",
    "finish",
    "request_user_input",
    "resume",
    "start_planning",
]
