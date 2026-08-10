"""RabbitMQ-backed EventSource using aio-pika.

Design invariants
-----------------
- One durable named queue ``sentinel.<sentinel_name>`` per sentinel, so each
  sentinel gets its own copy of every message (no-steal fanout pattern).
- ``auto_delete=False`` — the queue survives sentinel restarts.
- ``prefetch_count=8`` — back-pressure without saturating the consumer.
- Bad JSON bodies are nacked without requeue and logged at ERROR level.
- ``stop_event`` is checked before every yield — the generator exits cleanly
  when the caller sets it.
- ``aio_pika.connect_robust`` provides automatic reconnection on transient
  broker failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import aio_pika

from twaky.sentinels.base import Event
from twaky.sentinels.sources.base import Ack, EventSource

log = logging.getLogger(__name__)


class RabbitMQEventSource(EventSource):
    """Consume events from one or more fanout/topic exchanges via aio-pika.

    Parameters
    ----------
    sentinel_name:
        Used to derive the queue name ``sentinel.<sentinel_name>``.
    rabbit_url:
        AMQP URL, e.g. ``amqp://guest:guest@localhost:5672/%2F``.
    bindings:
        List of exchange descriptors.  Each entry supports the keys:

        ``exchange`` (required)
            Exchange name.
        ``exchange_type`` (optional, default ``"fanout"``)
            ``"fanout"`` or ``"topic"``.
        ``routing_key`` (optional, default ``""``)
            Routing key for topic exchanges; ignored for fanout.
    """

    def __init__(
        self,
        *,
        sentinel_name: str,
        rabbit_url: str,
        bindings: list[dict[str, Any]],
    ) -> None:
        self._sentinel_name = sentinel_name
        self._rabbit_url = rabbit_url
        self._bindings = bindings

    async def stream(  # type: ignore[override]
        self, *, stop_event: asyncio.Event
    ) -> AsyncIterator[tuple[Event, Ack]]:
        """Yield ``(Event, Ack)`` pairs until ``stop_event`` is set.

        Connects to RabbitMQ, declares the durable queue, binds it to every
        configured exchange, then iterates over incoming messages.  The
        connection is closed in a ``finally`` block so resources are always
        released even if the caller cancels the iteration.

        The stop mechanism works by racing each ``queue.get()`` call against
        ``stop_event.wait()``.  When ``stop_event`` fires first, the generator
        returns without waiting for another broker message.
        """
        queue_name = f"sentinel.{self._sentinel_name}"
        conn = await aio_pika.connect_robust(self._rabbit_url)
        try:
            channel = await conn.channel()
            await channel.set_qos(prefetch_count=8)

            queue = await channel.declare_queue(
                queue_name,
                durable=True,
                auto_delete=False,
            )

            for binding in self._bindings:
                exchange_name: str = binding["exchange"]
                exchange_type: str = binding.get("exchange_type", "fanout")
                routing_key: str = binding.get("routing_key", "")

                exchange = await channel.declare_exchange(
                    exchange_name,
                    type=aio_pika.ExchangeType(exchange_type),
                    durable=True,
                )
                await queue.bind(exchange, routing_key=routing_key)

            # Race each message-get against stop_event so the generator can
            # exit cleanly without waiting for a message that may never come.
            while not stop_event.is_set():
                get_task = asyncio.ensure_future(queue.get(timeout=None, fail=False))
                stop_task = asyncio.ensure_future(stop_event.wait())

                done, pending = await asyncio.wait(
                    {get_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Cancel whichever task didn't win.
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

                if stop_event.is_set():
                    # stop_task won (or get_task returned None and stop is now set)
                    if get_task in done:
                        message = get_task.result()
                        if message is not None:
                            # Nack any dangling message — we're stopping.
                            await message.nack(requeue=True)
                    return

                # get_task won; inspect the result.
                message = get_task.result()
                if message is None:
                    # Queue is empty and no message arrived; tight-loop guard.
                    await asyncio.sleep(0.05)
                    continue

                try:
                    body = json.loads(message.body.decode())
                except Exception:
                    log.exception(
                        "Bad JSON body on queue %s — nacking without requeue",
                        queue_name,
                    )
                    await message.nack(requeue=False)
                    continue

                event: Event = {
                    "source_kind": "rabbitmq",
                    "source_ref": (f"{message.exchange}:{message.routing_key or ''}"),
                    "message_id": message.message_id or "",
                    "payload": body,
                }

                # Capture `message` in the default argument so each closure
                # references its own delivery, not the loop variable.
                async def _ack(_m: aio_pika.IncomingMessage = message) -> None:  # type: ignore[assignment]
                    await _m.ack()

                yield event, _ack

        finally:
            await conn.close()


__all__ = ["RabbitMQEventSource"]
