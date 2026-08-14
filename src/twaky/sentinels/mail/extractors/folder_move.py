"""Folder move extractor: pattern always, LLM decides memory."""

from __future__ import annotations

import logging
import re

from twaky.sentinels.mail.extractors.reclassification import ExtractionResult
from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.invoke import structured_call
from twaky.sentinels.mail.llm.tiers import UseCase
from twaky.sentinels.mail.prompts.extract_memory_from_move import folder_move_prompt
from twaky.sentinels.mail.schemas_write_side import FolderMoveOutput
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import memories as mem_store
from twaky.sentinels.mail.store import observations as obs_store
from twaky.sentinels.mail.store.observations import (
    ExtractionOutcome,
    ObservationType,
)

log = logging.getLogger(__name__)

_RULE_NAME_SANITIZER = re.compile(r"[^A-Za-z0-9-]+")


def _sanitize_folder_name(folder_name: str) -> str:
    """Match JMAP flag naming: alphanumeric + hyphen only."""
    return _RULE_NAME_SANITIZER.sub("-", folder_name).strip("-") or "Folder"


def extract_folder_move(
    *,
    email_id: str,
    mailbox_id: str,
    sender_email: str,
    folder_name: str,
    subject: str,
    history_count: int,
) -> ExtractionResult:
    sanitized = _sanitize_folder_name(folder_name)
    rule_name = f"label:{sanitized}"

    pattern = lp_store.record_decision(
        sender_email=sender_email, rule_name=rule_name, confidence_hint=0.85
    )
    pattern_ids = [pattern.id]
    memory_ids: list = []

    try:
        prompt = folder_move_prompt(
            sender_email=sender_email,
            history_count=history_count,
            folder_name=folder_name,
            subject=subject,
        )
        out: FolderMoveOutput = structured_call(
            prompt,
            FolderMoveOutput,
            hardening=Hardening.COMPACT,
            use_case=UseCase.EXTRACT_MEMORY_MOVE,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("folder_move: LLM failed: %r", e)
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.MOVED_TO_CUSTOM,
            extraction_outcome=ExtractionOutcome.ERROR,
            memory_ids=[],
            pattern_ids=pattern_ids,
            error_repr=repr(e),
        )
        return ExtractionResult(
            outcome=ExtractionOutcome.ERROR,
            memory_ids=[],
            pattern_ids=pattern_ids,
            error_repr=repr(e),
        )

    if out.should_extract and out.memory is not None and out.memory.confidence >= 0.7:
        m = mem_store.insert(
            kind=out.memory.kind,
            scope=out.memory.scope,
            scope_value=out.memory.scope_value,
            content=out.memory.content,
            source="auto_move",
            sender_email=(sender_email.lower() if out.memory.scope == "sender" else None),
            confidence=out.memory.confidence,
        )
        if m is not None:
            memory_ids = [m.id]

    obs_store.insert_if_new(
        email_id=email_id,
        mailbox_id=mailbox_id,
        observation_type=ObservationType.MOVED_TO_CUSTOM,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=memory_ids,
        pattern_ids=pattern_ids,
    )

    return ExtractionResult(
        outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=memory_ids,
        pattern_ids=pattern_ids,
    )


__all__ = ["extract_folder_move"]
