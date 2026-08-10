"""Tests for twaky.sentinels.mail.prompts.thread_status."""

from __future__ import annotations

from twaky.sentinels.mail.prompts.thread_status import thread_status_prompt


def _state(owner_email: str = "alice@x") -> dict:
    return {
        "thread": [
            {
                "from": "bob@example.com",
                "to": owner_email,
                "subject": "Re: Project",
                "received": "2026-08-10",
                "body": "Can you send me the report?",
            }
        ]
    }


def test_prompt_enumerates_four_statuses() -> None:
    prompt = thread_status_prompt(_state())
    for status in ("TO_REPLY", "ACTIONED", "FYI", "AWAITING_REPLY"):
        assert status in prompt, f"Expected status {status!r} not found in prompt"


def test_prompt_includes_owner() -> None:
    prompt = thread_status_prompt(_state(owner_email="alice@x"), owner_email="alice@x")
    assert "alice@x" in prompt


def test_prompt_edge_case_delegate_actioned() -> None:
    prompt = thread_status_prompt(_state())
    assert "delegate" in prompt.lower()
