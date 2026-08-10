"""Integration tests for the learn_pattern pipeline node.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 to target the dev DB.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels.mail.adapter import InMemoryMailAdapter
from twaky.sentinels.mail.nodes import NodeContext, make_learn_pattern
from twaky.sentinels.mail.schemas import LearnPatternOutput
from twaky.sentinels.mail.store import learned_patterns as lp_store

# ---------------------------------------------------------------------------
# Reachability helpers
# ---------------------------------------------------------------------------


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe():
    """Wipe mail_sentinel_learned_pattern before/after each test."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")


def _ctx() -> NodeContext:
    """Return a NodeContext backed by an in-memory mail adapter."""
    return NodeContext(
        base=None,  # type: ignore[arg-type]
        mail=InMemoryMailAdapter(),
        owner_email="me@x.com",
    )


def _mk_thread(
    sender: str = "alice@acme.com",
    subject: str = "hi",
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return a single-email thread with minimal required fields."""
    email: dict[str, Any] = {
        "id": "e1",
        "threadId": "t1",
        "receivedAt": "2026-01-01T10:00:00Z",
        "from": [{"email": sender, "name": "Alice"}],
        "to": [{"email": "me@x.com", "name": "Me"}],
        "subject": subject,
        "preview": "Hello world",
        "headers": [],
    }
    if extra:
        email.update(extra)
    return [email]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLearnPattern:
    def test_no_action_below_3_evidence(self) -> None:
        """When fewer than 3 entries in history, LLM is not called and node returns {}."""
        # No existing patterns seeded, so history will have only 1 entry (current)
        node = make_learn_pattern(_ctx())
        state = {
            "thread": _mk_thread(),
            "rule_name": "inbox-rule",
        }

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {}
        mock_llm.assert_not_called()

    def test_records_pattern_when_confidence_high(self) -> None:
        """When confidence >= ACTIVATION_THRESHOLD and should_learn=True,
        pattern is recorded and returned."""
        sender = "alice@acme.com"
        rule_name = "inbox-rule"

        # Seed 2 existing patterns to prime history
        lp_store.record_decision(sender, rule_name, confidence_hint=0.8)
        lp_store.record_decision(sender, rule_name, confidence_hint=0.85)

        node = make_learn_pattern(_ctx())
        state = {
            "thread": _mk_thread(sender=sender),
            "rule_name": rule_name,
        }

        llm_output = LearnPatternOutput(
            should_learn=True,
            confidence=0.95,
            reasoning="Consistent sender and rule.",
        )

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=llm_output,
        ):
            result = node(state)  # type: ignore[arg-type]

        # Verify the node returns learned_pattern dict
        assert "learned_pattern" in result
        learned = result.get("learned_pattern")
        assert learned is not None
        assert isinstance(learned, dict)
        assert learned["sender"] == sender
        assert learned["rule"] == rule_name
        assert isinstance(learned["confidence"], float)
        assert learned["confidence"] > 0

    def test_no_record_when_confidence_below_threshold(self) -> None:
        """When confidence < ACTIVATION_THRESHOLD, pattern is not recorded
        and node returns {}."""
        sender = "alice@acme.com"
        rule_name = "inbox-rule"

        # Seed 2 existing patterns
        lp_store.record_decision(sender, rule_name, confidence_hint=0.8)
        lp_store.record_decision(sender, rule_name, confidence_hint=0.85)

        node = make_learn_pattern(_ctx())
        state = {
            "thread": _mk_thread(sender=sender),
            "rule_name": rule_name,
        }

        # Confidence is 0.5, which is below ACTIVATION_THRESHOLD (0.90)
        llm_output = LearnPatternOutput(
            should_learn=True,
            confidence=0.5,
            reasoning="Not confident enough.",
        )

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=llm_output,
        ):
            result = node(state)  # type: ignore[arg-type]

        assert result == {}

    def test_no_action_when_rule_name_is_none(self) -> None:
        """When rule_name is None, node returns {} immediately without processing."""
        node = make_learn_pattern(_ctx())
        state = {
            "thread": _mk_thread(),
            "rule_name": None,
        }

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {}
        mock_llm.assert_not_called()

    def test_no_action_when_thread_empty(self) -> None:
        """When thread is empty, node returns {} immediately without processing."""
        node = make_learn_pattern(_ctx())
        state = {
            "thread": [],
            "rule_name": "some-rule",
        }

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {}
        mock_llm.assert_not_called()

    def test_no_action_when_sender_missing(self) -> None:
        """When sender email cannot be extracted, node returns {} gracefully."""
        node = make_learn_pattern(_ctx())
        # Create a malformed email without 'from' field
        state = {
            "thread": [
                {
                    "id": "e1",
                    "subject": "test",
                    "receivedAt": "2026-01-01T10:00:00Z",
                    # No 'from' field
                }
            ],
            "rule_name": "some-rule",
        }

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {}
        mock_llm.assert_not_called()

    def test_should_learn_false_returns_empty(self) -> None:
        """When LLM returns should_learn=False, node returns {} even if
        confidence is high."""
        sender = "alice@acme.com"
        rule_name = "inbox-rule"

        # Seed patterns
        lp_store.record_decision(sender, rule_name, confidence_hint=0.8)
        lp_store.record_decision(sender, rule_name, confidence_hint=0.85)

        node = make_learn_pattern(_ctx())
        state = {
            "thread": _mk_thread(sender=sender),
            "rule_name": rule_name,
        }

        llm_output = LearnPatternOutput(
            should_learn=False,
            confidence=0.95,
            reasoning="Not safe to learn.",
        )

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=llm_output,
        ):
            result = node(state)  # type: ignore[arg-type]

        assert result == {}
