"""CRUD for the ``mail_sentinel_rule`` table.

Provides MailRule dataclass, Condition TypedDict, RuleValidationError,
validation helpers, and full CRUD operations.

Design reference: spec §6.5.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from twaky.db import get_pool

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Condition(dict):
    """TypedDict for a single mail rule condition."""

    field: str
    operator: str
    value: str


class RuleValidationError(Exception):
    """Raised when a MailRule field fails validation."""


@dataclass(frozen=True)
class MailRule:
    """Frozen mirror of the ``mail_sentinel_rule`` table row (11 columns)."""

    id: UUID
    name: str
    description: str
    conditions: list[dict[str, Any]]
    combinator: str
    actions: list[str]
    priority: int
    enabled: bool
    run_on_threads: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

_FIELDS: frozenset[str] = frozenset({"from", "to", "subject", "body"})
_OPS: frozenset[str] = frozenset({"equals", "contains", "regex", "glob"})
_ACTIONS: frozenset[str] = frozenset(
    {"draft_reply", "archive", "mark_read", "notify", "delegate_to_atlas"}
)
_LABEL_ACTION_RE = re.compile(r"^label:[a-zA-Z0-9_\-]+$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_conditions(conditions: list[Any]) -> None:
    """Validate a list of Condition dicts.

    Raises
    ------
    RuleValidationError
        If any condition has an unknown field, unknown operator, non-string
        or empty value, or a regex value that does not compile.
    """
    for idx, cond in enumerate(conditions):
        field = cond.get("field", "")
        operator = cond.get("operator", "")
        value = cond.get("value", "")

        # field validation
        if field not in _FIELDS and not field.startswith("header:"):
            raise RuleValidationError(
                f"condition[{idx}]: unknown field {field!r}; "
                f"must be one of {sorted(_FIELDS)} or start with 'header:'"
            )

        # operator validation
        if operator not in _OPS:
            raise RuleValidationError(
                f"condition[{idx}]: unknown operator {operator!r}; "
                f"must be one of {sorted(_OPS)}"
            )

        # value validation
        if not isinstance(value, str) or not value:
            raise RuleValidationError(
                f"condition[{idx}]: value must be a non-empty string"
            )

        # regex compilation check
        if operator == "regex":
            try:
                re.compile(value)
            except re.error as exc:
                raise RuleValidationError(
                    f"condition[{idx}]: regex value {value!r} does not compile: {exc}"
                ) from exc


def validate_actions(actions: list[Any]) -> None:
    """Validate a list of action strings.

    Raises
    ------
    RuleValidationError
        If the list is empty or any action is not in the known set and does
        not match the ``label:<name>`` form.
    """
    if not actions:
        raise RuleValidationError("actions list must not be empty")

    for idx, action in enumerate(actions):
        if action not in _ACTIONS and not _LABEL_ACTION_RE.match(action):
            raise RuleValidationError(
                f"actions[{idx}]: unknown action {action!r}; "
                f"must be one of {sorted(_ACTIONS)} or match label:<name>"
            )


def validate_name(name: str) -> None:
    """Validate a rule name against the DB CHECK constraint regex.

    Raises
    ------
    RuleValidationError
        If *name* does not match ``^[a-z][a-z0-9_-]{0,63}$``.
    """
    if not _NAME_RE.match(name):
        raise RuleValidationError(
            f"invalid rule name {name!r}; must match ^[a-z][a-z0-9_-]{{0,63}}$"
        )


# ---------------------------------------------------------------------------
# Row → dataclass helper
# ---------------------------------------------------------------------------


def _row_to_rule(row: dict[str, Any]) -> MailRule:
    return MailRule(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        conditions=row["conditions"] or [],
        combinator=row["combinator"],
        actions=row["actions"] or [],
        priority=row["priority"],
        enabled=row["enabled"],
        run_on_threads=row["run_on_threads"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def list_all(*, enabled_only: bool = False) -> list[MailRule]:
    """Return all rules ordered by priority ASC, name ASC.

    Parameters
    ----------
    enabled_only:
        When True, only return rows where ``enabled = TRUE``.
    """
    if enabled_only:
        sql = (
            "SELECT * FROM mail_sentinel_rule "
            "WHERE enabled "
            "ORDER BY priority ASC, name ASC"
        )
    else:
        sql = "SELECT * FROM mail_sentinel_rule ORDER BY priority ASC, name ASC"

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return [_row_to_rule(r) for r in rows]


def by_name(name: str) -> MailRule | None:
    """Fetch a single rule by name. Returns None if not found."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM mail_sentinel_rule WHERE name = %s", (name,))
        row = cur.fetchone()
    return _row_to_rule(row) if row else None


def get(rule_id: UUID) -> MailRule | None:
    """Fetch a single rule by UUID. Returns None if not found."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM mail_sentinel_rule WHERE id = %s", (rule_id,))
        row = cur.fetchone()
    return _row_to_rule(row) if row else None


def create(
    *,
    name: str,
    description: str = "",
    conditions: list[dict[str, Any]] | None = None,
    combinator: str = "OR",
    actions: list[str],
    priority: int = 100,
    enabled: bool = True,
    run_on_threads: bool = True,
) -> MailRule:
    """Insert a new rule after full validation.

    Raises
    ------
    RuleValidationError
        If name, conditions, actions, or combinator are invalid.
    """
    validate_name(name)

    if conditions is None:
        conditions = []
    validate_conditions(conditions)
    validate_actions(actions)

    if combinator not in ("OR", "AND"):
        raise RuleValidationError(
            f"combinator must be 'OR' or 'AND', got {combinator!r}"
        )

    sql = (
        "INSERT INTO mail_sentinel_rule "
        "(name, description, conditions, combinator, actions, priority, enabled, run_on_threads) "
        "VALUES (%s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s) "
        "RETURNING *"
    )
    params = [
        name,
        description,
        json.dumps(conditions),
        combinator,
        json.dumps(actions),
        priority,
        enabled,
        run_on_threads,
    ]

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        inserted = cur.fetchone()

    assert inserted is not None  # RETURNING * always yields a row on INSERT
    return _row_to_rule(inserted)


_PATCH_ALLOWLIST = frozenset(
    {
        "name",
        "description",
        "conditions",
        "combinator",
        "actions",
        "priority",
        "enabled",
        "run_on_threads",
    }
)

_JSONB_FIELDS = frozenset({"conditions", "actions"})


def update(rule_id: UUID, patch: dict[str, Any]) -> MailRule:
    """Apply a partial patch to a rule row.

    Raises
    ------
    ValueError
        If *patch* is empty or contains unknown field names.
    RuleValidationError
        If conditions, actions, combinator, or name fail validation.
    KeyError
        If no row with *rule_id* exists.
    """
    if not patch:
        raise ValueError("empty patch")

    bad = set(patch) - _PATCH_ALLOWLIST
    if bad:
        raise ValueError(f"unknown fields: {sorted(bad)}")

    # Validate supplied fields
    if "name" in patch:
        validate_name(patch["name"])
    if "conditions" in patch:
        validate_conditions(patch["conditions"])
    if "actions" in patch:
        validate_actions(patch["actions"])
    if "combinator" in patch and patch["combinator"] not in ("OR", "AND"):
        raise RuleValidationError(
            f"combinator must be 'OR' or 'AND', got {patch['combinator']!r}"
        )

    set_clauses: list[str] = ["updated_at = now()"]
    params: list[Any] = []

    for key, value in patch.items():
        if key in _JSONB_FIELDS:
            set_clauses.append(f"{key} = %s::jsonb")
            params.append(json.dumps(value))
        else:
            set_clauses.append(f"{key} = %s")
            params.append(value)

    params.append(rule_id)

    sql = (
        f"UPDATE mail_sentinel_rule SET {', '.join(set_clauses)} "
        f"WHERE id = %s RETURNING *"
    )

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:
        raise KeyError(str(rule_id))
    return _row_to_rule(row)


def delete(rule_id: UUID) -> None:
    """Delete a rule by id. Raises KeyError if not present."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_rule WHERE id = %s", (rule_id,))
        if cur.rowcount == 0:
            raise KeyError(str(rule_id))


__all__ = [
    "Condition",
    "MailRule",
    "RuleValidationError",
    "by_name",
    "create",
    "delete",
    "get",
    "list_all",
    "update",
    "validate_actions",
    "validate_conditions",
    "validate_name",
]
