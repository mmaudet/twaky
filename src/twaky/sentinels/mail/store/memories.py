"""CRUD for the ``mail_sentinel_memory`` table.

Provides MailMemory dataclass, PUBLIC_EMAIL_DOMAINS refusal, dedup via
ON CONFLICT DO NOTHING, candidate_pool union query, and purge_expired TTL.

Design reference: spec §6.7.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from twaky.db import get_pool

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MailMemory:
    """Frozen mirror of the ``mail_sentinel_memory`` table row."""

    id: UUID
    kind: str
    scope: str
    scope_value: str
    content: str
    evidence: list[Any]
    created_at: datetime
    expires_at: datetime | None  # NULL when "keep permanent"
    source: str = "manual"
    sender_email: str | None = None
    mission_id: UUID | None = None
    confidence: float | None = None


# ---------------------------------------------------------------------------
# Public domain refusal list
# ---------------------------------------------------------------------------

PUBLIC_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        # Google
        "gmail.com",
        "googlemail.com",
        # Microsoft
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        # Yahoo
        "yahoo.com",
        "yahoo.fr",
        "yahoo.co.uk",
        "ymail.com",
        # Proton
        "protonmail.com",
        "proton.me",
        # Apple
        "icloud.com",
        "me.com",
        "mac.com",
        # AOL
        "aol.com",
        # GMX
        "gmx.com",
        "gmx.de",
        "gmx.fr",
        # Mail.com
        "mail.com",
        # French ISPs
        "orange.fr",
        "wanadoo.fr",
        "free.fr",
        "sfr.fr",
        "laposte.net",
    }
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalized_content(s: str) -> str:
    """Collapse internal whitespace and strip leading/trailing whitespace."""
    return " ".join(s.split()).strip()


def _row_to_memory(row: dict[str, Any]) -> MailMemory:
    return MailMemory(
        id=row["id"],
        kind=row["kind"],
        scope=row["scope"],
        scope_value=row["scope_value"],
        content=row["content"],
        evidence=row.get("evidence") or [],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        source=row.get("source", "manual"),
        sender_email=row.get("sender_email"),
        mission_id=row.get("mission_id"),
        confidence=(float(row["confidence"]) if row.get("confidence") is not None else None),
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def insert(
    *,
    kind: str,
    scope: str,
    scope_value: str,
    content: str,
    evidence: list[Any] | None = None,
    source: str = "manual",
    sender_email: str | None = None,
    mission_id: UUID | None = None,
    confidence: float | None = None,
) -> MailMemory | None:
    """Insert a memory row, returning None on duplicate or public-domain refusal.

    Extra fields introduced in SP5b:
      - source: 'manual' | 'auto_diff' | 'auto_reclass' | 'auto_move'
      - sender_email: dénormalisation pour indexation quand scope='sender'
      - mission_id: trace la mission d'origine (audit)
      - confidence: 0..1, utilisée par le ranking d'injection
    """
    import json

    scope_value = scope_value.strip().lower()
    content = _normalized_content(content)

    if scope == "domain" and scope_value in PUBLIC_EMAIL_DOMAINS:
        log.info(
            "mail_sentinel_memory: refusing domain-scoped insert for public domain %r",
            scope_value,
        )
        return None

    if evidence is None:
        evidence = []

    sql = (
        "INSERT INTO mail_sentinel_memory "
        "(kind, scope, scope_value, content, evidence, "
        " source, sender_email, mission_id, confidence) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s) "
        "ON CONFLICT (kind, scope, scope_value, content) DO NOTHING "
        "RETURNING *"
    )
    params = [
        kind, scope, scope_value, content, json.dumps(evidence),
        source, sender_email, mission_id, confidence,
    ]

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    return _row_to_memory(row) if row else None


def candidate_pool(sender_email: str, limit: int = 100) -> list[MailMemory]:
    """Return non-expired memories relevant to *sender_email*.

    Fetches the union of:
    - ``scope='sender'`` rows whose ``scope_value`` matches the normalized email.
    - ``scope='domain'`` rows whose ``scope_value`` matches the sender's domain.
    - ``scope='global'`` rows (all of them, up to the limit).

    Results are ordered by ``created_at DESC``.
    """
    sender_email = sender_email.lower()
    domain = sender_email.rsplit("@", 1)[-1]

    sql = (
        "SELECT * FROM mail_sentinel_memory "
        "WHERE expires_at > now() "
        "AND ("
        "  (scope = 'sender' AND scope_value = %s) "
        "  OR (scope = 'domain' AND scope_value = %s) "
        "  OR scope = 'global'"
        ") "
        "ORDER BY created_at DESC "
        "LIMIT %s"
    )

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (sender_email, domain, limit))
        rows = cur.fetchall()

    return [_row_to_memory(r) for r in rows]


def list_recent(*, scope: str | None = None, limit: int = 100) -> list[MailMemory]:
    """Return recently created memories, optionally filtered by scope.

    Parameters
    ----------
    scope:
        When provided, only return rows where ``scope = %s``.
    limit:
        Maximum number of rows to return (default 100).
    """
    if scope is not None:
        sql = (
            "SELECT * FROM mail_sentinel_memory "
            "WHERE scope = %s "
            "ORDER BY created_at DESC "
            "LIMIT %s"
        )
        params: list[Any] = [scope, limit]
    else:
        sql = "SELECT * FROM mail_sentinel_memory ORDER BY created_at DESC LIMIT %s"
        params = [limit]

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [_row_to_memory(r) for r in rows]


def get_many(ids: list[UUID]) -> list[MailMemory]:
    """Return memories for the given UUIDs (order not guaranteed).

    Rows that do not exist are silently omitted.
    """
    if not ids:
        return []

    sql = "SELECT * FROM mail_sentinel_memory WHERE id = ANY(%s)"

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, ([str(i) for i in ids],))
        rows = cur.fetchall()

    return [_row_to_memory(r) for r in rows]


def purge_expired() -> int:
    """Delete rows whose TTL has elapsed.

    Returns
    -------
    int
        Number of rows deleted.
    """
    sql = "DELETE FROM mail_sentinel_memory WHERE expires_at <= now()"

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.rowcount


def touch(ids: list[UUID]) -> int:
    """Push ``expires_at`` to now() + 7 days for rows in *ids*.

    Skips rows where ``expires_at IS NULL`` ("keep permanent"). Returns
    the number of rows actually updated.
    """
    if not ids:
        return 0
    sql = (
        "UPDATE mail_sentinel_memory "
        "SET expires_at = now() + INTERVAL '7 days' "
        "WHERE id = ANY(%s) AND expires_at IS NOT NULL"
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (list(ids),))
        return cur.rowcount


def list_for_prompt(
    *,
    sender_email: str,
    sender_domain: str,
    limit: int = 16,
) -> list[MailMemory]:
    """Return memories ranked by scope × confidence × age decay.

    Ranking = scope_weight × confidence × exp(-age_days / 30):
      - scope=sender → weight 3.0
      - scope=domain → weight 1.5
      - scope=global → weight 1.0
    Rows with expires_at in the past are excluded; rows with
    expires_at IS NULL are always eligible.
    """
    sql = """
        WITH candidates AS (
          SELECT id, kind, scope, scope_value, content, evidence,
                 created_at, expires_at, source, sender_email,
                 mission_id, confidence,
                 (CASE scope
                    WHEN 'sender' THEN 3.0
                    WHEN 'domain' THEN 1.5
                    WHEN 'global' THEN 1.0
                    ELSE 0.5
                  END) AS scope_weight,
                 COALESCE(confidence, 0.5) AS conf,
                 EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0 AS age_days
          FROM mail_sentinel_memory
          WHERE ((scope = 'sender' AND scope_value = %s)
              OR (scope = 'domain' AND scope_value = %s)
              OR (scope = 'global'))
            AND (expires_at IS NULL OR expires_at > now())
        )
        SELECT *
        FROM candidates
        ORDER BY (scope_weight * conf * exp(-age_days / 30.0)) DESC
        LIMIT %s
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (sender_email.lower(), sender_domain.lower(), limit))
        return [_row_to_memory(r) for r in cur.fetchall()]


def set_persist(memory_id: UUID, persist: bool) -> MailMemory | None:
    """Toggle a memory between permanent (expires_at=NULL) and 7-day TTL."""
    if persist:
        sql = (
            "UPDATE mail_sentinel_memory SET expires_at = NULL "
            "WHERE id = %s RETURNING *"
        )
        params: tuple[Any, ...] = (memory_id,)
    else:
        sql = (
            "UPDATE mail_sentinel_memory "
            "SET expires_at = now() + INTERVAL '7 days' "
            "WHERE id = %s RETURNING *"
        )
        params = (memory_id,)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return _row_to_memory(row) if row else None


__all__ = [
    "PUBLIC_EMAIL_DOMAINS",
    "MailMemory",
    "candidate_pool",
    "get_many",
    "insert",
    "list_for_prompt",
    "list_recent",
    "purge_expired",
    "set_persist",
    "touch",
]
