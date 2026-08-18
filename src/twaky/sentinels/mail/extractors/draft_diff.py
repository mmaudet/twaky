"""Draft diff extractor: LLM-based memory extraction from AI-vs-shipped diff.

Matches a sent mail to a recent Twaky mission by In-Reply-To message-id,
compares the AI-original draft to what the user actually shipped, and
calls the LLM to extract durable lessons.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from twaky.db import get_pool
from twaky.sentinels.mail.extractors.reclassification import ExtractionResult
from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.invoke import structured_call
from twaky.sentinels.mail.llm.tiers import UseCase
from twaky.sentinels.mail.prompts.extract_memory_from_diff import draft_diff_prompt
from twaky.sentinels.mail.schemas_write_side import DraftDiffOutput
from twaky.sentinels.mail.store import memories as mem_store
from twaky.sentinels.mail.store import observations as obs_store
from twaky.sentinels.mail.store.observations import (
    ExtractionOutcome,
    ObservationType,
)

log = logging.getLogger(__name__)

_TRIVIAL_DIFF_THRESHOLD = 0.05
_MIN_CONFIDENCE = 0.7


def _levenshtein_ratio(a: str, b: str) -> float:
    """Return the Levenshtein edit distance normalized to [0.0, 1.0].

    0.0 = identical; 1.0 = completely different.
    """
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0
    # Simple DP implementation; strings here are short (< 5 KB usually).
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n] / max(m, n)


def _find_matching_mission(
    *, owner_email: str, in_reply_to: str
) -> dict[str, Any] | None:
    """Return a mission whose artifacts reference *in_reply_to* message-id."""
    sql = """
        SELECT id, artifacts
        FROM mission
        WHERE state = 'awaiting_user'
          AND declared_by = 'sentinel:mail'
          AND owner_email = %s
          AND created_at > now() - INTERVAL '7 days'
        ORDER BY created_at DESC
        LIMIT 20
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (owner_email,))
        rows = cur.fetchall()

    for row in rows:
        artifacts = row["artifacts"] or []
        for art in artifacts:
            mid = (art or {}).get("in_reply_to_message_id")
            if mid and mid == in_reply_to:
                return row
    return None


def _extract_ai_draft(artifacts: list[dict[str, Any]]) -> str | None:
    for art in artifacts:
        if isinstance(art, dict) and art.get("kind") == "draft" and art.get("body"):
            return str(art["body"])
    return None


def _transition_mission_to_done(mission_id: UUID) -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mission SET state='done', state_reason='draft_sent_by_user', "
            "updated_at=now() WHERE id=%s",
            (mission_id,),
        )


def _delete_memories(ids: list[UUID]) -> None:
    if not ids:
        return
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory WHERE id = ANY(%s)", (list(ids),))


def extract_draft_diff(
    *,
    email_id: str,
    mailbox_id: str,
    sender_email: str,
    recipient_email: str,
    shipped_body: str,
    subject: str,
    in_reply_to: str | None,
    owner_email: str,
) -> ExtractionResult:
    if not in_reply_to:
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.SKIPPED_NO_MATCH,
        )
        return ExtractionResult(outcome=ExtractionOutcome.SKIPPED_NO_MATCH)

    mission = _find_matching_mission(owner_email=owner_email, in_reply_to=in_reply_to)
    if mission is None:
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.SKIPPED_NO_MATCH,
        )
        return ExtractionResult(outcome=ExtractionOutcome.SKIPPED_NO_MATCH)

    ai_draft = _extract_ai_draft(mission["artifacts"])
    if not ai_draft:
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.SKIPPED_NO_MATCH,
        )
        return ExtractionResult(outcome=ExtractionOutcome.SKIPPED_NO_MATCH)

    ratio = _levenshtein_ratio(ai_draft, shipped_body)
    if ratio < _TRIVIAL_DIFF_THRESHOLD:
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.SKIPPED_TRIVIAL,
        )
        return ExtractionResult(outcome=ExtractionOutcome.SKIPPED_TRIVIAL)

    # Fetch previous memories for this sender to include in prompt
    prev = mem_store.list_for_prompt(
        sender_email=sender_email,
        sender_domain=sender_email.split("@")[-1] if "@" in sender_email else "",
        limit=8,
    )
    previous_memories = [
        {"id": str(m.id), "content": m.content, "scope": m.scope} for m in prev
    ]

    try:
        prompt = draft_diff_prompt(
            ai_draft=ai_draft,
            shipped_body=shipped_body,
            sender_email=sender_email,
            recipient_email=recipient_email,
            thread_language="auto",  # LLM infers from bodies
            previous_memories=previous_memories,
        )
        out: DraftDiffOutput = structured_call(
            prompt,
            DraftDiffOutput,
            hardening=Hardening.COMPACT,
            use_case=UseCase.EXTRACT_MEMORY_DIFF,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("draft_diff: LLM failed: %r", e)
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.ERROR,
            error_repr=repr(e),
        )
        return ExtractionResult(outcome=ExtractionOutcome.ERROR, error_repr=repr(e))

    _delete_memories(out.should_delete_previous_memory_ids)

    memory_ids: list[UUID] = []
    for em in out.memories:
        if em.confidence < _MIN_CONFIDENCE:
            continue
        m = mem_store.insert(
            kind=em.kind,
            scope=em.scope,
            scope_value=em.scope_value,
            content=em.content,
            source="auto_diff",
            sender_email=(sender_email.lower() if em.scope == "sender" else None),
            mission_id=mission["id"],
            confidence=em.confidence,
        )
        if m is not None:
            memory_ids.append(m.id)

    _transition_mission_to_done(mission["id"])

    obs_store.insert_if_new(
        email_id=email_id,
        mailbox_id=mailbox_id,
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=memory_ids,
    )

    return ExtractionResult(outcome=ExtractionOutcome.EXTRACTED, memory_ids=memory_ids)


__all__ = ["extract_draft_diff"]
