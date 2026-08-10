"""Integration tests for the mail-sentinel LangGraph pipeline.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 to target the dev DB.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels.mail.adapter import InMemoryMailAdapter
from twaky.sentinels.mail.nodes import NodeContext
from twaky.sentinels.mail.pipeline import process_email
from twaky.sentinels.mail.schemas import (
    ChooseRuleOutput,
    DraftReplyOutput,
    LearnPatternOutput,
    SelectMemoriesOutput,
    ThreadStatusOutput,
)
from twaky.sentinels.mail.state import ThreadStatus
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
    """Wipe rules, memories, and patterns tables before/after each test."""
    tables = [
        "mail_sentinel_rule",
        "mail_sentinel_memory",
        "mail_sentinel_learned_pattern",
    ]
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        for table in tables:
            cur.execute(f"DELETE FROM {table}")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        for table in tables:
            cur.execute(f"DELETE FROM {table}")


def _make_email(
    email_id: str,
    subject: str,
    from_addr: str,
    thread_id: str = "t1",
) -> dict[str, Any]:
    return {
        "id": email_id,
        "threadId": thread_id,
        "receivedAt": "2026-01-01T10:00:00Z",
        "from": [{"email": from_addr, "name": from_addr.split("@")[0]}],
        "to": [{"email": "me@x.com", "name": "Me"}],
        "subject": subject,
        "preview": subject,
        "headers": [],
    }


def _ctx(adapter: InMemoryMailAdapter, base: MagicMock | None = None) -> NodeContext:
    b = base if base is not None else MagicMock()
    b.sentinel_row.config_values = {}
    return NodeContext(base=b, mail=adapter, owner_email="me@x.com")


# ---------------------------------------------------------------------------
# Test: static match → archive, no draft
# ---------------------------------------------------------------------------


class TestEndToEndStaticArchiveNoReply:
    """Newsletter email matched by static condition → archived, no draft generated."""

    def test_end_to_end_static_archive_no_reply(self) -> None:
        # Arrange: create a rule that archives newsletters
        rules_store.create(
            name="spam-like",
            conditions=[
                {"field": "subject", "operator": "contains", "value": "newsletter"}
            ],
            actions=["archive"],
        )

        email = _make_email("e1", subject="Weekly newsletter", from_addr="news@x.com")
        adapter = InMemoryMailAdapter(seed={"e1": email})
        base = MagicMock()
        base.sentinel_row.config_values = {}
        ctx = _ctx(adapter, base)

        # Patch thread_status LLM call only (static match skips AI match_rules)
        def _fake_llm(prompt: Any, schema: Any, **kwargs: Any) -> Any:
            if schema is ThreadStatusOutput:
                return ThreadStatusOutput(status=ThreadStatus.FYI)
            raise AssertionError(f"Unexpected structured_call for schema {schema}")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            side_effect=_fake_llm,
        ):
            state = process_email(ctx, "e1")

        # Assert: archived, no draft, action recorded
        assert "e1" in adapter._archived
        assert state.get("draft") is None
        assert "archive" in state["actions_applied"]


# ---------------------------------------------------------------------------
# Test: AI match → draft_reply full path
# ---------------------------------------------------------------------------


class TestEndToEndAiMatchedDraftReply:
    """AI-matched rule with draft_reply action → draft saved, mission emitted."""

    def test_end_to_end_ai_matched_draft_reply(self) -> None:
        # Arrange: create a rule with no static conditions (deferred to AI)
        rules_store.create(
            name="reply-to-all",
            conditions=[],
            actions=["draft_reply"],
        )

        email = _make_email(
            "e1",
            subject="Question sur Q3",
            from_addr="alice@acme.com",
        )
        adapter = InMemoryMailAdapter(seed={"e1": email})
        base = MagicMock()
        base.sentinel_row.config_values = {}
        ctx = _ctx(adapter, base)

        # Per-use-case fake LLM
        def _fake_llm(prompt: Any, schema: Any, **kwargs: Any) -> Any:
            if schema is ChooseRuleOutput:
                return ChooseRuleOutput(rule="reply-to-all", matched_by="ai")
            if schema is LearnPatternOutput:
                return LearnPatternOutput(should_learn=False, confidence=0.5)
            if schema is ThreadStatusOutput:
                return ThreadStatusOutput(status=ThreadStatus.TO_REPLY)
            if schema is SelectMemoriesOutput:
                return SelectMemoriesOutput(memory_ids=[])
            if schema is DraftReplyOutput:
                return DraftReplyOutput(body="Bonjour Alice, oui.", language="fr")
            raise AssertionError(f"Unexpected structured_call for schema {schema}")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            side_effect=_fake_llm,
        ):
            state = process_email(ctx, "e1")

        # Assert draft content
        assert state["draft"] is not None and state["draft"].startswith("Bonjour Alice")
        assert state["draft_language"] == "fr"

        # Assert draft saved to adapter
        assert len(adapter._drafts) == 1
        assert adapter._drafts[0]["body"].startswith("Bonjour Alice")

        # Assert mission emitted
        base.mission_emitter.emit.assert_called()
