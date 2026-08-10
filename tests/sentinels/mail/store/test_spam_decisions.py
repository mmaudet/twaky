"""Tests for twaky.sentinels.mail.store.spam_decisions.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 env to override default host.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels.mail.store import spam_decisions as store
from twaky.sentinels.mail.store.spam_decisions import (
    AlreadyRestored,
    SpamDecisionNotFound,
)


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
    """Delete all rows from mail_sentinel_spam_decision before and after each test."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_spam_decision")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_spam_decision")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _insert(
    *,
    bucket: str = "spam",
    signal_source: str = "rspamd_junk_keyword",
    email_id: str | None = None,
    received_at: datetime | None = None,
    score: float | None = 0.9,
    reason: str | None = "test",
) -> store.SpamDecision:
    """Insert a decision and return the full SpamDecision via get()."""
    uid = store.insert(
        email_id=email_id or f"email-{uuid4().hex[:8]}",
        thread_id=None,
        sender_email="spammer@example.com",
        subject="Buy now",
        received_at=received_at or _NOW,
        bucket=bucket,
        signal_source=signal_source,
        score=score,
        reason=reason,
    )
    result = store.get(uid)
    assert result is not None
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_insert_returns_uuid():
    """insert() returns a UUID; get(uuid) returns a full SpamDecision."""
    uid = store.insert(
        email_id="msg-abc",
        thread_id="thread-1",
        sender_email="spammer@example.com",
        subject="You won a prize",
        received_at=_NOW,
        bucket="spam",
        signal_source="rspamd_junk_keyword",
        score=0.95,
        reason="keyword match",
    )
    assert uid is not None

    decision = store.get(uid)
    assert decision is not None
    assert decision.id == uid
    assert decision.email_id == "msg-abc"
    assert decision.thread_id == "thread-1"
    assert decision.sender_email == "spammer@example.com"
    assert decision.subject == "You won a prize"
    assert decision.bucket == "spam"
    assert decision.signal_source == "rspamd_junk_keyword"
    assert abs(decision.score - 0.95) < 0.001
    assert decision.reason == "keyword match"
    assert decision.restored_at is None
    assert decision.restored_by is None
    assert decision.decided_at is not None


def test_list_recent_orders_desc_and_limits():
    """list_recent(limit=2) returns the 2 most-recent rows in DESC order."""
    now = datetime.now(tz=UTC)

    uid1 = store.insert(
        email_id="e1",
        thread_id=None,
        sender_email="a@b.com",
        subject="s1",
        received_at=now - timedelta(hours=3),
        bucket="spam",
        signal_source="rspamd_junk_keyword",
        score=None,
        reason=None,
    )
    uid2 = store.insert(
        email_id="e2",
        thread_id=None,
        sender_email="a@b.com",
        subject="s2",
        received_at=now - timedelta(hours=2),
        bucket="spam",
        signal_source="rspamd_junk_keyword",
        score=None,
        reason=None,
    )
    uid3 = store.insert(
        email_id="e3",
        thread_id=None,
        sender_email="a@b.com",
        subject="s3",
        received_at=now - timedelta(hours=1),
        bucket="spam",
        signal_source="rspamd_junk_keyword",
        score=None,
        reason=None,
    )

    results = store.list_recent(limit=2)
    assert len(results) == 2
    # Most recent first — uid3 then uid2
    assert results[0].id == uid3
    assert results[1].id == uid2
    # uid1 excluded by limit
    ids = {r.id for r in results}
    assert uid1 not in ids


def test_list_recent_filters_by_bucket():
    """list_recent(bucket='spam') returns only spam rows."""
    _insert(bucket="spam")
    _insert(bucket="newsletter", signal_source="heuristic_newsletter")

    spam_results = store.list_recent(bucket="spam")
    assert len(spam_results) == 1
    assert spam_results[0].bucket == "spam"

    all_results = store.list_recent()
    assert len(all_results) == 2


def test_list_recent_before_cursor():
    """list_recent(before=middle_time) returns only rows older than middle_time."""
    now = datetime.now(tz=UTC)

    # Insert 3 rows with staggered decided_at via direct SQL
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        t_old = now - timedelta(hours=3)
        t_mid = now - timedelta(hours=2)
        t_new = now - timedelta(hours=1)

        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, subject, received_at, bucket, signal_source, decided_at) "
            "VALUES ('e-old', 'a@b.com', 's', %s, 'spam', 'rspamd_junk_keyword', %s)",
            (now, t_old),
        )
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, subject, received_at, bucket, signal_source, decided_at) "
            "VALUES ('e-mid', 'a@b.com', 's', %s, 'spam', 'rspamd_junk_keyword', %s)",
            (now, t_mid),
        )
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, subject, received_at, bucket, signal_source, decided_at) "
            "VALUES ('e-new', 'a@b.com', 's', %s, 'spam', 'rspamd_junk_keyword', %s)",
            (now, t_new),
        )

    # before=t_mid should return only the old row
    results = store.list_recent(before=t_mid, limit=10)
    assert len(results) == 1
    assert results[0].email_id == "e-old"


def test_restore_success_sets_fields():
    """restore() sets restored_at and restored_by on the decision."""
    decision = _insert()
    assert decision.restored_at is None

    restored = store.restore(decision.id, "me@x.com")

    assert restored.restored_at is not None
    assert restored.restored_by == "me@x.com"
    assert restored.id == decision.id

    # Verify via get() as well
    fetched = store.get(decision.id)
    assert fetched is not None
    assert fetched.restored_at is not None
    assert fetched.restored_by == "me@x.com"


def test_restore_twice_raises_already_restored():
    """Calling restore() a second time raises AlreadyRestored."""
    decision = _insert()
    store.restore(decision.id, "first@x.com")

    with pytest.raises(AlreadyRestored):
        store.restore(decision.id, "second@x.com")


def test_restore_missing_raises_not_found():
    """restore() on a non-existent UUID raises SpamDecisionNotFound."""
    with pytest.raises(SpamDecisionNotFound):
        store.restore(uuid4(), "someone@x.com")


def test_stats_aggregation():
    """stats() returns correct per-bucket and total counts."""
    _insert(bucket="spam")
    _insert(bucket="spam")
    _insert(bucket="newsletter", signal_source="heuristic_newsletter")
    _insert(bucket="phishing-alert", signal_source="rspamd_status_reject")

    # Restore one of the spam rows
    spam_rows = store.list_recent(bucket="spam")
    store.restore(spam_rows[0].id, "admin@x.com")

    result = store.stats(days=30)

    assert result["spam"] == 2
    assert result["newsletter"] == 1
    assert result["phishing_alert"] == 1
    assert result["restored"] == 1
    assert result["total_processed"] == 4


def test_purge_active_only_touches_non_restored():
    """purge_active() deletes old non-restored rows and leaves restored ones."""
    now = datetime.now(tz=UTC)

    # Insert rows and force decided_at to past via UPDATE
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        # 1. Old active row (should be purged)
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, subject, received_at, bucket, signal_source, decided_at) "
            "VALUES ('old-active', 'a@b.com', 's', %s, 'spam', 'rspamd_junk_keyword', "
            "now() - INTERVAL '40 days')",
            (now,),
        )
        # 2. Old restored row (should NOT be purged by purge_active)
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, subject, received_at, bucket, signal_source, "
            "decided_at, restored_at, restored_by) "
            "VALUES ('old-restored', 'a@b.com', 's', %s, 'spam', 'rspamd_junk_keyword', "
            "now() - INTERVAL '40 days', now() - INTERVAL '35 days', 'admin@x.com')",
            (now,),
        )
        # 3. Fresh active row (should NOT be purged)
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, subject, received_at, bucket, signal_source) "
            "VALUES ('fresh', 'a@b.com', 's', %s, 'spam', 'rspamd_junk_keyword')",
            (now,),
        )

    deleted = store.purge_active(older_than_days=30)
    assert deleted == 1

    # Verify remaining rows
    remaining = store.list_recent(limit=100)
    remaining_ids = {r.email_id for r in remaining}
    assert "old-active" not in remaining_ids
    assert "old-restored" in remaining_ids
    assert "fresh" in remaining_ids


def test_purge_restored_only_touches_restored():
    """purge_restored() deletes old restored rows and leaves non-restored ones."""
    now = datetime.now(tz=UTC)

    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        # 1. Old restored row (should be purged)
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, subject, received_at, bucket, signal_source, "
            "decided_at, restored_at, restored_by) "
            "VALUES ('old-restored', 'a@b.com', 's', %s, 'spam', 'rspamd_junk_keyword', "
            "now() - INTERVAL '40 days', now() - INTERVAL '35 days', 'admin@x.com')",
            (now,),
        )
        # 2. Old active row (should NOT be purged by purge_restored)
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, subject, received_at, bucket, signal_source, decided_at) "
            "VALUES ('old-active', 'a@b.com', 's', %s, 'spam', 'rspamd_junk_keyword', "
            "now() - INTERVAL '40 days')",
            (now,),
        )
        # 3. Fresh restored row (should NOT be purged — too recent)
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, subject, received_at, bucket, signal_source, "
            "restored_at, restored_by) "
            "VALUES ('fresh-restored', 'a@b.com', 's', %s, 'spam', 'rspamd_junk_keyword', "
            "now(), 'admin@x.com')",
            (now,),
        )

    deleted = store.purge_restored(older_than_days=30)
    assert deleted == 1

    remaining = store.list_recent(limit=100)
    remaining_ids = {r.email_id for r in remaining}
    assert "old-restored" not in remaining_ids
    assert "old-active" in remaining_ids
    assert "fresh-restored" in remaining_ids
