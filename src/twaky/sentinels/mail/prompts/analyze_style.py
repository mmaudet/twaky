"""Prompt: analyze N sample Sent mails to derive a writing-style profile.

The output is a plain-text style profile identical in shape to the
static ``USER_STYLE_MICHEL_MAUDET`` — it is injected directly into
the ``draft_reply`` prompt as ``<writing_style>``.
"""

from __future__ import annotations

from typing import Any


def analyze_style_prompt(
    *,
    owner_email: str,
    display_name: str,
    samples: list[dict[str, Any]],
) -> str:
    """Build the prompt that turns Sent samples into a style profile.

    Each sample is a dict with ``subject`` and ``body`` (plain text).
    """
    formatted_samples = "\n\n".join(
        f"--- Sample {i + 1} ---\nSubject: {s.get('subject', '')}\n\n{s.get('body', '')}"
        for i, s in enumerate(samples)
    )
    return (
        "You analyze a batch of the user's real Sent mail to produce a "
        "writing-style profile that another LLM will use to draft replies "
        "in the user's voice.\n\n"
        "Return ONLY the profile text (no preface, no framing) — it will "
        "be injected verbatim into a downstream prompt inside a "
        "<writing_style> block. The profile MUST include, in this order:\n\n"
        "LANGUAGE — MANDATORY MIRRORING\n"
        "  Detect languages the user writes in (FR/EN/etc.) and give a "
        "clear rule that the drafter must mirror the sender's language.\n\n"
        "ANTI-HALLUCINATION — CRITICAL\n"
        "  Never invent facts, offers, dates, names not in the incoming mail.\n\n"
        "GREETINGS\n"
        "  List the user's default openers per language, when they use "
        "first-name form, and what they never use.\n\n"
        "TONE & LENGTH\n"
        "  Describe defaults (short/medium/long), typical char count, "
        "when to be formal vs informal.\n\n"
        "ENGAGEMENT WITH THE SENDER'S MESSAGE\n"
        "  Rules for referencing the sender's concrete content, avoiding "
        "generic replies.\n\n"
        "CLOSINGS\n"
        "  List all closing formulas the user uses (with per-language "
        "guidance) and the exact signature block format ending with "
        "the user's first name on its own line.\n\n"
        "SIGNATURE BLOCK\n"
        "  Instruct the drafter to end with the closing + first name "
        "only, NOT the full company signature (that is appended "
        "automatically by the mail-sentinel pipeline).\n\n"
        "RECURRING PHRASES\n"
        "  Actual verbatim phrases the user reaches for repeatedly.\n\n"
        "FORMATTING\n"
        "  Paragraph separation, bullet lists policy, emojis, exclamation "
        "marks, AI-cliché phrases to avoid.\n\n"
        "REPLY BEHAVIOUR\n"
        "  Top-post rule, addressing each point, asking concrete questions "
        "when info is missing, mirroring language.\n\n"
        "WHAT HE/SHE NEVER DOES\n"
        "  Signing variants to avoid, emoji policy, fake claims of identity, "
        "hallucinated dates.\n\n"
        "FEW-SHOT EXAMPLES\n"
        "  Include 3 short verbatim replies from the samples below that "
        "best illustrate the style (running late, delegation, polite refusal, etc.).\n\n"
        f"Owner: {display_name} <{owner_email}>\n\n"
        "Samples (most recent Sent mails):\n\n"
        f"{formatted_samples}\n"
    )


__all__ = ["analyze_style_prompt"]
