"""CRUD for ``mail_sentinel_mailbox_state``.

Tracks the last JMAP `state` observed for each mailbox the observer
watches. Enables idempotent delta polling: on bootstrap the current
JMAP state is stored without replay; each subsequent tick queries
`Email/changes sinceState=<stored>` and advances the row on success.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

from twaky.db import get_pool


@dataclass(frozen=True)
class MailboxState:
    mailbox_id: str
    role: str | None
    name: str | None
    jmap_state: str
    updated_at: datetime


def _row(r: dict[str, Any]) -> MailboxState:
    return MailboxState(
        mailbox_id=r["mailbox_id"],
        role=r["role"],
        name=r["name"],
        jmap_state=r["jmap_state"],
        updated_at=r["updated_at"],
    )


def get(mailbox_id: str) -> MailboxState | None:
    sql = "SELECT * FROM mail_sentinel_mailbox_state WHERE mailbox_id = %s"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (mailbox_id,))
        row = cur.fetchone()
    return _row(row) if row else None


def upsert(
    *,
    mailbox_id: str,
    jmap_state: str,
    role: str | None = None,
    name: str | None = None,
) -> MailboxState:
    sql = """
        INSERT INTO mail_sentinel_mailbox_state (mailbox_id, role, name, jmap_state)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (mailbox_id) DO UPDATE SET
            role = COALESCE(EXCLUDED.role, mail_sentinel_mailbox_state.role),
            name = COALESCE(EXCLUDED.name, mail_sentinel_mailbox_state.name),
            jmap_state = EXCLUDED.jmap_state,
            updated_at = now()
        RETURNING *
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (mailbox_id, role, name, jmap_state))
        row = cur.fetchone()
    assert row is not None
    return _row(row)


def list_all() -> list[MailboxState]:
    sql = "SELECT * FROM mail_sentinel_mailbox_state ORDER BY mailbox_id ASC"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        return [_row(r) for r in cur.fetchall()]


__all__ = ["MailboxState", "get", "list_all", "upsert"]
