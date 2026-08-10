"""Tests for twaky.sentinels.mail.prompts.helpers."""

from __future__ import annotations

import re

from twaky.sentinels.mail.prompts.helpers import (
    email_list_block,
    today_for_llm,
    user_info_block,
)


def test_email_block_wraps_in_thread() -> None:
    thread = [
        {
            "from": "alice@example.com",
            "to": "bob@example.com",
            "subject": "Hello",
            "received": "2026-08-10",
            "body": "Hi Bob",
        }
    ]
    result = email_list_block(thread)
    assert result.startswith("<thread>")
    assert result.endswith("</thread>")
    assert "<email>" in result


def test_escapes_angle_brackets_in_body() -> None:
    thread = [
        {
            "from": "attacker@evil.com",
            "to": "victim@example.com",
            "subject": "<script>alert('xss')</script>",
            "received": "2026-08-10",
            "body": "Click <script>evil()</script> here",
        }
    ]
    result = email_list_block(thread)
    # Raw HTML injection must NOT appear outside surrounding XML tags
    assert "<script>" not in result
    # Escaped form must be present
    assert "&lt;script&gt;" in result


def test_user_info_block_contains_owner() -> None:
    result = user_info_block("alice@example.com")
    assert "<owner>alice@example.com</owner>" in result


def test_today_format() -> None:
    result = today_for_llm()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \([A-Za-z]+\)", result), (
        f"today_for_llm() returned unexpected format: {result!r}"
    )
