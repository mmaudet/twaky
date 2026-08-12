"""Seed a mail_sentinel_spam_decision row for E2E testing.

Inserts one spam decision for the Recent Spam tab E2E test.
Prints the decision UUID to stdout.

Usage (from inside the twaky-api container):
    uv run python /tmp/seed-spam-decision.py
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime

from twaky.sentinels.mail.store import spam_decisions


def seed_spam_decision() -> str:
    decision_id = spam_decisions.insert(
        email_id=f"e2e-spam-seed-{uuid.uuid4().hex[:8]}",
        thread_id=None,
        sender_email="spammer@evil.example.com",
        subject="You've won a prize!",
        received_at=datetime.now(tz=UTC),
        bucket="spam",
        signal_source="rspamd_junk_keyword",
        score=None,
        reason="E2E test seed row",
    )
    return str(decision_id)


if __name__ == "__main__":
    print(seed_spam_decision())
