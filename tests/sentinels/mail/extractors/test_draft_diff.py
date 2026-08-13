"""Draft diff extractor: LLM extracts memories from AI vs shipped diff."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from twaky.sentinels.mail.extractors.draft_diff import (
    _levenshtein_ratio,
    extract_draft_diff,
)
from twaky.sentinels.mail.schemas_write_side import DraftDiffOutput, ExtractedMemory
from twaky.sentinels.mail.store.observations import ExtractionOutcome

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_observation")
        cur.execute("DELETE FROM mission WHERE declared_by='sentinel:mail'")
    yield


def _insert_mission_with_artifact(*, message_id: str, ai_draft: str, owner: str):
    import json

    from twaky.db import get_pool

    mission_id = uuid4()
    artifacts = json.dumps([
        {"kind": "draft", "body": ai_draft, "in_reply_to_message_id": message_id}
    ])
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mission (id, owner_email, declared_by, intent_text, "
            "state, artifacts) VALUES (%s, %s, 'sentinel:mail', 'Draft ready: test', "
            "'awaiting_user', %s::jsonb)",
            (mission_id, owner, artifacts),
        )
    return mission_id


def test_levenshtein_ratio_identical():
    assert _levenshtein_ratio("hello", "hello") == 0.0


def test_levenshtein_ratio_completely_different():
    assert _levenshtein_ratio("hello", "world") > 0.5


def test_trivial_diff_returns_skipped():
    _insert_mission_with_artifact(
        message_id="<msg1@x>",
        ai_draft="Bonjour Alex,\n\nMerci.\n\nBien à vous,\n\nMichel-Marie",
        owner="mmaudet@linagora.com",
    )
    result = extract_draft_diff(
        email_id="e1",
        mailbox_id="sent-mbx",
        sender_email="alex@x.com",
        recipient_email="alex@x.com",
        shipped_body="Bonjour Alex,\n\nMerci!\n\nBien à vous,\n\nMichel-Marie",
        subject="Re: test",
        in_reply_to="<msg1@x>",
        owner_email="mmaudet@linagora.com",
    )
    assert result.outcome == ExtractionOutcome.SKIPPED_TRIVIAL
    assert result.memory_ids == []


def test_no_mission_match_returns_skipped_no_match():
    r = extract_draft_diff(
        email_id="e2",
        mailbox_id="sent-mbx",
        sender_email="x@y.com",
        recipient_email="x@y.com",
        shipped_body="Body",
        subject="s",
        in_reply_to="<nomatch@x>",
        owner_email="mmaudet@linagora.com",
    )
    assert r.outcome == ExtractionOutcome.SKIPPED_NO_MATCH


def test_llm_extraction_creates_memories_and_transitions_mission():
    mid = _insert_mission_with_artifact(
        message_id="<msg1@x>",
        ai_draft="Cher Alexandre,\n\nMerci.\n\nBien à vous,\n\nMichel-Marie",
        owner="mmaudet@linagora.com",
    )
    llm_out = DraftDiffOutput(
        memories=[
            ExtractedMemory(
                kind="preference",
                scope="sender",
                scope_value="alexandre@linagora.com",
                content="Utilise 'Bonjour Alexandre' au lieu de 'Cher Alexandre'",
                confidence=0.9,
            )
        ]
    )
    with patch(
        "twaky.sentinels.mail.extractors.draft_diff.structured_call",
        return_value=llm_out,
    ):
        r = extract_draft_diff(
            email_id="e3",
            mailbox_id="sent-mbx",
            sender_email="alexandre@linagora.com",
            recipient_email="alexandre@linagora.com",
            shipped_body="Bonjour Alexandre,\n\nMerci pour ta note, on regarde ça demain.\n\nBien à vous,\n\nMichel-Marie",
            subject="Re: sujet",
            in_reply_to="<msg1@x>",
            owner_email="mmaudet@linagora.com",
        )
    assert r.outcome == ExtractionOutcome.EXTRACTED
    assert len(r.memory_ids) == 1

    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT state FROM mission WHERE id=%s", (mid,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "done"


def test_low_confidence_memory_filtered_out():
    _insert_mission_with_artifact(
        message_id="<msg2@x>",
        ai_draft="A",
        owner="mmaudet@linagora.com",
    )
    llm_out = DraftDiffOutput(
        memories=[
            ExtractedMemory(
                kind="preference",
                scope="sender",
                scope_value="x@y.com",
                content="test",
                confidence=0.5,  # below 0.7 threshold
            )
        ]
    )
    with patch(
        "twaky.sentinels.mail.extractors.draft_diff.structured_call",
        return_value=llm_out,
    ):
        r = extract_draft_diff(
            email_id="e5",
            mailbox_id="sent-mbx",
            sender_email="x@y.com",
            recipient_email="x@y.com",
            shipped_body="A very different body indeed, longer than the AI draft",
            subject="Re: s",
            in_reply_to="<msg2@x>",
            owner_email="mmaudet@linagora.com",
        )
    assert r.outcome == ExtractionOutcome.EXTRACTED
    assert r.memory_ids == []


def test_llm_failure_returns_error_outcome():
    _insert_mission_with_artifact(
        message_id="<msg3@x>",
        ai_draft="A",
        owner="mmaudet@linagora.com",
    )
    with patch(
        "twaky.sentinels.mail.extractors.draft_diff.structured_call",
        side_effect=RuntimeError("llm down"),
    ):
        r = extract_draft_diff(
            email_id="e6",
            mailbox_id="sent-mbx",
            sender_email="x@y.com",
            recipient_email="x@y.com",
            shipped_body="A very different body indeed, longer than the AI draft",
            subject="Re: s",
            in_reply_to="<msg3@x>",
            owner_email="mmaudet@linagora.com",
        )
    assert r.outcome == ExtractionOutcome.ERROR
