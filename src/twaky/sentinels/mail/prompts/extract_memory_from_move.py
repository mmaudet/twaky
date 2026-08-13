"""Prompt: decide whether a folder move deserves a durable memory
beyond the statistical learned_pattern already recorded."""

from __future__ import annotations


def folder_move_prompt(
    *,
    sender_email: str,
    history_count: int,
    folder_name: str,
    subject: str,
) -> str:
    return (
        "The user moved a mail from Inbox to a custom folder. Decide whether "
        "this move reflects a durable relationship worth memorizing beyond "
        "the statistical pattern already recorded.\n\n"
        "Return JSON: {\"should_extract\": bool, "
        "\"memory\": {kind, scope, scope_value, content, confidence} | null}\n\n"
        "Extract a memory only when:\n"
        "- The sender has been seen >=3 times before AND consistently classified, OR\n"
        "- The destination folder name clearly implies a lasting role for the sender "
        "(e.g. \"Facturation\" for an accountant, \"Recrutement\" for a recruiter).\n\n"
        "Skip when:\n"
        "- First contact with a new sender (single move, no pattern yet).\n"
        "- Destination folder name is generic (e.g. \"Archive\", \"Divers\").\n\n"
        f"Sender: {sender_email} (seen {history_count} times before)\n"
        f"Destination folder: {folder_name}\n"
        f"Subject: {subject}\n"
    )


__all__ = ["folder_move_prompt"]
