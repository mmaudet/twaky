"""SP7 / Task 141 — style_profile.get_style_profile() DB-first resolution."""

from __future__ import annotations

import pytest

from twaky.sentinels.mail.store import style_profile as sp_store
from twaky.sentinels.mail.style_profile import (
    USER_STYLE_MICHEL_MAUDET,
    get_style_profile,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_style_profile")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_style_profile")


def test_static_fallback_when_no_db_row():
    assert get_style_profile("mmaudet@linagora.com") == USER_STYLE_MICHEL_MAUDET


def test_returns_none_when_no_db_row_and_not_in_static():
    assert get_style_profile("random@nowhere.com") is None


def test_db_row_overrides_static():
    sp_store.upsert(
        owner_email="mmaudet@linagora.com",
        profile="AUTO-COMPUTED-STYLE-MARKER — plenty of chars to pass any downstream min-length checks",
        sent_count_at_compute=200,
        sample_size=100,
    )
    result = get_style_profile("mmaudet@linagora.com")
    assert result is not None
    assert "AUTO-COMPUTED-STYLE-MARKER" in result
    assert result != USER_STYLE_MICHEL_MAUDET


def test_db_row_for_unknown_owner_is_returned():
    sp_store.upsert(
        owner_email="new-owner@x.com",
        profile="Custom profile for a user not in the static dict — long enough to satisfy any checks.",
        sent_count_at_compute=100,
        sample_size=100,
    )
    result = get_style_profile("new-owner@x.com")
    assert result is not None
    assert "Custom profile" in result
