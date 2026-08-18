"""Folder move extractor: pattern always, LLM decides memory."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from twaky.sentinels.mail.extractors.folder_move import extract_folder_move
from twaky.sentinels.mail.schemas_write_side import (
    ExtractedMemory,
    FolderMoveOutput,
)
from twaky.sentinels.mail.store.observations import ExtractionOutcome

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
        cur.execute("DELETE FROM mail_sentinel_observation")
    yield


def test_should_extract_true_creates_memory_and_pattern():
    llm_out = FolderMoveOutput(
        should_extract=True,
        memory=ExtractedMemory(
            kind="fact",
            scope="sender",
            scope_value="c@x.com",
            content="Fournisseur récurrent facturation",
            confidence=0.9,
        ),
    )
    with patch(
        "twaky.sentinels.mail.extractors.folder_move.structured_call",
        return_value=llm_out,
    ):
        r = extract_folder_move(
            email_id="e1",
            mailbox_id="mbx-inbox",
            sender_email="c@x.com",
            folder_name="Facturation",
            subject="Facture 2026-01",
            history_count=5,
        )
    assert r.outcome == ExtractionOutcome.EXTRACTED
    assert len(r.pattern_ids) == 1
    assert len(r.memory_ids) == 1


def test_should_extract_false_creates_pattern_only():
    llm_out = FolderMoveOutput(should_extract=False)
    with patch(
        "twaky.sentinels.mail.extractors.folder_move.structured_call",
        return_value=llm_out,
    ):
        r = extract_folder_move(
            email_id="e2",
            mailbox_id="mbx-inbox",
            sender_email="unknown@z.com",
            folder_name="Archive",
            subject="Info",
            history_count=1,
        )
    assert r.outcome == ExtractionOutcome.EXTRACTED
    assert len(r.pattern_ids) == 1
    assert r.memory_ids == []


def test_folder_name_sanitized_for_rule_name():
    with patch(
        "twaky.sentinels.mail.extractors.folder_move.structured_call",
        return_value=FolderMoveOutput(should_extract=False),
    ):
        extract_folder_move(
            email_id="e3",
            mailbox_id="mbx-inbox",
            sender_email="c@x.com",
            folder_name="Ma Facturation!",
            subject="s",
            history_count=1,
        )
    from twaky.sentinels.mail.store import learned_patterns as lp

    pats = lp.list_all()
    assert any(p.rule_name == "label:Ma-Facturation" for p in pats)


def test_llm_failure_returns_error_outcome():
    with patch(
        "twaky.sentinels.mail.extractors.folder_move.structured_call",
        side_effect=RuntimeError("llm down"),
    ):
        r = extract_folder_move(
            email_id="e4",
            mailbox_id="mbx-inbox",
            sender_email="c@x.com",
            folder_name="Facturation",
            subject="s",
            history_count=5,
        )
    # Pattern still recorded (deterministic), LLM failed → error outcome
    assert r.outcome == ExtractionOutcome.ERROR
    assert r.error_repr is not None
    assert len(r.pattern_ids) == 1
    assert r.memory_ids == []
