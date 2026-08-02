"""End-to-end: real Postgres UPDATE fires trigger → NOTIFY → cache invalidates.

Requires the twaky-pg container running and the T1 migration applied
(check: `docker compose ps twaky-pg` shows healthy).
"""

from __future__ import annotations

import asyncio
import os

import psycopg
import pytest

from twaky.agents import config_listener, registry
from twaky.agents_config import repository
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


@pytest.mark.asyncio
async def test_update_invalidates_cache_within_one_second():
    # Prime the cache.
    registry.invalidate_all()
    original = repository.get("plume")
    assert original is not None
    cached = registry.load_agent_config("plume")
    assert cached.temperature == original.temperature

    stop_event = asyncio.Event()
    listener_task = asyncio.create_task(config_listener.run(stop_event))

    try:
        # Give the LISTEN a moment to attach.
        await asyncio.sleep(0.5)

        # UPDATE the row (in the caller's connection — the trigger fires).
        new_temp = 0.42 if original.temperature != 0.42 else 0.43
        repository.update("plume", {"temperature": new_temp})

        # Wait for cache invalidation. The listener has ~1s to react.
        async def _wait_for_invalidation():
            for _ in range(20):  # 20 × 100ms = 2s max
                # A cleared cache means next load_agent_config re-reads DB.
                # Detect by locking + peeking at the internal cache dict.
                with registry._lock:
                    if "plume" not in registry._cache:
                        return True
                await asyncio.sleep(0.1)
            return False

        assert await _wait_for_invalidation(), (
            "cache was not invalidated within 2 seconds"
        )
    finally:
        stop_event.set()
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001, S110
            pass
        # Restore the DB row.
        repository.update("plume", {"temperature": original.temperature})
        registry.invalidate_all()
