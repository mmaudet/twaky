"""Frozen dataclasses mirroring the sentinel and sentinel_run DB rows.

All datetime fields are timezone-aware UTC (TIMESTAMPTZ → Python datetime
with tzinfo set by psycopg3's default row conversion).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class SentinelConfig:
    """Mirror of the ``sentinel`` table row (9 columns).

    Matches the schema from sql/008_init_sentinels.sh:
        name TEXT PK, display_name TEXT, description TEXT, version TEXT,
        enabled BOOLEAN, config_schema JSONB, config_values JSONB,
        created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ.
    """

    name: str
    display_name: str
    description: str
    version: str
    enabled: bool
    config_schema: dict
    config_values: dict
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SentinelRun:
    """Mirror of the ``sentinel_run`` table row (11 columns).

    Matches the schema from sql/008_init_sentinels.sh:
        id UUID PK, sentinel_name TEXT FK, event_ref TEXT,
        started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ?,
        duration_ms INT?, outcome TEXT, mission_id UUID?,
        llm_calls INT, error_repr TEXT?, trace JSONB.
    """

    id: UUID
    sentinel_name: str
    event_ref: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    outcome: str
    mission_id: UUID | None
    llm_calls: int
    error_repr: str | None
    trace: list


__all__ = ["SentinelConfig", "SentinelRun"]
