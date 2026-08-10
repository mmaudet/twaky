"""Mail sentinel state types and thread status enum."""

from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict
from uuid import UUID


class ThreadStatus(str, Enum):
    """Classification of email thread status."""

    TO_REPLY = "TO_REPLY"
    ACTIONED = "ACTIONED"
    FYI = "FYI"
    AWAITING_REPLY = "AWAITING_REPLY"


class MailAgentState(TypedDict, total=False):
    """State dictionary for the mail-sentinel LangGraph pipeline.

    All fields are optional (total=False). Represents the accumulated state
    across nodes: rules matching, pattern learning, thread classification,
    and draft reply composition.
    """

    email_id: str
    thread: list[dict[str, Any]]
    matched_by: str
    rule_name: str | None
    status: ThreadStatus
    memory_ids: list[UUID]
    draft: str | None
    draft_language: str | None
    learned_pattern: dict[str, Any] | None
    actions_applied: list[str]
    started_at: float
    llm_calls: int


__all__ = ["MailAgentState", "ThreadStatus"]
