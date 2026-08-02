"""In-process cache + invalidate + fallback tests.

Uses monkeypatching on the repository layer so we don't need a real DB
for the cache-behaviour tests. A separate test file
(test_registry_notify_trigger.py, T5's integration) proves the
end-to-end NOTIFY loop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from twaky.agents import registry
from twaky.agents_config.models import AgentConfig


def _cfg(agent_id: str = "plume", model: str | None = None) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        display_name=agent_id.capitalize(),
        role="specialist",
        system_prompt="system",
        model=model,
        temperature=None,
        updated_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    registry.invalidate_all()
    yield
    registry.invalidate_all()


class TestLoadAgentConfig:
    def test_cold_miss_loads_from_db(self):
        c = _cfg("plume", model="openai/gpt-4o")
        with patch("twaky.agents.registry._repository_get", return_value=c) as g:
            result = registry.load_agent_config("plume")
            assert result.model == "openai/gpt-4o"
            g.assert_called_once_with("plume")

    def test_warm_hit_does_not_call_db(self):
        c = _cfg("plume")
        with patch("twaky.agents.registry._repository_get", return_value=c) as g:
            registry.load_agent_config("plume")
            registry.load_agent_config("plume")
            registry.load_agent_config("plume")
        assert g.call_count == 1

    def test_invalidate_forces_reload(self):
        c1 = _cfg("plume", model="a")
        c2 = _cfg("plume", model="b")
        with patch("twaky.agents.registry._repository_get", side_effect=[c1, c2]) as g:
            first = registry.load_agent_config("plume")
            assert first.model == "a"
            registry.invalidate("plume")
            second = registry.load_agent_config("plume")
            assert second.model == "b"
        assert g.call_count == 2

    def test_invalidate_only_affects_one_key(self):
        with patch("twaky.agents.registry._repository_get") as g:
            g.side_effect = lambda aid: _cfg(aid)
            registry.load_agent_config("plume")
            registry.load_agent_config("chronos")
            g.reset_mock()
            registry.invalidate("plume")
            registry.load_agent_config("plume")
            registry.load_agent_config("chronos")
        assert g.call_count == 1  # only plume reloaded

    def test_invalidate_all_clears_everything(self):
        with patch("twaky.agents.registry._repository_get") as g:
            g.side_effect = lambda aid: _cfg(aid)
            registry.load_agent_config("plume")
            registry.load_agent_config("chronos")
            g.reset_mock()
            registry.invalidate_all()
            registry.load_agent_config("plume")
            registry.load_agent_config("chronos")
        assert g.call_count == 2


class TestFallbackOnMissingRow:
    def test_missing_row_falls_back_to_defaults(self):
        # DB row somehow absent (bad seed, manual delete) — daemon must
        # still produce a working config using the DEFAULT_PROMPTS module.
        with patch("twaky.agents.registry._repository_get", return_value=None):
            cfg = registry.load_agent_config("plume")
        assert cfg.id == "plume"
        assert cfg.system_prompt  # non-empty — pulled from DEFAULT_PROMPTS
        assert cfg.model is None
        assert cfg.temperature is None

    def test_unknown_agent_id_raises(self):
        with (
            patch("twaky.agents.registry._repository_get", return_value=None),
            pytest.raises(registry.AgentConfigMissing),
        ):
            registry.load_agent_config("zeus")
