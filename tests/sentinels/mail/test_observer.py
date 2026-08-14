"""Observer tick logic (SP5c): global Email/changes + dispatch by mailboxIds.

SP5c redesign: one global Email/changes call per tick, each email
dispatched based on its CURRENT mailboxIds (not the polled mailbox's
role). Adds `unmarked_spam` detection via open spam_decision lookup.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from twaky.sentinels.mail.observer import _GLOBAL_STATE_KEY, MailObserver


class FakeAdapter:
    """SP5c FakeAdapter: query_mailboxes + get_global_state + changes(since_state) + get_email."""

    def __init__(
        self,
        *,
        mailboxes: list[dict[str, Any]],
        global_state: str = "state-0",
        new_state: str = "state-1",
        created: list[str] | None = None,
        updated: list[str] | None = None,
        emails: dict[str, dict[str, Any]] | None = None,
    ):
        self._mailboxes = mailboxes
        self._global_state = global_state
        self._new_state = new_state
        self._created = created or []
        self._updated = updated or []
        self._emails = emails or {}

    async def query_mailboxes(self):
        return self._mailboxes

    async def get_global_state(self):
        return self._global_state

    async def changes(self, since_state: str, mailbox_id: str | None = None):
        return {
            "newState": self._new_state,
            "created": list(self._created),
            "updated": list(self._updated),
            "destroyed": [],
        }

    async def get_email(self, email_id: str):
        return self._emails.get(email_id)

    # SP7 style-analysis touch-points — safe defaults so the observer's
    # end-of-tick style hook doesn't blow up when a Sent mailbox is
    # present. Tests that specifically care about style analysis should
    # patch `run_analysis` directly.
    async def get_mailbox_total(self, mailbox_id: str) -> int:
        return 0

    async def list_recent_emails(self, mailbox_id: str, limit: int = 100) -> list:
        return []


@pytest.fixture(autouse=True)
def _clean_mailbox_state():
    """Wipe the global state row before + after each test."""
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mail_sentinel_mailbox_state WHERE mailbox_id = %s",
            (_GLOBAL_STATE_KEY,),
        )
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mail_sentinel_mailbox_state WHERE mailbox_id = %s",
            (_GLOBAL_STATE_KEY,),
        )


@pytest.fixture(autouse=True)
def _enable_observer(monkeypatch):
    from twaky.config import settings

    monkeypatch.setattr(settings, "mail_sentinel_observer_enabled", True)


@pytest.mark.integration
def test_bootstrap_stores_global_state_without_replay():
    """First tick with no prior state row = bootstrap, no dispatch."""
    import asyncio

    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-inbox", "role": "inbox", "name": "Inbox"}],
        global_state="bootstrap-state",
        created=["e1"],
        emails={"e1": {"id": "e1", "from": [{"email": "x@y.com"}]}},
    )
    result = asyncio.run(
        MailObserver().run_tick(adapter, owner_email="me@x.com")
    )
    assert result.observations_created == 0

    from twaky.sentinels.mail.store import mailbox_state as ms

    row = ms.get(_GLOBAL_STATE_KEY)
    assert row is not None
    assert row.jmap_state == "bootstrap-state"


@pytest.mark.integration
def test_dispatch_sent_mail_to_draft_diff():
    """An email currently in a mailbox with role=sent → extract_draft_diff."""
    import asyncio

    from twaky.sentinels.mail.store import mailbox_state as ms

    ms.upsert(
        mailbox_id=_GLOBAL_STATE_KEY,
        jmap_state="prior",
        role=None,
        name=_GLOBAL_STATE_KEY,
    )

    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-sent", "role": "sent", "name": "Sent"}],
        created=["e1"],
        emails={
            "e1": {
                "id": "e1",
                "mailboxIds": {"mbx-sent": True},
                "from": [{"email": "me@x.com"}],
                "to": [{"email": "recipient@x.com"}],
                "subject": "Re: hi",
                "textBody": [{"partId": "1"}],
                "bodyValues": {"1": {"value": "reply body"}},
                "headers": [{"name": "In-Reply-To", "value": "<orig@x>"}],
            }
        },
    )
    with patch(
        "twaky.sentinels.mail.observer.extract_draft_diff",
        return_value=None,
    ) as diff_mock:
        result = asyncio.run(
            MailObserver().run_tick(adapter, owner_email="me@x.com")
        )
    diff_mock.assert_called_once()
    # No result tallied because extract returned None; the code path
    # was correctly taken.
    assert result.errors == []


@pytest.mark.integration
def test_dispatch_junk_mail_to_reclassification_marked_spam():
    """An email in a role=junk mailbox → extract_reclassification(direction='in')."""
    import asyncio

    from twaky.sentinels.mail.store import mailbox_state as ms

    ms.upsert(
        mailbox_id=_GLOBAL_STATE_KEY,
        jmap_state="prior",
        role=None,
        name=_GLOBAL_STATE_KEY,
    )

    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-junk", "role": "junk", "name": "Junk"}],
        created=["e-spam"],
        emails={
            "e-spam": {
                "id": "e-spam",
                "mailboxIds": {"mbx-junk": True},
                "from": [{"email": "spammer@x.com"}],
                "subject": "SPAM",
            }
        },
    )
    with patch(
        "twaky.sentinels.mail.observer.extract_reclassification",
        return_value=None,
    ) as reclass_mock:
        asyncio.run(MailObserver().run_tick(adapter, owner_email="me@x.com"))

    reclass_mock.assert_called_once()
    kwargs = reclass_mock.call_args.kwargs
    assert kwargs["direction"] == "in"
    assert kwargs["sender_email"] == "spammer@x.com"


@pytest.mark.integration
def test_dispatch_inbox_with_open_spam_decision_to_unmarked_spam():
    """SP5c Fix B: mail in INBOX + open spam_decision → direction='out'."""
    import asyncio

    from twaky.db import get_pool
    from twaky.sentinels.mail.store import mailbox_state as ms

    # Seed an open spam_decision for e-restored
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_spam_decision")
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, received_at, bucket, signal_source) "
            "VALUES (%s, %s, now(), %s, %s)",
            ("e-restored", "legit@x.com", "spam", "rspamd_junk_keyword"),
        )

    ms.upsert(
        mailbox_id=_GLOBAL_STATE_KEY,
        jmap_state="prior",
        role=None,
        name=_GLOBAL_STATE_KEY,
    )

    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-inbox", "role": "inbox", "name": "Inbox"}],
        updated=["e-restored"],
        emails={
            "e-restored": {
                "id": "e-restored",
                "mailboxIds": {"mbx-inbox": True},  # user moved it back
                "from": [{"email": "legit@x.com"}],
                "subject": "Legit mail wrongly flagged",
            }
        },
    )
    with patch(
        "twaky.sentinels.mail.observer.extract_reclassification",
        return_value=None,
    ) as reclass_mock:
        asyncio.run(MailObserver().run_tick(adapter, owner_email="me@x.com"))

    reclass_mock.assert_called_once()
    kwargs = reclass_mock.call_args.kwargs
    assert kwargs["direction"] == "out"
    assert kwargs["sender_email"] == "legit@x.com"

    # Cleanup
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_spam_decision")


@pytest.mark.integration
def test_dispatch_custom_folder_to_folder_move():
    """A mail in a role=None non-system-name mailbox → extract_folder_move."""
    import asyncio

    from twaky.sentinels.mail.store import mailbox_state as ms

    ms.upsert(
        mailbox_id=_GLOBAL_STATE_KEY,
        jmap_state="prior",
        role=None,
        name=_GLOBAL_STATE_KEY,
    )

    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-fact", "role": None, "name": "Facturation"}],
        created=["e-invoice"],
        emails={
            "e-invoice": {
                "id": "e-invoice",
                "mailboxIds": {"mbx-fact": True},
                "from": [{"email": "comptable@x.com"}],
                "subject": "Facture 2026-01",
            }
        },
    )
    with patch(
        "twaky.sentinels.mail.observer.extract_folder_move",
        return_value=None,
    ) as move_mock:
        asyncio.run(MailObserver().run_tick(adapter, owner_email="me@x.com"))

    move_mock.assert_called_once()
    kwargs = move_mock.call_args.kwargs
    assert kwargs["folder_name"] == "Facturation"
    assert kwargs["sender_email"] == "comptable@x.com"


@pytest.mark.integration
def test_state_upserted_after_dispatch():
    """After a successful tick with changes, the global jmap_state advances."""
    import asyncio

    from twaky.sentinels.mail.store import mailbox_state as ms

    ms.upsert(
        mailbox_id=_GLOBAL_STATE_KEY,
        jmap_state="v-old",
        role=None,
        name=_GLOBAL_STATE_KEY,
    )

    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-inbox", "role": "inbox", "name": "Inbox"}],
        new_state="v-new",
        created=[],
        emails={},
    )
    asyncio.run(MailObserver().run_tick(adapter, owner_email="me@x.com"))

    row = ms.get(_GLOBAL_STATE_KEY)
    assert row is not None
    assert row.jmap_state == "v-new"


@pytest.mark.integration
def test_dispatch_skips_email_when_get_email_returns_none():
    """Missing email data → dispatch silently returns, no crash, no tally."""
    import asyncio

    from twaky.sentinels.mail.store import mailbox_state as ms

    ms.upsert(
        mailbox_id=_GLOBAL_STATE_KEY,
        jmap_state="prior",
        role=None,
        name=_GLOBAL_STATE_KEY,
    )

    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-inbox", "role": "inbox", "name": "Inbox"}],
        created=["ghost"],
        emails={},  # ghost id not present → get_email returns None
    )
    result = asyncio.run(
        MailObserver().run_tick(adapter, owner_email="me@x.com")
    )
    assert result.observations_created == 0
    assert result.errors == []
