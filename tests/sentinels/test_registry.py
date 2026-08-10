"""Unit tests for twaky.sentinels.registry.SentinelRegistry.

No DB required — ``twaky.sentinels.registry.repository`` is patched via
``unittest.mock.patch``.

Helper
------
``_row(name, enabled=True)`` — builds a minimal ``SentinelConfig`` with
real-ish timestamps so the tests don't need to care about the frozen-dc
constraints.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from twaky.sentinels.models import SentinelConfig
from twaky.sentinels.registry import SentinelRegistry

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _row(name: str, *, enabled: bool = True) -> SentinelConfig:
    """Create a minimal SentinelConfig for use in tests."""
    now = datetime.now(UTC)
    return SentinelConfig(
        name=name,
        display_name=name.title(),
        description=f"{name} sentinel",
        version="1.0.0",
        enabled=enabled,
        config_schema={},
        config_values={},
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> SentinelRegistry:
    """Fresh registry instance per test (no shared singleton state)."""
    return SentinelRegistry()


# ---------------------------------------------------------------------------
# get() tests
# ---------------------------------------------------------------------------


def test_get_miss_loads_from_repo(registry: SentinelRegistry) -> None:
    """A cache miss must call repository.get exactly once and return the row."""
    row = _row("mail")
    with patch("twaky.sentinels.registry.repository.get", return_value=row) as mock_get:
        result = registry.get("mail")

    assert result == row
    mock_get.assert_called_once_with("mail")


def test_get_hit_does_not_hit_repo(registry: SentinelRegistry) -> None:
    """A pre-seeded cache entry must be returned without touching the repository."""
    row = _row("mail")
    registry._by_name["mail"] = row

    with patch("twaky.sentinels.registry.repository.get") as mock_get:
        result = registry.get("mail")

    assert result == row
    mock_get.assert_not_called()


def test_get_unknown_returns_none(registry: SentinelRegistry) -> None:
    """get() must return None when the repository returns None."""
    with patch("twaky.sentinels.registry.repository.get", return_value=None):
        result = registry.get("does-not-exist")

    assert result is None


# ---------------------------------------------------------------------------
# list_enabled() tests
# ---------------------------------------------------------------------------


def test_list_enabled_loads_once(registry: SentinelRegistry) -> None:
    """Two consecutive list_enabled() calls must hit the repository only once."""
    row = _row("mail")
    with patch(
        "twaky.sentinels.registry.repository.list_enabled", return_value=[row]
    ) as mock_list:
        first = registry.list_enabled()
        second = registry.list_enabled()

    assert first == [row]
    assert second == [row]
    mock_list.assert_called_once()


def test_invalidate_forces_reload_of_list(registry: SentinelRegistry) -> None:
    """After invalidate(name), the next list_enabled() must re-query the repository."""
    row_a = _row("alpha")
    row_b = _row("beta")

    with patch(
        "twaky.sentinels.registry.repository.list_enabled",
        side_effect=[[row_a], [row_a, row_b]],
    ) as mock_list:
        first = registry.list_enabled()
        registry.invalidate("alpha")
        second = registry.list_enabled()

    assert first == [row_a]
    assert {c.name for c in second} == {"alpha", "beta"}
    assert mock_list.call_count == 2


# ---------------------------------------------------------------------------
# invalidate_all() test
# ---------------------------------------------------------------------------


def test_invalidate_all_drops_every_entry(registry: SentinelRegistry) -> None:
    """invalidate_all() must empty _by_name and reset _enabled_loaded."""
    registry._by_name["alpha"] = _row("alpha")
    registry._by_name["beta"] = _row("beta")
    registry._enabled_loaded = True

    registry.invalidate_all()

    assert registry._by_name == {}
    assert registry._enabled_loaded is False
