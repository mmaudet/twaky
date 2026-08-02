"""Pydantic models for the /api/agents surface."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Agent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    role: Literal["orchestrator", "specialist"]
    system_prompt: str
    model: str | None
    temperature: float | None
    effective_model: str
    updated_at: datetime


class AgentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    role: Literal["orchestrator", "specialist"]
    model: str | None
    temperature: float | None
    effective_model: str
    updated_at: datetime


class AgentUpdate(BaseModel):
    """Partial update. All fields optional; empty body → 422 (see router)."""

    model_config = ConfigDict(extra="forbid")
    system_prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    model: str | None = Field(default=None)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class DefaultPromptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_prompt: str


__all__ = ["Agent", "AgentSummary", "AgentUpdate", "DefaultPromptResponse"]
