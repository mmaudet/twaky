"""Unit tests for SP6d T1 D3: provenance column detection and persistence.

These tests use mock database connections — no live Postgres required.
The module-level ``_HAS_PROVENANCE_COLUMNS`` cache is reset before each test
via an autouse fixture to ensure test isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from twaky.sentinels.mail.store import spam_decisions as store
from twaky.sentinels.mail.store.spam_decisions import _reset_column_cache_for_tests

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 8, 12, 10, 0, 0, tzinfo=UTC)

_BASE_INSERT_KWARGS: dict[str, Any] = {
    "email_id": "test-email-001",
    "thread_id": "thread-001",
    "sender_email": "spammer@evil.com",
    "subject": "Buy crypto now",
    "received_at": _NOW,
    "bucket": "spam",
    "signal_source": "rspamd_junk_keyword",
    "score": 0.95,
    "reason": "junk keyword",
}


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Reset the module-level provenance column cache before every test."""
    _reset_column_cache_for_tests()


def _make_pool_mock(columns_exist: bool) -> MagicMock:
    """Build a pool mock where the information_schema check returns *columns_exist*.

    The mock records SQL + params passed to cursor.execute for assertions.
    RETURNING id returns a fake UUID row.
    """
    fake_uuid = UUID("12345678-0000-0000-0000-000000000001")

    executed_sqls: list[str] = []
    executed_params: list[tuple[Any, ...]] = []

    def _execute(sql: str, params: tuple[Any, ...] | None = None) -> None:
        executed_sqls.append(sql)
        executed_params.append(params or ())

    def _fetchone() -> tuple[Any, ...] | None:
        # First fetchone: information_schema check
        if len(executed_sqls) == 1:
            # Return a row (column found) or None (column absent)
            return ("envelope_headers",) if columns_exist else None
        # Second fetchone: RETURNING id from INSERT
        return (fake_uuid,)

    cursor = MagicMock()
    cursor.execute.side_effect = _execute
    cursor.fetchone.side_effect = _fetchone
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)

    pool = MagicMock()
    pool.connection.return_value = conn

    # Attach inspection helpers so tests can read captured SQL.
    pool._executed_sqls = executed_sqls
    pool._executed_params = executed_params

    return pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProvenancePersistence:
    def test_record_spam_decision_persists_provenance_when_columns_exist(self) -> None:
        """When provenance columns exist, INSERT includes all three new columns."""
        pool = _make_pool_mock(columns_exist=True)

        with patch(
            "twaky.sentinels.mail.store.spam_decisions.get_pool", return_value=pool
        ):
            store.insert(
                **_BASE_INSERT_KWARGS,
                origin_mailbox_id="inbox-uuid",
                origin_mailbox_role="inbox",
                envelope_headers={"from": "spammer@evil.com", "subject": "Buy crypto"},
            )

        # Two execute calls: info_schema + INSERT
        sqls = pool._executed_sqls
        assert len(sqls) == 2, f"expected 2 executes, got {len(sqls)}: {sqls}"

        insert_sql = sqls[1]
        assert "origin_mailbox_id" in insert_sql
        assert "origin_mailbox_role" in insert_sql
        assert "envelope_headers" in insert_sql

        insert_params = pool._executed_params[1]
        assert "inbox-uuid" in insert_params
        assert "inbox" in insert_params
        # envelope_headers should be JSON-serialised
        assert any("from" in str(p) for p in insert_params if p is not None)

    def test_record_spam_decision_falls_back_when_columns_missing(self) -> None:
        """When provenance columns are absent, INSERT uses legacy shape (9 columns)."""
        pool = _make_pool_mock(columns_exist=False)

        with patch(
            "twaky.sentinels.mail.store.spam_decisions.get_pool", return_value=pool
        ):
            store.insert(
                **_BASE_INSERT_KWARGS,
                origin_mailbox_id="inbox-uuid",
                origin_mailbox_role="inbox",
                envelope_headers={"from": "spammer@evil.com"},
            )

        sqls = pool._executed_sqls
        assert len(sqls) == 2, f"expected 2 executes, got {len(sqls)}: {sqls}"

        insert_sql = sqls[1]
        assert "origin_mailbox_id" not in insert_sql
        assert "origin_mailbox_role" not in insert_sql
        assert "envelope_headers" not in insert_sql

    def test_column_cache_not_refetched_on_second_call(self) -> None:
        """_HAS_PROVENANCE_COLUMNS is cached — information_schema queried only once."""
        pool = _make_pool_mock(columns_exist=True)

        with patch(
            "twaky.sentinels.mail.store.spam_decisions.get_pool", return_value=pool
        ):
            store.insert(**_BASE_INSERT_KWARGS)
            # Reset the mock's side_effect call count manually to track NEW calls.
            initial_call_count = pool.connection.call_count
            store.insert(**_BASE_INSERT_KWARGS)
            # Pool should have been used again (for the INSERT) but no info_schema re-check.
            second_call_count = pool.connection.call_count
            # Each insert after cache is set: only 1 DB call (INSERT, no schema check).
            assert second_call_count > initial_call_count


class TestListRecentWithMissingColumns:
    def test_list_recent_returns_none_when_columns_missing(self) -> None:
        """When provenance columns are absent in the row, SpamDecision fields are None."""
        # Simulate a legacy row shape returned by cursor.fetchall — no provenance keys.
        from datetime import UTC, datetime
        from uuid import uuid4

        legacy_row: dict[str, Any] = {
            "id": uuid4(),
            "email_id": "e-legacy",
            "thread_id": None,
            "sender_email": "old@example.com",
            "subject": "Old spam",
            "received_at": datetime(2026, 1, 1, tzinfo=UTC),
            "bucket": "spam",
            "signal_source": "rspamd_junk_keyword",
            "score": None,
            "reason": "test",
            "restored_at": None,
            "restored_by": None,
            "decided_at": datetime(2026, 1, 1, tzinfo=UTC),
            # No origin_mailbox_id, origin_mailbox_role, envelope_headers keys.
        }

        cursor = MagicMock()
        cursor.fetchall.return_value = [legacy_row]
        cursor.__enter__ = lambda s: s
        cursor.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.cursor.return_value = cursor
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)

        pool = MagicMock()
        pool.connection.return_value = conn

        with patch(
            "twaky.sentinels.mail.store.spam_decisions.get_pool", return_value=pool
        ):
            results = store.list_recent()

        assert len(results) == 1
        row = results[0]
        assert row.email_id == "e-legacy"
        assert row.origin_mailbox_id is None
        assert row.origin_mailbox_role is None
        assert row.envelope_headers is None
