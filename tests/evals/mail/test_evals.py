"""Fixture-based evals for the mail sentinel pipeline.

Loads 3 YAML fixtures from this directory, seeds InMemoryMailAdapter + the
rules store, patches ``structured_call`` with a deterministic dispatcher, and
asserts against each fixture's ``expected`` block.

Run offline (default, used in CI):
    uv run pytest tests/evals -v

Run against a real LLM (deferred to SP6b):
    EVAL_LIVE=1 uv run pytest tests/evals -v

Fixtures
--------
- spam_archive.yaml       — newsletter → archive action
- invoice_label.yaml      — invoice notification → label:invoice action
- meeting_request_draft.yaml — meeting request → draft_reply (draft saved)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
import yaml

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
# Directory containing the YAML fixtures
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent

_FIXTURE_FILES = [
    _FIXTURE_DIR / "spam_archive.yaml",
    _FIXTURE_DIR / "invoice_label.yaml",
    _FIXTURE_DIR / "meeting_request_draft.yaml",
]

# ---------------------------------------------------------------------------
# DB reachability
# ---------------------------------------------------------------------------


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Marks: require DB (rules store) + skip when DB not reachable
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable"),
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe_rules():
    """Clean the rules table before and after each eval test."""
    tables = [
        "mail_sentinel_rule",
        "mail_sentinel_learned_pattern",
    ]
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        for table in tables:
            cur.execute(f"DELETE FROM {table}")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        for table in tables:
            cur.execute(f"DELETE FROM {table}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(path: Path) -> dict[str, Any]:
    """Load a YAML fixture file and return its content as a dict."""
    with path.open() as fh:
        return yaml.safe_load(fh)  # type: ignore[no-any-return]


def _build_email(email_spec: dict[str, Any]) -> dict[str, Any]:
    """Convert a fixture email spec into the internal email dict shape."""
    return {
        "id": email_spec["id"],
        "threadId": email_spec.get("threadId"),
        "receivedAt": email_spec.get("receivedAt", "2026-01-01T00:00:00Z"),
        "from": email_spec.get("from", []),
        "to": email_spec.get("to", []),
        "subject": email_spec.get("subject", ""),
        "preview": email_spec.get("preview", ""),
        "headers": [],
    }


def _seed_rule(rule_spec: dict[str, Any]) -> None:
    """Insert the rule described in the fixture into the rules store."""
    rules_store.create(
        name=rule_spec["name"],
        conditions=rule_spec.get("conditions", []),
        combinator=rule_spec.get("combinator", "OR"),
        actions=rule_spec["actions"],
    )


def _make_ctx(adapter: InMemoryMailAdapter) -> NodeContext:
    """Build a NodeContext with a MagicMock base and no-op config_values."""
    base = MagicMock()
    base.sentinel_row.config_values = {}
    return NodeContext(base=base, mail=adapter, owner_email="me@example.com")


def _deterministic_llm(prompt: Any, schema: Any, **kwargs: Any) -> Any:
    """Deterministic fake LLM dispatcher used in offline evals.

    Returns scripted outputs per schema type so the pipeline can run
    end-to-end without any network calls.

    ``ThreadStatusOutput`` always returns ``TO_REPLY`` so the pipeline's
    post-status router can make the real decision based on whether the
    matched rule contains ``draft_reply`` — keeping draft-vs-no-draft
    logic in the production code, not here.
    """
    if schema is ChooseRuleOutput:
        # Not reached for static-match fixtures; safe fallback.
        return ChooseRuleOutput(rule=None, matched_by="empty")
    if schema is LearnPatternOutput:
        return LearnPatternOutput(should_learn=False, confidence=0.0)
    if schema is ThreadStatusOutput:
        # Always return TO_REPLY: the pipeline router (_route_after_status)
        # then checks rule.actions for "draft_reply" to decide the next node,
        # so production logic — not the fake LLM — controls draft routing.
        return ThreadStatusOutput(status=ThreadStatus.TO_REPLY)
    if schema is SelectMemoriesOutput:
        return SelectMemoriesOutput(memory_ids=[])
    if schema is DraftReplyOutput:
        return DraftReplyOutput(
            body="Thank you for your message. I will get back to you shortly.",
            language="en",
        )
    raise AssertionError(f"Unexpected structured_call for schema {schema!r}")


# ---------------------------------------------------------------------------
# Parametrized eval test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_path",
    _FIXTURE_FILES,
    ids=[p.stem for p in _FIXTURE_FILES],
)
def test_eval_fixture(fixture_path: Path) -> None:
    """Run a single fixture end-to-end and assert against its expected block."""
    live = os.environ.get("EVAL_LIVE", "").strip() == "1"
    if live:
        pytest.skip("EVAL_LIVE=1 real-LLM path deferred to SP6b")

    # --- Load fixture ---
    spec = _load_fixture(fixture_path)
    expected = spec["expected"]

    # --- Seed rule ---
    _seed_rule(spec["rule"])

    # --- Seed adapter ---
    email = _build_email(spec["email"])
    adapter = InMemoryMailAdapter(seed={email["id"]: email})

    # --- Build context ---
    ctx = _make_ctx(adapter)

    # --- Run pipeline with fake LLM ---
    with patch(
        "twaky.sentinels.mail.nodes.structured_call",
        side_effect=_deterministic_llm,
    ):
        state = process_email(ctx, email["id"])

    # --- Assert: coarse action check ---
    contains_action: str | None = expected.get("contains_action")
    if contains_action is not None:
        actions_applied: list[str] = state.get("actions_applied") or []
        assert contains_action in actions_applied, (
            f"[{spec['name']}] expected action {contains_action!r} "
            f"in actions_applied={actions_applied!r}"
        )

    # --- Assert: draft presence ---
    has_draft: bool = expected.get("has_draft", False)
    if has_draft:
        assert state.get("draft") is not None, (
            f"[{spec['name']}] expected a draft to be set but draft is None"
        )
        assert len(adapter._drafts) >= 1, (
            f"[{spec['name']}] expected at least one saved draft in adapter"
        )
    else:
        assert state.get("draft") is None, (
            f"[{spec['name']}] expected no draft but got: {state.get('draft')!r}"
        )
