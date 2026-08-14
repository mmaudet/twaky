"""SP5c: end-to-end pipeline tests for learned-pattern short-circuits.

Verifies that an active learned pattern for a sender (rule_name =
`label:X`, `trust_sender`, or `block_sender`) actually short-circuits
the pipeline:
- `spam_triage` is skipped (no LLM call, no spam check).
- `apply_actions` fires the synthetic action (label/mark trust/set $junk).
- The pipeline ends after `apply_actions` for `block_sender` (no draft).
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
from twaky.sentinels.mail.store import learned_patterns as lp_store


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


@pytest.fixture(autouse=True)
def _wipe():
    tables = [
        "mail_sentinel_rule",
        "mail_sentinel_memory",
        "mail_sentinel_learned_pattern",
        "mail_sentinel_spam_decision",
    ]
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        for t in tables:
            cur.execute(f"DELETE FROM {t}")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        for t in tables:
            cur.execute(f"DELETE FROM {t}")


def _email(email_id: str, from_addr: str) -> dict[str, Any]:
    return {
        "id": email_id,
        "threadId": "t1",
        "receivedAt": "2026-01-01T10:00:00Z",
        "from": [{"email": from_addr, "name": from_addr.split("@")[0]}],
        "to": [{"email": "me@x.com", "name": "Me"}],
        "subject": "Test",
        "preview": "test body",
        "headers": [],
    }


def _ctx(adapter: InMemoryMailAdapter, config: dict | None = None) -> NodeContext:
    base = MagicMock()
    base.sentinel_row.config_values = config or {}
    return NodeContext(base=base, mail=adapter, owner_email="me@x.com")


def _activate_pattern(sender: str, rule_name: str) -> None:
    """Insert 3 record_decision calls to activate a learned pattern."""
    for _ in range(3):
        lp_store.record_decision(
            sender_email=sender, rule_name=rule_name, confidence_hint=0.95
        )


def _no_llm_stub(*_args, **_kwargs) -> Any:
    raise AssertionError("Pipeline must not call the LLM when a learned pattern matches")


class TestLearnedPatternLabel:
    def test_label_pattern_applies_label_without_llm(self) -> None:
        _activate_pattern("comptable@x.com", "label:Facturation")
        email = _email("e1", "comptable@x.com")
        adapter = InMemoryMailAdapter(seed={"e1": email})
        ctx = _ctx(adapter, config={"spam_filter_enabled": True})

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            side_effect=_no_llm_stub,
        ):
            state = process_email(ctx, "e1")

        assert state.get("matched_by") == "learned_pattern"
        assert state.get("rule_name") == "label:Facturation"
        assert "label:Facturation" in (state.get("actions_applied") or [])
        # Label actually applied on adapter
        assert "e1" in adapter._labels
        assert "Facturation" in adapter._labels["e1"]
        # Spam triage was skipped: no spam_bucket entry set
        assert state.get("spam_bucket") is None


class TestLearnedPatternTrustSender:
    def test_trust_sender_skips_spam_triage_no_side_effect(self) -> None:
        _activate_pattern("legit@x.com", "trust_sender")
        email = _email("e2", "legit@x.com")
        # Even with a $junk keyword pre-set, trust_sender should NOT let
        # spam_triage run and reclassify.
        email["keywords"] = {"$junk": True}
        adapter = InMemoryMailAdapter(seed={"e2": email})
        ctx = _ctx(adapter, config={"spam_filter_enabled": True})

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            side_effect=_no_llm_stub,
        ):
            state = process_email(ctx, "e2")

        assert state.get("rule_name") == "trust_sender"
        assert "trust_sender" in (state.get("actions_applied") or [])
        # No label__spam__ applied since spam_triage was skipped
        assert "__spam__" not in adapter._labels.get("e2", [])


class TestLearnedPatternBlockSender:
    def test_block_sender_sets_junk_keyword(self) -> None:
        _activate_pattern("spammer@x.com", "block_sender")
        email = _email("e3", "spammer@x.com")
        adapter = InMemoryMailAdapter(seed={"e3": email})
        ctx = _ctx(adapter, config={"spam_filter_enabled": True})

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            side_effect=_no_llm_stub,
        ):
            state = process_email(ctx, "e3")

        assert state.get("rule_name") == "block_sender"
        assert "block_sender" in (state.get("actions_applied") or [])
        # $junk keyword set via adapter.set_keyword
        assert "e3" in adapter._keywords
        assert adapter._keywords["e3"].get("$junk") is True
