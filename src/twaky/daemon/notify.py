"""PG LISTEN → async iterator of (channel, payload) tuples."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import psycopg


async def listen(
    channels: list[str], dsn: str, poll_interval_s: float = 1.0
) -> AsyncIterator[tuple[str, str]]:
    """Yield (channel, payload) as they arrive. Runs until cancelled."""
    # psycopg3 has a blocking .notifies() helper; run in a thread and marshal via a queue.
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _run():
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            for ch in channels:
                cur.execute(f"LISTEN {ch}")
            # Poll indefinitely; timeout=poll_interval_s keeps us responsive.
            for note in conn.notifies(timeout=None):
                loop.call_soon_threadsafe(
                    queue.put_nowait, (note.channel, note.payload)
                )

    task = loop.run_in_executor(None, _run)
    try:
        while True:
            item = await queue.get()
            yield item
    finally:
        task.cancel()


__all__ = ["listen"]
