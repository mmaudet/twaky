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
        "You are a spam and phishing grey-zone classifier for a French tech CEO's inbox.\n\n"
        "IMPORTANT CONTEXT ABOUT THE OWNER:\n"
        "- The owner is Michel-Marie Maudet, CEO of Linagora (open-source software company, France).\n"
        "- He receives: business correspondence, GitHub/tech notifications, event invitations,\n"
        "  legitimate B2B outreach he engages with, personal mail from friends & family.\n"
        "- Rspamd on this account has requiredScore=12 (very permissive) — its `nonjunk`\n"
        "  keyword is UNRELIABLE. Do NOT defer to rspamd's verdict; classify on your own\n"
        "  analysis of the content + headers.\n\n"
        f"{user_info}\n\n"
        f"{thread_block}\n\n"
        "<headers_summary>\n"
        f"{headers_summary}\n"
        "</headers_summary>\n\n"
        f"{rspamd_block}\n\n"
        "CLASSIFICATION BUCKETS:\n\n"
        "**spam** — mark high-confidence (0.85+) for ANY of these:\n"
        "  • Unsolicited cold sales outreach in ANY language (recruitment, SEO services,\n"
        "    'question about your company', 'quick chat about your business', etc.).\n"
        "  • Marketing / promo from unknown senders (event promo, hotel promos, deals).\n"
        "  • Sender local part looks bot-generated (random consonants like `oltiwbr`, `eclybnm`).\n"
        "  • Sender TLD in {.click, .buzz, .homes, .online, .top, .xyz, .deals, .info,\n"
        "    .tk, .ml, .ga, .cf} unless clearly a real business relationship.\n"
        "  • SEO/backlink/'PowerSchool Support'/'ticket has been created' bait patterns.\n"
        "  • Marketing/promo from senders the owner never engaged with previously.\n\n"
        "**phishing-alert** — high-confidence (0.85+) for:\n"
        "  • Brand impersonation (PayPal/Amazon/Microsoft/BNP/bank/Facebook alert claiming\n"
        "    account suspension, urgent verification, security notice) especially with\n"
        "    from-domain mismatch or missing DKIM.\n"
        "  • Delivery status notification / bounce forgeries.\n"
        "  • 'Urgent action required' / 'Your account will be closed' patterns.\n"
        "  • Credential-harvesting links or suspicious attachments.\n\n"
        "**newsletter** — 0.70+ for:\n"
        "  • Legitimate periodic content the owner likely subscribed to (Substack, media,\n"
        "    company product updates like Google Cloud, LinkedIn digest, GitHub explore).\n"
        "  • Presence of list-unsubscribe header is a strong signal.\n\n"
        "**none** — for genuinely uncertain OR clearly legitimate mail:\n"
        "  • Personal correspondence, GitHub PR notifications for the owner's repos,\n"
        "    calendar invites, internal Linagora mails, replies in existing threads.\n\n"
        "Confidence in [0,1]. The runtime uses configurable thresholds "
        "(default: spam/phishing 0.70, newsletter 0.70). Give your honest confidence — "
        "the runtime enforces the threshold. Explain your reasoning briefly in `reason`."
    )
