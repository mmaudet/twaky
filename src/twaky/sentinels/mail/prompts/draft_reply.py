"""Prompt de rédaction de brouillon avec injection de mémoires."""

from __future__ import annotations

from typing import Any

from twaky.sentinels.mail.prompts.helpers import (
    email_list_block,
    today_for_llm,
    user_info_block,
)
from twaky.sentinels.mail.style_profile import get_style_profile

DEFAULT_WRITING_STYLE = """Keep it concise, direct, and friendly.
Keep the reply short. Aim for 2 sentences at most unless a brief answer to multiple questions needs more.
Don't be pushy.
Write in a plainspoken, professional tone.
Prefer short declarative sentences over polished or overly elaborate phrasing."""

DRAFT_OUTPUT_INSTRUCTION = (
    "Return plain text only. Do not use HTML tags. If a clickable link is "
    "necessary, use markdown links in the format [Label](https://example.com/path) "
    "or [Label](mailto:name@example.com)."
)


def draft_reply_prompt(
    state: dict[str, Any],
    memories: list[dict[str, Any]],
    *,
    owner_email: str = "",
) -> str:
    """Draft a reply to the email thread.

    Injects a <memories> block only when non-empty.
    Mirror the language of the latest message (ISO-639-1 language code).
    """
    thread: list[dict[str, Any]] = state.get("thread", [])
    # Precedence: state override → owner-specific profile → generic default.
    writing_style: str = (
        state.get("writing_style", "")
        or (get_style_profile(owner_email) if owner_email else None)
        or DEFAULT_WRITING_STYLE
    )

    # Build memories block only when non-empty
    memories_block = ""
    if memories:
        entries = "\n".join(
            f'  <memory id="{m.get("id", "")}">{m.get("content", "")}</memory>'
            for m in memories
        )
        memories_block = (
            "\n\n<memories>\n"
            "Facts and instructions learned from how the user edited previous drafts. "
            "Apply them when relevant; ignore them when they do not fit this email.\n"
            f"{entries}\n"
            "</memories>"
        )

    thread_block = email_list_block(thread)

    return (
        "You are an expert assistant that drafts email replies.\n\n"
        "Use context from the previous emails and the provided knowledge base to make it relevant and accurate.\n"
        "Current thread facts override advisory context. Do not ask for details already present there.\n"
        "IMPORTANT: Do NOT simply repeat or mirror what the last email said. "
        "It doesn't add anything to the conversation to repeat back to them what they just said.\n"
        "Don't mention that you're an AI.\n"
        "Don't reply with a Subject. Only reply with the body of the email.\n"
        f"{DRAFT_OUTPUT_INSTRUCTION}\n"
        'IMPORTANT: Format paragraphs using Unix newlines: use "\\n\\n" between paragraphs '
        'and "\\n" for single line breaks.\n'
        "Mirror the language of the latest message in the thread. "
        "Report the ISO-639-1 language code of the reply in the `language` field.\n\n"
        "IMPORTANT: Use placeholders sparingly! Only use them where you have limited information.\n"
        "Never use placeholders for the user's name. If the writing style below prescribes "
        "a closing formula, signature block, or name, follow it verbatim. Otherwise, "
        "do not add a signature.\n"
        "Do not invent information.\n"
        "Ground facts, terms, statuses, dates, approvals, attachments, completed actions, "
        "and external changes in the thread or provided context.\n"
        "Address each distinct question or requested action that the available context can answer; "
        "do not trade away completeness for brevity.\n"
        "When key context is missing, still draft the most useful reply you can, "
        "but use lower confidence when the draft relies on assumptions or user-fillable details.\n"
        "Treat email dates as message metadata, not calendar context.\n"
        "Do not use em dashes unless the provided writing style explicitly calls for them.\n"
        "Don't suggest meeting times or mention availability unless specific calendar information is provided.\n"
        "When the sender provides a scheduling link or scheduling process, use that path instead of "
        "adding the user's booking link.\n\n"
        "Write an email that follows up on the previous conversation.\n"
        "Your reply should aim to continue the conversation or provide new information based on the "
        "context or knowledge base. If you have nothing substantial to add, keep the reply minimal.\n"
        "By default, keep replies concise, direct, friendly, plainspoken, and no longer than needed.\n"
        "The user's writing style can override these defaults.\n\n"
        f"<writing_style>\n{writing_style}\n</writing_style>\n\n"
        f"{user_info_block(owner_email)}\n\n"
        f"Today: {today_for_llm()}"
        f"{memories_block}\n\n"
        f"{thread_block}"
    )
