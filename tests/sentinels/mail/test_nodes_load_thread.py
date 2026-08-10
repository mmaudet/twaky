"""Unit tests for mail sentinel pipeline nodes.

Tests for NodeContext and the load_thread node factory.
"""

from __future__ import annotations

from typing import Any

from twaky.sentinels.mail.adapter import InMemoryMailAdapter
from twaky.sentinels.mail.nodes import NodeContext, make_load_thread
from twaky.sentinels.mail.state import MailAgentState

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_email(
    email_id: str,
    thread_id: str | None,
    received_at: str,
) -> dict[str, Any]:
    """Build a minimal email dict."""
    email: dict[str, Any] = {
        "id": email_id,
        "receivedAt": received_at,
        "subject": f"Subject for {email_id}",
    }
    if thread_id is not None:
        email["threadId"] = thread_id
    return email


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadThread:
    def test_loads_thread_from_email_id(self) -> None:
        """Node fetches email, then loads its full thread ordered by receivedAt."""
        # Create 3 emails across 2 threads
        e1 = _make_email("e1", "t1", "2024-01-01T10:00:00Z")
        e2 = _make_email("e2", "t1", "2024-01-01T08:00:00Z")  # earlier in t1
        e3 = _make_email("e3", "t2", "2024-01-01T12:00:00Z")  # in t2

        adapter = InMemoryMailAdapter(seed={"e1": e1, "e2": e2, "e3": e3})
        ctx = NodeContext(
            base=None,  # type: ignore[arg-type]
            mail=adapter,
            owner_email="me@x.com",
        )
        node = make_load_thread(ctx)

        # Call with e1, should get both e1 and e2 (t1 thread) ordered by receivedAt
        state: MailAgentState = {"email_id": "e1"}
        result = node(state)

        assert "thread" in result
        thread = result["thread"]
        assert len(thread) == 2
        assert [e["id"] for e in thread] == ["e2", "e1"]  # ordered by receivedAt

    def test_orphan_email_yields_single_entry_thread(self) -> None:
        """Node returns single-entry thread for emails with no threadId."""
        # Create an email without a threadId
        email = _make_email("orphan", None, "2024-01-01T10:00:00Z")

        adapter = InMemoryMailAdapter(seed={"orphan": email})
        ctx = NodeContext(
            base=None,  # type: ignore[arg-type]
            mail=adapter,
            owner_email="me@x.com",
        )
        node = make_load_thread(ctx)

        state: MailAgentState = {"email_id": "orphan"}
        result = node(state)

        assert "thread" in result
        thread = result["thread"]
        assert len(thread) == 1
        assert thread[0]["id"] == "orphan"
