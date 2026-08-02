"""Pydantic models for the /skills surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_AGENT_IDS = Literal["atlas", "chronos", "plume", "iris"]


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: str = Field(pattern=_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=1000)
    python_source: str = Field(min_length=1, max_length=32000)
    config_schema: dict[str, Any]
    config_values: dict[str, Any]
    bound_agents: list[_AGENT_IDS]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class SkillSummary(BaseModel):
    """Shorter payload for the list endpoint. Omits code + config."""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: str
    description: str
    bound_agents: list[_AGENT_IDS]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=1000)
    python_source: str = Field(min_length=1, max_length=32000)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    config_values: dict[str, Any] = Field(default_factory=dict)
    bound_agents: list[_AGENT_IDS] = Field(default_factory=list)
    enabled: bool = True


class SkillUpdate(BaseModel):
    """Partial update. All fields optional; empty body → 422 (enforced in router)."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, pattern=_NAME_PATTERN)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    python_source: str | None = Field(default=None, min_length=1, max_length=32000)
    config_schema: dict[str, Any] | None = None
    config_values: dict[str, Any] | None = None
    bound_agents: list[_AGENT_IDS] | None = None
    enabled: bool | None = None


class SkillTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    args: dict[str, Any] = Field(default_factory=dict)


class SkillTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["ok", "timeout", "crashed", "error"]
    result: Any = None
    message: str | None = None


__all__ = [
    "Skill",
    "SkillCreate",
    "SkillSummary",
    "SkillTestRequest",
    "SkillTestResponse",
    "SkillUpdate",
]
