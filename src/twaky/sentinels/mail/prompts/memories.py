"""Prompts de sélection et d'extraction de mémoires.

Deux fonctions :
- ``select_memories_prompt`` : demande au LLM de choisir jusqu'à 16 ids dans
  un pool candidat en fonction de la pertinence pour le thread courant.
- ``extract_memories_from_edit_prompt`` : extrait des mémoires durables de
  l'écart entre le brouillon IA et la version envoyée par l'utilisateur.
  Le scope ``domain`` est refusé pour les fournisseurs grand public
  (la règle est dans le texte du prompt ; l'application est en T15/store).
"""

from __future__ import annotations

from typing import Any

from twaky.sentinels.mail.prompts.helpers import email_list_block, today_for_llm

MAX_SELECTED = 16

# Public email providers for which scope=domain must not be used
PUBLIC_PROVIDERS = (
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "yahoo.fr",
    "icloud.com",
    "me.com",
    "mac.com",
    "proton.me",
    "protonmail.com",
    "tutanota.com",
    "aol.com",
    "msn.com",
    "wanadoo.fr",
    "orange.fr",
    "free.fr",
    "sfr.fr",
    "laposte.net",
)


def select_memories_prompt(
    state: dict[str, Any],
    candidate_pool: list[dict[str, Any]],
) -> str:
    """Ask the LLM to pick up to 16 memory ids relevant to this thread.

    The pool is described in an XML block; the LLM returns ids ordered by
    relevance, most relevant first.
    """
    thread: list[dict[str, Any]] = state.get("thread", [])
    thread_block = email_list_block(thread)

    if candidate_pool:
        pool_entries = "\n".join(
            f'  <memory id="{m.get("id", "")}" '
            f'kind="{m.get("kind", "")}" '
            f'scope="{m.get("scope", "")}">'
            f"{m.get('content', '')}"
            f"</memory>"
            for m in candidate_pool
        )
        pool_block = f"<candidate_memories>\n{pool_entries}\n</candidate_memories>"
    else:
        pool_block = "<candidate_memories />"

    return (
        "You select which stored reply memories are relevant for drafting a reply "
        "to a specific incoming email.\n\n"
        "Memories are facts and instructions learned from how the user edited previous "
        "AI reply drafts. They will be injected into the drafting prompt. "
        "Injecting irrelevant memories degrades draft quality, so be selective.\n\n"
        "<selection_rules>\n"
        f"- Select a memory only when it would materially change or inform the reply to THIS email: "
        "a fact that answers something the email asks, a procedure whose trigger condition matches "
        "this situation, or guidance specific to this sender or their company.\n"
        '- Conditional memories ("When X, ...") apply only when the email actually matches the condition.\n'
        "- Prefer memories with concrete details (numbers, prices, contacts, links, policies) "
        "over generic advice.\n"
        "- Fewer, highly relevant memories beat many loosely related ones.\n"
        f"- If no memory clearly applies, return an empty list.\n"
        "- Work language-agnostically. The email and memories may be in different languages.\n"
        "</selection_rules>\n\n"
        f"Today: {today_for_llm()}\n\n"
        f"{pool_block}\n\n"
        f"{thread_block}\n\n"
        "<output_format>\n"
        f"Return the ids of the selected memories, most relevant first, at most {MAX_SELECTED}.\n"
        "Respond with:\n"
        "- memory_ids: list of UUID strings (may be empty)\n"
        "</output_format>"
    )


def extract_memories_from_edit_prompt(
    draft: str,
    sent: str,
    sender_email: str,
    sender_domain: str,
) -> str:
    """Extract durable memories from the delta between AI draft and sent version.

    Taxonomy:
    - kind: fact | procedure | preference
    - scope: sender | domain | global

    Scope ``domain`` MUST NOT be used when ``sender_domain`` is a public consumer
    email provider (e.g. gmail.com, outlook.com, yahoo.com, icloud.com, proton.me,
    and similar). For those providers, fall back to ``sender`` or ``global``.
    Enforcement is in the store (T15); the prompt makes the rule explicit so the
    LLM self-polices.
    """
    public_list = ", ".join(PUBLIC_PROVIDERS)

    return (
        "You extract durable memories from the difference between an AI-generated "
        "email draft and the version the user actually sent.\n\n"
        "The edit is the learning signal. Your job is to isolate what generalizes, "
        "and discard what was specific to this one message.\n\n"
        "<taxonomy>\n"
        "Extract memories of exactly three kinds:\n"
        "- FACT: a concrete piece of information the user added that the system did not know "
        "(a price, a deadline, a contact, a policy, a product limitation).\n"
        '- PROCEDURE: a conditional behaviour, phrased as "When X, do Y" '
        '(e.g. "When a prospect asks about on-premise deployment, point them to the '
        'sovereign hosting page").\n'
        "- PREFERENCE: a stylistic or tonal correction "
        "(e.g. \"Do not open with 'I hope this email finds you well'\").\n"
        "</taxonomy>\n\n"
        "<scope_rules>\n"
        "Assign a scope to each memory:\n"
        "- sender: applies only to this correspondent ({sender_email})\n"
        "- domain: applies to their organisation ({sender_domain})\n"
        "- global: applies to all emails\n\n"
        "IMPORTANT: NEVER use scope=domain when the sender's domain is a public consumer "
        f"email provider. Public providers include: {public_list}.\n"
        "If the sender's domain ({sender_domain}) is in that list, use scope=sender or "
        "scope=global instead.\n"
        "</scope_rules>\n\n"
        "<extraction_rules>\n"
        "- Extract nothing if the edit was purely cosmetic (typo, punctuation, reordering "
        "with no change of meaning).\n"
        "- Never extract one-off content: the specific date of this meeting, the name of "
        "this attachment.\n"
        "- Each memory must be self-contained and understandable without the original email.\n"
        "- Prefer few, high-quality memories. An empty list is a valid answer.\n"
        "- Work language-agnostically.\n"
        "</extraction_rules>\n\n"
        "<ai_draft>\n"
        f"{draft}\n"
        "</ai_draft>\n\n"
        "<sent_version>\n"
        f"{sent}\n"
        "</sent_version>\n\n"
        "Context:\n"
        f"- sender_email: {sender_email}\n"
        f"- sender_domain: {sender_domain}\n\n"
        "<output_format>\n"
        "Respond with a list of memories (may be empty, max 8). Each memory:\n"
        "- kind: fact | procedure | preference\n"
        "- scope: sender | domain | global\n"
        "- scope_value: the email address (for sender), domain (for domain), "
        'or "global" (for global)\n'
        "- content: the memory text (3–800 chars)\n"
        "</output_format>"
    ).format(sender_email=sender_email, sender_domain=sender_domain)
