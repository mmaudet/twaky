"""Reclassification extractor: user (un)marks spam."""

from __future__ import annotations

import pytest

from twaky.sentinels.mail.extractors.reclassification import (
    extract_reclassification,
)
from twaky.sentinels.mail.store.observations import ExtractionOutcome

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
        cur.execute("DELETE FROM mail_sentinel_observation")
        cur.execute("DELETE FROM mail_sentinel_spam_decision")
    yield


def test_unmarked_spam_creates_trust_sender_pattern_and_memory():
    result = extract_reclassification(
        email_id="e1",
        mailbox_id="junk-mbx",
        sender_email="legit@example.com",
        direction="out",
    )
    assert result.outcome == ExtractionOutcome.EXTRACTED
    assert len(result.pattern_ids) == 1
    assert len(result.memory_ids) == 1

    from twaky.sentinels.mail.store import learned_patterns as lp
    from twaky.sentinels.mail.store import memories as mem

    lp.by_sender("legit@example.com")
    # Not yet active (evidence_count=1 < 3), but row exists
    all_pats = lp.list_all()
    assert any(p.rule_name == "trust_sender" for p in all_pats)
    all_mems = mem.list_recent(limit=10)
    assert any(
        m.source == "auto_reclass" and "not classify" in m.content.lower()
        for m in all_mems
    )


def test_marked_spam_creates_block_sender_pattern():
    result = extract_reclassification(
        email_id="e2",
        mailbox_id="junk-mbx",
        sender_email="spammer@bad.com",
        direction="in",
    )
    assert result.outcome == ExtractionOutcome.EXTRACTED
    from twaky.sentinels.mail.store import learned_patterns as lp

    all_pats = lp.list_all()
    assert any(p.rule_name == "block_sender" for p in all_pats)


def test_three_unmark_events_activates_trust_pattern():
    for i in range(3):
        extract_reclassification(
            email_id=f"e{i}",
            mailbox_id="junk-mbx",
            sender_email="legit@example.com",
            direction="out",
        )
    from twaky.sentinels.mail.store import learned_patterns as lp

    active = lp.by_sender("legit@example.com")
    assert active is not None
    assert active.rule_name == "trust_sender"
    assert active.is_active


def test_restores_existing_spam_decision():
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, received_at, bucket, signal_source) "
            "VALUES (%s, %s, now(), %s, %s)",
            ("e1", "legit@example.com", "spam", "rspamd_junk_keyword"),
        )
    extract_reclassification(
        email_id="e1",
        mailbox_id="junk-mbx",
        sender_email="legit@example.com",
        direction="out",
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT restored_at, restored_by FROM mail_sentinel_spam_decision WHERE email_id=%s",
            ("e1",),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is not None
    assert row[1] == "user"


def test_idempotence_via_observation_unique():
    extract_reclassification(
        email_id="e1",
        mailbox_id="junk-mbx",
        sender_email="legit@example.com",
        direction="out",
    )
    extract_reclassification(
        email_id="e1",
        mailbox_id="junk-mbx",
        sender_email="legit@example.com",
        direction="out",
    )
    from twaky.sentinels.mail.store import observations as obs

    rows = obs.list_recent(limit=100)
    # Only ONE observation row for this (email_id, mailbox_id, type)
    assert sum(1 for r in rows if r.email_id == "e1") == 1
