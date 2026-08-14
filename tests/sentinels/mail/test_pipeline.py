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
    """Wipe rules, memories, patterns, and spam decisions tables before/after each test."""
    tables = [
        "mail_sentinel_rule",
        "mail_sentinel_memory",
        "mail_sentinel_learned_pattern",
        "mail_sentinel_spam_decision",
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


def _ctx(
    adapter: InMemoryMailAdapter,
    base: MagicMock | None = None,
    config_values: dict | None = None,
) -> NodeContext:
    b = base if base is not None else MagicMock()
    b.sentinel_row.config_values = config_values or {}
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
                return DraftReplyOutput(
                    body=(
                        "Bonjour Alice,\n\nOui, ça marche pour Q3. "
                        "On peut se caler jeudi ?\n\nBien à vous,\n\nMichel-Marie"
                    ),
                    language="fr",
                )
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


# ---------------------------------------------------------------------------
# Test: spam_triage routing — spam bucket ends early
# ---------------------------------------------------------------------------


class TestPipelineSpamBucketEndsEarly:
    """spam_triage detects spam → routes to END, no downstream nodes run."""

    def test_pipeline_spam_bucket_ends_early(self) -> None:
        # Arrange: email with $junk keyword (will be caught by spam_triage stage 1)
        email = _make_email("e1", subject="$junk keyword test", from_addr="spam@x.com")
        # Add $junk keyword to trigger spam detection
        email["keywords"] = {"$junk": True}

        adapter = InMemoryMailAdapter(seed={"e1": email})
        ctx = _ctx(adapter, config_values={"spam_filter_enabled": True})

        # Allow thread_status call but it shouldn't happen if routing works
        def _fake_llm(prompt: Any, schema: Any, **kwargs: Any) -> Any:
            if schema is ThreadStatusOutput:
                # If we get here, routing to END didn't work
                raise AssertionError(
                    "thread_status should not be called when spam_bucket routes to END"
                )
            raise AssertionError(f"Unexpected structured_call for schema {schema}")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            side_effect=_fake_llm,
        ):
            state = process_email(ctx, "e1")

        # Assert: spam_bucket is set, status is None (thread_status never ran)
        assert state.get("spam_bucket") == "spam", (
            f"Expected spam_bucket='spam', got {state.get('spam_bucket')}"
        )
        assert state.get("status") is None, (
            f"Expected status=None, got {state.get('status')}"
        )
        # Assert actions_applied was set by spam_triage (labeling, keyword)
        actions = state.get("actions_applied", [])
        assert "label:__spam__" in actions, (
            f"Expected 'label:__spam__' in actions, got {actions}"
        )
        assert "keyword:$junk" in actions, (
            f"Expected 'keyword:$junk' in actions, got {actions}"
        )
        # Assert email was labeled
        assert "e1" in adapter._labels, (
            f"Expected email e1 to be labeled, labels: {adapter._labels}"
        )
        assert "__spam__" in adapter._labels["e1"]
        # Assert draft was not created
        assert state.get("draft") is None
        # SP5c: match_rules now runs BEFORE spam_triage. With no rules
        # configured it returns matched_by="none" (not None). Spam bucket
        # then short-circuits the pipeline to END before apply_actions,
        # so no rule_name is chosen and no draft is produced.
        assert state.get("matched_by") in (None, "none")
        assert state.get("rule_name") is None


# ---------------------------------------------------------------------------
# Test: spam_triage routing — newsletter bucket continues
# ---------------------------------------------------------------------------


class TestPipelineNewsletterBucketContinues:
    """spam_triage detects newsletter → continues to match_rules, downstream runs."""

    def test_pipeline_newsletter_bucket_continues(self) -> None:
        # Arrange: email with newsletter heuristic markers
        email = _make_email("e1", subject="Newsletter", from_addr="news@x.com")
        # Add list-unsubscribe headers to trigger newsletter heuristic
        # Also add DKIM to keep score low (list-unsub=2, dkim present so no +3)
        email["headers"] = [
            {"name": "list-unsubscribe", "value": "<mailto:unsub@x.com>"},
            {"name": "list-unsubscribe-post", "value": "List-Unsubscribe=One-Click"},
            {"name": "dkim-signature", "value": "v=1; a=rsa-sha256; ..."},
        ]

        adapter = InMemoryMailAdapter(seed={"e1": email})
        ctx = _ctx(adapter, config_values={"spam_filter_enabled": True})

        # Per-use-case fake LLM (only thread_status should be called, not match_rules AI)
        def _fake_llm(prompt: Any, schema: Any, **kwargs: Any) -> Any:
            if schema is ThreadStatusOutput:
                return ThreadStatusOutput(status=ThreadStatus.FYI)
            raise AssertionError(f"Unexpected structured_call for schema {schema}")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            side_effect=_fake_llm,
        ):
            state = process_email(ctx, "e1")

        # Assert: spam_bucket is newsletter, status IS set (thread_status ran)
        assert state.get("spam_bucket") == "newsletter"
        assert state.get("status") == ThreadStatus.FYI
        # Assert email was labeled
        assert "e1" in adapter._labels
        assert "newsletter" in adapter._labels["e1"]


# ---------------------------------------------------------------------------
# Test: spam_triage routing — no spam detection, passes through
# ---------------------------------------------------------------------------


class TestPipelineBucketNonePassesThrough:
    """spam_triage sees no spam signals → bucket=None, pipeline continues normally."""

    def test_pipeline_bucket_none_passes_through_unchanged(self) -> None:
        # Arrange: plain legitimate email with no spam markers
        email = _make_email("e1", subject="Normal email", from_addr="alice@acme.com")

        adapter = InMemoryMailAdapter(seed={"e1": email})
        ctx = _ctx(adapter, config_values={"spam_filter_enabled": True})

        # Per-use-case fake LLM
        def _fake_llm(prompt: Any, schema: Any, **kwargs: Any) -> Any:
            if schema is ThreadStatusOutput:
                return ThreadStatusOutput(status=ThreadStatus.FYI)
            raise AssertionError(f"Unexpected structured_call for schema {schema}")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            side_effect=_fake_llm,
        ):
            state = process_email(ctx, "e1")

        # Assert: spam_bucket is None, pipeline continued normally
        assert state.get("spam_bucket") is None
        # Assert status was set by thread_status (pipeline ran)
        assert state.get("status") == ThreadStatus.FYI
        # Assert matched_by was set (match_rules ran, returned "none" since no rules matched)
        assert state.get("matched_by") == "none"
