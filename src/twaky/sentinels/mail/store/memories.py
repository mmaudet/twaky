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
    """Frozen mirror of the ``mail_sentinel_memory`` table row (8 columns)."""

    id: UUID
    kind: str
    scope: str
    scope_value: str
    content: str
    evidence: list[Any]
    created_at: datetime
    expires_at: datetime


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
        evidence=row["evidence"] or [],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
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
) -> MailMemory | None:
    """Insert a memory row, returning None on duplicate or public-domain refusal.

    Parameters
    ----------
    kind:
        One of ``'fact'``, ``'procedure'``, ``'preference'``.
    scope:
        One of ``'sender'``, ``'domain'``, ``'global'``.
    scope_value:
        Normalized to lowercase + stripped.
    content:
        Normalized: internal whitespace collapsed.
    evidence:
        Optional list of evidence dicts (stored as JSONB).

    Returns
    -------
    MailMemory | None
        The inserted row, or None if refused (public domain) or duplicate.
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
        "(kind, scope, scope_value, content, evidence) "
        "VALUES (%s, %s, %s, %s, %s::jsonb) "
        "ON CONFLICT (kind, scope, scope_value, content) DO NOTHING "
        "RETURNING *"
    )
    params = [kind, scope, scope_value, content, json.dumps(evidence)]

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


__all__ = [
    "PUBLIC_EMAIL_DOMAINS",
    "MailMemory",
    "candidate_pool",
    "get_many",
    "insert",
    "list_recent",
    "purge_expired",
]
