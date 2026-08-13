"""Prompt: extract durable lessons from the diff between an AI draft
and what the user actually sent."""

from __future__ import annotations

import json
from typing import Any


def draft_diff_prompt(
    *,
    ai_draft: str,
    shipped_body: str,
    sender_email: str,
    recipient_email: str,
    thread_language: str,
    previous_memories: list[dict[str, Any]],
) -> str:
    prev_block = json.dumps(previous_memories, ensure_ascii=False, indent=2)
    return (
        "You compare an AI-generated draft with what the user actually sent, "
        "and extract durable lessons the AI can apply to future replies.\n\n"
        "Return a JSON object with:\n"
        '  "memories": array of {kind, scope, scope_value, content, confidence}\n'
        '  "should_delete_previous_memory_ids": array of UUIDs (default [])\n\n'
        "Guidelines:\n"
        "- Only extract lessons that will apply beyond this specific mail.\n"
        "- Prefer scope=\"sender\" when the change is specific to this correspondent.\n"
        "- Prefer scope=\"domain\" when the change would apply to any correspondent in the same organization.\n"
        "- Prefer scope=\"global\" only when the lesson clearly applies to every reply the user writes.\n"
        "- Ignore purely factual insertions the user added (dates, numbers, names present in the incoming mail) — those are context, not lessons.\n"
        "- Include a confidence between 0 and 1. Use >=0.9 only when the diff clearly demonstrates a durable preference.\n"
        "- If a previous memory contradicts what the user just did, list its ID under should_delete_previous_memory_ids.\n"
        "- Keep each memory content <=200 characters, actionable, in the language the user writes drafts in.\n\n"
        f"Sender (original mail): {sender_email}\n"
        f"Recipient (of the sent reply): {recipient_email}\n"
        f"Thread language: {thread_language}\n\n"
        "AI draft:\n"
        '"""\n'
        f"{ai_draft}\n"
        '"""\n\n'
        "User's sent version:\n"
        '"""\n'
        f"{shipped_body}\n"
        '"""\n\n'
        "Previous memories for this sender:\n"
        f"{prev_block}\n"
    )


__all__ = ["draft_diff_prompt"]
