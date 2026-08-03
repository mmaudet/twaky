"""Integration test: real skill row write fires NOTIFY → registry.invalidate_all()."""

from __future__ import annotations

import asyncio

import pytest

from twaky.db import get_pool
from twaky.skills import config_listener, registry
from twaky.skills_config import repository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _clean_skills():
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill")


async def test_notify_invalidates_registry_cache_within_1s():
    # Warm the cache so we can detect its clearing.
    registry.invalidate_all()
    registry._cache["atlas"] = []  # type: ignore[attr-defined]

    stop = asyncio.Event()
    task = asyncio.create_task(config_listener.run(stop))
    try:
        # Give the listener a moment to LISTEN.
        await asyncio.sleep(0.5)

        # Fire the NOTIFY via a real insert.
        repository.create(
            name="notify_probe",
            description="d",
            python_source="def run(**kw): return 1",
            config_schema={},
            config_values={},
            bound_agents=["atlas"],
            enabled=True,
        )

        # Poll until the cache clears (up to 1 s).
        for _ in range(10):
            await asyncio.sleep(0.1)
            if "atlas" not in registry._cache:  # type: ignore[attr-defined]
                break
        assert "atlas" not in registry._cache  # type: ignore[attr-defined]
    finally:
        stop.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
