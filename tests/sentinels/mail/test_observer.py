"""Observer tick logic — dispatch to correct extractor per change type."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from twaky.sentinels.mail.observer import MailObserver


class FakeAdapter:
    def __init__(self, mailboxes, changes_map, emails):
        self._mailboxes = mailboxes
        self._changes_map = changes_map  # {mailbox_id: (new_state, created_ids)}
        self._emails = emails            # {email_id: email_dict}

    async def query_mailboxes(self):
        return self._mailboxes

    async def get_mailbox_state(self, mailbox_id):
        return self._changes_map.get(mailbox_id, ("state-init", []))[0]

    async def changes(self, mailbox_id, since_state):
        entry = self._changes_map.get(mailbox_id)
        if entry is None:
            return {"newState": "state-init", "created": [], "updated": [], "destroyed": []}
        new_state, created_ids = entry
        return {"newState": new_state, "created": created_ids, "updated": [], "destroyed": []}

    async def get_email(self, email_id):
        return self._emails.get(email_id)


@pytest.fixture(autouse=True)
def _cleanup_mailbox_state():
    """Remove any mbx-sent rows left by previous runs so tests are independent."""
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mail_sentinel_mailbox_state WHERE mailbox_id = 'mbx-sent'"
        )
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mail_sentinel_mailbox_state WHERE mailbox_id = 'mbx-sent'"
        )


@pytest.mark.integration
def test_bootstrap_stores_state_without_replay(monkeypatch):
    import asyncio

    from twaky.config import settings
    monkeypatch.setattr(settings, "mail_sentinel_observer_enabled", True)

    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-sent", "role": "sent", "name": "Sent"}],
        changes_map={"mbx-sent": ("state-1", ["e1"])},
        emails={"e1": {"id": "e1", "from": [{"email": "x@y.com"}]}},
    )
    obs = MailObserver()
    # No prior state → bootstrap should skip replay
    result = asyncio.run(obs.run_tick(adapter, owner_email="mmaudet@linagora.com"))
    assert result.observations_created == 0

    from twaky.sentinels.mail.store import mailbox_state as ms
    assert ms.get("mbx-sent") is not None


@pytest.mark.integration
def test_second_tick_dispatches_draft_sent(monkeypatch):
    import asyncio

    from twaky.config import settings
    from twaky.sentinels.mail.store import mailbox_state as ms

    monkeypatch.setattr(settings, "mail_sentinel_observer_enabled", True)
    ms.upsert(mailbox_id="mbx-sent", jmap_state="state-0", role="sent", name="Sent")

    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-sent", "role": "sent", "name": "Sent"}],
        changes_map={"mbx-sent": ("state-1", ["e1"])},
        emails={
            "e1": {
                "id": "e1",
                "from": [{"email": "recipient@x.com"}],
                "to": [{"email": "recipient@x.com"}],
                "subject": "Re: hello",
                "textBody": [{"partId": "1"}],
                "bodyValues": {"1": {"value": "Bonjour, merci."}},
                "headers": [{"name": "In-Reply-To", "value": "<orig@x>"}],
            }
        },
    )
    with patch("twaky.sentinels.mail.observer.extract_draft_diff") as diff_mock:
        diff_mock.return_value = MagicMock(
            outcome=MagicMock(value="skipped_no_match"),
            memory_ids=[],
            pattern_ids=[],
        )
        result = asyncio.run(
            MailObserver().run_tick(adapter, owner_email="mmaudet@linagora.com")
        )
    diff_mock.assert_called_once()
    assert result.observations_created >= 0  # dispatch happened
