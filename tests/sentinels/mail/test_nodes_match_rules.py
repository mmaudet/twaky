"""Integration tests for the match_rules pipeline node.

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
from twaky.sentinels.mail.nodes import NodeContext, make_match_rules
from twaky.sentinels.mail.schemas import ChooseRuleOutput
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import rules as rules_store

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
    """Wipe mail_sentinel_rule and mail_sentinel_learned_pattern before/after each test."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
        cur.execute("DELETE FROM mail_sentinel_rule")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
        cur.execute("DELETE FROM mail_sentinel_rule")


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


class TestMatchRulesCascade:
    def test_static_condition_short_circuits_ai(self) -> None:
        """A rule whose static condition matches should return matched_by='static'
        without ever calling structured_call."""
        rules_store.create(
            name="invoice-label",
            conditions=[
                {"field": "subject", "operator": "contains", "value": "invoice"}
            ],
            combinator="OR",
            actions=["archive"],
        )

        node = make_match_rules(_ctx())
        state = {"thread": _mk_thread(subject="Invoice for Q3")}

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result["matched_by"] == "static"
        assert result["rule_name"] == "invoice-label"
        mock_llm.assert_not_called()

    def test_and_combinator_fails_fast(self) -> None:
        """A rule with AND combinator where only one condition matches should
        return matched_by='none' without calling the LLM."""
        rules_store.create(
            name="strict-rule",
            conditions=[
                {"field": "subject", "operator": "contains", "value": "invoice"},
                {"field": "from", "operator": "contains", "value": "@nonexistent.com"},
            ],
            combinator="AND",
            actions=["archive"],
        )

        node = make_match_rules(_ctx())
        # Subject matches but sender does not
        state = {"thread": _mk_thread(sender="alice@acme.com", subject="Invoice Q3")}

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result["matched_by"] == "none"
        assert result["rule_name"] is None
        mock_llm.assert_not_called()

    def test_empty_conditions_delegate_to_ai(self) -> None:
        """A rule with empty conditions should be passed to the AI stage."""
        rules_store.create(
            name="ai-choice",
            conditions=[],
            combinator="OR",
            actions=["archive"],
        )

        ai_output = ChooseRuleOutput(rule="ai-choice", matched_by="ai")
        node = make_match_rules(_ctx())
        state = {"thread": _mk_thread()}

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=ai_output,
        ):
            result = node(state)  # type: ignore[arg-type]

        assert result["matched_by"] == "ai"
        assert result["rule_name"] == "ai-choice"

    def test_learned_pattern_beats_static(self) -> None:
        """An active learned pattern for the sender wins over a matching static rule."""
        # Create a rule that would match via static conditions
        rules_store.create(
            name="static-rule",
            conditions=[
                {"field": "subject", "operator": "contains", "value": "invoice"}
            ],
            combinator="OR",
            actions=["archive"],
        )
        # Create a different rule that the learned pattern points to
        rules_store.create(
            name="pattern-rule",
            conditions=[],
            combinator="OR",
            actions=["archive"],
        )

        # Seed a fully active learned pattern for the sender
        sender = "alice@acme.com"
        for _ in range(3):
            lp_store.record_decision(sender, "pattern-rule", confidence_hint=1.0)

        node = make_match_rules(_ctx())
        state = {"thread": _mk_thread(sender=sender, subject="Invoice for Q3")}

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result["matched_by"] == "learned_pattern"
        assert result["rule_name"] == "pattern-rule"
        mock_llm.assert_not_called()

    def test_thread_continuity_beats_pattern(self) -> None:
        """Thread continuity (prior _matched_rule) wins over a learned pattern."""
        # Create the thread-rule (active, run_on_threads=True)
        rules_store.create(
            name="thread-rule",
            conditions=[],
            combinator="OR",
            actions=["archive"],
            run_on_threads=True,
        )
        # Create a rule that the learned pattern points to
        rules_store.create(
            name="pattern-rule",
            conditions=[],
            combinator="OR",
            actions=["archive"],
        )

        sender = "alice@acme.com"
        # Seed a fully active learned pattern pointing to pattern-rule
        for _ in range(3):
            lp_store.record_decision(sender, "pattern-rule", confidence_hint=1.0)

        # Build a 2-email thread where the first email has _matched_rule set
        prior: dict[str, Any] = {
            "id": "e0",
            "threadId": "t1",
            "receivedAt": "2026-01-01T09:00:00Z",
            "from": [{"email": sender, "name": "Alice"}],
            "to": [{"email": "me@x.com", "name": "Me"}],
            "subject": "Earlier",
            "preview": "...",
            "headers": [],
            "_matched_rule": "thread-rule",
        }
        latest: dict[str, Any] = {
            "id": "e1",
            "threadId": "t1",
            "receivedAt": "2026-01-01T10:00:00Z",
            "from": [{"email": sender, "name": "Alice"}],
            "to": [{"email": "me@x.com", "name": "Me"}],
            "subject": "Reply",
            "preview": "...",
            "headers": [],
        }
        thread = [prior, latest]

        node = make_match_rules(_ctx())
        state = {"thread": thread}

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result["matched_by"] == "thread_continuity"
        assert result["rule_name"] == "thread-rule"
        mock_llm.assert_not_called()
