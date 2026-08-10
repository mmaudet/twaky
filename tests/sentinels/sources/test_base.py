"""Unit tests for twaky.sentinels.sources.base — no broker required."""

from __future__ import annotations

import asyncio
import inspect

import pytest

from twaky.sentinels.sources.base import Ack, EventSource, _noop_ack

# ---------------------------------------------------------------------------
# ABC enforcement
# ---------------------------------------------------------------------------


def test_cannot_instantiate_abc() -> None:
    """``EventSource`` must not be directly instantiable."""
    with pytest.raises(TypeError):
        EventSource()  # type: ignore[abstract]


def test_subclass_must_implement_stream() -> None:
    """A subclass that omits ``stream`` must raise ``TypeError`` on instantiation."""

    class IncompleteSource(EventSource):
        pass  # stream() intentionally omitted

    with pytest.raises(TypeError):
        IncompleteSource()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# _noop_ack
# ---------------------------------------------------------------------------


def test_noop_ack_is_awaitable() -> None:
    """``_noop_ack`` must be a coroutine function."""
    assert inspect.iscoroutinefunction(_noop_ack)


def test_noop_ack_returns_none() -> None:
    """``_noop_ack()`` must complete without error and return None."""
    result = asyncio.run(_noop_ack())
    assert result is None


# ---------------------------------------------------------------------------
# Ack type alias (structural check)
# ---------------------------------------------------------------------------


def test_ack_alias_exported() -> None:
    """``Ack`` must be importable from the base module."""
    assert Ack is not None
