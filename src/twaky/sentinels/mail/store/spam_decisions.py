"""CRUD for the ``mail_sentinel_spam_decision`` table.

Provides SpamDecision dataclass, SpamDecisionNotFound / AlreadyRestored
exceptions, and operations: insert, get, list_recent, restore, stats,
purge_active, purge_restored.

Design reference: spec §6.1 / SP6c Task 3.

SP6d T1 (D3): added optional provenance columns (origin_mailbox_id,
origin_mailbox_role, envelope_headers).  Column existence is detected once at
first call and cached at module scope via ``_HAS_PROVENANCE_COLUMNS``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import sql as pgsql
from psycopg.rows import dict_row

from twaky.db import get_pool

# ---------------------------------------------------------------------------
# Module-level provenance column cache (SP6d T1 D3)
# ---------------------------------------------------------------------------

#: None = unknown (first call will detect), True = columns exist, False = absent.
_HAS_PROVENANCE_COLUMNS: bool | None = None


def _reset_column_cache_for_tests() -> None:
    """Reset the module-level column-existence cache.

    Call this in an ``autouse=True`` fixture so test isolation is guaranteed.
    Each test that exercises the detection logic starts from a clean state.
    """
    global _HAS_PROVENANCE_COLUMNS
    _HAS_PROVENANCE_COLUMNS = None


def _detect_provenance_columns() -> bool:
    """Return True when ``envelope_headers`` column exists on the table.

    Checks ``information_schema.columns``; result is cached in
    ``_HAS_PROVENANCE_COLUMNS`` after the first successful call.
    """
    global _HAS_PROVENANCE_COLUMNS
    if _HAS_PROVENANCE_COLUMNS is not None:
        return _HAS_PROVENANCE_COLUMNS
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'mail_sentinel_spam_decision' "
            "AND column_name = 'envelope_headers'",
        )
        _HAS_PROVENANCE_COLUMNS = cur.fetchone() is not None
    return _HAS_PROVENANCE_COLUMNS


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SpamDecisionNotFound(Exception):
    """Raised by restore() when no row matches the given decision_id."""


class AlreadyRestored(Exception):
    """Raised by restore() when the decision was already restored."""


@dataclass(frozen=True)
class SpamDecision:
    """Frozen mirror of the ``mail_sentinel_spam_decision`` table row.

    The three provenance fields (SP6d T1) are present in the dataclass but
    will be ``None`` when the columns do not exist on the live DB yet.
    """

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
    # SP6d T1 provenance columns — None when columns absent on live DB.
    origin_mailbox_id: str | None = None
    origin_mailbox_role: str | None = None
    envelope_headers: dict[str, Any] | None = None


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
        origin_mailbox_id=row.get("origin_mailbox_id"),
        origin_mailbox_role=row.get("origin_mailbox_role"),
        envelope_headers=row.get("envelope_headers"),
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
    origin_mailbox_id: str | None = None,
    origin_mailbox_role: str | None = None,
    envelope_headers: dict[str, Any] | None = None,
) -> UUID:
    """Insert a spam decision row and return its UUID.

    When the provenance columns exist (detected on first call and cached),
    includes ``origin_mailbox_id``, ``origin_mailbox_role``, and
    ``envelope_headers`` in the INSERT.  Falls back to a legacy INSERT
    (without those columns) when the columns are absent — supporting a
    rolling-upgrade window where the migration has not yet been applied.

    Returns only the UUID (via RETURNING id) for minimal network overhead.
    """
    has_prov = _detect_provenance_columns()

    if has_prov:
        # Encode dict to JSON string for psycopg3 JSONB binding.
        env_headers_json: str | None = (
            json.dumps(envelope_headers) if envelope_headers is not None else None
        )
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mail_sentinel_spam_decision "
                "(email_id, thread_id, sender_email, subject, received_at, "
                " bucket, signal_source, score, reason, "
                " origin_mailbox_id, origin_mailbox_role, envelope_headers) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
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
                    origin_mailbox_id,
                    origin_mailbox_role,
                    env_headers_json,
                ),
            )
            row = cur.fetchone()
    else:
        # Legacy INSERT — provenance columns not yet migrated.
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

    The returned ``SpamDecision`` objects include the three provenance fields
    (``origin_mailbox_id``, ``origin_mailbox_role``, ``envelope_headers``)
    when the columns exist on the live DB, and ``None`` for those attributes
    when the columns are absent.
    """
    where_clauses: list[str] = []
    params: list[Any] = []

    if bucket is not None:
        where_clauses.append("bucket = %s")
        params.append(bucket)

    if before is not None:
        where_clauses.append("decided_at < %s")
        params.append(before)

    params.append(limit)

    if where_clauses:
        query: pgsql.SQL | pgsql.Composed = pgsql.SQL(
            "SELECT * FROM mail_sentinel_spam_decision WHERE {where} "
            "ORDER BY decided_at DESC LIMIT %s"
        ).format(where=pgsql.SQL(" AND ").join(pgsql.SQL(c) for c in where_clauses))
    else:
        query = pgsql.SQL(
            "SELECT * FROM mail_sentinel_spam_decision "
            "ORDER BY decided_at DESC LIMIT %s"
        )

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
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
    "_reset_column_cache_for_tests",
    "get",
    "insert",
    "list_recent",
    "purge_active",
    "purge_restored",
    "restore",
    "stats",
]
