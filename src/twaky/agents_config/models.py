"""Dataclass carried between DB, service, and (via mapping) API+registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AgentConfig:
    id: str
    display_name: str
    role: str
    system_prompt: str
    model: str | None
    temperature: float | None
    updated_at: datetime


__all__ = ["AgentConfig"]
