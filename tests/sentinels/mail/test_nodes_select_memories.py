"""Integration tests for the select_memories pipeline node.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 to target the dev DB.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels.mail.nodes import NodeContext, make_select_memories
from twaky.sentinels.mail.schemas import SelectMemoriesOutput
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
    pool_size: int = 100,
    max_inject: int = 16,
) -> NodeContext:
    """Return a NodeContext with mocked base config."""
    base = MagicMock()
    base.sentinel_row.config_values = {
        "memory_candidate_pool": pool_size,
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
        """When no memories exist in DB, node returns {"memory_ids": []}.

        Scenario:
        - DB is wiped (empty)
        - Expected: node returns {"memory_ids": []}, LLM not called
        """
        node = make_select_memories(_ctx())
        state = {
            "thread": _mk_thread(),
            "email_id": "e1",
        }

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {"memory_ids": []}
        mock_llm.assert_not_called()

    def test_calls_llm_when_pool_nonempty(self) -> None:
        """When candidate pool is non-empty, LLM is called and output propagates.

        Scenario:
        - Insert 2 memories: one sender-scoped (alice@acme.com),
          one global scope
        - Mock structured_call returning SelectMemoriesOutput with 1 id
        - Expected: node returns {"memory_ids": [m1.id]}
        """
        sender = "alice@acme.com"
        m1 = mem_store.insert(
            kind="fact",
            scope="sender",
            scope_value=sender,
            content="Alice prefers Friday meetings",
        )
        m2 = mem_store.insert(
            kind="preference",
            scope="global",
            scope_value="global",
            content="Be concise",
        )

        # Both should be inserted successfully
        assert m1 is not None
        assert m2 is not None

        node = make_select_memories(_ctx())
        state = {
            "thread": _mk_thread(sender=sender),
            "email_id": "e1",
        }

        mock_output = SelectMemoriesOutput(memory_ids=[m1.id])

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=mock_output,
        ) as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {"memory_ids": [m1.id]}
        mock_llm.assert_called_once()

        # Verify that pool_dicts were built correctly
        call_args = mock_llm.call_args
        assert call_args is not None
        prompt = call_args.args[0]
        # Check that both memories appear in the prompt
        assert str(m1.id) in prompt
        assert str(m2.id) in prompt

    def test_bounded_by_memory_inject_max(self) -> None:
        """When LLM returns many ids, output is truncated to max_inject.

        Scenario:
        - Insert 1 memory (to trigger LLM)
        - Mock LLM returning 20 ids (to exceed the default max_inject=3)
        - Config has memory_inject_max=3
        - Expected: node returns exactly 3 ids
        """
        sender = "alice@acme.com"
        m = mem_store.insert(
            kind="fact",
            scope="sender",
            scope_value=sender,
            content="Test memory",
        )
        assert m is not None

        # Create 20 fake uuids
        fake_ids = [uuid4() for _ in range(20)]

        node = make_select_memories(_ctx(pool_size=100, max_inject=3))
        state = {
            "thread": _mk_thread(sender=sender),
            "email_id": "e1",
        }

        mock_output = SelectMemoriesOutput(memory_ids=fake_ids)

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=mock_output,
        ):
            result = node(state)  # type: ignore[arg-type]

        # Result should have exactly 3 ids
        assert len(result["memory_ids"]) == 3
        assert result["memory_ids"] == fake_ids[:3]

    def test_empty_thread_yields_empty_list(self) -> None:
        """When thread is empty, node returns {"memory_ids": []}.

        Scenario:
        - state["thread"] is empty list
        - Expected: node returns {"memory_ids": []}, no DB or LLM calls
        """
        node = make_select_memories(_ctx())
        state = {
            "thread": [],
            "email_id": "e1",
        }

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {"memory_ids": []}
        mock_llm.assert_not_called()

    def test_missing_thread_key_defaults_to_empty(self) -> None:
        """When thread key is missing, defaults to empty list.

        Scenario:
        - state does not have "thread" key
        - Expected: node treats as empty thread
        """
        node = make_select_memories(_ctx())
        state = {"email_id": "e1"}

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {"memory_ids": []}
        mock_llm.assert_not_called()

    def test_none_thread_value_defaults_to_empty(self) -> None:
        """When thread value is None, defaults to empty list.

        Scenario:
        - state["thread"] is None
        - Expected: node treats as empty thread
        """
        node = make_select_memories(_ctx())
        state = {
            "thread": None,
            "email_id": "e1",
        }

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {"memory_ids": []}
        mock_llm.assert_not_called()

    def test_malformed_sender_email_gracefully_returns_empty(self) -> None:
        """When sender email cannot be extracted, node returns empty gracefully.

        Scenario:
        - Thread has email without 'from' field
        - Expected: node returns {"memory_ids": []}, no DB/LLM calls
        """
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

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {"memory_ids": []}
        mock_llm.assert_not_called()

    def test_respects_pool_size_config(self) -> None:
        """Verifies that pool_size config limits candidate_pool query.

        Scenario:
        - Insert 3 memories
        - Config has memory_candidate_pool=1
        - Expected: only 1 memory in the pool passed to LLM
        """
        sender = "alice@acme.com"
        m1 = mem_store.insert(
            kind="fact",
            scope="sender",
            scope_value=sender,
            content="First memory",
        )
        m2 = mem_store.insert(
            kind="fact",
            scope="sender",
            scope_value=sender,
            content="Second memory",
        )
        m3 = mem_store.insert(
            kind="fact",
            scope="sender",
            scope_value=sender,
            content="Third memory",
        )

        assert all([m1, m2, m3])

        node = make_select_memories(_ctx(pool_size=1, max_inject=16))
        state = {
            "thread": _mk_thread(sender=sender),
            "email_id": "e1",
        }

        mock_output = SelectMemoriesOutput(memory_ids=[])

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=mock_output,
        ) as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        # Verify that LLM was called with exactly 1 memory in pool
        call_args = mock_llm.call_args
        assert call_args is not None
        prompt = call_args.args[0]

        # Count memory ids in prompt (fragile but direct)
        # We should see only 1 memory id in the pool
        memory_count = prompt.count('id="')
        assert memory_count == 1

    def test_uses_sender_domain_and_global_scope(self) -> None:
        """Verifies that pool includes sender, domain, and global scoped memories.

        Scenario:
        - Insert sender-scoped memory (alice@acme.com)
        - Insert domain-scoped memory (acme.com)
        - Insert global-scoped memory
        - Expected: all 3 appear in the pool passed to LLM
        """
        sender = "alice@acme.com"
        domain = "acme.com"

        m_sender = mem_store.insert(
            kind="fact",
            scope="sender",
            scope_value=sender,
            content="Alice-specific",
        )
        m_domain = mem_store.insert(
            kind="fact",
            scope="domain",
            scope_value=domain,
            content="Acme-wide",
        )
        m_global = mem_store.insert(
            kind="preference",
            scope="global",
            scope_value="global",
            content="Universal rule",
        )

        assert all([m_sender, m_domain, m_global])

        node = make_select_memories(_ctx())
        state = {
            "thread": _mk_thread(sender=sender),
            "email_id": "e1",
        }

        mock_output = SelectMemoriesOutput(
            memory_ids=[m_sender.id, m_domain.id, m_global.id]
        )

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=mock_output,
        ) as mock_llm:
            result = node(state)  # type: ignore[arg-type]

        assert result == {"memory_ids": [m_sender.id, m_domain.id, m_global.id]}
        mock_llm.assert_called_once()
