"""Thread-safe in-process cache for agent configuration.

The cache is populated on cold read and cleared by config_listener.py
whenever Postgres NOTIFYs agent_config_changed. A row that is missing
from the DB falls back to defaults.py so the daemon never bricks on a
misconfigured install.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from twaky.agents.defaults import DEFAULT_PROMPTS, DISPLAY_NAMES, ROLES
from twaky.agents_config import repository
from twaky.agents_config.models import AgentConfig


class AgentConfigMissing(Exception):
    """Raised when an unknown agent id is requested (not in DEFAULT_PROMPTS)."""


_cache: dict[str, AgentConfig] = {}
_lock = threading.Lock()


def _repository_get(agent_id: str) -> AgentConfig | None:
    """Indirection kept for test monkeypatching."""
    return repository.get(agent_id)


def _fallback(agent_id: str) -> AgentConfig:
    if agent_id not in DEFAULT_PROMPTS:
        raise AgentConfigMissing(f"unknown agent id {agent_id!r}")
    return AgentConfig(
        id=agent_id,
        display_name=DISPLAY_NAMES[agent_id],
        role=ROLES[agent_id],
        system_prompt=DEFAULT_PROMPTS[agent_id],
        model=None,
        temperature=None,
        updated_at=datetime.now(UTC),
    )


def load_agent_config(agent_id: str) -> AgentConfig:
    """Fetch config, cache-first. Falls back to defaults on missing DB row."""
    with _lock:
        cached = _cache.get(agent_id)
        if cached is not None:
            return cached

    fetched = _repository_get(agent_id)
    cfg = fetched if fetched is not None else _fallback(agent_id)

    with _lock:
        _cache[agent_id] = cfg
    return cfg


def invalidate(agent_id: str) -> None:
    """Drop a single cache entry. Next load_agent_config() will re-fetch."""
    with _lock:
        _cache.pop(agent_id, None)


def invalidate_all() -> None:
    """Drop every cache entry. Called at daemon boot for a clean slate."""
    with _lock:
        _cache.clear()


__all__ = ["AgentConfigMissing", "invalidate", "invalidate_all", "load_agent_config"]
