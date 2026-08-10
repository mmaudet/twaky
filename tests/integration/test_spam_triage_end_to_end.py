"""Backend integration tests for the spam_triage pipeline node.

Exercises the end-to-end path using InMemoryMailAdapter + real Postgres:
  - Test 1: $junk keyword → spam decision row inserted, labels applied.
  - Test 2: nonjunk keyword → no spam decision row, pipeline reaches thread_status.

Requires:
  TWAKY_PG_HOST=172.27.0.33  (or a reachable Postgres with the spam_decision table)

Run:
    TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/integration/test_spam_triage_end_to_end.py -v
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from twaky.config import settings

# ---------------------------------------------------------------------------
# DB reachability
# ---------------------------------------------------------------------------

_PG_HOST = os.environ.get("TWAKY_PG_HOST", settings.twaky_pg_host)


def _pg_dsn() -> str:
    return (
        f"host={_PG_HOST} port={settings.twaky_pg_port} "
        f"dbname={settings.twaky_pg_db} "
        f"user={settings.twaky_pg_user} password={settings.twaky_pg_password}"
    )


def _pg_reachable() -> bool:
    try:
        with psycopg.connect(_pg_dsn(), connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _pg_reachable(), reason="twaky-pg not reachable"),
]

# ---------------------------------------------------------------------------
# Imports (deferred until after skip check to avoid import-time DB calls)
# ---------------------------------------------------------------------------

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
from twaky.sentinels.models import SentinelConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SPAM_FILTER_CONFIG: dict[str, Any] = {
    "spam_filter_enabled": True,
    "spam_llm_confidence_threshold": 0.85,
}

_TEST_EMAIL_ID = "spam-e2e-001"
_TEST_THREAD_ID = "spam-e2e-thread-001"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _seed_and_restore_config(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Seed spam_filter_enabled in the 'mail' sentinel config for the test duration.

    Uses direct Postgres UPDATE so the change is visible to the production pool.
    Restores the original config_values on teardown.
    """
    # Snapshot original
    with psycopg.connect(_pg_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT config_values FROM sentinel WHERE name = 'mail'")
        row = cur.fetchone()
        original_config = row[0] if row else {}

    # Apply spam_filter config
    with psycopg.connect(_pg_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        merged = {**(original_config or {}), **_SPAM_FILTER_CONFIG}
        cur.execute(
            "UPDATE sentinel SET config_values = %s::jsonb WHERE name = 'mail'",
            (json.dumps(merged),),
        )

    yield

    # Restore original config
    with psycopg.connect(_pg_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE sentinel SET config_values = %s::jsonb WHERE name = 'mail'",
            (json.dumps(original_config),),
        )


@pytest.fixture(autouse=True)
def _cleanup_spam_decisions() -> Generator[None, None, None]:
    """Delete test spam_decision rows before + after each test."""
    _delete_test_decisions()
    yield
    _delete_test_decisions()


def _delete_test_decisions() -> None:
    with psycopg.connect(_pg_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mail_sentinel_spam_decision WHERE email_id = %s",
            (_TEST_EMAIL_ID,),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    adapter: InMemoryMailAdapter,
    config_values: dict[str, Any],
    mission_mock: MagicMock | None = None,
) -> NodeContext:
    """Build a NodeContext backed by a mock base Context."""
    base = MagicMock()
    base.sentinel_row = MagicMock(spec=SentinelConfig)
    base.sentinel_row.config_values = dict(config_values)
    if mission_mock is not None:
        base.mission_emitter = mission_mock
    return NodeContext(base=base, mail=adapter, owner_email="me@example.com")


def _build_email(
    email_id: str,
    thread_id: str | None,
    keywords: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": email_id,
        "threadId": thread_id,
        "from": [{"email": "sender@external.example.com"}],
        "to": [{"email": "me@example.com"}],
        "subject": "Test email for spam_triage integration",
        "preview": "Preview text for the integration test email.",
        "receivedAt": "2026-08-10T10:00:00Z",
        "headers": [],
        "keywords": keywords,
        "hasAttachment": False,
    }


def _noop_llm(prompt: Any, schema: Any, **kwargs: Any) -> Any:
    """Minimal LLM stub for non-spam schemas (keeps pipeline non-blocking)."""
    if schema is ChooseRuleOutput:
        return ChooseRuleOutput(rule=None, matched_by="empty")
    if schema is LearnPatternOutput:
        return LearnPatternOutput(should_learn=False, confidence=0.0)
    if schema is ThreadStatusOutput:
        return ThreadStatusOutput(status=ThreadStatus.ACTIONED)
    if schema is SelectMemoriesOutput:
        return SelectMemoriesOutput(memory_ids=[])
    if schema is DraftReplyOutput:
        return DraftReplyOutput(body="Test draft.", language="en")
    raise AssertionError(f"Unexpected schema: {schema!r}")


def _count_decision_rows(email_id: str) -> list[dict[str, Any]]:
    """Return all spam_decision rows for a given email_id."""
    with (
        psycopg.connect(_pg_dsn()) as conn,
        conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
    ):
        cur.execute(
            "SELECT * FROM mail_sentinel_spam_decision WHERE email_id = %s",
            (email_id,),
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


def test_junk_keyword_produces_spam_decision_row_and_labels() -> None:
    """$junk keyword → spam_triage inserts spam_decision row + labels the email.

    Assertions:
    (a) Row inserted in ``mail_sentinel_spam_decision`` with
        ``signal_source='rspamd_junk_keyword'``.
    (b) ``adapter._labels[email_id]`` contains ``"__spam__"``
        (label applied via adapter).
    (c) ``adapter._keywords[email_id]["$junk"]`` is True
        (keyword set via adapter — mirrors what JMAP adapter would do).
    """
    email = _build_email(
        email_id=_TEST_EMAIL_ID,
        thread_id=_TEST_THREAD_ID,
        keywords={"$junk": True},
    )
    adapter = InMemoryMailAdapter(seed={_TEST_EMAIL_ID: email})
    ctx = _make_ctx(adapter, _SPAM_FILTER_CONFIG)

    with patch(
        "twaky.sentinels.mail.nodes.structured_call",
        side_effect=_noop_llm,
    ):
        state = process_email(ctx, _TEST_EMAIL_ID)

    # (a) DB row with correct signal_source
    rows = _count_decision_rows(_TEST_EMAIL_ID)
    assert len(rows) == 1, f"Expected 1 spam_decision row but got {len(rows)}"
    assert rows[0]["signal_source"] == "rspamd_junk_keyword", (
        f"Expected signal_source='rspamd_junk_keyword' but got {rows[0]['signal_source']!r}"
    )
    assert rows[0]["bucket"] == "spam", (
        f"Expected bucket='spam' but got {rows[0]['bucket']!r}"
    )

    # (b) __spam__ label applied
    labels = adapter._labels.get(_TEST_EMAIL_ID, [])
    assert "__spam__" in labels, (
        f"Expected '__spam__' label but adapter._labels={labels!r}"
    )

    # (c) $junk keyword set to True via adapter
    kw = adapter._keywords.get(_TEST_EMAIL_ID, {})
    assert kw.get("$junk") is True, (
        f"Expected adapter._keywords[email_id]['$junk']=True but got {kw!r}"
    )

    # State reflects termination via spam bucket
    assert state.get("spam_bucket") == "spam", (
        f"Expected state['spam_bucket']='spam' but got {state.get('spam_bucket')!r}"
    )


def test_nonjunk_keyword_leaves_pipeline_intact() -> None:
    """nonjunk keyword → spam_triage passes through; pipeline reaches thread_status.

    Assertions:
    - No ``mail_sentinel_spam_decision`` row inserted.
    - Pipeline reaches thread_status (state has ``status`` key).
    - ``spam_bucket`` is None (pass-through).
    """
    email = _build_email(
        email_id=_TEST_EMAIL_ID,
        thread_id=_TEST_THREAD_ID,
        keywords={"nonjunk": True},
    )
    adapter = InMemoryMailAdapter(seed={_TEST_EMAIL_ID: email})
    ctx = _make_ctx(adapter, _SPAM_FILTER_CONFIG)

    with patch(
        "twaky.sentinels.mail.nodes.structured_call",
        side_effect=_noop_llm,
    ):
        state = process_email(ctx, _TEST_EMAIL_ID)

    # No spam_decision row
    rows = _count_decision_rows(_TEST_EMAIL_ID)
    assert len(rows) == 0, (
        f"Expected 0 spam_decision rows but got {len(rows)}: {rows!r}"
    )

    # spam_bucket is None (pass-through)
    assert state.get("spam_bucket") is None, (
        f"Expected spam_bucket=None but got {state.get('spam_bucket')!r}"
    )

    # Pipeline reached thread_status (status key present)
    assert "status" in state, (
        f"Expected 'status' key in state (pipeline reached thread_status) but state keys={list(state.keys())!r}"
    )
