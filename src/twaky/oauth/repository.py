"""psycopg3 CRUD for the ``oauth_credential`` table.

Follows the idiom established in src/twaky/sentinels/repository.py:
  - dict_row factory for all SELECT queries
  - ``with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:``
  - RETURNING * on all writes
  - allowlist of writable fields; ValueError on unknown keys / empty patch
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

from twaky.db import get_pool
from twaky.oauth.models import OAuthCredential


class OAuthCredentialNotFound(Exception):
    """Raised when an oauth_credential row identified by sentinel_name does not exist."""


# ---------------------------------------------------------------------------
# Row → dataclass helper
# ---------------------------------------------------------------------------


def _row_to_credential(row: dict[str, Any]) -> OAuthCredential:
    return OAuthCredential(
        id=row["id"],
        sentinel_name=row["sentinel_name"],
        provider=row["provider"],
        client_id=row["client_id"],
        token_endpoint=row["token_endpoint"],
        session_url=row["session_url"],
        scope=row["scope"],
        refresh_token_enc=row["refresh_token_enc"],
        access_token_enc=row["access_token_enc"],
        access_token_expires_at=row["access_token_expires_at"],
        account_email=row["account_email"],
        account_name=row["account_name"],
        last_refresh_at=row["last_refresh_at"],
        last_refresh_error=row["last_refresh_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# CRUD functions
# ---------------------------------------------------------------------------


def get(sentinel_name: str) -> OAuthCredential | None:
    """Fetch a single oauth_credential by sentinel_name. Returns None if not found."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM oauth_credential WHERE sentinel_name = %s",
            (sentinel_name,),
        )
        row = cur.fetchone()
    return _row_to_credential(row) if row else None


def upsert(
    *,
    sentinel_name: str,
    provider: str,
    client_id: str,
    token_endpoint: str,
    session_url: str,
    scope: str,
    refresh_token_enc: str | None,
    access_token_enc: str | None,
    access_token_expires_at: datetime | None,
    account_email: str | None,
    account_name: str | None,
) -> OAuthCredential:
    """Insert or update an oauth_credential row.

    On conflict (sentinel_name), updates all writable fields.
    Also sets ``last_refresh_at = now()`` and clears ``last_refresh_error``.
    Returns the fully-populated OAuthCredential.
    """
    sql = """
        INSERT INTO oauth_credential (
            sentinel_name, provider, client_id, token_endpoint, session_url, scope,
            refresh_token_enc, access_token_enc, access_token_expires_at,
            account_email, account_name, last_refresh_at, last_refresh_error
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, now(), NULL
        )
        ON CONFLICT (sentinel_name) DO UPDATE SET
            provider = EXCLUDED.provider,
            client_id = EXCLUDED.client_id,
            token_endpoint = EXCLUDED.token_endpoint,
            session_url = EXCLUDED.session_url,
            scope = EXCLUDED.scope,
            refresh_token_enc = EXCLUDED.refresh_token_enc,
            access_token_enc = EXCLUDED.access_token_enc,
            access_token_expires_at = EXCLUDED.access_token_expires_at,
            account_email = EXCLUDED.account_email,
            account_name = EXCLUDED.account_name,
            last_refresh_at = now(),
            last_refresh_error = NULL
        RETURNING *
    """
    params: list[Any] = [
        sentinel_name,
        provider,
        client_id,
        token_endpoint,
        session_url,
        scope,
        refresh_token_enc,
        access_token_enc,
        access_token_expires_at,
        account_email,
        account_name,
    ]

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:  # pragma: no cover
        raise RuntimeError(
            "INSERT INTO oauth_credential ... RETURNING * yielded no row"
        )
    return _row_to_credential(row)


def update_after_refresh(
    *,
    sentinel_name: str,
    access_token_enc: str,
    access_token_expires_at: datetime | None,
    refresh_token_enc: str | None = None,
) -> OAuthCredential:
    """Update token fields after a successful token refresh.

    When ``refresh_token_enc`` is None, the existing DB value is preserved
    (the column is NOT overwritten).
    Sets ``last_refresh_at = now()`` and clears ``last_refresh_error``.

    Raises
    ------
    OAuthCredentialNotFound
        If no row with the given *sentinel_name* exists.
    """
    if refresh_token_enc is not None:
        sql = (
            "UPDATE oauth_credential "
            "SET access_token_enc = %s, "
            "    access_token_expires_at = %s, "
            "    refresh_token_enc = %s, "
            "    last_refresh_at = now(), "
            "    last_refresh_error = NULL "
            "WHERE sentinel_name = %s "
            "RETURNING *"
        )
        params: list[Any] = [
            access_token_enc,
            access_token_expires_at,
            refresh_token_enc,
            sentinel_name,
        ]
    else:
        sql = (
            "UPDATE oauth_credential "
            "SET access_token_enc = %s, "
            "    access_token_expires_at = %s, "
            "    last_refresh_at = now(), "
            "    last_refresh_error = NULL "
            "WHERE sentinel_name = %s "
            "RETURNING *"
        )
        params = [
            access_token_enc,
            access_token_expires_at,
            sentinel_name,
        ]

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:
        raise OAuthCredentialNotFound(sentinel_name)
    return _row_to_credential(row)


def set_error(sentinel_name: str, error: str) -> None:
    """Update ``last_refresh_error`` only. No other columns are touched."""
    sql = "UPDATE oauth_credential SET last_refresh_error = %s WHERE sentinel_name = %s"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (error, sentinel_name))


def delete(sentinel_name: str) -> None:
    """Delete the oauth_credential row. Silent no-op if the row does not exist."""
    sql = "DELETE FROM oauth_credential WHERE sentinel_name = %s"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (sentinel_name,))


__all__ = [
    "OAuthCredentialNotFound",
    "delete",
    "get",
    "set_error",
    "update_after_refresh",
    "upsert",
]
