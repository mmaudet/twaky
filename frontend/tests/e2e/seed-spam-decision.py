"""Seed a mail_sentinel_spam_decision row for E2E testing.

Inserts one spam decision for the Recent Spam tab E2E test.
Prints {"id": ..., "subject": ...} as JSON.

The subject carries a per-seed suffix and the caller locates its row by it:
decisions accumulate in the database, so a spec matching on the fixed sender
address hits every previous seed too and trips Playwright's strict mode from
the second execution onwards.

Usage (from inside the twaky-api container):
    python /tmp/seed-spam-decision.py
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from twaky.sentinels.mail.store import spam_decisions


def seed_spam_decision() -> dict[str, str]:
    tag = uuid.uuid4().hex[:8]
    subject = f"You've won a prize! [e2e-{tag}]"
    decision_id = spam_decisions.insert(
        email_id=f"e2e-spam-seed-{tag}",
        thread_id=None,
        sender_email="spammer@evil.example.com",
        subject=subject,
        received_at=datetime.now(tz=UTC),
        bucket="spam",
        signal_source="rspamd_junk_keyword",
        score=None,
        reason="E2E test seed row",
    )
    return {"id": str(decision_id), "subject": subject}


if __name__ == "__main__":
    print(json.dumps(seed_spam_decision()))
