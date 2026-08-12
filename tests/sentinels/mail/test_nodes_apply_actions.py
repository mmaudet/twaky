"""Integration tests for the apply_actions pipeline node.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 to target the dev DB.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels.mail.adapter import InMemoryMailAdapter
from twaky.sentinels.mail.nodes import NodeContext, make_apply_actions
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
    """Wipe mail_sentinel_rule before/after each test."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_rule")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_rule")


def _ctx(
    mail: InMemoryMailAdapter | None = None,
    emitter: MagicMock | None = None,
    delegation: MagicMock | None = None,
) -> NodeContext:
    """Build a NodeContext with a MagicMock base and optional overrides."""
    base = MagicMock()
    base.mission_emitter = emitter if emitter is not None else MagicMock()
    base.delegation = delegation if delegation is not None else MagicMock()
    return NodeContext(
        base=base,
        mail=mail if mail is not None else InMemoryMailAdapter(),
        owner_email="me@x.com",
    )


def _thread(email_id: str = "e1", subject: str = "Test") -> list[dict[str, Any]]:
    """Return a 1-email thread with minimal required fields."""
    return [
        {
            "id": email_id,
            "threadId": "t1",
            "receivedAt": "2026-01-01T10:00:00Z",
            "from": [{"email": "alice@acme.com", "name": "Alice"}],
            "to": [{"email": "me@x.com", "name": "Me"}],
            "subject": subject,
            "preview": "Hello",
            "headers": [],
        }
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApplyActions:
    def test_archive_and_mark_read_and_label(self) -> None:
        """archive, mark_read, and label:<name> all trigger adapter side-effects."""
        rules_store.create(
            name="multi-action",
            actions=["archive", "mark_read", "label:x"],
            conditions=[],
        )
        adapter = InMemoryMailAdapter()
        ctx = _ctx(mail=adapter)
        node = make_apply_actions(ctx)

        result = node(
            {"email_id": "e1", "rule_name": "multi-action", "thread": _thread("e1")}
        )  # type: ignore[arg-type]

        assert result["actions_applied"] == ["archive", "mark_read", "label:x"]
        assert "e1" in adapter._archived
        assert "e1" in adapter._read
        assert adapter._labels.get("e1") == ["x"]

    def test_notify_calls_emitter(self) -> None:
        """notify action calls mission_emitter.emit once."""
        rules_store.create(
            name="notify-rule",
            actions=["notify"],
            conditions=[],
        )
        emitter = MagicMock()
        ctx = _ctx(emitter=emitter)
        node = make_apply_actions(ctx)

        result = node(
            {
                "email_id": "e1",
                "rule_name": "notify-rule",
                "thread": _thread("e1", subject="Hello"),
            }
        )  # type: ignore[arg-type]

        assert result["actions_applied"] == ["notify"]
        emitter.emit.assert_called_once()
        call_kwargs = emitter.emit.call_args.kwargs
        assert call_kwargs["intent_text"] == "Mail: Hello"
        assert "notify-rule" in call_kwargs["reason"]

    def test_delegate_calls_delegation(self) -> None:
        """delegate_to_atlas action calls delegation.delegate once with timeout_s=60.0."""
        rules_store.create(
            name="delegate-rule",
            actions=["delegate_to_atlas"],
            conditions=[],
        )
        delegation = MagicMock()
        ctx = _ctx(delegation=delegation)
        node = make_apply_actions(ctx)

        result = node(
            {
                "email_id": "e1",
                "rule_name": "delegate-rule",
                "thread": _thread("e1", subject="Handle me"),
            }
        )  # type: ignore[arg-type]

        assert result["actions_applied"] == ["delegate_to_atlas"]
        delegation.delegate.assert_called_once()
        call_kwargs = delegation.delegate.call_args.kwargs
        assert "Handle me" in call_kwargs["intent_text"]
        assert call_kwargs["timeout_s"] == 60.0

    def test_draft_reply_marker_only(self) -> None:
        """draft_reply is a marker only — no save_draft call, no _drafts entry."""
        rules_store.create(
            name="draft-rule",
            actions=["draft_reply"],
            conditions=[],
        )
        adapter = InMemoryMailAdapter()
        ctx = _ctx(mail=adapter)
        node = make_apply_actions(ctx)

        result = node(
            {"email_id": "e1", "rule_name": "draft-rule", "thread": _thread("e1")}
        )  # type: ignore[arg-type]

        assert result["actions_applied"] == ["draft_reply"]
        assert adapter._drafts == []

    def test_disabled_rule_applies_nothing(self) -> None:
        """A disabled rule returns actions_applied=[] and no adapter side-effects."""
        rule = rules_store.create(
            name="disabled-rule",
            actions=["archive"],
            conditions=[],
        )
        rules_store.update(rule.id, {"enabled": False})

        adapter = InMemoryMailAdapter()
        ctx = _ctx(mail=adapter)
        node = make_apply_actions(ctx)

        result = node(
            {"email_id": "e1", "rule_name": "disabled-rule", "thread": _thread("e1")}
        )  # type: ignore[arg-type]

        assert result["actions_applied"] == []
        assert "e1" not in adapter._archived

    def test_missing_rule_name_returns_empty(self) -> None:
        """rule_name missing → actions_applied=[]."""
        ctx = _ctx()
        node = make_apply_actions(ctx)
        result = node({"email_id": "e1", "rule_name": None, "thread": _thread("e1")})  # type: ignore[arg-type]
        assert result["actions_applied"] == []

    def test_empty_thread_returns_empty(self) -> None:
        """Empty thread → actions_applied=[]."""
        rules_store.create(name="some-rule", actions=["archive"], conditions=[])
        ctx = _ctx()
        node = make_apply_actions(ctx)
        result = node({"email_id": "e1", "rule_name": "some-rule", "thread": []})  # type: ignore[arg-type]
        assert result["actions_applied"] == []

    def test_rule_not_in_store_returns_empty(self) -> None:
        """Rule not found in store → actions_applied=[]."""
        ctx = _ctx()
        node = make_apply_actions(ctx)
        result = node(
            {"email_id": "e1", "rule_name": "nonexistent-rule", "thread": _thread("e1")}
        )  # type: ignore[arg-type]
        assert result["actions_applied"] == []
