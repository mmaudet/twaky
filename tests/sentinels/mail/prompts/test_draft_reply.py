"""Tests for twaky.sentinels.mail.prompts.draft_reply."""

from __future__ import annotations

from twaky.sentinels.mail.prompts.draft_reply import draft_reply_prompt


def _state() -> dict:
    return {
        "thread": [
            {
                "from": "bob@example.com",
                "to": "alice@example.com",
                "subject": "Q3 plan",
                "received": "2026-08-10",
                "body": "What is the deadline for Q3?",
            }
        ]
    }


def test_no_memories_yields_no_block() -> None:
    prompt = draft_reply_prompt(_state(), memories=[])
    assert "<memories>" not in prompt


def test_memories_present_render_in_block() -> None:
    memories = [{"id": "abc-123", "content": "Q3 due Friday"}]
    prompt = draft_reply_prompt(_state(), memories=memories)
    assert "<memories>" in prompt
    assert "Q3 due Friday" in prompt


def test_prompt_forces_language_mirroring() -> None:
    prompt = draft_reply_prompt(_state(), memories=[])
    assert "Mirror the language" in prompt
    assert "ISO-639-1" in prompt
