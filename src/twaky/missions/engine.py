"""Mission state-transition engine.

Every mutation of the `mission` table goes through this module. Callers
outside this file MUST NOT write to the row directly. The engine:

1. Opens a transaction and locks the row with SELECT ... FOR UPDATE.
2. Validates the transition via guards.check_transition.
3. Applies the update (state, state_reason, plan, artifacts, updated_at).
4. Commits.

Langfuse trace emission is added in a later task (test-driven; keep this
task focused on the state machine + persistence).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from twaky.db import get_pool
from twaky.missions import repository
from twaky.missions.guards import check_transition
from twaky.missions.models import Mission, MissionState, PlanStep


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
        created_at=now,
        updated_at=now,
    )
    repository.insert(m)
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
            import json as _json

            params.append(_json.dumps([s.model_dump() for s in plan]))
        if artifacts is not None:
            sets.append("artifacts = %s::jsonb")
            import json as _json

            params.append(_json.dumps(artifacts))
        params.append(mission_id)
        cur.execute(f"UPDATE mission SET {', '.join(sets)} WHERE id = %s", params)
        conn.commit()


def start_planning(mission_id: UUID) -> None:
    _transition(mission_id, MissionState.PLANNING)


def commit_plan(mission_id: UUID, plan: list[PlanStep]) -> None:
    _transition(mission_id, MissionState.RUNNING, plan=plan)


def request_user_input(mission_id: UUID, reason: str, artifact: dict[str, Any]) -> None:
    _transition(
        mission_id,
        MissionState.AWAITING_USER,
        reason=reason,
        append_artifact=artifact,
    )


def resume(mission_id: UUID, user_response: dict[str, Any]) -> None:
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


def finish(
    mission_id: UUID,
    outcome: Literal["done", "failed"],
    artifacts: list[dict[str, Any]],
    reason: str = "",
) -> None:
    target = MissionState.DONE if outcome == "done" else MissionState.FAILED
    # Append the final artifacts to the existing list (don't clobber).
    with get_pool().connection() as conn, conn.cursor() as cur:
        current = repository.select_for_update(cur, mission_id)
        check_transition(current.state, target)
        merged = list(current.artifacts) + list(artifacts)
        import json as _json

        cur.execute(
            "UPDATE mission SET state = %s, state_reason = %s, artifacts = %s::jsonb, "
            "updated_at = %s WHERE id = %s",
            (target.value, reason or None, _json.dumps(merged), datetime.now(UTC), mission_id),
        )
        conn.commit()


def cancel(mission_id: UUID, reason: str) -> None:
    _transition(mission_id, MissionState.CANCELLED, reason=reason)


__all__ = [
    "cancel",
    "commit_plan",
    "declare",
    "finish",
    "request_user_input",
    "resume",
    "start_planning",
]
