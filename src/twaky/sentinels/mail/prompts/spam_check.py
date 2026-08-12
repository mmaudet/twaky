"""Prompt for spam/phishing grey-zone classification.

This module produces the prompt for Stage 4 (LLM grey-zone check) of the
spam-triage node. It classifies emails that rspamd flags as uncertain into
one of four buckets: spam, phishing-alert, newsletter, or none (pass through).
"""

from __future__ import annotations

from typing import Any

from twaky.sentinels.mail.prompts.helpers import (
    email_list_block,
    user_info_block,
)


def spam_check_prompt(
    state: dict[str, Any],
    headers_summary: str,
    rspamd_action: str | None,
    owner_email: str = "",
) -> str:
    """Generate prompt for spam grey-zone LLM classifier.

    Args:
        state: MailAgentState dict with optional 'thread' key containing email list.
        headers_summary: Compact header signals as key: value lines (e.g., "SPF: pass\nDKIM: fail").
        rspamd_action: Upstream rspamd verdict (e.g., "greylist", "add header") or None.
        owner_email: Email address of the thread owner for context.

    Returns:
        Plain string prompt for the LLM classifier (caller handles system/user split).
    """
    thread: list[dict[str, Any]] = state.get("thread") or []

    # Build rspamd verdict block
    if rspamd_action:
        rspamd_block = f"<rspamd_verdict>\nRspamd upstream verdict: {rspamd_action}\n</rspamd_verdict>"
    else:
        rspamd_block = (
            "<rspamd_verdict>\n"
            "No upstream verdict (rspamd passed or did not flag this email).\n"
            "</rspamd_verdict>"
        )

    thread_block = email_list_block(thread)
    user_info = user_info_block(owner_email)

    return (
        "You are a spam and phishing grey-zone classifier.\n\n"
        "Your role: Classify emails that rspamd or other filters flagged as uncertain into "
        "one of four buckets. Bias toward `none` (pass through) — prefer accuracy over recall.\n\n"
        f"{user_info}\n\n"
        f"{thread_block}\n\n"
        "<headers_summary>\n"
        f"{headers_summary}\n"
        "</headers_summary>\n\n"
        f"{rspamd_block}\n\n"
        "CLASSIFICATION BUCKETS:\n\n"
        "Return `bucket` = spam only if this is clearly bulk marketing OR clearly phishing.\n"
        "Return `phishing-alert` for high-confidence phishing (impersonation, credential harvesting, suspicious attachments).\n"
        "Return `newsletter` for legitimate newsletters the owner subscribed to.\n"
        "Return `none` if uncertain.\n\n"
        "Confidence in [0,1]; below 0.85 for spam/phishing-alert or 0.70 for newsletter means "
        "the runtime will pass through — prefer accuracy over recall."
    )
