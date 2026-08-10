"""psycopg3 CRUD for the ``sentinel`` and ``sentinel_run`` tables.

Follows the idiom established in src/twaky/skills_config/repository.py:
  - dict_row factory for all SELECT queries
  - ``with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:``
  - RETURNING * on all writes
  - %s::jsonb for JSONB parameter binding
  - allowlist of writable fields; ValueError on unknown keys / empty patch
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from twaky.db import get_pool
from twaky.sentinels.models import SentinelConfig, SentinelRun


class SentinelNotFound(Exception):
    """Raised when a sentinel row identified by name does not exist."""


# ---------------------------------------------------------------------------
# Row → dataclass helpers
# ---------------------------------------------------------------------------


def _row_to_config(row: dict[str, Any]) -> SentinelConfig:
    return SentinelConfig(
        name=row["name"],
        display_name=row["display_name"],
        description=row["description"],
        version=row["version"],
        enabled=row["enabled"],
        config_schema=row["config_schema"] or {},
        config_values=row["config_values"] or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_run(row: dict[str, Any]) -> SentinelRun:
    return SentinelRun(
        id=row["id"],
        sentinel_name=row["sentinel_name"],
        event_ref=row["event_ref"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        duration_ms=row["duration_ms"],
        outcome=row["outcome"],
        mission_id=row["mission_id"],
        llm_calls=row["llm_calls"],
        error_repr=row["error_repr"],
        trace=row["trace"] if row["trace"] is not None else [],
    )


# ---------------------------------------------------------------------------
# Sentinel CRUD
# ---------------------------------------------------------------------------


def list_all() -> list[SentinelConfig]:
    """Return all sentinel rows ordered by name."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM sentinel ORDER BY name")
        rows = cur.fetchall()
    return [_row_to_config(r) for r in rows]


def list_enabled() -> list[SentinelConfig]:
    """Return only enabled sentinel rows ordered by name."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM sentinel WHERE enabled ORDER BY name")
        rows = cur.fetchall()
    return [_row_to_config(r) for r in rows]


def get(name: str) -> SentinelConfig | None:
    """Fetch a single sentinel by name. Returns None if not found."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM sentinel WHERE name = %s", (name,))
        row = cur.fetchone()
    return _row_to_config(row) if row else None


def update(name: str, patch: dict[str, Any]) -> SentinelConfig:
    """Apply a partial patch to a sentinel row.

    Writable fields: ``enabled``, ``config_values``.
    Raises
    ------
    ValueError
        If *patch* is empty or contains unknown field names.
    SentinelNotFound
        If no row with the given *name* exists.
    """
    if not patch:
        raise ValueError("empty patch")

    allowed = {"enabled", "config_values"}
    bad = set(patch) - allowed
    if bad:
        raise ValueError(f"unknown fields: {sorted(bad)}")

    # Allowlist above is the sole guard against SQL injection in column names.
    set_clauses: list[str] = []
    params: list[Any] = []
    for key, value in patch.items():
        if key == "config_values":
            set_clauses.append(f"{key} = %s::jsonb")
            params.append(json.dumps(value))
        else:
            set_clauses.append(f"{key} = %s")
            params.append(value)
    params.append(name)

    sql = f"UPDATE sentinel SET {', '.join(set_clauses)} WHERE name = %s RETURNING *"

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:
        raise SentinelNotFound(name)
    return _row_to_config(row)


def update_config_value(name: str, key: str, value: Any) -> SentinelConfig:
    """Merge a single key into ``config_values`` without clobbering siblings.

    Uses ``jsonb_set(config_values, %s, %s::jsonb, true)`` so only the
    targeted key is written; all other keys in the JSONB object are preserved.

    Parameters
    ----------
    name:
        Sentinel primary key.
    key:
        Top-level key inside ``config_values`` to set (e.g. ``"jmap_last_state"``).
    value:
        The new value.  Will be JSON-serialised before binding.

    Raises
    ------
    SentinelNotFound
        If no row with the given *name* exists.
    """
    # jsonb_set path must be a text[] literal e.g. '{jmap_last_state}'
    path = "{" + key + "}"
    sql = (
        "UPDATE sentinel "
        "SET config_values = jsonb_set(config_values, %s, %s::jsonb, true) "
        "WHERE name = %s "
        "RETURNING *"
    )
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (path, json.dumps(value), name))
        row = cur.fetchone()

    if row is None:
        raise SentinelNotFound(name)
    return _row_to_config(row)


# ---------------------------------------------------------------------------
# Sentinel run CRUD
# ---------------------------------------------------------------------------


def insert_run(row: dict[str, Any]) -> SentinelRun:
    """Insert a new sentinel_run row.

    *row* must include at least ``sentinel_name``, ``event_ref``, ``outcome``.
    Optional keys: ``started_at``, ``completed_at``, ``duration_ms``,
    ``mission_id``, ``llm_calls``, ``error_repr``, ``trace``.

    Returns the fully-populated SentinelRun (with server-generated ``id``
    and ``started_at`` default if not supplied).
    """
    cols = [
        "sentinel_name",
        "event_ref",
        "outcome",
        "started_at",
        "completed_at",
        "duration_ms",
        "mission_id",
        "llm_calls",
        "error_repr",
        "trace",
    ]
    # Build column list and value list only for supplied keys
    provided_cols = [c for c in cols if c in row]
    placeholders = []
    params: list[Any] = []
    for col in provided_cols:
        if col == "trace":
            placeholders.append("%s::jsonb")
            params.append(json.dumps(row[col]))
        else:
            placeholders.append("%s")
            params.append(row[col])

    col_list = ", ".join(provided_cols)
    ph_list = ", ".join(placeholders)
    sql = f"INSERT INTO sentinel_run ({col_list}) VALUES ({ph_list}) RETURNING *"

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        inserted = cur.fetchone()

    assert inserted is not None  # RETURNING * always yields a row on INSERT
    return _row_to_run(inserted)


def update_run(run_id: UUID, patch: dict[str, Any]) -> SentinelRun:
    """Partial update a sentinel_run row.

    Writable fields: ``completed_at``, ``duration_ms``, ``outcome``,
    ``mission_id``, ``llm_calls``, ``error_repr``, ``trace``.

    Raises
    ------
    ValueError
        If *patch* is empty or contains unknown field names.
    SentinelNotFound
        If no row with the given *run_id* exists.
    """
    if not patch:
        raise ValueError("empty patch")

    allowed = {
        "completed_at",
        "duration_ms",
        "outcome",
        "mission_id",
        "llm_calls",
        "error_repr",
        "trace",
    }
    bad = set(patch) - allowed
    if bad:
        raise ValueError(f"unknown fields: {sorted(bad)}")

    set_clauses: list[str] = []
    params: list[Any] = []
    for key, value in patch.items():
        if key == "trace":
            set_clauses.append(f"{key} = %s::jsonb")
            params.append(json.dumps(value))
        else:
            set_clauses.append(f"{key} = %s")
            params.append(value)
    params.append(run_id)

    sql = f"UPDATE sentinel_run SET {', '.join(set_clauses)} WHERE id = %s RETURNING *"

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:
        raise SentinelNotFound(str(run_id))
    return _row_to_run(row)


def list_runs(
    sentinel_name: str,
    limit: int = 100,
    before: datetime | None = None,
) -> list[SentinelRun]:
    """List sentinel_run rows for a given sentinel, newest first.

    Parameters
    ----------
    sentinel_name:
        Filter runs to this sentinel.
    limit:
        Maximum rows to return.
    before:
        If supplied, only return rows with ``started_at < before``
        (pagination cursor).
    """
    if before is not None:
        sql = (
            "SELECT * FROM sentinel_run "
            "WHERE sentinel_name = %s AND started_at < %s "
            "ORDER BY started_at DESC LIMIT %s"
        )
        params: tuple[Any, ...] = (sentinel_name, before, limit)
    else:
        sql = (
            "SELECT * FROM sentinel_run "
            "WHERE sentinel_name = %s "
            "ORDER BY started_at DESC LIMIT %s"
        )
        params = (sentinel_name, limit)

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_run(r) for r in rows]


def get_run(run_id: UUID) -> SentinelRun | None:
    """Fetch a single sentinel_run by id. Returns None if not found."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM sentinel_run WHERE id = %s", (run_id,))
        row = cur.fetchone()
    return _row_to_run(row) if row else None


def find_run_by_event_ref(
    sentinel_name: str,
    event_ref: str,
    within_hours: int = 24,
) -> SentinelRun | None:
    """Return the most recent run matching *event_ref* within the time window.

    The SQL predicate is ``started_at > now() - %s * INTERVAL '1 hour'``
    (spec §4.1 idempotency guard requirement).

    Returns None if no matching run is found.
    """
    sql = (
        "SELECT * FROM sentinel_run "
        "WHERE sentinel_name = %s "
        "  AND event_ref = %s "
        "  AND started_at > now() - %s * INTERVAL '1 hour' "
        "ORDER BY started_at DESC LIMIT 1"
    )
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (sentinel_name, event_ref, within_hours))
        row = cur.fetchone()
    return _row_to_run(row) if row else None


def count_runs_24h(sentinel_name: str) -> tuple[int, int]:
    """Return ``(total, errors)`` run counts in the last 24 hours.

    Used by the housekeeping loop and the observability API.
    """
    sql = (
        "SELECT "
        "  COUNT(*) AS total, "
        "  COUNT(*) FILTER (WHERE outcome = 'error') AS errors "
        "FROM sentinel_run "
        "WHERE sentinel_name = %s AND started_at > now() - INTERVAL '24 hours'"
    )
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (sentinel_name,))
        row = cur.fetchone()
    assert row is not None
    return (int(row["total"]), int(row["errors"]))


def purge_old_runs(retention_days: int) -> int:
    """Delete sentinel_run rows older than *retention_days* days.

    Returns the number of deleted rows (used by the housekeeping loop for
    its info log).
    """
    sql = "DELETE FROM sentinel_run WHERE started_at < now() - %s * INTERVAL '1 day'"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (retention_days,))
        return cur.rowcount


__all__ = [
    "SentinelNotFound",
    "count_runs_24h",
    "find_run_by_event_ref",
    "get",
    "get_run",
    "insert_run",
    "list_all",
    "list_enabled",
    "list_runs",
    "purge_old_runs",
    "update",
    "update_config_value",
    "update_run",
]
