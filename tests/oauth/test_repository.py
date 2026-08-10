"""Integration tests for twaky.oauth.repository.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 to reach the dev postgres.

Seed row: 'mail' sentinel inserted by sql/008_init_sentinels.sh.
The _wipe fixture clears oauth_credential before/after each test.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from twaky.config import settings
from twaky.oauth import repository as repo
from twaky.oauth.repository import OAuthCredentialNotFound


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe():
    """Delete all oauth_credential rows before and after each test."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM oauth_credential")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM oauth_credential")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future_exp() -> datetime:
    return datetime.now(tz=UTC) + timedelta(hours=1)


def _upsert_mail(**overrides):
    """Upsert a minimal valid credential for sentinel_name='mail'."""
    kwargs: dict = {
        "sentinel_name": "mail",
        "provider": "oidc",
        "client_id": "twaky-mail-sentinel",
        "token_endpoint": "https://auth.example.com/token",
        "session_url": "https://jmap.example.com/session",
        "scope": "openid profile email offline_access",
        "refresh_token_enc": "R1",
        "access_token_enc": "A1",
        "access_token_expires_at": _future_exp(),
        "account_email": "user@example.com",
        "account_name": "Test User",
    }
    kwargs.update(overrides)
    return repo.upsert(**kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_missing_returns_none():
    assert repo.get("mail") is None


def test_upsert_inserts_new_row():
    cred = _upsert_mail()
    fetched = repo.get("mail")
    assert fetched is not None
    assert fetched.id == cred.id
    assert fetched.sentinel_name == "mail"
    assert fetched.provider == "oidc"
    assert fetched.client_id == "twaky-mail-sentinel"
    assert fetched.token_endpoint == "https://auth.example.com/token"
    assert fetched.session_url == "https://jmap.example.com/session"
    assert fetched.scope == "openid profile email offline_access"
    assert fetched.refresh_token_enc == "R1"
    assert fetched.access_token_enc == "A1"
    assert fetched.account_email == "user@example.com"
    assert fetched.account_name == "Test User"
    assert fetched.last_refresh_error is None
    assert fetched.last_refresh_at is not None


def test_upsert_updates_existing_row():
    _upsert_mail(access_token_enc="A1")
    _upsert_mail(access_token_enc="A2")

    # Only one row in DB
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM oauth_credential WHERE sentinel_name = 'mail'"
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1

    # Second upsert wins
    fetched = repo.get("mail")
    assert fetched is not None
    assert fetched.access_token_enc == "A2"


def test_update_after_refresh_preserves_refresh_when_none():
    _upsert_mail(refresh_token_enc="R1", access_token_enc="A1")

    updated = repo.update_after_refresh(
        sentinel_name="mail",
        access_token_enc="A2",
        access_token_expires_at=_future_exp(),
        refresh_token_enc=None,
    )

    assert updated.refresh_token_enc == "R1"  # preserved
    assert updated.access_token_enc == "A2"  # updated
    assert updated.last_refresh_error is None
    assert updated.last_refresh_at is not None


def test_update_after_refresh_rotates_refresh():
    _upsert_mail(refresh_token_enc="R1", access_token_enc="A1")

    updated = repo.update_after_refresh(
        sentinel_name="mail",
        access_token_enc="A2",
        access_token_expires_at=_future_exp(),
        refresh_token_enc="R2",
    )

    assert updated.refresh_token_enc == "R2"
    assert updated.access_token_enc == "A2"


def test_update_after_refresh_raises_when_missing():
    with pytest.raises(OAuthCredentialNotFound):
        repo.update_after_refresh(
            sentinel_name="mail",
            access_token_enc="A1",
            access_token_expires_at=_future_exp(),
        )


def test_update_after_refresh_clears_error():
    _upsert_mail()
    repo.set_error("mail", "some_error")

    updated = repo.update_after_refresh(
        sentinel_name="mail",
        access_token_enc="A2",
        access_token_expires_at=_future_exp(),
    )

    assert updated.last_refresh_error is None


def test_set_error_only_touches_that_column():
    cred_before = _upsert_mail(refresh_token_enc="R1", access_token_enc="A1")

    repo.set_error("mail", "invalid_grant")

    fetched = repo.get("mail")
    assert fetched is not None
    assert fetched.last_refresh_error == "invalid_grant"
    # Other business columns unchanged
    assert fetched.access_token_enc == "A1"
    assert fetched.refresh_token_enc == "R1"
    assert fetched.account_email == cred_before.account_email
    assert fetched.provider == cred_before.provider
    # updated_at is managed by a BEFORE UPDATE trigger — it WILL change
    # (we do not assert it stays the same)


def test_delete_is_idempotent():
    _upsert_mail()
    repo.delete("mail")
    repo.delete("mail")  # second delete: no error
    assert repo.get("mail") is None
