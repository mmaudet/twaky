"""Integration tests for the select_memories pipeline node.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 to target the dev DB.

SP5b: node now uses list_for_prompt (ranked SQL) + touch(); LLM step removed.
Returns {"memories": [{"id": str, "content": str}, ...]} instead of {"memory_ids": [...]}.
"""

from __future__ import annotations

import os
from datetime import UTC
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels.mail.nodes import NodeContext, make_select_memories
from twaky.sentinels.mail.store import memories as mem_store

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
    """Wipe mail_sentinel_memory before/after each test."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")


def _ctx(
    max_inject: int = 16,
) -> NodeContext:
    """Return a NodeContext with mocked base config."""
    base = MagicMock()
    base.sentinel_row.config_values = {
        "memory_inject_max": max_inject,
    }
    mail = MagicMock()
    return NodeContext(base=base, mail=mail, owner_email="me@x.com")


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


class TestSelectMemories:
    def test_empty_pool_yields_empty_list(self) -> None:
        """When no memories exist in DB, node returns {"memories": []}.

        Scenario:
        - DB is wiped (empty)
        - Expected: node returns {"memories": []}, no DB/LLM calls
        """
        node = make_select_memories(_ctx())
        state = {
            "thread": _mk_thread(),
            "email_id": "e1",
        }

        result = node(state)  # type: ignore[arg-type]

        assert result == {"memories": []}

    def test_returns_memories_with_id_and_content(self) -> None:
        """When memories exist, node returns list of {id, content} dicts.

        Scenario:
        - Insert 2 memories: one sender-scoped, one global
        - Expected: node returns {"memories": [{id: ..., content: ...}, ...]}
        """
        sender = "alice@acme.com"
        m1 = mem_store.insert(
            kind="fact",
            scope="sender",
            scope_value=sender,
            content="Alice prefers Friday meetings",
            confidence=0.9,
        )
        m2 = mem_store.insert(
            kind="preference",
            scope="global",
            scope_value="global",
            content="Be concise",
            confidence=0.8,
        )

        assert m1 is not None
        assert m2 is not None

        node = make_select_memories(_ctx())
        state = {
            "thread": _mk_thread(sender=sender),
            "email_id": "e1",
        }

        result = node(state)  # type: ignore[arg-type]

        assert "memories" in result
        memories = result["memories"]
        ids = {m["id"] for m in memories}
        assert str(m1.id) in ids
        assert str(m2.id) in ids
        # Each memory has exactly id and content
        for m in memories:
            assert "id" in m
            assert "content" in m

    def test_bounded_by_memory_inject_max(self) -> None:
        """When more memories exist than max_inject, result is truncated.

        Scenario:
        - Insert 5 memories for sender
        - Config has memory_inject_max=3
        - Expected: node returns exactly 3 memories
        """
        sender = "alice@acme.com"
        for i in range(5):
            mem_store.insert(
                kind="fact",
                scope="sender",
                scope_value=sender,
                content=f"Memory {i}",
                confidence=float(i + 1) / 10,
            )

        node = make_select_memories(_ctx(max_inject=3))
        state = {
            "thread": _mk_thread(sender=sender),
            "email_id": "e1",
        }

        result = node(state)  # type: ignore[arg-type]

        assert len(result["memories"]) == 3

    def test_empty_thread_yields_empty_list(self) -> None:
        """When thread is empty, node returns {"memories": []}.

        Scenario:
        - state["thread"] is empty list
        - Expected: node returns {"memories": []}, no DB calls
        """
        node = make_select_memories(_ctx())
        state = {
            "thread": [],
            "email_id": "e1",
        }

        result = node(state)  # type: ignore[arg-type]

        assert result == {"memories": []}

    def test_missing_thread_key_defaults_to_empty(self) -> None:
        """When thread key is missing, defaults to empty list.

        Scenario:
        - state does not have "thread" key
        - Expected: node treats as empty thread
        """
        node = make_select_memories(_ctx())
        state = {"email_id": "e1"}

        result = node(state)  # type: ignore[arg-type]

        assert result == {"memories": []}

    def test_none_thread_value_defaults_to_empty(self) -> None:
        """When thread value is None, defaults to empty list."""
        node = make_select_memories(_ctx())
        state = {
            "thread": None,
            "email_id": "e1",
        }

        result = node(state)  # type: ignore[arg-type]

        assert result == {"memories": []}

    def test_malformed_sender_email_gracefully_returns_empty(self) -> None:
        """When sender email cannot be extracted, node returns empty gracefully."""
        node = make_select_memories(_ctx())
        state = {
            "thread": [
                {
                    "id": "e1",
                    "subject": "test",
                    "preview": "Test",
                    # No 'from' field
                }
            ],
            "email_id": "e1",
        }

        result = node(state)  # type: ignore[arg-type]

        assert result == {"memories": []}

    def test_uses_sender_domain_and_global_scope(self) -> None:
        """Verifies that ranked result includes sender, domain, and global scoped memories.

        Scenario:
        - Insert sender-scoped memory (alice@acme.com)
        - Insert domain-scoped memory (acme.com)
        - Insert global-scoped memory
        - Expected: all 3 appear in the ranked result
        """
        sender = "alice@acme.com"
        domain = "acme.com"

        m_sender = mem_store.insert(
            kind="fact",
            scope="sender",
            scope_value=sender,
            content="Alice-specific",
            confidence=0.9,
        )
        m_domain = mem_store.insert(
            kind="fact",
            scope="domain",
            scope_value=domain,
            content="Acme-wide",
            confidence=0.8,
        )
        m_global = mem_store.insert(
            kind="preference",
            scope="global",
            scope_value="global",
            content="Universal rule",
            confidence=0.7,
        )

        assert all([m_sender, m_domain, m_global])

        node = make_select_memories(_ctx())
        state = {
            "thread": _mk_thread(sender=sender),
            "email_id": "e1",
        }

        result = node(state)  # type: ignore[arg-type]

        assert "memories" in result
        ids = {m["id"] for m in result["memories"]}
        assert str(m_sender.id) in ids
        assert str(m_domain.id) in ids
        assert str(m_global.id) in ids

    def test_touch_extends_ttl(self) -> None:
        """Returned memories have their TTL extended by touch().

        Scenario:
        - Insert a memory with expires_at = now() + 1 day
        - Run select_memories
        - Check expires_at is now roughly now() + 7 days
        """
        from datetime import datetime

        import psycopg

        sender = "alice@acme.com"
        m = mem_store.insert(
            kind="fact",
            scope="sender",
            scope_value=sender,
            content="Touch test",
            confidence=0.9,
        )
        assert m is not None

        # Set a short TTL so we can verify touch extends it
        with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE mail_sentinel_memory SET expires_at = now() + INTERVAL '1 day' WHERE id = %s",
                (str(m.id),),
            )

        node = make_select_memories(_ctx())
        state = {
            "thread": _mk_thread(sender=sender),
            "email_id": "e1",
        }
        out = node(state)  # type: ignore[arg-type]

        assert any(mem["id"] == str(m.id) for mem in out["memories"])

        with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT expires_at FROM mail_sentinel_memory WHERE id = %s", (str(m.id),))
            row = cur.fetchone()

        assert row is not None
        delta = row[0] - datetime.now(UTC)
        assert delta.days >= 6
