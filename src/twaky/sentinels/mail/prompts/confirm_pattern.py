"""SP5c 5.1: LLM prompt to confirm/refute a learned pattern.

Given a recent mail from a sender that has an active learned pattern
(``block_sender``, ``trust_sender``, or ``label:<name>``), ask the LLM
whether the pattern still fits this specific mail. Used by the periodic
pattern health check to detect drift (senders that changed behaviour,
false-positives that stabilised early).
"""

from __future__ import annotations

from typing import Any


def confirm_pattern_prompt(
    *,
    email: dict[str, Any],
    rule_name: str,
) -> str:
    """Build the confirmation prompt.

    Returns a plain-text prompt that expects a ``PatternConfirmOutput``
    (Pydantic) response.
    """
    subject = str(email.get("subject") or "").strip()
    preview = str(email.get("preview") or "").strip()[:800]
    from_list = email.get("from") or []
    sender = ""
    if isinstance(from_list, list) and from_list:
        first = from_list[0]
        if isinstance(first, dict):
            sender = str(first.get("email") or "")

    if rule_name == "block_sender":
        rule_description = (
            "the mails from this sender should be treated as spam / phishing / "
            "unwanted commercial junk"
        )
    elif rule_name == "trust_sender":
        rule_description = (
            "this sender is legit (real person, business partner, subscribed "
            "service the user actually wants) — never classify as spam"
        )
    elif rule_name.startswith("label:"):
        label = rule_name.split(":", 1)[1]
        rule_description = (
            f'mails from this sender should carry the "{label}" label because '
            "the user consistently moved them into that folder"
        )
    else:
        rule_description = f"the sender fits the rule '{rule_name}'"

    return (
        f"A learned pattern says: for sender ``{sender}``, {rule_description}.\n\n"
        "Look at the following recent mail from this sender and decide whether "
        "the pattern STILL fits this specific mail:\n\n"
        f"Sender: {sender}\n"
        f"Subject: {subject}\n"
        "Preview:\n"
        f'"""\n{preview}\n"""\n\n'
        'Return JSON: {"confirms": true|false, "reason": "<one short sentence>"}\n\n'
        "Guidelines:\n"
        "- confirms=true when the pattern's classification still makes sense for THIS mail.\n"
        "- confirms=false when the sender clearly changed behaviour (a formerly-spammy "
        "sender now sends a legit personal mail, or vice-versa; a formerly-tagged sender "
        "sends something obviously off-topic).\n"
        "- Be conservative: when unsure, prefer confirms=true (avoids over-decaying "
        "still-useful patterns).\n"
    )


__all__ = ["confirm_pattern_prompt"]
