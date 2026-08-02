"""Registry cache tests — no DB, monkeypatched repository seam."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from twaky.skills import registry
from twaky.skills_config.models import Skill


def _mk_skill(name: str, agent: str) -> Skill:
    return Skill(
        id=uuid4(),
        name=name,
        description="d",
        python_source="def run(**kw): return 1",
        config_schema={},
        config_values={},
        bound_agents=[agent],
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    registry.invalidate_all()
    yield
    registry.invalidate_all()


def test_cache_miss_populates_and_returns(monkeypatch):
    calls: list[str] = []

    def fake(agent_id):
        calls.append(agent_id)
        return [_mk_skill("s1", "atlas")]

    monkeypatch.setattr(registry, "_repository_get_bound", fake)
    result = registry.load_skills_for_agent("atlas")
    assert len(result) == 1
    assert calls == ["atlas"]


def test_cache_hit_avoids_second_repo_call(monkeypatch):
    calls: list[str] = []

    def fake(agent_id):
        calls.append(agent_id)
        return [_mk_skill("s1", "atlas")]

    monkeypatch.setattr(registry, "_repository_get_bound", fake)
    registry.load_skills_for_agent("atlas")
    registry.load_skills_for_agent("atlas")
    assert calls == ["atlas"]  # only one call


def test_invalidate_all_forces_refresh(monkeypatch):
    calls: list[str] = []

    def fake(agent_id):
        calls.append(agent_id)
        return []

    monkeypatch.setattr(registry, "_repository_get_bound", fake)
    registry.load_skills_for_agent("atlas")
    registry.invalidate_all()
    registry.load_skills_for_agent("atlas")
    assert calls == ["atlas", "atlas"]


def test_per_agent_isolation(monkeypatch):
    calls: list[str] = []

    def fake(agent_id):
        calls.append(agent_id)
        return [_mk_skill("s", agent_id)]

    monkeypatch.setattr(registry, "_repository_get_bound", fake)
    registry.load_skills_for_agent("atlas")
    registry.load_skills_for_agent("plume")
    registry.load_skills_for_agent("atlas")  # still cached
    registry.load_skills_for_agent("plume")  # still cached
    assert calls == ["atlas", "plume"]  # each fetched exactly once
