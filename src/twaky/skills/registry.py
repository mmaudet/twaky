"""Thread-safe per-agent skill cache.

Populated lazily on first call per agent. Invalidated coarsely on any
skill_changed NOTIFY (spec §5.1) — 4 tiny DB queries on next agent
invocation is cheaper than tracking per-agent dependencies.
"""

from __future__ import annotations

import threading

from twaky.skills_config import repository
from twaky.skills_config.models import Skill

_cache: dict[str, list[Skill]] = {}
_lock = threading.Lock()


def _repository_get_bound(agent_id: str) -> list[Skill]:
    """Indirection kept for test monkeypatching."""
    return repository.list_bound_and_enabled(agent_id)


def load_skills_for_agent(agent_id: str) -> list[Skill]:
    with _lock:
        cached = _cache.get(agent_id)
        if cached is not None:
            return cached
    fresh = _repository_get_bound(agent_id)
    with _lock:
        _cache[agent_id] = fresh
    return fresh


def invalidate_all() -> None:
    with _lock:
        _cache.clear()


__all__ = ["invalidate_all", "load_skills_for_agent"]
