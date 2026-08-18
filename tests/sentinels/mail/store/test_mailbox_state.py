"""Store CRUD for mail_sentinel_mailbox_state."""

from __future__ import annotations

import pytest

from twaky.sentinels.mail.store import mailbox_state as ms

pytestmark = pytest.mark.integration  # requires live twaky-pg


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_mailbox_state")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_mailbox_state")


def test_get_returns_none_when_absent():
    assert ms.get("mbx-1") is None


def test_upsert_inserts_new_row():
    row = ms.upsert(mailbox_id="mbx-1", jmap_state="state-A", role="sent", name="Sent")
    assert row.mailbox_id == "mbx-1"
    assert row.jmap_state == "state-A"
    assert row.role == "sent"
    assert row.name == "Sent"


def test_upsert_updates_existing_row():
    ms.upsert(mailbox_id="mbx-1", jmap_state="state-A", role="sent", name="Sent")
    row = ms.upsert(mailbox_id="mbx-1", jmap_state="state-B", role="sent", name="Sent")
    assert row.jmap_state == "state-B"
    got = ms.get("mbx-1")
    assert got is not None
    assert got.jmap_state == "state-B"


def test_list_all_orders_by_mailbox_id():
    ms.upsert(mailbox_id="b-mbx", jmap_state="s1")
    ms.upsert(mailbox_id="a-mbx", jmap_state="s1")
    rows = ms.list_all()
    assert [r.mailbox_id for r in rows] == ["a-mbx", "b-mbx"]
