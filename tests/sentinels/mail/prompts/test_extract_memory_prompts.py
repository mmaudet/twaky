"""Unit tests for extract_memory_from_diff and extract_memory_from_move prompts."""

from twaky.sentinels.mail.prompts.extract_memory_from_diff import draft_diff_prompt
from twaky.sentinels.mail.prompts.extract_memory_from_move import folder_move_prompt
from twaky.sentinels.mail.schemas_write_side import (
    DraftDiffOutput,
    ExtractedMemory,
    FolderMoveOutput,
)


def test_extracted_memory_content_max_200():
    import pytest
    with pytest.raises(ValueError):
        ExtractedMemory(
            kind="preference",
            scope="global",
            scope_value="*",
            content="x" * 201,
            confidence=0.9,
        )


def test_extracted_memory_confidence_range():
    import pytest
    with pytest.raises(ValueError):
        ExtractedMemory(
            kind="fact",
            scope="global",
            scope_value="*",
            content="ok",
            confidence=1.5,
        )


def test_draft_diff_output_defaults_empty_lists():
    out = DraftDiffOutput(memories=[])
    assert out.should_delete_previous_memory_ids == []


def test_folder_move_output_optional_memory():
    out = FolderMoveOutput(should_extract=False)
    assert out.memory is None


def test_draft_diff_prompt_contains_inputs():
    prompt = draft_diff_prompt(
        ai_draft="Cher Alexandre,\n\nMerci.\n\nBien à vous,\n\nMichel-Marie",
        shipped_body="Bonjour Alexandre,\n\nMerci.\n\nBien à vous,\n\nMichel-Marie",
        sender_email="alexandre@linagora.com",
        recipient_email="alexandre@linagora.com",
        thread_language="fr",
        previous_memories=[],
    )
    assert "Cher Alexandre" in prompt
    assert "Bonjour Alexandre" in prompt
    assert "alexandre@linagora.com" in prompt
    assert '"memories"' in prompt


def test_folder_move_prompt_contains_inputs():
    prompt = folder_move_prompt(
        sender_email="comptable@fournisseur.com",
        history_count=5,
        folder_name="Facturation",
        subject="Facture N°2026-0812",
    )
    assert "comptable@fournisseur.com" in prompt
    assert "Facturation" in prompt
    assert "5" in prompt
