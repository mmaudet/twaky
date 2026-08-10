"""Tests for twaky.sentinels.mail.store.learned_patterns.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 env to override default host.
"""

from __future__ import annotations

import os
from decimal import Decimal

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels.mail.store import learned_patterns as store
from twaky.sentinels.mail.store.learned_patterns import LearnedPattern


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
    """Delete all rows from mail_sentinel_learned_pattern before and after each test."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_decision_records_evidence_1():
    """First record_decision inserts a row with evidence_count=1 and exact confidence."""
    pattern = store.record_decision("alice@acme.com", "reply", confidence_hint=0.9)
    assert isinstance(pattern, LearnedPattern)
    assert pattern.evidence_count == 1
    assert pattern.confidence == Decimal("0.90")
    assert pattern.sender_email == "alice@acme.com"
    assert pattern.rule_name == "reply"


def test_bump_increments_and_smooths():
    """Second call increments evidence_count and smooths confidence (never drops)."""
    first = store.record_decision("alice@acme.com", "reply", confidence_hint=0.9)
    assert first.evidence_count == 1
    assert first.confidence == Decimal("0.90")

    second = store.record_decision("alice@acme.com", "reply", confidence_hint=0.95)
    assert second.evidence_count == 2
    # Smoothed: GREATEST(0.90, LEAST(1.0, 0.90*0.7 + 0.95*0.3)) = GREATEST(0.90, 0.915) = 0.915
    assert second.confidence >= Decimal("0.90")
    assert second.confidence <= Decimal("1.00")


def test_by_sender_returns_none_until_active():
    """by_sender returns None with < MIN_EVIDENCE confirmations; active after 3rd."""
    # 2 confirmations — not yet active
    store.record_decision("alice@acme.com", "reply", confidence_hint=0.99)
    store.record_decision("alice@acme.com", "reply", confidence_hint=0.99)

    assert store.by_sender("alice@acme.com") is None

    # 3rd confirmation — now active
    store.record_decision("alice@acme.com", "reply", confidence_hint=0.99)

    pattern = store.by_sender("alice@acme.com")
    assert pattern is not None
    assert isinstance(pattern, LearnedPattern)
    assert pattern.is_active is True
    assert pattern.evidence_count >= 3


def test_case_insensitive_sender_lookup():
    """Different casings of the same sender email all bump the same row."""
    store.record_decision("Bob@Acme.com", "archive", confidence_hint=0.95)
    store.record_decision("bob@acme.com", "archive", confidence_hint=0.95)
    store.record_decision("BOB@ACME.COM", "archive", confidence_hint=0.95)

    pattern = store.by_sender("bob@acme.com")
    assert pattern is not None
    assert pattern.evidence_count == 3
    assert pattern.sender_email == "bob@acme.com"


def test_forget_removes():
    """forget() removes the pattern so by_sender returns None afterwards."""
    # Build up to active state
    for _ in range(3):
        store.record_decision("alice@acme.com", "reply", confidence_hint=0.99)

    assert store.by_sender("alice@acme.com") is not None

    store.forget("alice@acme.com", "reply")

    assert store.by_sender("alice@acme.com") is None


def test_list_active_only():
    """list_all(active_only=True) includes active senders and excludes inactive ones."""
    # a@x.com: 3 confirmations → active
    for _ in range(3):
        store.record_decision("a@x.com", "reply", confidence_hint=0.99)

    # b@x.com: 1 confirmation → NOT active (evidence < MIN_EVIDENCE)
    store.record_decision("b@x.com", "reply", confidence_hint=0.99)

    active = store.list_all(active_only=True)
    senders = [p.sender_email for p in active]

    assert "a@x.com" in senders
    assert "b@x.com" not in senders
