"""Fixture-based evals for the mail sentinel pipeline.

Loads 3 YAML fixtures from this directory, seeds InMemoryMailAdapter + the
rules store, patches ``structured_call`` with a deterministic dispatcher, and
asserts against each fixture's ``expected`` block.

Also loads 5 SP6c spam-triage fixtures from ``tests/evals/mail/spam/``.  These
fixtures exercise the ``spam_triage`` node independently of ``match_rules`` —
no ``rule:`` key, ``config_values.spam_filter_enabled=true``.

Run offline (default, used in CI):
    uv run pytest tests/evals -v

Run against a real LLM (deferred to SP6b):
    EVAL_LIVE=1 uv run pytest tests/evals -v

Fixtures
--------
- spam_archive.yaml       — newsletter → archive action
- invoice_label.yaml      — invoice notification → label:invoice action
- meeting_request_draft.yaml — meeting request → draft_reply (draft saved)

Spam fixtures (tests/evals/mail/spam/)
---------------------------------------
- phishing_hard_attachment_dkim_none.yaml — LLM → phishing-alert (mission)
- newsletter_list_unsub.yaml             — heuristic → newsletter (no LLM)
- promo_marketing_greylist.yaml          — rspamd greylist → LLM called
- personal_reply_thread.yaml             — nonjunk keyword → bucket=none
- ham_edge_invoice.yaml                  — FP protection → bucket=none
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
    SpamCheckOutput,
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

_SPAM_FIXTURE_DIR = _FIXTURE_DIR / "spam"
_SPAM_FIXTURE_FILES = sorted(_SPAM_FIXTURE_DIR.glob("*.yaml"))

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
        # Spam fixtures supply headers, keywords, hasAttachment.
        # Original fixtures default to [] / {} / False.
        "headers": email_spec.get("headers", []),
        "keywords": email_spec.get("keywords", {}),
        "hasAttachment": email_spec.get("hasAttachment", False),
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


# ---------------------------------------------------------------------------
# SP6c Spam-triage parametrized eval test
# ---------------------------------------------------------------------------


def _make_spam_ctx(
    adapter: InMemoryMailAdapter,
    config_values: dict[str, Any],
    mission_mock: MagicMock,
) -> NodeContext:
    """Build a NodeContext seeded with the given config_values for spam tests."""
    base = MagicMock()
    base.sentinel_row.config_values = dict(config_values)
    base.mission_emitter = mission_mock
    return NodeContext(base=base, mail=adapter, owner_email="me@example.com")


def _spam_eval_fallback(prompt: Any, schema: Any, **kwargs: Any) -> Any:
    """Deterministic fallback for non-spam schemas in spam-eval tests.

    Returns conservative outputs that keep the pipeline moving without
    triggering draft_reply (which would emit a mission and confuse the
    mission_emitted assertion):
    - ``ThreadStatusOutput`` → ACTIONED (not TO_REPLY, so no draft path).
    - Everything else → same as ``_deterministic_llm``.
    """
    if schema is ThreadStatusOutput:
        return ThreadStatusOutput(status=ThreadStatus.ACTIONED)
    return _deterministic_llm(prompt, schema, **kwargs)


def _make_spam_llm_dispatcher(
    fake_outputs: dict[str, Any],
) -> Any:
    """Return a ``structured_call`` side-effect function for spam evals.

    Dispatches on schema type:
    - ``SpamCheckOutput`` → uses ``fake_outputs["spam_check"]`` entry.
      Increments call_count so the test can assert ``llm_called=true``.
    - All other schemas → fall back to ``_spam_eval_fallback`` (ACTIONED,
      no draft path, no downstream mission emit) without incrementing
      call_count (spam_triage-only tracking).

    Call count is tracked on the returned function object so the test can
    assert ``llm_called`` (bool: at least one SpamCheckOutput call).
    """
    call_count: list[int] = [0]

    def _dispatcher(prompt: Any, schema: Any, **kwargs: Any) -> Any:
        if schema is SpamCheckOutput:
            call_count[0] += 1
            sc = fake_outputs.get("spam_check")
            if sc is None:
                raise AssertionError(
                    "SpamCheckOutput called but no fake_llm_outputs.spam_check defined"
                )
            return SpamCheckOutput(
                bucket=sc["bucket"],
                confidence=sc["confidence"],
                reason=sc["reason"],
            )
        # Fallback to conservative dispatcher for pipeline-continuation schemas.
        return _spam_eval_fallback(prompt, schema, **kwargs)

    _dispatcher.call_count = call_count  # type: ignore[attr-defined]
    return _dispatcher


@pytest.mark.parametrize(
    "fixture_path",
    _SPAM_FIXTURE_FILES,
    ids=[p.stem for p in _SPAM_FIXTURE_FILES],
)
def test_spam_eval_fixture(fixture_path: Path) -> None:
    """Run a single spam-triage fixture end-to-end and assert expected outcomes.

    Assertions:
    - ``state["spam_bucket"]`` matches ``expected.bucket``
      (if ``expected.bucket`` is null, only asserts that LLM was called).
    - ``expected.llm_called`` matches whether ``structured_call`` was invoked.
    - ``expected.mission_emitted`` matches mission_emitter.emit call count.
    """
    live = os.environ.get("EVAL_LIVE", "").strip() == "1"
    if live:
        pytest.skip("EVAL_LIVE=1 real-LLM path not supported for spam evals")

    # --- Load fixture ---
    spec = _load_fixture(fixture_path)
    expected = spec["expected"]
    config_values: dict[str, Any] = spec.get("config_values", {})
    fake_outputs: dict[str, Any] = spec.get("fake_llm_outputs") or {}

    # --- Seed adapter ---
    email = _build_email(spec["email"])
    adapter = InMemoryMailAdapter(seed={email["id"]: email})

    # --- Build context with real mission mock ---
    mission_mock = MagicMock()
    mission_mock.emit = MagicMock()
    ctx = _make_spam_ctx(adapter, config_values, mission_mock)

    # --- Build dispatcher + run pipeline ---
    dispatcher = _make_spam_llm_dispatcher(fake_outputs)
    with patch(
        "twaky.sentinels.mail.nodes.structured_call",
        side_effect=dispatcher,
    ):
        state = process_email(ctx, email["id"])

    # --- Assert: spam_bucket ---
    # The node returns Python None for the pass-through case (bucket=none).
    # YAML `null` deserialises to Python None; YAML `none` deserialises to
    # the string "none".  Normalise: treat the string "none" as Python None
    # so fixture authors can write `bucket: none` naturally.
    _raw_expected_bucket = expected.get("bucket")
    expected_bucket = None if _raw_expected_bucket == "none" else _raw_expected_bucket
    actual_bucket = state.get("spam_bucket")

    if _raw_expected_bucket is not None:
        # _raw_expected_bucket is not None (YAML null) → we have an assertion.
        # Note: "none" → expected_bucket=None, and None → expected_bucket=None too.
        assert actual_bucket == expected_bucket, (
            f"[{spec['name']}] expected spam_bucket={expected_bucket!r} "
            f"but got {actual_bucket!r}"
        )
    else:
        # YAML null bucket means "LLM-dependent — accept any value but verify LLM was called"
        pass

    # --- Assert: LLM called (bool) ---
    spam_llm_calls = dispatcher.call_count[0]  # type: ignore[attr-defined]
    expected_llm_called: bool = expected.get("llm_called", False)
    if expected_llm_called:
        assert spam_llm_calls > 0, (
            f"[{spec['name']}] expected LLM to be called but call_count=0"
        )
    else:
        assert spam_llm_calls == 0, (
            f"[{spec['name']}] expected NO LLM call but call_count={spam_llm_calls}"
        )

    # --- Assert: mission emitted ---
    expected_mission_emitted: bool = expected.get("mission_emitted", False)
    actual_emit_count: int = mission_mock.emit.call_count
    if expected_mission_emitted:
        assert actual_emit_count >= 1, (
            f"[{spec['name']}] expected mission to be emitted but emit_count=0"
        )
    else:
        assert actual_emit_count == 0, (
            f"[{spec['name']}] expected NO mission emission but emit_count={actual_emit_count}"
        )
