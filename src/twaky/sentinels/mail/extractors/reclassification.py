"""Reclassification extractor: deterministic (no LLM).

When the user moves a mail out of Spam (direction='out') the sender
becomes trusted; moving IN flags them as spam-worthy. After three
consistent observations from the same sender, the pattern activates
and short-circuits the spam triage in future runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from twaky.db import get_pool
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import memories as mem_store
from twaky.sentinels.mail.store import observations as obs_store
from twaky.sentinels.mail.store.observations import (
    ExtractionOutcome,
    ObservationType,
)


@dataclass
class ExtractionResult:
    outcome: ExtractionOutcome
    memory_ids: list[UUID] = field(default_factory=list)
    pattern_ids: list[UUID] = field(default_factory=list)
    error_repr: str | None = None


def _observation_type(direction: Literal["in", "out"]) -> ObservationType:
    return (
        ObservationType.MARKED_SPAM
        if direction == "in"
        else ObservationType.UNMARKED_SPAM
    )


def _maybe_restore_spam_decision(
    email_id: str, direction: Literal["in", "out"]
) -> None:
    if direction != "out":
        return
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mail_sentinel_spam_decision "
            "SET restored_at = now(), restored_by = 'user' "
            "WHERE email_id = %s AND restored_at IS NULL",
            (email_id,),
        )


def extract_reclassification(
    *,
    email_id: str,
    mailbox_id: str,
    sender_email: str,
    direction: Literal["in", "out"],
) -> ExtractionResult:
    if direction == "out":
        rule_name = "trust_sender"
        content = "Legit sender — do not classify as spam."
        hint = 0.95
    else:
        rule_name = "block_sender"
        content = "Treat this sender as spam by default."
        hint = 0.90

    pattern = lp_store.record_decision(
        sender_email=sender_email, rule_name=rule_name, confidence_hint=hint
    )

    memory = mem_store.insert(
        kind="fact",
        scope="sender",
        scope_value=sender_email.lower(),
        content=content,
        source="auto_reclass",
        sender_email=sender_email.lower(),
        confidence=1.0,
    )

    _maybe_restore_spam_decision(email_id, direction)

    # memory may be None when the same content was already stored (dup).
    # In that case we still record the observation but with empty memory_ids.
    mem_ids: list[UUID] = [memory.id] if memory is not None else []

    obs_store.insert_if_new(
        email_id=email_id,
        mailbox_id=mailbox_id,
        observation_type=_observation_type(direction),
        extraction_outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=mem_ids,
        pattern_ids=[pattern.id],
    )

    return ExtractionResult(
        outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=mem_ids,
        pattern_ids=[pattern.id],
    )


__all__ = ["ExtractionResult", "extract_reclassification"]
