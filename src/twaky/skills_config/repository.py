"""psycopg3 CRUD for the `skill` table.

Raw SQL, matching src/twaky/agents_config/repository.py convention.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from twaky.db import get_pool
from twaky.skills_config.models import Skill


class SkillNotFound(Exception):
    pass


class SkillNameConflict(Exception):
    pass


def _row_to_skill(row: dict[str, Any]) -> Skill:
    return Skill(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        python_source=row["python_source"],
        config_schema=row["config_schema"] or {},
        config_values=row["config_values"] or {},
        bound_agents=list(row["bound_agents"] or []),
        enabled=row["enabled"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_all() -> list[Skill]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM skill ORDER BY name")
        rows = cur.fetchall()
    return [_row_to_skill(r) for r in rows]


def get(skill_id: UUID) -> Skill | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM skill WHERE id = %s", (skill_id,))
        row = cur.fetchone()
    return _row_to_skill(row) if row else None


def list_bound_and_enabled(agent_id: str) -> list[Skill]:
    """Enabled skills whose bound_agents JSONB array contains agent_id."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM skill "
            "WHERE enabled AND bound_agents @> %s::jsonb "
            "ORDER BY name",
            (json.dumps([agent_id]),),
        )
        rows = cur.fetchall()
    return [_row_to_skill(r) for r in rows]


def create(
    *,
    name: str,
    description: str,
    python_source: str,
    config_schema: dict,
    config_values: dict,
    bound_agents: list[str],
    enabled: bool = True,
) -> Skill:
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "INSERT INTO skill "
                "(name, description, python_source, config_schema, "
                " config_values, bound_agents, enabled) "
                "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s) "
                "RETURNING *",
                (
                    name,
                    description,
                    python_source,
                    json.dumps(config_schema),
                    json.dumps(config_values),
                    json.dumps(bound_agents),
                    enabled,
                ),
            )
            row = cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise SkillNameConflict(name) from exc
    assert row is not None  # RETURNING * always yields a row on INSERT
    return _row_to_skill(row)


def update(skill_id: UUID, patch: dict[str, Any]) -> Skill:
    if not patch:
        raise ValueError("empty patch")

    allowed = {
        "name",
        "description",
        "python_source",
        "config_schema",
        "config_values",
        "bound_agents",
        "enabled",
    }
    bad = set(patch) - allowed
    if bad:
        raise ValueError(f"unknown fields: {sorted(bad)}")

    # `allowed` above is the sole guard against SQL identifier injection: only
    # whitelisted column names can reach the f-string below; values use %s placeholders.
    set_clauses: list[str] = []
    params: list[Any] = []
    for key, value in patch.items():
        if key in {"config_schema", "config_values", "bound_agents"}:
            set_clauses.append(f"{key} = %s::jsonb")
            params.append(json.dumps(value))
        else:
            set_clauses.append(f"{key} = %s")
            params.append(value)
    params.append(skill_id)

    sql = f"UPDATE skill SET {', '.join(set_clauses)} WHERE id = %s RETURNING *"

    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise SkillNameConflict(patch.get("name")) from exc

    if row is None:
        raise SkillNotFound(str(skill_id))
    return _row_to_skill(row)


def delete(skill_id: UUID) -> bool:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill WHERE id = %s", (skill_id,))
        return cur.rowcount == 1


__all__ = [
    "Skill",
    "SkillNameConflict",
    "SkillNotFound",
    "create",
    "delete",
    "get",
    "list_all",
    "list_bound_and_enabled",
    "update",
]
