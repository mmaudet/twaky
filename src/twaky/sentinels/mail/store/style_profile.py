"""CRUD for ``mail_sentinel_style_profile``.

Per-owner writing-style profile auto-computed from the Sent folder.
Refreshed when a delta of new sent mails exceeds a threshold (see
``analyze_style.py``). The stored profile is the LLM-generated text
that gets injected into ``draft_reply`` prompts, replacing the static
``USER_STYLE_MICHEL_MAUDET`` fallback when present.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

from twaky.db import get_pool


@dataclass(frozen=True)
class StyleProfile:
    owner_email: str
    profile: str
    computed_at: datetime
    sent_count_at_compute: int
    sample_size: int
    model: str | None


def _row(r: dict[str, Any]) -> StyleProfile:
    return StyleProfile(
        owner_email=r["owner_email"],
        profile=r["profile"],
        computed_at=r["computed_at"],
        sent_count_at_compute=r["sent_count_at_compute"],
        sample_size=r["sample_size"],
        model=r["model"],
    )


def get(owner_email: str) -> StyleProfile | None:
    sql = "SELECT * FROM mail_sentinel_style_profile WHERE owner_email = %s"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (owner_email.lower().strip(),))
        row = cur.fetchone()
    return _row(row) if row else None


def upsert(
    *,
    owner_email: str,
    profile: str,
    sent_count_at_compute: int,
    sample_size: int,
    model: str | None = None,
) -> StyleProfile:
    sql = """
        INSERT INTO mail_sentinel_style_profile
            (owner_email, profile, sent_count_at_compute, sample_size, model)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (owner_email) DO UPDATE SET
            profile = EXCLUDED.profile,
            computed_at = now(),
            sent_count_at_compute = EXCLUDED.sent_count_at_compute,
            sample_size = EXCLUDED.sample_size,
            model = EXCLUDED.model
        RETURNING *
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql,
            (
                owner_email.lower().strip(),
                profile,
                sent_count_at_compute,
                sample_size,
                model,
            ),
        )
        row = cur.fetchone()
    assert row is not None
    return _row(row)


def list_all() -> list[StyleProfile]:
    sql = "SELECT * FROM mail_sentinel_style_profile ORDER BY owner_email ASC"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        return [_row(r) for r in cur.fetchall()]


def delete(owner_email: str) -> bool:
    sql = "DELETE FROM mail_sentinel_style_profile WHERE owner_email = %s"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (owner_email.lower().strip(),))
        return cur.rowcount > 0


__all__ = ["StyleProfile", "delete", "get", "list_all", "upsert"]
