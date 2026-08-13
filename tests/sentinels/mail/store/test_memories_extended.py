"""Extensions to memories store: source, touch, list_for_prompt, set_persist."""

from __future__ import annotations

from datetime import UTC

import pytest

from twaky.sentinels.mail.store import memories as mem

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")


def test_insert_records_new_fields():
    m = mem.insert(
        kind="preference",
        scope="sender",
        scope_value="a@example.com",
        content="Use Bonjour",
        source="auto_diff",
        sender_email="a@example.com",
        confidence=0.9,
    )
    assert m.source == "auto_diff"
    assert m.sender_email == "a@example.com"
    assert m.confidence == pytest.approx(0.9)


def test_insert_default_source_is_manual():
    m = mem.insert(
        kind="fact", scope="global", scope_value="*", content="Always sign Michel-Marie"
    )
    assert m.source == "manual"


def test_touch_extends_expires_at():
    from twaky.db import get_pool
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="foo")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mail_sentinel_memory SET expires_at = now() + INTERVAL '1 day' WHERE id = %s",
            (m.id,),
        )
    updated = mem.touch([m.id])
    assert updated == 1
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT expires_at FROM mail_sentinel_memory WHERE id = %s", (m.id,)
        )
        row = cur.fetchone()
        assert row is not None
    # Assert expiry is > 6 days out
    from datetime import datetime
    delta = row[0] - datetime.now(UTC)
    assert delta.days >= 6


def test_touch_skips_permanent_memories():
    from twaky.db import get_pool
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="perm")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mail_sentinel_memory SET expires_at = NULL WHERE id = %s",
            (m.id,),
        )
    updated = mem.touch([m.id])
    assert updated == 0


def test_list_for_prompt_ranks_sender_over_global():
    mem.insert(
        kind="preference",
        scope="global",
        scope_value="*",
        content="global rule",
        confidence=0.9,
    )
    mem.insert(
        kind="preference",
        scope="sender",
        scope_value="a@example.com",
        content="sender rule",
        confidence=0.9,
        sender_email="a@example.com",
    )
    rows = mem.list_for_prompt(
        sender_email="a@example.com", sender_domain="example.com", limit=16
    )
    assert rows[0].scope == "sender"
    assert rows[1].scope == "global"


def test_list_for_prompt_filters_expired():
    from twaky.db import get_pool
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="expired")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mail_sentinel_memory SET expires_at = now() - INTERVAL '1 day' WHERE id = %s",
            (m.id,),
        )
    rows = mem.list_for_prompt(sender_email="x@y.com", sender_domain="y.com")
    assert all(r.id != m.id for r in rows)


def test_delete_removes_row():
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="x")
    assert mem.delete(m.id) is True
    assert mem.list_recent(limit=10) == [] or all(r.id != m.id for r in mem.list_recent(limit=10))


def test_delete_missing_returns_false():
    from uuid import uuid4
    assert mem.delete(uuid4()) is False


def test_set_persist_true_nulls_expires_at():
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="p")
    updated = mem.set_persist(m.id, True)
    assert updated is not None
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT expires_at FROM mail_sentinel_memory WHERE id = %s", (m.id,)
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is None


def test_set_persist_false_resets_ttl():
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="p")
    mem.set_persist(m.id, True)
    mem.set_persist(m.id, False)
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT expires_at FROM mail_sentinel_memory WHERE id = %s", (m.id,)
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is not None
