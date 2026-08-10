"""Unit tests for twaky.oauth.models — no DB required."""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from twaky.oauth.models import OAuthCredential

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_FIELDS = [
    "id",
    "sentinel_name",
    "provider",
    "client_id",
    "token_endpoint",
    "session_url",
    "scope",
    "refresh_token_enc",
    "access_token_enc",
    "access_token_expires_at",
    "account_email",
    "account_name",
    "last_refresh_at",
    "last_refresh_error",
    "created_at",
    "updated_at",
]


def _make_credential(**overrides) -> OAuthCredential:
    now = datetime.now(tz=UTC)
    base: dict = {
        "id": uuid4(),
        "sentinel_name": "mail",
        "provider": "oidc",
        "client_id": "twaky-mail-sentinel",
        "token_endpoint": "https://auth.example.com/token",
        "session_url": "https://jmap.example.com/session",
        "scope": "openid profile email offline_access",
        "refresh_token_enc": None,
        "access_token_enc": None,
        "access_token_expires_at": None,
        "account_email": None,
        "account_name": None,
        "last_refresh_at": None,
        "last_refresh_error": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return OAuthCredential(**base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dataclass_is_frozen():
    """Assignment to any field must raise FrozenInstanceError."""
    cred = _make_credential()
    with pytest.raises(FrozenInstanceError):
        cred.sentinel_name = "other"  # type: ignore[misc]


def test_field_count_is_16():
    """OAuthCredential must have exactly 16 fields."""
    fields = dataclasses.fields(OAuthCredential)
    assert len(fields) == 16


def test_field_names_match_spec():
    """Field names must match the DB column names exactly, in order."""
    field_names = [f.name for f in dataclasses.fields(OAuthCredential)]
    assert field_names == _EXPECTED_FIELDS


def test_nullable_fields_accept_none():
    """Optional fields must accept None without error."""
    cred = _make_credential(
        refresh_token_enc=None,
        access_token_enc=None,
        access_token_expires_at=None,
        account_email=None,
        account_name=None,
        last_refresh_at=None,
        last_refresh_error=None,
    )
    assert cred.refresh_token_enc is None
    assert cred.access_token_enc is None
    assert cred.access_token_expires_at is None
    assert cred.account_email is None
    assert cred.account_name is None
    assert cred.last_refresh_at is None
    assert cred.last_refresh_error is None


def test_non_null_fields_set_correctly():
    """Non-nullable fields must be stored and accessible."""
    uid = uuid4()
    now = datetime.now(tz=UTC)
    cred = _make_credential(
        id=uid,
        sentinel_name="mail",
        provider="oidc",
        client_id="client-123",
        token_endpoint="https://auth.example.com/token",
        session_url="https://jmap.example.com/session",
        scope="openid email",
        created_at=now,
        updated_at=now,
    )
    assert cred.id == uid
    assert cred.sentinel_name == "mail"
    assert cred.provider == "oidc"
    assert cred.client_id == "client-123"
    assert cred.token_endpoint == "https://auth.example.com/token"
    assert cred.session_url == "https://jmap.example.com/session"
    assert cred.scope == "openid email"
    assert cred.created_at == now
    assert cred.updated_at == now
