"""Store CRUD for mail_sentinel_observation."""

from __future__ import annotations

from uuid import uuid4

import pytest

from twaky.sentinels.mail.store import observations as obs
from twaky.sentinels.mail.store.observations import (
    ExtractionOutcome,
    ObservationType,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_observation")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_observation")


def test_insert_if_new_creates_row():
    row = obs.insert_if_new(
        email_id="e1",
        mailbox_id="m1",
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=[uuid4()],
    )
    assert row is not None
    assert row.email_id == "e1"
    assert row.observation_type == ObservationType.DRAFT_SENT
    assert len(row.memory_ids) == 1


def test_insert_if_new_conflict_returns_none():
    obs.insert_if_new(
        email_id="e1",
        mailbox_id="m1",
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
    )
    result = obs.insert_if_new(
        email_id="e1",
        mailbox_id="m1",
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
    )
    assert result is None


def test_list_recent_orders_desc_and_limits():
    for i in range(5):
        obs.insert_if_new(
            email_id=f"e{i}",
            mailbox_id="m1",
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.EXTRACTED,
        )
    rows = obs.list_recent(limit=3)
    assert len(rows) == 3


def test_purge_older_than_removes_only_old_rows():
    from twaky.db import get_pool

    # Insert one with recent observed_at (default now())
    obs.insert_if_new(
        email_id="e_recent",
        mailbox_id="m1",
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
    )
    # Force one to be old
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mail_sentinel_observation "
            "(email_id, mailbox_id, observation_type, observed_at, extraction_outcome) "
            "VALUES (%s, %s, %s, now() - INTERVAL '45 days', %s)",
            ("e_old", "m1", "draft_sent", "extracted"),
        )

    deleted = obs.purge_older_than(30)
    assert deleted == 1
    remaining = obs.list_recent()
    assert [r.email_id for r in remaining] == ["e_recent"]
