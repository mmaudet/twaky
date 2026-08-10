"""Integration test for twaky.sentinels.config_listener.

Requires a live twaky-pg instance with the ``sentinel`` table, the
``notify_sentinel_changed`` trigger, and the ``mail`` seed row.

Set ``TWAKY_TEST_DSN`` env var to override the default DSN from settings.

Pattern mirrors tests/sentinels/test_repository.py (marker + skipif).
"""

from __future__ import annotations

import asyncio
import contextlib
import os

import psycopg
import pytest
import pytest_asyncio  # noqa: F401 — ensures asyncio mode is available

from twaky.config import settings
from twaky.sentinels import repository
from twaky.sentinels.config_listener import run_config_listener
from twaky.sentinels.registry import SentinelRegistry

# ---------------------------------------------------------------------------
# DSN helpers (mirrors test_repository.py)
# ---------------------------------------------------------------------------


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
    pytest.mark.asyncio,
]


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_invalidates_cache() -> None:
    """Updating a sentinel row must evict its cache entry via NOTIFY.

    Flow
    ----
    1. Warm the cache for the 'mail' sentinel.
    2. Start the listener task; give it 0.3 s to register LISTEN.
    3. Toggle ``enabled`` via the repository (fires the trigger).
    4. Poll up to 2 s for eviction (50 ms ticks).
    5. Assert 'mail' is no longer in the registry's _by_name dict.
    6. Restore original state in the finally block.
    """
    registry = SentinelRegistry()
    stop = asyncio.Event()
    listener_task: asyncio.Task | None = None

    # Fetch the 'mail' row so we know the original enabled state.
    original = repository.get("mail")
    assert original is not None, "seed row 'mail' must exist"
    original_enabled = original.enabled

    try:
        # Step 1: warm the cache.
        cached = registry.get("mail")
        assert cached is not None
        assert "mail" in registry._by_name

        # Step 2: start the listener and let it register LISTEN.
        listener_task = asyncio.create_task(
            run_config_listener(_dsn(), registry, stop_event=stop)
        )
        await asyncio.sleep(0.3)

        # Step 3: trigger the NOTIFY by toggling enabled.
        repository.update("mail", {"enabled": not original_enabled})

        # Step 4: poll for eviction.
        evicted = False
        for _ in range(40):  # 40 × 50 ms = 2 s
            await asyncio.sleep(0.05)
            if "mail" not in registry._by_name:
                evicted = True
                break

        # Step 5: assert.
        assert evicted, "'mail' was not evicted from the registry within 2 s"

    finally:
        # Restore original enabled state.
        with contextlib.suppress(Exception):
            repository.update("mail", {"enabled": original_enabled})

        # Shut down the listener task.
        stop.set()
        if listener_task is not None:
            listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await listener_task
