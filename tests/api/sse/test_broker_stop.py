"""Unit tests for SSEBroker.stop() — verifies executor-thread cleanup.

These tests use a patched psycopg so no real Postgres is needed.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from twaky.daemon.notify import listen

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeNotifies:
    """Fake psycopg Connection.notifies() that yields nothing and respects timeout."""

    def __init__(self, timeout: float) -> None:
        self._timeout = timeout

    def __iter__(self):
        # Simulate the bounded-timeout poll: sleep for the requested duration
        # then return without yielding any notifications.
        time.sleep(min(self._timeout, 0.05))
        return iter([])


class _FakeConn:
    def notifies(self, timeout: float | None = None) -> _FakeNotifies:
        return _FakeNotifies(timeout if timeout is not None else 0.05)

    def cursor(self):
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__ = MagicMock(return_value=False)
        cur.execute = MagicMock()
        return cur

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@contextmanager
def _patch_psycopg():
    fake_conn = _FakeConn()
    with patch("psycopg.connect", return_value=fake_conn):
        yield fake_conn


# ---------------------------------------------------------------------------
# D4a — stop_event causes listen() to return promptly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_event_causes_listen_to_return():
    """Consuming listen() with a stop_event and setting it should make the
    executor future complete within 2 s."""
    stop_event = asyncio.Event()

    with _patch_psycopg():

        async def _consume():
            async for _ in listen(
                ["ch"],
                "postgresql://fake/db",
                poll_interval_s=0.1,
                stop_event=stop_event,
            ):
                pass  # pragma: no cover — no notifications are delivered

        task = asyncio.create_task(_consume())

        # Give the executor thread a moment to start.
        await asyncio.sleep(0.1)

        # Signal stop and cancel — mirrors what broker.stop() does.
        stop_event.set()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (TimeoutError, asyncio.CancelledError):
            pass

        # Verify no notify_run threads remain after a short grace period.
        await asyncio.sleep(0.2)
        leaked = [t for t in threading.enumerate() if t.name.startswith("notify_run")]
        assert leaked == [], f"Leaked notify_run threads: {leaked}"


# ---------------------------------------------------------------------------
# D4b — broker.stop() joins within 3 s, no _run threads remain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_stop_joins_within_2s():
    """After SSEBroker.stop(), no threads with _run as target should remain."""
    from twaky.api.sse.broker import SSEBroker

    with _patch_psycopg():
        # Patch settings so broker can build a DSN without real config.
        with patch("twaky.config.settings") as mock_settings:
            mock_settings.pg_dsn = "postgresql://fake/db"

            broker = SSEBroker()
            await broker.start()

            # Let the listener loop spin for a bit.
            await asyncio.sleep(0.15)

            await broker.stop()

        # Give OS a moment to reclaim thread resources.
        deadline = time.monotonic() + 3.0
        leaked: list = ["placeholder"]
        while time.monotonic() < deadline and leaked:
            leaked = [
                t for t in threading.enumerate() if t.name.startswith("notify_run")
            ]
            if leaked:
                await asyncio.sleep(0.1)

        assert leaked == [], f"Leaked notify_run threads after broker.stop(): {leaked}"
