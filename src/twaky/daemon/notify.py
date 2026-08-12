"""PG LISTEN → async iterator of (channel, payload) tuples."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import AsyncIterator

import psycopg

logger = logging.getLogger(__name__)

# How long to wait for the executor thread to exit before giving up (seconds).
_THREAD_JOIN_TIMEOUT = 2.0


async def listen(
    channels: list[str],
    dsn: str,
    poll_interval_s: float = 1.0,
    stop_event: asyncio.Event | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """Yield (channel, payload) as they arrive. Runs until cancelled.

    If *stop_event* is supplied the executor thread will exit cleanly once the
    event is set, allowing the thread to be joined.  When *stop_event* is
    ``None`` behaviour is byte-identical to the original (blocking indefinite
    loop) for backward compatibility.
    """
    # psycopg3 has a blocking .notifies() helper; run in a thread and marshal via a queue.
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _run() -> None:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            for ch in channels:
                cur.execute(f"LISTEN {ch}")
            if stop_event is None:
                # Legacy path: block forever, same behaviour as before.
                for note in conn.notifies(timeout=None):
                    loop.call_soon_threadsafe(
                        queue.put_nowait, (note.channel, note.payload)
                    )
            else:
                # Cooperative path: poll with a bounded timeout so we can
                # notice when stop_event is set and return promptly.
                while not stop_event.is_set():
                    for note in conn.notifies(timeout=poll_interval_s):
                        loop.call_soon_threadsafe(
                            queue.put_nowait, (note.channel, note.payload)
                        )

    # Submit to a dedicated single-thread executor so we can keep a reference
    # to the raw concurrent.futures.Future.  asyncio.wrap_future lets us await
    # it normally; cancel() on the asyncio wrapper does NOT interrupt the
    # underlying thread, but once stop_event is set the thread exits promptly.
    _executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="notify_run"
    )
    raw_future: concurrent.futures.Future[None] = _executor.submit(_run)
    asyncio_future = asyncio.wrap_future(raw_future, loop=loop)

    try:
        while True:
            item = await queue.get()
            yield item
    finally:
        # Cancel the asyncio wrapper so any pending await on it is dropped.
        asyncio_future.cancel()

        if stop_event is not None:
            # Signal the thread to exit its polling loop, then poll asyncio-side
            # until the raw future is done (max _THREAD_JOIN_TIMEOUT seconds).
            stop_event.set()
            deadline = loop.time() + _THREAD_JOIN_TIMEOUT
            while not raw_future.done() and loop.time() < deadline:
                await asyncio.sleep(0.05)
            if not raw_future.done():
                logger.warning(
                    "notify._run thread did not exit within %.1f s — leaking thread",
                    _THREAD_JOIN_TIMEOUT,
                )

        _executor.shutdown(wait=False)


__all__ = ["listen"]
