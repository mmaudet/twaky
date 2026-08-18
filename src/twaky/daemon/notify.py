"""PG LISTEN → async iterator of (channel, payload) tuples."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
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

    The worker thread can never outlive the iteration. It polls with a bounded
    timeout and checks an internal stop flag that this generator always sets on
    teardown, whether the consumer breaks, is cancelled, or raises.

    That flag is not a nicety. ``conn.notifies(timeout=None)`` — the shape this
    helper used when no *stop_event* was passed — blocks the worker forever;
    ``concurrent.futures`` threads are non-daemon and its atexit hook joins
    them, so a single un-stoppable listener hung interpreter shutdown for good.
    Tests passed, then the process never exited. ``_executor.shutdown(wait=
    False)`` does not interrupt a thread already parked inside psycopg.

    *stop_event* remains available for callers that want to stop the listener
    from the outside, independently of the iteration.
    """
    # psycopg3 has a blocking .notifies() helper; run in a thread and marshal via a queue.
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    # Set on teardown of this generator, always. `stop_event` is the caller's
    # optional handle; this one is ours and is never None.
    thread_stop = threading.Event()

    def _stopping() -> bool:
        return thread_stop.is_set() or (stop_event is not None and stop_event.is_set())

    def _run() -> None:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            for ch in channels:
                cur.execute(f"LISTEN {ch}")
            while not _stopping():
                for note in conn.notifies(timeout=poll_interval_s):
                    loop.call_soon_threadsafe(
                        queue.put_nowait, (note.channel, note.payload)
                    )
                    if _stopping():
                        break

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

        # Signal the thread to leave its polling loop, then wait asyncio-side
        # until the raw future is done (max _THREAD_JOIN_TIMEOUT seconds).
        thread_stop.set()
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
