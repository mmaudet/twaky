"""CRUD for ``mail_sentinel_observation``.

Audit log of user actions the observer detected and extracted. Every
observation is idempotent on `(email_id, mailbox_id, observation_type)`
via the UNIQUE constraint — a crash-and-replay tick cannot double-count.
Rows older than 30 days are purged by the housekeeping loop; the log
is for debug/observability, not load-bearing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from twaky.db import get_pool


class ObservationType(str, Enum):
    DRAFT_SENT = "draft_sent"
    MARKED_SPAM = "marked_spam"
    UNMARKED_SPAM = "unmarked_spam"
    MOVED_TO_CUSTOM = "moved_to_custom"


class ExtractionOutcome(str, Enum):
    EXTRACTED = "extracted"
    SKIPPED_TRIVIAL = "skipped_trivial"
    SKIPPED_NO_MATCH = "skipped_no_match"
    ERROR = "error"


@dataclass(frozen=True)
class Observation:
    id: UUID
    email_id: str
    mailbox_id: str
    observation_type: ObservationType
    observed_at: datetime
    extraction_outcome: ExtractionOutcome
    memory_ids: list[UUID]
    pattern_ids: list[UUID]
    error_repr: str | None


def _row(r: dict[str, Any]) -> Observation:
    return Observation(
        id=r["id"],
        email_id=r["email_id"],
        mailbox_id=r["mailbox_id"],
        observation_type=ObservationType(r["observation_type"]),
        observed_at=r["observed_at"],
        extraction_outcome=ExtractionOutcome(r["extraction_outcome"]),
        memory_ids=list(r["memory_ids"] or []),
        pattern_ids=list(r["pattern_ids"] or []),
        error_repr=r["error_repr"],
    )


def insert_if_new(
    *,
    email_id: str,
    mailbox_id: str,
    observation_type: ObservationType,
    extraction_outcome: ExtractionOutcome,
    memory_ids: list[UUID] | tuple[UUID, ...] = (),
    pattern_ids: list[UUID] | tuple[UUID, ...] = (),
    error_repr: str | None = None,
) -> Observation | None:
    sql = """
        INSERT INTO mail_sentinel_observation
            (email_id, mailbox_id, observation_type, extraction_outcome,
             memory_ids, pattern_ids, error_repr)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (email_id, mailbox_id, observation_type) DO NOTHING
        RETURNING *
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        try:
            cur.execute(
                sql,
                (
                    email_id,
                    mailbox_id,
                    observation_type.value,
                    extraction_outcome.value,
                    list(memory_ids),
                    list(pattern_ids),
                    error_repr,
                ),
            )
            row = cur.fetchone()
        except UniqueViolation:
            return None
    return _row(row) if row else None


def list_recent(*, limit: int = 100) -> list[Observation]:
    sql = "SELECT * FROM mail_sentinel_observation ORDER BY observed_at DESC LIMIT %s"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (limit,))
        return [_row(r) for r in cur.fetchall()]


def purge_older_than(days: int) -> int:
    sql = (
        "DELETE FROM mail_sentinel_observation "
        "WHERE observed_at < now() - make_interval(days => %s)"
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (days,))
        return cur.rowcount


__all__ = [
    "ExtractionOutcome",
    "Observation",
    "ObservationType",
    "insert_if_new",
    "list_recent",
    "purge_older_than",
]
