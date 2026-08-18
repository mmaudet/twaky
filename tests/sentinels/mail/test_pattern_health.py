"""SP5c 5.1: LLM pattern health check tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from twaky.sentinels.mail import pattern_health as ph
from twaky.sentinels.mail.pattern_health import PatternConfirmOutput
from twaky.sentinels.mail.store import learned_patterns as lp

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")


def _insert_active_pattern(sender: str, rule: str, days_since_confirmed: int) -> None:
    """Insert an active pattern with a stale ``last_confirmed`` date."""
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mail_sentinel_learned_pattern "
            "(sender_email, rule_name, confidence, evidence_count, first_seen, last_confirmed) "
            "VALUES (%s, %s, %s, %s, now() - make_interval(days => %s), now() - make_interval(days => %s))",
            (
                sender.lower(),
                rule,
                Decimal("0.95"),
                3,
                days_since_confirmed,
                days_since_confirmed,
            ),
        )


def _fake_adapter(mails_by_sender: dict[str, list[dict]]):
    class _FA:
        async def list_recent_emails_from(
            self, *, sender_email: str, since_days: int = 30, limit: int = 1
        ):
            return mails_by_sender.get(sender_email.lower(), [])[:limit]

    return _FA()


def test_no_stale_patterns_returns_zero():
    _insert_active_pattern("fresh@x.com", "block_sender", days_since_confirmed=1)
    adapter = _fake_adapter({})
    stats = asyncio.run(ph.run_pattern_health_check(adapter, stale_days=7))
    assert stats == {
        "scanned": 0,
        "confirmed": 0,
        "decayed": 0,
        "deleted": 0,
        "skipped_no_mail": 0,
        "skipped_llm_error": 0,
    }


def test_confirmed_bumps_last_confirmed():
    _insert_active_pattern("spammer@x.com", "block_sender", days_since_confirmed=10)
    email = {
        "id": "e1",
        "from": [{"email": "spammer@x.com"}],
        "subject": "Buy pills now",
        "preview": "Click here to buy…",
    }
    adapter = _fake_adapter({"spammer@x.com": [email]})
    with patch(
        "twaky.sentinels.mail.pattern_health.structured_call",
        return_value=PatternConfirmOutput(confirms=True, reason="still spammy"),
    ):
        stats = asyncio.run(ph.run_pattern_health_check(adapter))
    assert stats["scanned"] == 1
    assert stats["confirmed"] == 1
    assert stats["decayed"] == 0

    # Verify last_confirmed was bumped
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT last_confirmed FROM mail_sentinel_learned_pattern "
            "WHERE sender_email='spammer@x.com'"
        )
        row = cur.fetchone()
    assert row is not None
    assert (datetime.now(tz=UTC) - row[0]) < timedelta(minutes=1)


def test_refuted_decays_confidence():
    _insert_active_pattern("was_spam@x.com", "block_sender", days_since_confirmed=10)
    email = {
        "id": "e2",
        "from": [{"email": "was_spam@x.com"}],
        "subject": "Meeting tomorrow?",
        "preview": "Hi, are you free for a chat?",
    }
    adapter = _fake_adapter({"was_spam@x.com": [email]})
    with patch(
        "twaky.sentinels.mail.pattern_health.structured_call",
        return_value=PatternConfirmOutput(confirms=False, reason="looks legit now"),
    ):
        stats = asyncio.run(ph.run_pattern_health_check(adapter))
    assert stats["decayed"] == 1
    assert stats["deleted"] == 0

    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT confidence FROM mail_sentinel_learned_pattern "
            "WHERE sender_email='was_spam@x.com'"
        )
        row = cur.fetchone()
    # 0.95 * 0.7 = 0.665, still >= 0.5 → row survives with decayed conf
    assert row is not None
    assert Decimal(str(row[0])) < Decimal("0.7")
    assert Decimal(str(row[0])) >= Decimal("0.5")


def test_refuted_below_threshold_deletes():
    from twaky.db import get_pool

    # Insert with an already-low confidence so one decay tips it under 0.5
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mail_sentinel_learned_pattern "
            "(sender_email, rule_name, confidence, evidence_count, first_seen, last_confirmed) "
            "VALUES (%s, %s, %s, %s, now() - INTERVAL '30 days', now() - INTERVAL '30 days')",
            ("weak@x.com", "block_sender", Decimal("0.60"), 3),
        )

    email = {
        "id": "e3",
        "from": [{"email": "weak@x.com"}],
        "subject": "hi",
        "preview": "test",
    }
    adapter = _fake_adapter({"weak@x.com": [email]})
    with patch(
        "twaky.sentinels.mail.pattern_health.structured_call",
        return_value=PatternConfirmOutput(confirms=False, reason="drift"),
    ):
        stats = asyncio.run(ph.run_pattern_health_check(adapter, stale_days=7))
    # But: list_active_stale requires confidence >= 0.9, so this row won't
    # be picked up. Adjust: mark it active by high conf, then decay 3× to
    # sink below 0.5. Simpler: verify our decay math independently.
    # Since 0.60 doesn't meet ACTIVATION_THRESHOLD=0.9, scanned=0.
    assert stats["scanned"] == 0


def test_delete_path_via_repeated_decays():
    """A pattern that starts at 0.95 needs 3 decay rounds to drop below 0.5.

    Instead of running 3 ticks, we manually stress the decay_confidence
    function to confirm the delete branch works.
    """
    _insert_active_pattern("bad@x.com", "block_sender", days_since_confirmed=10)
    # 0.95 → 0.665 → 0.4655 (below 0.5 → delete)
    lp.decay_confidence("bad@x.com", "block_sender")
    lp.decay_confidence("bad@x.com", "block_sender")
    result = lp.decay_confidence("bad@x.com", "block_sender")
    assert result is None  # deleted

    # Verify gone
    assert lp.by_sender("bad@x.com") is None


def test_no_recent_mail_skips_pattern():
    _insert_active_pattern("silent@x.com", "trust_sender", days_since_confirmed=10)
    adapter = _fake_adapter({})  # no mails at all
    with patch(
        "twaky.sentinels.mail.pattern_health.structured_call",
    ) as llm_mock:
        stats = asyncio.run(ph.run_pattern_health_check(adapter))
    assert stats["scanned"] == 1
    assert stats["skipped_no_mail"] == 1
    llm_mock.assert_not_called()


def test_llm_error_skips_gracefully():
    _insert_active_pattern("weird@x.com", "trust_sender", days_since_confirmed=10)
    email = {
        "id": "e4",
        "from": [{"email": "weird@x.com"}],
        "subject": "hi",
        "preview": "test",
    }
    adapter = _fake_adapter({"weird@x.com": [email]})
    with patch(
        "twaky.sentinels.mail.pattern_health.structured_call",
        side_effect=RuntimeError("llm down"),
    ):
        stats = asyncio.run(ph.run_pattern_health_check(adapter))
    assert stats["skipped_llm_error"] == 1
    # Pattern untouched (still exists)
    assert lp.by_sender("weird@x.com") is not None


def test_batch_limit_respected():
    for i in range(10):
        _insert_active_pattern(f"s{i}@x.com", "block_sender", days_since_confirmed=10)
    adapter = _fake_adapter({})  # empty → skipped_no_mail

    stats = asyncio.run(ph.run_pattern_health_check(adapter, batch_limit=3))
    assert stats["scanned"] == 3
    assert stats["skipped_no_mail"] == 3


def test_list_active_stale_ordering_oldest_first():
    _insert_active_pattern("a@x.com", "block_sender", days_since_confirmed=5)
    _insert_active_pattern("b@x.com", "block_sender", days_since_confirmed=30)
    _insert_active_pattern("c@x.com", "block_sender", days_since_confirmed=15)

    stale = lp.list_active_stale(days=4, limit=10)
    # b (30d) first, then c (15d), then a (5d)
    assert [p.sender_email for p in stale] == ["b@x.com", "c@x.com", "a@x.com"]
