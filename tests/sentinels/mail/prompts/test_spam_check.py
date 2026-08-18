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


def _bucket_section(prompt: str, bucket: str) -> str:
    """Return the prompt text describing *bucket*, up to the next bucket header.

    Buckets are introduced as ``**<name>**`` markers; the section runs to the
    next marker (or to the end of the prompt for the last bucket).
    """
    marker = f"**{bucket}**"
    start = prompt.find(marker)
    assert start != -1, f"bucket header {marker!r} not found in prompt"
    rest = prompt[start + len(marker) :]
    nxt = rest.find("**")
    return rest if nxt == -1 else rest[:nxt]


def test_uncertain_mail_is_routed_to_the_none_bucket() -> None:
    """Genuinely uncertain mail must be documented as belonging to ``none``.

    Asserts the *placement* of the uncertainty instruction rather than a
    literal sentence. The prompt is retuned regularly (cf. 6d04832, which
    dropped the old "prefer accuracy over recall" wording while keeping the
    behaviour); pinning exact phrasing breaks the test on every reword
    without catching a behavioural regression.
    """
    prompt = spam_check_prompt(
        _state(),
        headers_summary="SPF: pass",
        rspamd_action=None,
        owner_email="alice@x",
    )
    none_section = _bucket_section(prompt, "none").lower()
    assert "uncertain" in none_section, (
        "The 'none' bucket must be the documented destination for uncertain "
        f"mail; its section reads: {none_section!r}"
    )
