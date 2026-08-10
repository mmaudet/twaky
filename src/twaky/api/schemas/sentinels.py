"""Pydantic models for the /sentinels surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

_OUTCOME = Literal["ignored", "processed", "mission_created", "delegated", "error"]


class Stats24h(BaseModel):
    """24-hour run statistics for a sentinel."""

    total: int
    errors: int


class SentinelSummary(BaseModel):
    """Shallow representation used in the list endpoint."""

    name: str
    display_name: str
    enabled: bool
    version: str
    stats_24h: Stats24h


class SentinelDetail(BaseModel):
    """Full sentinel row including config schema and values."""

    name: str
    display_name: str
    description: str
    enabled: bool
    version: str
    config_schema: dict[str, Any]
    config_values: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SentinelPatch(BaseModel):
    """Partial update payload. Extra keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    config_values: dict[str, Any] | None = None


class SentinelRunSummary(BaseModel):
    """One row from ``sentinel_run``, without the heavy fields."""

    id: UUID
    sentinel_name: str
    event_ref: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    outcome: _OUTCOME
    mission_id: UUID | None
    llm_calls: int


class SentinelRunDetail(SentinelRunSummary):
    """Full run record including trace and error information."""

    trace: list[dict[str, Any]]
    error_repr: str | None


__all__ = [
    "SentinelDetail",
    "SentinelPatch",
    "SentinelRunDetail",
    "SentinelRunSummary",
    "SentinelSummary",
    "Stats24h",
]
