"""In-process SSE broker fed by PG LISTEN mission_changed."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import structlog

from twaky.config import settings

log = structlog.get_logger("twaky.api.sse")


class SSEBroker:
    def __init__(self, queue_maxsize: int = 100) -> None:
        self.subscribers: dict[UUID, asyncio.Queue[dict]] = {}
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._queue_maxsize = queue_maxsize

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._listener())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def subscribe(self) -> tuple[UUID, asyncio.Queue[dict]]:
        sub_id = uuid4()
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._queue_maxsize)
        self.subscribers[sub_id] = queue
        return sub_id, queue

    def unsubscribe(self, sub_id: UUID) -> None:
        self.subscribers.pop(sub_id, None)

    async def _listener(self) -> None:
        from twaky.daemon.notify import listen

        try:
            async for _channel, payload in listen(
                ["mission_changed"],
                settings.pg_dsn,
                stop_event=self._stop_event,
            ):
                if self._stop_event.is_set():
                    break
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                self._broadcast(data)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SSE listener crashed")

    def _broadcast(self, payload: dict) -> None:
        for sub_id, queue in list(self.subscribers.items()):
            _put_dropping_oldest(queue, payload, sub_id)


def _put_dropping_oldest(
    queue: asyncio.Queue[dict], payload: dict, sub_id: UUID
) -> None:
    """Put `payload` in queue; if full, drop the oldest and retry once."""
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()  # drop oldest
            log.warning("SSE queue full, dropped oldest event", sub_id=str(sub_id))
        except asyncio.QueueEmpty:
            return
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            log.warning(
                "SSE queue still full after drop, giving up",
                sub_id=str(sub_id),
            )


__all__ = ["SSEBroker"]
