"""Frozen dataclass mirroring the ``oauth_credential`` DB row.

All datetime fields are timezone-aware UTC (TIMESTAMPTZ → Python datetime
with tzinfo set by psycopg3's default row conversion).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class OAuthCredential:
    """Mirror of the ``oauth_credential`` table row (16 columns).

    Matches the schema from sql/009_init_oauth_credential.sh:
        id UUID PK, sentinel_name TEXT FK, provider TEXT, client_id TEXT,
        token_endpoint TEXT, session_url TEXT, scope TEXT,
        refresh_token_enc TEXT?, access_token_enc TEXT?,
        access_token_expires_at TIMESTAMPTZ?, account_email TEXT?,
        account_name TEXT?, last_refresh_at TIMESTAMPTZ?,
        last_refresh_error TEXT?, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ.
    """

    id: UUID
    sentinel_name: str
    provider: str
    client_id: str
    token_endpoint: str
    session_url: str
    scope: str
    refresh_token_enc: str | None
    access_token_enc: str | None
    access_token_expires_at: datetime | None
    account_email: str | None
    account_name: str | None
    last_refresh_at: datetime | None
    last_refresh_error: str | None
    created_at: datetime
    updated_at: datetime


__all__ = ["OAuthCredential"]
