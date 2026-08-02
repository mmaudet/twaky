"""Repository CRUD unit tests — real Postgres via the shared fixture."""

from __future__ import annotations

import os

import psycopg
import pytest

from twaky.agents_config import repository
from twaky.agents_config.models import AgentConfig
from twaky.agents_config.repository import AgentConfigNotFound
from twaky.config import settings


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable"),
]


class TestListAll:
    def test_returns_four_rows_sorted_by_id(self):
        rows = repository.list_all()
        ids = [r.id for r in rows]
        assert ids == ["atlas", "chronos", "iris", "plume"]  # alphabetical

    def test_all_rows_are_agent_config_instances(self):
        rows = repository.list_all()
        assert all(isinstance(r, AgentConfig) for r in rows)


class TestGet:
    def test_get_atlas_returns_row(self):
        cfg = repository.get("atlas")
        assert cfg is not None
        assert cfg.id == "atlas"
        assert cfg.display_name == "Atlas"
        assert cfg.role == "orchestrator"
        assert cfg.system_prompt  # non-empty

    def test_get_unknown_returns_none(self):
        assert repository.get("zeus") is None


class TestUpdate:
    def test_update_temperature(self):
        original = repository.get("plume")
        assert original is not None
        try:
            fresh = repository.update("plume", {"temperature": 0.3})
            assert fresh.temperature == pytest.approx(0.3)
            assert fresh.updated_at > original.updated_at
        finally:
            repository.update("plume", {"temperature": original.temperature})

    def test_update_model_to_null(self):
        original = repository.get("plume")
        assert original is not None
        try:
            fresh = repository.update("plume", {"model": "openai/gpt-4o"})
            assert fresh.model == "openai/gpt-4o"
            fresh = repository.update("plume", {"model": None})
            assert fresh.model is None
        finally:
            repository.update("plume", {"model": original.model})

    def test_update_unknown_agent_raises(self):
        with pytest.raises(AgentConfigNotFound):
            repository.update("zeus", {"temperature": 0.5})

    def test_update_empty_patch_raises_value_error(self):
        with pytest.raises(ValueError, match="empty patch"):
            repository.update("plume", {})

    def test_update_unknown_field_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown fields"):
            repository.update("plume", {"model": "x", "wibble": 1})
