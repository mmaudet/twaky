"""API tests for /mail-sentinel/spam endpoints.

Requires a live Postgres instance (TWAKY_PG_HOST=172.27.0.33) because the
spam_decisions store uses the real DB pool.

Auth is cookie-only: _cookie() returns a signed session for "alice@x",
which matches TWAKY_OWNER_EMAIL set in the autouse _env fixture.

Error envelope shape: {"error": {"code": "...", "message": "..."}}
(the SP6 error_response() pattern, registered in errors.py).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session
from twaky.config import settings
from twaky.sentinels.mail.store import spam_decisions

# ---------------------------------------------------------------------------
# DB reachability guard
# ---------------------------------------------------------------------------

_FERNET_KEY = Fernet.generate_key().decode()


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
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("MODEL", "sentinel-default-model")
    monkeypatch.setenv("TWAKY_SECRET_KEY", _FERNET_KEY)
    # Rebuild Settings from updated env and patch every module that holds a reference
    from twaky import config as _cfg
    from twaky.crypto import secrets as _crypto

    cfg = _cfg.Settings(_env_file=None)
    monkeypatch.setattr("twaky.api.deps.settings", cfg)
    monkeypatch.setattr("twaky.crypto.secrets.settings", cfg)
    _crypto._fernet.cache_clear()
    yield
    _crypto._fernet.cache_clear()


@pytest.fixture(autouse=True)
def _wipe():
    """Wipe all mail_sentinel_spam_decision rows before and after each test.

    Guarded by TWAKY_ALLOW_DESTRUCTIVE_TESTS — see
    ``docs/superpowers/investigations/2026-08-12-spam-decision-purge.md``.
    """
    from tests._conftest_helpers import destructive_wipe_allowed, skip_reason

    if not destructive_wipe_allowed():
        pytest.skip(skip_reason())
    _truncate()
    yield
    _truncate()


def _truncate() -> None:
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_spam_decision;")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cookie() -> dict[str, str]:
    """Return a valid owner session cookie dict."""
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _owner_client(monkeypatch) -> TestClient:
    """Return a TestClient wired with the test env settings and owner session."""
    from twaky import config as _cfg

    monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
    return TestClient(app, cookies=_cookie())


def _unauthenticated_client() -> TestClient:
    """Return a TestClient with no session cookie."""
    return TestClient(app)


def _seed_decision(
    *,
    bucket: str = "spam",
    signal_source: str = "rspamd_junk_keyword",
    email_id: str | None = None,
    origin_mailbox_id: str | None = None,
    origin_mailbox_role: str | None = None,
) -> spam_decisions.SpamDecision:
    """Insert a spam decision row and return the full record."""
    if email_id is None:
        email_id = f"Mtest{uuid4().hex}"
    decision_id = spam_decisions.insert(
        email_id=email_id,
        thread_id=None,
        sender_email="spammer@evil.example",
        subject="You won!",
        received_at=datetime.now(UTC) - timedelta(hours=1),
        bucket=bucket,
        signal_source=signal_source,
        score=None,
        reason=None,
        origin_mailbox_id=origin_mailbox_id,
        origin_mailbox_role=origin_mailbox_role,
    )
    row = spam_decisions.get(decision_id)
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# GET /mail-sentinel/spam
# ---------------------------------------------------------------------------


def test_list_401_unauthenticated():
    r = _unauthenticated_client().get("/mail-sentinel/spam")
    assert r.status_code == 401


def test_list_returns_empty_when_no_rows(monkeypatch):
    r = _owner_client(monkeypatch).get("/mail-sentinel/spam")
    assert r.status_code == 200
    assert r.json() == []


def test_list_returns_paginated(monkeypatch):
    """Seed 5 rows; GET limit=3 must return 3 most recent."""
    for _ in range(5):
        _seed_decision()
    r = _owner_client(monkeypatch).get("/mail-sentinel/spam?limit=3")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    # Returned rows should all have the spam bucket
    assert all(row["bucket"] == "spam" for row in body)


def test_list_filters_by_bucket(monkeypatch):
    """Seed 1 spam + 1 newsletter; GET ?bucket=spam must return exactly 1 row."""
    _seed_decision(bucket="spam")
    _seed_decision(bucket="newsletter")
    r = _owner_client(monkeypatch).get("/mail-sentinel/spam?bucket=spam")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["bucket"] == "spam"


# ---------------------------------------------------------------------------
# GET /mail-sentinel/spam — provenance flag
# ---------------------------------------------------------------------------


def test_get_recent_without_provenance_omits_origin_fields(monkeypatch):
    """Without ?with_provenance=1, origin fields are always None even when stored."""
    _seed_decision(
        origin_mailbox_id="mbox-abc123",
        origin_mailbox_role="inbox",
    )
    r = _owner_client(monkeypatch).get("/mail-sentinel/spam")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["origin_mailbox_id"] is None
    assert body[0]["origin_mailbox_role"] is None


def test_get_recent_with_provenance_includes_origin_fields(monkeypatch):
    """With ?with_provenance=1, stored values are returned.

    Skipped when the migration (sql/013_*) hasn't been applied yet — in that
    case the store uses a legacy INSERT without provenance columns, so there
    is nothing to assert.
    """
    from twaky.sentinels.mail.store.spam_decisions import _detect_provenance_columns

    if not _detect_provenance_columns():
        pytest.skip("provenance columns not yet migrated (sql/013_* pending)")

    _seed_decision(
        origin_mailbox_id="mbox-abc123",
        origin_mailbox_role="inbox",
    )
    r = _owner_client(monkeypatch).get("/mail-sentinel/spam?with_provenance=1")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["origin_mailbox_id"] == "mbox-abc123"
    assert body[0]["origin_mailbox_role"] == "inbox"


def test_get_recent_with_provenance_null_when_store_row_null(monkeypatch):
    """With ?with_provenance=1 but store row has None, response has None."""
    _seed_decision()  # no provenance values
    r = _owner_client(monkeypatch).get("/mail-sentinel/spam?with_provenance=1")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["origin_mailbox_id"] is None
    assert body[0]["origin_mailbox_role"] is None


# ---------------------------------------------------------------------------
# POST /mail-sentinel/spam/{decision_id}/restore
# ---------------------------------------------------------------------------


def test_restore_401_unauthenticated():
    fake_id = uuid4()
    r = _unauthenticated_client().post(f"/mail-sentinel/spam/{fake_id}/restore")
    assert r.status_code == 401


def test_restore_404_missing(monkeypatch):
    fake_id = uuid4()
    r = _owner_client(monkeypatch).post(f"/mail-sentinel/spam/{fake_id}/restore")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "spam_decision_not_found"


def test_restore_409_already_restored(monkeypatch):
    """Seed a row, restore it via the store, then POST restore again → 409."""
    row = _seed_decision()
    # Restore via the store directly (simulates a prior restore)
    spam_decisions.restore(row.id, "alice@x")

    # Now the API should see it as already restored
    mock_adapter = MagicMock()
    monkeypatch.setattr(
        "twaky.api.routers.mail_sentinel_spam._get_mail_adapter",
        lambda: mock_adapter,
    )
    r = _owner_client(monkeypatch).post(f"/mail-sentinel/spam/{row.id}/restore")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_restored"


def test_restore_502_when_jmap_fails(monkeypatch):
    """Monkeypatch adapter.set_keywords_bulk to raise → 502 jmap_restore_failed."""
    row = _seed_decision()

    mock_adapter = MagicMock()
    mock_adapter.set_keywords_bulk.side_effect = RuntimeError("JMAP server error")
    monkeypatch.setattr(
        "twaky.api.routers.mail_sentinel_spam._get_mail_adapter",
        lambda: mock_adapter,
    )

    r = _owner_client(monkeypatch).post(f"/mail-sentinel/spam/{row.id}/restore")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "jmap_restore_failed"

    # The error message must be sanitized — no JMAP session URL or hostname leakage
    error_message = r.json()["error"]["message"]
    assert (
        "jmap" not in error_message.lower()
        or "rejected the restore request" in error_message
    )
    # Raw exception string must not bleed through (no JMAP server error string)
    assert "JMAP server error" not in error_message

    # DB must NOT have been touched — restored_at should still be None
    refetch = spam_decisions.get(row.id)
    assert refetch is not None
    assert refetch.restored_at is None


def test_restore_happy_path_updates_and_returns(monkeypatch):
    """Monkeypatch adapter as MagicMock; POST → 200 with restored_at set.

    Verifies the JMAP restore now:
    - Adds the email back to INBOX via ``mailbox_patches`` (fixes the
      match_rules ``archive`` collision described in the fix commit).
    - Clears ``$label-newsletter`` and ``$label-__spam__`` (the actual
      keyword names the sentinel writes via ``adapter.label()`` which
      prefixes ``$label-``) in addition to the legacy unprefixed
      ``__spam__``/``newsletter`` for back-compat.
    """
    row = _seed_decision()

    mock_adapter = MagicMock()
    mock_adapter.set_keywords_bulk.return_value = None
    mock_adapter.resolve_role_mailbox_id.return_value = "inbox-mbox-uuid"
    monkeypatch.setattr(
        "twaky.api.routers.mail_sentinel_spam._get_mail_adapter",
        lambda: mock_adapter,
    )

    r = _owner_client(monkeypatch).post(f"/mail-sentinel/spam/{row.id}/restore")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(row.id)
    assert body["restored_at"] is not None
    assert body["restored_by"] == "alice@x"

    mock_adapter.resolve_role_mailbox_id.assert_called_once_with("inbox")
    mock_adapter.set_keywords_bulk.assert_called_once_with(
        row.email_id,
        {
            "$junk": False,
            "nonjunk": True,
            "$label-__spam__": False,
            "$label-newsletter": False,
            "__spam__": False,
            "newsletter": False,
        },
        mailbox_patches={"inbox-mbox-uuid": True},
    )


# ---------------------------------------------------------------------------
# GET /mail-sentinel/spam/stats
# ---------------------------------------------------------------------------


def test_stats_401_unauthenticated():
    r = _unauthenticated_client().get("/mail-sentinel/spam/stats")
    assert r.status_code == 401


def test_stats_returns_aggregation(monkeypatch):
    """Seed 2 spam + 1 newsletter + restore 1 spam → spam:2, newsletter:1,
    phishing_alert:0, restored:1, total_processed:3."""
    row1 = _seed_decision(bucket="spam")
    _seed_decision(bucket="spam")
    _seed_decision(bucket="newsletter")

    # Restore the first spam row via the store
    spam_decisions.restore(row1.id, "alice@x")

    r = _owner_client(monkeypatch).get("/mail-sentinel/spam/stats?days=30")
    assert r.status_code == 200
    body = r.json()
    assert body["spam"] == 2
    assert body["newsletter"] == 1
    assert body["phishing_alert"] == 0
    assert body["restored"] == 1
    assert body["total_processed"] == 3
