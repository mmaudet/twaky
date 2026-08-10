"""CRUD for the ``mail_sentinel_spam_decision`` table.

Provides SpamDecision dataclass, SpamDecisionNotFound / AlreadyRestored
exceptions, and operations: insert, get, list_recent, restore, stats,
purge_active, purge_restored.

Design reference: spec §6.1 / SP6c Task 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from twaky.db import get_pool

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SpamDecisionNotFound(Exception):
    """Raised by restore() when no row matches the given decision_id."""


class AlreadyRestored(Exception):
    """Raised by restore() when the decision was already restored."""


@dataclass(frozen=True)
class SpamDecision:
    """Frozen mirror of the ``mail_sentinel_spam_decision`` table row (13 columns)."""

    id: UUID
    email_id: str
    thread_id: str | None
    sender_email: str
    subject: str
    received_at: datetime
    bucket: str
    signal_source: str
    score: float | None
    reason: str | None
    restored_at: datetime | None
    restored_by: str | None
    decided_at: datetime


# ---------------------------------------------------------------------------
# Row → dataclass helper
# ---------------------------------------------------------------------------


def _row_to_decision(row: dict[str, Any]) -> SpamDecision:
    return SpamDecision(
        id=row["id"],
        email_id=row["email_id"],
        thread_id=row["thread_id"],
        sender_email=row["sender_email"],
        subject=row["subject"],
        received_at=row["received_at"],
        bucket=row["bucket"],
        signal_source=row["signal_source"],
        score=float(row["score"]) if row["score"] is not None else None,
        reason=row["reason"],
        restored_at=row["restored_at"],
        restored_by=row["restored_by"],
        decided_at=row["decided_at"],
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def insert(
    *,
    email_id: str,
    thread_id: str | None,
    sender_email: str,
    subject: str,
    received_at: datetime,
    bucket: str,
    signal_source: str,
    score: float | None,
    reason: str | None,
) -> UUID:
    """Insert a spam decision row and return its UUID.

    Returns only the UUID (via RETURNING id) for minimal network overhead.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, thread_id, sender_email, subject, received_at, "
            " bucket, signal_source, score, reason) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                email_id,
                thread_id,
                sender_email,
                subject,
                received_at,
                bucket,
                signal_source,
                score,
                reason,
            ),
        )
        row = cur.fetchone()
        assert row is not None  # RETURNING id always yields a row on INSERT
        return row[0]


def get(decision_id: UUID) -> SpamDecision | None:
    """Fetch a single decision by UUID. Returns None if not found."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM mail_sentinel_spam_decision WHERE id = %s",
            (decision_id,),
        )
        row = cur.fetchone()
    return _row_to_decision(row) if row else None


def list_recent(
    *,
    bucket: str | None = None,
    limit: int = 50,
    before: datetime | None = None,
) -> list[SpamDecision]:
    """Return recent decisions ordered by decided_at DESC.

    Parameters
    ----------
    bucket:
        When provided, only return rows where ``bucket = %s``.
    limit:
        Maximum number of rows to return (default 50).
    before:
        When provided, only return rows where ``decided_at < %s``.
    """
    where_clauses: list[str] = []
    params: list[Any] = []

    if bucket is not None:
        where_clauses.append("bucket = %s")
        params.append(bucket)

    if before is not None:
        where_clauses.append("decided_at < %s")
        params.append(before)

    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)
    else:
        where_sql = ""

    params.append(limit)

    sql = (
        f"SELECT * FROM mail_sentinel_spam_decision "
        f"{where_sql} "
        f"ORDER BY decided_at DESC LIMIT %s"
    )

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [_row_to_decision(r) for r in rows]


def restore(decision_id: UUID, restored_by: str) -> SpamDecision:
    """Mark a spam decision as restored (not-spam / false positive).

    Sets ``restored_at = now()`` and ``restored_by = restored_by``.
    Does NOT delete the row — preserves audit trail.

    Raises
    ------
    AlreadyRestored
        If the decision has already been restored (``restored_at IS NOT NULL``).
    SpamDecisionNotFound
        If no row with ``decision_id`` exists.
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "UPDATE mail_sentinel_spam_decision "
            "SET restored_at = now(), restored_by = %s "
            "WHERE id = %s AND restored_at IS NULL "
            "RETURNING *",
            (restored_by, decision_id),
        )
        row = cur.fetchone()

        if row is not None:
            return _row_to_decision(row)

        # Distinguish "already restored" from "not found"
        cur.execute(
            "SELECT id, restored_at FROM mail_sentinel_spam_decision WHERE id = %s",
            (decision_id,),
        )
        existing = cur.fetchone()

    if existing is None:
        raise SpamDecisionNotFound(str(decision_id))
    raise AlreadyRestored(str(decision_id))


def stats(days: int = 30) -> dict[str, int]:
    """Return aggregated counts over the last ``days`` days.

    Returns
    -------
    dict with keys:
        ``spam``, ``newsletter``, ``phishing_alert``, ``restored``,
        ``total_processed``.
    """
    sql = (
        "SELECT "
        "  COUNT(*) FILTER (WHERE bucket = 'spam')            AS spam, "
        "  COUNT(*) FILTER (WHERE bucket = 'newsletter')       AS newsletter, "
        "  COUNT(*) FILTER (WHERE bucket = 'phishing-alert')   AS phishing_alert, "
        "  COUNT(*) FILTER (WHERE restored_at IS NOT NULL)     AS restored, "
        "  COUNT(*)                                            AS total_processed "
        "FROM mail_sentinel_spam_decision "
        "WHERE decided_at > now() - %s * INTERVAL '1 day'"
    )

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (days,))
        row = cur.fetchone()

    assert row is not None  # aggregate always returns one row
    return {
        "spam": int(row["spam"]),
        "newsletter": int(row["newsletter"]),
        "phishing_alert": int(row["phishing_alert"]),
        "restored": int(row["restored"]),
        "total_processed": int(row["total_processed"]),
    }


def purge_active(older_than_days: int) -> int:
    """Delete non-restored decisions older than ``older_than_days`` days.

    Returns
    -------
    int
        Number of rows deleted.
    """
    sql = (
        "DELETE FROM mail_sentinel_spam_decision "
        "WHERE restored_at IS NULL "
        "AND decided_at < now() - %s * INTERVAL '1 day'"
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (older_than_days,))
        return cur.rowcount


def purge_restored(older_than_days: int) -> int:
    """Delete restored decisions older than ``older_than_days`` days.

    Returns
    -------
    int
        Number of rows deleted.
    """
    sql = (
        "DELETE FROM mail_sentinel_spam_decision "
        "WHERE restored_at IS NOT NULL "
        "AND decided_at < now() - %s * INTERVAL '1 day'"
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (older_than_days,))
        return cur.rowcount


__all__ = [
    "AlreadyRestored",
    "SpamDecision",
    "SpamDecisionNotFound",
    "get",
    "insert",
    "list_recent",
    "purge_active",
    "purge_restored",
    "restore",
    "stats",
]
