"""Dataclass carried between DB, service, tool_adapter, and API mapping layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Skill:
    id: UUID
    name: str
    description: str
    python_source: str
    config_schema: dict
    config_values: dict
    bound_agents: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


__all__ = ["Skill"]
