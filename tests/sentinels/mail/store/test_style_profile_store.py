"""Store CRUD for mail_sentinel_style_profile."""

from __future__ import annotations

import pytest

from twaky.sentinels.mail.store import style_profile as sp

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_style_profile")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_style_profile")


def test_get_returns_none_when_absent():
    assert sp.get("nobody@example.com") is None


def test_upsert_inserts_and_normalizes_email():
    row = sp.upsert(
        owner_email="  Michel@Example.com  ",
        profile="my-style",
        sent_count_at_compute=100,
        sample_size=50,
        model="openai/Mistral",
    )
    assert row.owner_email == "michel@example.com"
    assert row.profile == "my-style"
    assert row.sent_count_at_compute == 100
    assert row.sample_size == 50
    assert row.model == "openai/Mistral"


def test_upsert_updates_existing_and_refreshes_computed_at():
    r1 = sp.upsert(
        owner_email="x@y.com",
        profile="v1",
        sent_count_at_compute=10,
        sample_size=10,
    )
    import time

    time.sleep(0.01)
    r2 = sp.upsert(
        owner_email="x@y.com",
        profile="v2",
        sent_count_at_compute=60,
        sample_size=50,
    )
    assert r2.profile == "v2"
    assert r2.sent_count_at_compute == 60
    assert r2.computed_at >= r1.computed_at


def test_delete_returns_true_when_present():
    sp.upsert(
        owner_email="x@y.com",
        profile="v1",
        sent_count_at_compute=10,
        sample_size=10,
    )
    assert sp.delete("x@y.com") is True
    assert sp.get("x@y.com") is None


def test_delete_returns_false_when_absent():
    assert sp.delete("ghost@nowhere.com") is False


def test_list_all_orders_by_owner_email():
    sp.upsert(
        owner_email="b@y.com", profile="p", sent_count_at_compute=1, sample_size=1
    )
    sp.upsert(
        owner_email="a@y.com", profile="p", sent_count_at_compute=1, sample_size=1
    )
    rows = sp.list_all()
    assert [r.owner_email for r in rows] == ["a@y.com", "b@y.com"]
