"""RabbitMQ → event_log worker.

Binds a durable queue (default `agent.graph.ingest`) to a configurable list of
fanout exchanges with an empty routing key, so each source exchange delivers a
COPY of every message to us without stealing from existing consumers.

Every message is inserted into `event_log` as JSONB, then acked. A dead-letter
exchange + queue catch permanently-failing messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import signal
from typing import Any

import aio_pika
import psycopg
import structlog
from aio_pika import ExchangeType

from twaky.config import settings
from twaky.db import get_pool

logging.basicConfig(level=logging.INFO)
log = structlog.get_logger("twaky.ingest")


DLX_SUFFIX = ".dlx"
DLQ_SUFFIX = ".dlq"


def _message_key(exchange: str, body: bytes, message_id: str | None) -> str:
    """Stable id used for dedup in event_log.message_id.

    Prefer publisher-set message_id; fall back to sha256(exchange || body)
    which deduplicates identical redeliveries.
    """
    if message_id:
        return message_id
    h = hashlib.sha256()
    h.update(exchange.encode())
    h.update(b"|")
    h.update(body)
    return "sha256:" + h.hexdigest()


def _decode_payload(body: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(body)
        if not isinstance(obj, dict):
            return {"_wrapped": obj}
        return obj
    except json.JSONDecodeError:
        return {"_raw": body.decode("utf-8", errors="replace")}


def _insert_event(
    exchange: str, routing_key: str, message_id: str, payload: dict[str, Any]
) -> bool:
    """Insert into event_log. Returns True if inserted, False if dedup skipped."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO event_log (exchange, routing_key, message_id, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (message_id) DO NOTHING
                RETURNING id
                """,
                (exchange, routing_key, message_id, json.dumps(payload)),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


async def _bind_and_consume(
    channel: aio_pika.abc.AbstractChannel,
) -> aio_pika.abc.AbstractQueue:
    # Dead-letter side first (main queue points at DLX).
    dlx_name = settings.agent_queue + DLX_SUFFIX
    dlq_name = settings.agent_queue + DLQ_SUFFIX
    dlx = await channel.declare_exchange(dlx_name, ExchangeType.FANOUT, durable=True)
    dlq = await channel.declare_queue(dlq_name, durable=True)
    await dlq.bind(dlx)

    # Main queue with DLX pointer.
    main = await channel.declare_queue(
        settings.agent_queue,
        durable=True,
        arguments={"x-dead-letter-exchange": dlx_name},
    )

    # Bind to each source fanout exchange (passive: we assume they exist,
    # created by Cozy/Sabre/Calendar side services).
    for exch in settings.exchanges:
        source = await channel.get_exchange(exch, ensure=True)
        await main.bind(source, routing_key="")
        log.info("bound", queue=settings.agent_queue, exchange=exch)

    return main


async def _consume(queue: aio_pika.abc.AbstractQueue) -> None:
    async with queue.iterator() as it:
        async for message in it:
            try:
                exch = message.exchange or ""
                rk = message.routing_key or ""
                mid = _message_key(exch, message.body, message.message_id)
                payload = _decode_payload(message.body)
                inserted = _insert_event(exch, rk, mid, payload)
                await message.ack()
                if inserted:
                    log.info("ingested", exchange=exch, mid=mid[:32])
                else:
                    log.debug("dedup", exchange=exch, mid=mid[:32])
            except psycopg.Error as e:
                log.error("db error, rejecting to DLQ", err=str(e))
                await message.reject(requeue=False)
            except Exception as e:
                log.exception("unexpected error, rejecting to DLQ", err=str(e))
                await message.reject(requeue=False)


async def run() -> None:
    log.info(
        "starting ingest worker",
        queue=settings.agent_queue,
        exchanges=settings.exchanges,
        prefetch=settings.agent_prefetch,
    )
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=settings.agent_prefetch)
        queue = await _bind_and_consume(channel)

        loop = asyncio.get_event_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        consumer_task = asyncio.create_task(_consume(queue))
        await stop.wait()
        log.info("shutting down")
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass


__all__ = ["run"]
