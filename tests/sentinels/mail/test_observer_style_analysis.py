"""Observer's SP7 style-analysis trigger."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from twaky.sentinels.mail.analyze_style import StyleProfileOutput
from twaky.sentinels.mail.observer import MailObserver
from twaky.sentinels.mail.store import mailbox_state as ms
from twaky.sentinels.mail.store import style_profile as sp_store

pytestmark = pytest.mark.integration


class FakeAdapter:
    def __init__(self, *, sent_total: int, sent_samples: list):
        self.sent_total = sent_total
        self.sent_samples = sent_samples

    async def query_mailboxes(self):
        return [{"id": "mbx-sent", "role": "sent", "name": "Sent"}]

    async def get_global_state(self):
        return "state-0"

    async def changes(self, since_state, mailbox_id=None):
        return {
            "newState": "state-1",
            "created": [],
            "updated": [],
            "destroyed": [],
        }

    async def get_email(self, email_id):
        return None

    async def get_mailbox_total(self, mailbox_id):
        return self.sent_total

    async def list_recent_emails(self, mailbox_id, limit=100):
        return self.sent_samples[:limit]


def _cleanup_all():
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_style_profile")
        cur.execute("DELETE FROM mail_sentinel_mailbox_state")


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch):
    from twaky.config import settings
    from twaky.sentinels.mail.observer import _GLOBAL_STATE_KEY

    monkeypatch.setattr(settings, "mail_sentinel_observer_enabled", True)
    _cleanup_all()
    # SP5c: seed the GLOBAL state row so the tick doesn't bootstrap-and-return
    ms.upsert(
        mailbox_id=_GLOBAL_STATE_KEY,
        jmap_state="state-0",
        role=None,
        name=_GLOBAL_STATE_KEY,
    )
    yield
    _cleanup_all()


def _fake_samples(n: int) -> list[dict]:
    return [
        {
            "subject": f"Re: subject {i}",
            "textBody": [{"partId": "1"}],
            "bodyValues": {
                "1": {
                    "value": "Bonjour,\n\nDetailed reply with substantive content "
                    "that comfortably exceeds the 100-char minimum threshold.\n\nBien à vous,\n\nMichel-Marie"
                }
            },
        }
        for i in range(n)
    ]


def test_triggers_analysis_when_no_profile_exists():
    """Bootstrap path: no DB row → analysis runs."""
    import asyncio

    adapter = FakeAdapter(sent_total=200, sent_samples=_fake_samples(10))
    output = StyleProfileOutput(
        profile="Fresh auto-computed profile content — long enough to satisfy the min-length constraint of one hundred characters."
    )
    with patch(
        "twaky.sentinels.mail.analyze_style.structured_call",
        return_value=output,
    ):
        asyncio.run(
            MailObserver().run_tick(adapter, owner_email="mmaudet@linagora.com")
        )

    stored = sp_store.get("mmaudet@linagora.com")
    assert stored is not None
    assert stored.sent_count_at_compute == 200
    assert stored.sample_size == 10


def test_skips_analysis_when_delta_below_threshold():
    import asyncio

    sp_store.upsert(
        owner_email="mmaudet@linagora.com",
        profile="existing",
        sent_count_at_compute=200,
        sample_size=100,
    )
    adapter = FakeAdapter(sent_total=210, sent_samples=_fake_samples(10))
    with patch(
        "twaky.sentinels.mail.analyze_style.structured_call",
    ) as mock_llm:
        asyncio.run(
            MailObserver().run_tick(adapter, owner_email="mmaudet@linagora.com")
        )
    mock_llm.assert_not_called()
    stored = sp_store.get("mmaudet@linagora.com")
    assert stored is not None
    assert stored.profile == "existing"  # unchanged


def test_triggers_analysis_when_delta_at_threshold():
    import asyncio

    sp_store.upsert(
        owner_email="mmaudet@linagora.com",
        profile="existing",
        sent_count_at_compute=200,
        sample_size=100,
    )
    adapter = FakeAdapter(sent_total=250, sent_samples=_fake_samples(10))  # delta = 50
    output = StyleProfileOutput(
        profile="Refreshed profile after delta reached — long enough to satisfy the min-length constraint of one hundred characters."
    )
    with patch(
        "twaky.sentinels.mail.analyze_style.structured_call",
        return_value=output,
    ):
        asyncio.run(
            MailObserver().run_tick(adapter, owner_email="mmaudet@linagora.com")
        )
    stored = sp_store.get("mmaudet@linagora.com")
    assert stored is not None
    assert "Refreshed profile" in stored.profile
    assert stored.sent_count_at_compute == 250


def test_analysis_failure_does_not_break_tick(caplog):
    """LLM failure logged as warning, does not raise from run_tick."""
    import asyncio

    adapter = FakeAdapter(sent_total=200, sent_samples=_fake_samples(10))
    with patch(
        "twaky.sentinels.mail.analyze_style.structured_call",
        side_effect=RuntimeError("llm crashed"),
    ):
        result = asyncio.run(
            MailObserver().run_tick(adapter, owner_email="mmaudet@linagora.com")
        )
    # LLM failure => run_analysis returns None (caught internally, not raised).
    # Tick completes cleanly and the observer surfaces no error entry.
    assert result.errors == []
    assert sp_store.get("mmaudet@linagora.com") is None
