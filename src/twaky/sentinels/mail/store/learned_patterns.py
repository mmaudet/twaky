"""CRUD for the ``mail_sentinel_learned_pattern`` table.

Provides LearnedPattern dataclass, insert-or-bump with confidence smoothing,
by_sender lookup, list_all, and forget.

Design reference: spec §6.8.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from twaky.db import get_pool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTIVATION_THRESHOLD: Decimal = Decimal("0.90")
MIN_EVIDENCE: int = 3

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LearnedPattern:
    """Frozen mirror of the ``mail_sentinel_learned_pattern`` table row (7 columns)."""

    id: UUID
    sender_email: str
    rule_name: str
    confidence: Decimal
    evidence_count: int
    first_seen: datetime
    last_confirmed: datetime

    @property
    def is_active(self) -> bool:
        """Return True when confidence ≥ ACTIVATION_THRESHOLD and evidence_count ≥ MIN_EVIDENCE."""
        return (
            self.confidence >= ACTIVATION_THRESHOLD
            and self.evidence_count >= MIN_EVIDENCE
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_to_pattern(row: dict[str, Any]) -> LearnedPattern:
    return LearnedPattern(
        id=row["id"],
        sender_email=row["sender_email"],
        rule_name=row["rule_name"],
        confidence=Decimal(str(row["confidence"])),
        evidence_count=row["evidence_count"],
        first_seen=row["first_seen"],
        last_confirmed=row["last_confirmed"],
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def by_sender(email: str) -> LearnedPattern | None:
    """Return the highest-confidence active pattern for *email*.

    Active means confidence ≥ ACTIVATION_THRESHOLD and evidence_count ≥ MIN_EVIDENCE.
    Tie-breaking: confidence DESC, then last_confirmed ASC.

    Returns None if no active pattern exists.
    """
    email = email.lower()
    sql = (
        "SELECT * FROM mail_sentinel_learned_pattern "
        "WHERE sender_email = %s "
        "  AND confidence >= %s "
        "  AND evidence_count >= %s "
        "ORDER BY confidence DESC, last_confirmed ASC "
        "LIMIT 1"
    )
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (email, ACTIVATION_THRESHOLD, MIN_EVIDENCE))
        row = cur.fetchone()
    return _row_to_pattern(row) if row else None


def record_decision(
    sender_email: str,
    rule_name: str,
    *,
    confidence_hint: float = 0.85,
) -> LearnedPattern:
    """Insert a new pattern or bump an existing one with confidence smoothing.

    The confidence_hint is clamped to [0, 1] before being passed to the DB.
    On conflict the existing row is updated:
      - evidence_count incremented by 1
      - confidence = GREATEST(old, LEAST(1.0, old * 0.7 + hint * 0.3))
      - last_confirmed set to now()

    Parameters
    ----------
    sender_email:
        Lowercased before storing.
    rule_name:
        Name of the rule that matched.
    confidence_hint:
        Suggested confidence for this decision; clamped to [0, 1].

    Returns
    -------
    LearnedPattern
        The inserted or updated row.
    """
    sender_email = sender_email.lower()
    hint = Decimal(str(max(0.0, min(1.0, confidence_hint))))

    sql = """
        INSERT INTO mail_sentinel_learned_pattern
            (sender_email, rule_name, confidence, evidence_count)
        VALUES (%s, %s, %s, 1)
        ON CONFLICT (sender_email, rule_name) DO UPDATE SET
            evidence_count = mail_sentinel_learned_pattern.evidence_count + 1,
            confidence = GREATEST(
                mail_sentinel_learned_pattern.confidence,
                LEAST(1.0, mail_sentinel_learned_pattern.confidence * 0.7 + EXCLUDED.confidence * 0.3)
            ),
            last_confirmed = now()
        RETURNING *
    """

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (sender_email, rule_name, hint))
        row = cur.fetchone()

    assert row is not None  # RETURNING * always yields a row
    return _row_to_pattern(row)


def list_all(*, active_only: bool = False) -> list[LearnedPattern]:
    """Return all learned patterns, optionally filtered to active ones.

    Parameters
    ----------
    active_only:
        When True, only return rows where confidence ≥ ACTIVATION_THRESHOLD
        and evidence_count ≥ MIN_EVIDENCE.
        Active rows are ordered by sender_email ASC.
        All rows are ordered by sender_email ASC, confidence DESC.
    """
    if active_only:
        sql = (
            "SELECT * FROM mail_sentinel_learned_pattern "
            "WHERE confidence >= %s AND evidence_count >= %s "
            "ORDER BY sender_email ASC"
        )
        params: tuple[Any, ...] = (ACTIVATION_THRESHOLD, MIN_EVIDENCE)
    else:
        sql = (
            "SELECT * FROM mail_sentinel_learned_pattern "
            "ORDER BY sender_email ASC, confidence DESC"
        )
        params = ()

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_pattern(r) for r in rows]


def mark_confirmed(sender_email: str, rule_name: str) -> LearnedPattern | None:
    """SP5c 5.1: bump ``last_confirmed=now()`` when a health check LLM
    verdict confirmed the pattern still fits a recent mail from *sender_email*.

    Returns the updated row or None if the pattern no longer exists.
    """
    sender_email = sender_email.lower()
    sql = (
        "UPDATE mail_sentinel_learned_pattern "
        "SET last_confirmed = now() "
        "WHERE sender_email = %s AND rule_name = %s "
        "RETURNING *"
    )
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (sender_email, rule_name))
        row = cur.fetchone()
    return _row_to_pattern(row) if row else None


def decay_confidence(
    sender_email: str,
    rule_name: str,
    *,
    factor: Decimal = Decimal("0.7"),
    delete_below: Decimal = Decimal("0.5"),
) -> LearnedPattern | None:
    """SP5c 5.1: multiply ``confidence`` by *factor* to weaken a drifted
    pattern. If the result falls below *delete_below*, the row is
    DELETED entirely (returned None).

    Used by the pattern health check when an LLM verdict disagrees with
    a stored pattern (sender changed behaviour, false-positive stabilised).
    """
    sender_email = sender_email.lower()
    # Fetch → compute → either update or delete.
    fetch_sql = (
        "SELECT * FROM mail_sentinel_learned_pattern "
        "WHERE sender_email = %s AND rule_name = %s"
    )
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(fetch_sql, (sender_email, rule_name))
        row = cur.fetchone()
    if row is None:
        return None

    old_conf = Decimal(str(row["confidence"]))
    new_conf = old_conf * factor
    if new_conf < delete_below:
        forget(sender_email, rule_name)
        return None

    upd = (
        "UPDATE mail_sentinel_learned_pattern "
        "SET confidence = %s "
        "WHERE sender_email = %s AND rule_name = %s "
        "RETURNING *"
    )
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(upd, (new_conf, sender_email, rule_name))
        row = cur.fetchone()
    return _row_to_pattern(row) if row else None


def list_active_stale(days: int, *, limit: int = 5) -> list[LearnedPattern]:
    """SP5c 5.1: return up to *limit* ACTIVE patterns whose ``last_confirmed``
    is older than *days*, ordered oldest-first.

    Used by the health check to pick which patterns to re-verify this tick.
    """
    sql = (
        "SELECT * FROM mail_sentinel_learned_pattern "
        "WHERE confidence >= %s AND evidence_count >= %s "
        "  AND last_confirmed < now() - make_interval(days => %s) "
        "ORDER BY last_confirmed ASC "
        "LIMIT %s"
    )
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (ACTIVATION_THRESHOLD, MIN_EVIDENCE, days, limit))
        rows = cur.fetchall()
    return [_row_to_pattern(r) for r in rows]


def forget(sender_email: str, rule_name: str) -> None:
    """Delete the pattern identified by (sender_email lowercase, rule_name).

    Silently does nothing if the row does not exist.
    """
    sender_email = sender_email.lower()
    sql = (
        "DELETE FROM mail_sentinel_learned_pattern "
        "WHERE sender_email = %s AND rule_name = %s"
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (sender_email, rule_name))


__all__ = [
    "ACTIVATION_THRESHOLD",
    "MIN_EVIDENCE",
    "LearnedPattern",
    "by_sender",
    "decay_confidence",
    "forget",
    "list_active_stale",
    "list_all",
    "mark_confirmed",
    "record_decision",
]
