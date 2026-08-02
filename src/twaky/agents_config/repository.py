"""psycopg3 CRUD for the `agent` table.

Raw SQL, matching src/twaky/missions/repository.py convention.
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from twaky.agents_config.models import AgentConfig
from twaky.db import get_pool


class AgentConfigNotFound(Exception):
    pass


def _row_to_config(row: dict[str, Any]) -> AgentConfig:
    return AgentConfig(
        id=row["id"],
        display_name=row["display_name"],
        role=row["role"],
        system_prompt=row["system_prompt"],
        model=row["model"],
        temperature=row["temperature"],
        updated_at=row["updated_at"],
    )


def list_all() -> list[AgentConfig]:
    """Return all agent rows, ordered by id (stable presentation)."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM agent ORDER BY id")
        rows = cur.fetchall()
    return [_row_to_config(r) for r in rows]


def get(agent_id: str) -> AgentConfig | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM agent WHERE id = %s", (agent_id,))
        row = cur.fetchone()
    return _row_to_config(row) if row else None


def update(agent_id: str, patch: dict[str, Any]) -> AgentConfig:
    """Apply partial update. Keys accepted: system_prompt, model, temperature.

    Raises AgentConfigNotFound if the row doesn't exist.
    Returns the fresh row after the DB trigger bumps updated_at.
    """
    if not patch:
        raise ValueError("empty patch")

    allowed = {"system_prompt", "model", "temperature"}
    bad = set(patch) - allowed
    if bad:
        raise ValueError(f"unknown fields: {sorted(bad)}")

    sets = [f"{k} = %s" for k in patch]
    params = [*patch.values(), agent_id]
    sql = f"UPDATE agent SET {', '.join(sets)} WHERE id = %s"

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.rowcount == 0:
            raise AgentConfigNotFound(f"agent {agent_id!r} not found")
        conn.commit()

    fresh = get(agent_id)
    assert fresh is not None  # just wrote it
    return fresh


__all__ = ["AgentConfigNotFound", "get", "list_all", "update"]
