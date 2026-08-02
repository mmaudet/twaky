"""psycopg3 CRUD for the `mission` table.

Raw SQL (no ORM) to stay consistent with src/twaky/db.py. Callers that
need atomicity across state transitions use select_for_update inside
their own connection/transaction.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from twaky.db import get_pool
from twaky.missions.models import Mission, MissionState, PlanStep


class MissionNotFound(Exception):
    pass


def _row_to_mission(row: dict[str, Any]) -> Mission:
    plan = row.get("plan")
    if plan is not None:
        plan = [PlanStep(**s) for s in plan]
    return Mission(
        id=row["id"],
        owner_email=row["owner_email"],
        declared_by=row["declared_by"],
        declared_at=row["declared_at"],
        intent_text=row["intent_text"],
        plan=plan,
        state=MissionState(row["state"]),
        state_reason=row["state_reason"],
        due_at=row["due_at"],
        artifacts=row["artifacts"] or [],
        langfuse_session_id=row["langfuse_session_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def insert(m: Mission) -> None:
    """Insert a fresh mission. Fails if id already exists."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mission (
                id, owner_email, declared_by, declared_at, intent_text,
                plan, state, state_reason, due_at, artifacts,
                langfuse_session_id, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s, %s, %s)
            """,
            (
                m.id,
                m.owner_email,
                m.declared_by,
                m.declared_at,
                m.intent_text,
                json.dumps([s.model_dump() for s in m.plan]) if m.plan else None,
                m.state.value,
                m.state_reason,
                m.due_at,
                json.dumps(m.artifacts),
                m.langfuse_session_id,
                m.created_at,
                m.updated_at,
            ),
        )
        conn.commit()


def get(mission_id: UUID) -> Mission | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM mission WHERE id = %s", (mission_id,))
        row = cur.fetchone()
    return _row_to_mission(row) if row else None


def update_state(
    mission_id: UUID,
    new_state: MissionState,
    reason: str | None = None,
    plan: list[PlanStep] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> None:
    """Update state + optional plan/artifacts, bump updated_at. One row, one txn."""
    sets = ["state = %s", "state_reason = %s", "updated_at = %s"]
    params: list[Any] = [new_state.value, reason, datetime.now(tz=UTC)]
    if plan is not None:
        sets.append("plan = %s::jsonb")
        params.append(json.dumps([s.model_dump() for s in plan]))
    if artifacts is not None:
        sets.append("artifacts = %s::jsonb")
        params.append(json.dumps(artifacts))
    params.append(mission_id)
    sql = f"UPDATE mission SET {', '.join(sets)} WHERE id = %s"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.rowcount == 0:
            raise MissionNotFound(f"mission {mission_id} not found")
        conn.commit()


def list_live(owner_email: str) -> list[Mission]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM mission WHERE owner_email = %s AND state IN "
            "('declared','planning','running','awaiting_user') "
            "ORDER BY declared_at DESC",
            (owner_email,),
        )
        rows = cur.fetchall()
    return [_row_to_mission(r) for r in rows]


def list_all(owner_email: str, limit: int = 500) -> list[Mission]:
    """Return ALL missions (live + terminal) sorted by declared_at DESC."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM mission WHERE owner_email = %s "
            "ORDER BY declared_at DESC LIMIT %s",
            (owner_email, limit),
        )
        rows = cur.fetchall()
    return [_row_to_mission(r) for r in rows]


def select_for_update(cur: Any, mission_id: UUID) -> Mission:
    """SELECT ... FOR UPDATE inside a caller's transaction. Locks the row."""
    cur.row_factory = dict_row
    cur.execute("SELECT * FROM mission WHERE id = %s FOR UPDATE", (mission_id,))
    row = cur.fetchone()
    if row is None:
        raise MissionNotFound(f"mission {mission_id} not found")
    return _row_to_mission(row)


__all__ = [
    "MissionNotFound",
    "get",
    "insert",
    "list_all",
    "list_live",
    "select_for_update",
    "update_state",
]
