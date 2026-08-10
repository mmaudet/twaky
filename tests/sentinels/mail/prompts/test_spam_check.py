"""Tests for twaky.sentinels.mail.prompts.spam_check."""

from __future__ import annotations

from twaky.sentinels.mail.prompts.spam_check import spam_check_prompt


def _state() -> dict:
    """Helper: minimal MailAgentState."""
    return {
        "thread": [
            {
                "from": "marketing@example.com",
                "to": "alice@x",
                "subject": "Special Offer",
                "received": "2026-08-10",
                "body": "Check out our latest deals!",
            }
        ]
    }


def test_prompt_contains_all_four_bucket_options() -> None:
    """Prompt must enumerate all four classification buckets."""
    prompt = spam_check_prompt(
        _state(),
        headers_summary="SPF: pass\nDKIM: pass",
        rspamd_action="greylist",
        owner_email="alice@x",
    )
    for bucket in ("spam", "phishing-alert", "newsletter", "none"):
        assert bucket in prompt, f"Expected bucket {bucket!r} not found in prompt"


def test_prompt_mentions_owner_email() -> None:
    """Prompt must include the owner email address."""
    owner = "alice@x"
    prompt = spam_check_prompt(
        _state(),
        headers_summary="SPF: pass",
        rspamd_action=None,
        owner_email=owner,
    )
    assert owner in prompt, f"Expected owner email {owner!r} not found in prompt"


def test_prompt_mentions_rspamd_action_when_given() -> None:
    """Prompt must include the rspamd action when provided."""
    action = "greylist"
    prompt = spam_check_prompt(
        _state(),
        headers_summary="SPF: pass",
        rspamd_action=action,
        owner_email="alice@x",
    )
    assert action in prompt, f"Expected rspamd action {action!r} not found in prompt"


def test_prompt_omits_rspamd_section_when_none() -> None:
    """Prompt must indicate 'no upstream verdict' when rspamd_action is None."""
    prompt = spam_check_prompt(
        _state(),
        headers_summary="SPF: pass",
        rspamd_action=None,
        owner_email="alice@x",
    )
    # Check for "no upstream verdict" or equivalent phrasing
    assert "no upstream verdict" in prompt.lower(), (
        "Expected 'no upstream verdict' phrase in prompt when rspamd_action is None"
    )


def test_prompt_biases_toward_none() -> None:
    """Prompt must emphasize 'uncertain' and 'prefer accuracy over recall' bias."""
    prompt = spam_check_prompt(
        _state(),
        headers_summary="SPF: pass",
        rspamd_action=None,
        owner_email="alice@x",
    )
    # Check for keywords indicating bias toward 'none' and accuracy preference
    assert "uncertain" in prompt.lower(), (
        "Expected 'uncertain' keyword in prompt for bias toward 'none'"
    )
    assert "prefer accuracy over recall" in prompt.lower(), (
        "Expected 'prefer accuracy over recall' instruction in prompt"
    )
