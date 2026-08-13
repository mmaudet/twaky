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


def test_owner_style_profile_injected_when_owner_known() -> None:
    """When owner_email matches a registered profile, use it as writing style."""
    prompt = draft_reply_prompt(
        _state(), memories=[], owner_email="michel.maudet@linagora.com"
    )
    # Signature block distinctive to Michel's profile
    assert "Villa Good Tech" in prompt
    assert "LINAGORA" in prompt
    # Greeting patterns
    assert "Bonjour," in prompt


def test_default_style_when_owner_unknown() -> None:
    """Unknown owner → falls back to generic DEFAULT_WRITING_STYLE."""
    prompt = draft_reply_prompt(
        _state(), memories=[], owner_email="stranger@example.com"
    )
    assert "Villa Good Tech" not in prompt
    # Default style is generic
    assert "plainspoken" in prompt


def test_state_writing_style_overrides_profile() -> None:
    """Explicit state['writing_style'] overrides the owner profile."""
    state = _state()
    state["writing_style"] = "STATE-OVERRIDE-MARKER"
    prompt = draft_reply_prompt(
        state, memories=[], owner_email="michel.maudet@linagora.com"
    )
    assert "STATE-OVERRIDE-MARKER" in prompt
    assert "Villa Good Tech" not in prompt
