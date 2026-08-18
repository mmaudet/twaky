"""Integration: select_memories ranks + touches, match_rules short-circuits patterns."""

from __future__ import annotations

from datetime import UTC
from unittest.mock import MagicMock

import pytest

from twaky.sentinels.mail.nodes import (
    NodeContext,
    make_match_rules,
    make_select_memories,
)
from twaky.sentinels.mail.store import learned_patterns as lp
from twaky.sentinels.mail.store import memories as mem

pytestmark = pytest.mark.integration


def _build_ctx(
    *, owner_email: str = "mmaudet@linagora.com", memory_inject_max: int = 16
) -> NodeContext:
    """Build a NodeContext with mocked base + mail — enough for these unit-level integration tests."""
    base = MagicMock()
    base.sentinel_row.config_values = {"memory_inject_max": memory_inject_max}
    mail = MagicMock()
    return NodeContext(base=base, mail=mail, owner_email=owner_email)


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
    yield


def test_match_rules_short_circuits_on_label_pattern():
    for _ in range(3):
        lp.record_decision(
            sender_email="c@x.com", rule_name="label:Facturation", confidence_hint=0.95
        )
    ctx = _build_ctx()
    state = {
        "email_id": "e1",
        "thread": [{"from": [{"email": "c@x.com"}], "subject": "s", "textBody": "b"}],
    }
    node = make_match_rules(ctx)
    result = node(state)  # type: ignore[arg-type]
    assert result.get("matched_by") == "learned_pattern"
    assert result.get("rule_name") == "label:Facturation"


def test_match_rules_short_circuits_on_trust_sender():
    for _ in range(3):
        lp.record_decision(
            sender_email="legit@x.com", rule_name="trust_sender", confidence_hint=0.95
        )
    ctx = _build_ctx()
    state = {
        "email_id": "e1",
        "thread": [
            {"from": [{"email": "legit@x.com"}], "subject": "s", "textBody": "b"}
        ],
    }
    result = make_match_rules(ctx)(state)  # type: ignore[arg-type]
    assert result.get("matched_by") == "learned_pattern"
    assert result.get("rule_name") == "trust_sender"
    assert result.get("skip_spam_triage") is True


def test_match_rules_short_circuits_on_block_sender():
    for _ in range(3):
        lp.record_decision(
            sender_email="spammer@x.com", rule_name="block_sender", confidence_hint=0.95
        )
    ctx = _build_ctx()
    state = {
        "email_id": "e1",
        "thread": [
            {"from": [{"email": "spammer@x.com"}], "subject": "s", "textBody": "b"}
        ],
    }
    result = make_match_rules(ctx)(state)  # type: ignore[arg-type]
    assert result.get("matched_by") == "learned_pattern"
    assert result.get("rule_name") == "block_sender"
    assert result.get("bucket") == "spam"


def test_select_memories_touches_returned_ids():
    from datetime import datetime

    from twaky.db import get_pool

    m = mem.insert(
        kind="preference",
        scope="sender",
        scope_value="a@x.com",
        content="x",
        source="auto_diff",
        sender_email="a@x.com",
        confidence=0.9,
    )
    assert m is not None
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mail_sentinel_memory SET expires_at = now() + INTERVAL '1 day' WHERE id = %s",
            (m.id,),
        )

    ctx = _build_ctx()
    state = {
        "email_id": "e1",
        "thread": [{"from": [{"email": "a@x.com"}]}],
    }
    node = make_select_memories(ctx)
    out = node(state)  # type: ignore[arg-type]
    assert "memories" in out

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT expires_at FROM mail_sentinel_memory WHERE id=%s", (m.id,))
        row = cur.fetchone()
    assert row is not None
    delta = row[0] - datetime.now(UTC)
    assert delta.days >= 6
