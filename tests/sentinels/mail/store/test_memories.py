"""Tests for twaky.sentinels.mail.store.memories.

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 env to override default host.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels.mail.store import memories as store
from twaky.sentinels.mail.store.memories import MailMemory


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
    """Delete all rows from mail_sentinel_memory before and after each test."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")


# ---------------------------------------------------------------------------
# TestInsert
# ---------------------------------------------------------------------------


class TestInsert:
    def test_ok_scope_sender(self):
        mem = store.insert(
            kind="fact",
            scope="sender",
            scope_value="a@b.com",
            content="Q3 due Friday",
        )
        assert mem is not None
        assert isinstance(mem, MailMemory)
        assert mem.kind == "fact"
        assert mem.scope == "sender"
        assert mem.scope_value == "a@b.com"
        assert mem.content == "Q3 due Friday"
        assert mem.id is not None

    def test_duplicate_returns_none(self):
        first = store.insert(
            kind="fact",
            scope="sender",
            scope_value="a@b.com",
            content="Q3 due Friday",
        )
        assert first is not None

        second = store.insert(
            kind="fact",
            scope="sender",
            scope_value="a@b.com",
            content="Q3 due Friday",
        )
        assert second is None

    def test_public_domain_refused(self):
        result = store.insert(
            kind="preference",
            scope="domain",
            scope_value="gmail.com",
            content="Send brief replies",
        )
        assert result is None

    def test_public_domain_case_insensitive(self):
        result = store.insert(
            kind="preference",
            scope="domain",
            scope_value="GmAIL.com",
            content="Send brief replies",
        )
        assert result is None

    def test_content_normalized(self):
        first = store.insert(
            kind="fact",
            scope="sender",
            scope_value="a@b.com",
            content="x   y  z",
        )
        assert first is not None
        assert first.content == "x y z"

        # "x y z" normalizes to the same value → dedup
        second = store.insert(
            kind="fact",
            scope="sender",
            scope_value="a@b.com",
            content="x y z",
        )
        assert second is None


# ---------------------------------------------------------------------------
# TestCandidatePool
# ---------------------------------------------------------------------------


class TestCandidatePool:
    def test_union_of_scopes(self):
        # seed: alice sender-scoped
        alice_mem = store.insert(
            kind="fact",
            scope="sender",
            scope_value="alice@acme.com",
            content="Alice prefers short replies",
        )
        assert alice_mem is not None

        # seed: acme.com domain-scoped
        acme_mem = store.insert(
            kind="fact",
            scope="domain",
            scope_value="acme.com",
            content="Acme uses formal language",
        )
        assert acme_mem is not None

        # seed: global memory
        global_mem = store.insert(
            kind="preference",
            scope="global",
            scope_value="",
            content="Always end with a call to action",
        )
        assert global_mem is not None

        # seed: unrelated sender
        bob_mem = store.insert(
            kind="fact",
            scope="sender",
            scope_value="bob@other.com",
            content="Bob prefers verbose replies",
        )
        assert bob_mem is not None

        pool = store.candidate_pool("alice@acme.com", limit=10)
        contents = [m.content for m in pool]

        assert "Alice prefers short replies" in contents
        assert "Acme uses formal language" in contents
        assert "Always end with a call to action" in contents
        assert "Bob prefers verbose replies" not in contents


# ---------------------------------------------------------------------------
# TestPurge
# ---------------------------------------------------------------------------


class TestPurge:
    def test_purge_expired(self):
        fresh = store.insert(
            kind="fact",
            scope="sender",
            scope_value="c@d.com",
            content="Fresh memory",
        )
        assert fresh is not None

        stale = store.insert(
            kind="fact",
            scope="sender",
            scope_value="c@d.com",
            content="Stale memory",
        )
        assert stale is not None

        # Force the stale row's expires_at into the past
        with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE mail_sentinel_memory "
                "SET expires_at = now() - INTERVAL '1 hour' "
                "WHERE id = %s",
                (str(stale.id),),
            )

        deleted = store.purge_expired()
        assert deleted == 1

        remaining = store.list_recent()
        assert len(remaining) == 1
        assert remaining[0].content == "Fresh memory"
