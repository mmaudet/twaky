"""Pydantic models for the Mission domain.

Persistence uses raw psycopg (see repository.py). These models are the
single source of truth for serialization (API, Langfuse, tests).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MissionState(StrEnum):
    DECLARED = "declared"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {MissionState.DONE, MissionState.FAILED, MissionState.CANCELLED}


class PlanStep(BaseModel):
    model_config = ConfigDict(frozen=False, extra="forbid")

    agent: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "in_progress", "done", "skipped"] = "pending"


class Mission(BaseModel):
    model_config = ConfigDict(frozen=False, extra="forbid")

    id: UUID
    owner_email: str
    declared_by: str
    declared_at: datetime
    intent_text: str
    plan: list[PlanStep] | None = None
    state: MissionState = MissionState.DECLARED
    state_reason: str | None = None
    due_at: datetime | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    langfuse_session_id: str | None = None
    created_at: datetime
    updated_at: datetime
