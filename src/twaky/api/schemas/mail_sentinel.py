"""Pydantic models for the /mail-sentinel surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

# ---------------------------------------------------------------------------
# Rule schemas
# ---------------------------------------------------------------------------

_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"


class MailRuleSummary(BaseModel):
    """Shallow representation used in the rules list endpoint."""

    id: UUID
    name: str
    description: str
    priority: int
    enabled: bool
    run_on_threads: bool
    action_count: int
    condition_count: int


class MailRuleDetail(MailRuleSummary):
    """Full rule row including conditions, combinator, actions, timestamps."""

    conditions: list[dict[str, Any]]
    combinator: str
    actions: list[str]
    created_at: datetime
    updated_at: datetime


class MailRuleCreate(BaseModel):
    """Payload for POST /mail-sentinel/rules."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    conditions: list[dict[str, Any]] = []
    combinator: str = "OR"
    actions: list[str]
    priority: int = 100
    enabled: bool = True
    run_on_threads: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        import re

        if not re.match(_NAME_PATTERN, v):
            raise ValueError(f"name must match {_NAME_PATTERN!r}")
        return v


class MailRulePatch(BaseModel):
    """Partial update payload for PATCH /mail-sentinel/rules/{id}."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    conditions: list[dict[str, Any]] | None = None
    combinator: str | None = None
    actions: list[str] | None = None
    priority: int | None = None
    enabled: bool | None = None
    run_on_threads: bool | None = None


# ---------------------------------------------------------------------------
# Memory schemas
# ---------------------------------------------------------------------------


class MailMemorySummary(BaseModel):
    """Summary of a mail_sentinel_memory row (evidence omitted)."""

    id: UUID
    kind: str
    scope: str
    scope_value: str
    content: str
    created_at: datetime
    expires_at: datetime


# ---------------------------------------------------------------------------
# Learned pattern schemas
# ---------------------------------------------------------------------------


class LearnedPatternSummary(BaseModel):
    """Summary of a mail_sentinel_learned_pattern row."""

    id: str
    sender_email: str
    rule_name: str
    confidence: float
    evidence_count: int
    first_seen: datetime
    last_confirmed: datetime
    is_active: bool


__all__ = [
    "LearnedPatternSummary",
    "MailMemorySummary",
    "MailRuleCreate",
    "MailRuleDetail",
    "MailRulePatch",
    "MailRuleSummary",
]
